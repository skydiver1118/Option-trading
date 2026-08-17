from __future__ import annotations
import json, math, os
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf
import pandas_market_calendars as mcal

ET=ZoneInfo('America/New_York')
TICKERS=['SOXL','LITE','AAOI','MRVL','MU','AVGO','SMH']
PUT_NAMES=['SOXL','LITE','AAOI','MRVL','MU','AVGO']
TARGET_DTE=(28,45)
RISK_FREE=0.04


def f(x):
    try:
        if x is None or (isinstance(x,float) and math.isnan(x)): return None
        return round(float(x),2)
    except Exception:return None


def fibs(low,high):
    r=high-low
    return {'23.6%':high-.236*r,'38.2%':high-.382*r,'50%':high-.5*r,'61.8%':high-.618*r,'78.6%':high-.786*r}


def support_below(price, fib):
    vals=[float(v) for v in fib.values() if v is not None and float(v)<=price]
    return max(vals) if vals else float(fib['78.6%'])


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


def norm_cdf(x): return 0.5*(1+math.erf(x/math.sqrt(2)))


def put_delta_bs(spot,strike,iv,dte):
    try:
        sigma=max(float(iv),1e-6); t=max(float(dte)/365.0,1e-6)
        d1=(math.log(spot/strike)+(RISK_FREE+0.5*sigma*sigma)*t)/(sigma*math.sqrt(t))
        return norm_cdf(d1)-1.0
    except Exception:return None


def earnings_date(ticker):
    try:
        cal=yf.Ticker(ticker).calendar
        ed=cal.get('Earnings Date') if isinstance(cal,dict) else None
        if not ed:return None
        e=ed[0] if isinstance(ed,list) else ed
        return pd.Timestamp(e).date()
    except Exception:return None


def candidate_puts(ticker,price,support,expiry):
    if not expiry:return []
    puts=yf.Ticker(ticker).option_chain(expiry).puts.copy()
    if puts.empty:return []
    exp_date=datetime.strptime(expiry,'%Y-%m-%d').date(); today=datetime.now(ET).date(); dte=max((exp_date-today).days,1)
    ed=earnings_date(ticker); event_risk=bool(ed and today<ed<=exp_date)
    puts=puts[puts.strike<price].copy(); rows=[]
    for _,r in puts.iterrows():
        strike=float(r.strike); bid=float(r.bid or 0); ask=float(r.ask or 0)
        mid=(bid+ask)/2 if bid>0 and ask>0 else max(bid,ask)
        if mid<=0: continue
        iv=float(r.impliedVolatility) if pd.notna(r.impliedVolatility) else None
        delta=put_delta_bs(price,strike,iv,dte) if iv else None
        be=strike-mid
        rows.append({'strike':strike,'bid':bid,'ask':ask,'premium':mid,'breakeven':be,'iv_pct':iv*100 if iv else None,'delta':delta,'annualized_return_pct':(mid/strike)*(365/dte)*100 if strike else None,'distance_to_support_pct':((be-support)/support*100) if support else None,'dte':dte,'earnings_risk':event_risk,'earnings_date':ed.isoformat() if ed else None})
    if not rows:return []
    targets=[('Conservative',0.12),('Preferred',0.20),('Aggressive',0.30)]; chosen=[]; used=set()
    fallback_pct={'Conservative':0.75,'Preferred':0.82,'Aggressive':0.90}
    for label,target in targets:
        pool=[x for x in rows if x['strike'] not in used]
        if not pool: continue
        with_delta=[x for x in pool if x['delta'] is not None]
        pick=min(with_delta,key=lambda x:abs(abs(x['delta'])-target)) if with_delta else min(pool,key=lambda x:abs(x['strike']-price*fallback_pct[label]))
        used.add(pick['strike']); pick=dict(pick); pick['profile']=label; chosen.append(pick)
    return chosen


def market_open_now(now):
    cal=mcal.get_calendar('NYSE'); sched=cal.schedule(start_date=now.date(),end_date=now.date())
    if sched.empty:return False
    return sched.iloc[0].market_open.tz_convert(ET)<=now<=sched.iloc[0].market_close.tz_convert(ET)


def should_run(now):
    event=os.getenv('GITHUB_EVENT_NAME','')
    if event in {'workflow_dispatch','push'}: return True
    cal=mcal.get_calendar('NYSE'); sched=cal.schedule(start_date=now.date(),end_date=now.date())
    if sched.empty:return False
    cron=os.getenv('GITHUB_EVENT_SCHEDULE','').strip(); off=now.utcoffset().total_seconds()/3600
    if off==-4:
        valid={'30 14 * * 1-5','0 17 * * 1-5','30 18 * * 1-5','45 14 * * 1-5','15 17 * * 1-5','45 18 * * 1-5'}
    else:
        valid={'30 15 * * 1-5','0 18 * * 1-5','30 19 * * 1-5','45 15 * * 1-5','15 18 * * 1-5','45 19 * * 1-5'}
    return cron in valid


def main():
    now=datetime.now(ET)
    if not should_run(now): print('Not a scheduled ET trading-day refresh; exiting.'); return
    raw={}; analyses=[]
    for sym in TICKERS:
        h=yf.Ticker(sym).history(period='3mo',interval='1d',auto_adjust=True,actions=False)
        if h.empty: continue
        h=h[['Open','High','Low','Close']].dropna(); close=float(h.Close.iloc[-1]); prev=float(h.Close.iloc[-2]) if len(h)>1 else close
        recent=h.tail(45); sane=recent[(recent.High<=close*1.65)&(recent.Low>=close*0.45)]
        if len(sane)>=20: recent=sane
        low=float(recent.Low.min()); high=float(recent.High.max()); fb=fibs(low,high)
        raw[sym]={'price':close,'change_pct':(close/prev-1)*100 if prev else 0,'low45':low,'high45':high,'fib':fb,'ema20':float(h.Close.ewm(span=20,adjust=False).mean().iloc[-1]),'key_support':support_below(close,fb)}
    for sym in PUT_NAMES:
        x=raw[sym]; expiry=nearest_expiry(yf.Ticker(sym)); candidates=candidate_puts(sym,x['price'],x['key_support'],expiry)
        pref=next((c for c in candidates if c['profile']=='Preferred'),candidates[0] if candidates else None)
        near=abs(x['price']-x['key_support'])/x['price']<=.04; vertical=x['change_pct']>=6
        premium_good=bool(pref and pref['annualized_return_pct'] and pref['annualized_return_pct']>=35); event=bool(pref and pref['earnings_risk'])
        score=55+(12 if near else 0)+(12 if premium_good else 0)-(18 if vertical else 0)-(18 if event else 0)-(8 if sym=='SOXL' else 0)
        score=max(0,min(100,score)); decision='SELL' if score>=68 and not vertical and not event else 'WAIT'
        clean=[]
        for c in candidates:
            clean.append({**c,'strike':f(c['strike']),'bid':f(c['bid']),'ask':f(c['ask']),'premium':f(c['premium']),'breakeven':f(c['breakeven']),'iv_pct':f(c['iv_pct']),'delta':f(c['delta']),'annualized_return_pct':f(c['annualized_return_pct']),'distance_to_support_pct':f(c['distance_to_support_pct'])})
        analyses.append({'ticker':sym,'price':f(x['price']),'change_pct':f(x['change_pct']),'decision':decision,'score':int(score),'contract':(f"{expiry} ${pref['strike']:.0f}P" if pref else None),'premium':f(pref['premium']) if pref else None,'breakeven':f(pref['breakeven']) if pref else None,'key_support':f(x['key_support']),'fib':{k:f(v) for k,v in x['fib'].items()},'candidates':clean,'trigger':f"Nearest technical support ${x['key_support']:.2f}; prefer stable/reclaiming tape before entry.",'risk':'3x daily leverage and volatility drag.' if sym=='SOXL' else ('Earnings falls before expiration.' if event else 'Gap risk and volatility expansion.'),'note':f"Adjusted 45-day swing ${x['low45']:.2f} → ${x['high45']:.2f}; EMA20 ${x['ema20']:.2f}"})
    ranking=sorted(analyses,key=lambda z:z['score'],reverse=True)
    smh=raw.get('SMH',{}); entries=[]
    if smh:
        price=smh['price']; levels=sorted([v for v in smh['fib'].values() if v<=price],reverse=True)
        entries=[{'zone':f"${price*.995:.0f}–${price*1.005:.0f}",'allocation':20,'label':'starter only'}]
        for i,(label,alloc) in enumerate([('first support',30),('strong support',30),('major accumulation',20)]):
            if i<len(levels): entries.append({'zone':f"${levels[i]-3:.0f}–${levels[i]+3:.0f}",'allocation':alloc,'label':label})
    payload={'updated_et':now.strftime('%Y-%m-%d %I:%M %p ET'),'market_state':'OPEN' if market_open_now(now) else 'CLOSED','tickers':[{'ticker':k,'price':f(v['price']),'change_pct':f(v['change_pct'])} for k,v in raw.items()],'ranking':ranking,'analysis':analyses,'smh':{'price':f(smh.get('price')),'entries':entries}}
    os.makedirs('data',exist_ok=True)
    with open('data/dashboard.json','w') as fh: json.dump(payload,fh,indent=2)
    print(json.dumps(payload,indent=2))

if __name__=='__main__': main()
