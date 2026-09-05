import os, json, math, time
from pathlib import Path
import requests
import numpy as np
import pandas as pd

SYMBOLS = ["SMH","SPMO","VGT","SPY","TQQQ"]
START = "2000-01-01"
END = "2026-09-04"
COST = 0.0005
OUT = Path("artifacts/tradier_cpa")
OUT.mkdir(parents=True, exist_ok=True)

TOKEN = os.getenv("TRADIER_TOKEN") or os.getenv("TRADIER_API_TOKEN")
if not TOKEN:
    raise SystemExit("Missing Tradier token: set TRADIER_TOKEN or TRADIER_API_TOKEN")

BASE = "https://api.tradier.com/v1/markets/history"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}

def fetch_history(symbol):
    r = requests.get(BASE, headers=HEADERS, params={"symbol":symbol,"interval":"daily","start":START,"end":END}, timeout=60)
    r.raise_for_status()
    payload = r.json()
    days = (((payload or {}).get("history") or {}).get("day"))
    if not days:
        raise RuntimeError(f"No Tradier history for {symbol}: {payload}")
    if isinstance(days, dict): days = [days]
    df = pd.DataFrame(days)
    df["date"] = pd.to_datetime(df["date"])
    for c in ["open","high","low","close","volume"]: df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna().sort_values("date").set_index("date")[["open","high","low","close","volume"]]

def indicators(df):
    x=df.copy()
    x["ema10"] = x.close.ewm(span=10, adjust=False).mean()
    x["ema20"] = x.close.ewm(span=20, adjust=False).mean()
    x["sma50"] = x.close.rolling(50).mean()
    x["sma200"] = x.close.rolling(200).mean()
    x["volmed20"] = x.volume.rolling(20).median()
    x["spread"] = (x.ema10-x.ema20).abs()/x.close
    x["spread5"] = x.spread.rolling(5).mean()
    x["prev5high"] = x.high.shift(1).rolling(5).max()
    return x

def signals(x, variant):
    above = (x.close>x.ema10)&(x.close>x.ema20)
    prev_below = x.close.shift(1) <= pd.concat([x.ema10.shift(1),x.ema20.shift(1)],axis=1).max(axis=1)
    wedge = prev_below & above & (x.close>x.prev5high) & (x.spread<x.spread5) & (x.volume>x.volmed20)
    touch = (x.low <= pd.concat([x.ema10,x.ema20],axis=1).max(axis=1)) & above
    crossback = (x.ema10>x.ema20) & touch & (x.close>x.close.shift(1))
    entry = wedge | crossback
    if variant=="Regime": entry &= (x.close>x.sma50)&(x.sma50>x.sma200)
    if variant=="Fast": exit_ = x.close < x.ema10
    else: exit_ = (x.close<x.ema10)&(x.close<x.ema20)
    if variant=="Regime": exit_ |= (x.close<x.sma50)
    return entry.fillna(False), exit_.fillna(False)

def run_strategy(x, variant):
    entry, exit_ = signals(x, variant)
    n=len(x); pos=np.zeros(n); rets=np.zeros(n); trades=[]; inpos=False; ep=None; ed=None
    for i in range(1,n):
        if not inpos and bool(entry.iloc[i-1]):
            inpos=True; ep=float(x.open.iloc[i])*(1+COST); ed=x.index[i]
        elif inpos and bool(exit_.iloc[i-1]):
            xp=float(x.open.iloc[i])*(1-COST)
            trades.append({"entry_date":ed,"exit_date":x.index[i],"entry_price":ep,"exit_price":xp,"return":xp/ep-1})
            inpos=False; ep=None; ed=None
        if inpos:
            if ed==x.index[i]: rets[i]=float(x.close.iloc[i])/float(x.open.iloc[i])*(1-COST)-1
            else: rets[i]=float(x.close.iloc[i])/float(x.close.iloc[i-1])-1
            pos[i]=1
    if inpos:
        xp=float(x.close.iloc[-1])*(1-COST)
        trades.append({"entry_date":ed,"exit_date":x.index[-1],"entry_price":ep,"exit_price":xp,"return":xp/ep-1})
    eq=pd.Series((1+rets).cumprod(), index=x.index)
    return eq, pd.DataFrame(trades), pd.Series(pos,index=x.index)

def metrics(eq,trades,pos):
    dr=eq.pct_change().fillna(0)
    years=max((eq.index[-1]-eq.index[0]).days/365.25,1/365.25)
    total=eq.iloc[-1]-1; cagr=eq.iloc[-1]**(1/years)-1
    dd=eq/eq.cummax()-1; mdd=dd.min()
    sd=dr.std(); sharpe=(dr.mean()/sd*math.sqrt(252)) if sd>0 else np.nan
    dn=dr[dr<0].std(); sortino=(dr.mean()/dn*math.sqrt(252)) if dn and dn>0 else np.nan
    calmar=cagr/abs(mdd) if mdd<0 else np.nan
    if len(trades):
        wins=trades[trades["return"]>0]; losses=trades[trades["return"]<=0]
        pf=wins["return"].sum()/abs(losses["return"].sum()) if len(losses) and losses["return"].sum()!=0 else np.nan
        wr=len(wins)/len(trades); avgw=wins["return"].mean() if len(wins) else np.nan; avgl=losses["return"].mean() if len(losses) else np.nan; worst=trades["return"].min()
    else: pf=wr=avgw=avgl=worst=np.nan
    return {"CAGR":cagr,"TotalReturn":total,"MaxDD":mdd,"Sharpe":sharpe,"Sortino":sortino,"Calmar":calmar,"Trades":len(trades),"WinRate":wr,"AvgWinner":avgw,"AvgLoser":avgl,"ProfitFactor":pf,"Exposure":pos.mean(),"WorstTrade":worst}

def buyhold(x):
    rets=x.close.pct_change().fillna(0); eq=(1+rets).cumprod(); pos=pd.Series(1.0,index=x.index)
    return eq,pd.DataFrame(),pos

all_rows=[]; meta={}
for sym in SYMBOLS:
    raw=fetch_history(sym); raw.to_csv(OUT/f"{sym}_tradier_raw.csv")
    x=indicators(raw).dropna().copy()
    if len(x) < 2:
        raise RuntimeError(f"Insufficient Tradier history after 200-day warm-up for {sym}: {len(raw)} rows")
    meta[sym]={"raw_start":str(raw.index.min().date()),"raw_end":str(raw.index.max().date()),"raw_rows":len(raw),"test_start":str(x.index.min().date()),"test_rows":len(x)}
    for variant in ["Core","Fast","Regime"]:
        eq,tr,pos=run_strategy(x,variant); m=metrics(eq,tr,pos); m.update({"Symbol":sym,"Strategy":variant}); all_rows.append(m)
        tr.to_csv(OUT/f"{sym}_{variant}_trades.csv",index=False)
    eq,tr,pos=buyhold(x); m=metrics(eq,tr,pos); m.update({"Symbol":sym,"Strategy":"BuyHold"}); all_rows.append(m)
    time.sleep(0.15)

summary=pd.DataFrame(all_rows)
cols=["Symbol","Strategy","CAGR","TotalReturn","MaxDD","Sharpe","Sortino","Calmar","Trades","WinRate","AvgWinner","AvgLoser","ProfitFactor","Exposure","WorstTrade"]
summary[cols].to_csv(OUT/"summary.csv",index=False)
(OUT/"meta.json").write_text(json.dumps({"source":"Tradier /v1/markets/history","requested_start":START,"requested_end":END,"cost_per_side":COST,"execution":"close signal, next-open trade","symbols":meta},indent=2))
print(summary[cols].to_string(index=False))
print(json.dumps(meta,indent=2))
