import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime

st.set_page_config(page_title="BTST / STBT Radar", layout="wide")

STOCKS = [
    "ADANIENT.NS", "ADANIPORTS.NS", "AXISBANK.NS", "BAJFINANCE.NS", "BPCL.NS",
    "HDFCBANK.NS", "HINDUNILVR.NS", "ICICIBANK.NS", "INFY.NS", "ITC.NS",
    "KOTAKBANK.NS", "LT.NS", "MARUTI.NS", "NTPC.NS", "ONGC.NS",
    "POWERGRID.NS", "RELIANCE.NS", "SBIN.NS", "TCS.NS"
]

MIN_SCORE = 6

st.title("📊 BTST / STBT Radar")
st.caption("Mobile-friendly dashboard for Indian market scanner logic")

interval = st.selectbox("Timeframe", ["15m", "30m", "60m", "1d"], index=2)
period = "60d" if interval == "1d" else "30d"
min_score = st.slider("Minimum score", 1, 8, MIN_SCORE)

@st.cache_data(ttl=900)
def download_data(symbol: str, period: str, interval: str):
    return yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=False)

def scan_stock(symbol: str):
    try:
        df = download_data(symbol, period, interval)
        if df.empty or len(df) < 60:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df["EMA20"] = df["Close"].ewm(span=20).mean()
        df["EMA50"] = df["Close"].ewm(span=50).mean()
        df["VOLMA"] = df["Volume"].rolling(20).mean()

        close = float(df["Close"].iloc[-1])
        open_ = float(df["Open"].iloc[-1])
        high = float(df["High"].iloc[-1])
        low = float(df["Low"].iloc[-1])
        volume = float(df["Volume"].iloc[-1])

        ema20 = float(df["EMA20"].iloc[-1])
        ema50 = float(df["EMA50"].iloc[-1])
        volma = float(df["VOLMA"].iloc[-1])

        prev_high = float(df["High"].iloc[-6:-1].max())
        prev_low = float(df["Low"].iloc[-6:-1].min())

        ssl_sweep = low < prev_low and close > prev_low
        bsl_sweep = high > prev_high and close < prev_high
        bull_disp = close > open_ and close > prev_high and volume > volma
        bear_disp = close < open_ and close < prev_low and volume > volma

        buy_score = 0
        buy_score += int(close > ema20 and close > ema50)
        buy_score += int(close >= prev_high * 0.995)
        buy_score += int(volume > volma)
        buy_score += int(close > float(df["Close"].iloc[-6]))
        buy_score += int(high > float(df["High"].iloc[-2]) and low > float(df["Low"].iloc[-2]))
        buy_score += int(ssl_sweep)
        buy_score += int(bull_disp)
        buy_score += int(close > ema20)

        sell_score = 0
        sell_score += int(close < ema20 and close < ema50)
        sell_score += int(close <= prev_low * 1.005 or bsl_sweep)
        sell_score += int(volume > volma)
        sell_score += int(close < float(df["Close"].iloc[-6]))
        sell_score += int(high < float(df["High"].iloc[-2]) and low < float(df["Low"].iloc[-2]))
        sell_score += int(bsl_sweep)
        sell_score += int(bear_disp)
        sell_score += int(close < ema20)

        buy_execute = buy_score >= min_score and ssl_sweep and bull_disp
        sell_execute = sell_score >= min_score and bsl_sweep and bear_disp

        if buy_execute and not sell_execute:
            action = "🔥 BTST EXECUTE"
            probability = "70%+"
        elif sell_execute and not buy_execute:
            action = "🔥 STBT EXECUTE"
            probability = "70%+"
        elif buy_score >= min_score and buy_score > sell_score:
            action = "🟡 BUY BIAS - WAIT"
            probability = "Watch"
        elif sell_score >= min_score and sell_score > buy_score:
            action = "🟡 SELL BIAS - WAIT"
            probability = "Watch"
        else:
            action = "❌ NO TRADE"
            probability = "Low"

        return {
            "Stock": symbol.replace(".NS", ""),
            "Close": round(close, 2),
            "Buy Score": f"{buy_score}/8",
            "Sell Score": f"{sell_score}/8",
            "SSL Sweep": "YES" if ssl_sweep else "NO",
            "BSL Sweep": "YES" if bsl_sweep else "NO",
            "Bull Disp": "YES" if bull_disp else "NO",
            "Bear Disp": "YES" if bear_disp else "NO",
            "Action": action,
            "Probability": probability,
        }
    except Exception as e:
        return {"Stock": symbol.replace(".NS", ""), "Close": None, "Buy Score": "-", "Sell Score": "-", "SSL Sweep": "-", "BSL Sweep": "-", "Bull Disp": "-", "Bear Disp": "-", "Action": f"Error: {e}", "Probability": "-"}

if st.button("🔄 Run Scan") or True:
    rows = []
    progress = st.progress(0)
    for i, symbol in enumerate(STOCKS):
        result = scan_stock(symbol)
        if result:
            rows.append(result)
        progress.progress((i + 1) / len(STOCKS))

    df = pd.DataFrame(rows)

    st.subheader("Scanner Results")
    st.write(f"Last refresh: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    execute_df = df[df["Action"].str.contains("EXECUTE", na=False)]
    watch_df = df[df["Action"].str.contains("WAIT", na=False)]

    if not execute_df.empty:
        st.error("🔥 Executable setups found")
        st.dataframe(execute_df, use_container_width=True, hide_index=True)
    else:
        st.success("No executable trade right now")

    if not watch_df.empty:
        st.warning("Watchlist candidates")
        st.dataframe(watch_df, use_container_width=True, hide_index=True)

    st.dataframe(df, use_container_width=True, hide_index=True)
