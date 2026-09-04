#!/usr/bin/env python3
import os, math, io, requests
from pathlib import Path
import pandas as pd
import numpy as np

TOKEN=os.environ['TRADIER_TOKEN']
BASE='https://api.tradier.com/v1'
OUT=Path('data/williams_r'); OUT.mkdir(parents=True, exist_ok=True)


def fetch_spy(start='2016-08-25', end=None):
    if end is None: end=pd.Timestamp.today().date().isoformat()
    s=requests.Session(); s.headers.update({'Authorization':f'Bearer {TOKEN}','Accept':'application/json'})
    r=s.get(f'{BASE}/markets/history',params={'symbol':'SPY','interval':'daily','start':start,'end':end},timeout=30)
    r.raise_for_status(); h=r.json().get('history') or {}; d=h.get('day') or []
    if isinstance(d,dict): d=[d]
    df=pd.DataFrame(d); df['date']=pd.to_datetime(df['date'])
    for c in ['open','high','low','close','volume']: df[c]=pd.to_numeric(df[c],errors='coerce')
    return df.drop_duplicates('date').sort_values('date').set_index('date')


def fetch_tbill(start='2016-08-25', end=None):
    if end is None: end=pd.Timestamp.today().date().isoformat()
    url=f'https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO&cosd={start}&coed={end}'
    r=requests.get(url,timeout=30); r.raise_for_status()
    y=pd.read_csv(io.StringIO(r.text))
    y.columns=['date','yield_pct']; y['date']=pd.to_datetime(y['date']); y['yield_pct']=pd.to_numeric(y['yield_pct'],errors='coerce')
    return y.set_index('date').sort_index()


def add_wr(df,n=2):
    x=df.copy(); hh=x.high.rolling(n).max(); ll=x.low.rolling(n).min(); rng=hh-ll
    x['wr']=np.where(rng.ne(0),-100*(hh-x.close)/rng,np.nan); x['prev_high']=x.high.shift(1)
    return x


def ten_year_periods(df):
    end=df.index.max().normalize(); start=end-pd.DateOffset(years=10)
    is_end=start+pd.DateOffset(years=6)-pd.Timedelta(days=1)
    val_start=is_end+pd.Timedelta(days=1); val_end=start+pd.DateOffset(years=8)-pd.Timedelta(days=1)
    oos_start=val_end+pd.Timedelta(days=1)
    return [('IS',start,is_end),('Validation',val_start,val_end),('OOS',oos_start,end)]


def tactical_curve(seg, yields):
    x=add_wr(seg,2)
    y=yields.reindex(x.index).ffill().fillna(0.0)
    cash=1.0; shares=0.0; pos=False; pending=None; vals=[]
    for dt,row in x.iterrows():
        # Accrue one trading-day approximation of the annualized 3m Treasury yield while idle.
        if not pos:
            annual=max(float(y.loc[dt,'yield_pct']),0.0)/100.0
            cash *= (1.0 + annual/252.0)
        # Execute prior signal at today's close.
        if pending:
            side,sigdt,reason=pending
            if side=='buy' and not pos:
                p=float(row.close); shares=cash/p; cash=0.0; pos=True
            elif side=='sell' and pos:
                p=float(row.close); cash=shares*p; shares=0.0; pos=False
            pending=None
        wr=row.wr
        if pd.notna(wr):
            entry=wr < -90
            exit1=pd.notna(row.prev_high) and row.close > row.prev_high
            exit2=wr > -30
            if pos and (exit1 or exit2) and pending is None:
                pending=('sell',dt,'exit')
            elif (not pos) and entry and pending is None:
                pending=('buy',dt,'entry')
        vals.append(cash if not pos else shares*float(row.close))
    return pd.Series(vals,index=x.index,name='tactical')


def metrics(eq):
    eq=eq.dropna(); dr=eq.pct_change().fillna(0.0); years=max((eq.index[-1]-eq.index[0]).days/365.25,1/365.25)
    total=float(eq.iloc[-1]/eq.iloc[0]-1); cagr=float((eq.iloc[-1]/eq.iloc[0])**(1/years)-1)
    vol=float(dr.std(ddof=0)*np.sqrt(252)); sharpe=float(dr.mean()/dr.std(ddof=0)*np.sqrt(252)) if dr.std(ddof=0)>0 else np.nan
    mdd=float((eq/eq.cummax()-1).min()); calmar=float(cagr/abs(mdd)) if mdd<0 else np.nan
    return total,cagr,vol,sharpe,mdd,calmar


def main():
    spy=fetch_spy(); tb=fetch_tbill(start=spy.index.min().date().isoformat(),end=spy.index.max().date().isoformat())
    rows=[]; curves=[]
    for period,start,end in ten_year_periods(spy):
        seg=spy.loc[(spy.index>=start)&(spy.index<=end)].copy()
        core=seg.close/seg.close.iloc[0]
        tactical=tactical_curve(seg,tb)
        # Tactical allocation from 0% (pure SPY) to 100% in 10% increments; no rebalancing within each period.
        for tw in np.arange(0,1.0001,0.1):
            combined=(1-tw)*core + tw*tactical
            total,cagr,vol,sharpe,mdd,calmar=metrics(combined)
            rows.append({'period':period,'start':seg.index.min().date().isoformat(),'end':seg.index.max().date().isoformat(),
                         'tactical_weight':round(float(tw),1),'core_spy_weight':round(float(1-tw),1),
                         'total_return':total,'cagr':cagr,'annualized_volatility':vol,'sharpe':sharpe,
                         'max_drawdown':mdd,'calmar':calmar,'ending_equity_100k':100000*combined.iloc[-1]})
        # Save a few representative curves.
        for tw in [0.0,0.2,0.3,0.5,1.0]:
            c=(1-tw)*core+tw*tactical
            curves.append(pd.DataFrame({'date':c.index,'period':period,'tactical_weight':tw,'equity':100000*c.values}))
    res=pd.DataFrame(rows); res.to_csv(OUT/'10y_core_spy_plus_wr_tactical_grid.csv',index=False)
    pd.concat(curves,ignore_index=True).to_csv(OUT/'10y_core_spy_plus_wr_selected_curves.csv',index=False)

    # Select tactical weight using IS Sharpe only, then lock it for validation and OOS.
    isr=res[res.period.eq('IS')].sort_values(['sharpe','cagr'],ascending=False)
    chosen=float(isr.iloc[0].tactical_weight)
    locked=res[res.tactical_weight.eq(chosen)].copy(); locked['selected_on']='IS_SHARPE'; locked['locked_tactical_weight']=chosen
    locked.to_csv(OUT/'10y_core_spy_plus_wr_IS_selected_locked.csv',index=False)

    print('Grid:')
    print(res.to_string(index=False))
    print(f'\nIS-selected tactical weight by Sharpe: {chosen:.0%}')
    print(locked.to_string(index=False))

if __name__=='__main__': main()
