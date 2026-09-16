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

def add_ind(x,rl,cl):
 y=x.copy(); y['roc']=y.close/y.close.shift(rl)-1
 d=y.close.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0); su=up.rolling(cl).sum(); sd=dn.rolling(cl).sum(); y['cmo']=100*(su-sd)/(su+sd)
 return y

def bt(y,a,b,ri,ro,ci,co):
 cash=1.; sh=0.; pos=False; ent=None; ed=None; eq=[]; tr=[]; exp=0; start=max(a,21)
 for i in range(start,b):
  s=y.iloc[i-1]; op=y.open.iloc[i]
  enter=(s.roc<ri and s.cmo<ci); exit=(s.roc>ro or s.cmo>co)
  if not pos and enter:
   sh=cash*(1-COST/10000)/op; cash=0.; pos=True; ent=op; ed=y.date.iloc[i]
  elif pos and exit:
   cash=sh*op*(1-COST/10000); tr.append((ed,y.date.iloc[i],cash)); sh=0.; pos=False
  val=cash if not pos else sh*y.close.iloc[i]; eq.append(val); exp+=int(pos)
 if pos:
  cash=sh*y.close.iloc[b-1]*(1-COST/10000); tr.append((ed,y.date.iloc[b-1],cash)); eq[-1]=cash
 if len(eq)<2:return None
 e=pd.Series(eq); r=e.pct_change().fillna(0); dd=e/e.cummax()-1; years=(y.date.iloc[b-1]-y.date.iloc[start]).days/365.25
 cagr=e.iloc[-1]**(1/years)-1 if years>0 else np.nan; mdd=float(dd.min()); sharpe=float(np.sqrt(252)*r.mean()/r.std()) if r.std()>0 else np.nan
 wins=[]; prev=1.
 for _,_,v in tr: wins.append(v/prev-1); prev=v
 w=np.array(wins) if wins else np.array([]); gp=w[w>0].sum() if len(w) else 0; gl=-w[w<0].sum() if len(w) else 0
 return {'trades':len(tr),'win_rate':float((w>0).mean()) if len(w) else np.nan,'avg_trade':float(w.mean()) if len(w) else np.nan,'profit_factor':float(gp/gl) if gl>0 else None,'cagr':float(cagr),'sharpe':sharpe,'max_dd':mdd,'calmar':float(cagr/abs(mdd)) if mdd<0 else None,'exposure':exp/len(eq)}

def one(sym):
 x=hist(sym); cut=int(len(x)*IS_FRAC); best=None
 for rl,ri,ro,cl,ci,co in itertools.product(ROC_LB,ROC_IN,ROC_OUT,CMO_LB,CMO_IN,CMO_OUT):
  y=add_ind(x,rl,cl); m=bt(y,0,cut,ri,ro,ci,co)
  if not m or m['trades']<10 or m['calmar'] is None or np.isnan(m['calmar']): continue
  score=(m['calmar'],m['sharpe'],m['cagr'])
  if best is None or score>best[0]: best=(score,(rl,ri,ro,cl,ci,co),m)
 p=best[1]; y=add_ind(x,p[0],p[3]); oos=bt(y,cut,len(x),p[1],p[2],p[4],p[5])
 return {'symbol':sym,'is_start':str(x.date.iloc[0].date()),'is_end':str(x.date.iloc[cut-1].date()),'oos_start':str(x.date.iloc[cut].date()),'oos_end':str(x.date.iloc[-1].date()),'params':{'roc_lb':p[0],'roc_entry':p[1],'roc_exit':p[2],'cmo_lb':p[3],'cmo_entry':p[4],'cmo_exit':p[5]},'IS':best[2],'OOS':oos}

out=[one(s) for s in SYMS]
print(json.dumps(out,indent=2));
rows=[]
for z in out:
 r={'symbol':z['symbol'],**z['params']}
 for k,v in z['IS'].items():r['IS_'+k]=v
 for k,v in z['OOS'].items():r['OOS_'+k]=v
 r.update({k:z[k] for k in ['is_start','is_end','oos_start','oos_end']}); rows.append(r)
pd.DataFrame(rows).to_csv('roc_cmo_is_oos_results.csv',index=False)
