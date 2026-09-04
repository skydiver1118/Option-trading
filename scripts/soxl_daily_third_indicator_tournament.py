#!/usr/bin/env python3
import os, math, requests
from pathlib import Path
import pandas as pd, numpy as np
TOKEN=os.environ['TRADIER_TOKEN']; BASE='https://api.tradier.com/v1'; OUT=Path('data/williams_r'); OUT.mkdir(parents=True,exist_ok=True)
WE=-90; WX=-30; CE=-80

def fetch(sym):
 s=requests.Session(); s.headers.update({'Authorization':f'Bearer {TOKEN}','Accept':'application/json'}); end=pd.Timestamp.today().date(); start=(pd.Timestamp(end)-pd.DateOffset(years=11)).date(); rows=[]; cur=pd.Timestamp(start); endt=pd.Timestamp(end)
 while cur<=endt:
  stop=min(endt,cur+pd.DateOffset(years=8)-pd.Timedelta(days=1)); r=s.get(f'{BASE}/markets/history',params={'symbol':sym,'interval':'daily','start':cur.date().isoformat(),'end':stop.date().isoformat()},timeout=30); r.raise_for_status(); d=(r.json().get('history') or {}).get('day') or []; rows += [d] if isinstance(d,dict) else d; cur=stop+pd.Timedelta(days=1)
 x=pd.DataFrame(rows); x['date']=pd.to_datetime(x.date)
 for c in ['open','high','low','close','volume']: x[c]=pd.to_numeric(x[c],errors='coerce')
 return x.drop_duplicates('date').sort_values('date').set_index('date')

def prep():
 s=fetch('SOXL'); q=fetch('QQQ'); x=s.join(q[['open','high','low','close','volume']].add_prefix('q_'),how='inner')
 hh=x.high.rolling(2).max(); ll=x.low.rolling(2).min(); x['wr']=-100*(hh-x.close)/(hh-ll)
 tp=(x.high+x.low+x.close)/3; ma=tp.rolling(5).mean(); md=tp.rolling(5).apply(lambda z:np.mean(np.abs(z-np.mean(z))),raw=True); x['cci']=(tp-ma)/(0.015*md); x['prev_high']=x.high.shift(1)
 # SOXL indicators
 x['ret1']=x.close.pct_change(); x['gap']=x.open/x.close.shift(1)-1; x['clv']=(2*x.close-x.high-x.low)/(x.high-x.low).replace(0,np.nan)
 x['rvol20']=x.volume/x.volume.shift(1).rolling(20).mean(); x['volz20']=(x.volume-x.volume.shift(1).rolling(20).mean())/x.volume.shift(1).rolling(20).std()
 tr=pd.concat([(x.high-x.low).abs(),(x.high-x.close.shift(1)).abs(),(x.low-x.close.shift(1)).abs()],axis=1).max(axis=1); x['atr14']=tr.rolling(14).mean(); x['atrpct']=x.atr14/x.close
 x['bbwidth20']=(4*x.close.rolling(20).std())/x.close.rolling(20).mean()
 up=x.high.diff(); dn=-x.low.diff(); plus_dm=np.where((up>dn)&(up>0),up,0.0); minus_dm=np.where((dn>up)&(dn>0),dn,0.0); atr14=tr.rolling(14).mean(); plus_di=100*pd.Series(plus_dm,index=x.index).rolling(14).mean()/atr14; minus_di=100*pd.Series(minus_dm,index=x.index).rolling(14).mean()/atr14; dx=100*(plus_di-minus_di).abs()/(plus_di+minus_di); x['adx14']=dx.rolling(14).mean()
 # QQQ regime/momentum
 for n in [20,50,100,200]: x[f'q_ema{n}']=x.q_close.ewm(span=n,adjust=False,min_periods=n).mean()
 x['q_dist50']=x.q_close/x.q_ema50-1; x['q_dist200']=x.q_close/x.q_ema200-1; x['q_ret1']=x.q_close.pct_change(); x['q_ret5']=x.q_close.pct_change(5); x['q_atr14']=pd.concat([(x.q_high-x.q_low).abs(),(x.q_high-x.q_close.shift(1)).abs(),(x.q_low-x.q_close.shift(1)).abs()],axis=1).max(axis=1).rolling(14).mean(); x['q_atrpct']=x.q_atr14/x.q_close
 return x

def bt(seg,cond_fn):
 cash=100000.; sh=0.; pos=False; pending=None; ent=None; entdt=None; trs=[]; eq=[]
 for dt,r in seg.iterrows():
  if pending:
   px=float(r.open)
   if pending=='buy' and not pos: sh=cash/px; cash=0.; pos=True; ent=px; entdt=dt
   elif pending=='sell' and pos: cash=sh*px; trs.append((entdt,dt,px/ent-1)); sh=0.; pos=False
   pending=None
  if pd.notna(r.wr) and pd.notna(r.cci):
   entry=(r.wr<WE) and (r.cci<CE) and cond_fn(r)
   exit_sig=(r.close>r.prev_high) or (r.wr>WX)
   if pos and exit_sig: pending='sell'
   elif (not pos) and entry: pending='buy'
  eq.append((dt,cash if not pos else sh*float(r.close),pos))
 e=pd.DataFrame(eq,columns=['date','equity','pos']).set_index('date'); t=pd.DataFrame(trs,columns=['entry','exit','return']); rr=e.equity.pct_change().fillna(0); yrs=max((e.index[-1]-e.index[0]).days/365.25,1/365.25); total=e.equity.iloc[-1]/e.equity.iloc[0]-1; cagr=(1+total)**(1/yrs)-1; mdd=(e.equity/e.equity.cummax()-1).min(); sd=rr.std(ddof=0); sharpe=rr.mean()/sd*np.sqrt(252) if sd>0 else np.nan; calmar=cagr/abs(mdd) if mdd<0 else np.nan
 if len(t):
  w=t[t['return']>0]['return']; l=t[t['return']<0]['return']; pf=w.sum()/abs(l.sum()) if len(l) else math.inf; win=(t['return']>0).mean(); avg=t['return'].mean()
 else: pf=win=avg=np.nan
 return dict(trades=len(t),win_rate=win,profit_factor=pf,avg_trade=avg,total_return=total,cagr=cagr,sharpe=sharpe,max_drawdown=mdd,calmar=calmar,exposure=e.pos.mean())

def families():
 fs=[]
 def add(name,params,builder): fs.append((name,params,builder))
 add('baseline',[None],lambda p:(lambda r: True))
 add('RVOL20_min',[0.5,0.75,1.0,1.25,1.5,2.0],lambda p:(lambda r: pd.notna(r.rvol20) and r.rvol20>=p))
 add('RVOL20_max',[0.75,1.0,1.25,1.5,2.0],lambda p:(lambda r: pd.notna(r.rvol20) and r.rvol20<=p))
 add('VOLZ20_min',[-0.5,0,0.5,1,1.5],lambda p:(lambda r: pd.notna(r.volz20) and r.volz20>=p))
 add('ATRpct_min',[0.04,0.05,0.06,0.07,0.08,0.10],lambda p:(lambda r: pd.notna(r.atrpct) and r.atrpct>=p))
 add('ATRpct_max',[0.05,0.06,0.07,0.08,0.10,0.12],lambda p:(lambda r: pd.notna(r.atrpct) and r.atrpct<=p))
 add('BBwidth20_min',[0.10,0.15,0.20,0.25,0.30,0.40],lambda p:(lambda r: pd.notna(r.bbwidth20) and r.bbwidth20>=p))
 add('ADX14_min',[15,20,25,30,35,40],lambda p:(lambda r: pd.notna(r.adx14) and r.adx14>=p))
 add('ADX14_max',[15,20,25,30,35],lambda p:(lambda r: pd.notna(r.adx14) and r.adx14<=p))
 add('Gap_down',[0,-0.01,-0.02,-0.03,-0.05,-0.08],lambda p:(lambda r: pd.notna(r.gap) and r.gap<=p))
 add('CloseLocation_low',[-0.8,-0.6,-0.4,-0.2,0],lambda p:(lambda r: pd.notna(r.clv) and r.clv<=p))
 add('Ret1_down',[-0.01,-0.02,-0.03,-0.05,-0.07,-0.10],lambda p:(lambda r: pd.notna(r.ret1) and r.ret1<=p))
 add('QQQ_above_EMA50',[0],lambda p:(lambda r: pd.notna(r.q_ema50) and r.q_close>r.q_ema50))
 add('QQQ_above_EMA200',[0],lambda p:(lambda r: pd.notna(r.q_ema200) and r.q_close>r.q_ema200))
 add('QQQ_below_EMA50',[0],lambda p:(lambda r: pd.notna(r.q_ema50) and r.q_close<r.q_ema50))
 add('QQQ_below_EMA200',[0],lambda p:(lambda r: pd.notna(r.q_ema200) and r.q_close<r.q_ema200))
 add('QQQ_dist50_min',[-0.10,-0.05,0,0.03,0.05],lambda p:(lambda r: pd.notna(r.q_dist50) and r.q_dist50>=p))
 add('QQQ_dist200_min',[-0.15,-0.10,-0.05,0,0.05,0.10],lambda p:(lambda r: pd.notna(r.q_dist200) and r.q_dist200>=p))
 add('QQQ_ret1_down',[-0.005,-0.01,-0.02,-0.03,-0.05],lambda p:(lambda r: pd.notna(r.q_ret1) and r.q_ret1<=p))
 add('QQQ_ret5_down',[-0.01,-0.02,-0.03,-0.05,-0.08,-0.10],lambda p:(lambda r: pd.notna(r.q_ret5) and r.q_ret5<=p))
 add('QQQ_ATRpct_min',[0.01,0.015,0.02,0.025,0.03,0.04],lambda p:(lambda r: pd.notna(r.q_atrpct) and r.q_atrpct>=p))
 return fs

def main():
 x=prep(); end=x.index.max().normalize(); start=end-pd.DateOffset(years=10); is_end=start+pd.DateOffset(years=6)-pd.Timedelta(days=1); va_start=is_end+pd.Timedelta(days=1); va_end=start+pd.DateOffset(years=8)-pd.Timedelta(days=1); oo_start=va_end+pd.Timedelta(days=1); segs={'IS':x.loc[start:is_end],'Validation':x.loc[va_start:va_end],'OOS':x.loc[oo_start:end]}
 winners=[]; allrows=[]
 for fam,params,builder in families():
  cand=[]
  for p in params:
   z=bt(segs['IS'],builder(p)); z.update(family=fam,param=p,period='IS'); allrows.append(z); cand.append(z)
  eligible=[z for z in cand if z['trades']>=30] or cand
  best=sorted(eligible,key=lambda z:(z['calmar'] if pd.notna(z['calmar']) else -999,z['sharpe'] if pd.notna(z['sharpe']) else -999,z['profit_factor'] if pd.notna(z['profit_factor']) else -999,z['cagr']),reverse=True)[0]
  p=best['param']; rec={'family':fam,'param':p}
  for period in ['IS','Validation','OOS']:
   z=bt(segs[period],builder(p));
   for k,v in z.items(): rec[f'{period}_{k}']=v
  winners.append(rec)
 pd.DataFrame(allrows).to_csv(OUT/'soxl_third_indicator_tournament_all_is.csv',index=False)
 w=pd.DataFrame(winners); w['robust_score']=w['Validation_calmar'].fillna(-9)+w['OOS_calmar'].fillna(-9)+0.5*w['Validation_sharpe'].fillna(-9)+0.5*w['OOS_sharpe'].fillna(-9); w=w.sort_values('robust_score',ascending=False); w.to_csv(OUT/'soxl_third_indicator_tournament_winners.csv',index=False); print(w.to_string(index=False))
if __name__=='__main__': main()
