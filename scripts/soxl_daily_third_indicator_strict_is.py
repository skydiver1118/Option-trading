#!/usr/bin/env python3
import os, math, requests
from pathlib import Path
import pandas as pd, numpy as np
TOKEN=os.environ['TRADIER_TOKEN']; BASE='https://api.tradier.com/v1'; OUT=Path('data/williams_r'); OUT.mkdir(parents=True,exist_ok=True)
WE=-90; WX=-30; CE=-80; MIN_TRADES=30

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
 x['ret1']=x.close.pct_change(); x['gap']=x.open/x.close.shift(1)-1; x['clv']=(2*x.close-x.high-x.low)/(x.high-x.low).replace(0,np.nan)
 for n in [10,20,50]:
  x[f'rvol{n}']=x.volume/x.volume.shift(1).rolling(n).mean(); mu=x.volume.shift(1).rolling(n).mean(); sd=x.volume.shift(1).rolling(n).std(); x[f'volz{n}']=(x.volume-mu)/sd
 tr=pd.concat([(x.high-x.low).abs(),(x.high-x.close.shift(1)).abs(),(x.low-x.close.shift(1)).abs()],axis=1).max(axis=1)
 for n in [5,10,14,20,30]: x[f'atrpct{n}']=tr.rolling(n).mean()/x.close
 for n in [10,20,30]: x[f'bbwidth{n}']=(4*x.close.rolling(n).std())/x.close.rolling(n).mean()
 up=x.high.diff(); dn=-x.low.diff(); plus_dm=np.where((up>dn)&(up>0),up,0.0); minus_dm=np.where((dn>up)&(dn>0),dn,0.0)
 for n in [7,14,20]:
  atr=tr.rolling(n).mean(); plus_di=100*pd.Series(plus_dm,index=x.index).rolling(n).mean()/atr; minus_di=100*pd.Series(minus_dm,index=x.index).rolling(n).mean()/atr; dx=100*(plus_di-minus_di).abs()/(plus_di+minus_di); x[f'adx{n}']=dx.rolling(n).mean()
 for n in [20,50,100,200]: x[f'q_ema{n}']=x.q_close.ewm(span=n,adjust=False,min_periods=n).mean()
 x['q_dist20']=x.q_close/x.q_ema20-1; x['q_dist50']=x.q_close/x.q_ema50-1; x['q_dist100']=x.q_close/x.q_ema100-1; x['q_dist200']=x.q_close/x.q_ema200-1
 for n in [1,2,3,5,10]: x[f'q_ret{n}']=x.q_close.pct_change(n)
 qtr=pd.concat([(x.q_high-x.q_low).abs(),(x.q_high-x.q_close.shift(1)).abs(),(x.q_low-x.q_close.shift(1)).abs()],axis=1).max(axis=1)
 for n in [5,10,14,20]: x[f'q_atrpct{n}']=qtr.rolling(n).mean()/x.q_close
 return x

def bt(seg,cond):
 cash=100000.; sh=0.; pos=False; pending=None; ent=None; entdt=None; trs=[]; eq=[]
 for dt,r in seg.iterrows():
  if pending:
   px=float(r.open)
   if pending=='buy' and not pos: sh=cash/px; cash=0.; pos=True; ent=px; entdt=dt
   elif pending=='sell' and pos: cash=sh*px; trs.append((entdt,dt,px/ent-1)); sh=0.; pos=False
   pending=None
  if pd.notna(r.wr) and pd.notna(r.cci):
   entry=(r.wr<WE) and (r.cci<CE) and cond(r); exit_sig=(r.close>r.prev_high) or (r.wr>WX)
   if pos and exit_sig: pending='sell'
   elif (not pos) and entry: pending='buy'
  eq.append((dt,cash if not pos else sh*float(r.close),pos))
 e=pd.DataFrame(eq,columns=['date','equity','pos']).set_index('date'); t=pd.DataFrame(trs,columns=['entry','exit','return']); rr=e.equity.pct_change().fillna(0); yrs=max((e.index[-1]-e.index[0]).days/365.25,1/365.25); total=e.equity.iloc[-1]/e.equity.iloc[0]-1; cagr=(1+total)**(1/yrs)-1; mdd=(e.equity/e.equity.cummax()-1).min(); sd=rr.std(ddof=0); sharpe=rr.mean()/sd*np.sqrt(252) if sd>0 else np.nan; calmar=cagr/abs(mdd) if mdd<0 else np.nan
 if len(t):
  w=t[t['return']>0]['return']; l=t[t['return']<0]['return']; pf=w.sum()/abs(l.sum()) if len(l) else math.inf; win=(t['return']>0).mean(); avg=t['return'].mean(); med=t['return'].median()
 else: pf=win=avg=med=np.nan
 return dict(trades=len(t),win_rate=win,profit_factor=pf,avg_trade=avg,median_trade=med,total_return=total,cagr=cagr,sharpe=sharpe,max_drawdown=mdd,calmar=calmar,exposure=e.pos.mean(),ending_equity=e.equity.iloc[-1])

def candidates():
 out=[]
 def add(fam,param,fn): out.append((fam,str(param),fn))
 for n in [10,20,50]:
  for th in [0.5,0.75,1,1.25,1.5,2]: add(f'RVOL{n}_min',th,lambda r,n=n,th=th: pd.notna(r[f'rvol{n}']) and r[f'rvol{n}']>=th)
  for th in [0.75,1,1.25,1.5,2]: add(f'RVOL{n}_max',th,lambda r,n=n,th=th: pd.notna(r[f'rvol{n}']) and r[f'rvol{n}']<=th)
  for th in [-0.5,0,0.5,1,1.5,2]: add(f'VOLZ{n}_min',th,lambda r,n=n,th=th: pd.notna(r[f'volz{n}']) and r[f'volz{n}']>=th)
 for n in [5,10,14,20,30]:
  for th in [0.03,0.04,0.05,0.06,0.07,0.08,0.10,0.12]: add(f'ATRpct{n}_min',th,lambda r,n=n,th=th: pd.notna(r[f'atrpct{n}']) and r[f'atrpct{n}']>=th)
  for th in [0.04,0.05,0.06,0.07,0.08,0.10,0.12,0.15]: add(f'ATRpct{n}_max',th,lambda r,n=n,th=th: pd.notna(r[f'atrpct{n}']) and r[f'atrpct{n}']<=th)
 for n in [10,20,30]:
  for th in [0.08,0.10,0.15,0.20,0.25,0.30,0.40,0.50]: add(f'BBwidth{n}_min',th,lambda r,n=n,th=th: pd.notna(r[f'bbwidth{n}']) and r[f'bbwidth{n}']>=th)
 for n in [7,14,20]:
  for th in [10,15,20,25,30,35,40,50]: add(f'ADX{n}_min',th,lambda r,n=n,th=th: pd.notna(r[f'adx{n}']) and r[f'adx{n}']>=th)
  for th in [15,20,25,30,35,40]: add(f'ADX{n}_max',th,lambda r,n=n,th=th: pd.notna(r[f'adx{n}']) and r[f'adx{n}']<=th)
 for th in [0,-.005,-.01,-.02,-.03,-.05,-.08,-.10]: add('Gap_down',th,lambda r,th=th: pd.notna(r.gap) and r.gap<=th)
 for th in [-.9,-.8,-.6,-.4,-.2,0,.2]: add('CLV_low',th,lambda r,th=th: pd.notna(r.clv) and r.clv<=th)
 for th in [-.01,-.02,-.03,-.04,-.05,-.07,-.10,-.12]: add('Ret1_down',th,lambda r,th=th: pd.notna(r.ret1) and r.ret1<=th)
 for n in [20,50,100,200]:
  add(f'QQQ_above_EMA{n}',0,lambda r,n=n: pd.notna(r[f'q_ema{n}']) and r.q_close>r[f'q_ema{n}']); add(f'QQQ_below_EMA{n}',0,lambda r,n=n: pd.notna(r[f'q_ema{n}']) and r.q_close<r[f'q_ema{n}'])
 for n in [20,50,100,200]:
  for th in [-.20,-.15,-.10,-.05,0,.03,.05,.10]: add(f'QQQ_dist{n}_min',th,lambda r,n=n,th=th: pd.notna(r[f'q_dist{n}']) and r[f'q_dist{n}']>=th)
  for th in [-.10,-.05,0,.03,.05,.10,.15]: add(f'QQQ_dist{n}_max',th,lambda r,n=n,th=th: pd.notna(r[f'q_dist{n}']) and r[f'q_dist{n}']<=th)
 for n in [1,2,3,5,10]:
  for th in [-.005,-.01,-.02,-.03,-.05,-.08,-.10,-.15]: add(f'QQQ_ret{n}_down',th,lambda r,n=n,th=th: pd.notna(r[f'q_ret{n}']) and r[f'q_ret{n}']<=th)
  for th in [-.05,-.03,-.02,-.01,0,.01,.02,.03,.05]: add(f'QQQ_ret{n}_min',th,lambda r,n=n,th=th: pd.notna(r[f'q_ret{n}']) and r[f'q_ret{n}']>=th)
 for n in [5,10,14,20]:
  for th in [.005,.01,.015,.02,.025,.03,.04,.05]: add(f'QQQ_ATRpct{n}_min',th,lambda r,n=n,th=th: pd.notna(r[f'q_atrpct{n}']) and r[f'q_atrpct{n}']>=th)
 return out

def main():
 x=prep(); end=x.index.max().normalize(); start=end-pd.DateOffset(years=10); is_end=start+pd.DateOffset(years=6)-pd.Timedelta(days=1); va_start=is_end+pd.Timedelta(days=1); va_end=start+pd.DateOffset(years=8)-pd.Timedelta(days=1); oo_start=va_end+pd.Timedelta(days=1); segs={'IS':x.loc[start:is_end],'Validation':x.loc[va_start:va_end],'OOS':x.loc[oo_start:end]}
 base=bt(segs['IS'],lambda r:True); rows=[]
 for fam,param,fn in candidates():
  z=bt(segs['IS'],fn); z.update(family=fam,param=param); rows.append(z)
 isdf=pd.DataFrame(rows); eligible=isdf[isdf.trades>=MIN_TRADES].copy(); isdf.to_csv(OUT/'soxl_third_indicator_strict_is_all_candidates.csv',index=False)
 winners=[]
 for criterion in ['cagr','sharpe']:
  w=eligible.sort_values([criterion,'profit_factor','calmar'],ascending=[False,False,False]).iloc[0]
  fam,param=w.family,w.param
  fn=next(fn for f,p,fn in candidates() if f==fam and p==param)
  rec={'criterion':criterion,'family':fam,'param':param}
  for period,seg in segs.items():
   z=bt(seg,fn)
   for k,v in z.items(): rec[f'{period}_{k}']=v
  winners.append(rec)
 b={'criterion':'baseline','family':'baseline','param':''}
 for period,seg in segs.items():
  z=bt(seg,lambda r:True)
  for k,v in z.items(): b[f'{period}_{k}']=v
 winners.append(b)
 pd.DataFrame(winners).to_csv(OUT/'soxl_third_indicator_strict_is_winners.csv',index=False); print(pd.DataFrame(winners).to_string(index=False))
if __name__=='__main__': main()
