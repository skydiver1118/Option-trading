#!/usr/bin/env python3
from pathlib import Path
import pandas as pd
import numpy as np

DATA=Path('data/williams_r/SPY_tradier_daily.csv')
OUT=Path('data/williams_r/10y_spy_buyhold_benchmark.csv')

df=pd.read_csv(DATA,parse_dates=['date']).set_index('date').sort_index()
periods=[
('IS','2016-09-06','2022-09-02'),
('Validation','2022-09-06','2024-08-30'),
('OOS','2024-09-03','2026-09-03'),
]
rows=[]
for label,start,end in periods:
    x=df.loc[(df.index>=pd.Timestamp(start)) & (df.index<=pd.Timestamp(end))].copy()
    start_px=float(x['close'].iloc[0]); end_px=float(x['close'].iloc[-1])
    total=end_px/start_px-1
    years=max((x.index[-1]-x.index[0]).days/365.25,1/365.25)
    cagr=(end_px/start_px)**(1/years)-1
    eq=x['close']/start_px*100000
    peak=eq.cummax(); mdd=float((eq/peak-1).min())
    dr=x['close'].pct_change().dropna(); vol=float(dr.std(ddof=0)*np.sqrt(252)); sharpe=float(dr.mean()/dr.std(ddof=0)*np.sqrt(252)) if dr.std(ddof=0)>0 else np.nan
    calmar=float(cagr/abs(mdd)) if mdd<0 else np.nan
    rows.append({'period':label,'start':x.index[0].date().isoformat(),'end':x.index[-1].date().isoformat(),'start_close':start_px,'end_close':end_px,'total_return':total,'cagr':cagr,'annualized_volatility':vol,'sharpe':sharpe,'max_drawdown':mdd,'calmar':calmar,'exposure':1.0,'ending_equity':float(eq.iloc[-1])})
pd.DataFrame(rows).to_csv(OUT,index=False)
print(pd.DataFrame(rows).to_string(index=False))
