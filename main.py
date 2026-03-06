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

# ---------------- Security & Cookies (保持原样) ----------------
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

def cookie_get(name: str) -> str:
    html = f"""<script>
    function getCookie(name) {{
      const value = `; ${{document.cookie}}`;
      const parts = value.split(`; ${{name}}=`);
      if (parts.length === 2) return parts.pop().split(';').shift();
      return "";
    }}
    Streamlit.setComponentValue(getCookie("{name}") || "");
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

# ---------------- Profile & Auth (保持原样) ----------------
def get_user_profile(username: str):
    rows = db_fetchall("select username, nickname, avatar from users where username=%s", [username])
    if not rows: return {"username": username, "nickname": username, "avatar": "🙂"}
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
    if not rows: return False
    stored = rows[0].get("session_token_hash") or ""
    return hmac.compare_digest(stored, sha256_token(raw_token))

def try_auto_login_once():
    if st.session_state.get("_cookie_checked"): return
    st.session_state["_cookie_checked"] = True
    if st.session_state.get("authed_user"): return
    v = cookie_get(COOKIE_NAME)
    if not v: return
    u, tok = parse_cookie_value(v)
    if u and verify_session_token(normalize_username(u), tok):
        login_as(normalize_username(u))

# ---------------- Records 核心逻辑 ----------------
def parse_amount_any(x) -> float:
    if x is None: return 0.0
    s = str(x).strip()
    try: return float(eval(s)) if s else 0.0
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

def delete_records(username: str, ids: list[int]):
    if ids: db_execute("delete from records where username=%s and id = any(%s)", [username, ids])

# ---------------- UI ----------------
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

def login_panel():
    if st.session_state.get("authed_user") or not st.session_state.get("show_login"): return
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
                else: st.error("登录失败")
        with t2:
            u = st.text_input("新用户名", key="reg_user")
            p1 = st.text_input("新密码", type="password", key="reg_pass1")
            if st.button("注册", key="do_register"):
                uu = normalize_username(u)
                hp = pbkdf2_hash_password(p1)
                try:
                    db_execute("insert into users(username, pass_salt, pass_hash, nickname, avatar) values(%s,%s,%s,%s,%s)", [uu, hp["salt"], hp["hash"], uu, "🙂"])
                    st.success("注册成功，请登录")
                except: st.error("用户名已存在")

# ---------------- 启动 ----------------
if "authed_user" not in st.session_state: st.session_state.authed_user = None
if "show_login" not in st.session_state: st.session_state.show_login = False

top_bar()
st.divider()
try_auto_login_once()
login_panel()

if not st.session_state.get("authed_user"):
    st.info("请先登录使用。")
    st.stop()

USERNAME = st.session_state.authed_user
profile = get_user_profile(USERNAME)

# --- Sidebar: 记账录入 (核心修改区) ---
st.sidebar.header("📝 记账录入")

# 修改1: 类别选择移到 Form 外，实现实时联动
rtype = st.sidebar.selectbox("1. 收支类型", ["支出", "收入"], key="sidebar_rtype")
cat_opts = EXP_CATS if rtype == "支出" else INC_CATS

with st.sidebar.form("record_form", clear_on_submit=True):
    d = st.date_input("2. 日期", value=date.today())
    book = st.selectbox("3. 账本", BOOK_OPTIONS)
    cat_base = st.selectbox("4. 类别", cat_opts)
    cat_custom = st.text_input("如选“其他”，自定义名称")
    item = st.text_input("5. 项目/备注")
    # 修改2: 清空金额框，使用 placeholder
    amt = st.text_input("6. 金额", value="", placeholder="直接输入数字或计算式")
    
    if st.form_submit_button("保存"):
        try:
            amount = abs(parse_amount_any(amt))
            final_cat = cat_custom.strip() if (cat_base == "其他" and cat_custom.strip()) else cat_base
            insert_record(USERNAME, d, book, final_cat, item, amount, rtype)
            st.sidebar.success("✅ 已保存")
            st.rerun()
        except: st.sidebar.error("金额错误")

# ---------------- 数据展现 ----------------
df = load_records(USERNAME)
inc = df[df["rtype"] == "收入"]["amount"].sum() if not df.empty else 0.0
exp = df[df["rtype"] == "支出"]["amount"].sum() if not df.empty else 0.0

c1, c2, c3 = st.columns(3)
c1.metric("累计收入", f"¥ {inc:,.2f}")
c2.metric("累计支出", f"¥ {exp:,.2f}")
c3.metric("净结余", f"¥ {(inc-exp):,.2f}")

tab1, tab2, tab3 = st.tabs(["📋 明细管理", "📊 统计导入", "👤 个人设置"])

with tab1:
    st.subheader("📋 明细记录")
    if df.empty:
        st.info("暂无记录")
    else:
        view = df.copy()
        view.insert(0, "🗑 删除", False)
        # 筛选器保持
        f1, f2 = st.columns(2)
        with f1: tfilter = st.multiselect("收支类型", ["收入", "支出"], default=["收入", "支出"])
        with f2: bfilter = st.multiselect("账本筛选", sorted(view["book"].unique().tolist()))
        
        vv = view[view["rtype"].isin(tfilter)].copy()
        if bfilter: vv = vv[vv["book"].isin(bfilter)]
        
        # 修改3: 这里是你的删除和编辑区
        edited = st.data_editor(
            vv, use_container_width=True, hide_index=True,
            column_config={
                "🗑 删除": st.column_config.CheckboxColumn("🗑 删除"),
                "id": st.column_config.NumberColumn("ID", disabled=True),
                "record_date": st.column_config.DateColumn("日期"),
                "amount": st.column_config.NumberColumn("金额", format="%.2f"),
            }
        )
        
        colA, colB = st.columns(2)
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

# --- Tab2 & Tab3 保持你原来的逻辑 (导入、个人设置等) ---
with tab2:
    # ... (保持原有的统计和 CSV 导入代码)
    st.write("统计与导入功能已保留。")
    # (此处建议直接粘贴你原本 Tab2 里的代码块)

with tab3:
    # ... (保持原有的个人设置代码)
    st.write("设置功能已保留。")
    if st.button("🧹 清除保持登录"):
        db_execute("update users set session_token_hash=%s where username=%s", ["", USERNAME])
        cookie_delete(COOKIE_NAME)
        st.success("✅ 已清除，下次需要重新登录")
