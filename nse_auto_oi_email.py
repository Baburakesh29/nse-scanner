"""
BTST / STBT NSE Auto Scanner with OI + Email Alerts
---------------------------------------------------
How to run:
    python nse_auto_oi_email.py

Email setup uses Windows environment variables:
    GMAIL_USER
    GMAIL_APP_PASSWORD
    ALERT_TO_EMAIL

Example CMD commands:
    setx GMAIL_USER "yourgmail@gmail.com"
    setx GMAIL_APP_PASSWORD "your_app_password"
    setx ALERT_TO_EMAIL "yourgmail@gmail.com"

After setx, close CMD and open a new CMD.
"""

import os
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import pandas as pd
import yfinance as yf
import requests
import schedule

# =========================
# SETTINGS
# =========================
SCAN_INTERVAL_MINUTES = 15
MIN_SCORE = 6

STOCKS = [
    "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS",
    "SBIN.NS", "BPCL.NS", "ITC.NS", "LT.NS", "AXISBANK.NS",
    "KOTAKBANK.NS", "ADANIENT.NS", "ADANIPORTS.NS", "BAJFINANCE.NS",
    "BHARTIARTL.NS", "HINDUNILVR.NS", "MARUTI.NS", "TATASTEEL.NS",
    "SUNPHARMA.NS", "ULTRACEMCO.NS", "POWERGRID.NS", "NTPC.NS",
    "ONGC.NS", "COALINDIA.NS", "JSWSTEEL.NS", "HCLTECH.NS", "WIPRO.NS"
]

RESULTS_CSV = "scanner_results.csv"
ALERT_LOG_CSV = "alert_log.csv"

# =========================
# NSE SESSION FOR OI
# =========================
nse_session = requests.Session()
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}


def init_nse_session():
    try:
        nse_session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
    except Exception:
        pass


def get_nse_oi_data(symbol_without_ns):
    """Returns OI data from NSE derivative quote endpoint for F&O names.
    This is unofficial and can occasionally fail if NSE blocks/changes endpoint.
    """
    try:
        init_nse_session()
        url = f"https://www.nseindia.com/api/quote-derivative?symbol={symbol_without_ns}"
        response = nse_session.get(url, headers=NSE_HEADERS, timeout=12)
        if response.status_code != 200:
            return None
        data = response.json()
        stocks = data.get("stocks", [])
        if not stocks:
            return None

        # Prefer Futures instrument if present
        chosen = None
        for item in stocks:
            meta = item.get("metadata", {})
            instrument = str(meta.get("instrumentType", "")).lower()
            if "future" in instrument:
                chosen = item
                break
        if chosen is None:
            chosen = stocks[0]

        trade_info = chosen.get("marketDeptOrderBook", {}).get("tradeInfo", {})
        metadata = chosen.get("metadata", {})

        oi = trade_info.get("openInterest")
        change_oi = trade_info.get("changeinOpenInterest")
        last_price = metadata.get("lastPrice") or trade_info.get("lastPrice")

        return {
            "oi": float(oi) if oi is not None else None,
            "change_oi": float(change_oi) if change_oi is not None else None,
            "fno_price": float(last_price) if last_price is not None else None,
        }
    except Exception:
        return None


def interpret_oi(price_change, oi_change):
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

# =========================
# TECHNICAL LOGIC
# =========================

def scan_stock(symbol):
    try:
        df = yf.download(symbol, period="30d", interval="60m", progress=False, auto_adjust=False)
        if df.empty or len(df) < 60:
            return None

        # Flatten multi-index columns if yfinance returns them
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
        df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
        df["VOLMA"] = df["Volume"].rolling(20).mean()

        close = float(df["Close"].iloc[-1])
        open_ = float(df["Open"].iloc[-1])
        high = float(df["High"].iloc[-1])
        low = float(df["Low"].iloc[-1])
        volume = float(df["Volume"].iloc[-1])

        prev_close = float(df["Close"].iloc[-2])
        close_5_back = float(df["Close"].iloc[-6])
        ema20 = float(df["EMA20"].iloc[-1])
        ema50 = float(df["EMA50"].iloc[-1])
        volma = float(df["VOLMA"].iloc[-1]) if pd.notna(df["VOLMA"].iloc[-1]) else 0

        prev_high = float(df["High"].iloc[-6:-1].max())
        prev_low = float(df["Low"].iloc[-6:-1].min())
        prev_bar_high = float(df["High"].iloc[-2])
        prev_bar_low = float(df["Low"].iloc[-2])

        ssl_sweep = low < prev_low and close > prev_low
        bsl_sweep = high > prev_high and close < prev_high

        bull_displacement = close > open_ and close > prev_high and volume > volma
        bear_displacement = close < open_ and close < prev_low and volume > volma

        buy_score = 0
        sell_score = 0

        # BUY model: 8 parameters
        buy_score += 1 if close > ema20 and close > ema50 else 0
        buy_score += 1 if close >= prev_high * 0.995 else 0
        buy_score += 1 if volume > volma else 0
        buy_score += 1 if close > close_5_back else 0
        buy_score += 1 if high > prev_bar_high and low > prev_bar_low else 0
        buy_score += 1 if ssl_sweep else 0
        buy_score += 1 if bull_displacement else 0
        buy_score += 1 if close > ema20 else 0

        # SELL model: 8 parameters
        sell_score += 1 if close < ema20 and close < ema50 else 0
        sell_score += 1 if close <= prev_low * 1.005 or bsl_sweep else 0
        sell_score += 1 if volume > volma else 0
        sell_score += 1 if close < close_5_back else 0
        sell_score += 1 if high < prev_bar_high and low < prev_bar_low else 0
        sell_score += 1 if bsl_sweep else 0
        sell_score += 1 if bear_displacement else 0
        sell_score += 1 if close < ema20 else 0

        stock_name = symbol.replace(".NS", "")
        oi_data = get_nse_oi_data(stock_name)
        price_change = close - prev_close
        oi_type = "No OI Data"
        oi = None
        change_oi = None
        if oi_data:
            oi = oi_data.get("oi")
            change_oi = oi_data.get("change_oi")
            oi_type = interpret_oi(price_change, change_oi)

        buy_execute = buy_score >= MIN_SCORE and ssl_sweep and bull_displacement and oi_type in ["Long Buildup", "Short Covering"]
        sell_execute = sell_score >= MIN_SCORE and bsl_sweep and bear_displacement and oi_type in ["Short Buildup", "Long Unwinding"]

        if buy_execute and not sell_execute:
            action = "🔥 BTST EXECUTE"
            probability = "70%+"
            entry = round(close, 2)
            sl = round(min(prev_low, low), 2)
            tp1 = round(close + (close - sl), 2) if close > sl else round(close * 1.01, 2)
        elif sell_execute and not buy_execute:
            action = "🔥 STBT EXECUTE"
            probability = "70%+"
            entry = round(close, 2)
            sl = round(max(prev_high, high), 2)
            tp1 = round(close - (sl - close), 2) if sl > close else round(close * 0.99, 2)
        elif buy_score >= MIN_SCORE and sell_score >= MIN_SCORE:
            action = "⚠️ CONFLICT / SKIP"
            probability = "Avoid"
            entry = sl = tp1 = "-"
        elif buy_score >= MIN_SCORE:
            action = "BUY BIAS - WAIT"
            probability = "Watch"
            entry = sl = tp1 = "-"
        elif sell_score >= MIN_SCORE:
            action = "SELL BIAS - WAIT"
            probability = "Watch"
            entry = sl = tp1 = "-"
        else:
            action = "NO TRADE"
            probability = "Low"
            entry = sl = tp1 = "-"

        return {
            "Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Stock": stock_name,
            "Close": round(close, 2),
            "Buy Score": f"{buy_score}/8",
            "Sell Score": f"{sell_score}/8",
            "SSL Sweep": "YES" if ssl_sweep else "NO",
            "BSL Sweep": "YES" if bsl_sweep else "NO",
            "Bull Disp": "YES" if bull_displacement else "NO",
            "Bear Disp": "YES" if bear_displacement else "NO",
            "OI Type": oi_type,
            "OI": int(oi) if oi is not None else "-",
            "Change OI": int(change_oi) if change_oi is not None else "-",
            "Action": action,
            "Probability": probability,
            "Entry": entry,
            "SL": sl,
            "TP1": tp1,
        }
    except Exception as e:
        return {
            "Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Stock": symbol.replace(".NS", ""),
            "Close": "-",
            "Buy Score": "-",
            "Sell Score": "-",
            "SSL Sweep": "-",
            "BSL Sweep": "-",
            "Bull Disp": "-",
            "Bear Disp": "-",
            "OI Type": "Error",
            "OI": "-",
            "Change OI": "-",
            "Action": f"ERROR: {str(e)[:60]}",
            "Probability": "-",
            "Entry": "-",
            "SL": "-",
            "TP1": "-",
        }

# =========================
# EMAIL LOGIC
# =========================

def load_alerted_keys():
    if not os.path.exists(ALERT_LOG_CSV):
        return set()
    try:
        df = pd.read_csv(ALERT_LOG_CSV)
        return set(df["AlertKey"].astype(str).tolist())
    except Exception:
        return set()


def save_alert_key(key):
    row = pd.DataFrame([{"Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "AlertKey": key}])
    if os.path.exists(ALERT_LOG_CSV):
        row.to_csv(ALERT_LOG_CSV, mode="a", header=False, index=False)
    else:
        row.to_csv(ALERT_LOG_CSV, index=False)


def send_email(subject, body):
    gmail_user = os.getenv("GMAIL_USER")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD")
    alert_to = os.getenv("ALERT_TO_EMAIL")

    if not gmail_user or not gmail_password or not alert_to:
        print("Email not sent: missing GMAIL_USER / GMAIL_APP_PASSWORD / ALERT_TO_EMAIL environment variables.")
        return False

    try:
        msg = MIMEMultipart()
        msg["From"] = gmail_user
        msg["To"] = alert_to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, alert_to, msg.as_string())
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False


def maybe_send_alerts(results_df):
    execute_df = results_df[results_df["Action"].astype(str).str.contains("EXECUTE", na=False)]
    if execute_df.empty:
        print("No executable setup. No email sent.")
        return

    alerted = load_alerted_keys()
    for _, row in execute_df.iterrows():
        # Avoid repeated alerts for same stock/action/entry on same date
        alert_key = f"{datetime.now().strftime('%Y-%m-%d')}|{row['Stock']}|{row['Action']}|{row['Entry']}"
        if alert_key in alerted:
            continue

        subject = f"{row['Action']} - {row['Stock']}"
        body = f"""🔥 EXECUTE TRADE FOUND

Stock: {row['Stock']}
Action: {row['Action']}
Probability: {row['Probability']}
Close: {row['Close']}
Entry: {row['Entry']}
SL: {row['SL']}
TP1: {row['TP1']}

Buy Score: {row['Buy Score']}
Sell Score: {row['Sell Score']}
SSL Sweep: {row['SSL Sweep']}
BSL Sweep: {row['BSL Sweep']}
Bull Displacement: {row['Bull Disp']}
Bear Displacement: {row['Bear Disp']}
OI Type: {row['OI Type']}
OI: {row['OI']}
Change OI: {row['Change OI']}

Time: {row['Time']}

Rule: execute only after confirming chart structure manually.
"""
        sent = send_email(subject, body)
        if sent:
            print(f"Email sent: {row['Stock']} {row['Action']}")
            save_alert_key(alert_key)

# =========================
# MAIN SCAN
# =========================

def run_scan():
    print("\n" + "=" * 80)
    print(f"Running BTST/STBT scan at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    results = []
    for stock in STOCKS:
        result = scan_stock(stock)
        if result:
            results.append(result)
            print(f"{result['Stock']:<12} BUY {result['Buy Score']:<4} SELL {result['Sell Score']:<4} OI {result['OI Type']:<16} {result['Action']}")

    if not results:
        print("No results produced.")
        return

    df = pd.DataFrame(results)
    df.to_csv(RESULTS_CSV, index=False)
    print(f"\nSaved latest results to: {RESULTS_CSV}")

    maybe_send_alerts(df)


if __name__ == "__main__":
    run_scan()
    print(f"\nScanner started... running every {SCAN_INTERVAL_MINUTES} minutes")
    print("Keep this window open. Press CTRL+C to stop.")
    schedule.every(SCAN_INTERVAL_MINUTES).minutes.do(run_scan)
    while True:
        schedule.run_pending()
        time.sleep(1)
