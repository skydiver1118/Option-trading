from __future__ import annotations
import json, math, os
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf
import pandas_market_calendars as mcal

ET=ZoneInfo('America/New_York')
TICKERS=['SOXL','LITE','AAOI','MRVL','MU','AVGO','QTUM','SMH']
PUT_NAMES=['SOXL','LITE','AAOI','MRVL','MU','AVGO','QTUM']
TARGET_DTE=(28,45)
RISK_FREE=0.04
TRADIER_BASE='https://api.tradier.com/v1'
TRADIER_TOKEN=os.getenv('TRADIER_TOKEN','').strip()


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


def tradier_get(path, params):
    if not TRADIER_TOKEN: raise RuntimeError('TRADIER_TOKEN not configured')
    url=f"{TRADIER_BASE}{path}?{urlencode(params)}"
    req=Request(url,headers={'Authorization':f'Bearer {TRADIER_TOKEN}','Accept':'application/json','User-Agent':'option-dashboard/1.0'})
    with urlopen(req,timeout=20) as resp:return json.loads(resp.read().decode('utf-8'))


def tradier_expirations(symbol):
    j=tradier_get('/markets/options/expirations',{'symbol':symbol,'includeAllRoots':'false','strikes':'false'}); dates=((j or {}).get('expirations') or {}).get('date') or []
    if isinstance(dates,str):dates=[dates]
    return list(dates)


def yfinance_expirations(symbol):return list(yf.Ticker(symbol).options)


def choose_expiry(symbol):
    today=datetime.now(ET).date(); sources=[]
    if TRADIER_TOKEN:
        try:sources=[('Tradier',tradier_expirations(symbol))]
        except Exception as e:print(f'Tradier expirations fallback for {symbol}: {e}')
    sources.append(('yfinance',yfinance_expirations(symbol)))
    for source,dates in sources:
        candidates=[]
        for s in dates:
            try:
                d=datetime.strptime(s,'%Y-%m-%d').date();dte=(d-today).days
                if TARGET_DTE[0]<=dte<=TARGET_DTE[1]:candidates.append((abs(dte-35),d,s))
            except Exception:pass
        if not candidates:
            for s in dates:
                try:
                    d=datetime.strptime(s,'%Y-%m-%d').date();dte=(d-today).days
                    if dte>7:candidates.append((abs(dte-35),d,s))
                except Exception:pass
        if candidates:return min(candidates)[2],source
    return None,None


def norm_cdf(x):return .5*(1+math.erf(x/math.sqrt(2)))
def put_delta_bs(spot,strike,iv,dte):
    try:
        sigma=max(float(iv),1e-6);t=max(float(dte)/365,1e-6);d1=(math.log(spot/strike)+(RISK_FREE+.5*sigma*sigma)*t)/(sigma*math.sqrt(t));return norm_cdf(d1)-1
    except Exception:return None


def earnings_date(ticker):
    if ticker=='QTUM':return None
    try:
        cal=yf.Ticker(ticker).calendar;ed=cal.get('Earnings Date') if isinstance(cal,dict) else None
        if not ed:return None
        e=ed[0] if isinstance(ed,list) else ed;return pd.Timestamp(e).date()
    except Exception:return None


def tradier_put_rows(ticker,expiry):
    j=tradier_get('/markets/options/chains',{'symbol':ticker,'expiration':expiry,'greeks':'true'});options=((j or {}).get('options') or {}).get('option') or []
    if isinstance(options,dict):options=[options]
    rows=[]
    for o in options:
        if str(o.get('option_type','')).lower()!='put':continue
        g=o.get('greeks') or {};iv=g.get('mid_iv') if g.get('mid_iv') is not None else g.get('smv_vol');delta=g.get('delta')
        rows.append({'strike':o.get('strike'),'bid':o.get('bid'),'ask':o.get('ask'),'iv':iv,'delta':delta,'source':'Tradier','delta_source':'Tradier/ORATS' if delta is not None else None})
    return rows


def yfinance_put_rows(ticker,expiry):
    puts=yf.Ticker(ticker).option_chain(expiry).puts.copy();return [{'strike':r.strike,'bid':r.bid,'ask':r.ask,'iv':r.impliedVolatility if pd.notna(r.impliedVolatility) else None,'delta':None,'source':'yfinance','delta_source':None} for _,r in puts.iterrows()]


def candidate_puts(ticker,price,support,expiry):
    if not expiry:return [],'none'
    source='yfinance';rows=[]
    if TRADIER_TOKEN:
        try:
            rows=tradier_put_rows(ticker,expiry);source='Tradier'
            if not rows:raise RuntimeError('empty Tradier chain')
        except Exception as e:print(f'Tradier chain fallback for {ticker}: {e}');rows=yfinance_put_rows(ticker,expiry);source='yfinance'
    else:rows=yfinance_put_rows(ticker,expiry)
    exp_date=datetime.strptime(expiry,'%Y-%m-%d').date();today=datetime.now(ET).date();dte=max((exp_date-today).days,1);ed=earnings_date(ticker);event_risk=bool(ed and today<ed<=exp_date);cleaned=[]
    for r in rows:
        try:strike=float(r.get('strike'));bid=float(r.get('bid') or 0);ask=float(r.get('ask') or 0)
        except Exception:continue
        if strike>=price:continue
        mid=(bid+ask)/2 if bid>0 and ask>0 else max(bid,ask)
        if mid<=0:continue
        try:iv=float(r.get('iv')) if r.get('iv') is not None else None
        except Exception:iv=None
        try:delta=float(r.get('delta')) if r.get('delta') is not None else None
        except Exception:delta=None
        delta_source=r.get('delta_source')
        if delta is None and iv:delta=put_delta_bs(price,strike,iv,dte);delta_source='Black-Scholes estimate'
        be=strike-mid;cleaned.append({'strike':strike,'bid':bid,'ask':ask,'premium':mid,'breakeven':be,'iv_pct':iv*100 if iv else None,'delta':delta,'annualized_return_pct':(mid/strike)*(365/dte)*100 if strike else None,'distance_to_support_pct':((be-support)/support*100) if support else None,'dte':dte,'earnings_risk':event_risk,'earnings_date':ed.isoformat() if ed else None,'source':source,'delta_source':delta_source})
    if not cleaned:return [],source
    targets=[('Conservative',.12),('Preferred',.20),('Aggressive',.30)];chosen=[];used=set();fallback_pct={'Conservative':.75,'Preferred':.82,'Aggressive':.90}
    for label,target in targets:
        pool=[x for x in cleaned if x['strike'] not in used]
        if not pool:continue
        with_delta=[x for x in pool if x['delta'] is not None];pick=min(with_delta,key=lambda x:abs(abs(x['delta'])-target)) if with_delta else min(pool,key=lambda x:abs(x['strike']-price*fallback_pct[label]));used.add(pick['strike']);pick=dict(pick);pick['profile']=label;chosen.append(pick)
    return chosen,source


def market_open_now(now):
    sched=mcal.get_calendar('NYSE').schedule(start_date=now.date(),end_date=now.date());return False if sched.empty else sched.iloc[0].market_open.tz_convert(ET)<=now<=sched.iloc[0].market_close.tz_convert(ET)


def should_run(now):
    event=os.getenv('GITHUB_EVENT_NAME','')
    if event in {'workflow_dispatch','push'}:return True
    if mcal.get_calendar('NYSE').schedule(start_date=now.date(),end_date=now.date()).empty:return False
    cron=os.getenv('GITHUB_EVENT_SCHEDULE','').strip();off=now.utcoffset().total_seconds()/3600
    valid={'20 14 * * 1-5','30 14 * * 1-5','40 14 * * 1-5','0 17 * * 1-5','15 17 * * 1-5','30 18 * * 1-5','45 18 * * 1-5'} if off==-4 else {'20 15 * * 1-5','30 15 * * 1-5','40 15 * * 1-5','0 18 * * 1-5','15 18 * * 1-5','30 19 * * 1-5','45 19 * * 1-5'}
    return cron in valid


def main():
    now=datetime.now(ET)
    if not should_run(now):print('Not a scheduled ET trading-day refresh; exiting.');return
    raw={};analyses=[];option_sources=set()
    for sym in TICKERS:
        h=yf.Ticker(sym).history(period='3mo',interval='1d',auto_adjust=True,actions=False)
        if h.empty:continue
        h=h[['Open','High','Low','Close']].dropna();close=float(h.Close.iloc[-1]);prev=float(h.Close.iloc[-2]) if len(h)>1 else close;recent=h.tail(45);sane=recent[(recent.High<=close*1.65)&(recent.Low>=close*.45)]
        if len(sane)>=20:recent=sane
        low=float(recent.Low.min());high=float(recent.High.max());fb=fibs(low,high);raw[sym]={'price':close,'change_pct':(close/prev-1)*100 if prev else 0,'low45':low,'high45':high,'fib':fb,'ema20':float(h.Close.ewm(span=20,adjust=False).mean().iloc[-1]),'key_support':support_below(close,fb)}
    for sym in PUT_NAMES:
        x=raw[sym];expiry,_=choose_expiry(sym);candidates,source=candidate_puts(sym,x['price'],x['key_support'],expiry);option_sources.add(source);pref=next((c for c in candidates if c['profile']=='Preferred'),candidates[0] if candidates else None);near=abs(x['price']-x['key_support'])/x['price']<=.04;vertical=x['change_pct']>=6;premium_good=bool(pref and pref['annualized_return_pct'] and pref['annualized_return_pct']>=35);event=bool(pref and pref['earnings_risk']);score=55+(12 if near else 0)+(12 if premium_good else 0)-(18 if vertical else 0)-(18 if event else 0)-(8 if sym=='SOXL' else 0);score=max(0,min(100,score));decision='SELL' if score>=68 and not vertical and not event else 'WAIT';clean=[]
        for c in candidates:clean.append({**c,'strike':f(c['strike']),'bid':f(c['bid']),'ask':f(c['ask']),'premium':f(c['premium']),'breakeven':f(c['breakeven']),'iv_pct':f(c['iv_pct']),'delta':f(c['delta']),'annualized_return_pct':f(c['annualized_return_pct']),'distance_to_support_pct':f(c['distance_to_support_pct'])})
        risk='3x daily leverage and volatility drag.' if sym=='SOXL' else ('ETF thematic/concentration risk; assignment is acceptable only if you want to own QTUM.' if sym=='QTUM' else ('Earnings falls before expiration.' if event else 'Gap risk and volatility expansion.'))
        analyses.append({'ticker':sym,'price':f(x['price']),'change_pct':f(x['change_pct']),'decision':decision,'score':int(score),'contract':(f"{expiry} ${pref['strike']:.0f}P" if pref else None),'premium':f(pref['premium']) if pref else None,'breakeven':f(pref['breakeven']) if pref else None,'key_support':f(x['key_support']),'fib':{k:f(v) for k,v in x['fib'].items()},'candidates':clean,'option_source':source,'trigger':f"Nearest technical support ${x['key_support']:.2f}; prefer stable/reclaiming tape before entry.",'risk':risk,'note':f"Adjusted 45-day swing ${x['low45']:.2f} → ${x['high45']:.2f}; EMA20 ${x['ema20']:.2f}"})
    ranking=sorted(analyses,key=lambda z:z['score'],reverse=True);smh=raw.get('SMH',{});entries=[]
    if smh:
        price=smh['price'];levels=sorted([v for v in smh['fib'].values() if v<=price],reverse=True);entries=[{'zone':f"${price*.995:.0f}–${price*1.005:.0f}",'allocation':20,'label':'starter only'}]
        for i,(label,alloc) in enumerate([('first support',30),('strong support',30),('major accumulation',20)]):
            if i<len(levels):entries.append({'zone':f"${levels[i]-3:.0f}–${levels[i]+3:.0f}",'allocation':alloc,'label':label})
    source_label='Tradier real-time' if option_sources=={'Tradier'} else ('yfinance fallback' if option_sources=={'yfinance'} else 'Tradier + yfinance fallback');payload={'updated_et':now.strftime('%Y-%m-%d %I:%M %p ET'),'market_state':'OPEN' if market_open_now(now) else 'CLOSED','option_data_source':source_label,'tickers':[{'ticker':k,'price':f(v['price']),'change_pct':f(v['change_pct'])} for k,v in raw.items()],'ranking':ranking,'analysis':analyses,'smh':{'price':f(smh.get('price')),'entries':entries}};os.makedirs('data',exist_ok=True)
    with open('data/dashboard.json','w') as fh:json.dump(payload,fh,indent=2)
    print(json.dumps(payload,indent=2))

if __name__=='__main__':main()
