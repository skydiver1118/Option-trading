#!/usr/bin/env python3
"""Backtest the frozen daily 3-factor TCAR signal on TQQQ using Tradier daily data.

Rules intentionally mirror the SOXL no-QQQ-sizing test:
Entry signal at close: WR(2)<-90 AND CCI(5)<-80 AND ADX(20)>=15
Entry execution: next trading-day open
Exit signal at close: Close > prior-day High OR WR(2)>-30
Exit execution: next trading-day open
Position size: 100%, one long position at a time
"""
import json, math, os
from pathlib import Path
import numpy as np
import pandas as pd
import requests

SYMBOL='TQQQ'; START='2016-09-06'; END='2026-09-04'; WARMUP='2015-09-01'; INITIAL=100000.0
OUT=Path('data/williams_r'); OUT.mkdir(parents=True,exist_ok=True)

def fetch_tradier():
    token=os.environ['TRADIER_TOKEN']
    r=requests.get('https://api.tradier.com/v1/markets/history',headers={'Authorization':f'Bearer {token}','Accept':'application/json'},params={'symbol':SYMBOL,'interval':'daily','start':WARMUP,'end':END},timeout=30)
    r.raise_for_status(); payload=r.json(); days=payload.get('history',{}).get('day',[])
    if isinstance(days,dict): days=[days]
    df=pd.DataFrame(days); df['date']=pd.to_datetime(df['date']); df=df.set_index('date').sort_index()
    for c in ['open','high','low','close','volume']: df[c]=pd.to_numeric(df[c],errors='coerce')
    return df.dropna(subset=['open','high','low','close'])

def indicators(df):
    d=df.copy(); hh=d.high.rolling(2).max(); ll=d.low.rolling(2).min(); den=(hh-ll).replace(0,np.nan)
    d['wr']=-100*(hh-d.close)/den
    tp=(d.high+d.low+d.close)/3; sma=tp.rolling(5).mean(); md=tp.rolling(5).apply(lambda x: np.mean(np.abs(x-x.mean())),raw=True)
    d['cci']=(tp-sma)/(0.015*md.replace(0,np.nan))
    up=d.high.diff(); dn=-d.low.diff(); plus=np.where((up>dn)&(up>0),up,0.0); minus=np.where((dn>up)&(dn>0),dn,0.0)
    tr=pd.concat([(d.high-d.low),(d.high-d.close.shift()).abs(),(d.low-d.close.shift()).abs()],axis=1).max(axis=1)
    atr=tr.ewm(alpha=1/20,adjust=False,min_periods=20).mean(); p=pd.Series(plus,index=d.index).ewm(alpha=1/20,adjust=False,min_periods=20).mean(); m=pd.Series(minus,index=d.index).ewm(alpha=1/20,adjust=False,min_periods=20).mean()
    pdi=100*p/atr; mdi=100*m/atr; dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan); d['adx']=dx.ewm(alpha=1/20,adjust=False,min_periods=20).mean(); d['prior_high']=d.high.shift(1)
    return d

def backtest(d):
    d=d.loc[START:END].copy(); eq=INITIAL; position=False; entry=None; trades=[]; daily=[]
    for i in range(len(d)):
        row=d.iloc[i]; date=d.index[i]
        # execute action signaled at prior close
        if position and entry.get('pending_exit',False):
            px=float(row.open); ret=px/entry['price']-1; eq*=1+ret
            trades.append({'entry_signal_date':entry['signal_date'].date().isoformat(),'entry_date':entry['date'].date().isoformat(),'entry_price':entry['price'],'exit_date':date.date().isoformat(),'exit_price':px,'return':ret,'holding_days':(date-entry['date']).days})
            position=False; entry=None
        elif (not position) and i>0:
            prev=d.iloc[i-1]
            if bool(prev.wr < -90 and prev.cci < -80 and prev.adx >= 15):
                position=True; entry={'signal_date':d.index[i-1],'date':date,'price':float(row.open),'pending_exit':False}
        # mark equity at close
        mark=eq if not position else eq*(float(row.close)/entry['price'])
        daily.append((date,mark,1 if position else 0))
        if position:
            entry['pending_exit']=bool((row.close > row.prior_high) or (row.wr > -30))
    if position:
        px=float(d.iloc[-1].close); ret=px/entry['price']-1; eq*=1+ret
        trades.append({'entry_signal_date':entry['signal_date'].date().isoformat(),'entry_date':entry['date'].date().isoformat(),'entry_price':entry['price'],'exit_date':d.index[-1].date().isoformat(),'exit_price':px,'return':ret,'holding_days':(d.index[-1]-entry['date']).days})
    t=pd.DataFrame(trades); curve=pd.DataFrame(daily,columns=['date','equity','position']).set_index('date'); curve.iloc[-1,curve.columns.get_loc('equity')]=eq
    years=(curve.index[-1]-curve.index[0]).days/365.25; total=eq/INITIAL-1; cagr=(eq/INITIAL)**(1/years)-1
    rets=curve.equity.pct_change().fillna(0); sharpe=(rets.mean()/rets.std(ddof=1)*math.sqrt(252)) if rets.std(ddof=1)>0 else float('nan'); dd=curve.equity/curve.equity.cummax()-1; maxdd=float(dd.min())
    wins=t[t['return']>0]['return']; losses=t[t['return']<0]['return']; pf=float(wins.sum()/(-losses.sum())) if len(losses) else float('inf')
    hold_ret=float(d.close.iloc[-1]/d.open.iloc[0]-1); hold_curve=d.close/d.open.iloc[0]*INITIAL; hret=hold_curve.pct_change().fillna(0); hsh=(hret.mean()/hret.std(ddof=1)*math.sqrt(252)) if hret.std(ddof=1)>0 else float('nan'); hdd=float((hold_curve/hold_curve.cummax()-1).min()); hcagr=(1+hold_ret)**(1/years)-1
    metrics={'symbol':SYMBOL,'start':curve.index[0].date().isoformat(),'end':curve.index[-1].date().isoformat(),'trades':len(t),'win_rate':float((t['return']>0).mean()) if len(t) else 0,'profit_factor':pf,'avg_trade':float(t['return'].mean()) if len(t) else 0,'median_trade':float(t['return'].median()) if len(t) else 0,'avg_holding_days':float(t.holding_days.mean()) if len(t) else 0,'total_return':total,'cagr':cagr,'sharpe':sharpe,'max_drawdown':maxdd,'calmar':cagr/abs(maxdd) if maxdd<0 else float('nan'),'exposure':float(curve.position.mean()),'ending_value':eq,'hold_total_return':hold_ret,'hold_cagr':hcagr,'hold_sharpe':hsh,'hold_max_drawdown':hdd,'hold_calmar':hcagr/abs(hdd) if hdd<0 else float('nan'),'hold_ending_value':INITIAL*(1+hold_ret)}
    return metrics,t,curve

if __name__=='__main__':
    d=indicators(fetch_tradier()); m,t,c=backtest(d); t.to_csv(OUT/'tqqq_daily_tcar_10y_trades.csv',index=False); c.to_csv(OUT/'tqqq_daily_tcar_10y_equity.csv'); (OUT/'tqqq_daily_tcar_10y_metrics.json').write_text(json.dumps(m,indent=2,allow_nan=True))
    print(json.dumps(m,indent=2,allow_nan=True))
