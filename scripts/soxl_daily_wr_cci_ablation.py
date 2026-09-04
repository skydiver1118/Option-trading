#!/usr/bin/env python3
import os, math, requests
from pathlib import Path
import pandas as pd
import numpy as np

TOKEN=os.environ['TRADIER_TOKEN']; BASE='https://api.tradier.com/v1'; OUT=Path('data/williams_r'); OUT.mkdir(parents=True,exist_ok=True)
WR_N=2; WR_ENTRY=-90; WR_EXIT=-30; CCI_N=5; CCI_ENTRY=-80; CCI_EXIT=0

def fetch(sym='SOXL'):
    s=requests.Session(); s.headers.update({'Authorization':f'Bearer {TOKEN}','Accept':'application/json'})
    end=pd.Timestamp.today().date(); start=(pd.Timestamp(end)-pd.DateOffset(years=11)).date(); rows=[]; cur=pd.Timestamp(start); endt=pd.Timestamp(end)
    while cur<=endt:
        stop=min(endt,cur+pd.DateOffset(years=8)-pd.Timedelta(days=1))
        r=s.get(f'{BASE}/markets/history',params={'symbol':sym,'interval':'daily','start':cur.date().isoformat(),'end':stop.date().isoformat()},timeout=30); r.raise_for_status()
        d=(r.json().get('history') or {}).get('day') or []; rows += [d] if isinstance(d,dict) else d; cur=stop+pd.Timedelta(days=1)
    x=pd.DataFrame(rows); x['date']=pd.to_datetime(x.date)
    for c in ['open','high','low','close','volume']: x[c]=pd.to_numeric(x[c],errors='coerce')
    return x.drop_duplicates('date').sort_values('date').set_index('date')

def prep(x):
    x=x.copy(); hh=x.high.rolling(WR_N).max(); ll=x.low.rolling(WR_N).min(); x['wr']=-100*(hh-x.close)/(hh-ll)
    tp=(x.high+x.low+x.close)/3; ma=tp.rolling(CCI_N).mean(); md=tp.rolling(CCI_N).apply(lambda z:np.mean(np.abs(z-np.mean(z))),raw=True); x['cci']=(tp-ma)/(0.015*md); x['prev_high']=x.high.shift(1)
    return x

def bt(seg,mode):
    x=prep(seg); cash=100000.; shares=0.; pos=False; pending=None; ent=None; entdt=None; trs=[]; eq=[]
    for dt,r in x.iterrows():
        if pending:
            side=pending; px=float(r.open)
            if side=='buy' and not pos:
                shares=cash/px; cash=0.; pos=True; ent=px; entdt=dt
            elif side=='sell' and pos:
                cash=shares*px; trs.append((entdt,dt,px/ent-1)); shares=0.; pos=False; ent=entdt=None
            pending=None
        valid=pd.notna(r.wr) and pd.notna(r.cci)
        if valid:
            price_exit=pd.notna(r.prev_high) and r.close>r.prev_high
            if mode=='WR_only':
                entry=r.wr<WR_ENTRY; exit_sig=price_exit or (r.wr>WR_EXIT)
            elif mode=='CCI_only':
                entry=r.cci<CCI_ENTRY; exit_sig=price_exit or (r.cci>CCI_EXIT)
            else:
                entry=(r.wr<WR_ENTRY) and (r.cci<CCI_ENTRY); exit_sig=price_exit or (r.wr>WR_EXIT) or (r.cci>CCI_EXIT)
            if pos and exit_sig: pending='sell'
            elif (not pos) and entry: pending='buy'
        eq.append((dt,cash if not pos else shares*float(r.close),pos))
    e=pd.DataFrame(eq,columns=['date','equity','pos']).set_index('date'); t=pd.DataFrame(trs,columns=['entry','exit','return']); rr=e.equity.pct_change().fillna(0)
    yrs=max((e.index[-1]-e.index[0]).days/365.25,1/365.25); total=float(e.equity.iloc[-1]/e.equity.iloc[0]-1); cagr=float((1+total)**(1/yrs)-1); mdd=float((e.equity/e.equity.cummax()-1).min()); sd=rr.std(ddof=0); sharpe=float(rr.mean()/sd*np.sqrt(252)) if sd>0 else np.nan; calmar=float(cagr/abs(mdd)) if mdd<0 else np.nan
    if len(t):
        wins=t[t['return']>0]['return']; losses=t[t['return']<0]['return']; pf=float(wins.sum()/abs(losses.sum())) if len(losses) else math.inf; win=float((t['return']>0).mean()); avg=float(t['return'].mean()); med=float(t['return'].median()); hold=float((pd.to_datetime(t.exit)-pd.to_datetime(t.entry)).dt.days.mean())
    else: pf=win=avg=med=hold=np.nan
    return {'mode':mode,'trades':len(t),'win_rate':win,'profit_factor':pf,'avg_trade':avg,'median_trade':med,'avg_hold_days':hold,'total_return':total,'cagr':cagr,'sharpe':sharpe,'max_drawdown':mdd,'calmar':calmar,'exposure':float(e.pos.mean()),'ending_equity':float(e.equity.iloc[-1])}

def main():
    x=fetch(); end=x.index.max().normalize(); start=end-pd.DateOffset(years=10); is_end=start+pd.DateOffset(years=6)-pd.Timedelta(days=1); va_start=is_end+pd.Timedelta(days=1); va_end=start+pd.DateOffset(years=8)-pd.Timedelta(days=1); oo_start=va_end+pd.Timedelta(days=1)
    segs={'IS':x.loc[start:is_end],'Validation':x.loc[va_start:va_end],'OOS':x.loc[oo_start:end]}; rows=[]
    for period,seg in segs.items():
        for mode in ['WR_only','CCI_only','WR_plus_CCI']:
            z=bt(seg,mode); z.update({'period':period,'start':seg.index.min().date().isoformat(),'end':seg.index.max().date().isoformat(),'execution':'next_open'}); rows.append(z)
    df=pd.DataFrame(rows); df.to_csv(OUT/'soxl_daily_wr_cci_ablation.csv',index=False); print(df.to_string(index=False))
if __name__=='__main__': main()
# trigger 2026-09-04
