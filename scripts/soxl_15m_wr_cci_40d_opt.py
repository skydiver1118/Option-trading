#!/usr/bin/env python3
import os, math, itertools, requests
from pathlib import Path
import pandas as pd
import numpy as np

TOKEN=os.environ['TRADIER_TOKEN']
BASE='https://api.tradier.com/v1'
OUT=Path('data/williams_r'); OUT.mkdir(parents=True, exist_ok=True)
SYMBOL='SOXL'


def fetch_15m():
    end=pd.Timestamp.now(tz='America/New_York').date()
    start=(pd.Timestamp(end)-pd.Timedelta(days=39)).date()
    s=requests.Session(); s.headers.update({'Authorization':f'Bearer {TOKEN}','Accept':'application/json'})
    # Tradier Time & Sales. session_filter=open => regular session; interval=15min.
    r=s.get(f'{BASE}/markets/timesales', params={
        'symbol':SYMBOL,'interval':'15min','start':f'{start} 09:30','end':f'{end} 16:00','session_filter':'open'
    }, timeout=30)
    r.raise_for_status(); payload=r.json(); series=(payload.get('series') or {}).get('data') or []
    if isinstance(series,dict): series=[series]
    df=pd.DataFrame(series)
    if df.empty: raise RuntimeError('No 15m Tradier data returned')
    # Tradier typically returns time, open/high/low/close, volume, vwap.
    time_col='time' if 'time' in df.columns else ('timestamp' if 'timestamp' in df.columns else None)
    if not time_col: raise RuntimeError(f'No time field in columns {df.columns.tolist()}')
    dt=pd.to_datetime(df[time_col], errors='coerce')
    if dt.dt.tz is None:
        dt=dt.dt.tz_localize('America/New_York', nonexistent='shift_forward', ambiguous='NaT')
    else:
        dt=dt.dt.tz_convert('America/New_York')
    df['dt']=dt
    for c in ['open','high','low','close','volume','vwap']:
        if c in df.columns: df[c]=pd.to_numeric(df[c],errors='coerce')
    df=df.dropna(subset=['dt','open','high','low','close']).sort_values('dt').drop_duplicates('dt').set_index('dt')
    # hard regular-hours guard
    t=df.index.time
    df=df[(t>=pd.Timestamp('09:30').time()) & (t<=pd.Timestamp('16:00').time())]
    return df


def add_ind(df,wr_n,cci_n):
    x=df.copy(); hh=x.high.rolling(wr_n).max(); ll=x.low.rolling(wr_n).min(); den=hh-ll
    x['wr']=np.where(den.ne(0), -100*(hh-x.close)/den, np.nan)
    tp=(x.high+x.low+x.close)/3.0; sma=tp.rolling(cci_n).mean(); md=tp.rolling(cci_n).apply(lambda z: np.mean(np.abs(z-np.mean(z))),raw=True)
    x['cci']=np.where(md.ne(0),(tp-sma)/(0.015*md),np.nan); x['prev_high']=x.high.shift(1)
    return x


def bt(df,wr_n,we,wx,cci_n,ce,cx):
    x=add_ind(df,wr_n,cci_n)
    cash=100000.; shares=0.; pos=False; pending=None; ent=None; entdt=None; trades=[]; eq=[]
    pend_signal_dt=None; pend_reason=None
    for dt,r in x.iterrows():
        # execute prior bar signal at current bar open
        if pending=='buy' and not pos:
            p=float(r.open); shares=cash/p; cash=0.; pos=True; ent=p; entdt=dt; pending=None
        elif pending=='sell' and pos:
            p=float(r.open); cash=shares*p; trades.append((entdt,ent,dt,p,p/ent-1,pend_reason)); shares=0.; pos=False; ent=entdt=None; pending=None
        if pd.notna(r.wr) and pd.notna(r.cci):
            if pos and pending is None:
                reasons=[]
                if pd.notna(r.prev_high) and r.close>r.prev_high: reasons.append('close>prev_high')
                if r.wr>wx: reasons.append('wr_exit')
                if r.cci>cx: reasons.append('cci_exit')
                if reasons: pending='sell'; pend_reason='+'.join(reasons)
            elif (not pos) and pending is None and r.wr<we and r.cci<ce:
                pending='buy'; pend_reason='entry'
        equity=cash if not pos else shares*float(r.close); eq.append((dt,equity,pos))
    t=pd.DataFrame(trades,columns=['entry_dt','entry_price','exit_dt','exit_price','return','exit_reason'])
    e=pd.DataFrame(eq,columns=['dt','equity','pos']).set_index('dt')
    rr=e.equity.pct_change().fillna(0); bars_per_year=26*252
    total=float(e.equity.iloc[-1]/e.equity.iloc[0]-1); n=max(len(e)-1,1); ann=float((1+total)**(bars_per_year/n)-1) if total>-1 else -1
    mdd=float((e.equity/e.equity.cummax()-1).min()); sd=rr.std(ddof=0); sharpe=float(rr.mean()/sd*np.sqrt(bars_per_year)) if sd>0 else np.nan; calmar=float(ann/abs(mdd)) if mdd<0 else np.nan
    if len(t):
        wins=t.loc[t['return']>0,'return']; losses=t.loc[t['return']<0,'return']; pf=float(wins.sum()/abs(losses.sum())) if len(losses) else math.inf
        win=float((t['return']>0).mean()); avg=float(t['return'].mean()); med=float(t['return'].median()); hold_bars=float(((pd.to_datetime(t.exit_dt)-pd.to_datetime(t.entry_dt)).dt.total_seconds()/900).mean())
    else: pf=win=avg=med=hold_bars=np.nan
    return {'trades':len(t),'win_rate':win,'profit_factor':pf,'avg_trade':avg,'median_trade':med,'avg_hold_15m_bars':hold_bars,'total_return':total,'annualized_return':ann,'sharpe':sharpe,'max_drawdown':mdd,'calmar':calmar,'exposure':float(e.pos.mean()),'ending_equity':float(e.equity.iloc[-1])}


def main():
    df=fetch_15m(); df.to_csv(OUT/'SOXL_tradier_15m_40d.csv')
    days=pd.Index(sorted(pd.unique(df.index.date)))
    cut=max(1,int(len(days)*0.70)); is_days=days[:cut]; oos_days=days[cut:]
    is_df=df[np.isin(df.index.date,is_days)]; oos_df=df[np.isin(df.index.date,oos_days)]
    # Broad but controlled grid; minimum 8 IS trades because history is short.
    wr_ns=[2,3,4,5,7,10,14,20]; wr_entries=[-95,-90,-85,-80,-75]; wr_exits=[-50,-40,-30,-20,-10]
    cci_ns=[3,5,7,10,14,20]; cci_entries=[-150,-120,-100,-80,-50]; cci_exits=[-50,0,50,100]
    rows=[]
    for p in itertools.product(wr_ns,wr_entries,wr_exits,cci_ns,cci_entries,cci_exits):
        s=bt(is_df,*p)
        if s['trades']<8 or not np.isfinite(s['calmar']): continue
        rows.append({'wr_lookback':p[0],'wr_entry':p[1],'wr_exit':p[2],'cci_lookback':p[3],'cci_entry':p[4],'cci_exit':p[5],**s})
    grid=pd.DataFrame(rows)
    if grid.empty: raise RuntimeError('No parameter sets passed IS trade filter')
    grid=grid.sort_values(['calmar','sharpe','annualized_return','profit_factor'],ascending=False)
    grid.to_csv(OUT/'soxl_15m_40d_IS_grid.csv',index=False)
    b=grid.iloc[0]; p=(int(b.wr_lookback),float(b.wr_entry),float(b.wr_exit),int(b.cci_lookback),float(b.cci_entry),float(b.cci_exit))
    pd.DataFrame([b]).to_csv(OUT/'soxl_15m_40d_selected.csv',index=False)
    outs=[]
    for label,seg in [('IS',is_df),('OOS',oos_df)]:
        s=bt(seg,*p); outs.append({'period':label,'start':seg.index.min().isoformat(),'end':seg.index.max().isoformat(),'trading_days':len(pd.unique(seg.index.date)),'bars':len(seg),'wr_lookback':p[0],'wr_entry':p[1],'wr_exit':p[2],'cci_lookback':p[3],'cci_entry':p[4],'cci_exit':p[5],**s})
    pd.DataFrame(outs).to_csv(OUT/'soxl_15m_40d_locked_IS_OOS.csv',index=False)
    print('Trading days',len(days),'IS',len(is_days),'OOS',len(oos_days),'BEST',p)
    print(pd.DataFrame(outs).to_string(index=False))

if __name__=='__main__': main()
