import streamlit as st
import pandas as pd
from datetime import date
import re, secrets, hashlib, hmac
import psycopg2
import psycopg2.extras
import streamlit.components.v1 as components

# --- 页面配置 ---
st.set_page_config(page_title="私人理财中心（多用户优化版）", layout="wide")

# --- 从 Secrets 读取配置 ---
# 提醒：请确保在 Streamlit Cloud 的 Secrets 中配置了 DATABASE_URL
APP_SECRET = st.secrets.get("APP_SECRET", "default_secret_key_please_change_it")
if "DATABASE_URL" not in st.secrets:
    st.error("❌ 未找到 DATABASE_URL，请在 Streamlit Secrets 中配置。")
    st.stop()
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
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or [])
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e

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

def cookie_get(name: str):
    html = f"""<script>
    function getCookie(name) {{
      const value = `; ${{document.cookie}}`;
      const parts = value.split(`; ${{name}}=`);
      if (parts.length === 2) return parts.pop().split(';').shift();
      return "";
    }}
    window.parent.postMessage({{type: 'streamlit:setComponentValue', value: getCookie("{name}") || ""}}, '*');
    </script>"""
    return components.html(html, height=0, width=0)

def cookie_set(name: str, value: str, days: int):
    html = f"""<script>
    const d = new Date(); d.setTime(d.getTime() + ({days}*24*60*60*1000));
    document.cookie = "{name}={value};expires=" + d.toUTCString() + ";path=/;SameSite=Lax";
    </script>"""
    components.html(html, height=0, width=0)

def cookie_delete(name: str):
    html = f"""<script>document.cookie = "{name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";</script>"""
    components.html(html, height=0, width=0)

def make_cookie_value(username: str, raw_token: str) -> str:
    sig = hmac_sign(f"{username}|{raw_token}")
    return f"{username}|{raw_token}|{sig}"

def parse_cookie_value(v: str):
    try:
        u, t, sig = (v or "").split("|")
        exp = hmac_sign(f"{u}|{t}")
        if not hmac.compare_digest(exp, sig): return None, None
        return u, t
    except: return None, None

# ---------------- Profile & Auth ----------------
def get_user_profile(username: str):
    rows = db_fetchall("select username, nickname, avatar from users where username=%s", [username])
    if not rows: return {"username": username, "nickname": username, "avatar": "🙂"}
    r = rows[0]
    return {"username": r["username"], "nickname": r["nickname"] or r["username"], "avatar": r["avatar"] or "🙂"}

def set_user_profile(username: str, nickname: str, avatar: str):
    db_execute("update users set nickname=%s, avatar=%s where username=%s", [nickname, avatar, username])

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
    if not rows: return False
    stored = rows[0].get("session_token_hash") or ""
    return hmac.compare_digest(stored, sha256_token(raw_token))

# ---------------- Records 核心逻辑 ----------------
def parse_amount_any(x) -> float:
    if x is None: return 0.0
    s = str(x).strip()
    if not s: return 0.0
    # 安全地过滤掉非数学字符
    s = re.sub(r"[^0-9\+\-\*\/\.\(\)]", "", s)
    try: return float(eval(s))
    except: return 0.0

def load_records(username: str) -> pd.DataFrame:
    rows = db_fetchall("select id, record_date, book, category, item, amount, rtype from records where username=%s order by record_date desc, id desc", [username])
    if not rows: return pd.DataFrame(columns=["id","record_date","book","category","item","amount","rtype"])
    df = pd.DataFrame(rows)
    df["record_date"] = pd.to_datetime(df["record_date"])
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    return df

def insert_record(username: str, d: date, book: str, cat: str, item: str, amt: float, rtype: str):
    db_execute("insert into records(username, record_date, book, category, item, amount, rtype) values(%s,%s,%s,%s,%s,%s,%s)",
               [username, d, book, cat, item or "", float(amt), rtype])

def update_records_bulk(username: str, df: pd.DataFrame):
    conn = get_conn()
    with conn.cursor() as cur:
        for _, r in df.iterrows():
            cur.execute("update records set record_date=%s, book=%s, category=%s, item=%s, amount=%s, rtype=%s where id=%s and username=%s",
                        [pd.to_datetime(r["record_date"]).date(), str(r["book"]), str(r["category"]), str(r.get("item","") or ""), float(r["amount"]), str(r["rtype"]), int(r["id"]), username])
    conn.commit()

def delete_records(username: str, ids: list):
    if ids: db_execute("delete from records where username=%s and id = any(%s)", [username, ids])

# ---------------- UI 组件 ----------------
def top_bar():
    l, r = st.columns([6, 2])
    with l: st.markdown("## 💰 私人理财中心")
    with r:
        if st.session_state.get("authed_user"):
            p = get_user_profile(st.session_state.authed_user)
            st.markdown(f"<div style='text-align:right;font-size:14px'>{p['avatar']} <b>{p['nickname']}</b></div>", unsafe_allow_html=True)
            if st.button("退出", key="logout_top"): logout()
        else:
            if st.button("登录 / 注册", key="login_top"): st.session_state.show_login = True

# ---------------- 身份检查逻辑 ----------------
if "authed_user" not in st.session_state: st.session_state.authed_user = None
if "show_login" not in st.session_state: st.session_state.show_login = False

top_bar()
st.divider()

# Cookie 自动登录处理
if not st.session_state.authed_user:
    cookie_val = cookie_get(COOKIE_NAME)
    # 此处利用 Streamlit 组件异步特性，如果是空可能还没加载完
    if cookie_val:
        u, tok = parse_cookie_value(cookie_val)
        if u and verify_session_token(normalize_username(u), tok):
            st.session_state.authed_user = normalize_username(u)
            st.rerun()

# 登录面板
if not st.session_state.authed_user and st.session_state.show_login:
    with st.expander("🔐 用户登录 / 注册", expanded=True):
        t1, t2 = st.tabs(["登录", "注册"])
        with t1:
            u_in = st.text_input("用户名", key="login_user")
            p_in = st.text_input("密码", type="password", key="login_pass")
            if st.button("立即进入", key="do_login"):
                uu = normalize_username(u_in)
                rows = db_fetchall("select pass_salt, pass_hash from users where username=%s", [uu])
                if rows and verify_password(p_in, rows[0]["pass_salt"], rows[0]["pass_hash"]):
                    st.session_state.authed_user = uu
                    raw = rotate_session_token(uu)
                    cookie_set(COOKIE_NAME, make_cookie_value(uu, raw), days=COOKIE_DAYS)
                    st.session_state.show_login = False
                    st.rerun()
                else: st.error("用户名或密码错误")
        with t2:
            ru = st.text_input("新用户名", key="reg_user")
            rp1 = st.text_input("新密码", type="password", key="reg_pass1")
            if st.button("注册账号", key="do_register"):
                uu = normalize_username(ru)
                if len(rp1) < 6: st.error("密码至少6位")
                else:
                    hp = pbkdf2_hash_password(rp1)
                    try:
                        db_execute("insert into users(username, pass_salt, pass_hash, nickname, avatar) values(%s,%s,%s,%s,%s)", [uu, hp["salt"], hp["hash"], uu, "🙂"])
                        st.success("注册成功！请切换到登录标签页。")
                    except: st.error("该用户名已被占用")

if not st.session_state.authed_user:
    st.info("👋 欢迎！请登录以开始管理您的私人账本。")
    st.stop()

# ---------------- 主程序运行区 ----------------
USERNAME = st.session_state.authed_user
profile = get_user_profile(USERNAME)

# --- Sidebar: 记账录入 ---
st.sidebar.header("📝 记账录入")

# 联动优化：收支类型放在 Form 外，保证实时刷新类别下拉框
rtype = st.sidebar.selectbox("收支类型", ["支出", "收入"], key="main_rtype")
cat_opts = EXP_CATS if rtype == "支出" else INC_CATS

with st.sidebar.form("add_record_form", clear_on_submit=True):
    d = st.date_input("日期", value=date.today())
    book = st.selectbox("账本", BOOK_OPTIONS)
    cat_base = st.selectbox("类别", cat_opts)
    cat_custom = st.text_input("自定义类别 (选“其他”时填)")
    item = st.text_input("备注/具体项目")
    # 金额优化：空值+placeholder
    amt_str = st.text_input("金额", placeholder="支持计算式如 50+10")
    
    if st.form_submit_button("确认存入", use_container_width=True):
        if not amt_str:
            st.sidebar.warning("请输入金额")
        else:
            amount = abs(parse_amount_any(amt_str))
            final_cat = cat_custom.strip() if (cat_base == "其他" and cat_custom.strip()) else cat_base
            insert_record(USERNAME, d, book, final_cat, item, amount, rtype)
            st.rerun()

# --- 主界面：资产看板 ---
df = load_records(USERNAME)
inc_total = df[df["rtype"] == "收入"]["amount"].sum() if not df.empty else 0.0
exp_total = df[df["rtype"] == "支出"]["amount"].sum() if not df.empty else 0.0

m1, m2, m3 = st.columns(3)
m1.metric("累计收入", f"¥ {inc_total:,.2f}")
m2.metric("累计支出", f"¥ {exp_total:,.2f}")
m3.metric("结余", f"¥ {(inc_total - exp_total):,.2f}")

tab1, tab2, tab3 = st.tabs(["📋 明细管理", "📊 统计中心", "👤 个人设置"])

with tab1:
    st.subheader("历史账单明细")
    if df.empty:
        st.info("目前还没有记录，在左侧录入第一笔吧！")
    else:
        # 交互式编辑与删除区
        view_df = df.copy()
        view_df.insert(0, "选择", False)
        
        # 列表筛选
        c_f1, c_f2 = st.columns(2)
        with c_f1: 
            f_type = st.multiselect("类型", ["支出", "收入"], default=["支出", "收入"])
        with f_f2:
            f_book = st.multiselect("账本", BOOK_OPTIONS)
        
        filtered = view_df[view_df["rtype"].isin(f_type)]
        if f_book: filtered = filtered[filtered["book"].isin(f_book)]
        
        edited_df = st.data_editor(
            filtered,
            use_container_width=True,
            hide_index=True,
            column_config={
                "选择": st.column_config.CheckboxColumn("🗑", help="勾选后点击下方删除"),
                "id": st.column_config.NumberColumn("ID", disabled=True),
                "record_date": st.column_config.DateColumn("日期"),
                "amount": st.column_config.NumberColumn("金额", format="%.2f")
            }
        )
        
        btn_l, btn_r = st.columns([1, 4])
        if btn_l.button("💾 保存所有修改", type="primary"):
            update_records_bulk(USERNAME, edited_df.drop(columns=["选择"]))
            st.success("修改已同步至数据库")
            st.rerun()
            
        if btn_l.button("🗑 删除选中行"):
            ids_to_del = edited_df.loc[edited_df["选择"] == True, "id"].tolist()
            if ids_to_del:
                delete_records(USERNAME, [int(i) for i in ids_to_del])
                st.success(f"已成功删除 {len(ids_to_del)} 条记录")
                st.rerun()

with tab2:
    st.subheader("数据分析与导出")
    if not df.empty:
        # 简单的收支趋势
        df_trend = df.copy()
        df_trend['month'] = df_trend['record_date'].dt.to_period('M').astype(str)
        trend_chart = df_trend.groupby(['month', 'rtype'])['amount'].sum().unstack(fill_value=0)
        st.line_chart(trend_chart)
        
        st.divider()
        # CSV 导出功能
        csv = df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 导出全量数据为 CSV", data=csv, file_name=f"records_{USERNAME}.csv", mime="text/csv")
    else:
        st.write("暂无统计数据。")

with tab3:
    st.subheader("个人资料与偏好")
    p = get_user_profile(USERNAME)
    new_nick = st.text_input("昵称", value=p["nickname"])
    new_avatar = st.text_input("头像 (Emoji)", value=p["avatar"])
    
    if st.button("更新个人资料"):
        set_user_profile(USERNAME, new_nick, new_avatar)
        st.success("更新成功！")
        st.rerun()
    
    st.divider()
    if st.button("🧹 清除所有登录凭证 (下次需重新登录)"):
        db_execute("update users set session_token_hash=NULL where username=%s", [USERNAME])
        cookie_delete(COOKIE_NAME)
        st.success("已清除。")
        st.rerun()
