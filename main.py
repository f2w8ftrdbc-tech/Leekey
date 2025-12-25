import streamlit as st
import pandas as pd
from datetime import datetime

# --- 1. 初始化 ---
st.set_page_config(page_title="私人理财中心", layout="wide")

if 'records' not in st.session_state:
    st.session_state.records = pd.DataFrame(columns=["日期", "账本", "类别", "项目", "金额", "类型"])
if 'init_balance' not in st.session_state:
    st.session_state.init_balance = 0.0

# --- 2. 侧边栏：修复逻辑的核心 ---
st.sidebar.header("📝 记账录入")
with st.sidebar.form("entry_form", clear_on_submit=True):
    d = st.date_input("日期", datetime.now())
    b = st.selectbox("归属账本", ["生活主账", "车子专项", "学费/购汇", "理财账本"])
    
    # 获取收支类型
    type_choice = st.selectbox("收支类型", ["支出", "收入"])
    
    # 根据类型动态显示分类
    if type_choice == "支出":
        category_options = ["Eat outside", "Shopping", "Bill", "Petrol", "Insurance", "Rent", "其他"]
    else:
        # 严格按照你的要求：工资、业余项目、亲情赠与
        category_options = ["工资", "业余项目", "亲情赠与", "理财收益", "其他"]
    
    c_base = st.selectbox("分类", category_options)
    custom_c = st.text_input("如选'其他'，请手动输入名称")
    
    item = st.text_input("备注项目")
    amt_input = st.text_input("金额 (支持计算 50+10)", value="0")
    
    if st.form_submit_button("确认存入"):
        try:
            # 1. 确定分类名称
            final_cat = custom_c if (c_base == "其他" and custom_c != "") else c_base
            # 2. 计算金额
            amt = float(eval(amt_input))
            
            # 3. 构造新数据 (确保 类型 字段被正确写入)
            new_data = {
                "日期": d,
                "账本": b,
                "类别": final_cat,
                "项目": item,
                "金额": amt,
                "类型": type_choice # 关键：这里直接保存选择的类型
            }
            
            st.session_state.records = pd.concat([st.session_state.records, pd.DataFrame([new_data])], ignore_index=True)
            st.sidebar.success(f"已存入一笔{type_choice}：{final_cat}")
        except Exception as e:
            st.sidebar.error(f"输入错误: {e}")

# --- 3. 数据看板 ---
st.title("💰 我的财务一体化看板")

# 分开汇总收入和支出
actual_inc = st.session_state.records[st.session_state.records['类型'] == "收入"]['金额'].sum()
actual_exp = st.session_state.records[st.session_state.records['类型'] == "支出"]['金额'].sum()
total_balance = st.session_state.init_balance + actual_inc - actual_exp

col1, col2, col3 = st.columns(3)
col1.metric("账户总结余", f"¥ {total_balance:,.2f}")
col2.metric("累计总收入", f"¥ {actual_inc:,.2f}")
col3.metric("累计总支出", f"¥ {actual_exp:,.2f}", delta=f"-{actual_exp:,.2f}")

# --- 4. 展示表格 ---
tab1, tab2 = st.tabs(["📋 历史明细", "📊 分类统计"])
with tab1:
    st.dataframe(st.session_state.records.sort_values("日期", ascending=False), use_container_width=True)
with tab2:
    if not st.session_state.records.empty:
        st.write("支出分布")
        exp_only = st.session_state.records[st.session_state.records['类型'] == "支出"]
        if not exp_only.empty:
            st.bar_chart(exp_only.groupby('类别')['金额'].sum())
