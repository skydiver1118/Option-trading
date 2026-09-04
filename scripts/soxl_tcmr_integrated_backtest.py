#!/usr/bin/env python3
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
 s=fetch('SOXL'); q=fetch('QQQ'); x=s.join(q[['close']].rename(columns={'close':'qqq_close'}),how='inner')
 hh=x.high.rolling(2).max(); ll=x.low.rolling(2).min(); x['wr']=-100*(hh-x.close)/(hh-ll)
 tp=(x.high+x.low+x.close)/3; ma=tp.rolling(5).mean(); md=tp.rolling(5).apply(lambda z:np.mean(np.abs(z-np.mean(z))),raw=True); x['cci']=(tp-ma)/(0.015*md)
 tr=pd.concat([(x.high-x.low).abs(),(x.high-x.close.shift(1)).abs(),(x.low-x.close.shift(1)).abs()],axis=1).max(axis=1); up=x.high.diff(); dn=-x.low.diff(); plus_dm=np.where((up>dn)&(up>0),up,0.0); minus_dm=np.where((dn>up)&(dn>0),dn,0.0); atr=tr.rolling(20).mean(); plus_di=100*pd.Series(plus_dm,index=x.index).rolling(20).mean()/atr; minus_di=100*pd.Series(minus_dm,index=x.index).rolling(20).mean()/atr; dx=100*(plus_di-minus_di).abs()/(plus_di+minus_di); x['adx20']=dx.rolling(20).mean()
 x['prev_high']=x.high.shift(1); x['qqq_ema200']=x.qqq_close.ewm(span=200,adjust=False,min_periods=200).mean(); return x

def bt(seg):
 cash=100000.; sh=0.; pos=False; pending=None; ent=None; entdt=None; weight=0.; trs=[]; eq=[]
 for dt,r in seg.iterrows():
  if pending:
   side,w=pending; px=float(r.open)
   if side=='buy' and not pos:
    alloc=cash*w; sh=alloc/px; cash-=alloc; pos=True; ent=px; entdt=dt; weight=w
   elif side=='sell' and pos:
    cash+=sh*px; trs.append((entdt,dt,px/ent-1,weight)); sh=0.; pos=False; weight=0.
   pending=None
  if pd.notna(r.wr) and pd.notna(r.cci) and pd.notna(r.adx20) and pd.notna(r.qqq_ema200):
   if pos and ((r.close>r.prev_high) or (r.wr>-30)): pending=('sell',0)
   elif (not pos) and (r.wr<-90) and (r.cci<-80) and (r.adx20>=15):
    w=1.0 if r.qqq_close>r.qqq_ema200 else 0.5; pending=('buy',w)
  eq.append((dt,cash+(sh*r.close if pos else 0),pos,weight))
 e=pd.DataFrame(eq,columns=['date','equity','pos','weight']).set_index('date'); t=pd.DataFrame(trs,columns=['entry','exit','return','weight']); rr=e.equity.pct_change().fillna(0); yrs=max((e.index[-1]-e.index[0]).days/365.25,1/365.25); total=e.equity.iloc[-1]/e.equity.iloc[0]-1; cagr=(1+total)**(1/yrs)-1; mdd=(e.equity/e.equity.cummax()-1).min(); sd=rr.std(ddof=0); sharpe=rr.mean()/sd*np.sqrt(252) if sd else np.nan; calmar=cagr/abs(mdd) if mdd<0 else np.nan
 if len(t):
  w=t[t['return']>0]['return']; l=t[t['return']<0]['return']; pf=w.sum()/abs(l.sum()) if len(l) else math.inf; win=(t['return']>0).mean(); avg=t['return'].mean(); med=t['return'].median(); avgw=t['weight'].mean()
 else: pf=win=avg=med=avgw=np.nan
 return dict(trades=len(t),win_rate=win,profit_factor=pf,avg_trade=avg,median_trade=med,total_return=total,cagr=cagr,sharpe=sharpe,max_drawdown=mdd,calmar=calmar,exposure=e.pos.mean(),avg_position_weight=avgw,ending_equity=e.equity.iloc[-1])

def main():
 x=prep(); end=x.index.max().normalize(); start=end-pd.DateOffset(years=10); is_end=start+pd.DateOffset(years=6)-pd.Timedelta(days=1); va_start=is_end+pd.Timedelta(days=1); va_end=start+pd.DateOffset(years=8)-pd.Timedelta(days=1); oo_start=va_end+pd.Timedelta(days=1)
 segs={'IS':x.loc[start:is_end],'Validation':x.loc[va_start:va_end],'OOS':x.loc[oo_start:end]}; rows=[]
 for p,s in segs.items():
  z=bt(s); z.update(period=p,start=s.index.min().date(),end=s.index.max().date()); rows.append(z)
 pd.DataFrame(rows).to_csv(OUT/'soxl_tcmr_integrated_performance.csv',index=False); print(pd.DataFrame(rows).to_string(index=False))
if __name__=='__main__': main()
