#!/usr/bin/env python3
import os, math, itertools, requests
from pathlib import Path
import pandas as pd
import numpy as np

TOKEN=os.environ['TRADIER_TOKEN']
BASE='https://api.tradier.com/v1'
OUT=Path('data/williams_r'); OUT.mkdir(parents=True, exist_ok=True)
SYMBOL='SOXL'

def fetch_30m():
    end=pd.Timestamp.now(tz='America/New_York').date()
    start=(pd.Timestamp(end)-pd.Timedelta(days=39)).date()
    s=requests.Session(); s.headers.update({'Authorization':f'Bearer {TOKEN}','Accept':'application/json'})
    r=s.get(f'{BASE}/markets/timesales', params={'symbol':SYMBOL,'interval':'15min','start':f'{start} 09:30','end':f'{end} 16:00','session_filter':'open'}, timeout=30)
    r.raise_for_status(); series=(r.json().get('series') or {}).get('data') or []
    if isinstance(series,dict): series=[series]
    df=pd.DataFrame(series)
    tc='time' if 'time' in df.columns else 'timestamp'
    dt=pd.to_datetime(df[tc],errors='coerce')
    if dt.dt.tz is None: dt=dt.dt.tz_localize('America/New_York',nonexistent='shift_forward',ambiguous='NaT')
    else: dt=dt.dt.tz_convert('America/New_York')
    df['dt']=dt
    for c in ['open','high','low','close','volume']:
        df[c]=pd.to_numeric(df[c],errors='coerce')
    df=df.dropna(subset=['dt','open','high','low','close']).sort_values('dt').drop_duplicates('dt').set_index('dt')
    t=df.index.time; df=df[(t>=pd.Timestamp('09:30').time()) & (t<=pd.Timestamp('15:45').time())]
    # resample each trading day into regular-session 30m bars anchored at 09:30
    parts=[]
    for d,g in df.groupby(df.index.date):
        g=g.copy()
        anchor=pd.Timestamp(str(d)+' 09:30',tz='America/New_York')
        bucket=((g.index-anchor).total_seconds()//1800).astype(int)
        g['bucket']=bucket
        for _,x in g.groupby('bucket'):
            if x.empty: continue
            parts.append({'dt':x.index[0],'open':x.open.iloc[0],'high':x.high.max(),'low':x.low.min(),'close':x.close.iloc[-1],'volume':x.volume.sum() if 'volume' in x else np.nan})
    return pd.DataFrame(parts).set_index('dt').sort_index()

def add_ind(df,wn,cn):
    x=df.copy(); hh=x.high.rolling(wn).max(); ll=x.low.rolling(wn).min(); den=hh-ll
    x['wr']=np.where(den.ne(0),-100*(hh-x.close)/den,np.nan)
    tp=(x.high+x.low+x.close)/3; sma=tp.rolling(cn).mean(); md=tp.rolling(cn).apply(lambda z:np.mean(np.abs(z-np.mean(z))),raw=True)
    x['cci']=np.where(md.ne(0),(tp-sma)/(0.015*md),np.nan); x['prev_high']=x.high.shift(1); return x

def bt(df,wn,we,wx,cn,ce,cx):
    x=add_ind(df,wn,cn); cash=100000.; sh=0.; pos=False; pending=None; ent=None; entdt=None; trades=[]; eq=[]; reason=None
    for dt,r in x.iterrows():
        if pending=='buy' and not pos:
            p=float(r.open); sh=cash/p; cash=0.; pos=True; ent=p; entdt=dt; pending=None
        elif pending=='sell' and pos:
            p=float(r.open); cash=sh*p; trades.append((entdt,dt,p/ent-1)); sh=0.; pos=False; ent=entdt=None; pending=None
        if pd.notna(r.wr) and pd.notna(r.cci):
            if pos and pending is None:
                if (pd.notna(r.prev_high) and r.close>r.prev_high) or r.wr>wx or r.cci>cx: pending='sell'
            elif (not pos) and pending is None and r.wr<we and r.cci<ce: pending='buy'
        eq.append((dt,cash if not pos else sh*float(r.close),pos))
    t=pd.DataFrame(trades,columns=['entry_dt','exit_dt','return']); e=pd.DataFrame(eq,columns=['dt','equity','pos']).set_index('dt'); rr=e.equity.pct_change().fillna(0)
    total=float(e.equity.iloc[-1]/e.equity.iloc[0]-1); barsyr=13*252; n=max(len(e)-1,1); ann=float((1+total)**(barsyr/n)-1) if total>-1 else -1
    mdd=float((e.equity/e.equity.cummax()-1).min()); sd=rr.std(ddof=0); sharpe=float(rr.mean()/sd*np.sqrt(barsyr)) if sd>0 else np.nan; calmar=float(ann/abs(mdd)) if mdd<0 else np.nan
    if len(t):
        w=t[t['return']>0]['return']; l=t[t['return']<0]['return']; pf=float(w.sum()/abs(l.sum())) if len(l) else math.inf; win=float((t['return']>0).mean()); avg=float(t['return'].mean()); med=float(t['return'].median()); hold=float(((pd.to_datetime(t.exit_dt)-pd.to_datetime(t.entry_dt)).dt.total_seconds()/1800).mean())
    else: pf=win=avg=med=hold=np.nan
    return dict(trades=len(t),win_rate=win,profit_factor=pf,avg_trade=avg,median_trade=med,avg_hold_30m_bars=hold,total_return=total,annualized_return=ann,sharpe=sharpe,max_drawdown=mdd,calmar=calmar,exposure=float(e.pos.mean()),ending_equity=float(e.equity.iloc[-1]))

def main():
    df=fetch_30m(); df.to_csv(OUT/'SOXL_tradier_30m_40d.csv')
    days=pd.Index(sorted(pd.unique(df.index.date))); cut=max(1,int(len(days)*.70)); isd,ood=days[:cut],days[cut:]; isdf=df[np.isin(df.index.date,isd)]; oodf=df[np.isin(df.index.date,ood)]
    wr_ns=[2,3,4,5,7,10]; wr_entries=[-95,-90,-85,-80]; wr_exits=[-50,-40,-30,-20]
    cci_ns=[3,5,7,10]; cci_entries=[-120,-100,-80,-50]; cci_exits=[0,50,100]
    rows=[]
    for p in itertools.product(wr_ns,wr_entries,wr_exits,cci_ns,cci_entries,cci_exits):
        s=bt(isdf,*p)
        if s['trades']<8 or not np.isfinite(s['calmar']): continue
        rows.append({'wr_lookback':p[0],'wr_entry':p[1],'wr_exit':p[2],'cci_lookback':p[3],'cci_entry':p[4],'cci_exit':p[5],**s})
    g=pd.DataFrame(rows).sort_values(['calmar','sharpe','annualized_return','profit_factor'],ascending=False); b=g.iloc[0]; p=(int(b.wr_lookback),float(b.wr_entry),float(b.wr_exit),int(b.cci_lookback),float(b.cci_entry),float(b.cci_exit))
    g.to_csv(OUT/'soxl_30m_40d_IS_grid.csv',index=False); pd.DataFrame([b]).to_csv(OUT/'soxl_30m_40d_selected.csv',index=False)
    outs=[]
    for label,seg in [('IS',isdf),('OOS',oodf)]:
        s=bt(seg,*p); outs.append({'period':label,'start':seg.index.min().isoformat(),'end':seg.index.max().isoformat(),'trading_days':len(pd.unique(seg.index.date)),'bars':len(seg),'wr_lookback':p[0],'wr_entry':p[1],'wr_exit':p[2],'cci_lookback':p[3],'cci_entry':p[4],'cci_exit':p[5],**s})
    pd.DataFrame(outs).to_csv(OUT/'soxl_30m_40d_locked_IS_OOS.csv',index=False)
    print('BEST',p); print(pd.DataFrame(outs).to_string(index=False))
if __name__=='__main__': main()
