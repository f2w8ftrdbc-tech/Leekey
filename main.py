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
# 1) Local persistence (won't lose after restart)
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
    st.session_state.records.to_csv(RECORDS_PATH, index=False, encoding="utf-8-sig")
    st.session_state.budgets.to_csv(BUDGETS_PATH, index=False, encoding="utf-8-sig")
    CONFIG_PATH.write_text(
        json.dumps({"init_balance": st.session_state.init_balance}, ensure_ascii=False),
        encoding="utf-8"
    )


# =========================
# 2) Session state init
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


def parse_memo_text_to_df(text: str) -> pd.DataFrame:
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

        nums = re.findall(r"[-+]?\d[\d,]*\.?\d*", line)
        if not nums:
            continue
        amt = parse_amount(nums[-1])

        t = normalize_type(line)
        if t == "":
            t = "支出" if "-" in nums[-1] else "收入"

        book = "生活主账"
        for b in ["生活主账", "车子专项", "学费/购汇", "理财账本"]:
            if b in line:
                book = b
                break

        cat = "其他"
        exp_cats = ["Eat outside", "Shopping", "Bill", "Petrol", "Insurance", "Rent"]
        inc_cats = ["工资", "业余项目", "亲情赠与", "理财收益"]
        for c in exp_cats + inc_cats:
            if c in line:
                cat = c
                break

        # note
        tmp = re.sub(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", "", line).strip()
        tmp = re.sub(r"[-+]?\d[\d,]*\.?\d*", "", tmp).strip()
        item = re.sub(r"\s+", " ", tmp)

        rows.append({
            "日期": d,
            "账本": book,
            "类别": cat,
            "项目": item,
            "金额": float(abs(amt)),
            "类型": t
        })
    return pd.DataFrame(rows)


def next_id() -> int:
    df = st.session_state.records
    if df.empty:
        return 1
    try:
        return int(pd.to_numeric(df["ID"], errors="coerce").max()) + 1
    except Exception:
        return len(df) + 1


# =========================
# 4) Sidebar input
# =========================
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
            st.session_state.records = pd.concat(
                [st.session_state.records, pd.DataFrame([new_row])],
                ignore_index=True
            )
            persist_all()
            st.sidebar.success(f"✅ 已记录 {t_type}：{final_cat} ¥{amt:,.2f}")
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
tab1, tab2 = st.tabs(["📋 明细（行内修改/删除）", "📈 理财中心（统计/导入/预算）"])

# -------------------------
# Tab1: inline edit & delete
# -------------------------
with tab1:
    st.subheader("📋 历史明细（直接改、直接删）")

    if st.session_state.records.empty:
        st.info("尚无记录，请在左侧录入或在「理财中心」导入。")
    else:
        # show latest first
        base = st.session_state.records.copy()
        base["日期"] = pd.to_datetime(base["日期"], errors="coerce")
        base = base.sort_values(["日期", "ID"], ascending=[False, False]).reset_index(drop=True)

        # add delete checkbox column
        if "🗑 删除" not in base.columns:
            base.insert(0, "🗑 删除", False)

        # optional quick filters
        f1, f2, f3, f4 = st.columns([1.2, 1.2, 1.2, 2.0])
        with f1:
            type_filter = st.multiselect("类型筛选", ["收入", "支出"], default=["收入", "支出"])
        with f2:
            book_filter = st.multiselect("账本筛选", sorted(base["账本"].dropna().unique().tolist()))
        with f3:
            cat_filter = st.multiselect("类别筛选", sorted(base["类别"].dropna().unique().tolist()))
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

        st.caption(f"当前显示：{len(view)} 条（勾选「🗑 删除」后点击下方按钮即可删除；修改单元格后点击保存即可落盘）")

        # Editable grid
        edited = st.data_editor(
            view,
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            column_config={
                "🗑 删除": st.column_config.CheckboxColumn("🗑 删除", help="勾选后会被删除"),
                "ID": st.column_config.NumberColumn("ID", disabled=True),
                "日期": st.column_config.DateColumn("日期"),
                "金额": st.column_config.NumberColumn("金额", format="%.2f"),
                "类型": st.column_config.SelectboxColumn("类型", options=["收入", "支出"]),
                "账本": st.column_config.SelectboxColumn("账本", options=["生活主账", "车子专项", "学费/购汇", "理财账本"]),
                # 类别可以自由编辑；你也可以改成 SelectboxColumn 并提供固定选项
                "类别": st.column_config.TextColumn("类别"),
                "项目": st.column_config.TextColumn("项目"),
            },
            key="editor_records",
        )

        colA, colB, colC = st.columns([1.3, 1.3, 2.4])

        with colA:
            if st.button("💾 保存修改", type="primary"):
                try:
                    # apply changes back by ID
                    edited2 = edited.copy()
                    # remove helper derived columns if present
                    for c in ["年份", "月份", "年月"]:
                        if c in edited2.columns:
                            edited2 = edited2.drop(columns=[c])

                    # rebuild full table: take original, update rows that appear in edited view
                    full = st.session_state.records.copy()
                    full["ID"] = pd.to_numeric(full["ID"], errors="coerce").astype(int)
                    edited2["ID"] = pd.to_numeric(edited2["ID"], errors="coerce").astype(int)

                    # update non-deleted rows
                    # (we do NOT delete here; deletion is separate button)
                    upd_cols = ["日期", "账本", "类别", "项目", "金额", "类型"]
                    for _, row in edited2.iterrows():
                        rid = int(row["ID"])
                        for col in upd_cols:
                            full.loc[full["ID"] == rid, col] = row[col]

                    st.session_state.records = full[RECORD_COLS]
                    persist_all()
                    st.success("✅ 已保存修改（并写入 data/records.csv）")
                    st.rerun()
                except Exception as e:
                    st.error(f"保存失败：{e}")

        with colB:
            if st.button("🗑 执行删除（删勾选行）"):
                try:
                    del_ids = edited.loc[edited["🗑 删除"] == True, "ID"].tolist()
                    del_ids = [int(x) for x in del_ids]
                    if not del_ids:
                        st.info("你还没有勾选任何要删除的记录。")
                    else:
                        full = st.session_state.records.copy()
                        full["ID"] = pd.to_numeric(full["ID"], errors="coerce").astype(int)
                        full = full[~full["ID"].isin(del_ids)].copy()
                        st.session_state.records = full[RECORD_COLS]
                        persist_all()
                        st.success(f"✅ 已删除 {len(del_ids)} 条记录")
                        st.rerun()
                except Exception as e:
                    st.error(f"删除失败：{e}")

        with colC:
            st.download_button(
                "⬇️ 下载当前备份（records_backup.csv）",
                data=st.session_state.records.to_csv(index=False).encode("utf-8-sig"),
                file_name="records_backup.csv",
                mime="text/csv"
            )
            st.caption("自动保存位置：data/records.csv（你改代码/重启后会自动加载）")


# -------------------------
# Tab2: finance center (import + stats + budget)
# -------------------------
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
                        tmp["金额"] = tmp["金额"].abs()
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

                    start = next_id()
                    tmp.insert(0, "ID", range(start, start + len(tmp)))
                    tmp = tmp[RECORD_COLS]

                    st.session_state.records = pd.concat([st.session_state.records, tmp], ignore_index=True)
                    persist_all()
                    st.success(f"✅ 已导入 {len(tmp)} 条（已自动保存到 data/records.csv）")
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
                start = next_id()
                df_m.insert(0, "ID", range(start, start + len(df_m)))
                df_m = df_m[RECORD_COLS]

                st.session_state.records = pd.concat([st.session_state.records, df_m], ignore_index=True)
                persist_all()
                st.success(f"✅ 已导入 {len(df_m)} 条（已自动保存到 data/records.csv）")
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

    st.subheader("📊 统计中心（按年/月/日期区间）")
    df = enrich_records(st.session_state.records)
    if df.empty:
        st.info("暂无数据可统计。")
    else:
        colA, colB, colC = st.columns([1.2, 1.2, 2.0])
        with colA:
            mode = st.radio("统计口径", ["年份", "月份", "自定义区间"], horizontal=True)
        with colB:
            type_filter = st.multiselect("收支类型筛选", ["收入", "支出"], default=["收入", "支出"], key="stat_type_filter")

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

    st.subheader("🎯 预算（可选）")
    left, right = st.columns([1.2, 2.8])
    with left:
        df_now = enrich_records(st.session_state.records)
        all_ym = sorted(df_now["年月"].unique().tolist()) if not df_now.empty else ["2025-12"]
        all_cats = sorted(df_now["类别"].dropna().unique().tolist()) if not df_now.empty else ["其他"]

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
                "年月": bud_ym, "类别": bud_cat, "类型": bud_type, "预算金额": float(bud_amt)
            }])], ignore_index=True)
            st.session_state.budgets = bud_df
            persist_all()
            st.success("已保存预算。")

    with right:
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


# =========================
# 7) Settings
# =========================
with st.expander("⚙️ 账户配置"):
    new_init = st.number_input("设置起始资金", value=float(st.session_state.init_balance))
    if new_init != st.session_state.init_balance:
        st.session_state.init_balance = float(new_init)
        persist_all()
        st.success("起始资金已保存。")

    st.download_button(
        "⬇️ 下载 records 备份（records_backup.csv）",
        data=st.session_state.records.to_csv(index=False).encode("utf-8-sig"),
        file_name="records_backup.csv",
        mime="text/csv"
    )

    if st.button("🚨 清空所有记录（不可逆）"):
        st.session_state.records = pd.DataFrame(columns=RECORD_COLS)
        persist_all()
        st.rerun()
