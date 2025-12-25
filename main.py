import streamlit as st
import pandas as pd
from datetime import datetime
import re
import json
from pathlib import Path
import hashlib
import hmac
import secrets
import time
import streamlit.components.v1 as components

# =================================
# 0) Page
# =================================
st.set_page_config(page_title="私人理财中心（多用户）", layout="wide")

# =================================
# 1) Global paths
# =================================
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

USERS_DIR = DATA_DIR / "users"
USERS_DIR.mkdir(exist_ok=True)

AUTH_PATH = DATA_DIR / "auth_users.json"      # store user credentials & session tokens (hashed)
APP_CONFIG_PATH = DATA_DIR / "app_config.json"  # app secret

RECORD_COLS = ["ID", "日期", "账本", "类别", "项目", "金额", "类型"]
BUDGET_COLS = ["年月", "类别", "类型", "预算金额"]

COOKIE_NAME = "pf_auth"   # persistent login cookie name


# =================================
# 2) Cookie helpers via components
# =================================
def cookie_get(name: str) -> str:
    # Returns cookie value string or "".
    html = f"""
    <script>
    function getCookie(name) {{
      const value = `; ${{document.cookie}}`;
      const parts = value.split(`; ${{name}}=`);
      if (parts.length === 2) return parts.pop().split(';').shift();
      return "";
    }}
    const v = getCookie("{name}");
    Streamlit.setComponentValue(v || "");
    </script>
    """
    return components.html(html, height=0, width=0)


def cookie_set(name: str, value: str, days: int = 30):
    # Set cookie for `days` days.
    html = f"""
    <script>
    const d = new Date();
    d.setTime(d.getTime() + ({days}*24*60*60*1000));
    const expires = "expires="+ d.toUTCString();
    document.cookie = "{name}={value};" + expires + ";path=/;SameSite=Lax";
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


# =================================
# 3) Security: password hashing + token signing
# =================================
def load_app_secret() -> str:
    if APP_CONFIG_PATH.exists():
        cfg = json.loads(APP_CONFIG_PATH.read_text(encoding="utf-8"))
        if cfg.get("app_secret"):
            return cfg["app_secret"]
    secret = secrets.token_hex(32)
    APP_CONFIG_PATH.write_text(json.dumps({"app_secret": secret}, ensure_ascii=False), encoding="utf-8")
    return secret


APP_SECRET = load_app_secret()


def pbkdf2_hash_password(password: str, salt_hex: str | None = None) -> dict:
    if salt_hex is None:
        salt = secrets.token_bytes(16)
    else:
        salt = bytes.fromhex(salt_hex)
    pwd = (password + APP_SECRET).encode("utf-8")
    dk = hashlib.pbkdf2_hmac("sha256", pwd, salt, 200_000)
    return {"salt": salt.hex(), "hash": dk.hex()}


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    test = pbkdf2_hash_password(password, salt_hex=salt_hex)["hash"]
    return hmac.compare_digest(test, hash_hex)


def load_auth_db() -> dict:
    if AUTH_PATH.exists():
        try:
            return json.loads(AUTH_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_auth_db(db: dict):
    AUTH_PATH.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_username(u: str) -> str:
    u = (u or "").strip()
    u = re.sub(r"[^A-Za-z0-9_]", "", u)
    return u.lower()


def sign_token(raw_token: str) -> str:
    # HMAC signature so cookie can't be forged easily
    sig = hmac.new(APP_SECRET.encode("utf-8"), raw_token.encode("utf-8"), hashlib.sha256).hexdigest()
    return sig


def make_session_cookie_value(username: str, raw_token: str) -> str:
    # store username|token|sig
    sig = sign_token(f"{username}|{raw_token}")
    return f"{username}|{raw_token}|{sig}"


def parse_session_cookie_value(v: str):
    # returns (username, raw_token) if valid format else (None, None)
    try:
        parts = (v or "").split("|")
        if len(parts) != 3:
            return None, None
        username, raw_token, sig = parts
        expected = sign_token(f"{username}|{raw_token}")
        if not hmac.compare_digest(expected, sig):
            return None, None
        return username, raw_token
    except Exception:
        return None, None


# =================================
# 4) User-scoped persistence + profile
# =================================
def user_dir(username: str) -> Path:
    d = USERS_DIR / username
    d.mkdir(exist_ok=True)
    return d


def paths_for_user(username: str):
    ud = user_dir(username)
    return {
        "records": ud / "records.csv",
        "budgets": ud / "budgets.csv",
        "config": ud / "config.json",
    }


def load_csv(path: Path, cols: list[str]) -> pd.DataFrame:
    if path.exists():
        df = pd.read_csv(path)
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        return df[cols]
    return pd.DataFrame(columns=cols)


def prepare_for_editor(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=RECORD_COLS)

    x = df.copy()
    for c in RECORD_COLS:
        if c not in x.columns:
            x[c] = "" if c not in ["ID", "金额"] else 0

    x["ID"] = pd.to_numeric(x["ID"], errors="coerce").fillna(0).astype(int)

    d = pd.to_datetime(x["日期"], errors="coerce")
    x = x[~d.isna()].copy()
    x["日期"] = pd.to_datetime(x["日期"], errors="coerce").dt.date

    x["金额"] = pd.to_numeric(x["金额"], errors="coerce").fillna(0.0).astype(float)

    for c in ["账本", "类别", "项目", "类型"]:
        x[c] = x[c].astype(str).replace({"nan": "", "None": ""}).fillna("")

    x.loc[~x["类型"].isin(["收入", "支出"]), "类型"] = "支出"
    return x[RECORD_COLS]


def enrich_records(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    out["日期"] = pd.to_datetime(out["日期"], errors="coerce")
    out = out.dropna(subset=["日期"])
    out["年份"] = out["日期"].dt.year
    out["月份"] = out["日期"].dt.month
    out["年月"] = out["日期"].dt.to_period("M").astype(str)
    return out


def load_user_config(username: str) -> dict:
    p = paths_for_user(username)["config"]
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    else:
        cfg = {}

    # defaults
    cfg.setdefault("init_balance", 0.0)
    cfg.setdefault("nickname", username)
    cfg.setdefault("avatar", "🙂")  # emoji
    return cfg


def save_user_config(username: str, cfg: dict):
    p = paths_for_user(username)["config"]
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def persist_user_state(username: str):
    p = paths_for_user(username)
    st.session_state.records.to_csv(p["records"], index=False, encoding="utf-8-sig")
    st.session_state.budgets.to_csv(p["budgets"], index=False, encoding="utf-8-sig")

    cfg = load_user_config(username)
    cfg["init_balance"] = float(st.session_state.init_balance)
    # nickname/avatar stored in cfg (maybe updated elsewhere)
    save_user_config(username, cfg)


def load_user_state(username: str):
    p = paths_for_user(username)
    st.session_state.records = prepare_for_editor(load_csv(p["records"], RECORD_COLS))
    st.session_state.budgets = load_csv(p["budgets"], BUDGET_COLS)

    cfg = load_user_config(username)
    st.session_state.init_balance = float(cfg.get("init_balance", 0.0))
    st.session_state.nickname = cfg.get("nickname", username)
    st.session_state.avatar = cfg.get("avatar", "🙂")


def next_id() -> int:
    df = st.session_state.records
    if df.empty:
        return 1
    return int(pd.to_numeric(df["ID"], errors="coerce").max()) + 1


def parse_amount(s: str) -> float:
    s = (s or "").strip()
    if s == "":
        return 0.0
    clean = re.sub(r"[^\d\.\-]", "", s)
    if clean in ["", "-", ".", "-."]:
        return 0.0
    return float(clean)


def normalize_type(t: str) -> str:
    t = (t or "").strip().lower()
    if t in ["收入", "income", "in", "+", "earning", "earnings", "收"]:
        return "收入"
    if t in ["支出", "expense", "out", "-", "spend", "spending", "支"]:
        return "支出"
    return ""


# =================================
# 5) Auth: register/login/logout + persistence
# =================================
def create_or_rotate_session_token(db: dict, username: str) -> str:
    raw = secrets.token_urlsafe(24)
    # store hashed token (not plaintext)
    token_hash = hashlib.sha256((raw + APP_SECRET).encode("utf-8")).hexdigest()
    db[username]["session_token_hash"] = token_hash
    db[username]["session_token_issued_at"] = datetime.now().isoformat()
    save_auth_db(db)
    return raw


def verify_session_token(db: dict, username: str, raw_token: str) -> bool:
    if username not in db:
        return False
    rec = db[username]
    stored = rec.get("session_token_hash", "")
    if not stored:
        return False
    token_hash = hashlib.sha256((raw_token + APP_SECRET).encode("utf-8")).hexdigest()
    return hmac.compare_digest(stored, token_hash)


def login(username: str):
    st.session_state.authed_user = username
    load_user_state(username)


def logout():
    # clear server-side authed state
    st.session_state.authed_user = None
    for k in ["records", "budgets", "init_balance", "nickname", "avatar"]:
        st.session_state.pop(k, None)
    # clear cookie
    cookie_delete(COOKIE_NAME)
    st.session_state.show_login = False
    st.rerun()


def ensure_user_storage(username: str):
    ud = user_dir(username)
    (ud / "records.csv").touch(exist_ok=True)
    (ud / "budgets.csv").touch(exist_ok=True)
    cfgp = ud / "config.json"
    if not cfgp.exists():
        cfgp.write_text(json.dumps({"init_balance": 0.0, "nickname": username, "avatar": "🙂"}, ensure_ascii=False), encoding="utf-8")


def try_cookie_auto_login():
    # only try once per session
    if st.session_state.get("_cookie_checked"):
        return
    st.session_state["_cookie_checked"] = True

    if st.session_state.get("authed_user"):
        return

    v = cookie_get(COOKIE_NAME)
    if not v:
        return

    username, raw_token = parse_session_cookie_value(v)
    if not username or not raw_token:
        return

    db = load_auth_db()
    username = normalize_username(username)
    if not username or username not in db:
        return

    if verify_session_token(db, username, raw_token):
        ensure_user_storage(username)
        login(username)
        # 不强制 rerun，让页面自然继续渲染即可
    else:
        # invalid cookie -> clear
        cookie_delete(COOKIE_NAME)


def top_login_bar():
    # right top bar (visual)
    col_left, col_right = st.columns([5, 1])

    with col_left:
        st.markdown("## 💰 私人理财中心")

    with col_right:
        if st.session_state.get("authed_user"):
            avatar = st.session_state.get("avatar", "🙂")
            nickname = st.session_state.get("nickname", st.session_state.authed_user)
            st.markdown(
                f"""
                <div style="text-align:right; font-size:14px; line-height:1.2;">
                  <div>{avatar} <b>{nickname}</b></div>
                </div>
                """,
                unsafe_allow_html=True
            )
            if st.button("退出", key="logout_top"):
                logout()
        else:
            if st.button("登录 / 注册", key="login_top"):
                st.session_state.show_login = True


def login_panel():
    if st.session_state.get("authed_user"):
        return

    if not st.session_state.get("show_login"):
        return

    db = load_auth_db()

    with st.expander("🔐 用户登录 / 注册", expanded=True):
        tabs = st.tabs(["登录", "注册"])

        with tabs[0]:
            u = st.text_input("用户名（字母/数字/下划线）", key="login_user_top")
            p = st.text_input("密码", type="password", key="login_pass_top")
            remember = st.checkbox("保持登录（30天）", value=True)

            if st.button("登录", key="login_btn_top"):
                uu = normalize_username(u)
                if not uu:
                    st.error("用户名只能包含字母/数字/下划线")
                    return
                if uu not in db:
                    st.error("用户不存在")
                    return
                rec = db[uu]
                if verify_password(p, rec["salt"], rec["hash"]):
                    ensure_user_storage(uu)
                    login(uu)

                    if remember:
                        raw_token = create_or_rotate_session_token(db, uu)
                        cookie_set(COOKIE_NAME, make_session_cookie_value(uu, raw_token), days=30)

                    st.session_state.show_login = False
                    st.success("✅ 登录成功")
                    st.rerun()
                else:
                    st.error("密码错误")

        with tabs[1]:
            u = st.text_input("新用户名（字母/数字/下划线）", key="reg_user_top")
            p1 = st.text_input("新密码（>=6位）", type="password", key="reg_pass1_top")
            p2 = st.text_input("确认密码", type="password", key="reg_pass2_top")
            if st.button("注册", key="reg_btn_top"):
                uu = normalize_username(u)
                if not uu:
                    st.error("用户名只能包含字母/数字/下划线")
                    return
                if uu in db:
                    st.error("用户名已存在")
                    return
                if len(p1) < 6:
                    st.error("密码至少 6 位")
                    return
                if p1 != p2:
                    st.error("两次密码不一致")
                    return

                h = pbkdf2_hash_password(p1)
                db[uu] = {"salt": h["salt"], "hash": h["hash"], "created_at": datetime.now().isoformat()}
                save_auth_db(db)

                ensure_user_storage(uu)
                st.success("✅ 注册成功，请切换到「登录」进行登录。")


# =================================
# 6) Render top bar + auto login
# =================================
if "authed_user" not in st.session_state:
    st.session_state.authed_user = None
if "show_login" not in st.session_state:
    st.session_state.show_login = False

top_login_bar()
st.divider()

# attempt auto-login via cookie
try_cookie_auto_login()

# show login panel if needed
login_panel()

if not st.session_state.get("authed_user"):
    st.info("请点击右上角「登录 / 注册」后使用。")
    st.stop()

USERNAME = st.session_state.authed_user

# ensure user state loaded
if "records" not in st.session_state:
    load_user_state(USERNAME)

# =================================
# 7) Sidebar input
# =================================
st.sidebar.header("📝 记账录入")

t_type = st.sidebar.selectbox("选择收支类型", ["支出", "收入"], key="type_selector")

if t_type == "支出":
    cat_opts = ["Eat outside", "Shopping", "Bill", "Petrol", "Insurance", "Rent", "其他"]
else:
    cat_opts = ["工资", "业余项目", "亲情赠与", "理财收益", "其他"]

with st.sidebar.form("record_form", clear_on_submit=True):
    d = st.date_input("日期", datetime.now())
    b = st.selectbox("归属账本", ["生活主账", "车子专项", "学费/购汇", "理财账本"])
    c_base = st.selectbox("选择分类", cat_opts)
    custom_c = st.text_input("如选'其他'，请手动输入名称")
    item = st.text_input("备注项目")
    amt_input = st.text_input("金额", value="", placeholder="0")
    submit = st.form_submit_button("确认存入账本")

    if submit:
        try:
            amt = parse_amount(amt_input)
            final_cat = custom_c if (c_base == "其他" and custom_c.strip() != "") else c_base
            new_row = {
                "ID": next_id(),
                "日期": d,
                "账本": b,
                "类别": final_cat,
                "项目": item,
                "金额": float(amt),
                "类型": t_type
            }
            st.session_state.records = prepare_for_editor(
                pd.concat([st.session_state.records, pd.DataFrame([new_row])], ignore_index=True)
            )
            persist_user_state(USERNAME)
            st.sidebar.success(f"✅ 已记录 {t_type}：{final_cat} ¥{amt:,.2f}")
        except Exception:
            st.sidebar.error("金额输入有误")

# =================================
# 8) Dashboard
# =================================
st.title(f"📊 财务看板")

df0 = enrich_records(st.session_state.records)
inc = df0[df0["类型"] == "收入"]["金额"].sum() if not df0.empty else 0.0
exp = df0[df0["类型"] == "支出"]["金额"].sum() if not df0.empty else 0.0
bal = float(st.session_state.init_balance) + inc - exp

c1, c2, c3 = st.columns(3)
c1.metric("目前总结余", f"¥ {bal:,.2f}")
c2.metric("累计总收入", f"¥ {inc:,.2f}")
c3.metric("累计总支出", f"¥ {exp:,.2f}", delta=f"-{exp:,.2f}")

tab1, tab2 = st.tabs(["📋 明细（行内修改/删除）", "📈 统计/导入/个人设置"])

# -------- Tab1: inline edit/delete
with tab1:
    st.subheader("📋 历史明细（直接改、直接删）")
    full = prepare_for_editor(st.session_state.records)

    if full.empty:
        st.info("暂无记录。")
    else:
        base = full.copy()
        base["_dt"] = pd.to_datetime(base["日期"], errors="coerce")
        base = base.sort_values(["_dt", "ID"], ascending=[False, False]).drop(columns=["_dt"]).reset_index(drop=True)

        if "🗑 删除" not in base.columns:
            base.insert(0, "🗑 删除", False)

        f1, f2, f3, f4 = st.columns([1.2, 1.2, 1.2, 2.0])
        with f1:
            type_filter = st.multiselect("类型筛选", ["收入", "支出"], default=["收入", "支出"])
        with f2:
            book_filter = st.multiselect("账本筛选", sorted(base["账本"].unique().tolist()))
        with f3:
            cat_filter = st.multiselect("类别筛选", sorted(base["类别"].unique().tolist()))
        with f4:
            keyword = st.text_input("关键词（匹配项目/类别/账本）", placeholder="例如：Rent / Petrol / 工资")

        view = base.copy()
        view = view[view["类型"].isin(type_filter)]
        if book_filter:
            view = view[view["账本"].isin(book_filter)]
        if cat_filter:
            view = view[view["类别"].isin(cat_filter)]
        if keyword.strip():
            kw = keyword.strip()
            mask = (
                view["项目"].astype(str).str.contains(kw, na=False) |
                view["类别"].astype(str).str.contains(kw, na=False) |
                view["账本"].astype(str).str.contains(kw, na=False)
            )
            view = view[mask]

        st.caption(f"当前显示：{len(view)} 条")

        if view.empty:
            st.info("当前筛选条件下没有记录。")
        else:
            edited = st.data_editor(
                view,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                column_config={
                    "🗑 删除": st.column_config.CheckboxColumn("🗑 删除"),
                    "ID": st.column_config.NumberColumn("ID", disabled=True),
                    "日期": st.column_config.DateColumn("日期"),
                    "金额": st.column_config.NumberColumn("金额", format="%.2f"),
                    "类型": st.column_config.SelectboxColumn("类型", options=["收入", "支出"]),
                    "账本": st.column_config.TextColumn("账本"),
                    "类别": st.column_config.TextColumn("类别"),
                    "项目": st.column_config.TextColumn("项目"),
                },
                key="editor_records",
            )

            colA, colB, colC = st.columns([1.3, 1.3, 2.4])
            with colA:
                if st.button("💾 保存修改", type="primary"):
                    edited2 = prepare_for_editor(edited.drop(columns=["🗑 删除"], errors="ignore"))
                    full2 = prepare_for_editor(st.session_state.records)

                    upd_cols = ["日期", "账本", "类别", "项目", "金额", "类型"]
                    for _, row in edited2.iterrows():
                        rid = int(row["ID"])
                        for col in upd_cols:
                            full2.loc[full2["ID"] == rid, col] = row[col]

                    st.session_state.records = prepare_for_editor(full2)
                    persist_user_state(USERNAME)
                    st.success("✅ 已保存")
                    st.rerun()

            with colB:
                if st.button("🗑 执行删除（删勾选行）"):
                    del_ids = edited.loc[edited["🗑 删除"] == True, "ID"].tolist()
                    del_ids = [int(x) for x in del_ids]
                    if not del_ids:
                        st.info("未勾选要删除的记录。")
                    else:
                        full2 = prepare_for_editor(st.session_state.records)
                        full2 = full2[~full2["ID"].isin(del_ids)].copy()
                        st.session_state.records = prepare_for_editor(full2)
                        persist_user_state(USERNAME)
                        st.success(f"✅ 已删除 {len(del_ids)} 条")
                        st.rerun()

            with colC:
                st.download_button(
                    "⬇️ 下载当前备份（records_backup.csv）",
                    data=prepare_for_editor(st.session_state.records).to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"{USERNAME}_records_backup.csv",
                    mime="text/csv"
                )

# -------- Tab2: stats + import + profile + remember-me control
with tab2:
    st.subheader("📊 统计中心（按年/月/区间）")
    df = enrich_records(prepare_for_editor(st.session_state.records))
    if df.empty:
        st.info("暂无数据可统计。")
    else:
        colA, colB, colC = st.columns([1.2, 1.2, 2.0])
        with colA:
            mode = st.radio("统计口径", ["年份", "月份", "自定义区间"], horizontal=True)
        with colB:
            type_filter = st.multiselect("收支类型筛选", ["收入", "支出"], default=["收入", "支出"])

        if mode == "年份":
            with colC:
                years = sorted(df["年份"].unique().tolist())
                sel_years = st.multiselect("选择年份", years, default=[max(years)])
            fdf = df[df["年份"].isin(sel_years)]
        elif mode == "月份":
            with colC:
                ym_list = sorted(df["年月"].unique().tolist())
                sel_ym = st.multiselect("选择年月（YYYY-MM）", ym_list, default=[ym_list[-1]])
            fdf = df[df["年月"].isin(sel_ym)]
        else:
            with colC:
                min_d = df["日期"].min().date()
                max_d = df["日期"].max().date()
                dr = st.date_input("选择日期区间", value=(min_d, max_d))
            start_d, end_d = dr if isinstance(dr, tuple) else (dr, dr)
            fdf = df[(df["日期"].dt.date >= start_d) & (df["日期"].dt.date <= end_d)]

        fdf = fdf[fdf["类型"].isin(type_filter)]
        if fdf.empty:
            st.warning("筛选后没有记录。")
        else:
            income_sum = fdf[fdf["类型"] == "收入"]["金额"].sum()
            expense_sum = fdf[fdf["类型"] == "支出"]["金额"].sum()
            net_sum = income_sum - expense_sum

            s1, s2, s3 = st.columns(3)
            s1.metric("收入合计", f"¥ {income_sum:,.2f}")
            s2.metric("支出合计", f"¥ {expense_sum:,.2f}")
            s3.metric("净额(收入-支出)", f"¥ {net_sum:,.2f}")

            st.write("### 📈 趋势（按月汇总）")
            mdf = fdf.groupby(["年月", "类型"], as_index=False)["金额"].sum().sort_values("年月")
            mwide = mdf.pivot_table(index="年月", columns="类型", values="金额", aggfunc="sum", fill_value=0)
            st.line_chart(mwide)

    st.divider()
    st.subheader("📥 导入（只导入到当前登录用户）")
    up = st.file_uploader("上传 CSV（列：日期/账本/类别/项目/金额/类型）", type=["csv"])
    if up is not None:
        try:
            df_in = pd.read_csv(up)
            st.dataframe(df_in.head(20), use_container_width=True)
            if st.button("✅ 导入到我的账本"):
                tmp = pd.DataFrame()
                tmp["日期"] = pd.to_datetime(df_in.get("日期"), errors="coerce")
                tmp = tmp.dropna(subset=["日期"])
                tmp["日期"] = tmp["日期"].dt.date

                tmp["账本"] = df_in.get("账本", "生活主账")
                tmp["类别"] = df_in.get("类别", "其他")
                tmp["项目"] = df_in.get("项目", "")
                tmp["金额"] = df_in.get("金额", 0).astype(str).apply(parse_amount).abs()
                tmp["类型"] = df_in.get("类型", "").astype(str).apply(normalize_type)
                tmp.loc[~tmp["类型"].isin(["收入", "支出"]), "类型"] = "支出"

                start = next_id()
                tmp.insert(0, "ID", range(start, start + len(tmp)))
                tmp = prepare_for_editor(tmp[RECORD_COLS])

                st.session_state.records = prepare_for_editor(pd.concat([prepare_for_editor(st.session_state.records), tmp], ignore_index=True))
                persist_user_state(USERNAME)
                st.success(f"✅ 已导入 {len(tmp)} 条")
                st.rerun()
        except Exception as e:
            st.error(f"导入失败：{e}")

    st.divider()
    st.subheader("👤 个人设置（头像 / 昵称 / 起始资金 / 登录持久化）")

    cfg = load_user_config(USERNAME)

    # avatar & nickname
    new_avatar = st.text_input("头像（建议输入一个 emoji）", value=st.session_state.get("avatar", cfg.get("avatar", "🙂")))
    new_nickname = st.text_input("昵称（显示在右上角）", value=st.session_state.get("nickname", cfg.get("nickname", USERNAME)))

    new_init = st.number_input("起始资金", value=float(st.session_state.init_balance))

    colx, coly = st.columns([1.3, 1.7])
    with colx:
        if st.button("💾 保存个人设置", type="primary"):
            # update session
            st.session_state.avatar = new_avatar.strip() if new_avatar.strip() else "🙂"
            st.session_state.nickname = new_nickname.strip() if new_nickname.strip() else USERNAME
            st.session_state.init_balance = float(new_init)

            # save to user config
            cfg["avatar"] = st.session_state.avatar
            cfg["nickname"] = st.session_state.nickname
            cfg["init_balance"] = float(st.session_state.init_balance)
            save_user_config(USERNAME, cfg)

            # also persist files
            persist_user_state(USERNAME)
            st.success("✅ 已保存（右上角会更新）")
            st.rerun()

    with coly:
        st.caption("登录持久化：如果你不想自动登录，可以清除“保持登录”状态。")
        if st.button("🧹 清除保持登录（本机不再自动登录）"):
            # rotate server token to invalidate cookie
            db = load_auth_db()
            if USERNAME in db:
                db[USERNAME]["session_token_hash"] = ""
                save_auth_db(db)
            cookie_delete(COOKIE_NAME)
            st.success("✅ 已清除保持登录（下次需要重新登录）")
