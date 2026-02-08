import os
import sys
import shutil
import tempfile

# 优先设置 SSL 证书路径，避免 curl/requests 报错 77（Windows 下路径含中文时 curl 无法正确读取）
def _path_has_non_ascii(p):
    return p != p.encode("ascii", errors="replace").decode("ascii")

def _setup_ssl_cert():
    try:
        import certifi
        _cacert = certifi.where()
        # 路径含中文或为 Windows 时，curl 常报 77，将证书复制到纯英文路径再指定
        if _path_has_non_ascii(_cacert) or sys.platform == "win32":
            for _dir in (tempfile.gettempdir(), os.environ.get("LOCALAPPDATA", ""), "C:\\Windows\\Temp"):
                if _dir and os.path.isdir(_dir) and not _path_has_non_ascii(_dir):
                    _dest = os.path.join(_dir, "cacert_daily_stock.pem")
                    try:
                        if not os.path.exists(_dest) or os.path.getmtime(_dest) < os.path.getmtime(_cacert):
                            shutil.copy2(_cacert, _dest)
                        os.environ["SSL_CERT_FILE"] = _dest
                        os.environ["REQUESTS_CA_BUNDLE"] = _dest
                        break
                    except OSError:
                        continue
        else:
            os.environ.setdefault("SSL_CERT_FILE", _cacert)
            os.environ.setdefault("REQUESTS_CA_BUNDLE", _cacert)
    except Exception:
        pass

_setup_ssl_cert()

import streamlit as st
import time
from datetime import datetime, timedelta
import akshare as ak
from google import genai
from google.genai import types
import pandas as pd
from dotenv import load_dotenv

# 导入pipeline模块
from src.core.pipeline import StockAnalysisPipeline
from src.enums import ReportType
from src.analyzer import STOCK_NAME_MAP
from src.auth import register, login
from src.usage_tracker import record_usage
from datetime import date as date_type

# --- 1. 页面配置 ---
st.set_page_config(
    page_title="A股分析助手",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 加载环境变量：从 run_new.py 所在目录加载 .env，避免工作目录导致找不到
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_env_path)

# --- 核心修复：使用 cache_resource 保持 Client 连接活跃 ---
@st.cache_resource
def get_gemini_client():
    """
    使用 st.cache_resource 缓存客户端实例。
    防止 Streamlit 每次 Rerun 时重新创建客户端导致旧连接被关闭。
    代理 URL 可通过 GEMINI_PROXY_URL 环境变量配置（如 socks5://127.0.0.1:10808 或 http://VPS_IP:8888）。
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        st.error("❌ 未检测到 GEMINI_API_KEY，请检查环境变量或 .env 文件")
        return None
    # 仅对 Gemini 使用代理，不设置全局 HTTP_PROXY，避免国内接口(Tushare/腾讯/新浪)也走代理导致失败
    proxy_url = os.getenv("GEMINI_PROXY_URL", "socks5h://127.0.0.1:10808")
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            client_args={"proxy": proxy_url} if proxy_url else {},
            async_client_args={"proxy": proxy_url} if proxy_url else {},
        )
    )
    return client

# 获取全局唯一的 client 实例
client = get_gemini_client()

# 自定义样式
st.markdown("""
<style>
    .stTextArea textarea { font-size: 14px; }
    .block-container { padding-top: 2rem; }
    .stChatMessage { border-radius: 10px; margin-bottom: 10px; }
</style>
""", unsafe_allow_html=True)

# --- 1.5 用户认证（需注册/登录后才能使用）---
_auth_required = os.getenv("AUTH_REQUIRED", "true").lower() in ("true", "1", "yes")
if "current_user" not in st.session_state:
    st.session_state.current_user = None

if _auth_required and st.session_state.current_user is None:
    st.title("📈 智能股票分析助手")
    st.caption("使用前请先登录或注册")
    tab1, tab2 = st.tabs(["登录", "注册"])
    with tab1:
        with st.form("login_form"):
            login_user = st.text_input("用户名")
            login_pwd = st.text_input("密码", type="password")
            if st.form_submit_button("登录"):
                ok, user, msg = login(login_user, login_pwd)
                if ok and user:
                    st.session_state.current_user = user
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
    with tab2:
        with st.form("register_form"):
            reg_user = st.text_input("用户名（至少2位）")
            reg_pwd = st.text_input("密码（至少6位）", type="password")
            reg_email = st.text_input("邮箱（必填）")
            reg_phone = st.text_input("手机号（必填）")
            if st.form_submit_button("注册"):
                ok, msg = register(reg_user, reg_pwd, reg_email, reg_phone)
                if ok:
                    st.success(msg + "，请切换到「登录」 tab 登录")
                else:
                    st.error(msg)
    st.stop()

# --- 2. 工具函数 ---

def get_latest_trading_date_ashare():
    try:
        trade_date_df = ak.tool_trade_date_hist_sina()
        current_date = datetime.now().date()
        past_trading_days = trade_date_df[trade_date_df['trade_date'] < current_date]
        return past_trading_days.iloc[-1]['trade_date'] if not past_trading_days.empty else None
    except:
        return datetime.now().date() - timedelta(days=1)

def _calc_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def _calc_macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = _calc_ema(close, fast)
    ema_slow = _calc_ema(close, slow)
    dif = ema_fast - ema_slow
    dea = _calc_ema(dif, signal)
    macd_bar = (dif - dea) * 2
    return dif, dea, macd_bar

def _calc_rsi(close: pd.Series, period=14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))

def check_market_trend():
    """
    根据市场环境判断大盘状态，并给出推荐指标与操作逻辑：
    - 明显趋势（牛/熊）：趋势类指标 MA/MACD/ADX，顺势持有，均线支撑/压力
    - 横盘震荡：动量类 RSI/KDJ/WR，高抛低吸，关注超买超卖
    - 趋势反转点：MACD+RSI/KDJ 组合，背离预警 + 趋势确认
    """
    try:
        # 优先用日线判断环境（指标更稳定）
        df = ak.stock_zh_index_daily(symbol="sh000001")
        if df is None or df.empty or len(df) < 60:
            # 回退：用 5 分钟数据仅做简单趋势
            df_min = ak.stock_zh_a_minute(symbol="sh000001", period='5', adjust='qfq')
            if df_min.empty:
                return "无法获取大盘数据"
            df_min['ma5'] = df_min['close'].rolling(5).mean()
            df_min['ma20'] = df_min['close'].rolling(20).mean()
            last = df_min.iloc[-1]
            direction = "UP" if (last['ma5'] > last['ma20'] and last['close'] > last['ma20']) else "DOWN/震荡"
            return f"大盘趋势：{direction} (收盘:{last['close']}, MA20:{last['ma20']:.2f}) [数据不足，仅分钟级]"

        df = df.sort_values('date').reset_index(drop=True)
        close = df['close']
        df['ma5'] = close.rolling(5).mean()
        df['ma20'] = close.rolling(20).mean()
        dif, dea, macd_bar = _calc_macd(close)
        df['macd_dif'] = dif
        df['macd_dea'] = dea
        df['macd_bar'] = macd_bar
        df['rsi'] = _calc_rsi(close, 14)

        # 取最近一段用于判断（约 20 日）
        lookback = 20
        recent = df.iloc[-lookback:].copy()
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last

        price_now = last['close']
        ma5, ma20 = last['ma5'], last['ma20']
        rsi_now = last['rsi']
        macd_dif_now = last['macd_dif']
        macd_dea_now = last['macd_dea']
        macd_gold = (prev['macd_dif'] <= prev['macd_dea']) and (last['macd_dif'] > last['macd_dea'])
        macd_death = (prev['macd_dif'] >= prev['macd_dea']) and (last['macd_dif'] < last['macd_dea'])

        # 均线粘合度：横盘时 MA5 与 MA20 接近
        ma_spread_pct = abs(ma5 - ma20) / ma20 * 100 if ma20 and ma20 > 0 else 99
        is_sideways_ma = ma_spread_pct < 1.5
        is_rsi_neutral = 40 <= rsi_now <= 60

        # 背离检测：近期价格高点 vs 前一段高点；RSI 对应是否未创新高/新低
        high_win = 5
        recent_high_idx = recent['high'].idxmax()
        recent_high_price = recent.loc[recent_high_idx, 'high']
        recent_high_rsi = recent.loc[recent_high_idx, 'rsi']
        prev_win = df.iloc[-lookback - 30:-lookback] if len(df) >= lookback + 30 else df.iloc[:max(0, len(df) - lookback)]
        if len(prev_win) >= 10:
            prev_high_idx = prev_win['high'].idxmax()
            prev_high_price = prev_win.loc[prev_high_idx, 'high']
            prev_high_rsi = prev_win.loc[prev_high_idx, 'rsi']
            top_divergence = recent_high_price > prev_high_price and recent_high_rsi < prev_high_rsi - 3
            prev_low_idx = prev_win['low'].idxmin()
            prev_low_price = prev_win.loc[prev_low_idx, 'low']
            prev_low_rsi = prev_win.loc[prev_low_idx, 'rsi']
            recent_low_idx = recent['low'].idxmin()
            recent_low_price = recent.loc[recent_low_idx, 'low']
            recent_low_rsi = recent.loc[recent_low_idx, 'rsi']
            bottom_divergence = recent_low_price < prev_low_price and recent_low_rsi > prev_low_rsi + 3
        else:
            top_divergence = bottom_divergence = False

        # 判定环境与建议
        env = "未知"
        recommend = ""
        logic = ""

        if top_divergence or bottom_divergence:
            env = "趋势反转预警"
            recommend = "组合使用：MACD + RSI/KDJ"
            if top_divergence:
                logic = "顶背离：价格创新高但 RSI 未创新高，警惕见顶；等待 MACD 死叉确认后再考虑减仓。"
            else:
                logic = "底背离：价格创新低但 RSI 未新低，关注见底机会；等待 MACD 金叉确认后再考虑介入。"
        elif is_sideways_ma or is_rsi_neutral:
            env = "横盘震荡"
            recommend = "动量类：RSI、KDJ、WR"
            logic = "高抛低吸，关注超买(RSI>70)、超卖(RSI<30)区域的反转信号，忽略趋势类追涨杀跌。"
        elif (ma5 > ma20 and price_now > ma20 and macd_dif_now > macd_dea_now):
            env = "明显上升趋势（偏牛）"
            recommend = "趋势类：MA、MACD、ADX"
            logic = "顺势持有，以均线为支撑/加仓参考，忽略超买超卖噪音；破位 MA20 再考虑止盈或减仓。"
        elif (ma5 < ma20 and price_now < ma20 and macd_dif_now < macd_dea_now):
            env = "明显下降趋势（偏熊）"
            recommend = "趋势类：MA、MACD、ADX"
            logic = "顺势观望或防守，反弹至均线压力减仓，不抄底；等 MACD 金叉+站上 MA20 再考虑参与。"
        else:
            env = "趋势不明确/过渡"
            recommend = "组合使用：MACD + RSI/KDJ"
            logic = "可观望或轻仓，等待趋势明朗（均线多头/空头排列）或出现明确背离/金叉死叉再操作。"

        summary = (
            f"【市场环境】{env}\n"
            f"【推荐指标】{recommend}\n"
            f"【操作逻辑】{logic}\n"
            f"【当前数据】收盘:{price_now:.2f} | MA5:{ma5:.2f} MA20:{ma20:.2f} | RSI:{rsi_now:.1f} | MACD:{'金叉' if macd_gold else '死叉' if macd_death else '中性'}"
        )
        return summary
    except Exception as e:
        return f"大盘检测出错: {e}"

def get_market_symbol(stock_code: str) -> str:
    stock_code = str(stock_code).strip()
    if stock_code.startswith('6'): return 'sh'
    elif stock_code.startswith('00') or stock_code.startswith('3'): return 'sz'
    elif stock_code.startswith('8') or stock_code.startswith('4'): return 'bj'
    return 'sh'


def _fetch_global_news_for_sectors():
    """
    拉取同花顺 + 东方财富全球要闻，合并为一段文本，供热门板块总结与回答使用。
    返回 (combined_text, error_msg)。error_msg 为空表示无致命错误。
    """
    parts = []
    try:
        ths = ak.stock_info_global_ths()
        if ths is not None and not ths.empty:
            for _, row in ths.head(15).iterrows():
                title = row.get("标题", row.get("标题名", row.get("title", "")))
                ts = row.get("时间", row.get("发布时间", row.get("date", "")))
                body = row.get("内容", row.get("摘要", row.get("新闻内容", row.get("content", ""))))
                parts.append(f"[同花顺] {ts} 标题：{title}\n{body}")
    except Exception as e:
        parts.append(f"[同花顺全球要闻获取失败: {e}]")
    try:
        em = getattr(ak, "stock_info_global_em", None)
        if callable(em):
            df_em = em()
            if df_em is not None and not df_em.empty:
                for _, row in df_em.head(15).iterrows():
                    title = row.get("标题", row.get("标题名", row.get("title", "")))
                    ts = row.get("时间", row.get("发布时间", row.get("date", "")))
                    body = row.get("内容", row.get("摘要", row.get("新闻内容", row.get("content", ""))))
                    parts.append(f"[东方财富] {ts} 标题：{title}\n{body}")
    except Exception as e:
        parts.append(f"[东方财富全球要闻获取失败: {e}]")
    combined = "\n\n".join(parts) if parts else "暂无全球要闻数据"
    return _fix_mojibake_utf8(combined), ""


def _fix_mojibake_utf8(text: str) -> str:
    """
    修复「UTF-8 被误当作 Latin-1/CP1252 解码」导致的乱码（多维度情报等接口常见）。
    先整段尝试；失败则按行尝试，避免混合编码时整段报错。
    """
    if not text or not isinstance(text, str):
        return text or ""

    def _decode(s: str) -> str:
        try:
            return s.encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError, AttributeError):
            return s

    try:
        return _decode(text)
    except Exception:
        pass
    # 按行修复，避免部分乱码部分正常时整段失败
    lines = text.split("\n")
    out = []
    for line in lines:
        try:
            out.append(_decode(line))
        except Exception:
            out.append(line)
    return "\n".join(out)


def run_analysis_steps_streaming(stock_code: str, stream_holder: dict):
    """
    流式执行：每步 yield 实际数据内容（无 Step 标题），并纳入 run.py 的大盘、热门板块、个股、龙虎榜、新闻。
    stream_holder 回填 analysis_result、news_context、mar_info、val_boards、ind_info、cap_info、news_text_ak 等供拼 prompt。
    """
    code = stock_code.strip()
    pipeline = StockAnalysisPipeline()
    stock_name = STOCK_NAME_MAP.get(code, "") or f"股票{code}"
    realtime_quote = None
    chip_data = None
    trend_result = None
    news_context = None
    enhanced_context = None
    context = None

    # ----- 日线数据 -----
    yield "### 📊 日线数据\n"
    success, error = pipeline.fetch_and_save_stock_data(code)
    daily_status = "日线数据已获取/校验完成" if success else f"日线获取失败: {error}"
    stream_holder["daily_status"] = daily_status
    if success:
        yield f"{daily_status}。\n\n"
    else:
        yield f"{daily_status}\n\n"

    # ----- 大盘环境（同 run.py）-----
    yield "### 📈 大盘环境\n"
    mar_info = check_market_trend()
    stream_holder["mar_info"] = mar_info
    yield f"{mar_info}\n\n"

    # # ----- 今日热门板块（同 run.py）-----
    # yield "### 🏷️ 今日热门板块\n"
    # try:
    #     csv_path = f'data/board/{datetime.now().strftime("%Y%m%d")}_close_select.csv'
    #     if os.path.exists(csv_path):
    #         val_boards_df = pd.read_csv(csv_path)
    #         val_boards = list(val_boards_df["board"])[:5]
    #         val_boards_str = ", ".join(val_boards)
    #         stream_holder["val_boards"] = "热门板块：" + val_boards_str
    #         yield f"{val_boards_str}\n\n"
    #     else:
    #         stream_holder["val_boards"] = "（本地板块数据未更新）"
    #         yield "今日板块数据未更新\n\n"
    # except Exception:
    #     stream_holder["val_boards"] = "暂无热门板块数据"
    #     yield "暂无热门板块数据\n\n"

    # ----- 实时行情 -----
    yield "### 💹 实时行情\n"
    realtime_info = "未获取到实时行情"
    try:
        realtime_quote = pipeline.fetcher_manager.get_realtime_quote(code)
        if realtime_quote:
            if realtime_quote.name:
                stock_name = realtime_quote.name
            price = getattr(realtime_quote, "price", None)
            vol_ratio = getattr(realtime_quote, "volume_ratio", None)
            turnover = getattr(realtime_quote, "turnover_rate", None)
            realtime_info = f"{stock_name} 现价 {price}，量比 {vol_ratio}，换手率 {turnover}%"
            stream_holder["realtime_info"] = realtime_info
            yield f"**{realtime_info}**\n\n"
        else:
            stream_holder["realtime_info"] = realtime_info
            yield f"{realtime_info}，将用历史数据。\n\n"
    except Exception as e:
        stream_holder["realtime_info"] = realtime_info
        yield f"实时行情获取失败: {e}\n\n"

    # ----- 个股当日行情（同 run.py）-----
    yield "### 📋 个股当日行情\n"
    cur_date = datetime.now().strftime("%Y%m%d")
    ind_info = f"{code} 最新数据"
    try:
        individual = ak.stock_zh_a_hist(code, start_date=cur_date)
        if not individual.empty:
            for col in individual.columns:
                ind_info += f"\n{col}：{individual.iloc[0][col]}"
            stream_holder["ind_info"] = f"{code}分析：{ind_info}"
            yield f"```\n{ind_info}\n```\n\n"
        else:
            ind_info = f"{code} 今日暂无行情（可能非交易时间）"
            stream_holder["ind_info"] = ind_info
            yield f"{ind_info}\n\n"
    except Exception as e:
        stream_holder["ind_info"] = f"个股数据获取失败: {e}"
        yield f"获取个股数据失败: {e}\n\n"

    # ----- 筹码分布 -----
    yield "### 🎯 筹码分布\n"
    chip_info = "未获取到筹码分布数据"
    try:
        chip_data = pipeline.fetcher_manager.get_chip_distribution(code)
        if chip_data:
            chip_info = f"获利比例 {chip_data.profit_ratio:.1%}，90% 集中度 {chip_data.concentration_90:.2%}"
            stream_holder["chip_info"] = chip_info
            yield f"**{chip_info}**\n\n"
        else:
            stream_holder["chip_info"] = chip_info
            yield f"{chip_info}\n\n"
    except Exception as e:
        stream_holder["chip_info"] = chip_info
        yield f"筹码分布获取失败: {e}\n\n"

    # ----- 趋势分析 -----
    yield "### 📉 趋势分析\n"
    trend_info = "无历史行情，未做趋势分析"
    try:
        context_for_trend = pipeline.db.get_analysis_context(code)
        if context_for_trend and context_for_trend.get("raw_data"):
            raw_data = context_for_trend["raw_data"]
            if isinstance(raw_data, list) and len(raw_data) > 0:
                df_trend = pd.DataFrame(raw_data)
                trend_result = pipeline.trend_analyzer.analyze(df_trend, code)
                trend_info = f"趋势状态 {trend_result.trend_status.value}，买入信号 {trend_result.buy_signal.value}，评分 {trend_result.signal_score}"
                if trend_result.signal_reasons:
                    trend_info += "；理由：" + "；".join(trend_result.signal_reasons[:3])
                if trend_result.risk_factors:
                    trend_info += "；风险：" + "；".join(trend_result.risk_factors[:2])
                stream_holder["trend_info"] = trend_info
                yield f"**{trend_result.trend_status.value}**，买入信号 **{trend_result.buy_signal.value}**，评分 **{trend_result.signal_score}**\n"
                if trend_result.signal_reasons:
                    yield "理由：" + "；".join(trend_result.signal_reasons[:3]) + "\n"
                if trend_result.risk_factors:
                    yield "风险：" + "；".join(trend_result.risk_factors[:2]) + "\n"
                yield "\n"
            else:
                stream_holder["trend_info"] = trend_info
                yield "历史数据为空，未做趋势分析\n\n"
        else:
            stream_holder["trend_info"] = trend_info
            yield f"{trend_info}\n\n"
    except Exception as e:
        stream_holder["trend_info"] = trend_info
        yield f"趋势分析失败: {e}\n\n"

    # ----- 龙虎榜与资金（同 run.py）-----
    yield "### 🐉 龙虎榜与资金\n"
    market = get_market_symbol(code)
    dd = None
    try:
        latest_date = get_latest_trading_date_ashare()
        dd = latest_date.strftime("%Y%m%d") if latest_date else datetime.now().strftime("%Y%m%d")
    except Exception:
        dd = datetime.now().strftime("%Y%m%d")
    longhu_info = ""
    try:
        buyin = ak.stock_lhb_stock_detail_em(symbol=code, date=dd, flag="买入")
        buyin = buyin[["交易营业部名称", "买入金额", "类型"]]
        for _, row in buyin.iterrows():
            longhu_info += f"买入 {(int(row['买入金额'])/(10**7)):.2f} 千万元 — {row['交易营业部名称']} ({row['类型']})\n"
    except Exception:
        longhu_info += f"{dd} 龙虎榜买入未上榜\n"
    try:
        sellout = ak.stock_lhb_stock_detail_em(symbol=code, date=dd, flag="卖出")
        sellout = sellout[["交易营业部名称", "卖出金额", "类型"]]
        for _, row in sellout.iterrows():
            longhu_info += f"卖出 {(int(row['卖出金额'])/(10**7)):.2f} 千万元 — {row['交易营业部名称']} ({row['类型']})\n"
    except Exception:
        longhu_info += f"{dd} 龙虎榜卖出未上榜\n"
    try:
        cap_flow = ak.stock_individual_fund_flow(stock=code, market=market)
        lt = get_latest_trading_date_ashare()
        if lt is not None and not cap_flow.empty:
            cap_flow["日期"] = pd.to_datetime(cap_flow["日期"]).dt.date
            row = cap_flow[cap_flow["日期"] == lt]
            if not row.empty:
                cap_ttl = row.iloc[0]["主力净流入-净额"]
                cap_info = f"主力净流入-净额：{float(cap_ttl/(10**8)):.2f} 亿元"
                stream_holder["cap_info"] = cap_info
                longhu_info = cap_info + "\n\n" + longhu_info
    except Exception:
        pass
    if not stream_holder.get("cap_info"):
        stream_holder["cap_info"] = "资金流向获取失败"
    longhu_full = longhu_info.strip() or "暂无龙虎榜数据"
    stream_holder["longhu_info"] = longhu_full
    yield f"```\n{longhu_full}\n```\n\n"

    # ----- 多维度情报搜索（展示搜索到的正文）-----
    yield "### 🔍 多维度情报搜索\n"
    if pipeline.search_service.is_available:
        try:
            intel_results = pipeline.search_service.search_comprehensive_intel(
                stock_code=code, stock_name=stock_name, max_searches=5
            )
            if intel_results:
                news_context = pipeline.search_service.format_intel_report(intel_results, stock_name)
                news_context = _fix_mojibake_utf8(news_context)
                try:
                    qctx = pipeline._build_query_context()
                    for dim_name, response in intel_results.items():
                        if response and getattr(response, "success", False) and getattr(response, "results", None):
                            pipeline.db.save_news_intel(
                                code=code, name=stock_name, dimension=dim_name,
                                query=response.query, response=response, query_context=qctx,
                            )
                except Exception:
                    pass
                yield f"```\n{news_context}\n```\n\n"
            else:
                yield "未获取到情报结果\n\n"
        except Exception as e:
            yield f"情报搜索失败: {e}\n\n"
    else:
        yield "搜索服务未配置，跳过情报搜索\n\n"

    # ----- 近期新闻（AkShare 近 3 天，同 run.py）-----
    yield "### 📰 近期新闻（近 3 天）\n"
    news_text_ak = "暂无近期新闻"
    try:
        stock_news = ak.stock_news_em(symbol=code)
        stock_news["发布时间"] = pd.to_datetime(stock_news["发布时间"], errors="coerce")
        recent = stock_news[stock_news["发布时间"] >= (datetime.now() - timedelta(days=3))]
        if not recent.empty:
            news_text_ak = ""
            for _, row in recent.head(10).iterrows():
                news_text_ak += f"{row['发布时间']}\n标题：{row['新闻标题']}\n内容：{row['新闻内容']}\n来源：{row['文章来源']}\n\n"
            stream_holder["news_text_ak"] = news_text_ak
            yield f"```\n{news_text_ak.strip()}\n```\n\n"
        else:
            stream_holder["news_text_ak"] = news_text_ak
            yield f"{news_text_ak}\n\n"
    except Exception as e:
        stream_holder["news_text_ak"] = news_text_ak
        yield f"新闻获取失败: {e}\n\n"

    # 同花顺/东方财富全球要闻不在初始分析中拉取，仅在用户追问「新闻」「消息」「板块」时再拉取并总结热门板块

    # ----- 分析上下文与增强 -----
    context = pipeline.db.get_analysis_context(code)
    if context is None:
        context = {
            "code": code, "stock_name": stock_name, "date": date_type.today().isoformat(),
            "data_missing": True, "today": {}, "yesterday": {},
        }
    enhanced_context = pipeline._enhance_context(
        context, realtime_quote, chip_data, trend_result, stock_name
    )

    # ----- AI 分析结论 -----
    yield "### 🤖 AI 分析结论\n"
    try:
        result = pipeline.analyzer.analyze(enhanced_context, news_context=news_context)
        if result:
            stream_holder["analysis_result"] = result
            stream_holder["news_context"] = news_context
            stream_holder["stock_name"] = stock_name
            stream_holder["enhanced_context"] = enhanced_context
            yield f"**操作建议**：{result.operation_advice}\n\n**情绪评分**：{result.sentiment_score}\n\n"
            if getattr(result, "analysis_summary", None):
                yield f"{result.analysis_summary}\n\n"
        else:
            yield "AI 分析返回为空\n\n"
    except Exception as e:
        yield f"AI 分析失败: {e}\n\n"

    # ----- 保存分析历史 -----
    if stream_holder.get("analysis_result"):
        try:
            ctx_snapshot = pipeline._build_context_snapshot(
                enhanced_context=enhanced_context, news_content=news_context,
                realtime_quote=realtime_quote, chip_data=chip_data,
            )
            pipeline.db.save_analysis_history(
                result=stream_holder["analysis_result"],
                query_id=pipeline.query_id or "",
                report_type=ReportType.FULL.value,
                news_content=news_context,
                context_snapshot=ctx_snapshot,
                save_snapshot=getattr(pipeline.config, "save_context_snapshot", True),
            )
            yield "已保存分析历史。\n\n"
        except Exception as e:
            yield f"保存分析历史失败: {e}\n\n"


# --- 3. 初始化会话状态 ---

if "messages" not in st.session_state:
    st.session_state.messages = []

if "enable_web_search" not in st.session_state:
    st.session_state.enable_web_search = False


def _get_chat_config():
    """若用户开启联网搜索，则返回带 Google Search 的 config，否则返回 None（使用默认）。"""
    if st.session_state.get("enable_web_search"):
        try:
            return types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        except Exception:
            return None
    return None

# 确保只有在 client 成功初始化后才创建 chat_session
if client and "chat_session" not in st.session_state:
    try:
        # 启动 Gemini 的聊天会话，model 名称请根据实际可用模型调整
        st.session_state.chat_session = client.chats.create(model="gemini-3-pro-preview")
    except Exception as e:
        st.error(f"无法初始化聊天会话: {e}")

# --- 4. 侧边栏：参数配置 ---

with st.sidebar:
    st.title("⚙️ 配置参数")
    _user = st.session_state.current_user
    if _user:
        st.caption(f"👤 {_user['username']}")
        if st.button("退出登录"):
            st.session_state.current_user = None
            st.rerun()
    st.divider()
    stock_code = st.text_input("股票代码", placeholder="例如: 601616")
    
    default_prompt = """简短总结，给出最直接的操作建议。

【决策 = 动力（具体理由） > 阻力（具体风险）】。
给出【失效条件】（止损逻辑）。"""
    
    user_system_prompt = st.text_area("分析指令 (System Prompt)", value=default_prompt, height=300)

    st.session_state.enable_web_search = st.checkbox(
        "🔍 联网搜索",
        value=st.session_state.get("enable_web_search", False),
        help="开启后，对话时可使用 Google 搜索获取实时信息（需额外计费）",
    )

    if st.button("🗑️ 清空对话"):
        st.session_state.messages = []
        if client:
            st.session_state.chat_session = client.chats.create(model="gemini-2.0-flash")
        st.rerun()

# --- 5. 主界面布局 ---

st.title("📈 智能股票分析助手")
st.caption("基于 Gemini 2.0 Flash 与 AkShare 实时数据")

# 免责声明
# st.warning("⚠️ **风险提示**：本工具生成的内容仅供技术交流与参考，不构成任何投资建议。股市有风险，入市需谨慎。")

# 展示历史消息
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# 初始分析逻辑：点击按钮触发第一次深度分析
if st.button("🚀 开始深度分析", type="primary"):
    if not client:
        st.error("Gemini Client 初始化失败，请检查 API Key。")
    elif not stock_code:
        st.error("请输入股票代码")
    else:
        # 流式展示：每步 yield 一段内容，参考 run.py 的流式输出
        stream_holder = {}
        steps_container = st.empty()
        full_response = ""
        with st.status("正在搜集多维数据...", expanded=True) as status:
            for chunk in run_analysis_steps_streaming(stock_code, stream_holder):
                full_response += chunk
                steps_container.markdown(full_response)
            status.update(label="数据准备就绪，正在生成报告...", state="complete")

        # 用流式阶段已写入的 stream_holder 拼 prompt（与页面对齐：展示过的全部进 full_context）
        mar_info = stream_holder.get("mar_info") or check_market_trend()
        val_boards = stream_holder.get("val_boards") or "暂无热门板块数据"
        ind_info = stream_holder.get("ind_info") or f"{stock_code} 暂无行情"
        cap_info = stream_holder.get("cap_info") or "资金流向获取失败"
        news_intel = (stream_holder.get("news_context") or "").strip() or "未做多维度情报搜索"
        news_text_ak = stream_holder.get("news_text_ak") or "暂无近期新闻"
        daily_status = stream_holder.get("daily_status") or ""
        realtime_info = stream_holder.get("realtime_info") or "未获取到实时行情"
        chip_info = stream_holder.get("chip_info") or "未获取到筹码分布"
        trend_info = stream_holder.get("trend_info") or "未做趋势分析"
        longhu_info = stream_holder.get("longhu_info") or "暂无龙虎榜数据"

        # 组装发送给 Gemini 的初始上下文（包含所有展示过的数据，不含全球要闻，追问时再按需拉取）
        full_context = f"""
        你是一个资深A股分析员。
        股票代码: {stock_code}
        当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        
        【日线数据】: {daily_status}
        【大盘环境】: {mar_info}
        【热门板块】: {val_boards}
        【实时行情】: {realtime_info}
        【个股当日行情】: {ind_info}
        【筹码分布】: {chip_info}
        【趋势分析】: {trend_info}
        【资金面】: {cap_info}
        【龙虎榜明细】:
        {longhu_info}
        
        【多维度情报搜索】:
        {news_intel}
        
        【近期新闻（近3天）】:
        {news_text_ak}
        
        请结合以上全部数据，执行以下指令：
        {user_system_prompt}
        输出要求漂亮的markdown格式。
        """

        # 调用 Gemini 并流式展示
        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""
            
            try:
                # 发送给 Chat Session（若开启联网搜索则传入带 Google Search 的 config）
                chat_config = _get_chat_config()
                responses = st.session_state.chat_session.send_message_stream(
                    full_context, config=chat_config
                )
                for chunk in responses:
                    full_response += chunk.text
                    response_placeholder.markdown(full_response)
                
                # 记录历史
                st.session_state.messages.append({"role": "user", "content": f"分析股票 {stock_code}"})
                st.session_state.messages.append({"role": "assistant", "content": full_response})
                # 用量监控
                if st.session_state.current_user:
                    record_usage(
                        st.session_state.current_user["id"],
                        st.session_state.current_user["username"],
                        "analysis",
                        stock_code,
                    )
            except Exception as e:
                st.error(f"API 调用出错: {e}")

# --- 6. 多轮追问聊天框 ---
def _user_asks_news_or_sectors(prompt: str) -> bool:
    """用户是否在问新闻、消息或板块（需拉取全球要闻并总结热门板块）"""
    if not prompt or not isinstance(prompt, str):
        return False
    p = prompt.strip()
    return "新闻" in p or "消息" in p or "板块" in p


if prompt := st.chat_input("您可以继续追问，例如：'如果缩量了怎么办？' 或 '详细解释一下资金面'"):
    if not client:
        st.error("Client 未连接")
    else:
        # 展示用户提问
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})

        # AI 回复
        with st.chat_message("assistant"):
            response_placeholder = st.empty()
            full_response = ""

            if _user_asks_news_or_sectors(prompt):
                # 先拉取同花顺 + 东方财富全球要闻，总结热门板块，yield 给用户后再结合新闻与板块回答
                with st.status("正在拉取同花顺与东方财富全球要闻…", expanded=True):
                    news_combined, _ = _fetch_global_news_for_sectors()
                # 用 Gemini 根据新闻总结当前热点板块（一次非流式调用）
                hot_sectors_summary = "暂无热门板块总结"
                try:
                    summary_prompt = f"""根据以下全球要闻内容，用一两段话总结当前A股市场热点板块（列出板块名称并简要说明原因）。只输出总结内容，不要复述新闻全文。

新闻与要闻：
{news_combined[:12000]}
"""
                    gen = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=summary_prompt,
                        config=types.GenerateContentConfig(temperature=0.3),
                    )
                    if gen and gen.text:
                        hot_sectors_summary = gen.text.strip()
                except Exception as e:
                    hot_sectors_summary = f"热门板块总结生成失败: {e}，将仅基于新闻原文回答。"
                # 先 yield 热门板块给用户
                full_response = "### 热门板块总结\n\n" + hot_sectors_summary + "\n\n---\n\n"
                response_placeholder.markdown(full_response)
                # 再结合新闻与热门板块总结，流式回答用户问题
                augmented_prompt = f"""请结合以下「热门板块总结」与「新闻摘要」回答用户问题。先可简要呼应热点，再针对用户问题给出分析。

【热门板块总结】
{hot_sectors_summary}

【新闻摘要】
{news_combined[:8000]}

用户问题：{prompt}
"""
                try:
                    chat_config = _get_chat_config()
                    responses = st.session_state.chat_session.send_message_stream(
                        augmented_prompt, config=chat_config
                    )
                    for chunk in responses:
                        full_response += chunk.text
                        response_placeholder.markdown(full_response)
                except Exception as e:
                    full_response += f"\n\n回复出错: {e}"
                    response_placeholder.markdown(full_response)
            else:
                try:
                    chat_config = _get_chat_config()
                    responses = st.session_state.chat_session.send_message_stream(
                        prompt, config=chat_config
                    )
                    for chunk in responses:
                        full_response += chunk.text
                        response_placeholder.markdown(full_response)
                except Exception as e:
                    st.error(f"回复出错: {e}。可能连接已断开，请尝试点击左侧'清空对话'按钮。")

            if full_response:
                st.session_state.messages.append({"role": "assistant", "content": full_response})
                # 用量监控
                if st.session_state.current_user:
                    record_usage(
                        st.session_state.current_user["id"],
                        st.session_state.current_user["username"],
                        "follow_up",
                        None,
                    )