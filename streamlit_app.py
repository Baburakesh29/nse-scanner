
import time
from datetime import datetime
import requests
import pandas as pd
import yfinance as yf
import streamlit as st

st.set_page_config(page_title="NSE OI BTST/STBT Scanner", layout="wide")

# -----------------------------
# CONFIG
# -----------------------------
STOCKS = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK",
    "INFY", "TCS", "ITC", "LT", "KOTAKBANK", "ADANIENT",
    "ADANIPORTS", "BAJFINANCE", "BPCL", "HINDUNILVR",
    "MARUTI", "NTPC", "ONGC", "POWERGRID"
]

MIN_SCORE = 6

# -----------------------------
# HELPERS
# -----------------------------
@st.cache_data(ttl=300, show_spinner=False)
def fetch_yfinance(symbol: str, interval: str = "60m", period: str = "30d") -> pd.DataFrame:
    """Fetch OHLCV from Yahoo Finance."""
    ticker = f"{symbol}.NS"
    df = yf.download(
        ticker,
        period=period,
        interval=interval,
        progress=False,
        threads=False,
        auto_adjust=False,
    )
    if df is None or df.empty:
        return pd.DataFrame()

    # yfinance may return MultiIndex columns in some versions
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    df = df.dropna()
    return df


def nse_session():
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/123.0 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/",
        "Connection": "keep-alive",
    }
    try:
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
    except Exception:
        pass
    return session, headers


@st.cache_data(ttl=300, show_spinner=False)
def fetch_nse_oi(symbol: str):
    """Fetch derivative OI info from NSE unofficial quote-derivative endpoint.
    Returns best-effort aggregated tradeInfo from response.
    """
    session, headers = nse_session()
    url = f"https://www.nseindia.com/api/quote-derivative?symbol={symbol}"
    try:
        r = session.get(url, headers=headers, timeout=12)
        if r.status_code != 200:
            return {"oi_signal": "No OI Data", "oi": None, "change_oi": None, "oi_note": f"HTTP {r.status_code}"}
        data = r.json()

        stocks = data.get("stocks", [])
        if not stocks:
            return {"oi_signal": "No OI Data", "oi": None, "change_oi": None, "oi_note": "No stocks node"}

        # Prefer futures-like instruments when present; fall back to first item
        chosen = None
        for item in stocks:
            meta = item.get("metadata", {})
            if str(meta.get("instrumentType", "")).upper().find("FUT") >= 0:
                chosen = item
                break
        if chosen is None:
            chosen = stocks[0]

        trade_info = chosen.get("marketDeptOrderBook", {}).get("tradeInfo", {})
        oi = trade_info.get("openInterest")
        change_oi = trade_info.get("changeinOpenInterest")

        try:
            oi = float(oi) if oi is not None else None
        except Exception:
            oi = None
        try:
            change_oi = float(change_oi) if change_oi is not None else None
        except Exception:
            change_oi = None

        return {"oi_signal": "Fetched", "oi": oi, "change_oi": change_oi, "oi_note": ""}
    except Exception as e:
        return {"oi_signal": "No OI Data", "oi": None, "change_oi": None, "oi_note": str(e)[:80]}


def interpret_oi(price_change: float, oi_change):
    if oi_change is None:
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


def score_stock(symbol: str, interval: str, period: str):
    df = fetch_yfinance(symbol, interval, period)

    if df.empty or len(df) < 60:
        return {
            "Stock": symbol, "Price": None, "Buy": "0/8", "Sell": "0/8",
            "SSL Sweep": "NO", "BSL Sweep": "NO", "Bull Disp": "NO", "Bear Disp": "NO",
            "OI Signal": "No Price Data", "Action": "❌ NO TRADE",
            "Probability": "Low", "Entry": "-", "SL": "-", "TP1": "-"
        }

    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["VOLMA"] = df["Volume"].rolling(20).mean()

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    close = float(latest["Close"])
    open_ = float(latest["Open"])
    high = float(latest["High"])
    low = float(latest["Low"])
    volume = float(latest["Volume"])
    ema20 = float(latest["EMA20"])
    ema50 = float(latest["EMA50"])
    volma = float(latest["VOLMA"]) if pd.notna(latest["VOLMA"]) else 0

    prev_high = float(df["High"].iloc[-6:-1].max())
    prev_low = float(df["Low"].iloc[-6:-1].min())
    price_change = close - float(prev["Close"])

    ssl_sweep = low < prev_low and close > prev_low
    bsl_sweep = high > prev_high and close < prev_high

    bull_displacement = close > open_ and close > prev_high and volume > volma
    bear_displacement = close < open_ and close < prev_low and volume > volma

    buy_score = 0
    sell_score = 0

    buy_score += int(close > ema20 and close > ema50)
    buy_score += int(close >= prev_high * 0.995)
    buy_score += int(volume > volma)
    buy_score += int(close > float(df["Close"].iloc[-6]))
    buy_score += int(high > float(prev["High"]) and low > float(prev["Low"]))
    buy_score += int(ssl_sweep)
    buy_score += int(bull_displacement)
    buy_score += int(close > ema20)

    sell_score += int(close < ema20 and close < ema50)
    sell_score += int(close <= prev_low * 1.005 or bsl_sweep)
    sell_score += int(volume > volma)
    sell_score += int(close < float(df["Close"].iloc[-6]))
    sell_score += int(high < float(prev["High"]) and low < float(prev["Low"]))
    sell_score += int(bsl_sweep)
    sell_score += int(bear_displacement)
    sell_score += int(close < ema20)

    oi = fetch_nse_oi(symbol)
    oi_signal = interpret_oi(price_change, oi.get("change_oi"))

    buy_oi_ok = oi_signal in ["Long Buildup", "Short Covering"]
    sell_oi_ok = oi_signal in ["Short Buildup", "Long Unwinding"]

    action = "❌ NO TRADE"
    probability = "Low"
    entry, sl, tp1 = "-", "-", "-"

    if buy_score >= MIN_SCORE and ssl_sweep and bull_displacement and buy_oi_ok:
        action = "🔥 BTST EXECUTE"
        probability = "70%+"
        entry = f"{round(close, 2)}+"
        sl = round(min(prev_low, low), 2)
        tp1 = round(close + (close - float(sl)) * 1.5, 2)
    elif sell_score >= MIN_SCORE and bsl_sweep and bear_displacement and sell_oi_ok:
        action = "🔥 STBT EXECUTE"
        probability = "70%+"
        entry = f"{round(close, 2)}-"
        sl = round(max(prev_high, high), 2)
        tp1 = round(close - (float(sl) - close) * 1.5, 2)
    elif buy_score >= MIN_SCORE and buy_oi_ok:
        action = "⭐ BTST WATCH"
        probability = "65% Watch"
    elif sell_score >= MIN_SCORE and sell_oi_ok:
        action = "⭐ STBT WATCH"
        probability = "65% Watch"
    elif buy_score >= 5:
        action = "⚠️ BUY BIAS - WAIT"
        probability = "Watch"
    elif sell_score >= 5:
        action = "⚠️ SELL BIAS - WAIT"
        probability = "Watch"

    return {
        "Stock": symbol,
        "Price": round(close, 2),
        "Buy": f"{buy_score}/8",
        "Sell": f"{sell_score}/8",
        "SSL Sweep": "YES" if ssl_sweep else "NO",
        "BSL Sweep": "YES" if bsl_sweep else "NO",
        "Bull Disp": "YES" if bull_displacement else "NO",
        "Bear Disp": "YES" if bear_displacement else "NO",
        "OI Signal": oi_signal,
        "OI Chg": oi.get("change_oi"),
        "Action": action,
        "Probability": probability,
        "Entry": entry,
        "SL": sl,
        "TP1": tp1
    }


# -----------------------------
# UI
# -----------------------------
st.title("🔥 NSE OI BTST / STBT Scanner")
st.caption("Trader X style: score + liquidity sweep + displacement + OI confirmation")

col1, col2, col3 = st.columns(3)
with col1:
    interval = st.selectbox("Timeframe", ["15m", "30m", "60m", "1d"], index=2)
with col2:
    min_score_ui = st.slider("Minimum score for watch", 1, 8, MIN_SCORE)
with col3:
    aplus_only = st.toggle("Show A+ / executable only", value=False)

# use selected score globally in this run
MIN_SCORE = min_score_ui
period = "60d" if interval == "1d" else "30d"

if st.button("🔄 Refresh scan"):
    st.cache_data.clear()
    st.rerun()

st.info("If market is closed or NSE/Yahoo blocks data temporarily, refresh after a few minutes or on next market session.")

with st.spinner("Scanning price, volume and OI..."):
    rows = [score_stock(s, interval, period) for s in STOCKS]

df = pd.DataFrame(rows)

st.subheader("📌 A+ / High Probability Setups")
a_df = df[df["Action"].str.contains("EXECUTE", na=False)]
if a_df.empty:
    st.warning("No executable A+ setup right now.")
else:
    st.dataframe(a_df, use_container_width=True)

st.subheader("📊 Full Scanner")
show_df = df.copy()
if aplus_only:
    show_df = show_df[show_df["Action"].str.contains("EXECUTE|WATCH", na=False)]

st.dataframe(show_df, use_container_width=True)

st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} local app time")
st.caption("Note: NSE OI endpoint is unofficial and may occasionally fail or rate-limit. Use broker/premium data for production trading.")
