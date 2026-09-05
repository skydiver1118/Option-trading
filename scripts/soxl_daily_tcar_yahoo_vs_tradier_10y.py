#!/usr/bin/env python3
# Yahoo vs Tradier replication with one-year indicator warmup
import os, math, requests
from pathlib import Path
import pandas as pd, numpy as np, yfinance as yf
TOKEN=os.environ['TRADIER_TOKEN']; BASE='https://api.tradier.com/v1'; OUT=Path('data/williams_r'); OUT.mkdir(parents=True,exist_ok=True)
START='2016-09-06'; END='2026-09-04'; WARMUP=(pd.Timestamp(START)-pd.DateOffset(years=1)).date().isoformat()

def fetch_tradier():
 s=requests.Session(); s.headers.update({'Authorization':f'Bearer {TOKEN}','Accept':'application/json'}); rows=[]; cur=pd.Timestamp(WARMUP); endt=pd.Timestamp(END)
 while cur<=endt:
  stop=min(endt,cur+pd.DateOffset(years=8)-pd.Timedelta(days=1)); r=s.get(f'{BASE}/markets/history',params={'symbol':'SOXL','interval':'daily','start':cur.date().isoformat(),'end':stop.date().isoformat()},timeout=30); r.raise_for_status(); d=(r.json().get('history') or {}).get('day') or []; rows += [d] if isinstance(d,dict) else d; cur=stop+pd.Timedelta(days=1)
 x=pd.DataFrame(rows); x['date']=pd.to_datetime(x.date)
 for c in ['open','high','low','close','volume']: x[c]=pd.to_numeric(x[c],errors='coerce')
 return x.drop_duplicates('date').sort_values('date').set_index('date')[['open','high','low','close','volume']]

def fetch_yahoo():
 x=yf.download('SOXL',start=WARMUP,end=(pd.Timestamp(END)+pd.Timedelta(days=1)).date().isoformat(),interval='1d',auto_adjust=True,actions=False,progress=False,threads=False)
 if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
 x=x.rename(columns={c:c.lower() for c in x.columns}); x.index=pd.to_datetime(x.index).tz_localize(None); return x[['open','high','low','close','volume']].dropna()

def prep(x):
 x=x.copy(); hh=x.high.rolling(2).max(); ll=x.low.rolling(2).min(); x['wr']=-100*(hh-x.close)/(hh-ll)
 tp=(x.high+x.low+x.close)/3; ma=tp.rolling(5).mean(); md=tp.rolling(5).apply(lambda z:np.mean(np.abs(z-np.mean(z))),raw=True); x['cci']=(tp-ma)/(0.015*md)
 tr=pd.concat([(x.high-x.low).abs(),(x.high-x.close.shift(1)).abs(),(x.low-x.close.shift(1)).abs()],axis=1).max(axis=1); up=x.high.diff(); dn=-x.low.diff(); pdm=np.where((up>dn)&(up>0),up,0.); mdm=np.where((dn>up)&(dn>0),dn,0.); atr=tr.rolling(20).mean(); pdi=100*pd.Series(pdm,index=x.index).rolling(20).mean()/atr; mdi=100*pd.Series(mdm,index=x.index).rolling(20).mean()/atr; x['adx20']=(100*(pdi-mdi).abs()/(pdi+mdi)).rolling(20).mean(); x['prev_high']=x.high.shift(1); return x

def run(x,source):
 x=x.loc[pd.Timestamp(START):pd.Timestamp(END)]
 cash=100000.; sh=0.; pos=False; pending=None; ent=None; entdt=None; sigdt=None; trades=[]; eq=[]
 for dt,r in x.iterrows():
  if pending:
   if pending=='buy' and not pos:
    px=float(r.open); sh=cash/px; cash=0.; pos=True; ent=px; entdt=dt
   elif pending=='sell' and pos:
    px=float(r.open); cash=sh*px; trades.append({'source':source,'entry_signal_date':sigdt,'entry_date':entdt,'entry_price':ent,'exit_date':dt,'exit_price':px,'return':px/ent-1,'holding_days':(dt-entdt).days}); sh=0.; pos=False
   pending=None
  if pd.notna(r.wr) and pd.notna(r.cci) and pd.notna(r.adx20):
   if pos and ((r.close>r.prev_high) or (r.wr>-30)): pending='sell'
   elif (not pos) and r.wr<-90 and r.cci<-80 and r.adx20>=15: pending='buy'; sigdt=dt
  eq.append((dt,cash if not pos else sh*r.close,pos))
 e=pd.DataFrame(eq,columns=['date','equity','pos']).set_index('date'); t=pd.DataFrame(trades); rr=e.equity.pct_change().fillna(0); yrs=(e.index[-1]-e.index[0]).days/365.25; total=e.equity.iloc[-1]/e.equity.iloc[0]-1; cagr=(1+total)**(1/yrs)-1; mdd=(e.equity/e.equity.cummax()-1).min(); sd=rr.std(ddof=0); sharpe=rr.mean()/sd*np.sqrt(252) if sd else np.nan; wins=t[t['return']>0]['return']; loss=t[t['return']<0]['return']; pf=wins.sum()/abs(loss.sum()) if len(loss) else np.inf
 stats={'source':source,'start':e.index[0].date(),'end':e.index[-1].date(),'bars':len(e),'trades':len(t),'win_rate':(t['return']>0).mean(),'profit_factor':pf,'avg_trade':t['return'].mean(),'median_trade':t['return'].median(),'avg_holding_days':t['holding_days'].mean(),'total_return':total,'cagr':cagr,'sharpe':sharpe,'max_drawdown':mdd,'calmar':cagr/abs(mdd),'exposure':e.pos.mean(),'ending_equity':e.equity.iloc[-1]}
 return stats,t,e

def main():
 td=fetch_tradier(); yd=fetch_yahoo(); common=td.index.intersection(yd.index); td=td.loc[common]; yd=yd.loc[common]
 diag=[]
 for c in ['open','high','low','close']:
  rel=(yd.loc[pd.Timestamp(START):,c]/td.loc[pd.Timestamp(START):,c]-1).replace([np.inf,-np.inf],np.nan).dropna(); diag.append({'field':c,'mean_abs_pct_diff':rel.abs().mean(),'median_abs_pct_diff':rel.abs().median(),'max_abs_pct_diff':rel.abs().max(),'corr':yd.loc[pd.Timestamp(START):,c].corr(td.loc[pd.Timestamp(START):,c])})
 ts,tt,te=run(prep(td),'Tradier'); ys,yt,ye=run(prep(yd),'Yahoo_auto_adjusted')
 pd.DataFrame([ts,ys]).to_csv(OUT/'soxl_daily_tcar_yahoo_vs_tradier_10y_performance.csv',index=False)
 pd.concat([tt,yt],ignore_index=True).to_csv(OUT/'soxl_daily_tcar_yahoo_vs_tradier_10y_trades.csv',index=False)
 pd.DataFrame(diag).to_csv(OUT/'soxl_daily_tcar_yahoo_vs_tradier_price_diagnostics.csv',index=False)
 a=set(zip(pd.to_datetime(tt.entry_date).dt.date,pd.to_datetime(tt.exit_date).dt.date)); b=set(zip(pd.to_datetime(yt.entry_date).dt.date,pd.to_datetime(yt.exit_date).dt.date)); summ={'tradier_trades':len(a),'yahoo_trades':len(b),'exact_matched_trades':len(a&b),'tradier_only':len(a-b),'yahoo_only':len(b-a)}; pd.DataFrame([summ]).to_csv(OUT/'soxl_daily_tcar_yahoo_vs_tradier_trade_match.csv',index=False)
 print(pd.DataFrame([ts,ys]).to_string(index=False)); print(pd.DataFrame([summ]).to_string(index=False)); print(pd.DataFrame(diag).to_string(index=False))
if __name__=='__main__': main()
