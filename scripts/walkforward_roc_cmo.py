import os,json,urllib.request,itertools
import pandas as pd
import numpy as np
SYMS=['SOXL','VGT','SPMO','VOO']; START='2010-01-01'; END='2026-09-16'; COST=5
ROC_LB=[4,5,7,9,11]; ROC_IN=[-.02,-.03,-.04,-.05]; ROC_OUT=[.02,.03,.04,.05]; CMO_LB=[7,10,14,20]; CMO_IN=[-30,-40,-50]; CMO_OUT=[30,40,50]
TOKEN=os.environ.get('TRADIER_TOKEN') or os.environ.get('TRADIER_ACCESS_TOKEN')
def hist(s):
 u=f'https://api.tradier.com/v1/markets/history?symbol={s}&interval=daily&start={START}&end={END}'; q=urllib.request.Request(u,headers={'Authorization':f'Bearer {TOKEN}','Accept':'application/json'})
 with urllib.request.urlopen(q) as r:j=json.load(r)
 d=j['history']['day']; d=[d] if isinstance(d,dict) else d; x=pd.DataFrame(d); x['date']=pd.to_datetime(x.date,errors='coerce')
 for c in ['open','close']:x[c]=pd.to_numeric(x[c],errors='coerce')
 return x.dropna(subset=['date','open','close']).sort_values('date').drop_duplicates('date').reset_index(drop=True)
def cache(x):
 c=x.close.to_numpy(float); ro={lb:np.r_[np.full(lb,np.nan),c[lb:]/c[:-lb]-1] for lb in ROC_LB}; d=np.diff(c,prepend=np.nan); up=np.where(d>0,d,0.); dn=np.where(d<0,-d,0.); cm={}
 for lb in CMO_LB:
  su=pd.Series(up).rolling(lb).sum().to_numpy(); sd=pd.Series(dn).rolling(lb).sum().to_numpy(); cm[lb]=100*(su-sd)/(su+sd)
 return ro,cm
def bt(op,cl,dates,roc,cmo,a,b,ri,rx,ci,cx):
 cash=1.; sh=0.; pos=False; eq=[]; trs=[]; exp=0; st=max(a,21)
 for i in range(st,b):
  r=roc[i-1]; m=cmo[i-1]
  if not pos and r<ri and m<ci: sh=cash*(1-COST/10000)/op[i]; cash=0.; pos=True; ent=sh*op[i]
  elif pos and (r>rx or m>cx): cash=sh*op[i]*(1-COST/10000); trs.append(cash/ent-1); sh=0.; pos=False
  eq.append(cash if not pos else sh*cl[i]); exp+=int(pos)
 if pos: cash=sh*cl[b-1]*(1-COST/10000); trs.append(cash/ent-1); eq[-1]=cash
 if len(eq)<2:return None
 e=np.array(eq); rr=np.r_[0,e[1:]/e[:-1]-1]; dd=e/np.maximum.accumulate(e)-1; yrs=(dates[b-1]-dates[st]).days/365.25; cg=e[-1]**(1/yrs)-1; md=dd.min(); sd=rr.std(ddof=1); w=np.array(trs); gp=w[w>0].sum() if len(w) else 0.; gl=-w[w<0].sum() if len(w) else 0.
 return dict(trades=len(w),win_rate=float((w>0).mean()) if len(w) else np.nan,avg_trade=float(w.mean()) if len(w) else np.nan,profit_factor=float(gp/gl) if gl else None,cagr=float(cg),sharpe=float(np.sqrt(252)*rr.mean()/sd) if sd else np.nan,max_dd=float(md),calmar=float(cg/abs(md)) if md<0 else None,exposure=exp/len(e),multiple=float(e[-1]))
def optimize(op,cl,dates,ro,cm,b):
 best=None
 for p in itertools.product(ROC_LB,ROC_IN,ROC_OUT,CMO_LB,CMO_IN,CMO_OUT):
  m=bt(op,cl,dates,ro[p[0]],cm[p[3]],0,b,p[1],p[2],p[4],p[5]);
  if not m or m['trades']<10 or m['calmar'] is None:continue
  score=(m['calmar'],m['sharpe'],m['cagr']); best=(score,p,m) if best is None or score>best[0] else best
 return best
def one(s):
 x=hist(s); op=x.open.to_numpy(float); cl=x.close.to_numpy(float); dates=x.date.tolist(); ro,cm=cache(x); first=int(len(x)*.50); test=252; folds=[]; wealth=1.; daily_parts=[]
 a=first
 while a<len(x)-30:
  b=min(a+test,len(x)); z=optimize(op,cl,dates,ro,cm,a); p=z[1]; m=bt(op,cl,dates,ro[p[0]],cm[p[3]],a,b,p[1],p[2],p[4],p[5]); folds.append({'test_start':str(x.date.iloc[a].date()),'test_end':str(x.date.iloc[b-1].date()),'params':p,'IS_calmar':z[2]['calmar'],'OOS':m}); wealth*=m['multiple']; a=b
 yrs=(x.date.iloc[-1]-x.date.iloc[first]).days/365.25; return {'symbol':s,'wf_start':str(x.date.iloc[first].date()),'wf_end':str(x.date.iloc[-1].date()),'folds':folds,'aggregate_multiple':wealth,'aggregate_cagr':wealth**(1/yrs)-1,'total_trades':sum(f['OOS']['trades'] for f in folds)}
out=[one(s) for s in SYMS]; print(json.dumps(out,indent=2)); open('roc_cmo_walkforward.json','w').write(json.dumps(out,indent=2))
