#!/usr/bin/env python3
"""Frozen public-data replication study for five Herman Strategy Vault picks.
Data: Yahoo Finance via yfinance. Research only; OOS window is reused/compromised.
Signals are computed using historical warm-up, but each scored partition starts flat at $100k.
Primary comparison: long/cash ETF implementations, next-open fills, 10 bps adverse cost per side.
"""
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

SYMBOLS = ['QQQ','TQQQ','SOXL']
START, END, WARMUP = '2016-09-06','2026-09-04','2015-01-01'
PARTS = {'IS':('2016-09-06','2022-09-02'),'VALIDATION':('2022-09-06','2024-09-03'),'OOS_REUSED':('2024-09-04','2026-09-04')}
COSTS=[0,5,10,25,50]; PRIMARY=10; INITIAL=100000.0
OUT=Path('research/herman_vault_top5_yahoo_20260912'); OUT.mkdir(parents=True,exist_ok=True)

def get_data(sym):
    d=yf.download(sym,start=WARMUP,end='2026-09-05',auto_adjust=False,actions=False,progress=False)
    if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
    d=d.rename(columns=str.lower)[['open','high','low','close','volume']].dropna().sort_index()
    # yfinance OHLC are split-adjusted when back_adjust=False? preserve returned OHLC consistently.
    return d

def rma(s,n): return s.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
def rsi(s,n=2):
    z=s.diff(); u=rma(z.clip(lower=0),n); dn=rma((-z.clip(upper=0)),n); rs=u/dn.replace(0,np.nan)
    return 100-100/(1+rs)
def adx(d,n=14,smt=14):
    up=d.high.diff(); dw=-d.low.diff(); pdm=pd.Series(np.where((up>dw)&(up>0),up,0.0),index=d.index); mdm=pd.Series(np.where((dw>up)&(dw>0),dw,0.0),index=d.index)
    pc=d.close.shift(1); tr=pd.concat([d.high-d.low,(d.high-pc).abs(),(d.low-pc).abs()],axis=1).max(axis=1)
    atr=rma(tr,n); pdi=(100*rma(pdm,n)/atr).ffill(); mdi=(100*rma(mdm,n)/atr).ffill(); den=(pdi+mdi).replace(0,np.nan)
    return 100*rma(((pdi-mdi).abs()/den),smt)
def vwma(p,v,n): return (p*v).rolling(n).sum()/v.rolling(n).sum().replace(0,np.nan)
def kama(src,n=21):
    signal=(src-src.shift(n)).abs(); noise=src.diff().abs().rolling(n).sum(); er=(signal/noise.replace(0,np.nan)).fillna(0)
    sc=(er*(0.666-0.0645)+0.0645)**2; out=[]; prev=0.0
    for px,a in zip(src.astype(float),sc.astype(float)):
        prev=prev+(0.0 if np.isnan(a) else a)*(px-prev); out.append(prev)
    return pd.Series(out,index=src.index)

def add_indicators(d):
    x=d.copy(); gap=x.open-x.close.shift(1); gu=gap.clip(lower=0); gd=(-gap.clip(upper=0)); su=gu.rolling(40).sum(); sd=gd.rolling(40).sum()
    ratio=(100*su/sd.replace(0,np.nan)).where(sd.ne(0),1.0); x['gap_sig']=ratio.rolling(20).mean()
    x['rsi2']=rsi(x.close,2); x['sma50']=x.close.rolling(50).mean()
    x['ub50']=x.high.rolling(50).max().shift(1); x['lb50']=x.low.rolling(50).min().shift(1); x['ub30']=x.high.rolling(30).max().shift(1); x['lb30']=x.low.rolling(30).min().shift(1); x['width']=(x.ub50-x.lb50)/x.lb50*100
    x['adx']=adx(x,14,14); fast=vwma(x.close,x.volume,13); slow=vwma(x.close,x.volume,26); vmult=(fast/slow)**2; x['tti']=fast*vmult-slow/vmult; x['tti_sig']=x.tti.rolling(9).mean()
    vpc=vwma(x.close,x.volume,25)-x.close.rolling(25).mean(); vpr=vwma(x.close,x.volume,5)/x.close.rolling(5).mean(); vm=x.volume.rolling(5).mean()/x.volume.rolling(25).mean(); x['vpci']=vpc*vpr*vm
    x['kama']=kama(x.close,21); return x

def cross_above(a,b,pa,pb): return pd.notna(a) and pd.notna(b) and pd.notna(pa) and pd.notna(pb) and a>b and pa<=pb
def cross_below(a,b,pa,pb): return pd.notna(a) and pd.notna(b) and pd.notna(pa) and pd.notna(pb) and a<b and pa>=pb

def backtest(full,strat,start,end,cost_bps):
    # indicators may use pre-partition history; trading state is reset flat at partition start.
    idx=full.loc[start:end].index; cash=INITIAL; qty=0.0; entry_px=None; entry_dt=None; pending=None; counter=None; tp_done=False; trades=[]; rows=[]
    for j,dt in enumerate(idx):
        i=full.index.get_loc(dt); r=full.loc[dt]
        # execute close-generated market order at today's open
        if pending=='exit' and qty>0:
            px=float(r.open)*(1-cost_bps/10000); cash+=qty*px; trades.append({'entry_date':entry_dt.date().isoformat(),'exit_date':dt.date().isoformat(),'return':px/entry_px-1}); qty=0; entry_px=entry_dt=None; counter=None; tp_done=False; pending=None
        elif pending=='enter' and qty==0:
            px=float(r.open)*(1+cost_bps/10000); qty=cash/px; cash=0.0; entry_px=px; entry_dt=dt; pending=None
        # standing Donchian TP1: 50% at +2%, conservatively require day's high to reach limit
        if strat=='donchian' and qty>0 and not tp_done:
            limit=entry_px*1.02
            if float(r.high)>=limit:
                sell=qty*0.5; px=limit*(1-cost_bps/10000); cash+=sell*px; qty-=sell; tp_done=True
        equity=cash+qty*float(r.close); rows.append((dt,equity,1 if qty>0 else 0))
        if i==len(full)-1: continue
        prev=full.iloc[i-1] if i>0 else None
        # exact/default source signals, with long/cash adaptation where original is long/short
        if strat=='gap':
            if qty==0 and pd.notna(r.gap_sig) and pd.notna(prev.gap_sig) and r.gap_sig>prev.gap_sig: pending='enter'
            elif qty>0 and pd.notna(r.gap_sig) and pd.notna(prev.gap_sig) and r.gap_sig<prev.gap_sig: pending='exit'
        elif strat=='rsi2':
            if qty==0 and pd.notna(prev.rsi2) and prev.rsi2<25 and r.rsi2>prev.rsi2 and r.close>r.sma50:
                pending='enter'; counter=1  # source increments on signal bar immediately after setting 0
            elif counter is not None:
                counter+=1
                if counter>=5: pending='exit'
        elif strat=='volume':
            if qty==0 and r.adx>30 and r.tti>r.tti_sig and r.vpci>0: pending='enter'
            elif qty>0 and r.vpci<0: pending='exit'
        elif strat=='donchian':
            if qty==0 and cross_above(r.close,r.ub50,prev.close,prev.ub50) and r.width>3: pending='enter'
            elif qty>0 and cross_below(r.close,r.lb30,prev.close,prev.lb30): pending='exit'
        elif strat=='kama':
            pos=1 if prev.close>r.kama else (-1 if prev.close<r.kama else 0)
            if qty==0 and pos==1: pending='enter'
            elif qty>0 and pos==-1: pending='exit'
    if qty>0:
        dt=idx[-1]; px=float(full.loc[dt,'close'])*(1-cost_bps/10000); cash+=qty*px; trades.append({'entry_date':entry_dt.date().isoformat(),'exit_date':dt.date().isoformat(),'return':px/entry_px-1}); qty=0; rows[-1]=(dt,cash,0)
    curve=pd.DataFrame(rows,columns=['date','equity','position']).set_index('date'); return curve,pd.DataFrame(trades)

def calc(curve,trades):
    years=(curve.index[-1]-curve.index[0]).days/365.25; total=curve.equity.iloc[-1]/INITIAL-1; cagr=(curve.equity.iloc[-1]/INITIAL)**(1/years)-1; rr=curve.equity.pct_change().fillna(0); sd=rr.std(ddof=1); sharpe=rr.mean()/sd*math.sqrt(252) if sd>0 else np.nan; dd=curve.equity/curve.equity.cummax()-1; mdd=float(dd.min())
    return {'total_return':float(total),'cagr':float(cagr),'sharpe':float(sharpe),'max_drawdown':mdd,'calmar':float(cagr/abs(mdd)) if mdd<0 else np.nan,'trades':int(len(trades)),'win_rate':float((trades['return']>0).mean()) if len(trades) else 0.0,'exposure':float(curve.position.mean())}

def main():
    specs={'gap':'FMZ436872 defaults 40/20; exact long-only slope rule','rsi2':'FMZ482838 defaults RSI2<25 recovery + SMA50; exact source counter semantics','volume':'FMZ458248 defaults ADX14/14, TTI13/26/9, VPCI5/25; exact long-only rules','donchian':'FMZ442343 defaults 50/30, width>3%, 50% TP at +2%; long/cash adaptation','kama':'FMZ437539 Length21; source close[1] vs current KAMA; long/cash adaptation'}
    allrows=[]
    for sym in SYMBOLS:
        d=add_indicators(get_data(sym)); d=d.loc[:END]
        for strat in specs:
            for part,(a,b) in PARTS.items():
                for cost in COSTS:
                    curve,trades=backtest(d,strat,a,b,cost); m=calc(curve,trades); allrows.append({'symbol':sym,'strategy':strat,'partition':part,'cost_bps_per_side':cost,**m,'spec':specs[strat]})
                    if cost==PRIMARY:
                        curve.to_csv(OUT/f'{sym}_{strat}_{part}_equity.csv'); trades.to_csv(OUT/f'{sym}_{strat}_{part}_trades.csv',index=False)
    out=pd.DataFrame(allrows); out.to_csv(OUT/'summary_all_costs.csv',index=False); out[out.cost_bps_per_side==PRIMARY].to_csv(OUT/'summary_primary_10bps.csv',index=False)
    meta={'data':'Yahoo Finance via yfinance','download_end_exclusive':'2026-09-05','study_end':END,'partitions':PARTS,'primary_cost_bps_per_side':PRIMARY,'cost_sensitivity':COSTS,'oos_integrity':'COMPROMISED/REUSED','execution':'close signal, next observed session open; Donchian TP1 standing limit','sizing':'100% cash, fractional long/cash; partition reset','initial_cash':INITIAL}
    (OUT/'spec.json').write_text(json.dumps(meta,indent=2)); print(out[out.cost_bps_per_side==PRIMARY].to_string(index=False))
if __name__=='__main__': main()
