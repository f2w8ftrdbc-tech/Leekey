import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime

# 1. 基础配置
st.set_page_config(page_title="毕业生理财专家", layout="wide")

# 2. 数据持久化（新手建议先用内存，后续我教你连数据库）
if 'data' not in st.session_state:
    st.session_state.data = pd.DataFrame(columns=["日期", "周期", "账本", "项目", "金额"])
if 'portfolio' not in st.session_state:
    st.session_state.portfolio = pd.DataFrame(columns=["代码", "份额", "成本"])

# 3. 侧边栏：周期与记账
view_mode = st.sidebar.select_slider("统计周期", options=["周", "月", "季度", "年"])

with st.sidebar.expander("📝 记一笔 (支持计算式)"):
    date = st.date_input("日期")
    book = st.selectbox("账本", ["日常", "车子专项", "大额/学费"])
    amt_str = st.text_input("金额", "0")
    if st.button("存入账本"):
        amt = float(eval(amt_str))
        new_row = {"日期": date, "周期": view_mode, "账本": book, "项目": "手动录入", "金额": amt}
        st.session_state.data = pd.concat([st.session_state.data, pd.DataFrame([new_row])], ignore_index=True)
        st.success("记账成功")

# 4. 资产中心：截图识图与行情
st.title("💹 我的全球资产配置")

tab1, tab2 = st.tabs(["实时看板", "📸 截图录入"])

with tab1:
    col1, col2 = st.columns(2)
    # 示例资产实时行情（假设你有英伟达和某基金）
    if not st.session_state.portfolio.empty:
        for i, row in st.session_state.portfolio.iterrows():
            price = yf.Ticker(row['代码']).fast_info['last_price']
            st.metric(f"{row['代码']} 现价", f"${price:.2f}", delta=f"{(price-row['成本'])*row['份额']:.2f}")

with tab2:
    st.info("毕业后资产多？直接上传支付宝/老虎证券截图")
    up_file = st.file_uploader("上传截图", type=['jpg', 'png'])
    if up_file:
        # 这里会运行我为你准备的 OCR 逻辑（需安装 easyocr）
        st.warning("正在调用识图接口... (演示模式：自动提取代码 NVDA, 份额 10)")
        if st.button("确认入库"):
            new_asset = {"代码": "NVDA", "份额": 10.0, "成本": 120.0}
            st.session_state.portfolio = pd.concat([st.session_state.portfolio, pd.DataFrame([new_asset])], ignore_index=True)
