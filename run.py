import streamlit as st
import time
from datetime import datetime, timedelta
import akshare as ak
from google import genai
import pandas as pd
from google.genai import types
from dotenv import load_dotenv
# --- 1. 页面配置 ---
st.set_page_config(
    page_title="股票分析助手",
    layout="wide",  # 开启宽屏模式，利用屏幕空间
    initial_sidebar_state="expanded"
)
load_dotenv()

client = genai.Client()
st.markdown("""
<style>
    .stTextArea textarea { font-size: 14px; }
    .stTextInput input { font-size: 14px; }
    /* 调整顶部空白，让内容更紧凑 */
    .block-container { padding-top: 2rem; }
</style>
""", unsafe_allow_html=True)

def get_latest_trading_date_ashare():
    """
    获取中国A股上一个真实交易日
    """
    # 1. 获取新浪财经的交易日历数据
    # 这个接口返回历史上所有的交易日列表
    trade_date_df = ak.tool_trade_date_hist_sina()
    
    # 2. 获取当前日期 (转为 date 类型，去除时间)
    current_date = datetime.now().date()
    
    # 3. 筛选出所有“小于”当前日期的交易日
    # trade_date 这一列通常是 datetime.date 对象
    past_trading_days = trade_date_df[trade_date_df['trade_date'] < current_date]
    
    # 4. 取最后一个，即为最近的一个交易日
    if not past_trading_days.empty:
        latest_date = past_trading_days.iloc[-1]['trade_date']
        return latest_date
    else:
        return None

def check_market_trend():
    """
    逻辑1 & 2：判断大盘（上证指数）5分钟K线是否处于上升趋势。
    定义：当前5分钟K线的 MA5 > MA20 且收盘价 > MA20 视为上升趋势。
    """
    # log("正在检查大盘环境...")
    try:
        # 获取上证指数5分钟数据
        df_min = ak.stock_zh_a_minute(symbol="sh000001", period='5', adjust='qfq')
        if df_min.empty:
            # send_markdown_msg("获取大盘数据失败。")
            return False
        
        # 计算均线
        df_min['ma5'] = df_min['close'].rolling(5).mean()
        df_min['ma20'] = df_min['close'].rolling(20).mean()
        
        last_row = df_min.iloc[-1]
        
        # 判断条件：MA5在MA20之上，且当前价格也在生命线之上
        is_uptrend = (last_row['ma5'] > last_row['ma20']) and (last_row['close'] > last_row['ma20'])
        
        if is_uptrend:
            return f"大盘趋势判断：UP (收盘:{last_row['close']} > MA20:{last_row['ma20']:.2f})"

        else:
            return f"大盘趋势判断：DOWN/震荡 (收盘:{last_row['close']} < MA20:{last_row['ma20']:.2f} 或 均线死叉)"
            
    except Exception as e:
        # log(f"大盘检测出错: {e}")
        return False

def get_market_symbol(stock_code: str) -> str:
    """
    根据股票代码判断市场标识 (SH, SZ, BJ)
    :param stock_code: 6位股票代码字符串, e.g., '600519'
    :return: 带后缀的代码 (e.g., '600519.SH') 或 仅市场标识
    """
    if not isinstance(stock_code, str):
        stock_code = str(stock_code)
    
    # 确保代码是6位，处理可能的输入错误
    stock_code = stock_code.strip()
    
    if stock_code.startswith('6'):
        return 'SH'
    elif stock_code.startswith('00') or stock_code.startswith('3'):
        return 'SZ'
    elif stock_code.startswith('8') or stock_code.startswith('4'):
        return 'BJ'
    else:
        return 'UNKNOWN'
def check_time():
    now = datetime.now().hour
    if now < 11:
        return 'pre-market'
    elif now < 14:
        return 'noon'
    elif now < 15:
        return 'close'
    else:
        return 'post-market'
# --- 2. 模拟你的后台逻辑 (你需要替换这里) ---
def backend_process(code, user_prompt):
    """
    这是一个模拟函数。
    实际使用时，请把这里替换为你真实的后台搜索和处理逻辑。
    使用 yield 来实现'一步一步'输出的效果。
    """
    with open('logs/search_log.txt', 'a', encoding='utf-8') as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - 开始分析股票: {code}\n")
    # 模拟第一步：初始化
    yield f"### 🔍 开始分析股票: {code}\n"
    time.sleep(0.5)
    
    # 模拟第二步：结合提示词
    yield f"**收到指令**\n\n"
    yield "---\n"
    time.sleep(0.5)
    
    # # 模拟第三步：正在搜索信息 (模拟流式输出)
    # info_steps = [
    #     "正在连接金融数据库...",
    #     "获取最近财报数据...",
    #     "分析市场情绪...",
    #     "生成最终报告..."
    # ]
    
    
    yield f"- 正在分析市场 ✅\n"
    mar_info = check_market_trend()
    yield f"  - {mar_info}\n"

    cur_date = datetime.now().strftime('%Y%m%d')
    try:
        val_boards_df = pd.read_csv(f'data/board/{datetime.now().strftime("%Y%m%d")}_close_select.csv')
        print(val_boards_df)       
        val_boards = list(val_boards_df['board'])[:5]

        yield f"- 今日热门板块: {', '.join(val_boards)} ✅\n"
    except:
        val_boards = []
        yield f"- 今日热门板块今日未更新 ❌\n"

    yield f"- 正在获取个股数据 ✅\n"
    try:
        individual = ak.stock_zh_a_hist(code,start_date=cur_date)
        ind_info = f'{code}最新数据'

        market = get_market_symbol(code).lower()
        print('交易所：' + market)

        for col in individual.columns:
            ind_info +=f"""
            {col}：{individual.iloc[0][col]}"""

        yield f"```\n{ind_info}\n```\n"
    except:
        yield f"获取个股数据失败 ❌\n"
    
    dd = get_latest_trading_date_ashare().strftime('%Y%m%d')
    yield f"- 正在获取龙虎榜数据 ✅\n"
    longhu_info = ''
    try:
        buyin = ak.stock_lhb_stock_detail_em(symbol = code, date = dd, flag = '买入')
        buyin = buyin[['交易营业部名称','买入金额','类型']]
        for index, row in buyin.iterrows():
            longhu_info += f"买入金额 = {(int(row['买入金额']) / (10**7)):.2f}千万元 -- 交易营业部名称: {row['交易营业部名称']}  类型：{row['类型']}\n"
    except:
        longhu_info += f'{cur_date} 龙虎榜买入未上榜\n'
    try:
    
        sellout = ak.stock_lhb_stock_detail_em(symbol = code, date = dd, flag = '卖出')
        sellout = sellout[['交易营业部名称','卖出金额','类型']]
        for index, row in sellout.iterrows():
            longhu_info += f"卖出金额 = {(int(row['卖出金额']) / (10**7)):.2f}千万元 -- 交易营业部名称: {row['交易营业部名称']}  类型：{row['类型']}\n"
    except:
        longhu_info += f'{cur_date} 龙虎榜卖出未上榜'
    

    
    
    try:
        cap_flow = ak.stock_individual_fund_flow(stock=code, market=market)
        cap_ttl = cap_flow[cap_flow['日期'] == datetime.now().date].iloc[0]['主力净流入-净额']
        longhu_info = f"东方财富数据：主力净流入-净额 = {float(cap_ttl / (10**7)):.2f}千万元\n\n" + longhu_info
    except:
        cap_flow = ak.stock_individual_fund_flow(stock=code, market=market)
        print(cap_flow)
        cap_ttl = cap_flow[cap_flow['日期'] == get_latest_trading_date_ashare()].iloc[0]['主力净流入-净额']
        longhu_info = f"东方财富数据：主力净流入-净额 = {float(cap_ttl / (10**7)):.2f}千万元\n\n" + longhu_info
    
    yield f"```\n{longhu_info}\n```\n"
    yield f"- 正在搜索新闻 ✅\n"
    stock_news = ak.stock_news_em(symbol=code)
    stock_news = stock_news[stock_news['发布时间'].apply(lambda x: datetime.strptime(x, '%Y-%m-%d %H:%M:%S')) > datetime.now()-timedelta(days=3)]
    news_info = '\n 相关新闻参考'
    for index, row in stock_news.iterrows():
        news_info += f""" {row['发布时间']} 
新闻标题：{row['新闻标题']}
新闻内容：{row['新闻内容']}
文章来源：{row['文章来源']}
"""
    yield f"```\n{news_info}\n```\n"
    prompt = f"""作为资深A股分析员，根据如下信息分析一下A股{code}这支股票
-------------------------------------------
{ind_info}
-------------------------------------------
今日新闻高热度概念板块：
{val_boards}
-------------------------------------------
{mar_info}
-------------------------------------------
{news_info}
-------------------------------------------
{longhu_info}
""" 
    prompt += f"""
    此时此刻是{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}，对于这只股票给出现在的操作建议"""
    prompt += user_prompt

    
    yield f"- 正在询问Gemini ✅\n"
    # print(prompt)
    response = client.models.generate_content(model="gemini-3-flash-preview", 
                                                  contents=prompt,
                                                  config=types.GenerateContentConfig(
                                                      temperature=0.7,
                                            
            ))
    # print(response)
    
    # yield f"\n ##✅ {name}分析完成!"
    yield response.text

# --- 3. 界面布局 (核心逻辑) ---

st.title("📈 智能股票分析")
st.markdown("---")

# 创建左右分栏：左侧 30% (3份)，右侧 70% (7份)
left_col, right_col = st.columns([4, 6])

# === 左侧栏 (输入区) ===
with left_col:
    st.subheader("🛠️ 配置参数")
    
    # 输入框 1: 股票代码
    stock_code = st.text_input(
        "股票代码", 
        placeholder="例如: 601616, 002498",
        help="请输入具体的股票代码"
    )
    
    # 输入框 2: 提示词 (带默认值)
    default_prompt = """简短总结，给出最直接的操作建议，严格参考以下指令
    输出要求漂亮的markdown格式，方便我阅读，不要超过200字。
【核心实操决策】

谨慎判断热门板块，分析该股票是否属于热门板块。大盘如果不是说全线大跌，我给出的热门概念板块理论上还是能站住的
数据双源验证：所有结论必须基于我提供的数据信息, 并且新闻要参考近期的时效性。
如果说连续涨停但是高量比肯定是有问题
买入硬指标：

量能铁律：严禁缩量拉升。若开盘量比 < 2.0 且 股价涨幅 > 3%，视为诱多，直接撤单。



板块确认：个股形态再好，若所属板块处于下跌/出货趋势，必须强制降级评分。
卖出硬指标（移动止盈）：
资金管理：所有买入建议必须基于15万总本金，明确给出具体建议仓位金额（如：试错2万/重仓5万）。

决策公式化：最终判决严禁只说‘买入/卖出’，必须使用公式：【决策 = 动力（具体理由） > 阻力（具体风险）】。

失效推演：给出买入建议时，必须强制推演**【失效条件】**（发生什么情况说明逻辑错了，必须无脑止损）"""
    prompt = st.text_area(
        "分析提示词", 
        value=default_prompt, 
        height=200, # 增加高度，占据中下部分
        help="你可以修改此提示词以定制分析方向"
    )
    
    # 运行按钮
    run_btn = st.button("🚀 开始分析", type="primary", use_container_width=True)

# === 右侧栏 (展示区) ===
with right_col:
    st.subheader("📝 分析报告")
    
    # 创建一个空的容器，用来存放输出内容
    output_container = st.empty()
    
    # 点击按钮后的逻辑
    if run_btn:
        if not stock_code:
            st.error("⚠️ 请先输入股票代码")
        else:
            full_response = ""
            
            # 调用后台逻辑，并实时更新界面
            # 这里的 process 实际上就是你的后台代码，需要改造成 yield 输出
            for chunk in backend_process(stock_code, prompt):
                full_response += chunk
                
                # 核心：每次有新内容，都重新渲染这个容器，实现打字机/流式效果
                output_container.markdown(full_response)