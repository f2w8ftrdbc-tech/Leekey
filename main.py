import streamlit as st
import pandas as pd
from datetime import datetime

# --- 1. 初始化 ---
st.set_page_config(page_title="私人理财中心", layout="wide")

if 'records' not in st.session_state:
    # 增加一个索引 ID 方便删除
    st.session_state.records = pd.DataFrame(columns=["ID", "日期", "账本", "类别", "项目", "金额", "类型"])
if 'init_balance' not in st.session_state:
    st.session_state.init_balance = 0.0

# --- 2. 侧边栏：智能记账 ---
st.sidebar.header("📝 记账录入")
with st.sidebar.form("entry_form", clear_on_submit=True):
    d = st.date_input("日期", datetime.now())
    b = st.selectbox("归属账本", ["生活主账", "车子专项", "学费/购汇", "理财账本"])
    t_type = st.selectbox("收支类型", ["支出", "收入"])
    
    # 动态切换分类
    if t_type == "支出":
        cat_opts = ["Eat outside", "Shopping", "Bill", "Petrol", "Insurance", "Rent", "其他"]
    else:
        cat_opts = ["工资", "业余项目", "亲情赠与", "理财收益", "其他"]
    
    c_base = st.selectbox("分类", cat_opts)
    custom_c = st.text_input("如选'其他'，请在此输入名称")
    
    item = st.text_input("备注项目")
    amt_input = st.text_input("金额 (支持计算 50+10)", value="0")
    
    if st.form_submit_button("确认存入"):
        try:
            final_cat = custom_c if (c_base == "其他" and custom_c != "") else c_base
            amt = float(eval(amt_input))
            
            # 生成新记录，并自动分配一个 ID
            new_id = len(st.session_state.records) + 1
            new_row = {
                "ID": new_id,
                "日期": d,
                "账本": b,
                "类别": final_cat,
                "项目": item,
                "金额": amt,
                "类型": t_type
            }
            
            st.session_state.records = pd.concat([st.session_state.records, pd.DataFrame([new_row])], ignore_index=True)
            st.sidebar.success(f"已存入：{final_cat} ({t_type})")
        except:
            st.sidebar.error("输入有误，请检查金额格式")

# --- 3. 主界面看板 ---
st.title("💰 我的财务一体化看板")

# 计算数据
actual_inc = st.session_state.records[st.session_state.records['类型'] == "收入"]['金额'].sum()
actual_exp = st.session_state.records[st.session_state.records['类型'] == "支出"]['金额'].sum()
total_balance = st.session_state.init_balance + actual_inc - actual_exp

c1, c2, c3 = st.columns(3)
c1.metric("目前总结余", f"¥ {total_balance:,.2f}")
c2.metric("累计总收入", f"¥ {actual_inc:,.2f}")
c3.metric("累计总支出", f"¥ {actual_exp:,.2f}", delta=f"-{actual_exp:,.2f}")

# --- 4. 历史记录与删除功能 ---
tab1, tab2 = st.tabs(["📋 历史明细与删除", "📈 理财链接"])

with tab1:
    st.subheader("账单明细")
    if not st.session_state.records.empty:
        # 显示表格
        df_display = st.session_state.records.sort_values("ID", ascending=False)
        st.dataframe(df_display, use_container_width=True)
        
        # --- 删除逻辑区块 ---
        st.divider()
        st.write("🗑️ **删除错误记录**")
        del_col1, del_col2 = st.columns([1, 2])
        with del_col1:
            # 让用户选择要删除的 ID
            target_id = st.selectbox("选择要删除的记录 ID", options=st.session_state.records["ID"].tolist())
        with del_col2:
            st.write("确认后不可撤销")
            if st.button("🔴 确认删除该条记录"):
                st.session_state.records = st.session_state.records[st.session_state.records["ID"] != target_id]
                st.success(f"ID {target_id} 已成功删除！")
                st.rerun()
    else:
        st.info("目前还没有记录，快去左侧录入吧！")

with tab2:
    st.link_button("🚀 前往养基宝查看实时持仓", "https://wx.yangjibao.com/app/hold")

# --- 5. 设置 ---
with st.expander("⚙️ 账户配置"):
    new_init = st.number_input("设置起始资金", value=st.session_state.init_balance)
    if st.button("更新起始资金"):
        st.session_state.init_balance = new_init
        st.rerun()
    
    if st.button("🚨 清空所有记录"):
        st.session_state.records = pd.DataFrame(columns=["ID", "日期", "账本", "类别", "项目", "金额", "类型"])
        st.rerun()
