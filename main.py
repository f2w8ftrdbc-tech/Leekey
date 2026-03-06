import streamlit as st
import pandas as pd
from datetime import date
import re, secrets, hashlib, hmac
import psycopg2
import psycopg2.extras
import streamlit.components.v1 as components

# --- 1. 页面配置 ---
st.set_page_config(page_title="私人理财中心 Pro", layout="wide", initial_sidebar_state="expanded")

# --- 2. 从 Secrets 读取配置 ---
if "DATABASE_URL" not in st.secrets:
    st.error("❌ 未找到 DATABASE_URL，请在 Streamlit Cloud 的 Settings -> Secrets 中配置。")
    st.stop()

DATABASE_URL = st.secrets["DATABASE_URL"]
APP_SECRET = st.secrets.get("APP_SECRET", "super-secret-key-12345")
COOKIE_DAYS = 30
COOKIE_NAME = "pf_auth_v2"

BOOK_OPTIONS = ["生活主账", "车子专项", "学费/购汇", "理财账本"]
EXP_CATS = ["Eat outside", "Shopping", "Bill", "Petrol", "Insurance", "Rent", "其他"]
INC_CATS = ["工资", "业余项目", "亲情赠与", "理财收益", "其他"]

# --- 3. 数据库核心逻辑 ---
@st.cache_resource
def get_conn():
    """建立数据库连接"""
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

def init_db():
    """初始化数据库：如果表不存在则创建"""
    conn = get_conn()
    with conn.cursor() as cur:
        # 创建用户表
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
        # 创建账单表
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

# 启动时执行建表
try:
    init_db()
except Exception as e:
    st.error(f"数据库初始化失败，请检查连接字符串: {e}")
    st.stop()

# --- 4. 安全与权限 (密码/Cookie) ---
def normalize_username(u: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "", (u or "").strip()).lower()

def hash_password(password: str, salt_hex: str = None):
    salt = secrets.token_bytes(16) if salt_hex is None else bytes.fromhex(salt_hex)
    pwd = (password + APP_SECRET).encode("utf-8")
    dk = hashlib.pbkdf2_hmac("sha256", pwd, salt, 200_000)
    return {"salt": salt.hex(), "hash": dk.hex()}

def verify_password(password: str, salt_hex: str, hash_hex: str):
    return hmac.compare_digest(hash_password(password, salt_hex)["hash"], hash_hex)

def cookie_set(name, value, days):
    html = f"<script>const d = new Date(); d.setTime(d.getTime() + ({days}*24*60*60*1000)); document.cookie = '{name}={value};expires=' + d.toUTCString() + ';path=/;SameSite=Lax';</script>"
    components.html(html, height=0)

def cookie_get(name):
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

# --- 5. 数据处理 ---
def parse_amount(x):
    s = re.sub(r"[^0-9\+\-\*\/\.]", "", str(x))
    try: return float(eval(s)) if s else 0.0
    except: return 0.0

def load_records(username):
    rows = db_fetchall("SELECT * FROM records WHERE username=%s ORDER BY record_date DESC, id DESC", [username])
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["id","record_date","book","category","item","amount","rtype"])

# --- 6. 登录/注册 UI ---
if "authed_user" not in st.session_state: st.session_state.authed_user = None

def login_ui():
    st.title("💰 私人理财中心")
    tab_login, tab_reg = st.tabs(["用户登录", "新用户注册"])
    
    with tab_login:
        u = st.text_input("用户名", key="l_u")
        p = st.text_input("密码", type="password", key="l_p")
        if st.button("登录"):
            uu = normalize_username(u)
            user = db_fetchall("SELECT * FROM users WHERE username=%s", [uu])
            if user and verify_password(p, user[0]['pass_salt'], user[0]['pass_hash']):
                st.session_state.authed_user = uu
                st.success("登录成功！")
                st.rerun()
            else: st.error("用户名或密码错误")

    with tab_reg:
        ru = st.text_input("设置用户名", key="r_u")
        rp = st.text_input("设置密码", type="password", key="r_p")
        if st.button("提交注册"):
            uu = normalize_username(ru)
            if len(rp) < 6: st.error("密码太短")
            else:
                hp = hash_password(rp)
                try:
                    db_execute("INSERT INTO users(username, pass_salt, pass_hash, nickname, avatar) VALUES(%s,%s,%s,%s,%s)", 
                               [uu, hp['salt'], hp['hash'], uu, "🙂"])
                    st.success("注册成功，请切换至登录页")
                except Exception as e:
                    if "already exists" in str(e): st.error("⚠️ 该用户名已被占用，请换一个。")
                    else: st.error(f"注册失败: {e}")

# --- 7. 主程序循环 ---
if not st.session_state.authed_user:
    login_ui()
    st.stop()

# --- 已登录状态 ---
USER = st.session_state.authed_user

# 侧边栏：录入
st.sidebar.markdown(f"### 👤 {USER}")
if st.sidebar.button("退出登录"):
    st.session_state.authed_user = None
    st.rerun()

st.sidebar.divider()
st.sidebar.header("📝 记账录入")

# 实时联动：类型在 Form 外
rtype = st.sidebar.selectbox("收支类型", ["支出", "收入"])
cat_list = EXP_CATS if rtype == "支出" else INC_CATS

with st.sidebar.form("add_form", clear_on_submit=True):
    d = st.date_input("日期", date.today())
    bk = st.selectbox("账本", BOOK_OPTIONS)
    ct = st.selectbox("分类", cat_list)
    custom_ct = st.text_input("自定义分类 (选'其他'时填写)")
    memo = st.text_input("备注")
    amt_raw = st.text_input("金额", placeholder="100 或 50+20")
    
    if st.form_submit_button("确认存入"):
        final_amt = abs(parse_amount(amt_raw))
        final_ct = custom_ct.strip() if ct == "其他" and custom_ct else ct
        if final_amt <= 0:
            st.error("请输入有效金额")
        else:
            db_execute("INSERT INTO records(username, record_date, book, category, item, amount, rtype) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                       [USER, d, bk, final_ct, memo, final_amt, rtype])
            st.success("已记录！")
            st.rerun()

# 主界面看板
df = load_records(USER)
inc = df[df['rtype'] == '收入']['amount'].sum() if not df.empty else 0
exp = df[df['rtype'] == '支出']['amount'].sum() if not df.empty else 0

c1, c2, c3 = st.columns(3)
c1.metric("总收入", f"¥{inc:,.2f}")
c2.metric("总支出", f"¥{exp:,.2f}")
c3.metric("结余", f"¥{inc-exp:,.2f}")

t1, t2 = st.tabs(["📋 账单明细", "📊 统计分析"])

with t1:
    if df.empty:
        st.info("还没有数据，开始记账吧！")
    else:
        # 管理界面：支持编辑和删除
        view_df = df.copy()
        view_df.insert(0, "🗑️", False)
        edited = st.data_editor(
            view_df,
            column_config={
                "🗑️": st.column_config.CheckboxColumn("删除"),
                "id": None, "username": None,
                "amount": st.column_config.NumberColumn("金额", format="%.2f")
            },
            hide_index=True,
            use_container_width=True
        )
        
        col_s, col_d = st.columns([1, 5])
        if col_s.button("💾 保存修改"):
            # 批量更新逻辑
            for _, r in edited.iterrows():
                if not r["🗑️"]:
                    db_execute("UPDATE records SET record_date=%s, book=%s, category=%s, item=%s, amount=%s WHERE id=%s",
                               [r['record_date'], r['book'], r['category'], r['item'], r['amount'], r['id']])
            st.success("修改已保存")
            st.rerun()
            
        if col_s.button("🔥 删除选中"):
            ids_to_del = edited[edited["🗑️"] == True]["id"].tolist()
            if ids_to_del:
                db_execute("DELETE FROM records WHERE id = ANY(%s)", [[int(i) for i in ids_to_del]])
                st.success(f"已删除 {len(ids_to_del)} 条记录")
                st.rerun()

with t2:
    if not df.empty:
        st.subheader("支出构成")
        exp_df = df[df['rtype'] == '支出']
        if not exp_df.empty:
            st.bar_chart(exp_df.groupby('category')['amount'].sum())
        
        st.divider()
        csv = df.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 导出全量数据 (CSV)", csv, f"records_{date.today()}.csv", "text/csv")
        st.rerun()
