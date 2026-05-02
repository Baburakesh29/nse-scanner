import time
from datetime import datetime
import requests
import pandas as pd
import yfinance as yf
import streamlit as st

st.set_page_config(page_title="F&O OI BTST/STBT Scanner", layout="wide")

FNO_STOCKS = [
    "RELIANCE","HDFCBANK","ICICIBANK","SBIN","AXISBANK","INFY","TCS","ITC","LT","KOTAKBANK",
    "ADANIENT","ADANIPORTS","BAJFINANCE","BPCL","HINDUNILVR","MARUTI","NTPC","ONGC","POWERGRID",
    "TATAMOTORS","TATASTEEL","JSWSTEEL","HINDALCO","COALINDIA","SUNPHARMA","CIPLA","DRREDDY",
    "BHARTIARTL","ULTRACEMCO","ASIANPAINT","TITAN","HCLTECH","WIPRO","TECHM","M&M","EICHERMOT",
    "HEROMOTOCO","BAJAJ-AUTO","GRASIM","DLF","INDUSINDBK","BANKBARODA","PNB","CANBK","VEDL","HAL","BEL","IRCTC"
]
MIN_SCORE = 6
VOL_SHOCK_MULTIPLE = 1.5

def make_nse_session():
    session = requests.Session()
    headers = {"User-Agent":"Mozilla/5.0","Accept":"application/json,text/plain,*/*","Accept-Language":"en-US,en;q=0.9","Referer":"https://www.nseindia.com/"}
    try:
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
    except Exception:
        pass
    return session, headers

@st.cache_data(ttl=900, show_spinner=False)
def get_nse_oi(symbol: str):
    session, headers = make_nse_session()
    try:
        r = session.get(f"https://www.nseindia.com/api/quote-derivative?symbol={symbol}", headers=headers, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        stocks = data.get("stocks", [])
        if not stocks:
            return None
        trade_info = stocks[0].get("marketDeptOrderBook", {}).get("tradeInfo", {})
        return {"oi": trade_info.get("openInterest"), "change_oi": trade_info.get("changeinOpenInterest")}
    except Exception:
        return None

@st.cache_data(ttl=900, show_spinner=False)
def get_price_data(symbol: str, interval: str):
    try:
        period = "60d" if interval == "1d" else "30d"
        df = yf.download(symbol + ".NS", period=period, interval=interval, progress=False, threads=False, timeout=20, auto_adjust=False)
        if df is None or df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        return df.dropna()
    except Exception:
        return pd.DataFrame()

def interpret_oi(price_change, oi_change):
    if oi_change is None:
        return "No OI Data"
    try:
        oi_change = float(oi_change)
    except Exception:
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

def scan_stock(symbol: str, interval: str):
    df = get_price_data(symbol, interval)
    min_len = 60 if interval != "1d" else 30
    if df.empty or len(df) < min_len:
        return None
    df["EMA20"] = df["Close"].ewm(span=20).mean()
    df["EMA50"] = df["Close"].ewm(span=50).mean()
    df["VOLMA20"] = df["Volume"].rolling(20).mean()
    last = df.iloc[-1]; prev = df.iloc[-2]
    close = float(last["Close"]); open_ = float(last["Open"]); high = float(last["High"]); low = float(last["Low"])
    volume = float(last["Volume"]); prev_close = float(prev["Close"])
    ema20 = float(last["EMA20"]); ema50 = float(last["EMA50"])
    volma = float(last["VOLMA20"]) if pd.notna(last["VOLMA20"]) else 0
    prev_high = float(df["High"].iloc[-6:-1].max()); prev_low = float(df["Low"].iloc[-6:-1].min()); close_6 = float(df["Close"].iloc[-6])
    pct_change = ((close - prev_close) / prev_close) * 100 if prev_close else 0
    vol_x = volume / volma if volma else 0
    top_gainer_flag = pct_change >= 1.0; top_loser_flag = pct_change <= -1.0; volume_shocker_flag = vol_x >= VOL_SHOCK_MULTIPLE
    ssl_sweep = low < prev_low and close > prev_low; bsl_sweep = high > prev_high and close < prev_high
    bull_displacement = close > open_ and close > prev_high and volume > volma
    bear_displacement = close < open_ and close < prev_low and volume > volma
    buy_score = sum([
        close > ema20 and close > ema50, close >= prev_high * 0.995, volume > volma, close > close_6,
        high > float(df["High"].iloc[-2]) and low > float(df["Low"].iloc[-2]), ssl_sweep, bull_displacement, close > ema20
    ])
    sell_score = sum([
        close < ema20 and close < ema50, close <= prev_low * 1.005 or bsl_sweep, volume > volma, close < close_6,
        high < float(df["High"].iloc[-2]) and low < float(df["Low"].iloc[-2]), bsl_sweep, bear_displacement, close < ema20
    ])
    oi_data = get_nse_oi(symbol); oi_change = oi_data.get("change_oi") if oi_data else None
    oi_signal = interpret_oi(close - prev_close, oi_change)
    action = "NO TRADE"; trade_type = "-"; probability = "Low"
    buy_active = top_gainer_flag or volume_shocker_flag; sell_active = top_loser_flag or volume_shocker_flag
    if buy_score >= MIN_SCORE and buy_active and bull_displacement and oi_signal in ["Long Buildup","Short Covering"]:
        action = "🔥 BTST EXECUTE"; trade_type = "BTST"; probability = "70%+"
    elif sell_score >= MIN_SCORE and sell_active and bear_displacement and oi_signal in ["Short Buildup","Long Unwinding"]:
        action = "🔥 STBT EXECUTE"; trade_type = "STBT"; probability = "70%+"
    elif buy_score >= MIN_SCORE and buy_active:
        action = "⭐ BTST WATCH"; trade_type = "BTST"; probability = "60–65%"
    elif sell_score >= MIN_SCORE and sell_active:
        action = "⭐ STBT WATCH"; trade_type = "STBT"; probability = "60–65%"
    if trade_type == "BTST":
        entry = round(close,2); sl = round(close*0.985,2); tp1 = round(close*1.025,2)
    elif trade_type == "STBT":
        entry = round(close,2); sl = round(close*1.015,2); tp1 = round(close*0.975,2)
    else:
        entry = sl = tp1 = "-"
    return {"Stock":symbol,"Close":round(close,2),"% Chg":round(pct_change,2),"Vol xAvg":round(vol_x,2),
            "Top Gainer":"YES" if top_gainer_flag else "NO","Top Loser":"YES" if top_loser_flag else "NO",
            "Volume Shocker":"YES" if volume_shocker_flag else "NO","OI Signal":oi_signal,"OI Change":oi_change if oi_change is not None else "-",
            "Buy Score":f"{int(buy_score)}/8","Sell Score":f"{int(sell_score)}/8","SSL Sweep":"YES" if ssl_sweep else "NO",
            "BSL Sweep":"YES" if bsl_sweep else "NO","Bull Disp":"YES" if bull_displacement else "NO","Bear Disp":"YES" if bear_displacement else "NO",
            "Action":action,"Probability":probability,"Entry":entry,"SL":sl,"TP1":tp1}

def run_scan(interval: str, selected_stocks):
    results=[]; progress=st.progress(0)
    for i, symbol in enumerate(selected_stocks):
        result = scan_stock(symbol, interval)
        if result: results.append(result)
        progress.progress((i+1)/len(selected_stocks))
    progress.empty()
    return pd.DataFrame(results)

st.title("🔥 NSE F&O OI BTST / STBT Scanner")
st.caption("Includes F&O top gainers, top losers, volume shockers, OI buildup/unwinding, and A+ execution filter.")
col1,col2,col3=st.columns(3)
with col1: interval_label=st.selectbox("Timeframe",["15m","30m","60m","1d"],index=2)
with col2: show_only=st.selectbox("View",["All","A+ Execute Only","Watchlist Only","Volume Shockers","Top Gainers","Top Losers"],index=0)
with col3: max_stocks=st.slider("Max stocks to scan",10,len(FNO_STOCKS),25)
if st.button("🔄 Run Fresh Scan", use_container_width=True): st.cache_data.clear()
with st.spinner("Scanning F&O stocks..."):
    df=run_scan(interval_label, FNO_STOCKS[:max_stocks])
st.write(f"Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
if df.empty:
    st.warning("No data available. If today is a holiday/weekend, try again on next market session.")
else:
    execute_df=df[df["Action"].str.contains("EXECUTE",na=False)]
    watch_df=df[df["Action"].str.contains("WATCH",na=False)]
    shock_df=df[df["Volume Shocker"]=="YES"]
    gainers_df=df[df["Top Gainer"]=="YES"].sort_values("% Chg",ascending=False)
    losers_df=df[df["Top Loser"]=="YES"].sort_values("% Chg",ascending=True)
    st.subheader("🔥 A+ Executable Setups")
    st.dataframe(execute_df, use_container_width=True) if not execute_df.empty else st.info("No A+ executable setups right now.")
    if show_only=="A+ Execute Only": display_df=execute_df
    elif show_only=="Watchlist Only": display_df=watch_df
    elif show_only=="Volume Shockers": display_df=shock_df
    elif show_only=="Top Gainers": display_df=gainers_df
    elif show_only=="Top Losers": display_df=losers_df
    else: display_df=df
    st.subheader("📊 Selected Scanner View")
    st.dataframe(display_df, use_container_width=True)
    c1,c2,c3=st.columns(3)
    with c1:
        st.subheader("🚀 F&O Top Gainers")
        st.dataframe(gainers_df[["Stock","Close","% Chg","Vol xAvg","OI Signal","Action"]].head(10), use_container_width=True)
    with c2:
        st.subheader("🔻 F&O Top Losers")
        st.dataframe(losers_df[["Stock","Close","% Chg","Vol xAvg","OI Signal","Action"]].head(10), use_container_width=True)
    with c3:
        st.subheader("⚡ Volume Shockers")
        st.dataframe(shock_df[["Stock","Close","% Chg","Vol xAvg","OI Signal","Action"]].head(10), use_container_width=True)
    st.download_button("Download Full Scan CSV", df.to_csv(index=False).encode("utf-8"), "fo_oi_scan.csv", "text/csv", use_container_width=True)
