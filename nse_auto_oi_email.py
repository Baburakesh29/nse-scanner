import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import requests
import pandas as pd
import yfinance as yf

FNO_STOCKS = [
    "RELIANCE","HDFCBANK","ICICIBANK","SBIN","AXISBANK","INFY","TCS","ITC","LT","KOTAKBANK",
    "ADANIENT","ADANIPORTS","BAJFINANCE","BPCL","HINDUNILVR","MARUTI","NTPC","ONGC","POWERGRID",
    "TATAMOTORS","TATASTEEL","JSWSTEEL","HINDALCO","COALINDIA","SUNPHARMA","CIPLA","DRREDDY",
    "BHARTIARTL","ULTRACEMCO","ASIANPAINT","TITAN","HCLTECH","WIPRO","TECHM","M&M","EICHERMOT",
    "HEROMOTOCO","BAJAJ-AUTO","GRASIM","DLF","INDUSINDBK","BANKBARODA","PNB","CANBK","VEDL","HAL","BEL","IRCTC"
]
MIN_SCORE = 6
VOL_SHOCK_MULTIPLE = 1.5
INTERVAL = "60m"

def send_email(subject, body):
    gmail_user=os.getenv("GMAIL_USER"); gmail_pass=os.getenv("GMAIL_APP_PASSWORD"); alert_to=os.getenv("ALERT_TO_EMAIL")
    if not gmail_user or not gmail_pass or not alert_to:
        print("Email secrets missing. Skipping email."); return
    msg=MIMEText(body); msg["Subject"]=subject; msg["From"]=gmail_user; msg["To"]=alert_to
    with smtplib.SMTP_SSL("smtp.gmail.com",465,timeout=30) as server:
        server.login(gmail_user,gmail_pass); server.send_message(msg)
    print("Email sent.")

def make_nse_session():
    session=requests.Session()
    headers={"User-Agent":"Mozilla/5.0","Accept":"application/json,text/plain,*/*","Accept-Language":"en-US,en;q=0.9","Referer":"https://www.nseindia.com/"}
    try: session.get("https://www.nseindia.com",headers=headers,timeout=10)
    except Exception: pass
    return session,headers

def get_nse_oi(symbol):
    session,headers=make_nse_session()
    try:
        r=session.get(f"https://www.nseindia.com/api/quote-derivative?symbol={symbol}",headers=headers,timeout=15)
        if r.status_code!=200: return None
        data=r.json(); stocks=data.get("stocks",[])
        if not stocks: return None
        return stocks[0].get("marketDeptOrderBook",{}).get("tradeInfo",{}).get("changeinOpenInterest",None)
    except Exception as e:
        print(f"NSE OI failed for {symbol}: {e}"); return None

def interpret_oi(price_change,oi_change):
    if oi_change is None: return "No OI Data"
    try: oi_change=float(oi_change)
    except Exception: return "No OI Data"
    if price_change>0 and oi_change>0: return "Long Buildup"
    if price_change<0 and oi_change>0: return "Short Buildup"
    if price_change>0 and oi_change<0: return "Short Covering"
    if price_change<0 and oi_change<0: return "Long Unwinding"
    return "Neutral"

def get_price_data(symbol):
    try:
        df=yf.download(symbol+".NS",period="30d",interval=INTERVAL,progress=False,threads=False,timeout=20,auto_adjust=False)
        if df is None or df.empty: return pd.DataFrame()
        if isinstance(df.columns,pd.MultiIndex): df.columns=[c[0] for c in df.columns]
        return df.dropna()
    except Exception as e:
        print(f"Price data failed for {symbol}: {e}"); return pd.DataFrame()

def scan_stock(symbol):
    try:
        df=get_price_data(symbol)
        if df.empty or len(df)<60:
            print(f"{symbol}: insufficient price data"); return None
        df["EMA20"]=df["Close"].ewm(span=20).mean(); df["EMA50"]=df["Close"].ewm(span=50).mean(); df["VOLMA20"]=df["Volume"].rolling(20).mean()
        last=df.iloc[-1]; prev=df.iloc[-2]
        close=float(last["Close"]); open_=float(last["Open"]); high=float(last["High"]); low=float(last["Low"]); volume=float(last["Volume"]); prev_close=float(prev["Close"])
        ema20=float(last["EMA20"]); ema50=float(last["EMA50"]); volma=float(last["VOLMA20"]) if pd.notna(last["VOLMA20"]) else 0
        prev_high=float(df["High"].iloc[-6:-1].max()); prev_low=float(df["Low"].iloc[-6:-1].min()); close_6=float(df["Close"].iloc[-6])
        pct_change=((close-prev_close)/prev_close)*100 if prev_close else 0; vol_x=volume/volma if volma else 0
        top_gainer_flag=pct_change>=1.0; top_loser_flag=pct_change<=-1.0; volume_shocker_flag=vol_x>=VOL_SHOCK_MULTIPLE
        ssl_sweep=low<prev_low and close>prev_low; bsl_sweep=high>prev_high and close<prev_high
        bull_displacement=close>open_ and close>prev_high and volume>volma; bear_displacement=close<open_ and close<prev_low and volume>volma
        buy_score=sum([close>ema20 and close>ema50,close>=prev_high*0.995,volume>volma,close>close_6,high>float(df["High"].iloc[-2]) and low>float(df["Low"].iloc[-2]),ssl_sweep,bull_displacement,close>ema20])
        sell_score=sum([close<ema20 and close<ema50,close<=prev_low*1.005 or bsl_sweep,volume>volma,close<close_6,high<float(df["High"].iloc[-2]) and low<float(df["Low"].iloc[-2]),bsl_sweep,bear_displacement,close<ema20])
        oi_change=get_nse_oi(symbol); oi_signal=interpret_oi(close-prev_close,oi_change)
        action="NO TRADE"; trade_type="-"; probability="Low"
        buy_active=top_gainer_flag or volume_shocker_flag; sell_active=top_loser_flag or volume_shocker_flag
        if buy_score>=MIN_SCORE and buy_active and bull_displacement and oi_signal in ["Long Buildup","Short Covering"]:
            action="BTST EXECUTE"; trade_type="BTST"; probability="70%+"
        elif sell_score>=MIN_SCORE and sell_active and bear_displacement and oi_signal in ["Short Buildup","Long Unwinding"]:
            action="STBT EXECUTE"; trade_type="STBT"; probability="70%+"
        elif buy_score>=MIN_SCORE and buy_active:
            action="BTST WATCH"; trade_type="BTST"; probability="60-65%"
        elif sell_score>=MIN_SCORE and sell_active:
            action="STBT WATCH"; trade_type="STBT"; probability="60-65%"
        if trade_type=="BTST": entry=round(close,2); sl=round(close*0.985,2); tp1=round(close*1.025,2)
        elif trade_type=="STBT": entry=round(close,2); sl=round(close*1.015,2); tp1=round(close*0.975,2)
        else: entry=sl=tp1="-"
        return {"Stock":symbol,"Close":round(close,2),"% Chg":round(pct_change,2),"Vol xAvg":round(vol_x,2),"Top Gainer":"YES" if top_gainer_flag else "NO","Top Loser":"YES" if top_loser_flag else "NO","Volume Shocker":"YES" if volume_shocker_flag else "NO","OI Signal":oi_signal,"OI Change":oi_change if oi_change is not None else "-","Buy Score":f"{int(buy_score)}/8","Sell Score":f"{int(sell_score)}/8","SSL Sweep":"YES" if ssl_sweep else "NO","BSL Sweep":"YES" if bsl_sweep else "NO","Bull Disp":"YES" if bull_displacement else "NO","Bear Disp":"YES" if bear_displacement else "NO","Action":action,"Probability":probability,"Entry":entry,"SL":sl,"TP1":tp1}
    except Exception as e:
        print(f"{symbol} scan failed: {e}"); return None

def run_scan():
    print("Starting F&O OI scanner...")
    results=[]
    for symbol in FNO_STOCKS:
        result=scan_stock(symbol)
        if result: results.append(result)
    if not results:
        print("No scan results."); return
    df=pd.DataFrame(results)
    print(df.to_string(index=False))
    execute_df=df[df["Action"].isin(["BTST EXECUTE","STBT EXECUTE"])]
    if execute_df.empty:
        print("No executable trades found. No email sent."); return
    body="EXECUTABLE F&O BTST / STBT SETUPS FOUND\n\n"+execute_df.to_string(index=False)+f"\n\nScan time: {datetime.now()}"
    send_email("NSE F&O BTST/STBT Execute Alert",body)
    print("Scan completed.")

if __name__=="__main__":
    run_scan()
