import os,json,urllib.request
import pandas as pd
import numpy as np
SYMS=['SOXL','VGT','SPMO','VOO']; START='2010-01-01'; END='2026-09-16'; COST=5
TOKEN=os.environ.get('TRADIER_TOKEN') or os.environ.get('TRADIER_ACCESS_TOKEN')
def hist(s):
 u=f'https://api.tradier.com/v1/markets/history?symbol={s}&interval=daily&start={START}&end={END}'
 q=urllib.request.Request(u,headers={'Authorization':f'Bearer {TOKEN}','Accept':'application/json'})
 with urllib.request.urlopen(q) as r:j=json.load(r)
 d=j['history']['day']; d=[d] if isinstance(d,dict) else d; x=pd.DataFrame(d)
 x['date']=pd.to_datetime(x.date,errors='coerce')
 for c in ['open','close']:x[c]=pd.to_numeric(x[c],errors='coerce')
 return x.dropna(subset=['date','open','close']).sort_values('date').drop_duplicates('date').reset_index(drop=True)
def indicators(x):
 x=x.copy(); x['roc']=x.close/x.close.shift(7)-1; d=x.close.diff(); up=d.clip(lower=0); dn=(-d.clip(upper=0)); su=up.rolling(14).sum(); sd=dn.rolling(14).sum(); x['cmo']=100*(su-sd)/(su+sd); return x
def bt(s,x,mode):
 x=indicators(x); cash=1.; shares=0.; pos=False; trades=[]; eq=[]; ent=None; ed=None; exposed=0
 for i in range(15,len(x)):
  sig=x.iloc[i-1]; op=x.open.iloc[i]
  enter=(sig.roc<-.03) if mode=='ROC' else (sig.roc<-.03 and sig.cmo<-50)
  exit=(sig.roc>.03) if mode=='ROC' else (sig.roc>.03 or sig.cmo>50)
  if not pos and enter: shares=cash*(1-COST/10000)/op; cash=0; pos=True; ent=op; ed=x.date.iloc[i]
  elif pos and exit:
   cash=shares*op*(1-COST/10000); gross=op/ent-1; trades.append((ed,x.date.iloc[i],gross,(x.date.iloc[i]-ed).days)); shares=0; pos=False
  val=cash if not pos else shares*x.close.iloc[i]; eq.append(val); exposed+=int(pos)
 if pos: cash=shares*x.close.iloc[-1]*(1-COST/10000); gross=x.close.iloc[-1]/ent-1; trades.append((ed,x.date.iloc[-1],gross,(x.date.iloc[-1]-ed).days)); eq[-1]=cash
 e=pd.Series(eq); dr=e/e.cummax()-1; re=e.pct_change().fillna(0); years=(x.date.iloc[-1]-x.date.iloc[14]).days/365.25; t=pd.DataFrame(trades,columns=['in','out','gross','days']); net=t.gross-(2*COST/10000); gp=net[net>0].sum(); gl=-net[net<0].sum(); cagr=e.iloc[-1]**(1/years)-1; sharpe=np.sqrt(252)*re.mean()/re.std() if re.std()>0 else np.nan; mdd=dr.min()
 return {'symbol':s,'mode':mode,'trades':len(t),'win_rate':float((net>0).mean()),'avg_trade':float(net.mean()),'profit_factor':float(gp/gl) if gl else None,'cagr':float(cagr),'sharpe':float(sharpe),'max_dd':float(mdd),'calmar':float(cagr/abs(mdd)) if mdd<0 else None,'exposure':exposed/len(eq),'avg_hold_days':float(t.days.mean())}
out=[]
for s in SYMS:
 x=hist(s)
 for mode in ['ROC','ROC+CMO']:out.append(bt(s,x,mode))
print(json.dumps(out,indent=2)); pd.DataFrame(out).to_csv('roc_cmo_comparison.csv',index=False)
