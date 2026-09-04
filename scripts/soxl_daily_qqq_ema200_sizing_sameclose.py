#!/usr/bin/env python3
import os, math, requests
from pathlib import Path
import pandas as pd
import numpy as np
TOKEN=os.environ['TRADIER_TOKEN']; BASE='https://api.tradier.com/v1'; OUT=Path('data/williams_r'); OUT.mkdir(parents=True,exist_ok=True)
WR_N=2; WE=-90; WX=-30; CCI_N=5; CE=-80; CX=0; BEAR_W=0.5

def fetch(sym):
 s=requests.Session(); s.headers.update({'Authorization':f'Bearer {TOKEN}','Accept':'application/json'}); end=pd.Timestamp.today().date(); start=(pd.Timestamp(end)-pd.DateOffset(years=11)).date(); rows=[]; cur=pd.Timestamp(start); endt=pd.Timestamp(end)
 while cur<=endt:
  stop=min(endt,cur+pd.DateOffset(years=8)-pd.Timedelta(days=1)); r=s.get(f'{BASE}/markets/history',params={'symbol':sym,'interval':'daily','start':cur.date().isoformat(),'end':stop.date().isoformat()},timeout=30); r.raise_for_status(); d=(r.json().get('history') or {}).get('day') or []; rows += [d] if isinstance(d,dict) else d; cur=stop+pd.Timedelta(days=1)
 x=pd.DataFrame(rows); x['date']=pd.to_datetime(x.date)
 for c in ['open','high','low','close']: x[c]=pd.to_numeric(x[c],errors='coerce')
 return x.drop_duplicates('date').sort_values('date').set_index('date')

def prep():
 s=fetch('SOXL'); q=fetch('QQQ'); q['ema200']=q.close.ewm(span=200,adjust=False,min_periods=200).mean(); x=s.join(q[['close','ema200']].rename(columns={'close':'qqq_close'}),how='inner'); hh=x.high.rolling(2).max(); ll=x.low.rolling(2).min(); x['wr']=-100*(hh-x.close)/(hh-ll); tp=(x.high+x.low+x.close)/3; ma=tp.rolling(5).mean(); md=tp.rolling(5).apply(lambda z:np.mean(np.abs(z-np.mean(z))),raw=True); x['cci']=(tp-ma)/(0.015*md); x['prev_high']=x.high.shift(1); return x

def bt(x):
 cash=100000.; sh=0.; pos=False; ent=None; entdt=None; alloc=0.; trs=[]; eq=[]
 for dt,r in x.iterrows():
  if pd.notna(r.wr) and pd.notna(r.cci):
   if pos and ((r.close>r.prev_high) or (r.wr>WX) or (r.cci>CX)):
    px=float(r.close); cash+=sh*px; trs.append((entdt,dt,px/ent-1,alloc/100000.)); sh=0.; pos=False; alloc=0.; ent=None; entdt=None
   elif (not pos) and (r.wr<WE) and (r.cci<CE):
    w=1.0 if r.qqq_close>r.ema200 else BEAR_W; px=float(r.close); alloc=cash*w; sh=alloc/px; cash-=alloc; pos=True; ent=px; entdt=dt
  eq.append((dt,cash+(sh*r.close if pos else 0),pos))
 e=pd.DataFrame(eq,columns=['date','equity','pos']).set_index('date'); t=pd.DataFrame(trs,columns=['entry','exit','return','weight']); rr=e.equity.pct_change().fillna(0); yrs=max((e.index[-1]-e.index[0]).days/365.25,1/365.25); total=e.equity.iloc[-1]/e.equity.iloc[0]-1; cagr=(1+total)**(1/yrs)-1; mdd=(e.equity/e.equity.cummax()-1).min(); sd=rr.std(ddof=0); sharpe=rr.mean()/sd*np.sqrt(252) if sd else np.nan; calmar=cagr/abs(mdd) if mdd<0 else np.nan
 if len(t):
  w=t[t['return']>0]['return']; l=t[t['return']<0]['return']; pf=w.sum()/abs(l.sum()) if len(l) else math.inf; win=(t['return']>0).mean(); avg=t['return'].mean(); med=t['return'].median()
 else: pf=win=avg=med=np.nan
 return {'trades':len(t),'win_rate':win,'profit_factor':pf,'avg_trade':avg,'median_trade':med,'total_return':total,'cagr':cagr,'sharpe':sharpe,'max_drawdown':mdd,'calmar':calmar,'exposure':e.pos.mean(),'ending_equity':e.equity.iloc[-1]}

def main():
 x=prep(); end=x.index.max().normalize(); start=end-pd.DateOffset(years=10); is_end=start+pd.DateOffset(years=6)-pd.Timedelta(days=1); va_start=is_end+pd.Timedelta(days=1); va_end=start+pd.DateOffset(years=8)-pd.Timedelta(days=1); oo_start=va_end+pd.Timedelta(days=1); segs={'IS':x.loc[start:is_end],'Validation':x.loc[va_start:va_end],'OOS':x.loc[oo_start:end]}; rows=[]
 for p,seg in segs.items():
  z=bt(seg); z.update(period=p,start=seg.index.min().date(),end=seg.index.max().date(),execution='same_close',bear_weight=BEAR_W); rows.append(z)
 pd.DataFrame(rows).to_csv(OUT/'soxl_daily_qqq_ema200_50pct_sameclose.csv',index=False); print(pd.DataFrame(rows).to_string(index=False))
if __name__=='__main__': main()
