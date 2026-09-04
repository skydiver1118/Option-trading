#!/usr/bin/env python3
import os, math, itertools, requests
from pathlib import Path
import pandas as pd
import numpy as np

TOKEN=os.environ['TRADIER_TOKEN']
BASE='https://api.tradier.com/v1'
OUT=Path('data/williams_r'); OUT.mkdir(parents=True, exist_ok=True)

WR_LOOKBACKS=[2,3,4,5,6,8,10,14,20]
WR_ENTRIES=[-80,-85,-90,-95]
WR_EXITS=[-40,-30,-20]
CCI_LOOKBACKS=[3,5,8,10,14,20]
CCI_ENTRIES=[-50,-80,-100,-120,-150,-200]
CCI_EXITS=[-50,0,50,100]
MIN_IS_TRADES=40

def fetch_history(symbol='SOXL', start='2015-09-01', end=None):
    if end is None: end=pd.Timestamp.today().date().isoformat()
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

def prep(df):
    x=df.copy(); x['prev_high']=x.high.shift(1); tp=(x.high+x.low+x.close)/3.0
    for n in WR_LOOKBACKS:
        hh=x.high.rolling(n).max(); ll=x.low.rolling(n).min(); rng=hh-ll
        x[f'wr{n}']=np.where(rng.ne(0),-100*(hh-x.close)/rng,np.nan)
    for n in CCI_LOOKBACKS:
        ma=tp.rolling(n).mean(); md=tp.rolling(n).apply(lambda z: np.mean(np.abs(z-z.mean())),raw=False)
        x[f'cci{n}']=(tp-ma)/(0.015*md.replace(0,np.nan))
    return x

def backtest(x,wrn,wre,wrx,ccin,ccie,ccix):
    cash=100000.0; shares=0.0; pos=False; ent=None; entdt=None; trades=[]; eq=[]
    for dt,row in x.iterrows():
        wr=row[f'wr{wrn}']; cci=row[f'cci{ccin}']
        if pd.notna(wr) and pd.notna(cci):
            entry=(wr<wre) and (cci<ccie)
            exit1=pd.notna(row.prev_high) and row.close>row.prev_high
            exit2=wr>wrx
            exit3=cci>ccix
            if pos and (exit1 or exit2 or exit3):
                p=float(row.close); cash=shares*p; trades.append((entdt,dt,p/ent-1)); shares=0; pos=False; ent=entdt=None
            elif (not pos) and entry:
                p=float(row.close); shares=cash/p; cash=0; pos=True; ent=p; entdt=dt
        equity=cash if not pos else shares*float(row.close); eq.append((dt,equity,pos))
    t=pd.DataFrame(trades,columns=['entry_date','exit_date','return']); e=pd.DataFrame(eq,columns=['date','equity','in_position']).set_index('date')
    if e.empty: return None
    dr=e.equity.pct_change().fillna(0); sd=dr.std(ddof=0); years=max((e.index[-1]-e.index[0]).days/365.25,1/365.25)
    total=float(e.equity.iloc[-1]/e.equity.iloc[0]-1); cagr=float((1+total)**(1/years)-1); mdd=float((e.equity/e.equity.cummax()-1).min())
    sharpe=float(dr.mean()/sd*np.sqrt(252)) if sd>0 else np.nan; calmar=float(cagr/abs(mdd)) if mdd<0 else np.nan
    if len(t):
        wins=t.loc[t['return']>0,'return']; losses=t.loc[t['return']<0,'return']; pf=float(wins.sum()/abs(losses.sum())) if len(losses) else math.inf
        wrate=float((t['return']>0).mean()); avg=float(t['return'].mean()); med=float(t['return'].median()); hold=float((pd.to_datetime(t.exit_date)-pd.to_datetime(t.entry_date)).dt.days.mean())
    else: pf=wrate=avg=med=hold=np.nan
    return {'trades':len(t),'win_rate':wrate,'profit_factor':pf,'avg_trade':avg,'median_trade':med,'avg_hold_days':hold,'total_return':total,'cagr':cagr,'sharpe':sharpe,'max_drawdown':mdd,'calmar':calmar,'exposure':float(e.in_position.mean()),'ending_equity':float(e.equity.iloc[-1])}

def buyhold(seg):
    eq=100000*seg.close/seg.close.iloc[0]; dr=eq.pct_change().fillna(0); sd=dr.std(ddof=0); years=max((eq.index[-1]-eq.index[0]).days/365.25,1/365.25)
    total=float(eq.iloc[-1]/eq.iloc[0]-1); cagr=float((1+total)**(1/years)-1); mdd=float((eq/eq.cummax()-1).min()); sharpe=float(dr.mean()/sd*np.sqrt(252)) if sd>0 else np.nan; calmar=float(cagr/abs(mdd)) if mdd<0 else np.nan
    return {'bh_total_return':total,'bh_cagr':cagr,'bh_sharpe':sharpe,'bh_max_drawdown':mdd,'bh_calmar':calmar,'bh_ending_equity':float(eq.iloc[-1])}

def split10(df):
    end=df.index.max().normalize(); start=end-pd.DateOffset(years=10); is_end=start+pd.DateOffset(years=6)-pd.Timedelta(days=1); val_end=start+pd.DateOffset(years=8)-pd.Timedelta(days=1)
    return [('IS',start,is_end),('Validation',is_end+pd.Timedelta(days=1),val_end),('OOS',val_end+pd.Timedelta(days=1),end)]

def main():
    raw=fetch_history(); x=prep(raw); periods=split10(raw)
    is_seg=x.loc[(x.index>=periods[0][1])&(x.index<=periods[0][2])]
    rows=[]
    for wrn,wre,wrx,ccin,ccie,ccix in itertools.product(WR_LOOKBACKS,WR_ENTRIES,WR_EXITS,CCI_LOOKBACKS,CCI_ENTRIES,CCI_EXITS):
        r=backtest(is_seg,wrn,wre,wrx,ccin,ccie,ccix)
        if r and r['trades']>=MIN_IS_TRADES and np.isfinite(r['calmar']):
            rows.append({'wr_lookback':wrn,'wr_entry':wre,'wr_exit':wrx,'cci_lookback':ccin,'cci_entry':ccie,'cci_exit':ccix,**r})
    opt=pd.DataFrame(rows).sort_values(['calmar','sharpe','cagr','profit_factor'],ascending=False)
    opt.to_csv(OUT/'soxl_WR_CCI_joint_IS_optimization.csv',index=False)
    best=opt.iloc[0]
    params={k:(int(best[k]) if 'lookback' in k else float(best[k])) for k in ['wr_lookback','wr_entry','wr_exit','cci_lookback','cci_entry','cci_exit']}
    pd.DataFrame([{**params,'IS_calmar':best.calmar,'IS_sharpe':best.sharpe,'IS_cagr':best.cagr,'IS_profit_factor':best.profit_factor,'IS_trades':best.trades}]).to_csv(OUT/'soxl_WR_CCI_joint_selected_parameters.csv',index=False)
    locked=[]
    for label,a,b in periods:
        seg=x.loc[(x.index>=a)&(x.index<=b)]
        r=backtest(seg,params['wr_lookback'],params['wr_entry'],params['wr_exit'],params['cci_lookback'],params['cci_entry'],params['cci_exit'])
        bh=buyhold(raw.loc[(raw.index>=a)&(raw.index<=b)])
        locked.append({'period':label,'start':seg.index.min().date().isoformat(),'end':seg.index.max().date().isoformat(),**params,**r,**bh,'selected_on':'IS_CALMAR'})
    pd.DataFrame(locked).to_csv(OUT/'soxl_WR_CCI_joint_10y_locked_same_close.csv',index=False)
    print('BEST',params); print(pd.DataFrame(locked).to_string(index=False))

if __name__=='__main__': main()
