#!/usr/bin/env python3
import os, math, requests
from pathlib import Path
import pandas as pd
import numpy as np

TOKEN=os.environ['TRADIER_TOKEN']
BASE='https://api.tradier.com/v1'
OUT=Path('data/williams_r'); OUT.mkdir(parents=True, exist_ok=True)
TICKERS=['SPY','SPMO']

def fetch_history(symbol,start='2015-09-01',end=None):
    if end is None: end=pd.Timestamp.today().date().isoformat()
    s=requests.Session(); s.headers.update({'Authorization':f'Bearer {TOKEN}','Accept':'application/json'})
    rows=[]; cur=pd.Timestamp(start); end_ts=pd.Timestamp(end)
    while cur<=end_ts:
        stop=min(end_ts,cur+pd.DateOffset(years=8)-pd.Timedelta(days=1))
        r=s.get(f'{BASE}/markets/history',params={'symbol':symbol,'interval':'daily','start':cur.date().isoformat(),'end':stop.date().isoformat()},timeout=30)
        r.raise_for_status(); h=r.json().get('history') or {}; d=h.get('day') or []
        if isinstance(d,dict): d=[d]
        rows.extend(d); cur=stop+pd.Timedelta(days=1)
    df=pd.DataFrame(rows); df['date']=pd.to_datetime(df['date'])
    for c in ['open','high','low','close','volume']: df[c]=pd.to_numeric(df[c],errors='coerce')
    return df.drop_duplicates('date').sort_values('date').set_index('date')

def add_wr(df,n=2):
    x=df.copy(); hh=x.high.rolling(n).max(); ll=x.low.rolling(n).min(); rng=hh-ll
    x['wr']=np.where(rng.ne(0),-100*(hh-x.close)/rng,np.nan); x['prev_high']=x.high.shift(1); return x

def backtest_same_close(df,n=2):
    x=add_wr(df,n); cash=100000.; shares=0.; pos=False; trades=[]; eq=[]; ent=None; entdt=None
    for dt,row in x.iterrows():
        wr=row.wr
        if pd.notna(wr):
            entry=wr < -90
            exit1=pd.notna(row.prev_high) and row.close > row.prev_high
            exit2=wr > -30
            if pos and (exit1 or exit2):
                p=float(row.close); cash=shares*p
                reason='+'.join([z for z,v in [('close>prev_high',exit1),('wr>-30',exit2)] if v])
                trades.append((entdt,ent,dt,p,p/ent-1,reason)); shares=0.; pos=False; ent=entdt=None
            elif (not pos) and entry:
                p=float(row.close); shares=cash/p; cash=0.; pos=True; ent=p; entdt=dt
        equity=cash if not pos else shares*float(row.close); eq.append((dt,equity,pos))
    t=pd.DataFrame(trades,columns=['entry_date','entry_price','exit_date','exit_price','return','exit_reason'])
    e=pd.DataFrame(eq,columns=['date','equity','in_position']).set_index('date')
    dr=e.equity.pct_change().fillna(0); years=max((e.index[-1]-e.index[0]).days/365.25,1/365.25)
    total=float(e.equity.iloc[-1]/e.equity.iloc[0]-1); cagr=float((1+total)**(1/years)-1); mdd=float((e.equity/e.equity.cummax()-1).min())
    sd=dr.std(ddof=0); sharpe=float(dr.mean()/sd*np.sqrt(252)) if sd>0 else np.nan; vol=float(sd*np.sqrt(252)); calmar=float(cagr/abs(mdd)) if mdd<0 else np.nan
    if len(t):
        wins=t.loc[t['return']>0,'return']; losses=t.loc[t['return']<0,'return']; pf=float(wins.sum()/abs(losses.sum())) if len(losses) else math.inf
        wr=float((t['return']>0).mean()); avg=float(t['return'].mean()); med=float(t['return'].median()); hold=float((pd.to_datetime(t.exit_date)-pd.to_datetime(t.entry_date)).dt.days.mean())
    else: pf=wr=avg=med=hold=np.nan
    return {'trades':len(t),'win_rate':wr,'profit_factor':pf,'avg_trade':avg,'median_trade':med,'avg_hold_days':hold,'total_return':total,'cagr':cagr,'annualized_volatility':vol,'sharpe':sharpe,'max_drawdown':mdd,'calmar':calmar,'exposure':float(e.in_position.mean()),'ending_equity':float(e.equity.iloc[-1])}

def buyhold(df):
    c=df.close.astype(float); eq=100000*c/c.iloc[0]; dr=eq.pct_change().fillna(0); years=max((eq.index[-1]-eq.index[0]).days/365.25,1/365.25)
    total=float(eq.iloc[-1]/eq.iloc[0]-1); cagr=float((1+total)**(1/years)-1); mdd=float((eq/eq.cummax()-1).min()); sd=dr.std(ddof=0)
    sharpe=float(dr.mean()/sd*np.sqrt(252)) if sd>0 else np.nan; calmar=float(cagr/abs(mdd)) if mdd<0 else np.nan
    return {'holding_return':total,'holding_cagr':cagr,'holding_sharpe':sharpe,'holding_max_drawdown':mdd,'holding_calmar':calmar,'holding_ending_equity':float(eq.iloc[-1])}

def split10(df):
    end=df.index.max().normalize(); start=end-pd.DateOffset(years=10); is_end=start+pd.DateOffset(years=6)-pd.Timedelta(days=1); val_end=start+pd.DateOffset(years=8)-pd.Timedelta(days=1)
    return [('IS',start,is_end),('Validation',is_end+pd.Timedelta(days=1),val_end),('OOS',val_end+pd.Timedelta(days=1),end)]

def main():
    rows=[]
    for sym in TICKERS:
        df=fetch_history(sym)
        for period,a,b in split10(df):
            seg=df.loc[(df.index>=a)&(df.index<=b)].copy(); s=backtest_same_close(seg,2); bh=buyhold(seg)
            rows.append({'ticker':sym,'period':period,'start':seg.index.min().date().isoformat(),'end':seg.index.max().date().isoformat(),**s,**bh})
    out=pd.DataFrame(rows); out.to_csv(OUT/'spy_spmo_10y_same_close_vs_hold.csv',index=False); print(out.to_string(index=False))

if __name__=='__main__': main()
