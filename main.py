import streamlit as st
import pandas as pd
from datetime import datetime
import streamlit.components.v1 as components

# --- 1. 页面基础配置 ---
st.set_page_config(page_title="私人理财中心", layout="wide", initial_sidebar_state="expanded")

# --- 2. 数据初始化 ---
if 'records' not in st.session_state:
    st.session_state.records = pd.DataFrame(columns=["日期", "账本", "类别", "项目", "金额", "类型"])
if 'init_balance' not in st.session_state:
    st.session_state.init_balance = 0.0

# --- 3. 侧边栏：智能记账录入 ---
st.sidebar.header("📝 记账录入")
with st.sidebar.form("entry_form", clear_on_submit=True):
    d = st.date_input("日期", datetime.now())
    b = st.selectbox("归属账本", ["生活主账", "车子专项", "学费/购汇"])
    t = st.selectbox("收支类型", ["支出", "收入"])
    
    # 动态分类逻辑
    if t == "支出":
        category_options = ["Eat outside", "Shopping", "Bill", "Petrol", "Insurance", "Rent", "其他"]
    else:
        category_options = ["工资薪水", "理财收益", "报销返现", "二手转卖", "其他"]
    
    c_base = st.selectbox("分类", category_options)
    
    # “其他”选项的自定义输入
    custom_c = st.text_input("如果是'其他'，请在此输入新分类名称", placeholder="例如：宠物、医疗...")
    
    item = st.text_input("备注 (如: Linkt, 毕业餐)")
    amt_input = st.text_input("金额 (支持计算 10+5)", value="0")
    
    if st.form_submit_button("确认存入"):
        try:
            # 确定最终分类名称
            final_category = custom_c if (c_base == "其他" and custom_c != "") else c_base
            amt = float(eval(amt_input))
            
            new_row = pd.DataFrame([{"日期": d, "账本": b, "类别": final_category, "项目": item, "金额": amt, "类型": t}])
            st.session_state.records = pd.concat([st.session_state.records, new_row], ignore_index=True)
            st.success(f"已记录到 [{final_category}]")
        except:
            st.error("金额格式不对哦")

# --- 4. 主界面：一体化看板 ---
st.title("💰 我的财务一体化看板")

# 统计核心数据
total_in = st.session_state.records[st.session_state.records['类型'] == "收入"]['金额'].sum()
total_out = st.session_state.records[st.session_state.records['类型'] == "支出"]['金额'].sum()
current_balance = st.session_state.init_balance + total_in - total_out

# 顶部结余显示
col1, col2, col3 = st.columns(3)
col1.metric("目前总结余", f"¥ {current_balance:,.2f}")
col2.metric("累计收入", f"¥ {total_in:,.2f}")
col3.metric("累计支出", f"¥ {total_out:,.2f}", delta=f"-{total_out:,.2f}", delta_color="inverse")

# --- 5. 功能标签页 ---
tab1, tab2, tab3 = st.tabs(["📊 支出分析", "📋 明细账单", "📈 理财实时追踪"])

with tab1:
    st.subheader("支出构成分析")
    exp_df = st.session_state.records[st.session_state.records['类型'] == "支出"]
    if not exp_df.empty:
        summary = exp_df.groupby('类别')['金额'].sum().reset_index()
        st.bar_chart(summary.set_index('类别'))
        st.table(summary.style.format({"金额": "{:.2f}"}))
    else:
        st.info("尚无支出记录")

with tab2:
    st.subheader("历史明细表")
    book_filter = st.multiselect("筛选账本", options=["生活主账", "车子专项", "学费/购汇"], default=["生活主账", "车子专项"])
    res_df = st.session_state.records[st.session_state.records['账本'].isin(book_filter)]
    st.dataframe(res_df.sort_values("日期", ascending=False), use_container_width=True)

with tab3:
    st.subheader("理财持仓同步")
    st.info("🔗 正在连接养基宝/外部理财链接...")
    # 集成你提供的理财链接
    components.iframe("https://wx.yangjibao.com/app/hold", height=800, scrolling=True)

# --- 6. 系统设置 ---
with st.expander("⚙️ 账户初始化及管理"):
    new_init = st.number_input("更新起始资金", value=st.session_state.init_balance)
    if st.button("更新初始余额"):
        st.session_state.init_balance = new_init
        st.rerun()
    
    if st.button("🗑️ 清空所有本地记录 (慎点)"):
        st.session_state.records = pd.DataFrame(columns=["日期", "账本", "类别", "项目", "金额", "类型"])
        st.rerun()
        st.warning("正在调用识图接口... (演示模式：自动提取代码 NVDA, 份额 10)")
        if st.button("确认入库"):
            new_asset = {"代码": "NVDA", "份额": 10.0, "成本": 120.0}
            st.session_state.portfolio = pd.concat([st.session_state.portfolio, pd.DataFrame([new_asset])], ignore_index=True)
