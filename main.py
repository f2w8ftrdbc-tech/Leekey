import os
import re
import ast
import hmac
import math
import sqlite3
import secrets
import hashlib
import operator as op
import tempfile
from datetime import date, datetime, timedelta
from typing import Optional, List

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ---------------- 可选依赖：Postgres ----------------
HAS_PSYCOPG2 = True
try:
    import psycopg2
    import psycopg2.extras
except Exception:
    HAS_PSYCOPG2 = False

# ---------------- 页面配置 ----------------
st.set_page_config(page_title="私人理财中心（稳定版）", layout="wide")

# ---------------- Secrets 读取 ----------------
def secret_get(key, default=None):
    try:
        return st.secrets[key]
    except Exception:
        return default

APP_SECRET = str(secret_get("APP_SECRET", "please_change_me"))
RAW_DATABASE_URL = str(secret_get("DATABASE_URL", "") or "").strip().strip('"').strip("'")
COOKIE_DAYS = int(secret_get("COOKIE_DAYS", 30))

COOKIE_NAME = "pf_auth"
LOCAL_SQLITE_PATH = os.path.join(tempfile.gettempdir(), "private_finance_center.db")

BOOK_OPTIONS = ["生活主账", "车子专项", "学费/购汇", "理财账本"]
EXP_CATS = ["Eat outside", "Shopping", "Bill", "Petrol", "Insurance", "Rent", "其他"]
INC_CATS = ["工资", "业余项目", "亲情赠与", "理财收益", "其他"]

# ---------------- DB Backend ----------------
def clean_database_url(url: str) -> str:
    url = (url or "").strip().strip('"').strip("'")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url

@st.cache_resource
def get_db_config():
    url = clean_database_url(RAW_DATABASE_URL)

    if url and HAS_PSYCOPG2:
        try:
            conn = psycopg2.connect(url)
            conn.close()
            return {
                "backend": "postgres",
                "url": url,
                "message": "当前使用 PostgreSQL",
            }
        except Exception as e:
            return {
                "backend": "sqlite",
                "path": LOCAL_SQLITE_PATH,
                "message": f"PostgreSQL 连接失败，已自动切换到 SQLite：{e}",
            }

    return {
        "backend": "sqlite",
        "path": LOCAL_SQLITE_PATH,
        "message": "未检测到可用 PostgreSQL，当前使用 SQLite",
    }

def is_postgres() -> bool:
    return get_db_config()["backend"] == "postgres"

def sqlite_sql(sql: str) -> str:
    return sql.replace("%s", "?")

def db_fetchall(sql, params=None):
    params = params or []
    cfg = get_db_config()

    if cfg["backend"] == "postgres":
        conn = psycopg2.connect(cfg["url"], cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
                return [dict(r) for r in rows]
        finally:
            conn.close()

    conn = sqlite3.connect(cfg["path"])
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute(sqlite_sql(sql), params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def db_execute(sql, params=None):
    params = params or []
    cfg = get_db_config()

    if cfg["backend"] == "postgres":
        conn = psycopg2.connect(cfg["url"])
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return

    conn = sqlite3.connect(cfg["path"])
    try:
        cur = conn.cursor()
        cur.execute(sqlite_sql(sql), params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def db_executemany(sql, rows):
    if not rows:
        return

    cfg = get_db_config()

    if cfg["backend"] == "postgres":
        conn = psycopg2.connect(cfg["url"])
        try:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(cur, sql, rows, page_size=500)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return

    conn = sqlite3.connect(cfg["path"])
    try:
        cur = conn.cursor()
        cur.executemany(sqlite_sql(sql), rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def ensure_tables():
    if is_postgres():
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
    else:
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
            id integer primary key autoincrement,
            username text not null,
            record_date text not null,
            book text not null,
            category text not null,
            item text not null default '',
            amount real not null default 0,
            rtype text not null check (rtype in ('收入','支出')),
            foreign key (username) references users(username) on delete cascade
        );
        """)
        db_execute("create index if not exists idx_records_user_date on records(username, record_date desc, id desc);")

# ---------------- Security & Cookies ----------------
def normalize_username(u: str) -> str:
    u = (u or "").strip()
    u = re.sub(r"[^A-Za-z0-9_]", "", u)
    return u.lower()

def pbkdf2_hash_password(password: str, salt_hex: Optional[str] = None) -> dict:
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
        try:
            return cookie_manager.get(name) or ""
        except Exception:
            return ""
    return ""

def cookie_set(name: str, value: str, days: int):
    if COOKIE_READ_AVAILABLE and cookie_manager is not None:
        try:
            expires_at = datetime.utcnow() + timedelta(days=days)
            cookie_manager.set(name, value, expires_at=expires_at)
            return
        except Exception:
            pass

    html = f"""
    <script>
    const d = new Date();
    d.setTime(d.getTime() + ({days} * 24 * 60 * 60 * 1000));
    document.cookie = "{name}={value};expires=" + d.toUTCString() + ";path=/;SameSite=Lax";
    </script>
    """
    components.html(html, height=0, width=0)

def cookie_delete(name: str):
    if COOKIE_READ_AVAILABLE and cookie_manager is not None:
        try:
            cookie_manager.delete(name)
            return
        except Exception:
            pass

    html = f"""
    <script>
    document.cookie = "{name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/; SameSite=Lax";
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
    except Exception:
        return None, None

# ---------------- Profile & Auth ----------------
def get_user_profile(username: str):
    rows = db_fetchall("select username, nickname, avatar from users where username=%s", [username])
    if not rows:
        return {"username": username, "nickname": username, "avatar": "🙂"}
    r = rows[0]
    return {
        "username": r["username"],
        "nickname": r["nickname"] or r["username"],
        "avatar": r["avatar"] or "🙂",
    }

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

    if st.session_state.get("authed_user"):
        st.session_state["_cookie_checked"] = True
        return

    v = cookie_get(COOKIE_NAME)
    if v:
        u, tok = parse_cookie_value(v)
        if u and tok and verify_session_token(normalize_username(u), tok):
            login_as(normalize_username(u))

    st.session_state["_cookie_checked"] = True

# ---------------- 金额表达式安全解析 ----------------
_ALLOWED_OPS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.USub: op.neg,
    ast.UAdd: op.pos,
}

def _safe_eval_expr(node):
    if isinstance(node, ast.Num):
        return node.n
    if isinstance(node, ast.Constant):
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
    if x is None:
        return 0.0
    s = str(x).strip()
    if not s:
        return 0.0
    if len(s) > 100:
        raise ValueError("金额表达式过长")
    if re.search(r"[^0-9\.\+\-\*\/\(\)\s]", s):
        raise ValueError("金额表达式含非法字符")

    try:
        tree = ast.parse(s, mode="eval")
        val = _safe_eval_expr(tree)
    except ZeroDivisionError:
        raise ValueError("金额表达式不能除以 0")
    except Exception as e:
        raise ValueError(f"金额表达式无效：{e}")

    val = float(val)
    if not math.isfinite(val):
        raise ValueError("金额必须是有限数字")
    return val

# ---------------- Records ----------------
def load_records(username: str) -> pd.DataFrame:
    rows = db_fetchall(
        """
        select id, record_date, book, category, item, amount, rtype
        from records
        where username=%s
        order by record_date desc, id desc
        """,
        [username],
    )
    if not rows:
        return pd.DataFrame(columns=["id", "record_date", "book", "category", "item", "amount", "rtype"])

    df = pd.DataFrame(rows)
    df["record_date"] = pd.to_datetime(df["record_date"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    df["rtype"] = df["rtype"].astype(str)
    df["book"] = df["book"].astype(str)
    df["category"] = df["category"].astype(str)
    df["item"] = df["item"].astype(str)
    return df

def insert_record(username: str, d: date, book: str, cat: str, item: str, amt: float, rtype: str):
    db_execute(
        """
        insert into records(username, record_date, book, category, item, amount, rtype)
        values(%s,%s,%s,%s,%s,%s,%s)
        """,
        [username, str(d), book, cat, item or "", float(amt), rtype],
    )

def insert_records_bulk(username: str, rows: List[tuple]):
    if not rows:
        return

    payload = []
    for r in rows:
        payload.append((username, str(r[0]), r[1], r[2], r[3], float(r[4]), r[5]))

    db_executemany(
        """
        insert into records(username, record_date, book, category, item, amount, rtype)
        values(%s,%s,%s,%s,%s,%s,%s)
        """,
        payload,
    )

def update_records_bulk(username: str, df: pd.DataFrame):
    if df.empty:
        return

    valid_rtypes = {"收入", "支出"}
    rows = []

    for _, r in df.iterrows():
        record_date = pd.to_datetime(r["record_date"], errors="coerce")
        if pd.isna(record_date):
            raise ValueError("存在无效日期")

        book = str(r["book"]).strip()
        category = str(r["category"]).strip()
        item = str(r.get("item", "") or "").strip()
        amount = float(r["amount"])
        rtype = str(r["rtype"]).strip()

        if not book:
            raise ValueError("账本不能为空")
        if not category:
            raise ValueError("类别不能为空")
        if rtype not in valid_rtypes:
            raise ValueError("收支类型只能是“收入”或“支出”")
        if not math.isfinite(amount):
            raise ValueError("金额无效")

        rows.append((
            str(record_date.date()),
            book,
            category,
            item,
            amount,
            rtype,
            int(r["id"]),
            username,
        ))

    db_executemany(
        """
        update records
        set record_date=%s, book=%s, category=%s, item=%s, amount=%s, rtype=%s
        where id=%s and username=%s
        """,
        rows,
    )

def delete_records(username: str, ids: List[int]):
    if not ids:
        return

    cfg = get_db_config()

    if cfg["backend"] == "postgres":
        conn = psycopg2.connect(cfg["url"])
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "delete from records where username=%s and id = any(%s)",
                    [username, ids],
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return

    conn = sqlite3.connect(cfg["path"])
    try:
        cur = conn.cursor()
        placeholders = ",".join(["?"] * len(ids))
        cur.execute(
            f"delete from records where username=? and id in ({placeholders})",
            [username] + [int(x) for x in ids],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

# ---------------- UI ----------------
def top_bar():
    left, right = st.columns([6, 2])

    with left:
        st.markdown("## 💰 私人理财中心")

    with right:
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
                    st.error("用户名不能为空或不合法")
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
                        """
                        insert into users(username, pass_salt, pass_hash, nickname, avatar)
                        values(%s,%s,%s,%s,%s)
                        """,
                        [uu, hp["salt"], hp["hash"], uu, "🙂"],
                    )
                    st.success("注册成功，请回到“登录”标签登录")
                except Exception:
                    st.error("用户名已存在或数据库错误")

def clean_text_series(series: pd.Series, default_value: str = "") -> pd.Series:
    s = series.copy()
    s = s.where(s.notna(), default_value)
    s = s.astype(str)
    s = s.replace({"nan": default_value, "None": default_value, "<NA>": default_value})
    return s

# ---------------- 启动 ----------------
ensure_tables()

if "authed_user" not in st.session_state:
    st.session_state.authed_user = None
if "show_login" not in st.session_state:
    st.session_state.show_login = False
if "_cookie_checked" not in st.session_state:
    st.session_state["_cookie_checked"] = False

try_auto_login_once()
top_bar()
st.divider()

cfg = get_db_config()
if cfg["backend"] == "sqlite":
    st.warning(cfg["message"])

login_panel()

if not st.session_state.get("authed_user"):
    if not COOKIE_READ_AVAILABLE:
        st.info("提示：当前环境未安装 extra_streamlit_components，Cookie 自动登录读取会降级（登录/退出/记账不受影响）。")
    st.info("请先登录使用。")
    st.stop()

USERNAME = st.session_state.authed_user

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
c3.metric("净结余", f"¥ {(inc - exp):,.2f}")

tab1, tab2, tab3 = st.tabs(["📋 明细管理", "📊 统计导入", "👤 个人设置"])

# ---------------- Tab1：明细管理 ----------------
with tab1:
    st.subheader("📋 明细记录")

    if df.empty:
        st.info("暂无记录")
    else:
        view = df.copy()
        view.insert(0, "🗑 删除", False)

        f1, f2, f3, f4 = st.columns([2, 2, 2, 3])
        with f1:
            tfilter = st.multiselect("收支类型", ["收入", "支出"], default=["收入", "支出"])
        with f2:
            bfilter = st.multiselect("账本筛选", sorted(view["book"].dropna().unique().tolist()))
        with f3:
            catfilter = st.multiselect("类别筛选", sorted(view["category"].dropna().unique().tolist()))
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
                "category": st.column_config.TextColumn("类别"),
                "item": st.column_config.TextColumn("项目/备注"),
            },
            key="records_editor",
        )

        colA, colB, colC = st.columns([2, 2, 3])
        with colA:
            if st.button("💾 保存修改", type="primary", key="save_records_btn"):
                try:
                    upd = edited.drop(columns=["🗑 删除"])
                    update_records_bulk(USERNAME, upd)
                    st.success("已更新")
                    st.rerun()
                except Exception as e:
                    st.error(f"保存失败：{e}")

        with colB:
            if st.button("🗑 删除勾选行", key="delete_records_btn"):
                try:
                    del_ids = edited.loc[edited["🗑 删除"] == True, "id"].tolist()
                    delete_records(USERNAME, [int(x) for x in del_ids])
                    st.success(f"已删除 {len(del_ids)} 条")
                    st.rerun()
                except Exception as e:
                    st.error(f"删除失败：{e}")

        with colC:
            st.caption("提示：直接在表格里修改日期/账本/类别/备注/金额/收支类型，然后点“保存修改”。")

# ---------------- Tab2：统计 + 导入导出 ----------------
with tab2:
    st.subheader("📊 统计与导入导出")

    if df.empty:
        st.info("暂无数据可统计或导入比对。")
    else:
        s1, s2, s3, s4 = st.columns([2, 2, 2, 2])
        with s1:
            dmin = st.date_input("起始日期", value=df["record_date"].min().date(), key="stat_dmin")
        with s2:
            dmax = st.date_input("结束日期", value=df["record_date"].max().date(), key="stat_dmax")
        with s3:
            book_sel = st.multiselect("账本", BOOK_OPTIONS, default=BOOK_OPTIONS, key="stat_book_sel")
        with s4:
            type_sel = st.multiselect("收支类型", ["收入", "支出"], default=["收入", "支出"], key="stat_type_sel")

        dff = df.copy()
        dff = dff[(dff["record_date"].dt.date >= dmin) & (dff["record_date"].dt.date <= dmax)]
        if book_sel:
            dff = dff[dff["book"].isin(book_sel)]
        if type_sel:
            dff = dff[dff["rtype"].isin(type_sel)]

        a1, a2, a3 = st.columns(3)
        a1.metric("区间收入", f"¥ {dff[dff['rtype'] == '收入']['amount'].sum():,.2f}")
        a2.metric("区间支出", f"¥ {dff[dff['rtype'] == '支出']['amount'].sum():,.2f}")
        a3.metric(
            "区间净结余",
            f"¥ {(dff[dff['rtype'] == '收入']['amount'].sum() - dff[dff['rtype'] == '支出']['amount'].sum()):,.2f}",
        )

        st.divider()

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
        if monthly.empty:
            st.info("当前筛选条件下暂无月度数据")
        else:
            st.line_chart(monthly, use_container_width=True)

        st.divider()

        st.markdown("#### 🧾 按类别汇总")
        cc1, cc2 = st.columns(2)

        with cc1:
            exp_cat = tmp[tmp["rtype"] == "支出"].groupby("category")["amount"].sum().sort_values(ascending=False)
            st.write("支出类别 Top")
            st.dataframe(exp_cat.reset_index().rename(columns={"amount": "sum"}), use_container_width=True)
            if len(exp_cat) > 0:
                st.bar_chart(exp_cat, use_container_width=True)
            else:
                st.info("暂无支出类别数据")

        with cc2:
            inc_cat = tmp[tmp["rtype"] == "收入"].groupby("category")["amount"].sum().sort_values(ascending=False)
            st.write("收入类别 Top")
            st.dataframe(inc_cat.reset_index().rename(columns={"amount": "sum"}), use_container_width=True)
            if len(inc_cat) > 0:
                st.bar_chart(inc_cat, use_container_width=True)
            else:
                st.info("暂无收入类别数据")

        st.divider()

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

    st.markdown("#### ⬆️ 导入 CSV（追加到数据库）")
    st.caption("支持列名：record_date / book / category / item / amount / rtype（大小写不敏感）；也支持手动映射。")

    up = st.file_uploader("上传 CSV 文件", type=["csv"], key="csv_uploader")

    if up is not None:
        try:
            imp = pd.read_csv(up)
            imp.columns = [str(c).strip() for c in imp.columns]

            st.write("预览：")
            st.dataframe(imp.head(20), use_container_width=True)

            cols_lower = {str(c).lower(): c for c in imp.columns}

            def pick(*names):
                for n in names:
                    if str(n).lower() in cols_lower:
                        return cols_lower[str(n).lower()]
                return None

            col_date = pick("record_date", "date", "日期")
            col_book = pick("book", "账本")
            col_cat = pick("category", "cat", "类别")
            col_item = pick("item", "remark", "备注", "项目")
            col_amt = pick("amount", "amt", "金额")
            col_type = pick("rtype", "type", "收支类型")

            st.write("字段映射（可修改）：")
            m1, m2, m3 = st.columns(3)

            options = [None] + imp.columns.tolist()

            with m1:
                map_date = st.selectbox(
                    "日期列",
                    options=options,
                    index=(imp.columns.tolist().index(col_date) + 1 if col_date in imp.columns else 0),
                    key="map_date",
                )
                map_amt = st.selectbox(
                    "金额列",
                    options=options,
                    index=(imp.columns.tolist().index(col_amt) + 1 if col_amt in imp.columns else 0),
                    key="map_amt",
                )

            with m2:
                map_book = st.selectbox(
                    "账本列",
                    options=options,
                    index=(imp.columns.tolist().index(col_book) + 1 if col_book in imp.columns else 0),
                    key="map_book",
                )
                map_type = st.selectbox(
                    "收支类型列",
                    options=options,
                    index=(imp.columns.tolist().index(col_type) + 1 if col_type in imp.columns else 0),
                    key="map_type",
                )

            with m3:
                map_cat = st.selectbox(
                    "类别列",
                    options=options,
                    index=(imp.columns.tolist().index(col_cat) + 1 if col_cat in imp.columns else 0),
                    key="map_cat",
                )
                map_item = st.selectbox(
                    "备注列",
                    options=options,
                    index=(imp.columns.tolist().index(col_item) + 1 if col_item in imp.columns else 0),
                    key="map_item",
                )

            default_book = st.selectbox("若 CSV 没有账本列：默认账本", BOOK_OPTIONS, index=0, key="default_book")
            default_type = st.selectbox("若 CSV 没有收支类型列：默认类型", ["支出", "收入"], index=0, key="default_type")

            if st.button("开始导入（追加）", type="primary", key="import_btn"):
                if map_date is None or map_amt is None:
                    st.error("至少需要映射：日期列、金额列")
                else:
                    work = imp.copy()

                    work["_date"] = pd.to_datetime(work[map_date], errors="coerce").dt.date
                    work["_amt"] = work[map_amt].apply(lambda v: abs(parse_amount_any(v)))

                    if map_book is None:
                        work["_book"] = default_book
                    else:
                        work["_book"] = clean_text_series(work[map_book], default_book)
                        work.loc[work["_book"].str.strip() == "", "_book"] = default_book

                    if map_type is None:
                        work["_rtype"] = default_type
                    else:
                        t = clean_text_series(work[map_type], default_type).str.strip()
                        t_lower = t.str.lower()

                        mapped = pd.Series([default_type] * len(t), index=t.index)
                        mapped[t.isin(["支出", "收入"])] = t[t.isin(["支出", "收入"])]
                        mapped[t_lower.isin(["expense", "exp", "out", "0"])] = "支出"
                        mapped[t_lower.isin(["income", "inc", "in", "1"])] = "收入"

                        work["_rtype"] = mapped

                    if map_cat is None:
                        work["_cat"] = "其他"
                    else:
                        work["_cat"] = clean_text_series(work[map_cat], "其他")
                        work.loc[work["_cat"].str.strip() == "", "_cat"] = "其他"

                    if map_item is None:
                        work["_item"] = ""
                    else:
                        work["_item"] = clean_text_series(work[map_item], "")

                    before = len(work)
                    work = work.dropna(subset=["_date"])
                    work = work[work["_amt"].notna()]
                    after = len(work)

                    rows_to_insert = []
                    for _, r in work.iterrows():
                        rows_to_insert.append((
                            r["_date"],
                            str(r["_book"]).strip() or default_book,
                            str(r["_cat"]).strip() or "其他",
                            str(r["_item"]).strip(),
                            float(r["_amt"]),
                            str(r["_rtype"]).strip() if str(r["_rtype"]).strip() in ["收入", "支出"] else default_type,
                        ))

                    insert_records_bulk(USERNAME, rows_to_insert)
                    st.success(f"✅ 导入完成：成功 {len(rows_to_insert)} 行（原始 {before} 行，过滤后 {after} 行）")
                    st.rerun()

        except Exception as e:
            st.error(f"导入失败：{e}")

# ---------------- Tab3：个人设置 ----------------
with tab3:
    st.subheader("👤 个人设置")

    p = get_user_profile(USERNAME)
    x1, x2 = st.columns([2, 3])

    with x1:
        avatar = st.text_input("头像（可用 emoji）", value=p["avatar"] or "🙂", key="profile_avatar")
        nickname = st.text_input("昵称", value=p["nickname"] or USERNAME, key="profile_nickname")

        if st.button("保存资料", type="primary", key="save_profile_btn"):
            set_user_profile(USERNAME, nickname.strip() or USERNAME, avatar.strip() or "🙂")
            st.success("✅ 已保存")
            st.rerun()

    with x2:
        st.markdown("##### 🔑 修改密码")
        oldp = st.text_input("旧密码", type="password", key="old_password")
        newp1 = st.text_input("新密码", type="password", key="new_password_1")
        newp2 = st.text_input("确认新密码", type="password", key="new_password_2")

        if st.button("更新密码", key="update_password_btn"):
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
                    "update users set pass_salt=%s, pass_hash=%s, session_token_hash=%s where username=%s",
                    [hp["salt"], hp["hash"], "", USERNAME],
                )
                cookie_delete(COOKIE_NAME)
                st.success("✅ 密码已更新（已清除保持登录，请重新登录）")
                st.session_state.authed_user = None
                st.session_state.show_login = True
                st.rerun()

    st.divider()

    if st.button("🧹 清除保持登录", key="clear_login_btn"):
        db_execute("update users set session_token_hash=%s where username=%s", ["", USERNAME])
        cookie_delete(COOKIE_NAME)
        st.success("✅ 已清除，下次需要重新登录")
