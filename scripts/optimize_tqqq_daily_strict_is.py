#!/usr/bin/env python3
"""Strict IS-only optimization for TQQQ daily mean reversion using Tradier.
Select WR+CCI parameters on IS only, then select a third-indicator configuration on IS only.
Freeze winners before computing validation/OOS. Next-open execution throughout.
"""
import os,json,math,itertools
from pathlib import Path
import numpy as np,pandas as pd,requests
S='TQQQ'; WARM='2015-09-01'; START='2016-09-06'; IS_END='2022-09-02'; VAL_START='2022-09-06'; VAL_END='2024-09-03'; OOS_START='2024-09-04'; END='2026-09-04'; INITIAL=100000.; OUT=Path('data/williams_r');OUT.mkdir(parents=True,exist_ok=True)
def get(sym):
 t=os.environ['TRADIER_TOKEN'];r=requests.get('https://api.tradier.com/v1/markets/history',headers={'Authorization':f'Bearer {t}','Accept':'application/json'},params={'symbol':sym,'interval':'daily','start':WARM,'end':END},timeout=30);r.raise_for_status();x=r.json().get('history',{}).get('day',[]);x=[x] if isinstance(x,dict) else x;d=pd.DataFrame(x);d['date']=pd.to_datetime(d.date);d=d.set_index('date').sort_index()
 for c in ['open','high','low','close','volume']:d[c]=pd.to_numeric(d[c],errors='coerce')
 return d.dropna(subset=['open','high','low','close'])
def wr(d,n):
 h=d.high.rolling(n).max();l=d.low.rolling(n).min();return -100*(h-d.close)/(h-l).replace(0,np.nan)
def cci(d,n):
 tp=(d.high+d.low+d.close)/3;m=tp.rolling(n).mean();md=tp.rolling(n).apply(lambda x:np.mean(np.abs(x-x.mean())),raw=True);return (tp-m)/(0.015*md.replace(0,np.nan))
def adx(d,n):
 up=d.high.diff();dn=-d.low.diff();p=pd.Series(np.where((up>dn)&(up>0),up,0.),index=d.index);m=pd.Series(np.where((dn>up)&(dn>0),dn,0.),index=d.index);tr=pd.concat([d.high-d.low,(d.high-d.close.shift()).abs(),(d.low-d.close.shift()).abs()],axis=1).max(axis=1);a=tr.ewm(alpha=1/n,adjust=False,min_periods=n).mean();pi=100*p.ewm(alpha=1/n,adjust=False,min_periods=n).mean()/a;mi=100*m.ewm(alpha=1/n,adjust=False,min_periods=n).mean()/a;dx=100*(pi-mi).abs()/(pi+mi).replace(0,np.nan);return dx.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
def rvol(d,n):return d.volume/d.volume.shift(1).rolling(n).mean()
def run(d,entry,exit_rule,start,end):
 x=d.loc[start:end];eq=INITIAL;pos=False;e=None;ts=[];curve=[]
 for i,(dt,row) in enumerate(x.iterrows()):
  if pos and e['exit']:
   rr=float(row.open)/e['px']-1;eq*=1+rr;ts.append(rr);pos=False;e=None
  elif not pos and i>0:
   pdt=x.index[i-1]
   if bool(entry.loc[pdt]):pos=True;e={'px':float(row.open),'exit':False}
  curve.append(eq if not pos else eq*float(row.close)/e['px'])
  if pos:e['exit']=bool(exit_rule.loc[dt])
 if pos:
  rr=float(x.close.iloc[-1])/e['px']-1;eq*=1+rr;ts.append(rr);curve[-1]=eq
 years=(x.index[-1]-x.index[0]).days/365.25;cr=(eq/INITIAL)**(1/years)-1;cv=pd.Series(curve,index=x.index);dr=cv.pct_change().fillna(0);sh=dr.mean()/dr.std(ddof=1)*math.sqrt(252) if dr.std(ddof=1)>0 else -99;dd=float((cv/cv.cummax()-1).min());a=np.array(ts);pf=float(a[a>0].sum()/-a[a<0].sum()) if (a<0).any() else float('inf')
 return {'trades':len(ts),'win_rate':float((a>0).mean()) if len(a) else 0,'pf':pf,'avg_trade':float(a.mean()) if len(a) else 0,'total_return':eq/INITIAL-1,'cagr':cr,'sharpe':sh,'max_dd':dd,'calmar':cr/abs(dd) if dd<0 else np.nan,'ending':eq}
d=get(S);q=get('QQQ').reindex(d.index).ffill();WR={n:wr(d,n) for n in range(2,11)};CCI={n:cci(d,n) for n in range(3,16)}
base=[]
for wn,wt,cn,ct in itertools.product(range(2,11),[-70,-75,-80,-85,-90,-95],range(3,16),[-50,-75,-80,-100,-125,-150,-175,-200]):
 ent=(WR[wn]<wt)&(CCI[cn]<ct);ex=(d.close>d.high.shift(1))|(WR[wn]>-30);m=run(d,ent,ex,START,IS_END)
 if m['trades']>=30:base.append((m['sharpe'],m['cagr'],wn,wt,cn,ct,m))
base_sh=max(base,key=lambda z:(z[0],z[1]));base_ret=max(base,key=lambda z:(z[1],z[0]));_,_,wn,wt,cn,ct,bis=base_sh;core=(WR[wn]<wt)&(CCI[cn]<ct);ex=(d.close>d.high.shift(1))|(WR[wn]>-30)
cands=[('NONE','none',core)]
for n in [5,7,10,14,20,30]:
 a=adx(d,n)
 for th in [10,15,20,25,30,35,40,45]:cands.append((f'ADX{n}_GE_{th}','adx',core&(a>=th)))
 for th in [20,25,30,35,40,45,50,60]:cands.append((f'ADX{n}_LE_{th}','adx',core&(a<=th)))
for n in [10,20,30,50]:
 rv=rvol(d,n)
 for th in [.5,.75,1,1.25,1.5,2]:cands.append((f'RVOL{n}_GE_{th}','rvol',core&(rv>=th)))
for n in [10,20,50,100,200]:
 dist=d.close/d.close.ewm(span=n,adjust=False).mean()-1
 for th in [-.2,-.15,-.1,-.05,0,.05]:cands.append((f'TQQQ_EMA{n}_DIST_GE_{th}','trend',core&(dist>=th)))
for n in [20,50,100,200]:
 dist=q.close/q.close.ewm(span=n,adjust=False).mean()-1
 for th in [-.15,-.1,-.05,0,.05]:cands.append((f'QQQ_EMA{n}_DIST_GE_{th}','qqq',core&(dist>=th)))
for n in [1,2,3,5,10,20]:
 rr=q.close.pct_change(n)
 for th in [-.15,-.1,-.075,-.05,-.03,-.02,-.01,0]:cands.append((f'QQQ_RET{n}_LE_{th}','qqqret',core&(rr<=th)))
sc=[]
for name,fam,ent in cands:
 m=run(d,ent,ex,START,IS_END)
 if m['trades']>=25:sc.append((m['sharpe'],m['cagr'],name,fam,ent,m))
win=max(sc,key=lambda z:(z[0],z[1]));_,_,name,fam,went,wis=win;periods={'IS':(START,IS_END),'VALIDATION':(VAL_START,VAL_END),'OOS':(OOS_START,END)}
report={'selection_policy':'Phase1 maximize IS Sharpe (min 30 trades); Phase2 maximize IS Sharpe (min 25 trades); validation/OOS never used for selection','phase1_candidates':len(base),'phase2_candidates':len(sc),'base_is_sharpe_winner':{'wr_n':wn,'wr_entry':wt,'cci_n':cn,'cci_entry':ct,'IS':bis},'base_is_cagr_winner':{'wr_n':base_ret[2],'wr_entry':base_ret[3],'cci_n':base_ret[4],'cci_entry':base_ret[5],'IS':base_ret[6]},'third_indicator_winner':name,'third_family':fam,'third_IS':wis,'performance':{},'baseline_performance':{}}
for p,(a,b) in periods.items():report['performance'][p]=run(d,went,ex,a,b);report['baseline_performance'][p]=run(d,core,ex,a,b)
(OUT/'tqqq_daily_strict_is_optimization.json').write_text(json.dumps(report,indent=2,allow_nan=True));print(json.dumps(report,indent=2,allow_nan=True))
# trigger 2
