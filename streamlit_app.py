import streamlit as st
import pandas as pd
import yfinance as yf
import requests
from datetime import datetime

st.set_page_config(page_title="NSE F&O BTST/STBT Radar", layout="wide")

# -----------------------------
# CONFIG
# -----------------------------
STOCKS = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK",
    "INFY", "TCS", "ITC", "LT", "KOTAKBANK",
    "ADANIENT", "ADANIPORTS", "BAJFINANCE", "BPCL", "HINDUNILVR",
    "MARUTI", "NTPC", "ONGC", "POWERGRID"
]

MIN_SCORE = 6
TOP_ACTIVE_COUNT = 15

# -----------------------------
# HELPERS
# -----------------------------
@st.cache_data(ttl=900)
def fetch_bulk_price_data(symbols):
    tickers = [s + ".NS" for s in symbols]
    data = yf.download(
        tickers=" ".join(tickers),
        period="10d",
        interval="60m",
        group_by="ticker",
        progress=False,
        threads=True,
        timeout=30,
    )
    return data


def get_symbol_df(bulk_data, symbol):
    ticker = symbol + ".NS"
    try:
        if isinstance(bulk_data.columns, pd.MultiIndex):
            df = bulk_data[ticker].dropna()
        else:
            df = bulk_data.dropna()
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=900)
def get_nse_oi(symbol):
    try:
        session = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        session.get("https://www.nseindia.com", headers=headers, timeout=8)
        url = f"https://www.nseindia.com/api/quote-derivative?symbol={symbol}"
        r = session.get(url, headers=headers, timeout=8)
        if r.status_code != 200:
            return "No OI Data"
        js = r.json()
        stocks = js.get("stocks", [])
        if not stocks:
            return "No OI Data"
        trade_info = stocks[0].get("marketDeptOrderBook", {}).get("tradeInfo", {})
        oi_change = trade_info.get("changeinOpenInterest", 0)
        if oi_change is None:
            return "No OI Data"
        return float(oi_change)
    except Exception:
        return "No OI Data"


def interpret_oi(price_change, oi_change):
    if oi_change == "No OI Data":
        return "No OI Data"
    if price_change > 0 and oi_change > 0:
        return "Long Buildup"
    if price_change < 0 and oi_change > 0:
        return "Short Buildup"
    if price_change > 0 and oi_change < 0:
        return "Short Covering"
    if price_change < 0 and oi_change < 0:
        return "Long Unwinding"
    return "Neutral"


def scan_without_oi(symbol, df):
    if df is None or df.empty or len(df) < 30:
        return None

    df = df.copy()
    df["EMA20"] = df["Close"].ewm(span=20).mean()
    df["EMA50"] = df["Close"].ewm(span=50).mean()
    df["VOLMA"] = df["Volume"].rolling(20).mean()

    close = float(df["Close"].iloc[-1])
    open_ = float(df["Open"].iloc[-1])
    high = float(df["High"].iloc[-1])
    low = float(df["Low"].iloc[-1])
    volume = float(df["Volume"].iloc[-1])
    volma = float(df["VOLMA"].iloc[-1]) if not pd.isna(df["VOLMA"].iloc[-1]) else 0
    ema20 = float(df["EMA20"].iloc[-1])
    ema50 = float(df["EMA50"].iloc[-1])

    prev_close = float(df["Close"].iloc[-2])
    close_6 = float(df["Close"].iloc[-6])
    prev_high = float(df["High"].iloc[-6:-1].max())
    prev_low = float(df["Low"].iloc[-6:-1].min())

    pct_change = ((close - prev_close) / prev_close) * 100 if prev_close else 0
    vol_ratio = volume / volma if volma else 0

    ssl_sweep = low < prev_low and close > prev_low
    bsl_sweep = high > prev_high and close < prev_high
    bull_disp = close > open_ and close > prev_high and volume > volma
    bear_disp = close < open_ and close < prev_low and volume > volma

    buy_score = 0
    sell_score = 0

    buy_score += int(close > ema20 and close > ema50)
    buy_score += int(close >= prev_high * 0.995)
    buy_score += int(volume > volma)
    buy_score += int(close > close_6)
    buy_score += int(high > float(df["High"].iloc[-2]) and low > float(df["Low"].iloc[-2]))
    buy_score += int(ssl_sweep)
    buy_score += int(bull_disp)
    buy_score += int(close > ema20)

    sell_score += int(close < ema20 and close < ema50)
    sell_score += int(close <= prev_low * 1.005 or bsl_sweep)
    sell_score += int(volume > volma)
    sell_score += int(close < close_6)
    sell_score += int(high < float(df["High"].iloc[-2]) and low < float(df["Low"].iloc[-2]))
    sell_score += int(bsl_sweep)
    sell_score += int(bear_disp)
    sell_score += int(close < ema20)

    return {
        "Stock": symbol,
        "Close": round(close, 2),
        "% Change": round(pct_change, 2),
        "Vol xAvg": round(vol_ratio, 2),
        "Buy Score Num": buy_score,
        "Sell Score Num": sell_score,
        "Buy Score": f"{buy_score}/8",
        "Sell Score": f"{sell_score}/8",
        "SSL Sweep": "YES" if ssl_sweep else "NO",
        "BSL Sweep": "YES" if bsl_sweep else "NO",
        "Bull Disp": "YES" if bull_disp else "NO",
        "Bear Disp": "YES" if bear_disp else "NO",
        "Price Change Raw": close - prev_close,
    }


def add_oi_and_action(row):
    oi_change = get_nse_oi(row["Stock"])
    oi_signal = interpret_oi(row["Price Change Raw"], oi_change)

    action = "NO TRADE"
    trade_type = "-"

    if row["Buy Score Num"] >= MIN_SCORE and row["Bull Disp"] == "YES" and oi_signal in ["Long Buildup", "Short Covering"]:
        action = "🔥 BTST EXECUTE"
        trade_type = "BTST"
    elif row["Sell Score Num"] >= MIN_SCORE and row["Bear Disp"] == "YES" and oi_signal in ["Short Buildup", "Long Unwinding"]:
        action = "🔥 STBT EXECUTE"
        trade_type = "STBT"
    elif row["Buy Score Num"] >= MIN_SCORE:
        action = "⭐ BTST WATCH"
    elif row["Sell Score Num"] >= MIN_SCORE:
        action = "⭐ STBT WATCH"

    close = row["Close"]
    if trade_type == "BTST":
        entry, sl, tp1 = close, round(close * 0.985, 2), round(close * 1.025, 2)
    elif trade_type == "STBT":
        entry, sl, tp1 = close, round(close * 1.015, 2), round(close * 0.975, 2)
    else:
        entry, sl, tp1 = "-", "-", "-"

    row["OI Change"] = oi_change
    row["OI Signal"] = oi_signal
    row["Action"] = action
    row["Entry"] = entry
    row["SL"] = sl
    row["TP1"] = tp1
    return row

# -----------------------------
# UI
# -----------------------------
st.title("🔥 NSE F&O BTST / STBT Radar")
st.caption("Top gainers/losers + volume shockers + OI confirmation + BTST/STBT scoring")

with st.sidebar:
    st.header("Settings")
    min_score = st.slider("Minimum score", 1, 8, MIN_SCORE)
    top_count = st.slider("Active candidates for OI check", 5, 25, TOP_ACTIVE_COUNT)
    run_scan = st.button("Refresh Scan")

st.info("Dashboard fetches live price data and checks OI only for top active F&O candidates to avoid timeout.")

try:
    bulk = fetch_bulk_price_data(STOCKS)
    pre_rows = []
    for s in STOCKS:
        df = get_symbol_df(bulk, s)
        result = scan_without_oi(s, df)
        if result:
            pre_rows.append(result)

    if not pre_rows:
        st.warning("No data available. This can happen on holidays/weekends or if data provider blocks requests.")
        st.stop()

    pre_df = pd.DataFrame(pre_rows)
    pre_df["Activity Score"] = pre_df["% Change"].abs() + pre_df["Vol xAvg"].fillna(0)
    active_df = pre_df.sort_values("Activity Score", ascending=False).head(top_count)

    final_rows = [add_oi_and_action(dict(row)) for _, row in active_df.iterrows()]
    final_df = pd.DataFrame(final_rows)

    st.subheader("🔥 A+ Executable Setups")
    exec_df = final_df[final_df["Action"].str.contains("EXECUTE", na=False)]
    if exec_df.empty:
        st.write("No A+ executable setup right now.")
    else:
        st.dataframe(exec_df[["Stock", "Close", "Buy Score", "Sell Score", "OI Signal", "Action", "Entry", "SL", "TP1"]], use_container_width=True)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.subheader("📈 F&O Top Gainers")
        st.dataframe(final_df.sort_values("% Change", ascending=False).head(10)[["Stock", "Close", "% Change", "Vol xAvg", "OI Signal", "Action"]], use_container_width=True)
    with c2:
        st.subheader("📉 F&O Top Losers")
        st.dataframe(final_df.sort_values("% Change", ascending=True).head(10)[["Stock", "Close", "% Change", "Vol xAvg", "OI Signal", "Action"]], use_container_width=True)
    with c3:
        st.subheader("⚡ Volume Shockers")
        st.dataframe(final_df.sort_values("Vol xAvg", ascending=False).head(10)[["Stock", "Close", "% Change", "Vol xAvg", "OI Signal", "Action"]], use_container_width=True)

    st.subheader("📊 Full Active F&O Scanner")
    show_cols = ["Stock", "Close", "% Change", "Vol xAvg", "Buy Score", "Sell Score", "SSL Sweep", "BSL Sweep", "Bull Disp", "Bear Disp", "OI Signal", "Action", "Entry", "SL", "TP1"]
    st.dataframe(final_df[show_cols], use_container_width=True)

    st.caption(f"Last refreshed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

except Exception as e:
    st.error(f"App error: {e}")
    st.write("Check GitHub repo file path and requirements.txt if this persists.")
