#!/usr/bin/env python3
import os, math, requests
from pathlib import Path
import pandas as pd
import numpy as np

TOKEN=os.environ['TRADIER_TOKEN']
BASE='https://api.tradier.com/v1'
OUT=Path('data/williams_r'); OUT.mkdir(parents=True, exist_ok=True)

def fetch_history(symbol='SPY', start=None, end=None):
    if end is None: end=pd.Timestamp.today().date().isoformat()
    if start is None: start=(pd.Timestamp(end)-pd.DateOffset(years=10, days=10)).date().isoformat()
    s=requests.Session(); s.headers.update({'Authorization':f'Bearer {TOKEN}','Accept':'application/json'})
    rows=[]; cur=pd.Timestamp(start); end_ts=pd.Timestamp(end)
    while cur<=end_ts:
        stop=min(end_ts, cur+pd.DateOffset(years=8)-pd.Timedelta(days=1))
        r=s.get(f'{BASE}/markets/history',params={'symbol':symbol,'interval':'daily','start':cur.date().isoformat(),'end':stop.date().isoformat()},timeout=30)
        r.raise_for_status(); h=r.json().get('history') or {}; d=h.get('day') or []
        if isinstance(d,dict): d=[d]
        rows.extend(d); cur=stop+pd.Timedelta(days=1)
    df=pd.DataFrame(rows); df['date']=pd.to_datetime(df['date'])
    for c in ['open','high','low','close','volume']: df[c]=pd.to_numeric(df[c],errors='coerce')
    return df.drop_duplicates('date').sort_values('date').set_index('date')

def add_wr(df,n):
    x=df.copy(); hh=x.high.rolling(n).max(); ll=x.low.rolling(n).min(); rng=hh-ll
    x['wr']=np.where(rng.ne(0),-100*(hh-x.close)/rng,np.nan)
    x['prev_high']=x.high.shift(1)
    return x

def backtest_next_close(df,n,slip_bps=0.0):
    x=add_wr(df,n); cash=100000.0; shares=0.0; pos=False; pending=None; trades=[]; eq=[]
    ent=None; entdt=None; entsig=None; b=slip_bps/10000
    for dt,row in x.iterrows():
        # Execute yesterday's signal at today's CLOSE.
        if pending is not None:
            side,sigdt,reason=pending
            if side=='buy' and not pos:
                p=float(row.close)*(1+b); shares=cash/p; cash=0.0; pos=True; ent=p; entdt=dt; entsig=sigdt
            elif side=='sell' and pos:
                p=float(row.close)*(1-b); cash=shares*p
                trades.append((entsig,entdt,ent,sigdt,dt,p,p/ent-1,reason))
                shares=0.0; pos=False; ent=entdt=entsig=None
            pending=None
        wr=row.wr
        if pd.notna(wr):
            entry=wr < -90
            exit1=pd.notna(row.prev_high) and row.close > row.prev_high
            exit2=wr > -30
            if pos and (exit1 or exit2) and pending is None:
                reason='+'.join([z for z,v in [('close>prev_high',exit1),('wr>-30',exit2)] if v])
                pending=('sell',dt,reason)
            elif (not pos) and entry and pending is None:
                pending=('buy',dt,'')
        equity=cash if not pos else shares*float(row.close)
        eq.append((dt,equity,pos))
    t=pd.DataFrame(trades,columns=['entry_signal_date','entry_date','entry_price','exit_signal_date','exit_date','exit_price','return','exit_reason'])
    e=pd.DataFrame(eq,columns=['date','equity','in_position']).set_index('date')
    peak=e.equity.cummax(); mdd=float((e.equity/peak-1).min()); years=max((e.index[-1]-e.index[0]).days/365.25,1/365.25)
    total=float(e.equity.iloc[-1]/e.equity.iloc[0]-1); cagr=float((e.equity.iloc[-1]/e.equity.iloc[0])**(1/years)-1)
    dr=e.equity.pct_change().fillna(0); sd=dr.std(ddof=0); vol=float(sd*np.sqrt(252)); sharpe=float(dr.mean()/sd*np.sqrt(252)) if sd>0 else np.nan
    calmar=float(cagr/abs(mdd)) if mdd<0 else np.nan
    if len(t):
        win=t.loc[t['return']>0,'return']; loss=t.loc[t['return']<0,'return']
        pf=float(win.sum()/abs(loss.sum())) if len(loss) else math.inf; wrate=float((t['return']>0).mean()); avg=float(t['return'].mean()); med=float(t['return'].median())
        holds=(pd.to_datetime(t.exit_date)-pd.to_datetime(t.entry_date)).dt.days; avg_hold=float(holds.mean())
    else: pf=wrate=avg=med=avg_hold=np.nan
    return {'lookback':n,'mode':'next_close','trades':len(t),'win_rate':wrate,'profit_factor':pf,'avg_trade':avg,'median_trade':med,'avg_hold_days':avg_hold,'total_return':total,'cagr':cagr,'annualized_volatility':vol,'sharpe':sharpe,'max_drawdown':mdd,'calmar':calmar,'exposure':float(e.in_position.mean()),'ending_equity':float(e.equity.iloc[-1])},t,e

def ten_year_split(df):
    end=df.index.max().normalize(); start=end-pd.DateOffset(years=10)
    is_end=start+pd.DateOffset(years=6)-pd.Timedelta(days=1)
    val_start=is_end+pd.Timedelta(days=1); val_end=start+pd.DateOffset(years=8)-pd.Timedelta(days=1)
    return [('IS',start,is_end),('Validation',val_start,val_end),('OOS',val_end+pd.Timedelta(days=1),end)]

def cut(df,a,b): return df.loc[(df.index>=a)&(df.index<=b)].copy()

def main():
    df=fetch_history(); periods=ten_year_split(df)
    # Fixed published lookback 2, all periods next-close execution.
    fixed=[]
    for label,a,b in periods:
        seg=cut(df,a,b); s,t,e=backtest_next_close(seg,2,0.0)
        s.update({'period':label,'start':seg.index.min().date().isoformat(),'end':seg.index.max().date().isoformat()}); fixed.append(s)
        t.to_csv(OUT/f'10y_{label.lower()}_lookback_2_next_close_trades.csv',index=False)
        e.to_csv(OUT/f'10y_{label.lower()}_lookback_2_next_close_equity.csv')
    pd.DataFrame(fixed).to_csv(OUT/'10y_split_lookback_2_next_close.csv',index=False)

    # Select lookback 2..25 using IS next-close CAGR only, then lock for Validation/OOS.
    is_df=cut(df,periods[0][1],periods[0][2]); opts=[]
    for n in range(2,26):
        s,_,_=backtest_next_close(is_df,n,0.0); opts.append(s)
    opt=pd.DataFrame(opts).sort_values(['cagr','profit_factor'],ascending=False)
    opt.to_csv(OUT/'10y_IS_optimization_2_25_next_close.csv',index=False)
    chosen=int(opt.iloc[0].lookback)
    locked=[]
    for label,a,b in periods:
        seg=cut(df,a,b); s,_,_=backtest_next_close(seg,chosen,0.0)
        s.update({'period':label,'start':seg.index.min().date().isoformat(),'end':seg.index.max().date().isoformat(),'selected_on':'IS_CAGR_next_close','locked_lookback':chosen}); locked.append(s)
    pd.DataFrame(locked).to_csv(OUT/'10y_split_IS_selected_locked_next_close.csv',index=False)
    print('Fixed WR(2), next close:'); print(pd.DataFrame(fixed).to_string(index=False))
    print(f'\nIS-selected lookback: {chosen}'); print(pd.DataFrame(locked).to_string(index=False))

if __name__=='__main__': main()
