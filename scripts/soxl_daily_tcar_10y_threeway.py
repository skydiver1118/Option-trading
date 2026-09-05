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
 tr=pd.concat([(x.high-x.low).abs(),(x.high-x.close.shift(1)).abs(),(x.low-x.close.shift(1)).abs()],axis=1).max(axis=1); up=x.high.diff(); dn=-x.low.diff(); pdm=np.where((up>dn)&(up>0),up,0.); mdm=np.where((dn>up)&(dn>0),dn,0.); atr=tr.rolling(20).mean(); pdi=100*pd.Series(pdm,index=x.index).rolling(20).mean()/atr; mdi=100*pd.Series(mdm,index=x.index).rolling(20).mean()/atr; x['adx20']=(100*(pdi-mdi).abs()/(pdi+mdi)).rolling(20).mean(); x['prev_high']=x.high.shift(1); x['qqq_ema200']=x.qqq_close.ewm(span=200,adjust=False,min_periods=200).mean(); return x

def run(seg,use_qqq):
 cash=100000.; sh=0.; pos=False; pending=None; ent=None; trades=[]; eq=[]; weights=[]
 for dt,r in seg.iterrows():
  if pending:
   side,w=pending; px=float(r.open)
   if side=='buy' and not pos:
    alloc=cash*w; sh=alloc/px; cash-=alloc; pos=True; ent=px; weights.append(w)
   elif side=='sell' and pos:
    cash+=sh*px; trades.append(px/ent-1); sh=0.; pos=False
   pending=None
  if pd.notna(r.wr) and pd.notna(r.cci) and pd.notna(r.adx20):
   if pos and ((r.close>r.prev_high) or (r.wr>-30)): pending=('sell',0)
   elif (not pos) and r.wr<-90 and r.cci<-80 and r.adx20>=15:
    if use_qqq:
     if pd.isna(r.qqq_ema200):
      continue
     w=1.0 if r.qqq_close>r.qqq_ema200 else 0.5
    else: w=1.0
    pending=('buy',w)
  eq.append((dt,cash+(sh*r.close if pos else 0),pos))
 e=pd.DataFrame(eq,columns=['date','equity','pos']).set_index('date'); t=pd.Series(trades,dtype=float); rr=e.equity.pct_change().fillna(0); yrs=(e.index[-1]-e.index[0]).days/365.25; total=e.equity.iloc[-1]/e.equity.iloc[0]-1; cagr=(1+total)**(1/yrs)-1; mdd=(e.equity/e.equity.cummax()-1).min(); sd=rr.std(ddof=0); sharpe=rr.mean()/sd*np.sqrt(252) if sd else np.nan; calmar=cagr/abs(mdd) if mdd<0 else np.nan; wins=t[t>0]; loss=t[t<0]; pf=wins.sum()/abs(loss.sum()) if len(loss) else np.inf
 return dict(trades=len(t),win_rate=(t>0).mean() if len(t) else np.nan,profit_factor=pf,avg_trade=t.mean() if len(t) else np.nan,median_trade=t.median() if len(t) else np.nan,total_return=total,cagr=cagr,sharpe=sharpe,max_drawdown=mdd,calmar=calmar,exposure=e.pos.mean(),avg_position_weight=np.mean(weights) if weights else np.nan,ending_equity=e.equity.iloc[-1])

def hold(seg):
 r=seg.close.pct_change().fillna(0); total=seg.close.iloc[-1]/seg.close.iloc[0]-1; yrs=(seg.index[-1]-seg.index[0]).days/365.25; cagr=(1+total)**(1/yrs)-1; curve=seg.close/seg.close.iloc[0]; mdd=(curve/curve.cummax()-1).min(); sd=r.std(ddof=0); sharpe=r.mean()/sd*np.sqrt(252) if sd else np.nan; return dict(trades=np.nan,win_rate=np.nan,profit_factor=np.nan,avg_trade=np.nan,median_trade=np.nan,total_return=total,cagr=cagr,sharpe=sharpe,max_drawdown=mdd,calmar=cagr/abs(mdd),exposure=1.0,avg_position_weight=1.0,ending_equity=100000*(1+total))

def main():
 x=prep(); end=x.index.max().normalize(); start=end-pd.DateOffset(years=10); seg=x.loc[start:end]
 rows=[]
 for name,useq in [('TCAR_no_QQQ',False),('TCAR_QQQ_100_50',True)]:
  z=run(seg,useq); z.update(portfolio=name,start=seg.index[0].date(),end=seg.index[-1].date()); rows.append(z)
 h=hold(seg); h.update(portfolio='SOXL_buy_hold',start=seg.index[0].date(),end=seg.index[-1].date()); rows.append(h)
 pd.DataFrame(rows).to_csv(OUT/'soxl_daily_tcar_10y_threeway.csv',index=False); print(pd.DataFrame(rows).to_string(index=False))
if __name__=='__main__': main()
