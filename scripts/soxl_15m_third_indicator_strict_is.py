#!/usr/bin/env python3
import os, math, requests
from pathlib import Path
import pandas as pd, numpy as np
TOKEN=os.environ['TRADIER_TOKEN']; BASE='https://api.tradier.com/v1'; OUT=Path('data/williams_r'); OUT.mkdir(parents=True,exist_ok=True)
# Frozen 15m baseline from prior research
WR_N=5; WE=-80; WX=-30; CCI_N=5; CE=-80; CX=0; MIN_TRADES=12

def fetch15(sym):
 end=pd.Timestamp.now(tz='America/New_York').date(); start=(pd.Timestamp(end)-pd.Timedelta(days=39)).date(); s=requests.Session(); s.headers.update({'Authorization':f'Bearer {TOKEN}','Accept':'application/json'}); r=s.get(f'{BASE}/markets/timesales',params={'symbol':sym,'interval':'15min','start':f'{start} 09:30','end':f'{end} 16:00','session_filter':'open'},timeout=30); r.raise_for_status(); d=(r.json().get('series') or {}).get('data') or []; d=[d] if isinstance(d,dict) else d; x=pd.DataFrame(d); tc='time' if 'time' in x else 'timestamp'; dt=pd.to_datetime(x[tc],errors='coerce'); dt=dt.dt.tz_localize('America/New_York',nonexistent='shift_forward',ambiguous='NaT') if dt.dt.tz is None else dt.dt.tz_convert('America/New_York'); x['dt']=dt
 for c in ['open','high','low','close','volume','vwap']:
  if c in x: x[c]=pd.to_numeric(x[c],errors='coerce')
 x=x.dropna(subset=['dt','open','high','low','close']).sort_values('dt').drop_duplicates('dt').set_index('dt'); t=x.index.time; return x[(t>=pd.Timestamp('09:30').time())&(t<=pd.Timestamp('16:00').time())]

def prep():
 x=fetch15('SOXL'); q=fetch15('QQQ')[['close','volume']].rename(columns={'close':'qclose','volume':'qvol'}); x=x.join(q,how='inner'); hh=x.high.rolling(WR_N).max(); ll=x.low.rolling(WR_N).min(); x['wr']=-100*(hh-x.close)/(hh-ll); tp=(x.high+x.low+x.close)/3; ma=tp.rolling(CCI_N).mean(); md=tp.rolling(CCI_N).apply(lambda z:np.mean(np.abs(z-np.mean(z))),raw=True); x['cci']=(tp-ma)/(0.015*md); x['prev_high']=x.high.shift(1); x['ret1']=x.close.pct_change(); x['gap']=x.open/x.close.shift(1)-1; x['clv']=(2*x.close-x.high-x.low)/(x.high-x.low).replace(0,np.nan)
 for n in [5,10,20,30,50]: x[f'rvol{n}']=x.volume/x.volume.shift(1).rolling(n).mean()
 tr=pd.concat([(x.high-x.low).abs(),(x.high-x.close.shift(1)).abs(),(x.low-x.close.shift(1)).abs()],axis=1).max(axis=1)
 for n in [5,7,10,14,20,30]: x[f'atrp{n}']=tr.rolling(n).mean()/x.close
 up=x.high.diff(); dn=-x.low.diff(); pdm=np.where((up>dn)&(up>0),up,0.); mdm=np.where((dn>up)&(dn>0),dn,0.)
 for n in [5,7,10,14,20]:
  atr=tr.rolling(n).mean(); pdi=100*pd.Series(pdm,index=x.index).rolling(n).mean()/atr; mdi=100*pd.Series(mdm,index=x.index).rolling(n).mean()/atr; x[f'adx{n}']=(100*(pdi-mdi).abs()/(pdi+mdi)).rolling(n).mean()
 for n in [5,10,20,30,50]: x[f'ema{n}']=x.close.ewm(span=n,adjust=False,min_periods=n).mean(); x[f'dist{n}']=x.close/x[f'ema{n}']-1; x[f'qema{n}']=x.qclose.ewm(span=n,adjust=False,min_periods=n).mean(); x[f'qdist{n}']=x.qclose/x[f'qema{n}']-1
 for n in [1,2,3,4,8,13,26]: x[f'qret{n}']=x.qclose.pct_change(n)
 return x

def bt(x,cond):
 cash=100000.; sh=0.; pos=False; pending=None; ent=None; trades=[]; eq=[]
 for dt,r in x.iterrows():
  if pending=='buy' and not pos: ent=float(r.open); sh=cash/ent; cash=0.; pos=True; pending=None
  elif pending=='sell' and pos: px=float(r.open); cash=sh*px; trades.append(px/ent-1); sh=0.; pos=False; pending=None
  if pd.notna(r.wr) and pd.notna(r.cci):
   if pos and ((r.close>r.prev_high) or (r.wr>WX) or (r.cci>CX)): pending='sell'
   elif (not pos) and r.wr<WE and r.cci<CE and cond(r): pending='buy'
  eq.append((dt,cash if not pos else sh*r.close,pos))
 e=pd.DataFrame(eq,columns=['dt','equity','pos']).set_index('dt'); rr=e.equity.pct_change().fillna(0); trd=pd.Series(trades,dtype=float); total=e.equity.iloc[-1]/e.equity.iloc[0]-1; n=max(len(e)-1,1); ann=(1+total)**((26*252)/n)-1 if total>-1 else -1; mdd=(e.equity/e.equity.cummax()-1).min(); sd=rr.std(ddof=0); sharpe=rr.mean()/sd*np.sqrt(26*252) if sd>0 else np.nan; wins=trd[trd>0]; loss=trd[trd<0]; pf=wins.sum()/abs(loss.sum()) if len(loss) else np.inf
 return dict(trades=len(trd),win_rate=(trd>0).mean() if len(trd) else np.nan,profit_factor=pf,avg_trade=trd.mean() if len(trd) else np.nan,total_return=total,annualized_return=ann,sharpe=sharpe,max_drawdown=mdd,calmar=ann/abs(mdd) if mdd<0 else np.nan,exposure=e.pos.mean(),ending_equity=e.equity.iloc[-1])

def candidates():
 o=[]
 def add(f,p,fn): o.append((f,str(p),fn))
 for n in [5,10,20,30,50]:
  for th in [.5,.75,1,1.25,1.5,2]: add(f'RVOL{n}_min',th,lambda r,n=n,th=th: pd.notna(r[f'rvol{n}']) and r[f'rvol{n}']>=th)
 for n in [5,7,10,14,20,30]:
  for th in [.005,.01,.015,.02,.025,.03,.04,.05]: add(f'ATRpct{n}_min',th,lambda r,n=n,th=th: pd.notna(r[f'atrp{n}']) and r[f'atrp{n}']>=th)
 for n in [5,7,10,14,20]:
  for th in [10,15,20,25,30,35,40,50]: add(f'ADX{n}_min',th,lambda r,n=n,th=th: pd.notna(r[f'adx{n}']) and r[f'adx{n}']>=th)
  for th in [15,20,25,30,35,40]: add(f'ADX{n}_max',th,lambda r,n=n,th=th: pd.notna(r[f'adx{n}']) and r[f'adx{n}']<=th)
 for th in [0,-.0025,-.005,-.01,-.015,-.02,-.03]: add('Gap_down',th,lambda r,th=th: pd.notna(r.gap) and r.gap<=th)
 for th in [-.8,-.6,-.4,-.2,0,.2]: add('CLV_low',th,lambda r,th=th: pd.notna(r.clv) and r.clv<=th)
 for n in [5,10,20,30,50]:
  for th in [-.05,-.03,-.02,-.01,0,.01,.02,.03,.05]: add(f'SOXL_distEMA{n}_min',th,lambda r,n=n,th=th: pd.notna(r[f'dist{n}']) and r[f'dist{n}']>=th); add(f'QQQ_distEMA{n}_min',th,lambda r,n=n,th=th: pd.notna(r[f'qdist{n}']) and r[f'qdist{n}']>=th)
 for n in [1,2,3,4,8,13,26]:
  for th in [-.005,-.01,-.02,-.03,-.05]: add(f'QQQ_ret{n}_down',th,lambda r,n=n,th=th: pd.notna(r[f'qret{n}']) and r[f'qret{n}']<=th)
 return o

def main():
 x=prep(); days=pd.Index(sorted(pd.unique(x.index.date))); cut=max(1,int(len(days)*.70)); isd,oosd=days[:cut],days[cut:]; IS=x[np.isin(x.index.date,isd)]; OOS=x[np.isin(x.index.date,oosd)]; baseIS=bt(IS,lambda r:True); baseOOS=bt(OOS,lambda r:True); rows=[]
 for f,p,fn in candidates(): z=bt(IS,fn); z.update(family=f,param=p); rows.append(z)
 d=pd.DataFrame(rows); elig=d[d.trades>=MIN_TRADES].copy(); d.to_csv(OUT/'soxl_15m_third_indicator_strict_IS_all.csv',index=False); outs=[]
 for crit in ['annualized_return','sharpe']:
  w=elig.sort_values([crit,'profit_factor','calmar'],ascending=False).iloc[0]; fn=next(fn for f,p,fn in candidates() if f==w.family and p==w.param); rec={'selection':crit,'family':w.family,'param':w.param};
  for label,seg in [('IS',IS),('OOS',OOS)]:
   z=bt(seg,fn)
   for k,v in z.items(): rec[f'{label}_{k}']=v
  outs.append(rec)
 b={'selection':'baseline','family':'WR5_CCI5','param':''};
 for label,z in [('IS',baseIS),('OOS',baseOOS)]:
  for k,v in z.items(): b[f'{label}_{k}']=v
 outs.append(b); pd.DataFrame(outs).to_csv(OUT/'soxl_15m_third_indicator_strict_IS_winners.csv',index=False); print('days',len(days),'IS',len(isd),'OOS',len(oosd),'candidates',len(d),'eligible',len(elig)); print(pd.DataFrame(outs).to_string(index=False))
if __name__=='__main__': main()
