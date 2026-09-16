import os, json, math, urllib.request
import pandas as pd
import numpy as np

SYMS=['SOXL','VGT','SPMO']; START='2010-01-01'; END='2026-09-16'; COST_BPS=5
TOKEN=os.environ.get('TRADIER_TOKEN') or os.environ.get('TRADIER_ACCESS_TOKEN')
if not TOKEN: raise RuntimeError('Missing TRADIER_TOKEN')

def get_hist(sym):
    url=f'https://api.tradier.com/v1/markets/history?symbol={sym}&interval=daily&start={START}&end={END}'
    req=urllib.request.Request(url,headers={'Authorization':f'Bearer {TOKEN}','Accept':'application/json'})
    with urllib.request.urlopen(req) as r: j=json.load(r)
    d=j['history']['day']; d=[d] if isinstance(d,dict) else d
    x=pd.DataFrame(d); x['date']=pd.to_datetime(x.date)
    for c in ['open','high','low','close']: x[c]=pd.to_numeric(x[c])
    return x.sort_values('date').reset_index(drop=True)

def stats(sym,x):
    x=x.copy(); x['roc']=x.close/x.close.shift(7)-1
    trades=[]; pos=False; entry=None; entrydate=None
    for i in range(8,len(x)):
        sig=x.roc.iloc[i-1]
        if not pos and sig < -.03:
            pos=True; entry=x.open.iloc[i]; entrydate=x.date.iloc[i]
        elif pos and sig > .03:
            exitp=x.open.iloc[i]; ret=exitp/entry-1; net=(1+ret)*(1-COST_BPS/10000)**2-1
            trades.append((entrydate,x.date.iloc[i],entry,exitp,ret,net)); pos=False
    if pos:
        exitp=x.close.iloc[-1]; ret=exitp/entry-1; net=(1+ret)*(1-COST_BPS/10000)-1
        trades.append((entrydate,x.date.iloc[-1],entry,exitp,ret,net))
    t=pd.DataFrame(trades,columns=['entry_date','exit_date','entry','exit','gross','net'])
    eq=(1+t.net).cumprod(); dd=eq/eq.cummax()-1
    gp=t.loc[t.net>0,'net'].sum(); gl=-t.loc[t.net<0,'net'].sum()
    years=(x.date.iloc[-1]-x.date.iloc[7]).days/365.25
    strat_cagr=eq.iloc[-1]**(1/years)-1
    bh=x.close.iloc[-1]/x.close.iloc[7]-1; bh_cagr=(1+bh)**(1/years)-1
    return {'symbol':sym,'start':str(x.date.iloc[7].date()),'end':str(x.date.iloc[-1].date()),'trades':len(t),'win_rate':(t.net>0).mean(),'avg_trade':t.net.mean(),'profit_factor':gp/gl if gl else None,'growth_multiple_net':eq.iloc[-1],'cagr_net':strat_cagr,'max_dd_closed_equity':dd.min(),'buy_hold_multiple':1+bh,'buy_hold_cagr':bh_cagr}

out=[stats(s,get_hist(s)) for s in SYMS]
print(json.dumps(out,indent=2))
pd.DataFrame(out).to_csv('roc7_results.csv',index=False)
