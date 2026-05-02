import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import yfinance as yf

# =========================
# CONFIG
# =========================

# F&O universe. You can add/remove symbols anytime.
STOCKS = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "INFY", "TCS", "ITC", "LT", "KOTAKBANK",
    "ADANIENT", "ADANIPORTS", "BAJFINANCE", "BAJAJFINSV", "BPCL", "HINDUNILVR", "MARUTI", "NTPC", "ONGC", "POWERGRID",
    "TATAMOTORS", "TATASTEEL", "JSWSTEEL", "HINDALCO", "COALINDIA", "ULTRACEMCO", "GRASIM", "CIPLA", "SUNPHARMA", "DRREDDY",
    "DIVISLAB", "APOLLOHOSP", "HEROMOTOCO", "EICHERMOT", "M&M", "BAJAJ-AUTO", "TVSMOTOR", "BHARTIARTL", "INDUSINDBK", "FEDERALBNK",
    "BANKBARODA", "PNB", "CANBK", "IDFCFIRSTB", "AUBANK", "CHOLAFIN", "MUTHOOTFIN", "SBILIFE", "HDFCLIFE", "ICICIPRULI",
    "DLF", "GODREJPROP", "OBEROIRLTY", "ASIANPAINT", "BERGEPAINT", "TITAN", "TRENT", "DMART", "VOLTAS", "CROMPTON",
    "AMBUJACEM", "ACC", "SHREECEM", "INDIGO", "CONCOR", "IRCTC", "IOC", "GAIL", "PETRONET", "VEDL",
    "SAIL", "NMDC", "PEL", "BIOCON", "LUPIN", "ZYDUSLIFE", "TORNTPHARM", "AUROPHARMA", "GLENMARK", "LAURUSLABS",
    "TECHM", "WIPRO", "HCLTECH", "LTIM", "MPHASIS", "PERSISTENT", "COFORGE", "OFSS", "PAGEIND", "ABB",
    "SIEMENS", "HAL", "BEL", "BHEL", "CUMMINSIND", "ASHOKLEY", "ESCORTS", "EXIDEIND", "MOTHERSON", "BOSCHLTD"
]

MIN_SCORE = 6
MAX_OI_CANDIDATES = 15   # Only active candidates get OI requests; keeps GitHub Actions fast
PRICE_PERIOD = "10d"
PRICE_INTERVAL = "60m"

# =========================
# EMAIL
# =========================

def send_email(subject: str, body: str) -> None:
    gmail_user = os.getenv("GMAIL_USER")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD")
    alert_to = os.getenv("ALERT_TO_EMAIL")

    if not gmail_user or not gmail_pass or not alert_to:
        print("Email secrets missing. Skipping email.")
        return

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = alert_to

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
        server.login(gmail_user, gmail_pass)
        server.send_message(msg)

    print("Email sent.")

# =========================
# PRICE / SCORE LOGIC
# =========================

def normalize_float(value):
    try:
        return float(value)
    except Exception:
        return None


def scan_price(symbol: str):
    """Fast price/volume scan using Yahoo. No OI here."""
    try:
        yf_symbol = symbol + ".NS"
        df = yf.download(
            yf_symbol,
            period=PRICE_PERIOD,
            interval=PRICE_INTERVAL,
            progress=False,
            threads=False,
            timeout=15,
            auto_adjust=False,
        )

        if df is None or df.empty or len(df) < 30:
            print(f"{symbol}: insufficient price data")
            return None

        # Handle multi-index columns if yfinance returns them.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0] for col in df.columns]

        df = df.dropna()
        if len(df) < 30:
            return None

        df["EMA20"] = df["Close"].ewm(span=20).mean()
        df["EMA50"] = df["Close"].ewm(span=50).mean()
        df["VOLMA"] = df["Volume"].rolling(20).mean()

        close = normalize_float(df["Close"].iloc[-1])
        open_ = normalize_float(df["Open"].iloc[-1])
        high = normalize_float(df["High"].iloc[-1])
        low = normalize_float(df["Low"].iloc[-1])
        volume = normalize_float(df["Volume"].iloc[-1])
        ema20 = normalize_float(df["EMA20"].iloc[-1])
        ema50 = normalize_float(df["EMA50"].iloc[-1])
        volma = normalize_float(df["VOLMA"].iloc[-1])
        prev_close = normalize_float(df["Close"].iloc[-2])
        close_6 = normalize_float(df["Close"].iloc[-6])

        if None in [close, open_, high, low, volume, ema20, ema50, volma, prev_close, close_6] or volma == 0:
            return None

        prev_high = normalize_float(df["High"].iloc[-6:-1].max())
        prev_low = normalize_float(df["Low"].iloc[-6:-1].min())
        last_high_2 = normalize_float(df["High"].iloc[-2])
        last_low_2 = normalize_float(df["Low"].iloc[-2])

        ssl_sweep = low < prev_low and close > prev_low
        bsl_sweep = high > prev_high and close < prev_high

        bull_displacement = close > open_ and close > prev_high and volume > volma
        bear_displacement = close < open_ and close < prev_low and volume > volma

        pct_change = ((close - prev_close) / prev_close) * 100 if prev_close else 0
        vol_xavg = volume / volma if volma else 0

        buy_score = 0
        sell_score = 0

        buy_score += int(close > ema20 and close > ema50)
        buy_score += int(close >= prev_high * 0.995)
        buy_score += int(volume > volma)
        buy_score += int(close > close_6)
        buy_score += int(high > last_high_2 and low > last_low_2)
        buy_score += int(ssl_sweep)
        buy_score += int(bull_displacement)
        buy_score += int(close > ema20)

        sell_score += int(close < ema20 and close < ema50)
        sell_score += int(close <= prev_low * 1.005 or bsl_sweep)
        sell_score += int(volume > volma)
        sell_score += int(close < close_6)
        sell_score += int(high < last_high_2 and low < last_low_2)
        sell_score += int(bsl_sweep)
        sell_score += int(bear_displacement)
        sell_score += int(close < ema20)

        bucket = []
        if pct_change >= 1.0:
            bucket.append("Top Gainer")
        if pct_change <= -1.0:
            bucket.append("Top Loser")
        if vol_xavg >= 1.5:
            bucket.append("Volume Shocker")
        if buy_score >= 5:
            bucket.append("Buy Bias")
        if sell_score >= 5:
            bucket.append("Sell Bias")

        return {
            "Stock": symbol,
            "Close": round(close, 2),
            "%Chg": round(pct_change, 2),
            "Vol xAvg": round(vol_xavg, 2),
            "Buy Score Num": buy_score,
            "Sell Score Num": sell_score,
            "Buy Score": f"{buy_score}/8",
            "Sell Score": f"{sell_score}/8",
            "SSL Sweep": "YES" if ssl_sweep else "NO",
            "BSL Sweep": "YES" if bsl_sweep else "NO",
            "Bull Disp": "YES" if bull_displacement else "NO",
            "Bear Disp": "YES" if bear_displacement else "NO",
            "Price Change": close - prev_close,
            "Bucket": ", ".join(bucket) if bucket else "Normal",
        }

    except Exception as e:
        print(f"{symbol} price scan failed: {e}")
        return None

# =========================
# NSE OI LOGIC — only shortlisted candidates
# =========================

def get_nse_oi(symbol: str):
    try:
        session = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/",
        }

        session.get("https://www.nseindia.com", headers=headers, timeout=6)
        url = f"https://www.nseindia.com/api/quote-derivative?symbol={symbol}"
        r = session.get(url, headers=headers, timeout=8)

        if r.status_code != 200:
            print(f"{symbol}: NSE OI HTTP {r.status_code}")
            return "No OI Data"

        data = r.json()
        stocks = data.get("stocks", [])
        if not stocks:
            return "No OI Data"

        # Prefer FUTSTK item if available.
        selected = None
        for item in stocks:
            meta = item.get("metadata", {})
            if meta.get("instrumentType") == "Stock Futures":
                selected = item
                break
        if selected is None:
            selected = stocks[0]

        trade_info = selected.get("marketDeptOrderBook", {}).get("tradeInfo", {})
        oi_change = trade_info.get("changeinOpenInterest", None)

        if oi_change is None:
            return "No OI Data"

        return float(oi_change)

    except Exception as e:
        print(f"NSE OI failed for {symbol}: {e}")
        return "No OI Data"


def interpret_oi(price_change, oi_change):
    if oi_change == "No OI Data":
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

# =========================
# FINAL DECISION
# =========================

def add_final_decision(row):
    buy_score = int(row["Buy Score Num"])
    sell_score = int(row["Sell Score Num"])
    oi_signal = row.get("OI Signal", "No OI Data")
    bull_disp = row.get("Bull Disp") == "YES"
    bear_disp = row.get("Bear Disp") == "YES"
    close = float(row["Close"])

    action = "NO TRADE"
    trade_type = "-"
    entry = "-"
    sl = "-"
    tp1 = "-"

    if buy_score >= MIN_SCORE and bull_disp and oi_signal in ["Long Buildup", "Short Covering"]:
        action = "BTST EXECUTE"
        trade_type = "BTST"
        entry = round(close, 2)
        sl = round(close * 0.985, 2)
        tp1 = round(close * 1.025, 2)
    elif sell_score >= MIN_SCORE and bear_disp and oi_signal in ["Short Buildup", "Long Unwinding"]:
        action = "STBT EXECUTE"
        trade_type = "STBT"
        entry = round(close, 2)
        sl = round(close * 1.015, 2)
        tp1 = round(close * 0.975, 2)
    elif buy_score >= MIN_SCORE:
        action = "BUY BIAS - WAIT"
    elif sell_score >= MIN_SCORE:
        action = "SELL BIAS - WAIT"

    row["Action"] = action
    row["Type"] = trade_type
    row["Entry"] = entry
    row["SL"] = sl
    row["TP1"] = tp1
    return row

# =========================
# RUN ONCE FOR GITHUB ACTIONS
# =========================

def run_scan():
    print("Starting optimized F&O OI scanner...")

    # 1) Fast price scan in parallel
    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(scan_price, symbol): symbol for symbol in STOCKS}
        for future in as_completed(futures):
            res = future.result()
            if res:
                results.append(res)

    if not results:
        print("No price scan results.")
        return

    df = pd.DataFrame(results)

    # 2) Shortlist active names for OI only
    df["Activity Score"] = (
        df["%Chg"].abs() * 2
        + df["Vol xAvg"]
        + df["Buy Score Num"] * 0.5
        + df["Sell Score Num"] * 0.5
    )

    active_df = df.sort_values("Activity Score", ascending=False).head(MAX_OI_CANDIDATES).copy()
    active_symbols = set(active_df["Stock"].tolist())
    print(f"Fetching OI for top {len(active_symbols)} active candidates: {sorted(active_symbols)}")

    oi_map = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(get_nse_oi, symbol): symbol for symbol in active_symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            oi_map[symbol] = future.result()

    df["OI Change"] = df["Stock"].map(oi_map).fillna("Not Checked")
    df["OI Signal"] = df.apply(
        lambda r: interpret_oi(r["Price Change"], r["OI Change"]) if r["OI Change"] != "Not Checked" else "Not Checked",
        axis=1,
    )

    df = df.apply(add_final_decision, axis=1)

    output_cols = [
        "Stock", "Close", "%Chg", "Vol xAvg", "Bucket", "Buy Score", "Sell Score",
        "SSL Sweep", "BSL Sweep", "Bull Disp", "Bear Disp", "OI Signal", "Action", "Entry", "SL", "TP1"
    ]
    out = df[output_cols].sort_values(["Action", "%Chg"], ascending=[True, False])

    print("\nOptimized F&O Scanner Results:\n")
    print(out.to_string(index=False))
    out.to_csv("scanner_results.csv", index=False)

    execute_df = out[out["Action"].isin(["BTST EXECUTE", "STBT EXECUTE"])]
    if execute_df.empty:
        print("No executable trades found. No email sent.")
        return

    body = "🔥 EXECUTABLE BTST / STBT SETUPS FOUND\n\n"
    body += execute_df.to_string(index=False)
    body += f"\n\nScan time: {datetime.now()}"

    send_email("🔥 NSE F&O BTST/STBT Execute Alert", body)
    print("Scan completed with executable alerts.")


if __name__ == "__main__":
    run_scan()
