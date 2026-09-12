#!/usr/bin/env python3
import os, json, math
from pathlib import Path
import numpy as np, pandas as pd, requests

SYMBOLS=['QQQ','TQQQ','SOXL']; START='2016-09-06'; END='2026-09-04'; WARMUP='2015-01-01'; INITIAL=100000.0; COST_BPS=10
OUT=Path('research/herman_vault_top5_20260912'); OUT.mkdir(parents=True,exist_ok=True)
PARTS={'IS':('2016-09-06','2022-09-02'),'VALIDATION':('2022-09-06','2024-09-03'),'OOS_REUSED':('2024-09-04','2026-09-04')}

def fetch(sym):
    r=requests.get('https://api.tradier.com/v1/markets/history',headers={'Authorization':f"Bearer {os.environ['TRADIER_TOKEN']}",'Accept':'application/json'},params={'symbol':sym,'interval':'daily','start':WARMUP,'end':END},timeout=30)
    r.raise_for_status(); x=r.json().get('history',{}).get('day',[]); x=[x] if isinstance(x,dict) else x
    d=pd.DataFrame(x); d['date']=pd.to_datetime(d['date']); d=d.set_index('date').sort_index()
    for c in ['open','high','low','close','volume']: d[c]=pd.to_numeric(d[c],errors='coerce')
    return d.dropna(subset=['open','high','low','close'])

def rma(s,n): return s.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
def rsi(s,n=2):
    z=s.diff(); up=rma(z.clip(lower=0),n); dn=rma((-z.clip(upper=0)),n); rs=up/dn.replace(0,np.nan); return 100-100/(1+rs)
def adx(d,n=14):
    up=d.high.diff(); dn=-d.low.diff(); pdm=pd.Series(np.where((up>dn)&(up>0),up,0),index=d.index); mdm=pd.Series(np.where((dn>up)&(dn>0),dn,0),index=d.index)
    tr=pd.concat([(d.high-d.low),(d.high-d.close.shift()).abs(),(d.low-d.close.shift()).abs()],axis=1).max(axis=1); a=rma(tr,n); p=100*rma(pdm,n)/a; m=100*rma(mdm,n)/a; dx=100*(p-m).abs()/(p+m).replace(0,np.nan); return rma(dx,n)
def vwma(p,v,n): return (p*v).rolling(n).sum()/v.rolling(n).sum().replace(0,np.nan)
def kama(s,n=21):
    signal=(s-s.shift(n)).abs(); noise=s.diff().abs().rolling(n).sum(); er=(signal/noise.replace(0,np.nan)).fillna(0); sc=(er*(0.666-0.0645)+0.0645)**2
    out=np.full(len(s),np.nan); prev=0.0
    for i,(px,a) in enumerate(zip(s.to_numpy(float),sc.to_numpy(float))): prev=prev+(0 if np.isnan(a) else a)*(px-prev); out[i]=prev
    return pd.Series(out,index=s.index)

def indicators(d):
    x=d.copy(); gap=x.open-x.close.shift(1); gu=gap.clip(lower=0); gd=(-gap.clip(upper=0)); ratio=100*gu.rolling(40).sum()/gd.rolling(40).sum().replace(0,np.nan); ratio=ratio.where(gd.rolling(40).sum()!=0,1.0)
    x['gap_signal']=ratio.rolling(20).mean(); x['rsi2']=rsi(x.close,2); x['sma50']=x.close.rolling(50).mean()
    x['ub50']=x.high.rolling(50).max().shift(1); x['lb50']=x.low.rolling(50).min().shift(1); x['ub30']=x.high.rolling(30).max().shift(1); x['lb30']=x.low.rolling(30).min().shift(1); x['don_width']=(x.ub50-x.lb50)/x.lb50*100
    x['kama']=kama(x.close,21)
    x['adx14']=adx(x,14); vf=vwma(x.close,x.volume,13); vs=vwma(x.close,x.volume,26); x['tti']=vf-vs; x['tti_sig']=x.tti.ewm(span=9,adjust=False,min_periods=9).mean()
    vpc=vwma(x.close,x.volume,25)-x.close.rolling(25).mean(); vpr=vwma(x.close,x.volume,5)/x.close.rolling(5).mean(); vm=x.volume.rolling(5).mean()/x.volume.rolling(25).mean(); x['vpci']=vpc*vpr*vm
    return x

def metrics(curve,trades,start,end):
    c=curve.loc[start:end].copy(); years=max((c.index[-1]-c.index[0]).days/365.25,1/365.25); total=c.equity.iloc[-1]/c.equity.iloc[0]-1; cagr=(1+total)**(1/years)-1; rr=c.equity.pct_change().fillna(0); sd=rr.std(ddof=1); sharpe=rr.mean()/sd*math.sqrt(252) if sd>0 else np.nan; dd=c.equity/c.equity.cummax()-1; mdd=float(dd.min()); t=pd.DataFrame(trades); t=t[(pd.to_datetime(t.exit_date)>=pd.Timestamp(start))&(pd.to_datetime(t.exit_date)<=pd.Timestamp(end))] if len(t) else t
    return {'total_return':float(total),'cagr':float(cagr),'sharpe':float(sharpe),'max_drawdown':mdd,'calmar':float(cagr/abs(mdd)) if mdd<0 else np.nan,'trades':int(len(t)),'win_rate':float((t.ret>0).mean()) if len(t) else 0.0,'exposure':float(c.position.mean())}

def run_longcash(d,name):
    cash=INITIAL; shares=0.0; entry=None; pending=None; held_counter=None; rows=[]; trades=[]; partial=False
    for i,(dt,row) in enumerate(d.iterrows()):
        if pending and shares>0 and pending=='exit':
            px=float(row.open)*(1-COST_BPS/10000); cash += shares*px; trades.append({'entry_date':entry[0].isoformat(),'exit_date':dt.isoformat(),'ret':px/entry[1]-1}); shares=0; entry=None; pending=None; held_counter=None; partial=False
        elif pending and shares==0 and pending=='enter':
            px=float(row.open)*(1+COST_BPS/10000); shares=cash/px; cash=0; entry=(dt,px); pending=None; partial=False
        if name=='donchian' and shares>0 and (not partial) and row.high>=entry[1]*1.02:
            qty=shares*0.5; px=entry[1]*1.02*(1-COST_BPS/10000); cash+=qty*px; shares-=qty; partial=True
        equity=cash+shares*float(row.close); rows.append((dt,equity,1 if shares>0 else 0))
        if i==len(d)-1: continue
        if name=='gap':
            if shares==0 and row.gap_signal>row.get('gap_signal_prev',np.nan): pending='enter'
            elif shares>0 and row.gap_signal<row.get('gap_signal_prev',np.nan): pending='exit'
        elif name=='rsi2':
            if shares==0 and row.get('rsi2_prev',np.nan)<25 and row.rsi2>row.get('rsi2_prev',np.nan) and row.close>row.sma50: pending='enter'; held_counter=1
            elif shares>0:
                held_counter=(held_counter or 1)+1
                if held_counter>=5: pending='exit'
        elif name=='volume':
            if shares==0 and row.adx14>30 and row.tti>row.tti_sig and row.vpci>0: pending='enter'
            elif shares>0 and row.vpci<0: pending='exit'
        elif name=='donchian':
            prev=d.iloc[i-1] if i>0 else None
            if shares==0 and prev is not None and prev.close<=prev.ub50 and row.close>row.ub50 and row.don_width>3: pending='enter'
            elif shares>0 and prev is not None and prev.close>=prev.lb30 and row.close<row.lb30: pending='exit'
        elif name=='kama':
            pos=1 if row.get('close_prev',np.nan)>row.kama else -1 if row.get('close_prev',np.nan)<row.kama else 0
            if shares==0 and pos==1: pending='enter'
            elif shares>0 and pos==-1: pending='exit'
    if shares>0:
        px=float(d.close.iloc[-1])*(1-COST_BPS/10000); cash+=shares*px; trades.append({'entry_date':entry[0].isoformat(),'exit_date':d.index[-1].isoformat(),'ret':px/entry[1]-1}); rows[-1]=(rows[-1][0],cash,0)
    return pd.DataFrame(rows,columns=['date','equity','position']).set_index('date'),trades

def main():
    summary=[]; specs={'gap':'FMZ436872 exact long-only 40/20 slope','rsi2':'FMZ482838 exact signal; source counter semantics preserved approximately with 5-bar signal-to-exit','volume':'FMZ458248 long-only ADX/TTI/VPCI; standard formulas used','donchian':'FMZ442343 long-only ETF adaptation, 50/30, width>3%, 50% TP at +2%','kama':'FMZ437539 long/cash ETF adaptation, Length21'}
    for sym in SYMBOLS:
        d=indicators(fetch(sym)); d['gap_signal_prev']=d.gap_signal.shift(1); d['rsi2_prev']=d.rsi2.shift(1); d['close_prev']=d.close.shift(1); d=d.loc[START:END].copy()
        for strat in ['gap','rsi2','volume','donchian','kama']:
            curve,trades=run_longcash(d,strat); curve.to_csv(OUT/f'{sym}_{strat}_equity.csv'); pd.DataFrame(trades).to_csv(OUT/f'{sym}_{strat}_trades.csv',index=False)
            for part,(a,b) in PARTS.items():
                m=metrics(curve,trades,a,b); summary.append({'symbol':sym,'strategy':strat,'partition':part,**m,'spec':specs[strat],'cost_bps_per_side':COST_BPS})
    pd.DataFrame(summary).to_csv(OUT/'summary.csv',index=False); (OUT/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=True)); print(pd.DataFrame(summary).to_string(index=False))
if __name__=='__main__': main()
