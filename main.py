# app.py
import streamlit as st
import pandas as pd
from datetime import datetime
import re
import json
from pathlib import Path

# =========================
# 0) Page
# =========================
st.set_page_config(page_title="私人理财中心", layout="wide")

# =========================
# 1) 本地持久化（关键：改代码/重启不丢数据）
# =========================
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

RECORDS_PATH = DATA_DIR / "records.csv"
BUDGETS_PATH = DATA_DIR / "budgets.csv"
CONFIG_PATH = DATA_DIR / "config.json"

RECORD_COLS = ["ID", "日期", "账本", "类别", "项目", "金额", "类型"]
BUDGET_COLS = ["年月", "类别", "类型", "预算金额"]


def load_csv(path: Path, cols: list[str]) -> pd.DataFrame:
    if path.exists():
        df = pd.read_csv(path)
        for c in cols:
            if c not in df.columns:
                df[c] = ""
        return df[cols]
    return pd.DataFrame(columns=cols)


def persist_all():
    """把当前 session_state 写入磁盘"""
    st.session_state.records.to_csv(RECORDS_PATH, index=False, encoding="utf-8-sig")
    st.session_state.budgets.to_csv(BUDGETS_PATH, index=False, encoding="utf-8-sig")
    CONFIG_PATH.write_text(
        json.dumps({"init_balance": st.session_state.init_balance}, ensure_ascii=False),
        encoding="utf-8"
    )


# =========================
# 2) Session State（启动自动从磁盘读取）
# =========================
if "records" not in st.session_state:
    st.session_state.records = load_csv(RECORDS_PATH, RECORD_COLS)

if "budgets" not in st.session_state:
    st.session_state.budgets = load_csv(BUDGETS_PATH, BUDGET_COLS)

if "init_balance" not in st.session_state:
    if CONFIG_PATH.exists():
        st.session_state.init_balance = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("init_balance", 0.0)
    else:
        st.session_state.init_balance = 0.0


# =========================
# 3) Helpers
# =========================
def parse_amount(s: str) -> float:
    """安全解析金额：支持 1,234 / $120 / -30 / 空值"""
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


def guess_type_and_amount(line: str):
    s = (line or "").strip()
    if not s:
        return "", None

    t = ""
    if any(k in s for k in ["收入", "income", "到账", "工资", "入账", "+"]):
        t = "收入"
    if any(k in s for k in ["支出", "expense", "消费", "付款", "花了", "转出", "-"]):
        t = "支出"

    nums = re.findall(r"[-+]?\d[\d,]*\.?\d*", s)
    amt = None
    if nums:
        amt = parse_amount(nums[-1])
        if amt and 1900 <= amt <= 2100 and len(nums) >= 2:
            amt = parse_amount(nums[-2])

    if t == "" and nums:
        if nums[-1].startswith("-"):
            t = "支出"
        elif nums[-1].startswith("+"):
            t = "收入"

    return t, amt


def parse_memo_text_to_df(text: str) -> pd.DataFrame:
    """
    粘贴导入文本：要求每行至少包含【YYYY-MM-DD】或【YYYY/MM/DD】+ 金额
    """
    rows = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue

        date_match = re.search(r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})", line)
        if not date_match:
            continue

        d = pd.to_datetime(date_match.group(1), errors="coerce")
        if pd.isna(d):
            continue
        d = d.date()

        t, amt = guess_type_and_amount(line)
        if amt is None:
            continue

        book = "生活主账"
        cat = "其他"

        for b in ["生活主账", "车子专项", "学费/购汇", "理财账本"]:
            if b in line:
                book = b
                break

        exp_cats = ["Eat outside", "Shopping", "Bill", "Petrol", "Insurance", "Rent"]
        inc_cats = ["工资", "业余项目", "亲情赠与", "理财收益"]
        for c in exp_cats + inc_cats:
            if c in line:
                cat = c
                break

        tmp = re.sub(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", "", line).strip()
        tmp = re.sub(r"[-+]?\d[\d,]*\.?\d*", "", tmp).strip()
        item = re.sub(r"\s+", " ", tmp)

        if t == "" and cat in inc_cats:
            t = "收入"
        if t == "" and cat in exp_cats:
            t = "支出"
        if t == "":
            t = "支出"

        rows.append({
            "日期": d,
            "账本": book,
            "类别": cat,
            "项目": item,
            "金额": float(abs(amt)),
            "类型": t
        })

    return pd.DataFrame(rows)


# =========================
# 4) Sidebar Input
# =========================
st.sidebar.header("📝 记账录入")

t_type = st.sidebar.selectbox("1. 选择收支类型", ["支出", "收入"], key="type_selector")

if t_type == "支出":
    cat_opts = ["Eat outside", "Shopping", "Bill", "Petrol", "Insurance", "Rent", "其他"]
else:
    cat_opts = ["工资", "业余项目", "亲情赠与", "理财收益", "其他"]

with st.sidebar.form("record_form", clear_on_submit=True):
    d = st.date_input("2. 日期", datetime.now())
    b = st.selectbox("3. 归属账本", ["生活主账", "车子专项", "学费/购汇", "理财账本"])
    c_base = st.selectbox("4. 选择分类", cat_opts)
    custom_c = st.text_input("如选'其他'，请手动输入名称")
    item = st.text_input("5. 备注项目")
    amt_input = st.text_input("6. 金额 (直接输入)", value="", placeholder="0")

    submit = st.form_submit_button("确认存入账本")

    if submit:
        try:
            amt = parse_amount(amt_input)
            final_cat = custom_c if (c_base == "其他" and custom_c.strip() != "") else c_base

            new_id = int(st.session_state.records["ID"].max() + 1) if not st.session_state.records.empty else 1
            new_row = {
                "ID": new_id,
                "日期": d,
                "账本": b,
                "类别": final_cat,
                "项目": item,
                "金额": float(amt),
                "类型": t_type
            }
            st.session_state.records = pd.concat([st.session_state.records, pd.DataFrame([new_row])], ignore_index=True)
            persist_all()  # ✅ 持久化
            st.sidebar.success(f"✅ 已记录{t_type}：{final_cat} ¥{amt:,.2f}")
        except Exception:
            st.sidebar.error("金额输入有误")


# =========================
# 5) Dashboard
# =========================
st.title("💰 我的财务一体化看板")

df0 = enrich_records(st.session_state.records)

inc = df0[df0["类型"] == "收入"]["金额"].sum() if not df0.empty else 0.0
exp = df0[df0["类型"] == "支出"]["金额"].sum() if not df0.empty else 0.0
bal = st.session_state.init_balance + inc - exp

c1, c2, c3 = st.columns(3)
c1.metric("目前总结余", f"¥ {bal:,.2f}")
c2.metric("累计总收入", f"¥ {inc:,.2f}")
c3.metric("累计总支出", f"¥ {exp:,.2f}", delta=f"-{exp:,.2f}")


# =========================
# 6) Tabs
# =========================
tab1, tab2 = st.tabs(["📋 历史明细与删除", "📈 理财中心（统计/导入/预算）"])

# ---- Tab1: History & Delete
with tab1:
    if not df0.empty:
        st.dataframe(df0.sort_values("ID", ascending=False), use_container_width=True)
        st.divider()

        st.write("🗑️ **删除错误记录**")
        target_id = st.selectbox("选择要删除的记录 ID", options=df0["ID"].tolist())
        if st.button("🔴 确认删除该记录"):
            st.session_state.records = st.session_state.records[st.session_state.records["ID"] != target_id]
            persist_all()  # ✅ 持久化
            st.rerun()
    else:
        st.info("尚无记录，请在左侧录入")

# ---- Tab2: Finance Center
with tab2:
    st.subheader("📥 数据导入（CSV/Excel/备忘录文本）")

    imp_tab1, imp_tab2, imp_tab3 = st.tabs(["上传CSV/Excel", "粘贴备忘录文本", "模板下载"])

    with imp_tab1:
        up = st.file_uploader("上传文件（CSV / XLSX）", type=["csv", "xlsx"])
        if up is not None:
            try:
                if up.name.endswith(".csv"):
                    df_in = pd.read_csv(up)
                else:
                    df_in = pd.read_excel(up)

                st.write("预览：")
                st.dataframe(df_in.head(30), use_container_width=True)

                st.info("映射列名到系统字段（列名不一致也没关系）。")

                cols = df_in.columns.tolist()
                m1, m2, m3 = st.columns(3)
                with m1:
                    col_date = st.selectbox("日期列", cols)
                    col_amt = st.selectbox("金额列", cols)
                with m2:
                    col_type = st.selectbox("类型列（收入/支出，可选）", ["<无>"] + cols)
                    col_cat = st.selectbox("类别列（可选）", ["<无>"] + cols)
                with m3:
                    col_book = st.selectbox("账本列（可选）", ["<无>"] + cols)
                    col_item = st.selectbox("项目/备注列（可选）", ["<无>"] + cols)

                if st.button("✅ 解析并导入", key="import_file_btn"):
                    tmp = pd.DataFrame()
                    tmp["日期"] = pd.to_datetime(df_in[col_date], errors="coerce")
                    tmp = tmp.dropna(subset=["日期"])
                    tmp["日期"] = tmp["日期"].dt.date

                    tmp["金额"] = df_in.loc[tmp.index, col_amt].astype(str).apply(parse_amount)

                    if col_type != "<无>":
                        tmp["类型"] = df_in.loc[tmp.index, col_type].astype(str).apply(normalize_type)
                        tmp = tmp[tmp["类型"].isin(["收入", "支出"])]
                    else:
                        tmp["类型"] = tmp["金额"].apply(lambda x: "支出" if x < 0 else "收入")
                        tmp["金额"] = tmp["金额"].abs()

                    if col_cat != "<无>":
                        tmp["类别"] = df_in.loc[tmp.index, col_cat].astype(str).replace({"": "其他"}).fillna("其他")
                    else:
                        tmp["类别"] = "其他"

                    if col_book != "<无>":
                        tmp["账本"] = df_in.loc[tmp.index, col_book].astype(str).replace({"": "生活主账"}).fillna("生活主账")
                    else:
                        tmp["账本"] = "生活主账"

                    if col_item != "<无>":
                        tmp["项目"] = df_in.loc[tmp.index, col_item].astype(str).fillna("")
                    else:
                        tmp["项目"] = ""

                    tmp["金额"] = tmp["金额"].abs()

                    start_id = int(st.session_state.records["ID"].max() + 1) if not st.session_state.records.empty else 1
                    tmp.insert(0, "ID", range(start_id, start_id + len(tmp)))
                    tmp = tmp[RECORD_COLS]

                    st.session_state.records = pd.concat([st.session_state.records, tmp], ignore_index=True)
                    persist_all()  # ✅ 持久化
                    st.success(f"✅ 已导入 {len(tmp)} 条记录（已自动保存到 data/records.csv）")
                    st.rerun()

            except Exception as e:
                st.error(f"导入失败：{e}")

    with imp_tab2:
        st.caption("每行至少包含：年份日期 + 金额（如 2025-12-01 支出 Rent 500）")
        memo = st.text_area(
            "粘贴区域",
            height=220,
            placeholder="例：\n2025-12-01 收入 工资 3000\n2025-12-02 支出 Rent 500\n2025/12/03 支出 Eat outside 午饭 35"
        )

        if st.button("✅ 解析文本并导入", key="import_memo_btn"):
            df_m = parse_memo_text_to_df(memo)
            if df_m.empty:
                st.warning("没有解析出有效记录：请确保每行至少包含【YYYY-MM-DD】或【YYYY/MM/DD】日期 + 金额。")
            else:
                st.write("解析预览：")
                st.dataframe(df_m.head(100), use_container_width=True)

                start_id = int(st.session_state.records["ID"].max() + 1) if not st.session_state.records.empty else 1
                df_m.insert(0, "ID", range(start_id, start_id + len(df_m)))
                df_m = df_m[RECORD_COLS]

                st.session_state.records = pd.concat([st.session_state.records, df_m], ignore_index=True)
                persist_all()  # ✅ 持久化
                st.success(f"✅ 已导入 {len(df_m)} 条记录（已自动保存到 data/records.csv）")
                st.rerun()

    with imp_tab3:
        template = pd.DataFrame(columns=["日期", "账本", "类别", "项目", "金额", "类型"])
        st.download_button(
            "⬇️ 下载 CSV 模板",
            data=template.to_csv(index=False).encode("utf-8-sig"),
            file_name="import_template.csv",
            mime="text/csv"
        )

    st.divider()

    # =========================
    # 统计中心
    # =========================
    st.subheader("📊 统计中心（按年/月/日期区间）")

    df = enrich_records(st.session_state.records)
    if df.empty:
        st.info("暂无数据可统计，请先录入或导入。")
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
            if isinstance(dr, tuple) and len(dr) == 2:
                start_d, end_d = dr
            else:
                start_d = end_d = dr
            fdf = df[(df["日期"].dt.date >= start_d) & (df["日期"].dt.date <= end_d)]

        fdf = fdf[fdf["类型"].isin(type_filter)]
        st.caption(f"当前筛选后记录数：{len(fdf)}")

        if fdf.empty:
            st.warning("筛选后没有记录，请调整条件。")
        else:
            income_sum = fdf[fdf["类型"] == "收入"]["金额"].sum()
            expense_sum = fdf[fdf["类型"] == "支出"]["金额"].sum()
            net_sum = income_sum - expense_sum

            s1, s2, s3 = st.columns(3)
            s1.metric("筛选区间收入合计", f"¥ {income_sum:,.2f}")
            s2.metric("筛选区间支出合计", f"¥ {expense_sum:,.2f}")
            s3.metric("筛选区间净额(收入-支出)", f"¥ {net_sum:,.2f}")

            dim_col1, dim_col2 = st.columns([1.5, 2.5])
            with dim_col1:
                group_dim = st.selectbox("选择统计维度", ["类别", "账本", "类型", "项目", "年月", "年份", "月份"], index=0)
            with dim_col2:
                sort_desc = st.checkbox("按金额从高到低排序", value=True)

            summary = (
                fdf.groupby(group_dim, as_index=False)["金额"]
                .sum()
                .rename(columns={"金额": "总额"})
            )
            summary = summary.sort_values("总额", ascending=not sort_desc)
            st.write("### ✅ 分支汇总")
            st.dataframe(summary.round(2), use_container_width=True)

            st.write("### 📈 趋势（按月汇总）")
            mdf = fdf.groupby(["年月", "类型"], as_index=False)["金额"].sum().sort_values("年月")
            mwide = mdf.pivot_table(index="年月", columns="类型", values="金额", aggfunc="sum", fill_value=0)
            st.line_chart(mwide)

            st.write("### 🧁 类别占比（条形图）")
            chart_type = st.radio("选择占比类型", ["支出占比", "收入占比"], horizontal=True)
            target_type = "支出" if chart_type == "支出占比" else "收入"
            cdf = fdf[fdf["类型"] == target_type]
            if cdf.empty:
                st.info(f"当前筛选条件下没有{target_type}记录。")
            else:
                cat_sum = cdf.groupby("类别")["金额"].sum().sort_values(ascending=False)
                st.bar_chart(cat_sum)

    st.divider()

    # =========================
    # 预算（可选）
    # =========================
    st.subheader("🎯 预算（可选）")

    left, right = st.columns([1.2, 2.8])
    with left:
        st.markdown("**录入/更新预算**")
        df_now = enrich_records(st.session_state.records)
        all_ym = sorted(df_now["年月"].unique().tolist()) if not df_now.empty else ["2025-12"]
        all_cats = sorted(df_now["类别"].unique().tolist()) if not df_now.empty else ["其他"]

        bud_ym = st.selectbox("预算年月", all_ym)
        bud_cat = st.selectbox("预算类别", all_cats)
        bud_type = st.selectbox("预算类型", ["支出", "收入"], index=0)
        bud_amt_str = st.text_input("预算金额", placeholder="例如 2000")

        if st.button("✅ 保存预算"):
            bud_amt = parse_amount(bud_amt_str)
            bud_df = st.session_state.budgets.copy()
            mask = (bud_df["年月"] == bud_ym) & (bud_df["类别"] == bud_cat) & (bud_df["类型"] == bud_type)
            bud_df = bud_df[~mask]
            bud_df = pd.concat([bud_df, pd.DataFrame([{
                "年月": bud_ym,
                "类别": bud_cat,
                "类型": bud_type,
                "预算金额": float(bud_amt)
            }])], ignore_index=True)
            st.session_state.budgets = bud_df
            persist_all()  # ✅ 持久化
            st.success(f"已保存预算：{bud_ym}/{bud_cat}/{bud_type}=¥{bud_amt:,.2f}")

    with right:
        st.markdown("**预算对比视图**")
        df_now = enrich_records(st.session_state.records)
        if df_now.empty:
            st.info("暂无记录。")
        else:
            view_ym_list = sorted(df_now["年月"].unique().tolist())
            view_ym = st.multiselect("选择查看的年月", view_ym_list, default=view_ym_list[-1:] if view_ym_list else [])

            if view_ym:
                actual = (
                    df_now[df_now["年月"].isin(view_ym)]
                    .groupby(["年月", "类别", "类型"], as_index=False)["金额"]
                    .sum()
                    .rename(columns={"金额": "实际金额"})
                )
                bud = st.session_state.budgets.copy()
                if not bud.empty:
                    bud = bud[bud["年月"].isin(view_ym)]
                comp = pd.merge(actual, bud, on=["年月", "类别", "类型"], how="left")
                comp["预算金额"] = comp["预算金额"].fillna(0.0)
                comp["差额(实际-预算)"] = (comp["实际金额"] - comp["预算金额"]).round(2)
                st.dataframe(comp.sort_values(["年月", "类型", "实际金额"], ascending=[True, True, False]), use_container_width=True)
            else:
                st.info("请选择至少一个年月查看预算对比。")

    st.divider()

    # ✅ 紧急备份按钮（任何时候都能导出）
    st.subheader("🛟 紧急备份（建议你现在点一次）")
    st.download_button(
        "⬇️ 下载当前 records 备份（records_backup.csv）",
        data=st.session_state.records.to_csv(index=False).encode("utf-8-sig"),
        file_name="records_backup.csv",
        mime="text/csv"
    )
    st.caption("本地也已自动保存到 data/records.csv；这个按钮是额外保险。")


# =========================
# 7) Settings
# =========================
with st.expander("⚙️ 账户配置"):
    new_init = st.number_input("1. 设置起始资金", value=float(st.session_state.init_balance))
    if new_init != st.session_state.init_balance:
        st.session_state.init_balance = float(new_init)
        persist_all()  # ✅ 持久化

    colx, coly = st.columns(2)
    with colx:
        if st.button("💾 手动保存到本地（写入 data/records.csv）"):
            persist_all()
            st.success("已保存。")
    with coly:
        if st.button("🚨 清空所有记录（不可逆）"):
            st.session_state.records = pd.DataFrame(columns=RECORD_COLS)
            persist_all()  # ✅ 持久化
            st.rerun()
