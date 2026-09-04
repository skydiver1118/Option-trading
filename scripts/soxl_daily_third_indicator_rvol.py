#!/usr/bin/env python3
import os, math, requests
from pathlib import Path
import pandas as pd, numpy as np
TOKEN=os.environ['TRADIER_TOKEN']; BASE='https://api.tradier.com/v1'; OUT=Path('data/williams_r'); OUT.mkdir(parents=True,exist_ok=True)
WE=-90; WX=-30; CE=-80
RVOL_NS=[10,20,30,50]; THRS=[0.75,1.0,1.25,1.5,2.0,2.5]

def fetch():
 s=requests.Session(); s.headers.update({'Authorization':f'Bearer {TOKEN}','Accept':'application/json'}); end=pd.Timestamp.today().date(); start=(pd.Timestamp(end)-pd.DateOffset(years=11)).date(); rows=[]; cur=pd.Timestamp(start); endt=pd.Timestamp(end)
 while cur<=endt:
  stop=min(endt,cur+pd.DateOffset(years=8)-pd.Timedelta(days=1)); r=s.get(f'{BASE}/markets/history',params={'symbol':'SOXL','interval':'daily','start':cur.date().isoformat(),'end':stop.date().isoformat()},timeout=30); r.raise_for_status(); d=(r.json().get('history') or {}).get('day') or []; rows += [d] if isinstance(d,dict) else d; cur=stop+pd.Timedelta(days=1)
 x=pd.DataFrame(rows); x['date']=pd.to_datetime(x.date)
 for c in ['open','high','low','close','volume']: x[c]=pd.to_numeric(x[c],errors='coerce')
 return x.drop_duplicates('date').sort_values('date').set_index('date')

def prep(x,n):
 x=x.copy(); hh=x.high.rolling(2).max(); ll=x.low.rolling(2).min(); x['wr']=-100*(hh-x.close)/(hh-ll); tp=(x.high+x.low+x.close)/3; ma=tp.rolling(5).mean(); md=tp.rolling(5).apply(lambda z:np.mean(np.abs(z-np.mean(z))),raw=True); x['cci']=(tp-ma)/(0.015*md); x['prev_high']=x.high.shift(1); x['rvol']=x.volume/x.volume.shift(1).rolling(n).mean(); return x

def bt(seg,n,thr):
 x=prep(seg,n); cash=100000.; sh=0.; pos=False; pending=None; ent=None; entdt=None; trs=[]; eq=[]
 for dt,r in x.iterrows():
  if pending:
   px=float(r.open)
   if pending=='buy' and not pos: sh=cash/px; cash=0.; pos=True; ent=px; entdt=dt
   elif pending=='sell' and pos: cash=sh*px; trs.append((entdt,dt,px/ent-1)); sh=0.; pos=False
   pending=None
  if pd.notna(r.wr) and pd.notna(r.cci) and pd.notna(r.rvol):
   entry=(r.wr<WE) and (r.cci<CE) and (r.rvol>=thr); exit_sig=(r.close>r.prev_high) or (r.wr>WX)
   if pos and exit_sig: pending='sell'
   elif (not pos) and entry: pending='buy'
  eq.append((dt,cash if not pos else sh*float(r.close),pos))
 e=pd.DataFrame(eq,columns=['date','equity','pos']).set_index('date'); t=pd.DataFrame(trs,columns=['entry','exit','return']); rr=e.equity.pct_change().fillna(0); yrs=max((e.index[-1]-e.index[0]).days/365.25,1/365.25); total=e.equity.iloc[-1]/e.equity.iloc[0]-1; cagr=(1+total)**(1/yrs)-1; mdd=(e.equity/e.equity.cummax()-1).min(); sd=rr.std(ddof=0); sharpe=rr.mean()/sd*np.sqrt(252) if sd>0 else np.nan; calmar=cagr/abs(mdd) if mdd<0 else np.nan
 if len(t):
  w=t[t['return']>0]['return']; l=t[t['return']<0]['return']; pf=w.sum()/abs(l.sum()) if len(l) else math.inf; win=(t['return']>0).mean(); avg=t['return'].mean(); med=t['return'].median(); hold=(pd.to_datetime(t.exit)-pd.to_datetime(t.entry)).dt.days.mean()
 else: pf=win=avg=med=hold=np.nan
 return {'rvol_n':n,'rvol_thr':thr,'trades':len(t),'win_rate':win,'profit_factor':pf,'avg_trade':avg,'median_trade':med,'avg_hold_days':hold,'total_return':total,'cagr':cagr,'sharpe':sharpe,'max_drawdown':mdd,'calmar':calmar,'exposure':e.pos.mean(),'ending_equity':e.equity.iloc[-1]}

def bucket_stats(seg,n=20):
 x=prep(seg,n); s=x[(x.wr<WE)&(x.cci<CE)&x.rvol.notna()].copy(); bins=[0,.75,1,1.25,1.5,2,np.inf]; labels=['<0.75','0.75-1','1-1.25','1.25-1.5','1.5-2','>2']; s['bucket']=pd.cut(s.rvol,bins=bins,labels=labels,right=False); return s.groupby('bucket',observed=False).agg(signals=('rvol','size'),mean_rvol=('rvol','mean')).reset_index()

def main():
 x=fetch(); end=x.index.max().normalize(); start=end-pd.DateOffset(years=10); is_end=start+pd.DateOffset(years=6)-pd.Timedelta(days=1); va_start=is_end+pd.Timedelta(days=1); va_end=start+pd.DateOffset(years=8)-pd.Timedelta(days=1); oo_start=va_end+pd.Timedelta(days=1); segs={'IS':x.loc[start:is_end],'Validation':x.loc[va_start:va_end],'OOS':x.loc[oo_start:end]}
 bucket_stats(segs['IS']).to_csv(OUT/'soxl_daily_rvol_is_buckets.csv',index=False)
 grid=[]
 for n in RVOL_NS:
  for thr in THRS:
   z=bt(segs['IS'],n,thr)
   if z['trades']>=30: grid.append(z)
 g=pd.DataFrame(grid).sort_values(['calmar','sharpe','profit_factor','cagr'],ascending=[False,False,False,False]); best=g.iloc[0]; n=int(best.rvol_n); thr=float(best.rvol_thr)
 rows=[]
 for p,seg in segs.items():
  z=bt(seg,n,thr); z.update(period=p,start=seg.index.min().date(),end=seg.index.max().date(),execution='next_open'); rows.append(z)
 pd.DataFrame(rows).to_csv(OUT/'soxl_daily_third_indicator_rvol_best.csv',index=False); g.to_csv(OUT/'soxl_daily_third_indicator_rvol_grid.csv',index=False); print('BEST',n,thr); print(pd.DataFrame(rows).to_string(index=False))
if __name__=='__main__': main()
