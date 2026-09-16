import os, json, urllib.request
import pandas as pd
import numpy as np

SYMS=['SOXL','VGT','SPMO','VOO']; START='2010-01-01'; END='2026-09-16'
TOKEN=os.environ.get('TRADIER_TOKEN') or os.environ.get('TRADIER_ACCESS_TOKEN')
if not TOKEN: raise RuntimeError('Missing TRADIER_TOKEN')

def get_hist(sym):
    url=f'https://api.tradier.com/v1/markets/history?symbol={sym}&interval=daily&start={START}&end={END}'
    req=urllib.request.Request(url,headers={'Authorization':f'Bearer {TOKEN}','Accept':'application/json'})
    with urllib.request.urlopen(req) as r: j=json.load(r)
    d=j['history']['day']; d=[d] if isinstance(d,dict) else d
    x=pd.DataFrame(d); x['date']=pd.to_datetime(x.date,errors='coerce')
    for c in ['open','high','low','close']: x[c]=pd.to_numeric(x[c],errors='coerce')
    x=x.dropna(subset=['date','open','close']).sort_values('date').drop_duplicates('date').reset_index(drop=True)
    return x

def run(sym,x,lookback=7,cost_bps=5):
    x=x.copy(); x['roc']=x.close/x.close.shift(lookback)-1
    trades=[]; pos=False; entry=entrydate=None
    for i in range(lookback+1,len(x)):
        sig=x.roc.iloc[i-1]
        if not pos and sig < -.03:
            pos=True; entry=x.open.iloc[i]; entrydate=x.date.iloc[i]
        elif pos and sig > .03:
            exitp=x.open.iloc[i]; gross=exitp/entry-1
            net=(1+gross)*(1-cost_bps/10000)**2-1
            trades.append((entrydate,x.date.iloc[i],entry,exitp,gross,net)); pos=False
    if pos:
        exitp=x.close.iloc[-1]; gross=exitp/entry-1
        net=(1+gross)*(1-cost_bps/10000)-1
        trades.append((entrydate,x.date.iloc[-1],entry,exitp,gross,net))
    t=pd.DataFrame(trades,columns=['entry_date','exit_date','entry','exit','gross','net'])
    if t.empty: return None
    eq=(1+t.net).cumprod(); dd=eq/eq.cummax()-1
    gp=t.loc[t.net>0,'net'].sum(); gl=-t.loc[t.net<0,'net'].sum()
    years=(x.date.iloc[-1]-x.date.iloc[lookback]).days/365.25
    bh=x.close.iloc[-1]/x.close.iloc[lookback]
    return {'symbol':sym,'lookback':lookback,'cost_bps_side':cost_bps,'start':str(x.date.iloc[lookback].date()),'end':str(x.date.iloc[-1].date()),'trades':len(t),'win_rate':(t.net>0).mean(),'avg_trade':t.net.mean(),'profit_factor':gp/gl if gl else None,'growth_multiple':eq.iloc[-1],'cagr':eq.iloc[-1]**(1/years)-1,'max_dd_closed':dd.min(),'buy_hold_multiple':bh,'buy_hold_cagr':bh**(1/years)-1}

data={s:get_hist(s) for s in SYMS}
base=[run(s,data[s],7,5) for s in SYMS]
robust=[run(s,data[s],lb,c) for s in SYMS for lb in range(4,12) for c in [0,5]]
print('BASELINE ROC7, 5 BPS/SIDE')
print(json.dumps(base,indent=2))
pd.DataFrame(base).to_csv('roc7_results.csv',index=False)
pd.DataFrame(robust).to_csv('roc4_11_robustness.csv',index=False)
