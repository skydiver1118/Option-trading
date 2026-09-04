#!/usr/bin/env python3
import os, math, requests, itertools
from pathlib import Path
import pandas as pd
import numpy as np
TOKEN=os.environ['TRADIER_TOKEN']; BASE='https://api.tradier.com/v1'; OUT=Path('data/williams_r'); OUT.mkdir(parents=True,exist_ok=True)

def fetch():
    end=pd.Timestamp.today().date(); start=(pd.Timestamp(end)-pd.DateOffset(years=10,days=10)).date()
    s=requests.Session(); s.headers.update({'Authorization':f'Bearer {TOKEN}','Accept':'application/json'})
    rows=[]; cur=pd.Timestamp(start); end_ts=pd.Timestamp(end)
    while cur<=end_ts:
        stop=min(end_ts,cur+pd.DateOffset(years=8)-pd.Timedelta(days=1))
        r=s.get(f'{BASE}/markets/history',params={'symbol':'SOXL','interval':'daily','start':cur.date().isoformat(),'end':stop.date().isoformat()},timeout=30); r.raise_for_status()
        d=(r.json().get('history') or {}).get('day') or []; d=[d] if isinstance(d,dict) else d; rows.extend(d); cur=stop+pd.Timedelta(days=1)
    df=pd.DataFrame(rows); df['date']=pd.to_datetime(df['date'])
    for c in ['open','high','low','close','volume']: df[c]=pd.to_numeric(df[c],errors='coerce')
    return df.drop_duplicates('date').sort_values('date').set_index('date')

def add_ind(df,wr_n,cci_n):
    x=df.copy(); hh=x.high.rolling(wr_n).max(); ll=x.low.rolling(wr_n).min(); x['wr']=-100*(hh-x.close)/(hh-ll)
    tp=(x.high+x.low+x.close)/3; sma=tp.rolling(cci_n).mean(); md=tp.rolling(cci_n).apply(lambda z: np.mean(np.abs(z-np.mean(z))),raw=True); x['cci']=(tp-sma)/(0.015*md); x['prev_high']=x.high.shift(1); return x

def bt(x,we,wx,ce,cx):
    cash=100000.; shares=0.; pos=False; ent=None; entdt=None; trs=[]; eq=[]
    for dt,r in x.iterrows():
        if pd.notna(r.wr) and pd.notna(r.cci):
            if pos and ((r.close>r.prev_high) or (r.wr>wx) or (r.cci>cx)):
                p=float(r.close); cash=shares*p; trs.append((entdt,dt,p/ent-1)); shares=0; pos=False; ent=entdt=None
            elif (not pos) and (r.wr<we) and (r.cci<ce):
                p=float(r.close); shares=cash/p; cash=0; pos=True; ent=p; entdt=dt
        eq.append((dt,cash if not pos else shares*float(r.close),pos))
    t=pd.DataFrame(trs,columns=['entry','exit','ret']); e=pd.DataFrame(eq,columns=['date','equity','pos']).set_index('date')
    rr=e.equity.pct_change().fillna(0); years=max((e.index[-1]-e.index[0]).days/365.25,1/365.25); total=e.equity.iloc[-1]/e.equity.iloc[0]-1; cagr=(1+total)**(1/years)-1; mdd=(e.equity/e.equity.cummax()-1).min(); sd=rr.std(ddof=0); sh=rr.mean()/sd*np.sqrt(252) if sd>0 else np.nan; cal=cagr/abs(mdd) if mdd<0 else np.nan
    if len(t):
        wins=t.loc[t.ret>0,'ret']; losses=t.loc[t.ret<0,'ret']; pf=wins.sum()/abs(losses.sum()) if len(losses) else np.inf; wr=(t.ret>0).mean(); avg=t.ret.mean(); med=t.ret.median(); hold=(pd.to_datetime(t.exit)-pd.to_datetime(t.entry)).dt.days.mean()
    else: pf=wr=avg=med=hold=np.nan
    return {'trades':len(t),'win_rate':wr,'profit_factor':pf,'avg_trade':avg,'median_trade':med,'avg_hold_days':hold,'total_return':float(total),'cagr':float(cagr),'sharpe':float(sh),'max_drawdown':float(mdd),'calmar':float(cal),'exposure':float(e.pos.mean()),'ending_equity':float(e.equity.iloc[-1])}

def bh(seg):
    c=seg.close.astype(float); eq=100000*c/c.iloc[0]; rr=eq.pct_change().fillna(0); years=max((eq.index[-1]-eq.index[0]).days/365.25,1/365.25); total=eq.iloc[-1]/eq.iloc[0]-1; cagr=(1+total)**(1/years)-1; mdd=(eq/eq.cummax()-1).min(); sd=rr.std(ddof=0); sh=rr.mean()/sd*np.sqrt(252); return {'bh_total_return':float(total),'bh_cagr':float(cagr),'bh_sharpe':float(sh),'bh_max_drawdown':float(mdd),'bh_calmar':float(cagr/abs(mdd))}

def main():
    df=fetch(); end=df.index.max().normalize(); start=end-pd.DateOffset(years=10); is_end=start+pd.DateOffset(years=6)-pd.Timedelta(days=1); val_end=start+pd.DateOffset(years=8)-pd.Timedelta(days=1)
    periods=[('IS',start,is_end),('Validation',is_end+pd.Timedelta(days=1),val_end),('OOS',val_end+pd.Timedelta(days=1),end)]
    is_df=df.loc[(df.index>=start)&(df.index<=is_end)]
    wr_ns=[2,3,5,7]; wr_entries=[-95,-90,-85]; wr_exits=[-40,-30,-20]; cci_ns=[3,5,7,10]; cci_entries=[-120,-80,-50]; cci_exits=[-20,0,50]
    cache={(wn,cn):add_ind(is_df,wn,cn) for wn in wr_ns for cn in cci_ns}; rows=[]
    for wn,we,wx,cn,ce,cx in itertools.product(wr_ns,wr_entries,wr_exits,cci_ns,cci_entries,cci_exits):
        s=bt(cache[(wn,cn)],we,wx,ce,cx)
        if s['trades']>=40: rows.append({'wr_lookback':wn,'wr_entry':we,'wr_exit':wx,'cci_lookback':cn,'cci_entry':ce,'cci_exit':cx,**s})
    grid=pd.DataFrame(rows).sort_values(['calmar','sharpe','cagr','profit_factor'],ascending=False); grid.to_csv(OUT/'soxl_WR_CCI_fast_IS_grid.csv',index=False)
    b=grid.iloc[0]; p=(int(b.wr_lookback),float(b.wr_entry),float(b.wr_exit),int(b.cci_lookback),float(b.cci_entry),float(b.cci_exit))
    pd.DataFrame([b]).to_csv(OUT/'soxl_WR_CCI_fast_selected.csv',index=False)
    outs=[]
    for label,a,z in periods:
        seg=df.loc[(df.index>=a)&(df.index<=z)]; x=add_ind(seg,p[0],p[3]); s=bt(x,p[1],p[2],p[4],p[5]); outs.append({'period':label,'start':seg.index.min().date().isoformat(),'end':seg.index.max().date().isoformat(),'wr_lookback':p[0],'wr_entry':p[1],'wr_exit':p[2],'cci_lookback':p[3],'cci_entry':p[4],'cci_exit':p[5],**s,**bh(seg)})
    pd.DataFrame(outs).to_csv(OUT/'soxl_WR_CCI_fast_locked.csv',index=False); print('BEST',p); print(pd.DataFrame(outs).to_string(index=False))
if __name__=='__main__': main()
