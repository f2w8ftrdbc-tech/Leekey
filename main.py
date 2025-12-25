import streamlit as st
import pandas as pd
from datetime import datetime

# --- 1. 初始化 ---
st.set_page_config(page_title="私人理财中心", layout="wide")

if 'records' not in st.session_state:
    st.session_state.records = pd.DataFrame(columns=["ID", "日期", "账本", "类别", "项目", "金额", "类型"])
if 'init_balance' not in st.session_state:
    st.session_state.init_balance = 0.0

# --- 2. 侧边栏：实时联动逻辑 ---
st.sidebar.header("📝 记账录入")

# 这里我们将类型选择移出 Form，或者利用 key 实现实时监听
t_type = st.sidebar.selectbox("1. 选择收支类型", ["支出", "收入"], key="type_selector")

# 根据上面的选择，立即定义分类选项
if t_type == "支出":
    cat_opts = ["Eat outside", "Shopping", "Bill", "Petrol", "Insurance", "Rent", "其他"]
else:
    # 严格匹配你的要求
    cat_opts = ["工资", "业余项目", "亲情赠与", "理财收益", "其他"]

# 使用 Form 包裹剩下的输入框，提高输入效率
with st.sidebar.form("record_form", clear_on_submit=True):
    d = st.date_input("2. 日期", datetime.now())
    b = st.selectbox("3. 归属账本", ["生活主账", "车子专项", "学费/购汇", "理财账本"])
    
    # 这里的分类会随 t_type 实时变化
    c_base = st.selectbox("4. 选择分类", cat_opts)
    custom_c = st.text_input("如选'其他'，请手动输入名称")
    
    item = st.text_input("5. 备注项目")
    
    # 解决 0 的问题：使用空字符串作为默认值，提示符设为 0
    amt_input = st.text_input("6. 金额 (直接输入)", value="", placeholder="0")
    
    submit = st.form_submit_button("确认存入账本")
    
    if submit:
        try:
            # 逻辑处理
            final_amt_str = amt_input if amt_input.strip() != "" else "0"
            amt = float(eval(final_amt_str))
            final_cat = custom_c if (c_base == "其他" and custom_c != "") else c_base
            
            # 写入记录
            new_id = len(st.session_state.records) + 1
            new_row = {
                "ID": new_id, "日期": d, "账本": b, "类别": final_cat, 
                "项目": item, "金额": amt, "类型": t_type
            }
            st.session_state.records = pd.concat([st.session_state.records, pd.DataFrame([new_row])], ignore_index=True)
            st.sidebar.success(f"✅ 已记录{t_type}：{final_cat}")
        except:
            st.sidebar.error("金额输入有误")

# --- 3. 汇总看板 ---
st.title("💰 我的财务一体化看板")
inc = st.session_state.records[st.session_state.records['类型'] == "收入"]['金额'].sum()
exp = st.session_state.records[st.session_state.records['类型'] == "支出"]['金额'].sum()
bal = st.session_state.init_balance + inc - exp

c1, c2, c3 = st.columns(3)
c1.metric("目前总结余", f"¥ {bal:,.2f}")
c2.metric("累计总收入", f"¥ {inc:,.2f}")
c3.metric("累计总支出", f"¥ {exp:,.2f}", delta=f"-{exp:,.2f}")

# --- 4. 历史记录与删除 ---
tab1, tab2 = st.tabs(["📋 历史明细与删除", "📈 理财中心"])
with tab1:
    if not st.session_state.records.empty:
        st.dataframe(st.session_state.records.sort_values("ID", ascending=False), use_container_width=True)
        st.divider()
        st.write("🗑️ **删除错误记录**")
        target_id = st.selectbox("选择要删除的记录 ID", options=st.session_state.records["ID"].tolist())
        if st.button("🔴 确认删除该记录"):
            st.session_state.records = st.session_state.records[st.session_state.records["ID"] != target_id]
            st.rerun()
    else:
        st.info("尚无记录，请在左侧录入")

with tab2:
    st.link_button("🚀 前往养基宝查看实时持仓", "https://wx.yangjibao.com/app/hold")

# --- 5. 设置 ---
with st.expander("⚙️ 账户配置"):
    st.session_state.init_balance = st.number_input("1. 设置起始资金", value=st.session_state.init_balance)
    if st.button("🚨 清空所有记录"):
        st.session_state.records = pd.DataFrame(columns=["ID", "日期", "账本", "类别", "项目", "金额", "类型"])
        st.rerun()
