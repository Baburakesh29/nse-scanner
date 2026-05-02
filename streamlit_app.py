import streamlit as st
import pandas as pd
import yfinance as yf

st.set_page_config(page_title="BTST / STBT Radar", layout="wide")

st.title("📊 BTST / STBT Scanner (Live)")

stocks = [
    "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS",
    "AXISBANK.NS", "INFY.NS", "TCS.NS", "ITC.NS",
    "LT.NS", "KOTAKBANK.NS"
]

data = []

for stock in stocks:
    try:
        df = yf.download(stock, period="5d", interval="1d", progress=False)

        if len(df) < 2:
            continue

        last_close = df["Close"].iloc[-1]
        prev_close = df["Close"].iloc[-2]

        change = ((last_close - prev_close) / prev_close) * 100

        action = "BUY" if change > 1 else "SELL" if change < -1 else "WAIT"

        data.append({
            "Stock": stock.replace(".NS",""),
            "Close": round(last_close,2),
            "% Change": round(change,2),
            "Action": action
        })

    except:
        pass

df_final = pd.DataFrame(data)

if not df_final.empty:
    st.dataframe(df_final, use_container_width=True)
else:
    st.warning("No data available")
