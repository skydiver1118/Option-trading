#!/usr/bin/env python3
import os,math,json
from pathlib import Path
import numpy as np,pandas as pd,requests
S='TQQQ'; WARM='2015-09-01'; START='2016-09-06'; END='2026-09-04'; INITIAL=100000.; OUT=Path('data/williams_r');OUT.mkdir(parents=True,exist_ok=True)
WR_N=3; WR_ENTRY=-70; WR_EXIT=-30; CCI_N=4; CCI_ENTRY=-75

def get():
 t=os.environ['TRADIER_TOKEN'];r=requests.get('https://api.tradier.com/v1/markets/history',headers={'Authorization':f'Bearer {t}','Accept':'application/json'},params={'symbol':S,'interval':'daily','start':WARM,'end':END},timeout=30);r.raise_for_status();x=r.json().get('history',{}).get('day',[]);x=[x] if isinstance(x,dict) else x;d=pd.DataFrame(x);d['date']=pd.to_datetime(d.date);d=d.set_index('date').sort_index();
 for c in ['open','high','low','close','volume']:d[c]=pd.to_numeric(d[c],errors='coerce')
 return d.dropna(subset=['open','high','low','close'])
def wr(d,n):
 h=d.high.rolling(n).max();l=d.low.rolling(n).min();return -100*(h-d.close)/(h-l).replace(0,np.nan)
def cci(d,n):
 tp=(d.high+d.low+d.close)/3;m=tp.rolling(n).mean();md=tp.rolling(n).apply(lambda x:np.mean(np.abs(x-x.mean())),raw=True);return (tp-m)/(0.015*md.replace(0,np.nan))
def run(d):
 d=d.copy();d['wr']=wr(d,WR_N);d['cci']=cci(d,CCI_N);d['ph']=d.high.shift(1);d=d.loc[START:END]
 eq=INITIAL;pos=False;e=None;tr=[];curve=[]
 for i,(dt,r) in enumerate(d.iterrows()):
  if pos and e['exit']:
   px=float(r.open);rr=px/e['px']-1;eq*=1+rr;tr.append({'entry_signal_date':e['sig'].date().isoformat(),'entry_date':e['date'].date().isoformat(),'entry_price':e['px'],'exit_date':dt.date().isoformat(),'exit_price':px,'return':rr,'holding_days':(dt-e['date']).days});pos=False;e=None
  elif not pos and i>0:
   p=d.iloc[i-1];pdt=d.index[i-1]
   if bool((p.wr<WR_ENTRY) and (p.cci<CCI_ENTRY)):pos=True;e={'sig':pdt,'date':dt,'px':float(r.open),'exit':False}
  curve.append(eq if not pos else eq*float(r.close)/e['px'])
  if pos:e['exit']=bool((r.close>r.ph) or (r.wr>WR_EXIT))
 if pos:
  px=float(d.close.iloc[-1]);rr=px/e['px']-1;eq*=1+rr;tr.append({'entry_signal_date':e['sig'].date().isoformat(),'entry_date':e['date'].date().isoformat(),'entry_price':e['px'],'exit_date':d.index[-1].date().isoformat(),'exit_price':px,'return':rr,'holding_days':(d.index[-1]-e['date']).days});curve[-1]=eq
 t=pd.DataFrame(tr);c=pd.Series(curve,index=d.index);years=(d.index[-1]-d.index[0]).days/365.25;tot=eq/INITIAL-1;cagr=(eq/INITIAL)**(1/years)-1;ret=c.pct_change().fillna(0);sh=ret.mean()/ret.std(ddof=1)*math.sqrt(252);dd=float((c/c.cummax()-1).min());a=t['return'].to_numpy();pf=float(a[a>0].sum()/-a[a<0].sum());hold=float(d.close.iloc[-1]/d.open.iloc[0]-1);hc=(1+hold)**(1/years)-1;hcurve=d.close/d.open.iloc[0]*INITIAL;hr=hcurve.pct_change().fillna(0);hsh=hr.mean()/hr.std(ddof=1)*math.sqrt(252);hdd=float((hcurve/hcurve.cummax()-1).min())
 m={'rule':{'wr_n':WR_N,'wr_entry':WR_ENTRY,'cci_n':CCI_N,'cci_entry':CCI_ENTRY,'wr_exit':WR_EXIT},'trades':len(t),'win_rate':float((a>0).mean()),'profit_factor':pf,'avg_trade':float(a.mean()),'median_trade':float(np.median(a)),'avg_holding_days':float(t.holding_days.mean()),'total_return':tot,'cagr':cagr,'sharpe':sh,'max_drawdown':dd,'calmar':cagr/abs(dd),'ending_value':eq,'hold_total_return':hold,'hold_cagr':hc,'hold_sharpe':hsh,'hold_max_drawdown':hdd,'hold_calmar':hc/abs(hdd),'hold_ending_value':INITIAL*(1+hold)}
 return m,t
if __name__=='__main__':
 m,t=run(get());t.to_csv(OUT/'tqqq_final_frozen_10y_trades.csv',index=False);(OUT/'tqqq_final_frozen_10y_metrics.json').write_text(json.dumps(m,indent=2));print(json.dumps(m,indent=2))
