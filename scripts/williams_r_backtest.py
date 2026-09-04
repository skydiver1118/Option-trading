#!/usr/bin/env python3
import os, math, requests
from pathlib import Path
import pandas as pd
import numpy as np

TOKEN=os.environ['TRADIER_TOKEN']
BASE='https://api.tradier.com/v1'
OUT=Path('data/williams_r'); OUT.mkdir(parents=True, exist_ok=True)

def fetch_history(symbol='SPY', start='1993-01-29', end=None):
    if end is None: end=pd.Timestamp.today().date().isoformat()
    s=requests.Session(); s.headers.update({'Authorization':f'Bearer {TOKEN}','Accept':'application/json'})
    rows=[]; cur=pd.Timestamp(start); end_ts=pd.Timestamp(end)
    while cur<=end_ts:
        stop=min(end_ts, cur+pd.DateOffset(years=8)-pd.Timedelta(days=1))
        r=s.get(f'{BASE}/markets/history',params={'symbol':symbol,'interval':'daily','start':cur.date().isoformat(),'end':stop.date().isoformat()},timeout=30)
        r.raise_for_status(); h=r.json().get('history') or {}; d=h.get('day') or []
        if isinstance(d,dict): d=[d]
        rows.extend(d); cur=stop+pd.Timedelta(days=1)
    df=pd.DataFrame(rows)
    df['date']=pd.to_datetime(df['date'])
    for c in ['open','high','low','close','volume']: df[c]=pd.to_numeric(df[c],errors='coerce')
    return df.drop_duplicates('date').sort_values('date').set_index('date')

def add_wr(df,n):
    x=df.copy(); hh=x.high.rolling(n).max(); ll=x.low.rolling(n).min(); rng=hh-ll
    x['wr']=np.where(rng.ne(0),-100*(hh-x.close)/rng,np.nan)
    x['prev_high']=x.high.shift(1); return x

def backtest(df,n,mode='close',slip_bps=0.0):
    x=add_wr(df,n); cash=100000.0; shares=0.0; pos=False; pending=None; trades=[]; eq=[]; ent=None; entdt=None; entsig=None
    b=slip_bps/10000
    for dt,row in x.iterrows():
        if mode=='next_open' and pending:
            if pending[0]=='buy' and not pos:
                p=float(row.open)*(1+b); shares=cash/p; cash=0; pos=True; ent=p; entdt=dt; entsig=pending[1]
            elif pending[0]=='sell' and pos:
                p=float(row.open)*(1-b); cash=shares*p; trades.append((entsig,entdt,ent,pending[1],dt,p,p/ent-1,pending[2])); shares=0; pos=False; ent=entdt=entsig=None
            pending=None
        wr=row.wr
        if pd.notna(wr):
            entry=wr < -90
            exit1=pd.notna(row.prev_high) and row.close > row.prev_high
            exit2=wr > -30
            if mode=='close':
                if pos and (exit1 or exit2):
                    p=float(row.close)*(1-b); cash=shares*p; reason='+'.join([z for z,v in [('close>prev_high',exit1),('wr>-30',exit2)] if v]); trades.append((entsig,entdt,ent,dt,dt,p,p/ent-1,reason)); shares=0; pos=False; ent=entdt=entsig=None
                elif (not pos) and entry:
                    p=float(row.close)*(1+b); shares=cash/p; cash=0; pos=True; ent=p; entdt=dt; entsig=dt
            else:
                if pos and (exit1 or exit2) and pending is None:
                    reason='+'.join([z for z,v in [('close>prev_high',exit1),('wr>-30',exit2)] if v]); pending=('sell',dt,reason)
                elif (not pos) and entry and pending is None: pending=('buy',dt,'')
        equity=cash if not pos else shares*float(row.close); eq.append((dt,equity,pos))
    t=pd.DataFrame(trades,columns=['entry_signal_date','entry_date','entry_price','exit_signal_date','exit_date','exit_price','return','exit_reason'])
    e=pd.DataFrame(eq,columns=['date','equity','in_position']).set_index('date')
    peak=e.equity.cummax(); mdd=float((e.equity/peak-1).min()); years=max((e.index[-1]-e.index[0]).days/365.25,1/365.25)
    total=float(e.equity.iloc[-1]/e.equity.iloc[0]-1); cagr=float((e.equity.iloc[-1]/e.equity.iloc[0])**(1/years)-1)
    dr=e.equity.pct_change().fillna(0); vol=float(dr.std(ddof=0)*np.sqrt(252)); sharpe=float(dr.mean()/dr.std(ddof=0)*np.sqrt(252)) if dr.std(ddof=0)>0 else np.nan
    calmar=float(cagr/abs(mdd)) if mdd<0 else np.nan
    if len(t):
        win=t.loc[t['return']>0,'return']; loss=t.loc[t['return']<0,'return']; pf=float(win.sum()/abs(loss.sum())) if len(loss) else math.inf; wrate=float((t['return']>0).mean()); avg=float(t['return'].mean()); med=float(t['return'].median())
        holds=(pd.to_datetime(t.exit_date)-pd.to_datetime(t.entry_date)).dt.days; avg_hold=float(holds.mean())
    else: pf=wrate=avg=med=avg_hold=np.nan
    return {'lookback':n,'mode':mode,'trades':len(t),'win_rate':wrate,'profit_factor':pf,'avg_trade':avg,'median_trade':med,'avg_hold_days':avg_hold,'total_return':total,'cagr':cagr,'annualized_volatility':vol,'sharpe':sharpe,'max_drawdown':mdd,'calmar':calmar,'exposure':float(e.in_position.mean()),'ending_equity':float(e.equity.iloc[-1])},t,e

def slice_period(df,start,end):
    return df.loc[(df.index>=pd.Timestamp(start)) & (df.index<=pd.Timestamp(end))].copy()

def ten_year_split(df):
    end=df.index.max().normalize()
    start=end-pd.DateOffset(years=10)
    is_end=start+pd.DateOffset(years=6)-pd.Timedelta(days=1)
    val_start=is_end+pd.Timedelta(days=1); val_end=start+pd.DateOffset(years=8)-pd.Timedelta(days=1)
    oos_start=val_end+pd.Timedelta(days=1)
    return [('IS',start,is_end),('Validation',val_start,val_end),('OOS',oos_start,end)]

def main():
    df=fetch_history(); df.to_csv(OUT/'SPY_tradier_daily.csv')

    # Full-history replication retained.
    rows=[]
    for n in range(2,26):
        s,t,e=backtest(df,n,'close',0); rows.append(s)
        if n==2: t.to_csv(OUT/'trades_lookback_2_close.csv',index=False); e.to_csv(OUT/'equity_lookback_2_close.csv')
    opt=pd.DataFrame(rows); opt.to_csv(OUT/'optimization_2_25_close.csv',index=False)
    s2,t2,e2=backtest(df,2,'next_open',2.0); pd.DataFrame([s2]).to_csv(OUT/'reference_next_open_2bps.csv',index=False)
    ref=opt.loc[opt.lookback.eq(2)].iloc[0].to_dict(); pd.DataFrame([ref,s2]).to_csv(OUT/'summary.csv',index=False)

    # Most recent 10 years: 60% calendar time IS, 20% validation, 20% OOS.
    periods=ten_year_split(df)
    period_meta=[]
    fixed2=[]
    for label,start,end in periods:
        seg=slice_period(df,start,end)
        period_meta.append({'period':label,'start':seg.index.min().date().isoformat(),'end':seg.index.max().date().isoformat(),'bars':len(seg)})
        s,t,e=backtest(seg,2,'close',0); s.update({'period':label,'start':seg.index.min().date().isoformat(),'end':seg.index.max().date().isoformat()}); fixed2.append(s)
        t.to_csv(OUT/f'10y_{label.lower()}_lookback_2_trades.csv',index=False); e.to_csv(OUT/f'10y_{label.lower()}_lookback_2_equity.csv')
    fixed2_df=pd.DataFrame(fixed2); fixed2_df.to_csv(OUT/'10y_split_fixed_lookback_2.csv',index=False)
    pd.DataFrame(period_meta).to_csv(OUT/'10y_split_periods.csv',index=False)

    # Parameter selection uses IS only; validation and OOS are not used to choose lookback.
    is_label,is_start,is_end=periods[0]; is_df=slice_period(df,is_start,is_end)
    is_opt=[]
    for n in range(2,26):
        s,_,_=backtest(is_df,n,'close',0); is_opt.append(s)
    is_opt_df=pd.DataFrame(is_opt).sort_values(['cagr','profit_factor'],ascending=False)
    is_opt_df.to_csv(OUT/'10y_IS_optimization_2_25.csv',index=False)
    chosen=int(is_opt_df.iloc[0].lookback)

    locked=[]
    for label,start,end in periods:
        seg=slice_period(df,start,end); s,t,e=backtest(seg,chosen,'close',0); s.update({'period':label,'start':seg.index.min().date().isoformat(),'end':seg.index.max().date().isoformat(),'selected_on':'IS_CAGR','locked_lookback':chosen}); locked.append(s)
    locked_df=pd.DataFrame(locked); locked_df.to_csv(OUT/'10y_split_IS_selected_locked.csv',index=False)

    # Executable sensitivity for fixed 2-day strategy: next-open + 2 bps each side.
    real=[]
    for label,start,end in periods:
        seg=slice_period(df,start,end); s,_,_=backtest(seg,2,'next_open',2.0); s.update({'period':label,'start':seg.index.min().date().isoformat(),'end':seg.index.max().date().isoformat()}); real.append(s)
    pd.DataFrame(real).to_csv(OUT/'10y_split_lookback_2_next_open_2bps.csv',index=False)

    print('10-year periods:'); print(pd.DataFrame(period_meta).to_string(index=False))
    print('\nFixed Williams %R(2), close-fill:'); print(fixed2_df.to_string(index=False))
    print(f'\nIS-selected lookback by CAGR: {chosen}')
    print(locked_df.to_string(index=False))
    print('\nWilliams %R(2), next-open + 2 bps:'); print(pd.DataFrame(real).to_string(index=False))

if __name__=='__main__': main()
