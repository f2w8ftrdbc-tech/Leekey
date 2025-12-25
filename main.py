import streamlit as st
import pandas as pd
from datetime import datetime, date
import re, secrets, hashlib, hmac
import psycopg
import psycopg.rows
import streamlit.components.v1 as components

# =========================================================
# Config
# =========================================================
st.set_page_config(page_title="私人理财中心（公网多用户）", layout="wide")

APP_SECRET = st.secrets["APP_SECRET"]
DATABASE_URL = st.secrets["DATABASE_URL"]
COOKIE_DAYS = int(st.secrets.get("COOKIE_DAYS", 30))
COOKIE_NAME = "pf_auth"

BOOK_OPTIONS = ["生活主账", "车子专项", "学费/购汇", "理财账本"]
EXP_CATS = ["Eat outside", "Shopping", "Bill", "Petrol", "Insurance", "Rent", "其他"]
INC_CATS = ["工资", "业余项目", "亲情赠与", "理财收益", "其他"]

# =========================================================
# DB
# =========================================================
@st.cache_resource
def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row)

def db_fetchall(sql, params=None):
    with get_conn().cursor() as cur:
        cur.execute(sql, params or [])
        return cur.fetchall()

def db_execute(sql, params=None):
    with get_conn().cursor() as cur:
        cur.execute(sql, params or [])
    get_conn().commit()

# =========================================================
# Security
# =========================================================
def normalize_username(u: str) -> str:
    u = (u or "").strip()
    u = re.sub(r"[^A-Za-z0-9_]", "", u)
    return u.lower()

def pbkdf2_hash_password(password: str, salt_hex: str | None = None) -> dict:
    salt = secrets.token_bytes(16) if salt_hex is None else bytes.fromhex(salt_hex)
    pwd = (password + APP_SECRET).encode("utf-8")
    dk = hashlib.pbkdf2_hmac("sha256", pwd, salt, 200_000)
    return {"salt": salt.hex(), "hash": dk.hex()}

def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    test = pbkdf2_hash_password(password, salt_hex=salt_hex)["hash"]
    return hmac.compare_digest(test, hash_hex)

def hmac_sign(s: str) -> str:
    return hmac.new(APP_SECRET.encode("utf-8"), s.encode("utf-8"), hashlib.sha256).hexdigest()

def sha256_token(raw: str) -> str:
    return hashlib.sha256((raw + APP_SECRET).encode("utf-8")).hexdigest()

# =========================================================
# Cookie helpers (JS)
# =========================================================
def cookie_get(name: str) -> str:
    html = f"""
    <script>
    function getCookie(name) {{
      const value = `; ${{document.cookie}}`;
      const parts = value.split(`; ${{name}}=`);
      if (parts.length === 2) return parts.pop().split(';').shift();
      return "";
    }}
    Streamlit.setComponentValue(getCookie("{name}") || "");
    </script>
    """
    return components.html(html, height=0, width=0)

def cookie_set(name: str, value: str, days: int):
    html = f"""
    <script>
    const d = new Date();
    d.setTime(d.getTime() + ({days}*24*60*60*1000));
    document.cookie = "{name}={value};expires=" + d.toUTCString() + ";path=/;SameSite=Lax";
    </script>
    """
    components.html(html, height=0, width=0)

def cookie_delete(name: str):
    html = f"""
    <script>
    document.cookie = "{name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
    </script>
    """
    components.html(html, height=0, width=0)

def make_cookie_value(username: str, raw_token: str) -> str:
    sig = hmac_sign(f"{username}|{raw_token}")
    return f"{username}|{raw_token}|{sig}"

def parse_cookie_value(v: str):
    try:
        u, t, sig = (v or "").split("|")
        exp = hmac_sign(f"{u}|{t}")
        if not hmac.compare_digest(exp, sig):
            return None, None
        return u, t
    except:
        return None, None

# =========================================================
# User/profile
# =========================================================
def get_user_profile(username: str):
    rows = db_fetchall("select username, nickname, avatar from users where username=%s", [username])
    if not rows:
        return {"username": username, "nickname": username, "avatar": "🙂"}
    r = rows[0]
    nick = r["nickname"] if r["nickname"] else r["username"]
    avatar = r["avatar"] if r["avatar"] else "🙂"
    return {"username": r["username"], "nickname": nick, "avatar": avatar}

def set_user_profile(username: str, nickname: str, avatar: str):
    db_execute("update users set nickname=%s, avatar=%s where username=%s", [nickname, avatar, username])

# =========================================================
# Auth flows
# =========================================================
def login_as(username: str):
    st.session_state.authed_user = username

def logout():
    cookie_delete(COOKIE_NAME)
    st.session_state.authed_user = None
    st.session_state.show_login = False
    st.rerun()

def rotate_session_token(username: str) -> str:
    raw = secrets.token_urlsafe(24)
    db_execute("update users set session_token_hash=%s where username=%s", [sha256_token(raw), username])
    return raw

def verify_session_token(username: str, raw_token: str) -> bool:
    rows = db_fetchall("select session_token_hash from users where username=%s", [username])
    if not rows:
        return False
    stored = rows[0]["session_token_hash"] or ""
    if not stored:
        return False
    return hmac.compare_digest(stored, sha256_token(raw_token))

def try_auto_login_once():
    if st.session_state.get("_cookie_checked"):
        return
    st.session_state["_cookie_checked"] = True
    if st.session_state.get("authed_user"):
        return

    v = cookie_get(COOKIE_NAME)
    if not v:
        return
    u, tok = parse_cookie_value(v)
    if not u or not tok:
        return
    u = normalize_username(u)
    if not u:
        return
    if verify_session_token(u, tok):
        login_as(u)
    else:
        cookie_delete(COOKIE_NAME)

# =========================================================
# Records helpers
# =========================================================
def parse_amount_any(x) -> float:
    if x is None:
        return 0.0
    s = str(x).strip()
    if s == "":
        return 0.0
    s = re.sub(r"[^\d\.\-]", "", s)
    if s in ["", "-", ".", "-."]:
        return 0.0
    return float(s)

def load_records(username: str) -> pd.DataFrame:
    rows = db_fetchall(
        """select id, record_date, book, category, item, amount, rtype, created_at
           from records where username=%s
           order by record_date desc, id desc""",
        [username]
    )
    if not rows:
        return pd.DataFrame(columns=["id","record_date","book","category","item","amount","rtype","created_at"])
    df = pd.DataFrame(rows)
    df["record_date"] = pd.to_datetime(df["record_date"])
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    return df

def insert_record(username: str, d: date, book: str, cat: str, item: str, amt: float, rtype: str):
    db_execute(
        """insert into records(username, record_date, book, category, item, amount, rtype)
           values(%s,%s,%s,%s,%s,%s,%s)""",
        [username, d, book, cat, item or "", float(amt), rtype]
    )

def update_records_bulk(username: str, df: pd.DataFrame):
    # df columns: id, record_date, book, category, item, amount, rtype
    with get_conn().cursor() as cur:
        for _, r in df.iterrows():
            cur.execute(
                """update records
                   set record_date=%s, book=%s, category=%s, item=%s, amount=%s, rtype=%s
                   where id=%s and username=%s""",
                [
                    pd.to_datetime(r["record_date"]).date(),
                    str(r["book"]),
                    str(r["category"]),
                    str(r.get("item","") or ""),
                    float(r["amount"]),
                    str(r["rtype"]),
                    int(r["id"]),
                    username
                ]
            )
    get_conn().commit()

def delete_records(username: str, ids: list[int]):
    if not ids:
        return
    db_execute(
        "delete from records where username=%s and id = any(%s)",
        [username, ids]
    )

# =========================================================
# UI: Top right login bar
# =========================================================
def top_bar():
    l, r = st.columns([6, 2])
    with l:
        st.markdown("## 💰 私人理财中心（公网多用户）")
    with r:
        if st.session_state.get("authed_user"):
            p = get_user_profile(st.session_state.authed_user)
            st.markdown(
                f"<div style='text-align:right;font-size:14px'>{p['avatar']} <b>{p['nickname']}</b></div>",
                unsafe_allow_html=True
            )
            if st.button("退出", key="logout_btn_top"):
                logout()
        else:
            if st.button("登录 / 注册", key="login_btn_top"):
                st.session_state.show_login = True

def login_panel():
    if st.session_state.get("authed_user"):
        return
    if not st.session_state.get("show_login"):
        return

    with st.expander("🔐 用户登录 / 注册", expanded=True):
        t1, t2 = st.tabs(["登录", "注册"])

        with t1:
            u = st.text_input("用户名（字母/数字/下划线）", key="login_user")
            p = st.text_input("密码", type="password", key="login_pass")
            remember = st.checkbox("保持登录（30天）", value=True, key="remember_me")

            if st.button("登录", key="do_login"):
                uu = normalize_username(u)
                if not uu:
                    st.error("用户名不合法")
                    return
                rows = db_fetchall("select pass_salt, pass_hash from users where username=%s", [uu])
                if not rows:
                    st.error("用户不存在")
                    return
                if not verify_password(p, rows[0]["pass_salt"], rows[0]["pass_hash"]):
                    st.error("密码错误")
                    return

                login_as(uu)
                if remember:
                    raw = rotate_session_token(uu)
                    cookie_set(COOKIE_NAME, make_cookie_value(uu, raw), days=COOKIE_DAYS)
                st.session_state.show_login = False
                st.rerun()

        with t2:
            u = st.text_input("新用户名（字母/数字/下划线）", key="reg_user")
            p1 = st.text_input("新密码（>=6位）", type="password", key="reg_pass1")
            p2 = st.text_input("确认密码", type="password", key="reg_pass2")

            if st.button("注册", key="do_register"):
                uu = normalize_username(u)
                if not uu:
                    st.error("用户名不合法")
                    return
                if len(p1) < 6:
                    st.error("密码至少 6 位")
                    return
                if p1 != p2:
                    st.error("两次密码不一致")
                    return

                hp = pbkdf2_hash_password(p1)
                try:
                    db_execute(
                        """insert into users(username, pass_salt, pass_hash, nickname, avatar)
                           values(%s,%s,%s,%s,%s)""",
                        [uu, hp["salt"], hp["hash"], uu, "🙂"]
                    )
                    st.success("✅ 注册成功，请切换到「登录」登录。")
                except Exception as e:
                    st.error("注册失败：用户名可能已存在")

# =========================================================
# App start
# =========================================================
if "authed_user" not in st.session_state:
    st.session_state.authed_user = None
if "show_login" not in st.session_state:
    st.session_state.show_login = False

top_bar()
st.divider()

try_auto_login_once()
login_panel()

if not st.session_state.get("authed_user"):
    st.info("请点击右上角「登录 / 注册」后使用。")
    st.stop()

USERNAME = st.session_state.authed_user
profile = get_user_profile(USERNAME)

# =========================================================
# Sidebar: record input
# =========================================================
st.sidebar.header("📝 记账录入")

rtype = st.sidebar.selectbox("收支类型", ["支出", "收入"], key="rtype")
cat_opts = EXP_CATS if rtype == "支出" else INC_CATS

with st.sidebar.form("record_form", clear_on_submit=True):
    d = st.date_input("日期", value=date.today())
    book = st.selectbox("账本", BOOK_OPTIONS)
    cat_base = st.selectbox("类别", cat_opts)
    cat_custom = st.text_input("如选“其他”，自定义名称")
    item = st.text_input("项目/备注")
    amt = st.text_input("金额（可直接输入）", value="", placeholder="0")
    ok = st.form_submit_button("保存")

    if ok:
        try:
            amount = parse_amount_any(amt)
            final_cat = cat_custom.strip() if (cat_base == "其他" and cat_custom.strip()) else cat_base
            if amount < 0:
                amount = abs(amount)
            insert_record(USERNAME, d, book, final_cat, item, amount, rtype)
            st.sidebar.success("✅ 已保存")
            st.rerun()
        except Exception:
            st.sidebar.error("金额输入有误")

# =========================================================
# Load data
# =========================================================
df = load_records(USERNAME)

# =========================================================
# Dashboard
# =========================================================
inc = df[df["rtype"] == "收入"]["amount"].sum() if not df.empty else 0.0
exp = df[df["rtype"] == "支出"]["amount"].sum() if not df.empty else 0.0
bal = inc - exp

c1, c2, c3 = st.columns(3)
c1.metric("累计总收入", f"¥ {inc:,.2f}")
c2.metric("累计总支出", f"¥ {exp:,.2f}")
c3.metric("净额（收入-支出）", f"¥ {bal:,.2f}")

tab1, tab2, tab3 = st.tabs(["📋 明细（直接改/删）", "📊 统计", "👤 个人设置"])

# =========================================================
# Tab1: Inline edit + delete
# =========================================================
with tab1:
    st.subheader("📋 历史明细（行内修改/勾选删除）")

    if df.empty:
        st.info("暂无记录。")
    else:
        view = df.copy()
        view = view.rename(columns={
            "id": "ID",
            "record_date": "日期",
            "book": "账本",
            "category": "类别",
            "item": "项目",
            "amount": "金额",
            "rtype": "类型",
        })
        view["日期"] = pd.to_datetime(view["日期"]).dt.date

        if "🗑 删除" not in view.columns:
            view.insert(0, "🗑 删除", False)

        f1, f2, f3, f4 = st.columns([1.2, 1.2, 1.2, 2.0])
        with f1:
            tfilter = st.multiselect("类型筛选", ["收入", "支出"], default=["收入", "支出"])
        with f2:
            bfilter = st.multiselect("账本筛选", sorted(view["账本"].unique().tolist()))
        with f3:
            cfilter = st.multiselect("类别筛选", sorted(view["类别"].unique().tolist()))
        with f4:
            kw = st.text_input("关键词（项目/类别/账本）", placeholder="例如：Rent / Petrol / 工资")

        vv = view[view["类型"].isin(tfilter)].copy()
        if bfilter:
            vv = vv[vv["账本"].isin(bfilter)]
        if cfilter:
            vv = vv[vv["类别"].isin(cfilter)]
        if kw.strip():
            mask = (
                vv["项目"].astype(str).str.contains(kw, na=False) |
                vv["类别"].astype(str).str.contains(kw, na=False) |
                vv["账本"].astype(str).str.contains(kw, na=False)
            )
            vv = vv[mask]

        st.caption(f"当前显示：{len(vv)} 条")
        if vv.empty:
            st.info("筛选后无记录。")
        else:
            edited = st.data_editor(
                vv,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                column_config={
                    "🗑 删除": st.column_config.CheckboxColumn("🗑 删除"),
                    "ID": st.column_config.NumberColumn("ID", disabled=True),
                    "日期": st.column_config.DateColumn("日期"),
                    "金额": st.column_config.NumberColumn("金额", format="%.2f"),
                    "类型": st.column_config.SelectboxColumn("类型", options=["收入", "支出"]),
                    "账本": st.column_config.SelectboxColumn("账本", options=BOOK_OPTIONS),
                },
                key="editor_records_db"
            )

            colA, colB, colC = st.columns([1.3, 1.3, 2.4])

            with colA:
                if st.button("💾 保存修改", type="primary"):
                    # take edited rows and update in DB
                    upd = edited.drop(columns=["🗑 删除"], errors="ignore").copy()
                    upd = upd.rename(columns={
                        "ID": "id",
                        "日期": "record_date",
                        "账本": "book",
                        "类别": "category",
                        "项目": "item",
                        "金额": "amount",
                        "类型": "rtype",
                    })
                    upd["amount"] = pd.to_numeric(upd["amount"], errors="coerce").fillna(0.0)
                    update_records_bulk(USERNAME, upd[["id","record_date","book","category","item","amount","rtype"]])
                    st.success("✅ 已保存")
                    st.rerun()

            with colB:
                if st.button("🗑 执行删除（删勾选行）"):
                    del_ids = edited.loc[edited["🗑 删除"] == True, "ID"].tolist()
                    del_ids = [int(x) for x in del_ids]
                    if not del_ids:
                        st.info("未勾选任何记录。")
                    else:
                        delete_records(USERNAME, del_ids)
                        st.success(f"✅ 已删除 {len(del_ids)} 条")
                        st.rerun()

            with colC:
                export_df = view.drop(columns=["🗑 删除"], errors="ignore").copy()
                st.download_button(
                    "⬇️ 下载备份 CSV",
                    data=export_df.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"{USERNAME}_records_backup.csv",
                    mime="text/csv"
                )

# =========================================================
# Tab2: Stats + import
# =========================================================
with tab2:
    st.subheader("📊 统计中心（年 / 月 / 区间）")
    if df.empty:
        st.info("暂无数据可统计。")
    else:
        tdf = df.copy()
        tdf["日期"] = pd.to_datetime(tdf["record_date"])
        tdf["年份"] = tdf["日期"].dt.year
        tdf["月份"] = tdf["日期"].dt.month
        tdf["年月"] = tdf["日期"].dt.to_period("M").astype(str)

        colA, colB, colC = st.columns([1.2, 1.2, 2.0])
        with colA:
            mode = st.radio("统计口径", ["年份", "月份", "自定义区间"], horizontal=True)
        with colB:
            typ = st.multiselect("收支类型", ["收入", "支出"], default=["收入", "支出"])

        if mode == "年份":
            with colC:
                years = sorted(tdf["年份"].unique().tolist())
                sel = st.multiselect("选择年份", years, default=[max(years)])
            fdf = tdf[tdf["年份"].isin(sel)]
        elif mode == "月份":
            with colC:
                yms = sorted(tdf["年月"].unique().tolist())
                sel = st.multiselect("选择年月（YYYY-MM）", yms, default=[yms[-1]])
            fdf = tdf[tdf["年月"].isin(sel)]
        else:
            with colC:
                min_d = tdf["日期"].min().date()
                max_d = tdf["日期"].max().date()
                dr = st.date_input("选择区间", value=(min_d, max_d))
            start_d, end_d = dr if isinstance(dr, tuple) else (dr, dr)
            fdf = tdf[(tdf["日期"].dt.date >= start_d) & (tdf["日期"].dt.date <= end_d)]

        fdf = fdf[fdf["rtype"].isin(typ)]
        income_sum = fdf[fdf["rtype"] == "收入"]["amount"].sum()
        expense_sum = fdf[fdf["rtype"] == "支出"]["amount"].sum()
        net_sum = income_sum - expense_sum

        s1, s2, s3 = st.columns(3)
        s1.metric("收入合计", f"¥ {income_sum:,.2f}")
        s2.metric("支出合计", f"¥ {expense_sum:,.2f}")
        s3.metric("净额", f"¥ {net_sum:,.2f}")

        st.write("### 📈 趋势（按月汇总）")
        mdf = fdf.groupby(["年月", "rtype"], as_index=False)["amount"].sum().sort_values("年月")
        wide = mdf.pivot_table(index="年月", columns="rtype", values="amount", aggfunc="sum", fill_value=0)
        st.line_chart(wide)

    st.divider()
    st.subheader("📥 导入 CSV（导入到当前用户）")
    up = st.file_uploader("CSV列名建议：日期/账本/类别/项目/金额/类型", type=["csv"], key="uploader_db")
    if up is not None:
        try:
            df_in = pd.read_csv(up)
            st.dataframe(df_in.head(20), use_container_width=True)

            if st.button("✅ 执行导入"):
                # tolerant mapping
                col_map = {c: c.strip() for c in df_in.columns}
                df_in.rename(columns=col_map, inplace=True)

                # required: 日期/金额/类型
                dates = pd.to_datetime(df_in.get("日期", None), errors="coerce")
                tmp = pd.DataFrame()
                tmp["record_date"] = dates.dt.date
                tmp = tmp.dropna(subset=["record_date"])

                tmp["book"] = df_in.get("账本", "生活主账").fillna("生活主账")
                tmp["category"] = df_in.get("类别", "其他").fillna("其他")
                tmp["item"] = df_in.get("项目", "").fillna("")
                tmp["amount"] = df_in.get("金额", 0).apply(parse_amount_any).abs()

                tcol = df_in.get("类型", "支出").astype(str).str.strip()
                tcol = tcol.replace({"income":"收入","expense":"支出","Income":"收入","Expense":"支出"})
                tcol = tcol.where(tcol.isin(["收入","支出"]), "支出")
                tmp["rtype"] = tcol

                # bulk insert
                with get_conn().cursor() as cur:
                    for _, r in tmp.iterrows():
                        cur.execute(
                            """insert into records(username, record_date, book, category, item, amount, rtype)
                               values(%s,%s,%s,%s,%s,%s,%s)""",
                            [USERNAME, r["record_date"], r["book"], r["category"], r["item"], float(r["amount"]), r["rtype"]]
                        )
                get_conn().commit()
                st.success(f"✅ 已导入 {len(tmp)} 条")
                st.rerun()

        except Exception as e:
            st.error(f"导入失败：{e}")

# =========================================================
# Tab3: Profile
# =========================================================
with tab3:
    st.subheader("👤 个人设置（头像 / 昵称）")
    new_avatar = st.text_input("头像（建议一个 emoji）", value=profile["avatar"])
    new_nick = st.text_input("昵称（右上角显示）", value=profile["nickname"])
    if st.button("💾 保存个人设置", type="primary"):
        a = new_avatar.strip() if new_avatar.strip() else "🙂"
        n = new_nick.strip() if new_nick.strip() else USERNAME
        set_user_profile(USERNAME, n, a)
        st.success("✅ 已保存")
        st.rerun()

    st.divider()
    st.subheader("🔒 登录持久化管理")
    st.caption("如果你在公共电脑上登录过，可以在这里清除“保持登录”。")
    if st.button("🧹 清除保持登录（本机）"):
        db_execute("update users set session_token_hash=%s where username=%s", ["", USERNAME])
        cookie_delete(COOKIE_NAME)
        st.success("✅ 已清除，下次需要重新登录")
