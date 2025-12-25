import streamlit as st
import pandas as pd
from datetime import datetime
import re
import json
from pathlib import Path
import os
import hashlib
import hmac
import secrets

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

AUTH_PATH = DATA_DIR / "auth_users.json"   # store user credentials (hashed)
APP_CONFIG_PATH = DATA_DIR / "app_config.json"  # store app secret etc.

RECORD_COLS = ["ID", "日期", "账本", "类别", "项目", "金额", "类型"]
BUDGET_COLS = ["年月", "类别", "类型", "预算金额"]


# =================================
# 2) Security: password hashing
# =================================
def load_app_secret() -> str:
    """Get or create a persistent app secret used for hashing."""
    if APP_CONFIG_PATH.exists():
        cfg = json.loads(APP_CONFIG_PATH.read_text(encoding="utf-8"))
        if cfg.get("app_secret"):
            return cfg["app_secret"]
    secret = secrets.token_hex(32)
    APP_CONFIG_PATH.write_text(json.dumps({"app_secret": secret}, ensure_ascii=False), encoding="utf-8")
    return secret


APP_SECRET = load_app_secret()


def pbkdf2_hash_password(password: str, salt_hex: str | None = None) -> dict:
    """Return dict with salt and hash using PBKDF2-HMAC-SHA256."""
    if salt_hex is None:
        salt = secrets.token_bytes(16)
    else:
        salt = bytes.fromhex(salt_hex)
    # combine user password with app secret so even if auth_users.json leaked, cracking harder
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
    """Allow letters/digits/_ only to avoid path traversal."""
    u = (u or "").strip()
    u = re.sub(r"[^A-Za-z0-9_]", "", u)
    return u.lower()


# =================================
# 3) User-scoped persistence
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


def persist_user_state(username: str):
    p = paths_for_user(username)
    st.session_state.records.to_csv(p["records"], index=False, encoding="utf-8-sig")
    st.session_state.budgets.to_csv(p["budgets"], index=False, encoding="utf-8-sig")
    p["config"].write_text(
        json.dumps({"init_balance": st.session_state.init_balance}, ensure_ascii=False),
        encoding="utf-8"
    )


def load_user_state(username: str):
    p = paths_for_user(username)
    st.session_state.records = prepare_for_editor(load_csv(p["records"], RECORD_COLS))
    st.session_state.budgets = load_csv(p["budgets"], BUDGET_COLS)
    if p["config"].exists():
        st.session_state.init_balance = json.loads(p["config"].read_text(encoding="utf-8")).get("init_balance", 0.0)
    else:
        st.session_state.init_balance = 0.0


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
# 4) Auth UI (register/login/logout)
# =================================
def auth_panel():
    st.sidebar.header("🔐 登录 / 注册")

    if "authed_user" not in st.session_state:
        st.session_state.authed_user = None

    db = load_auth_db()

    if st.session_state.authed_user:
        st.sidebar.success(f"已登录：{st.session_state.authed_user}")
        if st.sidebar.button("退出登录"):
            st.session_state.authed_user = None
            # 清理用户数据（防止串号）
            for k in ["records", "budgets", "init_balance"]:
                if k in st.session_state:
                    del st.session_state[k]
            st.rerun()
        return

    tabs = st.sidebar.tabs(["登录", "注册"])

    with tabs[0]:
        u = st.text_input("用户名", key="login_user")
        p = st.text_input("密码", type="password", key="login_pass")
        if st.button("登录", key="login_btn"):
            uu = normalize_username(u)
            if not uu:
                st.sidebar.error("用户名只能包含字母/数字/下划线")
                return
            if uu not in db:
                st.sidebar.error("用户不存在")
                return
            rec = db[uu]
            if verify_password(p, rec["salt"], rec["hash"]):
                st.session_state.authed_user = uu
                load_user_state(uu)
                st.rerun()
            else:
                st.sidebar.error("密码错误")

    with tabs[1]:
        u = st.text_input("新用户名（字母/数字/下划线）", key="reg_user")
        p1 = st.text_input("新密码", type="password", key="reg_pass1")
        p2 = st.text_input("确认密码", type="password", key="reg_pass2")
        if st.button("注册", key="reg_btn"):
            uu = normalize_username(u)
            if not uu:
                st.sidebar.error("用户名只能包含字母/数字/下划线")
                return
            if uu in db:
                st.sidebar.error("用户名已存在")
                return
            if len(p1) < 6:
                st.sidebar.error("密码至少 6 位")
                return
            if p1 != p2:
                st.sidebar.error("两次密码不一致")
                return

            h = pbkdf2_hash_password(p1)
            db[uu] = {"salt": h["salt"], "hash": h["hash"], "created_at": datetime.now().isoformat()}
            save_auth_db(db)

            # init user storage
            ud = user_dir(uu)
            (ud / "records.csv").write_text(",".join(RECORD_COLS) + "\n", encoding="utf-8")
            (ud / "budgets.csv").write_text(",".join(BUDGET_COLS) + "\n", encoding="utf-8")
            (ud / "config.json").write_text(json.dumps({"init_balance": 0.0}), encoding="utf-8")

            st.sidebar.success("✅ 注册成功！请返回「登录」登录使用")


# =================================
# 5) Run auth first
# =================================
auth_panel()

if not st.session_state.get("authed_user"):
    st.title("💰 私人理财中心（多用户）")
    st.info("请先在左侧登录/注册后使用。")
    st.stop()

USERNAME = st.session_state.authed_user

# Ensure state exists (in case of rerun)
if "records" not in st.session_state:
    load_user_state(USERNAME)

# =================================
# 6) Sidebar: new record
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
# 7) Dashboard
# =================================
st.title(f"💰 我的财务一体化看板（用户：{USERNAME}）")

df0 = enrich_records(st.session_state.records)
inc = df0[df0["类型"] == "收入"]["金额"].sum() if not df0.empty else 0.0
exp = df0[df0["类型"] == "支出"]["金额"].sum() if not df0.empty else 0.0
bal = float(st.session_state.init_balance) + inc - exp

c1, c2, c3 = st.columns(3)
c1.metric("目前总结余", f"¥ {bal:,.2f}")
c2.metric("累计总收入", f"¥ {inc:,.2f}")
c3.metric("累计总支出", f"¥ {exp:,.2f}", delta=f"-{exp:,.2f}")

# =================================
# 8) Tabs
# =================================
tab1, tab2 = st.tabs(["📋 明细（行内修改/删除）", "📈 理财中心（统计/导入）"])

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

# -------- Tab2: import + stats
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
    st.subheader("📥 数据导入（当前用户：只导入到自己的账本）")

    up = st.file_uploader("上传 CSV（列名：日期/账本/类别/项目/金额/类型）", type=["csv"])
    if up is not None:
        try:
            df_in = pd.read_csv(up)
            st.dataframe(df_in.head(20), use_container_width=True)

            if st.button("✅ 导入到我的账本"):
                # 容错映射
                col_map = {c: c.strip() for c in df_in.columns}
                df_in.rename(columns=col_map, inplace=True)

                tmp = pd.DataFrame()
                tmp["日期"] = pd.to_datetime(df_in.get("日期"), errors="coerce")
                tmp = tmp.dropna(subset=["日期"])
                tmp["日期"] = tmp["日期"].dt.date

                tmp["账本"] = df_in.get("账本", "生活主账")
                tmp["类别"] = df_in.get("类别", "其他")
                tmp["项目"] = df_in.get("项目", "")
                tmp["金额"] = df_in.get("金额", 0).astype(str).apply(parse_amount)
                tmp["类型"] = df_in.get("类型", "").astype(str).apply(normalize_type)
                tmp.loc[~tmp["类型"].isin(["收入", "支出"]), "类型"] = "支出"
                tmp["金额"] = tmp["金额"].abs()

                start = next_id()
                tmp.insert(0, "ID", range(start, start + len(tmp)))
                tmp = prepare_for_editor(tmp[RECORD_COLS])

                st.session_state.records = prepare_for_editor(pd.concat([prepare_for_editor(st.session_state.records), tmp], ignore_index=True))
                persist_user_state(USERNAME)
                st.success(f"✅ 已导入 {len(tmp)} 条到你的账户")
                st.rerun()

        except Exception as e:
            st.error(f"导入失败：{e}")

# =================================
# 9) Settings
# =================================
with st.expander("⚙️ 账户配置（仅影响当前用户）"):
    new_init = st.number_input("设置起始资金", value=float(st.session_state.init_balance))
    if new_init != st.session_state.init_balance:
        st.session_state.init_balance = float(new_init)
        persist_user_state(USERNAME)
        st.success("起始资金已保存。")

    if st.button("🚨 清空我的所有记录（不可逆）"):
        st.session_state.records = pd.DataFrame(columns=RECORD_COLS)
        persist_user_state(USERNAME)
        st.rerun()
