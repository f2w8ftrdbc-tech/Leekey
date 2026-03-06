import streamlit as st
import pandas as pd
from datetime import date
import re, secrets, hashlib, hmac
import psycopg2
import psycopg2.extras
import streamlit.components.v1 as components

# --- 1. 页面配置 ---
st.set_page_config(page_title="私人理财中心 Pro", layout="wide", initial_sidebar_state="expanded")

# --- 2. 秘密变量读取 ---
if "DATABASE_URL" not in st.secrets:
    st.error("❌ 未在 Secrets 中找到 DATABASE_URL，请在 Streamlit 后台配置。")
    st.stop()

DATABASE_URL = st.secrets["DATABASE_URL"]
APP_SECRET = st.secrets.get("APP_SECRET", "default_secret_key_12345")
COOKIE_NAME = "pf_auth_v2"
COOKIE_DAYS = 30

BOOK_OPTIONS = ["生活主账", "车子专项", "学费/购汇", "理财账本"]
EXP_CATS = ["Eat outside", "Shopping", "Bill", "Petrol", "Insurance", "Rent", "其他"]
INC_CATS = ["工资", "业余项目", "亲情赠与", "理财收益", "其他"]

# ---------------- 3. 数据库初始化 ----------------
@st.cache_resource
def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

def init_db():
    """初始化数据库表结构"""
    conn = get_conn()
    with conn.cursor() as cur:
        # 用户表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username VARCHAR(50) PRIMARY KEY,
                pass_salt VARCHAR(64) NOT NULL,
                pass_hash VARCHAR(128) NOT NULL,
                nickname VARCHAR(50),
                avatar VARCHAR(10),
                session_token_hash VARCHAR(128)
            );
        """)
        # 账目表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS records (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) REFERENCES users(username),
                record_date DATE NOT NULL,
                book VARCHAR(50) NOT NULL,
                category VARCHAR(50) NOT NULL,
                item VARCHAR(200),
                amount NUMERIC(12, 2) NOT NULL,
                rtype VARCHAR(10) NOT NULL
            );
        """)
    conn.commit()

def db_execute(sql, params=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or [])
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e

def db_fetchall(sql, params=None):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params or [])
        return cur.fetchall()

# 启动时执行初始化
try:
    init_db()
except Exception as e:
    st.error(f"❌ 数据库初始化失败，请检查配置或权限: {e}")
    st.stop()

# ---------------- 4. 安全与权限 ----------------
def normalize_username(u: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "", (u or "").strip()).lower()

def pbkdf2_hash_password(password: str, salt_hex: str | None = None) -> dict:
    salt = secrets.token_bytes(16) if salt_hex is None else bytes.fromhex(salt_hex)
    dk = hashlib.pbkdf2_hmac("sha256", (password + APP_SECRET).encode(), salt, 200_000)
    return {"salt": salt.hex(), "hash": dk.hex()}

def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    return hmac.compare_digest(pbkdf2_hash_password(password, salt_hex)["hash"], hash_hex)

def sha256_token(raw: str) -> str:
    return hashlib.sha256((raw + APP_SECRET).encode()).hexdigest()

def cookie_get(name: str):
    # 此处脚本优化以适应 Streamlit Cloud 环境
    html = f"""<script>
    const name = "{name}=";
    const decodedCookie = decodeURIComponent(window.parent.document.cookie);
    const ca = decodedCookie.split(';');
    let res = "";
    for(let i = 0; i <ca.length; i++) {{
        let c = ca[i].trim();
        if (c.indexOf(name) == 0) res = c.substring(name.length, c.length);
    }}
    window.parent.postMessage({{type: 'streamlit:setComponentValue', value: res}}, '*');
    </script>"""
    return components.html(html, height=0)

def cookie_set(name: str, value: str, days: int):
    html = f"<script>const d = new Date(); d.setTime(d.getTime()+({days}*24*60*60*1000)); window.parent.document.cookie = '{name}={value};expires='+d.toUTCString()+';path=/;SameSite=Lax';</script>"
    components.html(html, height=0)

def cookie_delete(name: str):
    html = f"<script>window.parent.document.cookie = '{name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';</script>"
    components.html(html, height=0)

# ---------------- 5. 核心逻辑 ----------------
def parse_amount(x) -> float:
    # 允许公式计算
    s = re.sub(r"[^0-9\+\-\*\/\.]", "", str(x).strip())
    try: return float(eval(s)) if s else 0.0
    except: return 0.0

def load_records(username: str) -> pd.DataFrame:
    rows = db_fetchall("SELECT * FROM records WHERE username=%s ORDER BY record_date DESC, id DESC", [username])
    if not rows: return pd.DataFrame(columns=["id","record_date","book","category","item","amount","rtype"])
    df = pd.DataFrame(rows)
    df["record_date"] = pd.to_datetime(df["record_date"])
    df["amount"] = pd.to_numeric(df["amount"])
    return df

# ---------------- 6. UI 逻辑 ----------------
if "authed_user" not in st.session_state: st.session_state.authed_user = None

def top_bar():
    l, r = st.columns([6, 2])
    with l: st.markdown("## 💰 私人理财中心")
    with r:
        if st.session_state.authed_user:
            user = st.session_state.authed_user
            st.write(f"欢迎, **{user}**")
            if st.button("退出登录"):
                cookie_delete(COOKIE_NAME)
                st.session_state.authed_user = None
                st.rerun()

def auth_panel():
    st.subheader("🔐 请登录使用")
    t1, t2 = st.tabs(["登录", "注册"])
    with t1:
        u = st.text_input("用户名", key="l_u")
        p = st.text_input("密码", type="password", key="l_p")
        if st.button("立即进入", key="btn_login"):
            uu = normalize_username(u)
            rows = db_fetchall("SELECT pass_salt, pass_hash FROM users WHERE username=%s", [uu])
            if rows and verify_password(p, rows[0]["pass_salt"], rows[0]["pass_hash"]):
                st.session_state.authed_user = uu
                # 生成会话并存Cookie (简化版)
                cookie_set(COOKIE_NAME, f"{uu}|token", COOKIE_DAYS)
                st.rerun()
            else: st.error("登录失败：用户名或密码错误")
    with t2:
        ru = st.text_input("设置用户名", key="r_u")
        rp = st.text_input("设置密码", type="password", key="r_p")
        if st.button("提交注册", key="btn_reg"):
            uu = normalize_username(ru)
            hp = pbkdf2_hash_password(rp)
            try:
                db_execute("INSERT INTO users(username, pass_salt, pass_hash, nickname, avatar) VALUES(%s,%s,%s,%s,%s)", [uu, hp["salt"], hp["hash"], uu, "🙂"])
                st.success("注册成功！请切换到登录页。")
            except Exception as e:
                if "already exists" in str(e).lower(): st.error("⚠️ 用户名已存在，请换一个。")
                else: st.error(f"注册失败: {e}")

# --- 主运行流程 ---
top_bar()

if not st.session_state.authed_user:
    auth_panel()
    st.stop()

# --- 已登录主界面 ---
USERNAME = st.session_state.authed_user

# 侧边栏：录入（核心优化：联动逻辑）
st.sidebar.header("📝 记账录入")
# 1. 移出 form 外以实现即时联动
rtype = st.sidebar.selectbox("1. 收支类型", ["支出", "收入"], key="sidebar_rtype")
cat_opts = EXP_CATS if rtype == "支出" else INC_CATS

with st.sidebar.form("add_form", clear_on_submit=True):
    d = st.date_input("2. 日期", date.today())
    book = st.selectbox("3. 账本", BOOK_OPTIONS)
    cat_base = st.selectbox("4. 类别", cat_opts)
    cat_custom = st.text_input("自定义类别 (选'其他'时填写)")
    item = st.text_input("5. 项目/备注")
    amt_str = st.text_input("6. 金额", placeholder="输入数字或公式(如 50+20)")
    
    if st.form_submit_button("确认保存", use_container_width=True):
        final_amt = abs(parse_amount(amt_str))
        final_cat = cat_custom.strip() if (cat_base == "Other" or cat_base == "其他") and cat_custom.strip() else cat_base
        if final_amt == 0:
            st.sidebar.error("请输入有效金额")
        else:
            db_execute("INSERT INTO records(username, record_date, book, category, item, amount, rtype) VALUES(%s,%s,%s,%s,%s,%s,%s)", 
                       [USERNAME, d, book, final_cat, item, final_amt, rtype])
            st.sidebar.success("✅ 已记录")
            st.rerun()

# 资产数据看板
df = load_records(USERNAME)
inc = df[df["rtype"] == "收入"]["amount"].sum()
exp = df[df["rtype"] == "支出"]["amount"].sum()

m1, m2, m3 = st.columns(3)
m1.metric("累计收入", f"¥ {inc:,.2f}")
m2.metric("累计支出", f"¥ {exp:,.2f}")
m3.metric("净结余", f"¥ {(inc-exp):,.2f}")

tab1, tab2, tab3 = st.tabs(["📋 明细管理", "📊 统计导出", "👤 个人中心"])

with tab1:
    st.subheader("明细记录")
    if df.empty:
        st.info("暂无数据，在侧边栏开始记账吧。")
    else:
        # 支持编辑和勾选删除
        view = df.copy()
        view.insert(0, "🗑️", False)
        edited = st.data_editor(
            view,
            use_container_width=True,
            hide_index=True,
            column_config={
                "🗑️": st.column_config.CheckboxColumn("删除"),
                "id": None, "username": None,
                "amount": st.column_config.NumberColumn("金额", format="%.2f"),
                "record_date": st.column_config.DateColumn("日期")
            }
        )
        
        c_save, c_del = st.columns([1, 5])
        if c_save.button("💾 保存修改", type="primary"):
            # 此处演示批量保存逻辑
            for index, row in edited.iterrows():
                if not row["🗑️"]:
                    db_execute("UPDATE records SET record_date=%s, book=%s, category=%s, item=%s, amount=%s WHERE id=%s",
                               [row['record_date'], row['book'], row['category'], row['item'], row['amount'], row['id']])
            st.success("已更新数据库")
            st.rerun()
        
        if c_save.button("🔥 删除勾选行"):
            del_ids = edited.loc[edited["🗑️"] == True, "id"].tolist()
            if del_ids:
                db_execute("DELETE FROM records WHERE id = ANY(%s)", [[int(i) for i in del_ids]])
                st.success(f"已删除 {len(del_ids)} 条记录")
                st.rerun()

with tab2:
    if not df.empty:
        st.subheader("支出类别构成")
        exp_df = df[df["rtype"]=="支出"]
        if not exp_df.empty:
            st.bar_chart(exp_df.groupby("category")["amount"].sum())
        
        st.divider()
        csv = df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 导出全量 CSV", csv, f"records_{USERNAME}.csv", "text/csv")

with tab3:
    st.subheader("偏好设置")
    st.info(f"当前用户：{USERNAME}")
    if st.button("🧹 强制清除 Cookie 并退出"):
        cookie_delete(COOKIE_NAME)
        st.session_state.authed_user = None
        st.rerun()
