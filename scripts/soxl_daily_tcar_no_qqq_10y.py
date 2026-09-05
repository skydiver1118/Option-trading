#!/usr/bin/env python3
# trigger 2026-09-04
import os, math, requests
from pathlib import Path
import pandas as pd, numpy as np
TOKEN=os.environ['TRADIER_TOKEN']; BASE='https://api.tradier.com/v1'; OUT=Path('data/williams_r'); OUT.mkdir(parents=True,exist_ok=True)

def fetch(sym):
 s=requests.Session(); s.headers.update({'Authorization':f'Bearer {TOKEN}','Accept':'application/json'}); end=pd.Timestamp.today().date(); start=(pd.Timestamp(end)-pd.DateOffset(years=11)).date(); rows=[]; cur=pd.Timestamp(start); endt=pd.Timestamp(end)
 while cur<=endt:
  stop=min(endt,cur+pd.DateOffset(years=8)-pd.Timedelta(days=1)); r=s.get(f'{BASE}/markets/history',params={'symbol':sym,'interval':'daily','start':cur.date().isoformat(),'end':stop.date().isoformat()},timeout=30); r.raise_for_status(); d=(r.json().get('history') or {}).get('day') or []; rows += [d] if isinstance(d,dict) else d; cur=stop+pd.Timedelta(days=1)
 x=pd.DataFrame(rows); x['date']=pd.to_datetime(x.date)
 for c in ['open','high','low','close','volume']: x[c]=pd.to_numeric(x[c],errors='coerce')
 return x.drop_duplicates('date').sort_values('date').set_index('date')

def prep():
 x=fetch('SOXL'); hh=x.high.rolling(2).max(); ll=x.low.rolling(2).min(); x['wr']=-100*(hh-x.close)/(hh-ll)
 tp=(x.high+x.low+x.close)/3; ma=tp.rolling(5).mean(); md=tp.rolling(5).apply(lambda z:np.mean(np.abs(z-np.mean(z))),raw=True); x['cci']=(tp-ma)/(0.015*md)
 tr=pd.concat([(x.high-x.low).abs(),(x.high-x.close.shift(1)).abs(),(x.low-x.close.shift(1)).abs()],axis=1).max(axis=1); up=x.high.diff(); dn=-x.low.diff(); pdm=np.where((up>dn)&(up>0),up,0.); mdm=np.where((dn>up)&(dn>0),dn,0.); atr=tr.rolling(20).mean(); pdi=100*pd.Series(pdm,index=x.index).rolling(20).mean()/atr; mdi=100*pd.Series(mdm,index=x.index).rolling(20).mean()/atr; x['adx20']=(100*(pdi-mdi).abs()/(pdi+mdi)).rolling(20).mean(); x['prev_high']=x.high.shift(1); return x

def run(x):
 cash=100000.; sh=0.; pos=False; pending=None; ent=None; entdt=None; sigdt=None; trades=[]; eq=[]
 for dt,r in x.iterrows():
  if pending:
   side=pending
   if side=='buy' and not pos:
    px=float(r.open); sh=cash/px; cash=0.; pos=True; ent=px; entdt=dt
   elif side=='sell' and pos:
    px=float(r.open); cash=sh*px; trades.append({'entry_signal_date':sigdt,'entry_date':entdt,'entry_price':ent,'exit_date':dt,'exit_price':px,'return':px/ent-1,'holding_days':(dt-entdt).days}); sh=0.; pos=False; ent=entdt=None
   pending=None
  if pd.notna(r.wr) and pd.notna(r.cci) and pd.notna(r.adx20):
   if pos and ((r.close>r.prev_high) or (r.wr>-30)): pending='sell'
   elif (not pos) and (r.wr<-90) and (r.cci<-80) and (r.adx20>=15): pending='buy'; sigdt=dt
  eq.append((dt,cash if not pos else sh*r.close,pos))
 e=pd.DataFrame(eq,columns=['date','equity','pos']).set_index('date'); t=pd.DataFrame(trades)
 rr=e.equity.pct_change().fillna(0); yrs=(e.index[-1]-e.index[0]).days/365.25; total=e.equity.iloc[-1]/e.equity.iloc[0]-1; cagr=(1+total)**(1/yrs)-1; mdd=(e.equity/e.equity.cummax()-1).min(); sd=rr.std(ddof=0); sharpe=rr.mean()/sd*np.sqrt(252) if sd else np.nan; calmar=cagr/abs(mdd) if mdd<0 else np.nan
 if len(t):
  w=t[t['return']>0]['return']; l=t[t['return']<0]['return']; pf=w.sum()/abs(l.sum()) if len(l) else math.inf; win=(t['return']>0).mean(); avg=t['return'].mean(); med=t['return'].median(); ah=t['holding_days'].mean()
 else: pf=win=avg=med=ah=np.nan
 stats=dict(start=e.index[0].date(),end=e.index[-1].date(),trades=len(t),win_rate=win,profit_factor=pf,avg_trade=avg,median_trade=med,avg_holding_days=ah,total_return=total,cagr=cagr,sharpe=sharpe,max_drawdown=mdd,calmar=calmar,exposure=e.pos.mean(),ending_equity=e.equity.iloc[-1])
 return stats,t,e

def main():
 x=prep(); end=x.index.max().normalize(); start=end-pd.DateOffset(years=10); seg=x.loc[start:end]; stats,t,e=run(seg); pd.DataFrame([stats]).to_csv(OUT/'soxl_daily_tcar_no_qqq_10y_performance.csv',index=False); t.to_csv(OUT/'soxl_daily_tcar_no_qqq_10y_trades.csv',index=False); e.to_csv(OUT/'soxl_daily_tcar_no_qqq_10y_equity.csv'); print(pd.DataFrame([stats]).to_string(index=False)); print(t.to_string(index=False))
if __name__=='__main__': main()
