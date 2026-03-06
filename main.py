import streamlit as st
import pandas as pd
from datetime import date, datetime
import re, secrets, hashlib, hmac, ast, operator as op
import psycopg2
import psycopg2.extras
import streamlit.components.v1 as components

# ---------------- 页面配置 ----------------
st.set_page_config(page_title="私人理财中心（多用户优化版）", layout="wide")

# ---------------- Secrets 读取 ----------------
APP_SECRET = st.secrets.get("APP_SECRET", "default_secret_key_please_change_it")
DATABASE_URL = st.secrets["DATABASE_URL"]
COOKIE_DAYS = int(st.secrets.get("COOKIE_DAYS", 30))
COOKIE_NAME = "pf_auth"

BOOK_OPTIONS = ["生活主账", "车子专项", "学费/购汇", "理财账本"]
EXP_CATS = ["Eat outside", "Shopping", "Bill", "Petrol", "Insurance", "Rent", "其他"]
INC_CATS = ["工资", "业余项目", "亲情赠与", "理财收益", "其他"]

# ---------------- DB ----------------
@st.cache_resource
def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

def db_fetchall(sql, params=None):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params or [])
        return cur.fetchall()

def db_execute(sql, params=None):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params or [])
    conn.commit()

def ensure_tables():
    # 可选：自动建表（第一次部署很有用）
    db_execute("""
    create table if not exists users (
        username text primary key,
        pass_salt text not null,
        pass_hash text not null,
        nickname text,
        avatar text,
        session_token_hash text
    );
    """)
    db_execute("""
    create table if not exists records (
        id bigserial primary key,
        username text not null references users(username) on delete cascade,
        record_date date not null,
        book text not null,
        category text not null,
        item text not null default '',
        amount numeric not null default 0,
        rtype text not null check (rtype in ('收入','支出'))
    );
    """)
    db_execute("create index if not exists idx_records_user_date on records(username, record_date desc, id desc);")

# ---------------- Security & Cookies ----------------
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

# ---- Cookie：优先用 extra_streamlit_components（能读写）；没有就降级为仅写 ----
COOKIE_READ_AVAILABLE = False
cookie_manager = None
try:
    import extra_streamlit_components as stx
    cookie_manager = stx.CookieManager()
    COOKIE_READ_AVAILABLE = True
except Exception:
    COOKIE_READ_AVAILABLE = False

def cookie_get(name: str) -> str:
    if COOKIE_READ_AVAILABLE and cookie_manager is not None:
        return cookie_manager.get(name) or ""
    # Streamlit 原生 components.html 不能真正回传 cookie 值，这里只能降级
    return ""

def cookie_set(name: str, value: str, days: int):
    if COOKIE_READ_AVAILABLE and cookie_manager is not None:
        expires_at = datetime.utcnow() + pd.Timedelta(days=days)
        cookie_manager.set(name, value, expires_at=expires_at)
        return
    # 降级：仅写入（浏览器层面写是可以的）
    html = f"""<script>
    const d = new Date(); d.setTime(d.getTime() + ({days}*24*60*60*1000));
    document.cookie = "{name}={value};expires=" + d.toUTCString() + ";path=/;SameSite=Lax";
    </script>"""
    components.html(html, height=0, width=0)

def cookie_delete(name: str):
    if COOKIE_READ_AVAILABLE and cookie_manager is not None:
        cookie_manager.delete(name)
        return
    html = f"""<script>document.cookie = "{name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";</script>"""
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

# ---------------- Profile & Auth ----------------
def get_user_profile(username: str):
    rows = db_fetchall("select username, nickname, avatar from users where username=%s", [username])
    if not rows:
        return {"username": username, "nickname": username, "avatar": "🙂"}
    r = rows[0]
    return {"username": r["username"], "nickname": r["nickname"] or r["username"], "avatar": r["avatar"] or "🙂"}

def set_user_profile(username: str, nickname: str, avatar: str):
    db_execute("update users set nickname=%s, avatar=%s where username=%s", [nickname, avatar, username])

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
    stored = rows[0].get("session_token_hash") or ""
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
    if u and tok and verify_session_token(normalize_username(u), tok):
        login_as(normalize_username(u))

# ---------------- Records 核心逻辑 ----------------
_ALLOWED_OPS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.USub: op.neg,
    ast.UAdd: op.pos,
}

def _safe_eval_expr(node):
    if isinstance(node, ast.Num):  # py<3.8
        return node.n
    if isinstance(node, ast.Constant):  # py>=3.8
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("只允许数字")
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_safe_eval_expr(node.left), _safe_eval_expr(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_safe_eval_expr(node.operand))
    if isinstance(node, ast.Expression):
        return _safe_eval_expr(node.body)
    raise ValueError("不支持的表达式")

def parse_amount_any(x) -> float:
    """
    保留你原来的“可输入算式”功能，但禁用 eval，改为安全解析：
    允许：数字、小数、+ - * / ( )
    """
    if x is None:
        return 0.0
    s = str(x).strip()
    if not s:
        return 0.0
    # 过滤非法字符（只允许这些字符）
    if re.search(r"[^0-9\.\+\-\*\/\(\)\s]", s):
        raise ValueError("金额表达式含非法字符")
    tree = ast.parse(s, mode="eval")
    val = _safe_eval_expr(tree)
    return float(val)

def load_records(username: str) -> pd.DataFrame:
    rows = db_fetchall(
        """select id, record_date, book, category, item, amount, rtype
           from records
           where username=%s
           order by record_date desc, id desc""",
        [username],
    )
    if not rows:
        return pd.DataFrame(columns=["id","record_date","book","category","item","amount","rtype"])
    df = pd.DataFrame(rows)
    df["record_date"] = pd.to_datetime(df["record_date"])
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    df["rtype"] = df["rtype"].astype(str)
    return df

def insert_record(username: str, d: date, book: str, cat: str, item: str, amt: float, rtype: str):
    db_execute(
        """insert into records(username, record_date, book, category, item, amount, rtype)
           values(%s,%s,%s,%s,%s,%s,%s)""",
        [username, d, book, cat, item or "", float(amt), rtype],
    )

def update_records_bulk(username: str, df: pd.DataFrame):
    conn = get_conn()
    with conn.cursor() as cur:
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
                    username,
                ],
            )
    conn.commit()

def delete_records(username: str, ids: list[int]):
    if not ids:
        return
    # 强化：显式 cast，避免 any(array) 类型问题
    db_execute("delete from records where username=%s and id = any(%s::bigint[])", [username, ids])

# ---------------- UI ----------------
def top_bar():
    l, r = st.columns([6, 2])
    with l:
        st.markdown("## 💰 私人理财中心")
    with r:
        if st.session_state.get("authed_user"):
            p = get_user_profile(st.session_state.authed_user)
            st.markdown(
                f"<div style='text-align:right;font-size:14px'>{p['avatar']} <b>{p['nickname']}</b></div>",
                unsafe_allow_html=True,
            )
            if st.button("退出", key="logout_top"):
                logout()
        else:
            if st.button("登录 / 注册", key="login_top"):
                st.session_state.show_login = True

def login_panel():
    if st.session_state.get("authed_user") or not st.session_state.get("show_login"):
        return

    with st.expander("🔐 用户登录 / 注册", expanded=True):
        t1, t2 = st.tabs(["登录", "注册"])

        with t1:
            u = st.text_input("用户名", key="login_user")
            p = st.text_input("密码", type="password", key="login_pass")

            if st.button("登录", key="do_login"):
                uu = normalize_username(u)
                rows = db_fetchall("select pass_salt, pass_hash from users where username=%s", [uu])
                if rows and verify_password(p, rows[0]["pass_salt"], rows[0]["pass_hash"]):
                    login_as(uu)
                    raw = rotate_session_token(uu)
                    cookie_set(COOKIE_NAME, make_cookie_value(uu, raw), days=COOKIE_DAYS)
                    st.session_state.show_login = False
                    st.rerun()
                else:
                    st.error("登录失败：用户名或密码错误")

        with t2:
            u = st.text_input("新用户名", key="reg_user")
            p1 = st.text_input("新密码", type="password", key="reg_pass1")
            p2 = st.text_input("确认新密码", type="password", key="reg_pass2")

            if st.button("注册", key="do_register"):
                uu = normalize_username(u)
                if not uu:
                    st.error("用户名不能为空/不合法")
                    return
                if not p1:
                    st.error("密码不能为空")
                    return
                if p1 != p2:
                    st.error("两次密码不一致")
                    return

                hp = pbkdf2_hash_password(p1)
                try:
                    db_execute(
                        "insert into users(username, pass_salt, pass_hash, nickname, avatar) values(%s,%s,%s,%s,%s)",
                        [uu, hp["salt"], hp["hash"], uu, "🙂"],
                    )
                    st.success("注册成功，请回到“登录”标签登录")
                except Exception:
                    st.error("用户名已存在或数据库错误")

# ---------------- 启动 ----------------
ensure_tables()

if "authed_user" not in st.session_state:
    st.session_state.authed_user = None
if "show_login" not in st.session_state:
    st.session_state.show_login = False

top_bar()
st.divider()
try_auto_login_once()
login_panel()

if not st.session_state.get("authed_user"):
    if not COOKIE_READ_AVAILABLE:
        st.info("提示：当前环境未安装 extra_streamlit_components，Cookie 自动登录读取会降级（登录/退出/记账不受影响）。")
    st.info("请先登录使用。")
    st.stop()

USERNAME = st.session_state.authed_user
profile = get_user_profile(USERNAME)

# ---------------- Sidebar: 记账录入 ----------------
st.sidebar.header("📝 记账录入")

rtype = st.sidebar.selectbox("1. 收支类型", ["支出", "收入"], key="sidebar_rtype")
cat_opts = EXP_CATS if rtype == "支出" else INC_CATS

with st.sidebar.form("record_form", clear_on_submit=True):
    d = st.date_input("2. 日期", value=date.today())
    book = st.selectbox("3. 账本", BOOK_OPTIONS)
    cat_base = st.selectbox("4. 类别", cat_opts)
    cat_custom = st.text_input("如选“其他”，自定义名称")
    item = st.text_input("5. 项目/备注")
    amt = st.text_input("6. 金额", value="", placeholder="直接输入数字或计算式，例如 19.9*2+5")

    if st.form_submit_button("保存"):
        try:
            amount = abs(parse_amount_any(amt))
            final_cat = cat_custom.strip() if (cat_base == "其他" and cat_custom.strip()) else cat_base
            insert_record(USERNAME, d, book, final_cat, item, amount, rtype)
            st.sidebar.success("✅ 已保存")
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"金额错误：{e}")

# ---------------- 数据加载与总览 ----------------
df = load_records(USERNAME)

inc = df[df["rtype"] == "收入"]["amount"].sum() if not df.empty else 0.0
exp = df[df["rtype"] == "支出"]["amount"].sum() if not df.empty else 0.0

c1, c2, c3 = st.columns(3)
c1.metric("累计收入", f"¥ {inc:,.2f}")
c2.metric("累计支出", f"¥ {exp:,.2f}")
c3.metric("净结余", f"¥ {(inc-exp):,.2f}")

tab1, tab2, tab3 = st.tabs(["📋 明细管理", "📊 统计导入", "👤 个人设置"])

# ---------------- Tab1：明细管理 ----------------
with tab1:
    st.subheader("📋 明细记录")

    if df.empty:
        st.info("暂无记录")
    else:
        view = df.copy()
        view.insert(0, "🗑 删除", False)

        # 筛选器
        f1, f2, f3, f4 = st.columns([2, 2, 2, 3])
        with f1:
            tfilter = st.multiselect("收支类型", ["收入", "支出"], default=["收入", "支出"])
        with f2:
            bfilter = st.multiselect("账本筛选", sorted(view["book"].unique().tolist()))
        with f3:
            catfilter = st.multiselect("类别筛选", sorted(view["category"].unique().tolist()))
        with f4:
            kw = st.text_input("关键词搜索（备注/类别/账本）", value="")

        vv = view[view["rtype"].isin(tfilter)].copy()
        if bfilter:
            vv = vv[vv["book"].isin(bfilter)]
        if catfilter:
            vv = vv[vv["category"].isin(catfilter)]
        if kw.strip():
            k = kw.strip().lower()
            vv = vv[
                vv["item"].astype(str).str.lower().str.contains(k, na=False)
                | vv["category"].astype(str).str.lower().str.contains(k, na=False)
                | vv["book"].astype(str).str.lower().str.contains(k, na=False)
            ].copy()

        edited = st.data_editor(
            vv,
            use_container_width=True,
            hide_index=True,
            column_config={
                "🗑 删除": st.column_config.CheckboxColumn("🗑 删除"),
                "id": st.column_config.NumberColumn("ID", disabled=True),
                "record_date": st.column_config.DateColumn("日期"),
                "amount": st.column_config.NumberColumn("金额", format="%.2f"),
                "rtype": st.column_config.SelectboxColumn("收支类型", options=["收入", "支出"]),
                "book": st.column_config.SelectboxColumn("账本", options=BOOK_OPTIONS),
            },
        )

        colA, colB, colC = st.columns([2, 2, 3])
        with colA:
            if st.button("💾 保存修改", type="primary"):
                upd = edited.drop(columns=["🗑 删除"])
                update_records_bulk(USERNAME, upd)
                st.success("已更新")
                st.rerun()
        with colB:
            if st.button("🗑 删除勾选行"):
                del_ids = edited.loc[edited["🗑 删除"] == True, "id"].tolist()
                delete_records(USERNAME, [int(x) for x in del_ids])
                st.success(f"已删除 {len(del_ids)} 条")
                st.rerun()
        with colC:
            st.caption("提示：直接在表格里修改日期/账本/类别/备注/金额/收支类型，然后点“保存修改”。")

# ---------------- Tab2：统计 + 导入导出 ----------------
with tab2:
    st.subheader("📊 统计与导入导出")

    if df.empty:
        st.info("暂无数据可统计或导入比对。")
    else:
        # 统计筛选
        s1, s2, s3, s4 = st.columns([2, 2, 2, 2])
        with s1:
            dmin = st.date_input("起始日期", value=df["record_date"].min().date())
        with s2:
            dmax = st.date_input("结束日期", value=df["record_date"].max().date())
        with s3:
            book_sel = st.multiselect("账本", BOOK_OPTIONS, default=BOOK_OPTIONS)
        with s4:
            type_sel = st.multiselect("收支类型", ["收入", "支出"], default=["收入", "支出"])

        dff = df.copy()
        dff = dff[(dff["record_date"].dt.date >= dmin) & (dff["record_date"].dt.date <= dmax)]
        if book_sel:
            dff = dff[dff["book"].isin(book_sel)]
        if type_sel:
            dff = dff[dff["rtype"].isin(type_sel)]

        a1, a2, a3 = st.columns(3)
        a1.metric("区间收入", f"¥ {dff[dff['rtype']=='收入']['amount'].sum():,.2f}")
        a2.metric("区间支出", f"¥ {dff[dff['rtype']=='支出']['amount'].sum():,.2f}")
        a3.metric("区间净结余", f"¥ {(dff[dff['rtype']=='收入']['amount'].sum()-dff[dff['rtype']=='支出']['amount'].sum()):,.2f}")

        st.divider()

        # 按月汇总
        tmp = dff.copy()
        tmp["month"] = tmp["record_date"].dt.to_period("M").astype(str)
        monthly = (
            tmp.groupby(["month", "rtype"])["amount"]
            .sum()
            .reset_index()
            .pivot(index="month", columns="rtype", values="amount")
            .fillna(0.0)
            .sort_index()
        )
        monthly["净结余"] = monthly.get("收入", 0.0) - monthly.get("支出", 0.0)

        st.markdown("#### 📅 按月汇总")
        st.dataframe(monthly, use_container_width=True)

        st.markdown("#### 📈 图表（按月）")
        st.line_chart(monthly, use_container_width=True)

        st.divider()

        # 类别汇总（支出/收入分开更有意义）
        st.markdown("#### 🧾 按类别汇总")
        c1, c2 = st.columns(2)
        with c1:
            exp_cat = tmp[tmp["rtype"] == "支出"].groupby("category")["amount"].sum().sort_values(ascending=False)
            st.write("支出类别 Top")
            st.dataframe(exp_cat.reset_index().rename(columns={"amount": "sum"}), use_container_width=True)
            st.bar_chart(exp_cat, use_container_width=True)
        with c2:
            inc_cat = tmp[tmp["rtype"] == "收入"].groupby("category")["amount"].sum().sort_values(ascending=False)
            st.write("收入类别 Top")
            st.dataframe(inc_cat.reset_index().rename(columns={"amount": "sum"}), use_container_width=True)
            st.bar_chart(inc_cat, use_container_width=True)

        st.divider()

        # 导出
        st.markdown("#### ⬇️ 导出 CSV")
        export_df = dff.copy()
        export_df["record_date"] = export_df["record_date"].dt.date.astype(str)
        st.download_button(
            "下载当前筛选数据（CSV）",
            data=export_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"records_{USERNAME}_{date.today().isoformat()}.csv",
            mime="text/csv",
        )

    st.divider()

    # 导入
    st.markdown("#### ⬆️ 导入 CSV（追加到数据库）")
    st.caption("支持列名：record_date/book/category/item/amount/rtype（大小写不敏感）；也支持你自定义映射。")

    up = st.file_uploader("上传 CSV 文件", type=["csv"])
    if up is not None:
        try:
            imp = pd.read_csv(up)
            imp.columns = [c.strip() for c in imp.columns]
            st.write("预览：")
            st.dataframe(imp.head(20), use_container_width=True)

            # 自动识别常见列
            cols_lower = {c.lower(): c for c in imp.columns}

            def pick(*names):
                for n in names:
                    if n in cols_lower:
                        return cols_lower[n]
                return None

            col_date = pick("record_date", "date", "日期")
            col_book = pick("book", "账本")
            col_cat = pick("category", "cat", "类别")
            col_item = pick("item", "remark", "备注", "项目")
            col_amt  = pick("amount", "amt", "金额")
            col_type = pick("rtype", "type", "收支类型")

            st.write("字段映射（可修改）：")
            m1, m2, m3 = st.columns(3)
            with m1:
                map_date = st.selectbox("日期列", options=[None] + imp.columns.tolist(), index=(imp.columns.tolist().index(col_date)+1 if col_date in imp.columns else 0))
                map_amt  = st.selectbox("金额列", options=[None] + imp.columns.tolist(), index=(imp.columns.tolist().index(col_amt)+1 if col_amt in imp.columns else 0))
            with m2:
                map_book = st.selectbox("账本列", options=[None] + imp.columns.tolist(), index=(imp.columns.tolist().index(col_book)+1 if col_book in imp.columns else 0))
                map_type = st.selectbox("收支类型列", options=[None] + imp.columns.tolist(), index=(imp.columns.tolist().index(col_type)+1 if col_type in imp.columns else 0))
            with m3:
                map_cat  = st.selectbox("类别列", options=[None] + imp.columns.tolist(), index=(imp.columns.tolist().index(col_cat)+1 if col_cat in imp.columns else 0))
                map_item = st.selectbox("备注列", options=[None] + imp.columns.tolist(), index=(imp.columns.tolist().index(col_item)+1 if col_item in imp.columns else 0))

            default_book = st.selectbox("若 CSV 没有账本列：默认账本", BOOK_OPTIONS, index=0)
            default_type = st.selectbox("若 CSV 没有收支类型列：默认类型", ["支出", "收入"], index=0)

            if st.button("开始导入（追加）", type="primary"):
                # 校验
                if map_date is None or map_amt is None:
                    st.error("至少需要映射：日期列、金额列")
                else:
                    work = imp.copy()

                    # 日期
                    work["_date"] = pd.to_datetime(work[map_date], errors="coerce").dt.date
                    # 金额：允许表达式/字符串数字
                    def parse_amt_cell(v):
                        return abs(parse_amount_any(v))
                    work["_amt"] = work[map_amt].apply(parse_amt_cell)

                    # 账本
                    if map_book is None:
                        work["_book"] = default_book
                    else:
                        work["_book"] = work[map_book].astype(str).fillna(default_book)
                        work.loc[work["_book"].str.strip() == "", "_book"] = default_book

                    # 类型
                    if map_type is None:
                        work["_rtype"] = default_type
                    else:
                        t = work[map_type].astype(str).fillna(default_type).str.strip()
                        t = t.replace({"expense": "支出", "income": "收入", "0": "支出", "1": "收入"})
                        work["_rtype"] = t.where(t.isin(["支出", "收入"]), default_type)

                    # 类别/备注
                    work["_cat"] = (work[map_cat].astype(str) if map_cat is not None else "其他").fillna("其他")
                    work["_item"] = (work[map_item].astype(str) if map_item is not None else "").fillna("")

                    # 丢弃无效行
                    before = len(work)
                    work = work.dropna(subset=["_date"])
                    work = work[work["_amt"].notna()]
                    after = len(work)

                    # 写入
                    ok = 0
                    for _, r in work.iterrows():
                        insert_record(
                            USERNAME,
                            r["_date"],
                            str(r["_book"]),
                            str(r["_cat"]) if str(r["_cat"]).strip() else "其他",
                            str(r["_item"]) if str(r["_item"]) != "nan" else "",
                            float(r["_amt"]),
                            str(r["_rtype"]),
                        )
                        ok += 1

                    st.success(f"✅ 导入完成：成功 {ok} 行（原始 {before} 行，过滤后 {after} 行）")
                    st.rerun()

        except Exception as e:
            st.error(f"导入失败：{e}")

# ---------------- Tab3：个人设置 ----------------
with tab3:
    st.subheader("👤 个人设置")

    p = get_user_profile(USERNAME)
    c1, c2 = st.columns([2, 3])

    with c1:
        avatar = st.text_input("头像（可用 emoji）", value=p["avatar"] or "🙂")
        nickname = st.text_input("昵称", value=p["nickname"] or USERNAME)
        if st.button("保存资料", type="primary"):
            set_user_profile(USERNAME, nickname.strip() or USERNAME, avatar.strip() or "🙂")
            st.success("✅ 已保存")
            st.rerun()

    with c2:
        st.markdown("##### 🔑 修改密码")
        oldp = st.text_input("旧密码", type="password")
        newp1 = st.text_input("新密码", type="password")
        newp2 = st.text_input("确认新密码", type="password")

        if st.button("更新密码"):
            rows = db_fetchall("select pass_salt, pass_hash from users where username=%s", [USERNAME])
            if not rows or not verify_password(oldp, rows[0]["pass_salt"], rows[0]["pass_hash"]):
                st.error("旧密码不正确")
            elif not newp1:
                st.error("新密码不能为空")
            elif newp1 != newp2:
                st.error("两次新密码不一致")
            else:
                hp = pbkdf2_hash_password(newp1)
                db_execute(
                    "update users set pass_salt=%s, pass_hash=%s where username=%s",
                    [hp["salt"], hp["hash"], USERNAME],
                )
                # 为安全起见：改完密码让旧 cookie 失效
                db_execute("update users set session_token_hash=%s where username=%s", ["", USERNAME])
                cookie_delete(COOKIE_NAME)
                st.success("✅ 密码已更新（已清除保持登录，请重新登录）")
                st.session_state.authed_user = None
                st.session_state.show_login = True
                st.rerun()

    st.divider()
    if st.button("🧹 清除保持登录"):
        db_execute("update users set session_token_hash=%s where username=%s", ["", USERNAME])
        cookie_delete(COOKIE_NAME)
        st.success("✅ 已清除，下次需要重新登录")
