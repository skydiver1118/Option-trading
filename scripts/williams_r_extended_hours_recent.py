#!/usr/bin/env python3
import os, math, requests
from pathlib import Path
import pandas as pd
import numpy as np

TOKEN=os.environ['TRADIER_TOKEN']
BASE='https://api.tradier.com/v1'
OUT=Path('data/williams_r'); OUT.mkdir(parents=True, exist_ok=True)
SYMBOLS=['SPX','SPMO']

S=requests.Session(); S.headers.update({'Authorization':f'Bearer {TOKEN}','Accept':'application/json'})

def fetch_daily(symbol,start,end):
    r=S.get(f'{BASE}/markets/history',params={'symbol':symbol,'interval':'daily','start':start,'end':end},timeout=30)
    r.raise_for_status(); h=r.json().get('history') or {}; d=h.get('day') or []
    if isinstance(d,dict): d=[d]
    if not d: return pd.DataFrame()
    df=pd.DataFrame(d); df['date']=pd.to_datetime(df['date'])
    for c in ['open','high','low','close','volume']:
        if c in df: df[c]=pd.to_numeric(df[c],errors='coerce')
    return df.drop_duplicates('date').sort_values('date').set_index('date')

def fetch_all_sessions(symbol,start,end):
    r=S.get(f'{BASE}/markets/timesales',params={'symbol':symbol,'interval':'15min','start':start,'end':end,'session_filter':'all'},timeout=30)
    r.raise_for_status(); series=r.json().get('series') or {}; d=series.get('data') or []
    if isinstance(d,dict): d=[d]
    if not d: return pd.DataFrame()
    df=pd.DataFrame(d)
    df['time']=pd.to_datetime(df['time'])
    for c in ['price','open','high','low','close','volume','vwap']:
        if c in df: df[c]=pd.to_numeric(df[c],errors='coerce')
    return df.sort_values('time').set_index('time')

def add_wr(df,n=2):
    x=df.copy(); hh=x.high.rolling(n).max(); ll=x.low.rolling(n).min(); rng=hh-ll
    x['wr']=np.where(rng.ne(0),-100*(hh-x.close)/rng,np.nan); x['prev_high']=x.high.shift(1)
    return x

def first_post_price(ts, day):
    if ts.empty: return np.nan, None
    day=pd.Timestamp(day).date()
    z=ts[ts.index.date==day]
    # force true post-market, not the 16:00 regular-session closing interval
    z=z[(z.index.time>=pd.Timestamp('16:15').time()) & (z.index.time<=pd.Timestamp('20:00').time())]
    if z.empty: return np.nan, None
    row=z.iloc[0]
    px=row.get('open',np.nan)
    if pd.isna(px): px=row.get('price',np.nan)
    if pd.isna(px): px=row.get('close',np.nan)
    return float(px), z.index[0]

def run_symbol(symbol,daily,ts):
    x=add_wr(daily,2)
    cash=100000.0; shares=0.0; pos=False; ent=None; entdt=None; trades=[]; skipped=0
    for dt,row in x.iterrows():
        if pd.isna(row.wr): continue
        entry=row.wr < -90
        exit1=pd.notna(row.prev_high) and row.close > row.prev_high
        exit2=row.wr > -30
        px,extdt=first_post_price(ts,dt)
        if pos and (exit1 or exit2):
            if pd.isna(px): skipped += 1; continue
            cash=shares*px; ret=px/ent-1
            trades.append({'signal_date':dt.date().isoformat(),'entry_date':entdt,'entry_price':ent,'exit_date':extdt.isoformat(),'exit_price':px,'return':ret,'reason':'+'.join([k for k,v in [('close>prev_high',exit1),('wr>-30',exit2)] if v])})
            shares=0; pos=False; ent=None; entdt=None
        elif (not pos) and entry:
            if pd.isna(px): skipped += 1; continue
            shares=cash/px; cash=0; pos=True; ent=px; entdt=extdt.isoformat()
    t=pd.DataFrame(trades)
    if len(t):
        wins=t[t['return']>0]['return']; losses=t[t['return']<0]['return']; pf=float(wins.sum()/abs(losses.sum())) if len(losses) else math.inf
        return {'symbol':symbol,'daily_bars':len(daily),'extended_bars':len(ts),'closed_trades':len(t),'win_rate':float((t['return']>0).mean()),'profit_factor':pf,'avg_trade':float(t['return'].mean()),'total_return_closed_trades':float(np.prod(1+t['return'])-1),'skipped_signals_no_post_price':skipped},t
    return {'symbol':symbol,'daily_bars':len(daily),'extended_bars':len(ts),'closed_trades':0,'win_rate':np.nan,'profit_factor':np.nan,'avg_trade':np.nan,'total_return_closed_trades':0.0,'skipped_signals_no_post_price':skipped},t

def main():
    end=pd.Timestamp.today().normalize()
    start=end-pd.Timedelta(days=17)
    # daily history needs a few prior bars to calculate WR at beginning of available post-market window
    daily_start=(start-pd.Timedelta(days=10)).date().isoformat()
    rows=[]
    for sym in SYMBOLS:
        try:
            daily=fetch_daily(sym,daily_start,end.date().isoformat())
            ts=fetch_all_sessions(sym,start.strftime('%Y-%m-%d 00:00'),end.strftime('%Y-%m-%d 23:59'))
            if daily.empty:
                rows.append({'symbol':sym,'error':'no daily data'}); continue
            # only signal dates for which extended data window could exist
            daily=daily[daily.index>=start]
            summary,trades=run_symbol(sym,daily,ts)
            rows.append(summary)
            trades.to_csv(OUT/f'{sym}_recent_postmarket_trades.csv',index=False)
        except Exception as e:
            rows.append({'symbol':sym,'error':repr(e)})
    pd.DataFrame(rows).to_csv(OUT/'recent_postmarket_SPX_SPMO_summary.csv',index=False)
    print(pd.DataFrame(rows).to_string(index=False))

if __name__=='__main__': main()
