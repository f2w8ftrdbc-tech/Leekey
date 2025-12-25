import streamlit as st
import pandas as pd
from datetime import datetime
import streamlit.components.v1 as components

# --- 1. 页面基础配置 ---
st.set_page_config(page_title="私人理财中心", layout="wide", initial_sidebar_state="expanded")

# --- 2. 数据初始化 (持久化模拟) ---
if 'records' not in st.session_state:
    st.session_state.records = pd.DataFrame(columns=["日期", "账本", "类别", "项目", "金额", "类型"])
if 'init_balance' not in st.session_state:
    st.session_state.init_balance = 0.0

# --- 3. 侧边栏：功能录入 ---
st.sidebar.header("📝 记账录入")
with st.sidebar.form("entry_form", clear_on_submit=True):
    d = st.date_input("日期", datetime.now())
    b = st.selectbox("归属账本", ["生活主账", "车子专项", "学费/购汇"])
    t = st.selectbox("收支类型", ["支出", "收入"])
    # 这里严格对应你截图的类别
    c = st.selectbox("分类", ["Eat outside", "Shopping", "Bill", "Petrol", "Insurance", "Others", "理财收益"])
    item = st.text_input("备注 (如: Linkt, 毕业餐)")
    amt_input = st.text_input("金额 (支持计算 10+5)", value="0")
    
    if st.form_submit_button("确认存入"):
        try:
            amt = float(eval(amt_input))
            new_row = pd.DataFrame([{"日期": d, "账本": b, "类别": c, "项目": item, "金额": amt, "类型": t}])
            st.session_state.records = pd.concat([st.session_state.records, new_row], ignore_index=True)
            st.success("已记录！")
        except:
            st.error("金额格式不对哦")

# --- 4. 主界面布局 ---
st.title("💰 我的财务一体化看板")

# 计算数据
total_in = st.session_state.records[st.session_state.records['类型'] == "收入"]['金额'].sum()
total_out = st.session_state.records[st.session_state.records['类型'] == "支出"]['金额'].sum()
current_balance = st.session_state.init_balance + total_in - total_out

# 顶部结余汇总
col1, col2, col3 = st.columns(3)
col1.metric("账户总结余", f"¥ {current_balance:,.2f}")
col2.metric("累计收入", f"¥ {total_in:,.2f}")
col3.metric("累计支出", f"¥ {total_out:,.2f}", delta=f"-{total_out:,.2f}", delta_color="inverse")

# --- 5. 功能选项卡 ---
tab1, tab2, tab3 = st.tabs(["📊 支出分析", "📋 明细账单", "📈 理财实时追踪"])

with tab1:
    st.subheader("按类别汇总统计")
    if not st.session_state.records.empty:
        # 自动计算每个分类的总花费
        summary = st.session_state.records[st.session_state.records['类型']=="支出"].groupby('类别')['金额'].sum().reset_index()
        st.bar_chart(summary.set_index('类别'))
        st.table(summary.style.format({"金额": "{:.2f}"}))
    else:
        st.info("尚无支出记录")

with tab2:
    st.subheader("历史明细表")
    book_filter = st.multiselect("筛选账本", options=["生活主账", "车子专项", "学费/购汇"], default=["生活主账", "车子专项"])
    filtered_df = st.session_state.records[st.session_state.records['账本'].isin(book_filter)]
    st.dataframe(filtered_df.sort_values("日期", ascending=False), use_container_width=True)

with tab3:
    st.subheader("养基宝实时持仓")
    st.write("🔗 正在同步外部理财功能...")
    # 直接内嵌你提供的链接
    components.iframe("https://wx.yangjibao.com/app/hold", height=600, scrolling=True)

# --- 6. 初始金额设置 (隐藏在底部) ---
with st.expander("⚙️ 账户初始化设置"):
    new_init = st.number_input("设置起始资金 (如银行卡现有余额)", value=st.session_state.init_balance)
    if st.button("更新初始余额"):
        st.session_state.init_balance = new_init
        st.rerun()
        st.warning("正在调用识图接口... (演示模式：自动提取代码 NVDA, 份额 10)")
        if st.button("确认入库"):
            new_asset = {"代码": "NVDA", "份额": 10.0, "成本": 120.0}
            st.session_state.portfolio = pd.concat([st.session_state.portfolio, pd.DataFrame([new_asset])], ignore_index=True)
