from __future__ import annotations
import json, math, os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import yfinance as yf
import pandas_market_calendars as mcal

ET=ZoneInfo('America/New_York')
TICKERS=['SOXL','LITE','AAOI','MRVL','MU','SMH']
PUT_NAMES=['SOXL','LITE','AAOI','MRVL','MU']
TARGET_DTE=(28,45)


def f(x):
    try:
        if x is None or (isinstance(x,float) and math.isnan(x)): return None
        return round(float(x),2)
    except Exception:return None

def fibs(low,high):
    r=high-low
    return {'23.6%':high-.236*r,'38.2%':high-.382*r,'50%':high-.5*r,'61.8%':high-.618*r,'78.6%':high-.786*r}

def nearest_expiry(t):
    today=datetime.now(ET).date(); candidates=[]
    for s in t.options:
        d=datetime.strptime(s,'%Y-%m-%d').date(); dte=(d-today).days
        if TARGET_DTE[0] <= dte <= TARGET_DTE[1]: candidates.append((abs(dte-35),d,s))
    if not candidates:
        for s in t.options:
            d=datetime.strptime(s,'%Y-%m-%d').date(); dte=(d-today).days
            if dte>7:candidates.append((abs(dte-35),d,s))
    return min(candidates)[2] if candidates else None

def option_pick(ticker, price, fib, expiry):
    if not expiry:return None
    t=yf.Ticker(ticker); puts=t.option_chain(expiry).puts.copy()
    if puts.empty:return None
    target={'SOXL':fib['78.6%'],'AAOI':fib['61.8%'],'LITE':fib['50%'],'MRVL':fib['50%'],'MU':fib['50%']}[ticker]
    otm=puts[puts.strike < price].copy()
    if otm.empty:return None
    otm['dist']=(otm.strike-target).abs(); row=otm.sort_values('dist').iloc[0]
    bid=float(row.bid or 0); ask=float(row.ask or 0); mid=(bid+ask)/2 if bid>0 and ask>0 else max(bid,ask)
    strike=float(row.strike); be=strike-mid; cushion=(price-be)/price*100 if price else 0
    cash_yield=mid/strike*100 if strike else 0
    iv=float(row.impliedVolatility)*100 if pd.notna(row.impliedVolatility) else None
    return {'expiry':expiry,'strike':strike,'bid':bid,'ask':ask,'premium':mid,'breakeven':be,'cushion_pct':cushion,'cash_yield_pct':cash_yield,'iv_pct':iv}

def market_open_now(now):
    cal=mcal.get_calendar('NYSE'); sched=cal.schedule(start_date=now.date(),end_date=now.date())
    if sched.empty:return False
    o=sched.iloc[0].market_open.tz_convert(ET); c=sched.iloc[0].market_close.tz_convert(ET)
    return o<=now<=c

def should_run(now):
    if os.getenv('GITHUB_EVENT_NAME')=='workflow_dispatch':return True
    cal=mcal.get_calendar('NYSE'); sched=cal.schedule(start_date=now.date(),end_date=now.date())
    if sched.empty:return False
    return (now.hour,now.minute) in {(10,30),(13,0),(14,30)}

def main():
    now=datetime.now(ET)
    if not should_run(now):
        print('Not a scheduled ET trading-day refresh; exiting.'); return
    raw={}; analyses=[]
    for sym in TICKERS:
        t=yf.Ticker(sym); h=t.history(period='3mo',interval='1d',auto_adjust=False)
        if h.empty: continue
        close=float(h.Close.iloc[-1]); prev=float(h.Close.iloc[-2]) if len(h)>1 else close
        ch=(close/prev-1)*100 if prev else 0
        recent=h.tail(45); low=float(recent.Low.min()); high=float(recent.High.max()); fb=fibs(low,high)
        ema20=float(h.Close.ewm(span=20,adjust=False).mean().iloc[-1])
        raw[sym]={'ticker':sym,'price':close,'change_pct':ch,'low45':low,'high45':high,'fib':fb,'ema20':ema20}
    for sym in PUT_NAMES:
        x=raw[sym]; expiry=nearest_expiry(yf.Ticker(sym)); opt=option_pick(sym,x['price'],x['fib'],expiry)
        f23=x['fib']['23.6%']; near_support=abs(x['price']-f23)/x['price'] <= .04
        vertical=x['change_pct']>=6
        premium_good=bool(opt and opt['cash_yield_pct']>=4.5)
        event_penalty=False
        try:
            cal=yf.Ticker(sym).calendar
            ed=cal.get('Earnings Date') if isinstance(cal,dict) else None
            if ed and expiry:
                e=ed[0] if isinstance(ed,list) else ed
                event_penalty=datetime.now(ET).date() < pd.Timestamp(e).date() <= datetime.strptime(expiry,'%Y-%m-%d').date()
        except Exception: pass
        score=55 + (12 if near_support else 0) + (12 if premium_good else 0) - (18 if vertical else 0) - (18 if event_penalty else 0)
        if sym=='SOXL': score-=8
        score=max(0,min(100,score))
        decision='SELL' if score>=68 and not vertical else 'WAIT'
        preferred='No suitable chain'
        trigger='Wait for pullback into support with richer premium.'
        risk='High-beta semiconductor exposure.'
        if opt:
            preferred=f"{opt['expiry']} ${opt['strike']:.0f}P near ${opt['premium']:.2f} midpoint"
            trigger=f"Favor entry near ${f23:.2f} first support; require stable tape and ~{opt['cash_yield_pct']:.1f}% credit/strike or better."
            risk=('3x daily leverage and volatility drag.' if sym=='SOXL' else ('Earnings/event risk may dominate option pricing.' if event_penalty else 'Gap risk and volatility expansion.'))
        analyses.append({'ticker':sym,'price':f(x['price']),'change_pct':f(x['change_pct']),'decision':decision,'score':int(score),'contract':(f"{opt['expiry']} ${opt['strike']:.0f}P" if opt else None),'premium':f(opt['premium']) if opt else None,'breakeven':f(opt['breakeven']) if opt else None,'key_support':f(f23),'fib':{k:f(v) for k,v in x['fib'].items()},'preferred':preferred,'trigger':trigger,'risk':risk,'note':f"45-day swing ${x['low45']:.2f} → ${x['high45']:.2f}; EMA20 ${x['ema20']:.2f}"})
    ranking=sorted(analyses,key=lambda z:z['score'],reverse=True)
    smh=raw.get('SMH',{}); sf=smh.get('fib',{})
    entries=[]
    if smh:
        entries=[{'zone':f"${smh['price']*.995:.0f}–${smh['price']*1.005:.0f}",'allocation':20,'label':'starter only'}, {'zone':f"${sf['23.6%']-3:.0f}–${sf['23.6%']+3:.0f}",'allocation':30,'label':'first support'}, {'zone':f"${sf['38.2%']-3:.0f}–${sf['38.2%']+3:.0f}",'allocation':30,'label':'strong support'}, {'zone':f"${sf['50%']-3:.0f}–${sf['50%']+3:.0f}",'allocation':20,'label':'major accumulation'}]
    payload={'updated_et':now.strftime('%Y-%m-%d %I:%M %p ET'),'market_state':'OPEN' if market_open_now(now) else 'CLOSED','tickers':[{'ticker':k,'price':f(v['price']),'change_pct':f(v['change_pct'])} for k,v in raw.items()],'ranking':ranking,'analysis':analyses,'smh':{'price':f(smh.get('price')),'entries':entries}}
    os.makedirs('data',exist_ok=True)
    with open('data/dashboard.json','w') as fh: json.dump(payload,fh,indent=2)
    print(json.dumps(payload,indent=2))
if __name__=='__main__': main()
