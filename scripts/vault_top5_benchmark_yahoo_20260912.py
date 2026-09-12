#!/usr/bin/env python3
import json, math
from pathlib import Path
import numpy as np, pandas as pd, yfinance as yf
SYMS=['QQQ','TQQQ','SOXL']; PARTS={'IS':('2016-09-06','2022-09-02'),'VALIDATION':('2022-09-06','2024-09-03'),'OOS_REUSED':('2024-09-04','2026-09-04')}; INITIAL=100000.; COST=10
OUT=Path('research/herman_vault_top5_yahoo_20260912'); OUT.mkdir(parents=True,exist_ok=True)
rows=[]
for s in SYMS:
    d=yf.download(s,start='2016-09-01',end='2026-09-05',auto_adjust=False,actions=False,progress=False)
    if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
    d=d.rename(columns=str.lower).dropna(subset=['open','close'])
    for p,(a,b) in PARTS.items():
        q=d.loc[a:b]; buy=float(q.open.iloc[0])*(1+COST/10000); sell=float(q.close.iloc[-1])*(1-COST/10000); total=sell/buy-1; years=(q.index[-1]-q.index[0]).days/365.25; cagr=(1+total)**(1/years)-1
        curve=q.close/buy*INITIAL; curve.iloc[0]=INITIAL; rr=curve.pct_change().fillna(0); sd=rr.std(ddof=1); sh=rr.mean()/sd*math.sqrt(252) if sd>0 else np.nan; dd=curve/curve.cummax()-1; mdd=float(dd.min())
        rows.append({'symbol':s,'partition':p,'cost_bps_per_side':COST,'total_return':total,'cagr':cagr,'sharpe':sh,'max_drawdown':mdd,'calmar':cagr/abs(mdd) if mdd<0 else np.nan})
pd.DataFrame(rows).to_csv(OUT/'buyhold_primary_10bps.csv',index=False); print(pd.DataFrame(rows).to_string(index=False))
