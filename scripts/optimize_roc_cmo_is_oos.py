import os,json,urllib.request,itertools
import pandas as pd
import numpy as np
SYMS=['SOXL','VGT','SPMO','VOO']; START='2010-01-01'; END='2026-09-16'; COST=5; IS_FRAC=.70
TOKEN=os.environ.get('TRADIER_TOKEN') or os.environ.get('TRADIER_ACCESS_TOKEN')
ROC_LB=[4,5,7,9,11]; ROC_IN=[-.02,-.03,-.04,-.05]; ROC_OUT=[.02,.03,.04,.05]
CMO_LB=[7,10,14,20]; CMO_IN=[-30,-40,-50]; CMO_OUT=[30,40,50]

def hist(s):
 u=f'https://api.tradier.com/v1/markets/history?symbol={s}&interval=daily&start={START}&end={END}'
 q=urllib.request.Request(u,headers={'Authorization':f'Bearer {TOKEN}','Accept':'application/json'})
 with urllib.request.urlopen(q) as r:j=json.load(r)
 d=j['history']['day']; d=[d] if isinstance(d,dict) else d; x=pd.DataFrame(d)
 x['date']=pd.to_datetime(x.date,errors='coerce')
 for c in ['open','close']: x[c]=pd.to_numeric(x[c],errors='coerce')
 return x.dropna(subset=['date','open','close']).sort_values('date').drop_duplicates('date').reset_index(drop=True)

def caches(x):
 c=x.close.to_numpy(float); rocs={lb:np.r_[np.full(lb,np.nan),c[lb:]/c[:-lb]-1] for lb in ROC_LB}
 d=np.diff(c,prepend=np.nan); up=np.where(d>0,d,0.0); dn=np.where(d<0,-d,0.0); cmos={}
 for lb in CMO_LB:
  su=pd.Series(up).rolling(lb).sum().to_numpy(); sd=pd.Series(dn).rolling(lb).sum().to_numpy(); cmos[lb]=100*(su-sd)/(su+sd)
 return rocs,cmos

def bt(op,cl,dates,roc,cmo,a,b,ri,ro,ci,co):
 cash=1.; sh=0.; pos=False; eq=[]; trade_rets=[]; exp=0; start=max(a,21); entry_val=None
 for i in range(start,b):
  r=roc[i-1]; m=cmo[i-1]
  if not pos and r<ri and m<ci:
   sh=cash*(1-COST/10000)/op[i]; cash=0.; pos=True; entry_val=sh*op[i]
  elif pos and (r>ro or m>co):
   cash=sh*op[i]*(1-COST/10000); trade_rets.append(cash/entry_val-1); sh=0.; pos=False
  eq.append(cash if not pos else sh*cl[i]); exp+=int(pos)
 if pos:
  cash=sh*cl[b-1]*(1-COST/10000); trade_rets.append(cash/entry_val-1); eq[-1]=cash
 e=np.asarray(eq,float); rr=np.r_[0,e[1:]/e[:-1]-1]; peak=np.maximum.accumulate(e); dd=e/peak-1; years=(dates[b-1]-dates[start]).days/365.25
 cagr=e[-1]**(1/years)-1; mdd=float(dd.min()); sd=rr.std(ddof=1); sharpe=float(np.sqrt(252)*rr.mean()/sd) if sd>0 else np.nan; w=np.asarray(trade_rets)
 gp=w[w>0].sum() if len(w) else 0.; gl=-w[w<0].sum() if len(w) else 0.
 return {'trades':len(w),'win_rate':float((w>0).mean()) if len(w) else np.nan,'avg_trade':float(w.mean()) if len(w) else np.nan,'profit_factor':float(gp/gl) if gl>0 else None,'cagr':float(cagr),'sharpe':sharpe,'max_dd':mdd,'calmar':float(cagr/abs(mdd)) if mdd<0 else None,'exposure':exp/len(e)}

def one(sym):
 x=hist(sym); op=x.open.to_numpy(float); cl=x.close.to_numpy(float); dates=x.date.tolist(); rocs,cmos=caches(x); cut=int(len(x)*IS_FRAC); best=None
 for rl,ri,ro,clb,ci,co in itertools.product(ROC_LB,ROC_IN,ROC_OUT,CMO_LB,CMO_IN,CMO_OUT):
  m=bt(op,cl,dates,rocs[rl],cmos[clb],0,cut,ri,ro,ci,co)
  if m['trades']<10 or m['calmar'] is None or np.isnan(m['calmar']): continue
  score=(m['calmar'],m['sharpe'],m['cagr'])
  if best is None or score>best[0]: best=(score,(rl,ri,ro,clb,ci,co),m)
 p=best[1]; oos=bt(op,cl,dates,rocs[p[0]],cmos[p[3]],cut,len(x),p[1],p[2],p[4],p[5])
 return {'symbol':sym,'is_start':str(x.date.iloc[0].date()),'is_end':str(x.date.iloc[cut-1].date()),'oos_start':str(x.date.iloc[cut].date()),'oos_end':str(x.date.iloc[-1].date()),'params':{'roc_lb':p[0],'roc_entry':p[1],'roc_exit':p[2],'cmo_lb':p[3],'cmo_entry':p[4],'cmo_exit':p[5]},'IS':best[2],'OOS':oos}
out=[one(s) for s in SYMS]; print(json.dumps(out,indent=2)); rows=[]
for z in out:
 r={'symbol':z['symbol'],**z['params'],**{f'IS_{k}':v for k,v in z['IS'].items()},**{f'OOS_{k}':v for k,v in z['OOS'].items()},**{k:z[k] for k in ['is_start','is_end','oos_start','oos_end']}}; rows.append(r)
pd.DataFrame(rows).to_csv('roc_cmo_is_oos_results.csv',index=False)
