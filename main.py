import streamlit as st
import pandas as pd
from datetime import datetime

# --- 1. 页面配置 ---
st.set_page_config(page_title="私人理财中心", layout="wide")

# --- 2. 数据初始化 ---
if 'records' not in st.session_state:
    st.session_state.records = pd.DataFrame(columns=["日期", "账本", "类别", "项目", "金额", "类型"])
if 'init_balance' not in st.session_state:
    st.session_state.init_balance = 0.0

# --- 3. 侧边栏：智能记账 ---
st.sidebar.header("📝 记账录入")
with st.sidebar.form("entry_form", clear_on_submit=True):
    d = st.date_input("日期", datetime.now())
    b = st.selectbox("归属账本", ["生活主账", "车子专项", "学费/购汇", "理财账本"])
    t = st.selectbox("收支类型", ["支出", "收入"])
    
    # --- 核心改进：根据收支类型切换分类 ---
    if t == "支出":
        category_options = ["Eat outside", "Shopping", "Bill", "Petrol", "Insurance", "Rent", "其他"]
    else:
        # 你的新要求：收入分类
        category_options = ["工资", "业余项目", "亲情赠与", "理财收益", "其他"]
    
    c_base = st.selectbox("分类", category_options)
    custom_c = st.text_input("如选'其他'，请在此输入具体分类", placeholder="例如：毕业礼金")
    
    item = st.text_input("备注 (如: Linkt, 兼职设计)")
    amt_input = st.text_input("金额 (支持计算如 50+12.5)", value="0")
    
    if st.form_submit_button("确认存入"):
        try:
            # 确定最终分类
            final_cat = custom_c if (c_base == "其他" and custom_c != "") else c_base
            amt = float(eval(amt_input))
            
            new_row = pd.DataFrame([{"日期": d, "账本": b, "类别": final_cat, "项目": item, "金额": amt, "类型": t}])
            st.session_state.records = pd.concat([st.session_state.records, new_row], ignore_index=True)
            st.sidebar.success(f"已存入 {final_cat}")
        except:
            st.sidebar.error("金额格式错误")

# --- 4. 主界面：结余看板 ---
st.title("💰 我的财务一体化看板")

# 综合计算
total_in = st.session_state.records[st.session_state.records['类型'] == "收入"]['金额'].sum()
total_out = st.session_state.records[st.session_state.records['类型'] == "支出"]['金额'].sum()
# 最后的总结余 = 初始资金 + 收入 - 支出
current_balance = st.session_state.init_balance + total_in - total_out

c1, c2, c3 = st.columns(3)
c1.metric("目前总结余", f"¥ {current_balance:,.2f}")
c2.metric("累计总收入", f"¥ {total_in:,.2f}")
c3.metric("累计总支出", f"¥ {total_out:,.2f}", delta=f"-{total_out:,.2f}")

# --- 5. 功能模块 ---
tab1, tab2, tab3 = st.tabs(["📊 分类分析", "📋 明细历史", "📈 理财中心"])

with tab1:
    st.subheader("收支构成分析")
    col_a, col_b = st.columns(2)
    
    with col_a:
        st.write("**支出占比**")
        exp_df = st.session_state.records[st.session_state.records['类型'] == "支出"]
        if not exp_df.empty:
            st.bar_chart(exp_df.groupby('类别')['金额'].sum())
        else: st.write("暂无支出")
            
    with col_b:
        st.write("**收入来源**")
        inc_df = st.session_state.records[st.session_state.records['类型'] == "收入"]
        if not inc_df.empty:
            st.bar_chart(inc_df.groupby('类别')['金额'].sum())
        else: st.write("暂无收入")

with tab2:
    st.subheader("所有明细")
    # 增加按账本筛选的功能
    filter_b = st.multiselect("查看账本", ["生活主账", "车子专项", "学费/购汇", "理财账本"], default=["生活主账", "车子专项"])
    show_df = st.session_state.records[st.session_state.records['账本'].isin(filter_b)]
    st.dataframe(show_df.sort_values("日期", ascending=False), use_container_width=True)

with tab3:
    st.subheader("理财与资产管理")
    st.write("由于养基宝网页限制了直接嵌入，建议点击下方链接跳转查看：")
    st.link_button("🚀 前往养基宝查看实时持仓", "https://wx.yangjibao.com/app/hold")
    
    st.divider()
    st.write("💡 **理财结余同步建议**：")
    st.write("你可以每周查看一次养基宝，若有收益，在左侧选【收入】->【理财收益】录入，App 会自动计入你的【总结余】中。")

# --- 6. 账户配置 ---
with st.expander("⚙️ 设置起始资金"):
    new_init = st.number_input("银行卡当前总余额", value=st.session_state.init_balance)
    if st.button("更新起始点"):
        st.session_state.init_balance = new_init
        st.rerun()
