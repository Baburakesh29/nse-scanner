import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import requests
import pandas as pd
import yfinance as yf

STOCKS = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK",
    "INFY", "TCS", "ITC", "LT", "KOTAKBANK",
    "ADANIENT", "ADANIPORTS", "BAJFINANCE", "BPCL",
    "HINDUNILVR", "MARUTI", "NTPC", "ONGC", "POWERGRID"
]

MIN_SCORE = 6


def send_email(subject, body):
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


def get_nse_oi(symbol):
    try:
        session = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }

        session.get("https://www.nseindia.com", headers=headers, timeout=10)

        url = f"https://www.nseindia.com/api/quote-derivative?symbol={symbol}"
        r = session.get(url, headers=headers, timeout=15)

        if r.status_code != 200:
            return "No OI Data"

        data = r.json()

        stocks = data.get("stocks", [])
        if not stocks:
            return "No OI Data"

        trade_info = stocks[0].get("marketDeptOrderBook", {}).get("tradeInfo", {})

        oi_change = trade_info.get("changeinOpenInterest", 0)

        if oi_change is None:
            return "No OI Data"

        return float(oi_change)

    except Exception as e:
        print(f"NSE OI failed for {symbol}: {e}")
        return "No OI Data"


def interpret_oi(price_change, oi_change):
    if oi_change == "No OI Data":
        return "No OI Data"

    if price_change > 0 and oi_change > 0:
        return "Long Buildup"
    elif price_change < 0 and oi_change > 0:
        return "Short Buildup"
    elif price_change > 0 and oi_change < 0:
        return "Short Covering"
    elif price_change < 0 and oi_change < 0:
        return "Long Unwinding"
    else:
        return "Neutral"


def scan_stock(symbol):
    try:
        yf_symbol = symbol + ".NS"

        df = yf.download(
            yf_symbol,
            period="30d",
            interval="60m",
            progress=False,
            threads=False,
            timeout=20,
        )

        if df is None or df.empty or len(df) < 60:
            print(f"{symbol}: insufficient price data")
            return None

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
        prev_close = float(df["Close"].iloc[-2])
        close_6 = float(df["Close"].iloc[-6])

        ssl_sweep = low < prev_low and close > prev_low
        bsl_sweep = high > prev_high and close < prev_high

        bull_displacement = close > open_ and close > prev_high and volume > volma
        bear_displacement = close < open_ and close < prev_low and volume > volma

        buy_score = 0
        sell_score = 0

        buy_score += int(close > ema20 and close > ema50)
        buy_score += int(close >= prev_high * 0.995)
        buy_score += int(volume > volma)
        buy_score += int(close > close_6)
        buy_score += int(high > float(df["High"].iloc[-2]) and low > float(df["Low"].iloc[-2]))
        buy_score += int(ssl_sweep)
        buy_score += int(bull_displacement)
        buy_score += int(close > ema20)

        sell_score += int(close < ema20 and close < ema50)
        sell_score += int(close <= prev_low * 1.005 or bsl_sweep)
        sell_score += int(volume > volma)
        sell_score += int(close < close_6)
        sell_score += int(high < float(df["High"].iloc[-2]) and low < float(df["Low"].iloc[-2]))
        sell_score += int(bsl_sweep)
        sell_score += int(bear_displacement)
        sell_score += int(close < ema20)

        price_change = close - prev_close
        oi_change = get_nse_oi(symbol)
        oi_signal = interpret_oi(price_change, oi_change)

        action = "NO TRADE"
        trade_type = "-"

        if buy_score >= MIN_SCORE and bull_displacement and oi_signal in ["Long Buildup", "Short Covering"]:
            action = "BTST EXECUTE"
            trade_type = "BTST"

        elif sell_score >= MIN_SCORE and bear_displacement and oi_signal in ["Short Buildup", "Long Unwinding"]:
            action = "STBT EXECUTE"
            trade_type = "STBT"

        entry = close
        if trade_type == "BTST":
            sl = round(close * 0.985, 2)
            tp1 = round(close * 1.025, 2)
        elif trade_type == "STBT":
            sl = round(close * 1.015, 2)
            tp1 = round(close * 0.975, 2)
        else:
            sl = "-"
            tp1 = "-"

        return {
            "Stock": symbol,
            "Close": round(close, 2),
            "Buy Score": f"{buy_score}/8",
            "Sell Score": f"{sell_score}/8",
            "SSL Sweep": "YES" if ssl_sweep else "NO",
            "BSL Sweep": "YES" if bsl_sweep else "NO",
            "Bull Disp": "YES" if bull_displacement else "NO",
            "Bear Disp": "YES" if bear_displacement else "NO",
            "OI Signal": oi_signal,
            "Action": action,
            "Entry": round(entry, 2) if trade_type != "-" else "-",
            "SL": sl,
            "TP1": tp1,
        }

    except Exception as e:
        print(f"{symbol} scan failed: {e}")
        return None


def run_scan():
    print("Starting NSE OI scanner...")

    results = []

    for symbol in STOCKS:
        result = scan_stock(symbol)
        if result:
            results.append(result)

    if not results:
        print("No scan results.")
        return

    df = pd.DataFrame(results)
    print(df.to_string(index=False))

    df.to_csv("scanner_results.csv", index=False)

    execute_df = df[df["Action"].isin(["BTST EXECUTE", "STBT EXECUTE"])]

    if execute_df.empty:
        print("No executable trades found. No email sent.")
        return

    body = "🔥 EXECUTABLE BTST / STBT SETUPS FOUND\n\n"
    body += execute_df.to_string(index=False)
    body += f"\n\nScan time: {datetime.now()}"

    send_email("🔥 NSE BTST/STBT Execute Alert", body)

    print("Scan completed.")


if __name__ == "__main__":
    run_scan()
