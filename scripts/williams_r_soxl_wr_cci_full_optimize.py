#!/usr/bin/env python3
import os, math, requests, itertools
from pathlib import Path
import pandas as pd
import numpy as np

TOKEN=os.environ['TRADIER_TOKEN']
BASE='https://api.tradier.com/v1'
OUT=Path('data/williams_r'); OUT.mkdir(parents=True, exist_ok=True)
SYMBOL='SOXL'


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


def indicators(df,wr_n,cci_n):
    x=df.copy()
    hh=x.high.rolling(wr_n).max(); ll=x.low.rolling(wr_n).min(); rng=hh-ll
    x['wr']=np.where(rng.ne(0),-100*(hh-x.close)/rng,np.nan)
    tp=(x.high+x.low+x.close)/3.0
    sma=tp.rolling(cci_n).mean()
    md=tp.rolling(cci_n).apply(lambda z: np.mean(np.abs(z-np.mean(z))),raw=True)
    x['cci']=np.where(md.ne(0),(tp-sma)/(0.015*md),np.nan)
    x['prev_high']=x.high.shift(1)
    return x


def backtest(df,wr_n,wr_entry,wr_exit,cci_n,cci_entry,cci_exit):
    x=indicators(df,wr_n,cci_n)
    cash=100000.0; shares=0.0; pos=False; trades=[]; eq=[]; ent=None; entdt=None
    for dt,row in x.iterrows():
        wr=row.wr; cci=row.cci
        if pd.notna(wr) and pd.notna(cci):
            entry=(wr<wr_entry) and (cci<cci_entry)
            exit1=pd.notna(row.prev_high) and row.close>row.prev_high
            exit2=wr>wr_exit
            exit3=cci>cci_exit
            if pos and (exit1 or exit2 or exit3):
                p=float(row.close); cash=shares*p
                trades.append((entdt,ent,dt,p,p/ent-1))
                shares=0.0; pos=False; ent=entdt=None
            elif (not pos) and entry:
                p=float(row.close); shares=cash/p; cash=0.0; pos=True; ent=p; entdt=dt
        equity=cash if not pos else shares*float(row.close)
        eq.append((dt,equity,pos))
    t=pd.DataFrame(trades,columns=['entry_date','entry_price','exit_date','exit_price','return'])
    e=pd.DataFrame(eq,columns=['date','equity','in_position']).set_index('date')
    ret=e.equity.pct_change().fillna(0); years=max((e.index[-1]-e.index[0]).days/365.25,1/365.25)
    total=float(e.equity.iloc[-1]/e.equity.iloc[0]-1); cagr=float((1+total)**(1/years)-1)
    peak=e.equity.cummax(); mdd=float((e.equity/peak-1).min())
    sd=ret.std(ddof=0); sharpe=float(ret.mean()/sd*np.sqrt(252)) if sd>0 else np.nan; calmar=float(cagr/abs(mdd)) if mdd<0 else np.nan
    if len(t):
        wins=t.loc[t['return']>0,'return']; losses=t.loc[t['return']<0,'return']
        pf=float(wins.sum()/abs(losses.sum())) if len(losses) else math.inf
        wrate=float((t['return']>0).mean()); avg=float(t['return'].mean()); med=float(t['return'].median())
        hold=float((pd.to_datetime(t.exit_date)-pd.to_datetime(t.entry_date)).dt.days.mean())
    else: pf=wrate=avg=med=hold=np.nan
    return {'trades':len(t),'win_rate':wrate,'profit_factor':pf,'avg_trade':avg,'median_trade':med,'avg_hold_days':hold,'total_return':total,'cagr':cagr,'sharpe':sharpe,'max_drawdown':mdd,'calmar':calmar,'exposure':float(e.in_position.mean()),'ending_equity':float(e.equity.iloc[-1])}


def buyhold(df):
    c=df.close.astype(float); eq=100000*c/c.iloc[0]; r=eq.pct_change().fillna(0); years=max((eq.index[-1]-eq.index[0]).days/365.25,1/365.25)
    total=float(eq.iloc[-1]/eq.iloc[0]-1); cagr=float((1+total)**(1/years)-1); mdd=float((eq/eq.cummax()-1).min()); sd=r.std(ddof=0); sharpe=float(r.mean()/sd*np.sqrt(252)) if sd>0 else np.nan; calmar=float(cagr/abs(mdd)) if mdd<0 else np.nan
    return {'bh_total_return':total,'bh_cagr':cagr,'bh_sharpe':sharpe,'bh_max_drawdown':mdd,'bh_calmar':calmar,'bh_ending_equity':float(eq.iloc[-1])}


def split10(df):
    end=df.index.max().normalize(); start=end-pd.DateOffset(years=10)
    is_end=start+pd.DateOffset(years=6)-pd.Timedelta(days=1); val_end=start+pd.DateOffset(years=8)-pd.Timedelta(days=1)
    return [('IS',start,is_end),('Validation',is_end+pd.Timedelta(days=1),val_end),('OOS',val_end+pd.Timedelta(days=1),end)]


def main():
    df=fetch_history(); periods=split10(df)
    is_df=df.loc[(df.index>=periods[0][1])&(df.index<=periods[0][2])]
    wr_ns=[2,3,4,5,7,10]
    wr_entries=[-95,-90,-85,-80]
    wr_exits=[-40,-30,-20,-10]
    cci_ns=[3,5,7,10,14]
    cci_entries=[-150,-100,-80,-50]
    cci_exits=[-50,0,50,100]
    rows=[]
    for pars in itertools.product(wr_ns,wr_entries,wr_exits,cci_ns,cci_entries,cci_exits):
        s=backtest(is_df,*pars)
        if s['trades']<40: continue
        row={'wr_lookback':pars[0],'wr_entry':pars[1],'wr_exit':pars[2],'cci_lookback':pars[3],'cci_entry':pars[4],'cci_exit':pars[5],**s}
        rows.append(row)
    grid=pd.DataFrame(rows)
    grid=grid.sort_values(['calmar','sharpe','cagr','profit_factor'],ascending=False)
    grid.to_csv(OUT/'soxl_WR_CCI_full_IS_grid.csv',index=False)
    best=grid.iloc[0]
    p=(int(best.wr_lookback),float(best.wr_entry),float(best.wr_exit),int(best.cci_lookback),float(best.cci_entry),float(best.cci_exit))
    pd.DataFrame([{'wr_lookback':p[0],'wr_entry':p[1],'wr_exit':p[2],'cci_lookback':p[3],'cci_entry':p[4],'cci_exit':p[5],'IS_calmar':best.calmar,'IS_sharpe':best.sharpe,'IS_cagr':best.cagr,'IS_profit_factor':best.profit_factor,'IS_trades':best.trades}]).to_csv(OUT/'soxl_WR_CCI_full_selected_parameters.csv',index=False)
    outs=[]
    for label,a,b in periods:
        seg=df.loc[(df.index>=a)&(df.index<=b)]
        s=backtest(seg,*p); bh=buyhold(seg)
        outs.append({'period':label,'start':seg.index.min().date().isoformat(),'end':seg.index.max().date().isoformat(),'wr_lookback':p[0],'wr_entry':p[1],'wr_exit':p[2],'cci_lookback':p[3],'cci_entry':p[4],'cci_exit':p[5],**s,**bh,'selected_on':'IS_CALMAR'})
    pd.DataFrame(outs).to_csv(OUT/'soxl_WR_CCI_full_locked_same_close.csv',index=False)
    print('BEST',p)
    print(pd.DataFrame(outs).to_string(index=False))

if __name__=='__main__': main()
