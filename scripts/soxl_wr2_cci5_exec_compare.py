#!/usr/bin/env python3
import os, math, requests
from pathlib import Path
import pandas as pd
import numpy as np

TOKEN=os.environ['TRADIER_TOKEN']
BASE='https://api.tradier.com/v1'
OUT=Path('data/williams_r'); OUT.mkdir(parents=True,exist_ok=True)

WR_N=2; WR_ENTRY=-90; WR_EXIT=-30
CCI_N=5; CCI_ENTRY=-80; CCI_EXIT=0

def fetch():
    end=pd.Timestamp.today().date(); start=(pd.Timestamp(end)-pd.DateOffset(years=10,days=10)).date()
    s=requests.Session(); s.headers.update({'Authorization':f'Bearer {TOKEN}','Accept':'application/json'})
    rows=[]; cur=pd.Timestamp(start); end_ts=pd.Timestamp(end)
    while cur<=end_ts:
        stop=min(end_ts,cur+pd.DateOffset(years=8)-pd.Timedelta(days=1))
        r=s.get(f'{BASE}/markets/history',params={'symbol':'SOXL','interval':'daily','start':cur.date().isoformat(),'end':stop.date().isoformat()},timeout=30); r.raise_for_status()
        d=(r.json().get('history') or {}).get('day') or []; d=[d] if isinstance(d,dict) else d; rows.extend(d); cur=stop+pd.Timedelta(days=1)
    df=pd.DataFrame(rows); df['date']=pd.to_datetime(df['date'])
    for c in ['open','high','low','close','volume']: df[c]=pd.to_numeric(df[c],errors='coerce')
    return df.drop_duplicates('date').sort_values('date').set_index('date')

def add_ind(df):
    x=df.copy(); hh=x.high.rolling(WR_N).max(); ll=x.low.rolling(WR_N).min(); x['wr']=-100*(hh-x.close)/(hh-ll)
    tp=(x.high+x.low+x.close)/3; sma=tp.rolling(CCI_N).mean(); md=tp.rolling(CCI_N).apply(lambda z: np.mean(np.abs(z-np.mean(z))),raw=True)
    x['cci']=(tp-sma)/(0.015*md); x['prev_high']=x.high.shift(1); return x

def bt(seg,mode):
    x=add_ind(seg)
    cash=100000.; shares=0.; pos=False; pending=None; ent=None; entdt=None; trs=[]; eq=[]
    for dt,r in x.iterrows():
        if pending is not None:
            side,sigdt,reason=pending; px=float(r.open if mode=='next_open' else r.close)
            if side=='buy' and not pos:
                shares=cash/px; cash=0.; pos=True; ent=px; entdt=dt
            elif side=='sell' and pos:
                cash=shares*px; trs.append((entdt,dt,px/ent-1,reason)); shares=0.; pos=False; ent=entdt=None
            pending=None
        if pd.notna(r.wr) and pd.notna(r.cci):
            entry=(r.wr<WR_ENTRY) and (r.cci<CCI_ENTRY)
            ex1=pd.notna(r.prev_high) and r.close>r.prev_high
            ex2=r.wr>WR_EXIT; ex3=r.cci>CCI_EXIT
            if pos and (ex1 or ex2 or ex3):
                reason='+'.join([n for n,v in [('close>prev_high',ex1),('wr>-30',ex2),('cci>0',ex3)] if v]); pending=('sell',dt,reason)
            elif (not pos) and entry:
                pending=('buy',dt,'')
        equity=cash if not pos else shares*float(r.close)
        eq.append((dt,equity,pos))
    t=pd.DataFrame(trs,columns=['entry_date','exit_date','return','exit_reason'])
    e=pd.DataFrame(eq,columns=['date','equity','in_position']).set_index('date')
    rr=e.equity.pct_change().fillna(0); years=max((e.index[-1]-e.index[0]).days/365.25,1/365.25)
    total=float(e.equity.iloc[-1]/e.equity.iloc[0]-1); cagr=float((1+total)**(1/years)-1)
    mdd=float((e.equity/e.equity.cummax()-1).min()); sd=rr.std(ddof=0); sharpe=float(rr.mean()/sd*np.sqrt(252)) if sd>0 else np.nan; calmar=float(cagr/abs(mdd)) if mdd<0 else np.nan
    if len(t):
        wins=t.loc[t['return']>0,'return']; losses=t.loc[t['return']<0,'return']; pf=float(wins.sum()/abs(losses.sum())) if len(losses) else math.inf
        wr=float((t['return']>0).mean()); avg=float(t['return'].mean()); med=float(t['return'].median()); hold=float((pd.to_datetime(t.exit_date)-pd.to_datetime(t.entry_date)).dt.days.mean())
    else: pf=wr=avg=med=hold=np.nan
    return {'mode':mode,'trades':len(t),'win_rate':wr,'profit_factor':pf,'avg_trade':avg,'median_trade':med,'avg_hold_days':hold,'total_return':total,'cagr':cagr,'sharpe':sharpe,'max_drawdown':mdd,'calmar':calmar,'exposure':float(e.in_position.mean()),'ending_equity':float(e.equity.iloc[-1])},t

def main():
    df=fetch(); end=df.index.max().normalize(); start=end-pd.DateOffset(years=10); val_end=start+pd.DateOffset(years=8)-pd.Timedelta(days=1); oos_start=val_end+pd.Timedelta(days=1)
    seg=df.loc[(df.index>=oos_start)&(df.index<=end)]
    rows=[]
    for mode in ['next_open','next_close']:
        s,t=bt(seg,mode); s.update({'period':'OOS','start':seg.index.min().date().isoformat(),'end':seg.index.max().date().isoformat(),'wr_lookback':WR_N,'wr_entry':WR_ENTRY,'wr_exit':WR_EXIT,'cci_lookback':CCI_N,'cci_entry':CCI_ENTRY,'cci_exit':CCI_EXIT}); rows.append(s)
        t.to_csv(OUT/f'soxl_WR2_CCI5_m80_OOS_{mode}_trades.csv',index=False)
    pd.DataFrame(rows).to_csv(OUT/'soxl_WR2_CCI5_m80_OOS_exec_compare.csv',index=False)
    print(pd.DataFrame(rows).to_string(index=False))
if __name__=='__main__': main()
