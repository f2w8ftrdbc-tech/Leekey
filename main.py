import streamlit as st
import pandas as pd
from datetime import datetime
import re

# --- 1. 初始化 ---
st.set_page_config(page_title="私人理财中心", layout="wide")

if 'records' not in st.session_state:
    st.session_state.records = pd.DataFrame(columns=["ID", "日期", "账本", "类别", "项目", "金额", "类型"])
if 'init_balance' not in st.session_state:
    st.session_state.init_balance = 0.0
if 'budgets' not in st.session_state:
    # 预算表：按“年月 + 类别 + 类型(收入/支出)”存
    st.session_state.budgets = pd.DataFrame(columns=["年月", "类别", "类型", "预算金额"])


# --- 工具：安全解析金额（替代 eval）---
def parse_amount(s: str) -> float:
    s = (s or "").strip()
    if s == "":
        return 0.0
    # 只保留数字/小数点/负号，支持 "$1,234.5" 这类输入
    clean = re.sub(r"[^\d\.\-]", "", s)
    if clean in ["", "-", ".", "-."]:
        return 0.0
    return float(clean)


# --- 工具：预处理记录 DF（日期、年月、年、月）---
def enrich_records(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    out["日期"] = pd.to_datetime(out["日期"])
    out["年份"] = out["日期"].dt.year
    out["月份"] = out["日期"].dt.month
    out["年月"] = out["日期"].dt.to_period("M").astype(str)
    return out

import io

def normalize_type(t: str) -> str:
    t = (t or "").strip().lower()
    if t in ["收入", "income", "in", "+", "earning", "earnings"]:
        return "收入"
    if t in ["支出", "expense", "out", "-", "spend", "spending"]:
        return "支出"
    return ""

def parse_date_any(x):
    # 尝试多种日期格式
    try:
        return pd.to_datetime(x, errors="coerce").date()
    except:
        return None

def guess_type_and_amount(line: str):
    # 从一行文本里猜测收入/支出 和 金额
    s = (line or "").strip()
    if not s:
        return "", None

    # 类型判断：含关键字，或金额前缀符号
    t = ""
    if any(k in s for k in ["收入", "income", "到账", "工资", "入账", "+"]):
        t = "收入"
    if any(k in s for k in ["支出", "expense", "消费", "付款", "花了", "转出", "-"]):
        # 若同时出现，以“支出”为优先（更保守）
        t = "支出"

    # 金额：取最后一个像数字的片段（避免日期被当金额）
    nums = re.findall(r"[-+]?\d[\d,]*\.?\d*", s)
    amt = None
    if nums:
        # 取最大可能为金额的（通常最后一个）
        amt = parse_amount(nums[-1])
        # 如果识别成 2024 这类年份，且前面还有数字，尝试取更靠后的
        if amt and amt >= 1900 and amt <= 2100 and len(nums) >= 2:
            amt = parse_amount(nums[-2])

    # 若类型仍未知：看金额符号
    if t == "" and nums:
        if nums[-1].startswith("-"):
            t = "支出"
        elif nums[-1].startswith("+"):
            t = "收入"

    return t, amt

def parse_memo_text_to_df(text: str) -> pd.DataFrame:
    """
    支持类似：
    2024-03-01 支出 Eat outside 午饭 35
    2024/03/02 income 工资 8000
    3.5 Rent -1200
    2024-03-03 车子专项 Petrol 80 支出
    或者更随意的：日期/描述/金额混在一起
    """
    rows = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue

        # 先找日期（行里任意位置）
        date_match = re.search(r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})", line)
        d = parse_date_any(date_match.group(1)) if date_match else None
        if d is None:
            # 没日期就跳过（或你也可以默认今天）
            continue

        t, amt = guess_type_and_amount(line)
        if amt is None:
            continue

        # 默认值
        book = "生活主账"
        cat = "其他"
        item = ""

        # 粗略拆分：去掉日期和金额部分后，剩下当备注
        line_wo_date = re.sub(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", "", line).strip()
        # 去掉金额数字
        line_wo_amt = re.sub(r"[-+]?\d[\d,]*\.?\d*", "", line_wo_date).strip()
        item = re.sub(r"\s+", " ", line_wo_amt)

        # 尝试从文本中抓“账本/类别”（如果你文本里有这些关键词）
        # 账本：匹配你系统的四类
        for b in ["生活主账", "车子专项", "学费/购汇", "理财账本"]:
            if b in line:
                book = b
                break

        # 类别：支出/收入各自类别表
        exp_cats = ["Eat outside", "Shopping", "Bill", "Petrol", "Insurance", "Rent"]
        inc_cats = ["工资", "业余项目", "亲情赠与", "理财收益"]
        for c in exp_cats + inc_cats:
            if c in line:
                cat = c
                break

        # 如果类型还没猜出来：按类别兜底
        if t == "" and cat in inc_cats:
            t = "收入"
        if t == "" and cat in exp_cats:
            t = "支出"
        if t == "":
            # 最后兜底：金额为正默认支出（更保守），你也可以反过来
            t = "支出"

        rows.append({
            "日期": d,
            "账本": book,
            "类别": cat,
            "项目": item,
            "金额": float(abs(amt)) if t == "支出" else float(abs(amt)),
            "类型": t
        })

    return pd.DataFrame(rows)


# --- 2. 侧边栏：实时联动逻辑 ---
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

            new_id = (st.session_state.records["ID"].max() + 1) if (not st.session_state.records.empty) else 1
            new_row = {
                "ID": int(new_id),
                "日期": d,
                "账本": b,
                "类别": final_cat,
                "项目": item,
                "金额": float(amt),
                "类型": t_type
            }
            st.session_state.records = pd.concat(
                [st.session_state.records, pd.DataFrame([new_row])],
                ignore_index=True
            )
            st.sidebar.success(f"✅ 已记录{t_type}：{final_cat}  ¥{amt:,.2f}")
        except Exception:
            st.sidebar.error("金额输入有误，请重新输入（如 12.5 或 1200）")

st.divider()
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
            st.dataframe(df_in.head(20), use_container_width=True)

            st.info("请在下方映射列名到系统字段（你的原数据列名不一致也没关系）。")

            cols = df_in.columns.tolist()
            m1, m2, m3 = st.columns(3)
            with m1:
                col_date = st.selectbox("日期列", cols)
                col_amt  = st.selectbox("金额列", cols)
            with m2:
                col_type = st.selectbox("类型列（收入/支出，可选）", ["<无>"] + cols)
                col_cat  = st.selectbox("类别列（可选）", ["<无>"] + cols)
            with m3:
                col_book = st.selectbox("账本列（可选）", ["<无>"] + cols)
                col_item = st.selectbox("项目/备注列（可选）", ["<无>"] + cols)

            if st.button("✅ 解析并导入", key="import_file_btn"):
                tmp = pd.DataFrame()
                tmp["日期"] = pd.to_datetime(df_in[col_date], errors="coerce").dt.date
                tmp["金额"] = df_in[col_amt].astype(str).apply(parse_amount)

                if col_type != "<无>":
                    tmp["类型"] = df_in[col_type].astype(str).apply(normalize_type)
                else:
                    # 没有类型列就默认：金额<0为支出，否则收入（你也可改成全部支出）
                    tmp["类型"] = tmp["金额"].apply(lambda x: "支出" if x < 0 else "收入")
                    tmp["金额"] = tmp["金额"].abs()

                if col_cat != "<无>":
                    tmp["类别"] = df_in[col_cat].astype(str).replace({"": "其他"}).fillna("其他")
                else:
                    tmp["类别"] = "其他"

                if col_book != "<无>":
                    tmp["账本"] = df_in[col_book].astype(str).replace({"": "生活主账"}).fillna("生活主账")
                else:
                    tmp["账本"] = "生活主账"

                if col_item != "<无>":
                    tmp["项目"] = df_in[col_item].astype(str).fillna("")
                else:
                    tmp["项目"] = ""

                # 清洗掉无日期/无金额/无类型的行
                tmp = tmp.dropna(subset=["日期"])
                tmp = tmp[tmp["类型"].isin(["收入", "支出"])]
                tmp["金额"] = tmp["金额"].abs()

                # 生成 ID 并合并进 records
                start_id = int(st.session_state.records["ID"].max() + 1) if not st.session_state.records.empty else 1
                tmp.insert(0, "ID", range(start_id, start_id + len(tmp)))

                # 列顺序对齐
                tmp = tmp[["ID", "日期", "账本", "类别", "项目", "金额", "类型"]]

                st.session_state.records = pd.concat([st.session_state.records, tmp], ignore_index=True)
                st.success(f"✅ 已导入 {len(tmp)} 条记录")
                st.rerun()

        except Exception as e:
            st.error(f"导入失败：{e}")

with imp_tab2:
    st.caption("把备忘录里的多行文本直接粘贴进来。每行尽量包含：日期 + 金额（类型/类别/备注可有可无）。")
    memo = st.text_area("粘贴区域", height=200, placeholder="例：\n2024-03-01 支出 Eat outside 午饭 35\n2024/03/02 工资 8000 收入\n2024-03-03 车子专项 Petrol 80 支出")
    if st.button("✅ 解析文本并导入", key="import_memo_btn"):
        df_m = parse_memo_text_to_df(memo)
        if df_m.empty:
            st.warning("没有解析出有效记录。请确保每行至少包含：日期 + 金额。")
        else:
            st.write("解析预览：")
            st.dataframe(df_m.head(50), use_container_width=True)

            # 确认导入（不需要二次确认也可直接导入，这里给你更稳）
            if st.button("➡️ 确认写入账本", key="confirm_import_memo"):
                start_id = int(st.session_state.records["ID"].max() + 1) if not st.session_state.records.empty else 1
                df_m.insert(0, "ID", range(start_id, start_id + len(df_m)))
                df_m = df_m[["ID", "日期", "账本", "类别", "项目", "金额", "类型"]]
                st.session_state.records = pd.concat([st.session_state.records, df_m], ignore_index=True)
                st.success(f"✅ 已导入 {len(df_m)} 条记录")
                st.rerun()

with imp_tab3:
    st.write("你可以先下载模板，按模板填好再上传导入。")
    template = pd.DataFrame(columns=["日期", "账本", "类别", "项目", "金额", "类型"])
    st.download_button(
        "⬇️ 下载 CSV 模板",
        data=template.to_csv(index=False).encode("utf-8-sig"),
        file_name="import_template.csv",
        mime="text/csv"
    )


# --- 3. 汇总看板 ---
st.title("💰 我的财务一体化看板")

df0 = enrich_records(st.session_state.records)

inc = df0[df0['类型'] == "收入"]['金额'].sum() if not df0.empty else 0
exp = df0[df0['类型'] == "支出"]['金额'].sum() if not df0.empty else 0
bal = st.session_state.init_balance + inc - exp

c1, c2, c3 = st.columns(3)
c1.metric("目前总结余", f"¥ {bal:,.2f}")
c2.metric("累计总收入", f"¥ {inc:,.2f}")
c3.metric("累计总支出", f"¥ {exp:,.2f}", delta=f"-{exp:,.2f}")

# --- 4. 历史记录与删除 ---
tab1, tab2 = st.tabs(["📋 历史明细与删除", "📈 理财中心（统计/图表/预算）"])

with tab1:
    if not st.session_state.records.empty:
        st.dataframe(df0.sort_values("ID", ascending=False), use_container_width=True)
        st.divider()
        st.write("🗑️ **删除错误记录**")
        target_id = st.selectbox("选择要删除的记录 ID", options=df0["ID"].tolist())
        if st.button("🔴 确认删除该记录"):
            st.session_state.records = st.session_state.records[st.session_state.records["ID"] != target_id]
            st.rerun()
    else:
        st.info("尚无记录，请在左侧录入")


# --- 统计中心 + 图表 + 预算 ---
with tab2:
    st.subheader("📈 理财中心")
    st.link_button("🚀 前往养基宝查看实时持仓", "https://wx.yangjibao.com/app/hold")
    st.divider()

    st.subheader("📊 统计中心（按年/月/日期区间）")

    df = enrich_records(st.session_state.records)

    if df.empty:
        st.info("暂无数据可统计，请先在左侧录入。")
    else:
        # --- 1) 筛选器 ---
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
                date_range = st.date_input("选择日期区间", value=(min_d, max_d))
            if isinstance(date_range, tuple) and len(date_range) == 2:
                start_d, end_d = date_range
            else:
                start_d = end_d = date_range
            fdf = df[(df["日期"].dt.date >= start_d) & (df["日期"].dt.date <= end_d)]

        fdf = fdf[fdf["类型"].isin(type_filter)]
        st.caption(f"当前筛选后记录数：{len(fdf)}")

        if fdf.empty:
            st.warning("筛选后没有记录，请调整条件。")
        else:
            # --- 2) 总览指标 ---
            income_sum = fdf[fdf["类型"] == "收入"]["金额"].sum()
            expense_sum = fdf[fdf["类型"] == "支出"]["金额"].sum()
            net_sum = income_sum - expense_sum

            s1, s2, s3 = st.columns(3)
            s1.metric("筛选区间收入合计", f"¥ {income_sum:,.2f}")
            s2.metric("筛选区间支出合计", f"¥ {expense_sum:,.2f}")
            s3.metric("筛选区间净额(收入-支出)", f"¥ {net_sum:,.2f}")

            st.divider()

            # --- 3) 维度汇总（细分分支）---
            dim_col1, dim_col2 = st.columns([1.5, 2.5])
            with dim_col1:
                group_dim = st.selectbox(
                    "选择统计维度（细分分支）",
                    ["类别", "账本", "类型", "项目", "年月", "年份", "月份"],
                    index=0
                )
            with dim_col2:
                sort_desc = st.checkbox("按金额从高到低排序", value=True)

            summary = (
                fdf.groupby(group_dim, as_index=False)["金额"]
                   .sum()
                   .rename(columns={"金额": "总额"})
            )
            summary["总额"] = summary["总额"].round(2)
            summary = summary.sort_values("总额", ascending=not sort_desc)

            st.write("### ✅ 分支汇总（可按维度切换）")
            st.dataframe(summary, use_container_width=True)

            # --- 4) 透视统计 ---
            st.write("### 🧩 透视统计（行/列自由组合）")
            pcol1, pcol2, pcol3 = st.columns([1.2, 1.2, 1.6])
            with pcol1:
                row_dim = st.selectbox("行维度", ["类别", "账本", "类型", "项目", "年月", "年份", "月份"], index=0, key="row_dim")
            with pcol2:
                col_dim = st.selectbox("列维度", ["类型", "账本", "类别", "年月", "年份", "月份"], index=0, key="col_dim")
            with pcol3:
                show_total = st.checkbox("显示行列合计", value=True)

            pivot = pd.pivot_table(
                fdf,
                index=row_dim,
                columns=col_dim,
                values="金额",
                aggfunc="sum",
                fill_value=0
            )
            if show_total:
                pivot["行合计"] = pivot.sum(axis=1)
                pivot.loc["列合计"] = pivot.sum(axis=0)

            st.dataframe(pivot.round(2), use_container_width=True)

            st.divider()

            # --- 5) 图表：趋势折线（按月）---
            st.write("### 📈 趋势（按月汇总）")
            mdf = (
                fdf.groupby(["年月", "类型"], as_index=False)["金额"]
                   .sum()
                   .sort_values("年月")
            )
            # 变成宽表方便画图
            mwide = mdf.pivot_table(index="年月", columns="类型", values="金额", aggfunc="sum", fill_value=0).reset_index()

            # 给折线图用：设置年月为索引
            mwide_chart = mwide.set_index("年月")
            st.line_chart(mwide_chart)

            # --- 6) 图表：类别占比（支出/收入可切换）---
            st.write("### 🧁 类别占比（饼图/条形图）")
            chart_type = st.radio("选择占比类型", ["支出占比", "收入占比"], horizontal=True)
            target_type = "支出" if chart_type == "支出占比" else "收入"
            cdf = fdf[fdf["类型"] == target_type]

            if cdf.empty:
                st.info(f"当前筛选条件下没有{target_type}记录。")
            else:
                cat_sum = cdf.groupby("类别", as_index=False)["金额"].sum().sort_values("金额", ascending=False)
                cat_sum = cat_sum.rename(columns={"金额": "总额"}).set_index("类别")
                st.bar_chart(cat_sum)

            st.divider()

            # --- 7) 预算 vs 实际（按年月 + 类别）---
            st.write("### 🎯 预算 vs 实际（可选）")

            left, right = st.columns([1.2, 2.8])

            with left:
                st.markdown("**录入/更新预算**")
                # 预算录入：建议以“支出”为主，但也支持收入预算
                all_ym = sorted(df["年月"].unique().tolist())
                bud_ym = st.selectbox("预算年月", all_ym, index=len(all_ym)-1 if all_ym else 0)
                # 类别给出已有类别 + 手动输入
                all_cats = sorted(df["类别"].unique().tolist())
                bud_cat = st.selectbox("预算类别", all_cats) if all_cats else st.text_input("预算类别")
                bud_type = st.selectbox("预算类型", ["支出", "收入"], index=0)
                bud_amt_str = st.text_input("预算金额", placeholder="例如 2000")

                if st.button("✅ 保存预算"):
                    try:
                        bud_amt = parse_amount(bud_amt_str)
                        new_row = pd.DataFrame([{
                            "年月": bud_ym,
                            "类别": bud_cat,
                            "类型": bud_type,
                            "预算金额": float(bud_amt)
                        }])

                        bud_df = st.session_state.budgets.copy()
                        # 若已存在则覆盖
                        mask = (bud_df["年月"] == bud_ym) & (bud_df["类别"] == bud_cat) & (bud_df["类型"] == bud_type)
                        bud_df = bud_df[~mask]
                        bud_df = pd.concat([bud_df, new_row], ignore_index=True)
                        st.session_state.budgets = bud_df

                        st.success(f"已保存预算：{bud_ym} / {bud_cat} / {bud_type} = ¥{bud_amt:,.2f}")
                    except Exception:
                        st.error("预算金额输入有误")

            with right:
                st.markdown("**对比视图**")
                # 对比范围：用当前筛选 fdf 的年月做基础
                view_ym_list = sorted(fdf["年月"].unique().tolist())
                view_ym = st.multiselect("选择查看的年月", view_ym_list, default=view_ym_list[-1:] if view_ym_list else [])

                if not view_ym:
                    st.info("请选择至少一个年月查看预算对比。")
                else:
                    actual = (
                        fdf[fdf["年月"].isin(view_ym)]
                        .groupby(["年月", "类别", "类型"], as_index=False)["金额"]
                        .sum()
                        .rename(columns={"金额": "实际金额"})
                    )

                    bud = st.session_state.budgets.copy()
                    if not bud.empty:
                        bud = bud[bud["年月"].isin(view_ym)]
                    # 合并
                    comp = pd.merge(
                        actual,
                        bud,
                        on=["年月", "类别", "类型"],
                        how="left"
                    )
                    comp["预算金额"] = comp["预算金额"].fillna(0.0)
                    comp["差额(实际-预算)"] = (comp["实际金额"] - comp["预算金额"]).round(2)

                    # 展示
                    st.dataframe(comp.sort_values(["年月", "类型", "实际金额"], ascending=[True, True, False]), use_container_width=True)

                    # 图：按年月汇总预算/实际（分别对收入/支出）
                    st.markdown("**按年月汇总预算 vs 实际（收入/支出分开）**")
                    agg = comp.groupby(["年月", "类型"], as_index=False)[["实际金额", "预算金额"]].sum()

                    # 拆成两个表，各画一个条形图（Streamlit 简单）
                    for tt in ["支出", "收入"]:
                        sub = agg[agg["类型"] == tt].copy()
                        if sub.empty:
                            continue
                        sub = sub.set_index("年月")[["实际金额", "预算金额"]]
                        st.write(f"**{tt}：实际 vs 预算**")
                        st.bar_chart(sub)

            st.divider()

            # --- 8) 导出 ---
            st.write("### ⬇️ 导出")
            dl1, dl2, dl3 = st.columns(3)
            with dl1:
                st.download_button(
                    "下载：筛选后的明细 CSV",
                    data=fdf.to_csv(index=False).encode("utf-8-sig"),
                    file_name="records_filtered.csv",
                    mime="text/csv"
                )
            with dl2:
                st.download_button(
                    "下载：当前维度汇总 CSV",
                    data=summary.to_csv(index=False).encode("utf-8-sig"),
                    file_name="summary.csv",
                    mime="text/csv"
                )
            with dl3:
                st.download_button(
                    "下载：预算表 CSV",
                    data=st.session_state.budgets.to_csv(index=False).encode("utf-8-sig"),
                    file_name="budgets.csv",
                    mime="text/csv"
                )


# --- 5. 设置 ---
with st.expander("⚙️ 账户配置"):
    st.session_state.init_balance = st.number_input("1. 设置起始资金", value=st.session_state.init_balance)
    if st.button("🚨 清空所有记录"):
        st.session_state.records = pd.DataFrame(columns=["ID", "日期", "账本", "类别", "项目", "金额", "类型"])
        st.rerun()
