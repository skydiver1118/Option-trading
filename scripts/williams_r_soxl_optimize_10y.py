#!/usr/bin/env python3
import os, math, itertools, requests
from pathlib import Path
import pandas as pd
import numpy as np

TOKEN=os.environ['TRADIER_TOKEN']
BASE='https://api.tradier.com/v1'
OUT=Path('data/williams_r'); OUT.mkdir(parents=True, exist_ok=True)

SYMBOL='SOXL'
LOOKBACKS=range(2,16)
ENTRY_THRESHOLDS=[-95,-90,-85,-80]
EXIT_THRESHOLDS=[-20,-25,-30,-35,-40]


def fetch_history(symbol=SYMBOL,start=None,end=None):
    if end is None: end=pd.Timestamp.today().date().isoformat()
    if start is None: start=(pd.Timestamp(end)-pd.DateOffset(years=10,days=10)).date().isoformat()
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


def add_wr(df,n):
    x=df.copy(); hh=x.high.rolling(n).max(); ll=x.low.rolling(n).min(); rng=hh-ll
    x['wr']=np.where(rng.ne(0),-100*(hh-x.close)/rng,np.nan); x['prev_high']=x.high.shift(1)
    return x


def backtest_same_close(df,n,entry_thr,exit_thr):
    x=add_wr(df,n); cash=100000.; shares=0.; pos=False; trades=[]; eq=[]; ent=None; entdt=None
    for dt,row in x.iterrows():
        wr=row.wr
        if pd.notna(wr):
            exit1=pd.notna(row.prev_high) and row.close>row.prev_high
            exit2=wr>exit_thr
            if pos and (exit1 or exit2):
                p=float(row.close); cash=shares*p
                trades.append((entdt,ent,dt,p,p/ent-1))
                shares=0.; pos=False; ent=entdt=None
            elif (not pos) and wr<entry_thr:
                p=float(row.close); shares=cash/p; cash=0.; pos=True; ent=p; entdt=dt
        equity=cash if not pos else shares*float(row.close); eq.append((dt,equity,pos))
    t=pd.DataFrame(trades,columns=['entry_date','entry_price','exit_date','exit_price','return'])
    e=pd.DataFrame(eq,columns=['date','equity','in_position']).set_index('date')
    dr=e.equity.pct_change().fillna(0); years=max((e.index[-1]-e.index[0]).days/365.25,1/365.25)
    total=float(e.equity.iloc[-1]/e.equity.iloc[0]-1); cagr=float((1+total)**(1/years)-1)
    peak=e.equity.cummax(); mdd=float((e.equity/peak-1).min()); sd=dr.std(ddof=0); vol=float(sd*np.sqrt(252)); sharpe=float(dr.mean()/sd*np.sqrt(252)) if sd>0 else np.nan; calmar=float(cagr/abs(mdd)) if mdd<0 else np.nan
    if len(t):
        wins=t[t['return']>0]['return']; losses=t[t['return']<0]['return']; pf=float(wins.sum()/abs(losses.sum())) if len(losses) else math.inf
        win_rate=float((t['return']>0).mean()); avg=float(t['return'].mean()); med=float(t['return'].median()); avg_hold=float((pd.to_datetime(t.exit_date)-pd.to_datetime(t.entry_date)).dt.days.mean())
    else: pf=win_rate=avg=med=avg_hold=np.nan
    return {'lookback':n,'entry_thr':entry_thr,'exit_thr':exit_thr,'trades':len(t),'win_rate':win_rate,'profit_factor':pf,'avg_trade':avg,'median_trade':med,'avg_hold_days':avg_hold,'total_return':total,'cagr':cagr,'annualized_volatility':vol,'sharpe':sharpe,'max_drawdown':mdd,'calmar':calmar,'exposure':float(e.in_position.mean()),'ending_equity':float(e.equity.iloc[-1])},t,e


def buyhold(df):
    close=df.close.astype(float); eq=100000*close/close.iloc[0]; dr=eq.pct_change().fillna(0); years=max((eq.index[-1]-eq.index[0]).days/365.25,1/365.25)
    total=float(eq.iloc[-1]/eq.iloc[0]-1); cagr=float((1+total)**(1/years)-1); mdd=float((eq/eq.cummax()-1).min()); sd=dr.std(ddof=0); sharpe=float(dr.mean()/sd*np.sqrt(252)) if sd>0 else np.nan; calmar=float(cagr/abs(mdd)) if mdd<0 else np.nan
    return {'bh_total_return':total,'bh_cagr':cagr,'bh_sharpe':sharpe,'bh_max_drawdown':mdd,'bh_calmar':calmar,'bh_ending_equity':float(eq.iloc[-1])}


def split10(df):
    end=df.index.max().normalize(); start=end-pd.DateOffset(years=10); is_end=start+pd.DateOffset(years=6)-pd.Timedelta(days=1); val_end=start+pd.DateOffset(years=8)-pd.Timedelta(days=1)
    return [('IS',start,is_end),('Validation',is_end+pd.Timedelta(days=1),val_end),('OOS',val_end+pd.Timedelta(days=1),end)]


def main():
    df=fetch_history(); periods=split10(df)
    is_df=df.loc[(df.index>=periods[0][1])&(df.index<=periods[0][2])].copy()
    rows=[]
    for n,ent,ex in itertools.product(LOOKBACKS,ENTRY_THRESHOLDS,EXIT_THRESHOLDS):
        s,_,_=backtest_same_close(is_df,n,ent,ex)
        if s['trades']>=30: rows.append(s)
    opt=pd.DataFrame(rows)
    # primary objective: Calmar, tie-break Sharpe, CAGR, PF; avoids pure-return overfit on SOXL
    opt=opt.sort_values(['calmar','sharpe','cagr','profit_factor'],ascending=False)
    opt.to_csv(OUT/'soxl_IS_parameter_grid_same_close.csv',index=False)
    best=opt.iloc[0]
    n=int(best.lookback); ent=float(best.entry_thr); ex=float(best.exit_thr)
    locked=[]
    for label,a,b in periods:
        seg=df.loc[(df.index>=a)&(df.index<=b)].copy(); s,t,e=backtest_same_close(seg,n,ent,ex); bh=buyhold(seg)
        row={'period':label,'start':seg.index.min().date().isoformat(),'end':seg.index.max().date().isoformat(),**s,**bh,'selected_on':'IS_CALMAR','locked_lookback':n,'locked_entry_thr':ent,'locked_exit_thr':ex}
        locked.append(row); t.to_csv(OUT/f'soxl_{label.lower()}_optimized_same_close_trades.csv',index=False)
    pd.DataFrame(locked).to_csv(OUT/'soxl_10y_IS_optimized_locked_same_close.csv',index=False)
    pd.DataFrame([{'lookback':n,'entry_thr':ent,'exit_thr':ex,'IS_calmar':best.calmar,'IS_sharpe':best.sharpe,'IS_cagr':best.cagr,'IS_profit_factor':best.profit_factor,'IS_trades':best.trades}]).to_csv(OUT/'soxl_selected_parameters_same_close.csv',index=False)
    print('Selected',n,ent,ex); print(pd.DataFrame(locked).to_string(index=False))

if __name__=='__main__': main()
