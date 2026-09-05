#!/usr/bin/env python3
import os, requests
from pathlib import Path
import pandas as pd, numpy as np
TOKEN=os.environ['TRADIER_TOKEN']; BASE='https://api.tradier.com/v1'; OUT=Path('data/williams_r'); OUT.mkdir(parents=True,exist_ok=True)

def fetch15():
    end=pd.Timestamp.now(tz='America/New_York').date(); start=(pd.Timestamp(end)-pd.Timedelta(days=39)).date()
    s=requests.Session(); s.headers.update({'Authorization':f'Bearer {TOKEN}','Accept':'application/json'})
    r=s.get(f'{BASE}/markets/timesales',params={'symbol':'SOXL','interval':'15min','start':f'{start} 09:30','end':f'{end} 16:00','session_filter':'open'},timeout=30); r.raise_for_status()
    d=(r.json().get('series') or {}).get('data') or []; d=[d] if isinstance(d,dict) else d; x=pd.DataFrame(d)
    tc='time' if 'time' in x else 'timestamp'; dt=pd.to_datetime(x[tc],errors='coerce')
    if dt.dt.tz is None: dt=dt.dt.tz_localize('America/New_York',nonexistent='shift_forward',ambiguous='NaT')
    else: dt=dt.dt.tz_convert('America/New_York')
    x['dt']=dt
    for c in ['open','high','low','close','volume']:
        if c in x: x[c]=pd.to_numeric(x[c],errors='coerce')
    x=x.dropna(subset=['dt','open','high','low','close']).sort_values('dt').drop_duplicates('dt').set_index('dt')
    t=x.index.time
    return x[(t>=pd.Timestamp('09:30').time())&(t<=pd.Timestamp('16:00').time())]

def add_ind(x):
    x=x.copy(); hh=x.high.rolling(5).max(); ll=x.low.rolling(5).min(); x['wr']=-100*(hh-x.close)/(hh-ll)
    tp=(x.high+x.low+x.close)/3; ma=tp.rolling(5).mean(); md=tp.rolling(5).apply(lambda z:np.mean(np.abs(z-np.mean(z))),raw=True); x['cci']=(tp-ma)/(0.015*md); x['prev_high']=x.high.shift(1); return x

def bt(seg):
    x=add_ind(seg); cash=100000.; sh=0.; pos=False; pending=None; trades=[]; eq=[]
    for dt,r in x.iterrows():
        if pending=='buy' and not pos: sh=cash/float(r.open); cash=0.; pos=True; pending=None
        elif pending=='sell' and pos: cash=sh*float(r.open); sh=0.; pos=False; pending=None
        if pd.notna(r.wr) and pd.notna(r.cci):
            if pos and ((r.close>r.prev_high) or (r.wr>-30) or (r.cci>0)): pending='sell'
            elif (not pos) and (r.wr<-80) and (r.cci<-80): pending='buy'
        eq.append((dt,cash if not pos else sh*r.close,pos))
    e=pd.DataFrame(eq,columns=['dt','equity','pos']).set_index('dt'); total=e.equity.iloc[-1]/e.equity.iloc[0]-1
    return total,e.pos.mean(),e.equity.iloc[-1]

def hold(seg):
    # true buy-and-hold from first available RTH open to final RTH close
    total=float(seg.close.iloc[-1]/seg.open.iloc[0]-1)
    return total,100000*(1+total)

def main():
    x=fetch15(); days=pd.Index(sorted(pd.unique(x.index.date))); cut=max(1,int(len(days)*.70)); isd,oosd=days[:cut],days[cut:]
    rows=[]
    for label,dset in [('IS',isd),('OOS',oosd)]:
        seg=x[np.isin(x.index.date,dset)]; st,expo,endv=bt(seg); hr,hend=hold(seg)
        rows.append({'period':label,'start':seg.index.min().isoformat(),'end':seg.index.max().isoformat(),'trading_days':len(dset),'strategy_return':st,'strategy_exposure':expo,'strategy_ending_100k':endv,'soxl_hold_return':hr,'soxl_hold_ending_100k':hend})
    pd.DataFrame(rows).to_csv(OUT/'soxl_dcr15_vs_hold.csv',index=False); print(pd.DataFrame(rows).to_string(index=False))
if __name__=='__main__': main()
