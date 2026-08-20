from __future__ import annotations
import json, math, os, calendar
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import pandas as pd
import yfinance as yf
import pandas_market_calendars as mcal

ET=ZoneInfo('America/New_York')
TICKERS=['SOXL','LITE','AAOI','MRVL','MU','AVGO','QTUM','DRAM','SMH']
PUT_NAMES=['SOXL','LITE','AAOI','MRVL','MU','AVGO','QTUM','DRAM']
RISK_FREE=.04
TRADIER_BASE='https://api.tradier.com/v1';TRADIER_TOKEN=os.getenv('TRADIER_TOKEN','').strip()

def f(x):
 try:
  if x is None or (isinstance(x,float) and math.isnan(x)):return None
  return round(float(x),2)
 except:return None

def fibs(low,high):
 r=high-low;return {'23.6%':high-.236*r,'38.2%':high-.382*r,'50%':high-.5*r,'61.8%':high-.618*r,'78.6%':high-.786*r}
def support_below(price,fib):
 vals=[float(v) for v in fib.values() if v is not None and float(v)<=price];return max(vals) if vals else float(fib['78.6%'])
def third_friday(y,m):
 c=calendar.Calendar().monthdatescalendar(y,m);return [d for w in c for d in w if d.month==m and d.weekday()==4][2]
def is_monthly_expiry(s):
 try:
  d=datetime.strptime(s,'%Y-%m-%d').date();return d==third_friday(d.year,d.month)
 except:return False

def tradier_get(path,params):
 if not TRADIER_TOKEN:raise RuntimeError('TRADIER_TOKEN not configured')
 req=Request(f"{TRADIER_BASE}{path}?{urlencode(params)}",headers={'Authorization':f'Bearer {TRADIER_TOKEN}','Accept':'application/json','User-Agent':'option-dashboard/1.0'})
 with urlopen(req,timeout=20) as resp:return json.loads(resp.read().decode())
def tradier_expirations(symbol):
 j=tradier_get('/markets/options/expirations',{'symbol':symbol,'includeAllRoots':'false','strikes':'false'});dates=((j or {}).get('expirations') or {}).get('date') or [];return [dates] if isinstance(dates,str) else list(dates)
def yfinance_expirations(symbol):return list(yf.Ticker(symbol).options)
def choose_expiry(symbol):
 today=datetime.now(ET).date();sources=[]
 if TRADIER_TOKEN:
  try:sources=[('Tradier',tradier_expirations(symbol))]
  except Exception as e:print(f'Tradier expirations fallback for {symbol}: {e}')
 sources.append(('yfinance',yfinance_expirations(symbol)))
 for source,dates in sources:
  monthly=[]
  for s in dates:
   try:
    d=datetime.strptime(s,'%Y-%m-%d').date();dte=(d-today).days
    if dte>7 and is_monthly_expiry(s):monthly.append((d,s))
   except:pass
  if monthly:return min(monthly,key=lambda x:x[0])[1],source
 return None,None

def norm_cdf(x):return .5*(1+math.erf(x/math.sqrt(2)))
def put_delta_bs(spot,strike,iv,dte):
 try:
  sigma=max(float(iv),1e-6);t=max(dte/365,1e-6);d1=(math.log(spot/strike)+(RISK_FREE+.5*sigma*sigma)*t)/(sigma*math.sqrt(t));return norm_cdf(d1)-1
 except:return None
def earnings_date(ticker):
 if ticker in {'QTUM','DRAM'}:return None
 try:
  cal=yf.Ticker(ticker).calendar;ed=cal.get('Earnings Date') if isinstance(cal,dict) else None
  if not ed:return None
  return pd.Timestamp(ed[0] if isinstance(ed,list) else ed).date()
 except:return None
def tradier_put_rows(ticker,expiry):
 j=tradier_get('/markets/options/chains',{'symbol':ticker,'expiration':expiry,'greeks':'true'});opts=((j or {}).get('options') or {}).get('option') or [];opts=[opts] if isinstance(opts,dict) else opts;rows=[]
 for o in opts:
  if str(o.get('option_type','')).lower()!='put':continue
  g=o.get('greeks') or {};iv=g.get('mid_iv') if g.get('mid_iv') is not None else g.get('smv_vol');delta=g.get('delta');rows.append({'strike':o.get('strike'),'bid':o.get('bid'),'ask':o.get('ask'),'iv':iv,'delta':delta,'source':'Tradier','delta_source':'Tradier/ORATS' if delta is not None else None})
 return rows
def yfinance_put_rows(ticker,expiry):
 p=yf.Ticker(ticker).option_chain(expiry).puts;return [{'strike':r.strike,'bid':r.bid,'ask':r.ask,'iv':r.impliedVolatility if pd.notna(r.impliedVolatility) else None,'delta':None,'source':'yfinance','delta_source':None} for _,r in p.iterrows()]
def candidate_puts(ticker,price,support,expiry):
 if not expiry:return [],'none'
 source='yfinance'
 if TRADIER_TOKEN:
  try:
   rows=tradier_put_rows(ticker,expiry);source='Tradier'
   if not rows:raise RuntimeError('empty Tradier chain')
  except Exception as e:print(f'Tradier chain fallback for {ticker}: {e}');rows=yfinance_put_rows(ticker,expiry)
 else:rows=yfinance_put_rows(ticker,expiry)
 exp=datetime.strptime(expiry,'%Y-%m-%d').date();today=datetime.now(ET).date();dte=max((exp-today).days,1);ed=earnings_date(ticker);event=bool(ed and today<ed<=exp);clean=[]
 for r in rows:
  try:strike=float(r.get('strike'));bid=float(r.get('bid') or 0);ask=float(r.get('ask') or 0)
  except:continue
  if strike>=price:continue
  mid=(bid+ask)/2 if bid>0 and ask>0 else max(bid,ask)
  if mid<=0:continue
  try:iv=float(r.get('iv')) if r.get('iv') is not None else None
  except:iv=None
  try:delta=float(r.get('delta')) if r.get('delta') is not None else None
  except:delta=None
  ds=r.get('delta_source')
  if delta is None and iv:delta=put_delta_bs(price,strike,iv,dte);ds='Black-Scholes estimate'
  be=strike-mid;clean.append({'strike':strike,'bid':bid,'ask':ask,'premium':mid,'breakeven':be,'iv_pct':iv*100 if iv else None,'delta':delta,'annualized_return_pct':mid/strike*365/dte*100,'distance_to_support_pct':(be-support)/support*100 if support else None,'dte':dte,'earnings_risk':event,'earnings_date':ed.isoformat() if ed else None,'source':source,'delta_source':ds})
 if not clean:return [],source
 chosen=[];used=set()
 for label,target in [('Conservative',.12),('Preferred',.20),('Aggressive',.30)]:
  pool=[x for x in clean if x['strike'] not in used];wd=[x for x in pool if x['delta'] is not None]
  if not pool:continue
  pick=min(wd,key=lambda x:abs(abs(x['delta'])-target)) if wd else pool[0];used.add(pick['strike']);pick=dict(pick);pick['profile']=label;chosen.append(pick)
 return chosen,source
def market_open_now(now):
 s=mcal.get_calendar('NYSE').schedule(start_date=now.date(),end_date=now.date());return False if s.empty else s.iloc[0].market_open.tz_convert(ET)<=now<=s.iloc[0].market_close.tz_convert(ET)
def should_run(now):
 if os.getenv('GITHUB_EVENT_NAME','') in {'workflow_dispatch','push'}:return True
 if mcal.get_calendar('NYSE').schedule(start_date=now.date(),end_date=now.date()).empty:return False
 cron=os.getenv('GITHUB_EVENT_SCHEDULE','').strip();off=now.utcoffset().total_seconds()/3600
 valid={'0 14 * * 1-5','0 16 * * 1-5','0 18 * * 1-5'} if off==-4 else {'0 15 * * 1-5','0 17 * * 1-5','0 19 * * 1-5'}
 return cron in valid
def main():
 now=datetime.now(ET)
 if not should_run(now):print('Not scheduled');return
 raw={};analyses=[];sources=set()
 for sym in TICKERS:
  h=yf.Ticker(sym).history(period='3mo',interval='1d',auto_adjust=True,actions=False)
  if h.empty:continue
  h=h[['Open','High','Low','Close']].dropna();close=float(h.Close.iloc[-1]);prev=float(h.Close.iloc[-2]);recent=h.tail(45);sane=recent[(recent.High<=close*1.65)&(recent.Low>=close*.45)];recent=sane if len(sane)>=20 else recent;low=float(recent.Low.min());high=float(recent.High.max());fb=fibs(low,high);raw[sym]={'price':close,'change_pct':(close/prev-1)*100,'low45':low,'high45':high,'fib':fb,'ema20':float(h.Close.ewm(span=20,adjust=False).mean().iloc[-1]),'key_support':support_below(close,fb)}
 for sym in PUT_NAMES:
  x=raw[sym];expiry,_=choose_expiry(sym);cands,source=candidate_puts(sym,x['price'],x['key_support'],expiry);sources.add(source);pref=next((c for c in cands if c['profile']=='Preferred'),cands[0] if cands else None);near=abs(x['price']-x['key_support'])/x['price']<=.04;vertical=x['change_pct']>=6;pg=bool(pref and pref['annualized_return_pct']>=35);event=bool(pref and pref['earnings_risk']);score=max(0,min(100,55+(12 if near else 0)+(12 if pg else 0)-(18 if vertical else 0)-(18 if event else 0)-(8 if sym=='SOXL' else 0)));decision='SELL' if score>=68 and not vertical and not event else 'WAIT';clean=[{**c,'strike':f(c['strike']),'bid':f(c['bid']),'ask':f(c['ask']),'premium':f(c['premium']),'breakeven':f(c['breakeven']),'iv_pct':f(c['iv_pct']),'delta':f(c['delta']),'annualized_return_pct':f(c['annualized_return_pct']),'distance_to_support_pct':f(c['distance_to_support_pct'])} for c in cands];risk='3x daily leverage and volatility drag.' if sym=='SOXL' else ('ETF thematic/concentration risk.' if sym in {'QTUM','DRAM'} else ('Earnings falls before expiration.' if event else 'Gap risk and volatility expansion.'));analyses.append({'ticker':sym,'price':f(x['price']),'change_pct':f(x['change_pct']),'decision':decision,'score':int(score),'contract':f"{expiry} ${pref['strike']:.0f}P" if pref else None,'premium':f(pref['premium']) if pref else None,'breakeven':f(pref['breakeven']) if pref else None,'key_support':f(x['key_support']),'fib':{k:f(v) for k,v in x['fib'].items()},'candidates':clean,'option_source':source,'expiration_type':'Standard monthly (third Friday)','trigger':f"Nearest technical support ${x['key_support']:.2f}; prefer stable/reclaiming tape before entry.",'risk':risk,'note':f"Adjusted 45-day swing ${x['low45']:.2f} → ${x['high45']:.2f}; EMA20 ${x['ema20']:.2f}"})
 ranking=sorted(analyses,key=lambda z:z['score'],reverse=True);smh=raw.get('SMH',{});entries=[]
 if smh:
  price=smh['price'];levels=sorted([v for v in smh['fib'].values() if v<=price],reverse=True);entries=[{'zone':f"${price*.995:.0f}–${price*1.005:.0f}",'allocation':20,'label':'starter only'}]
  for i,(label,alloc) in enumerate([('first support',30),('strong support',30),('major accumulation',20)]):
   if i<len(levels):entries.append({'zone':f"${levels[i]-3:.0f}–${levels[i]+3:.0f}",'allocation':alloc,'label':label})
 source_label='Tradier real-time' if sources=={'Tradier'} else ('yfinance fallback' if sources=={'yfinance'} else 'Tradier + yfinance fallback');payload={'updated_et':now.strftime('%Y-%m-%d %I:%M %p ET'),'market_state':'OPEN' if market_open_now(now) else 'CLOSED','option_data_source':source_label,'expiration_policy':'Standard monthly options only (third Friday); no weeklies','tickers':[{'ticker':k,'price':f(v['price']),'change_pct':f(v['change_pct'])} for k,v in raw.items()],'ranking':ranking,'analysis':analyses,'smh':{'price':f(smh.get('price')),'entries':entries}};os.makedirs('data',exist_ok=True);json.dump(payload,open('data/dashboard.json','w'),indent=2);print(json.dumps(payload,indent=2))
if __name__=='__main__':main()
