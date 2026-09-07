#!/usr/bin/env python3
"""Read-only TCAR historical audit. Never imports a bot or calls account/order APIs."""
from __future__ import annotations
import ast
import hashlib
import itertools
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / 'research/tcar_skill_audit_20260906/spec.json'
OUT = ROOT / 'research/tcar_skill_audit_20260906/results'
CAPITAL = 100000.0
PARTS = {'IS':('2016-09-06','2022-09-02'),
         'VALIDATION':('2022-09-06','2024-09-03'),
         'OOS_REUSED':('2024-09-04','2026-09-04')}
START, END, WARMUP = '2016-09-06','2026-09-04','2015-09-06'
CHECKS = []
COLS = ['wr','cci','adx20','prev_high']


def check(name, passed, detail=''):
    CHECKS.append({'check':name,'passed':bool(passed),'detail':str(detail)})
    if not passed:
        raise AssertionError(name + ': ' + str(detail))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def clean(obj):
    if isinstance(obj,dict): return {str(k):clean(v) for k,v in obj.items()}
    if isinstance(obj,(list,tuple)): return [clean(v) for v in obj]
    if isinstance(obj,(np.integer,)): return int(obj)
    if isinstance(obj,(np.bool_,)): return bool(obj)
    if isinstance(obj,(float,np.floating)):
        return float(obj) if math.isfinite(obj) else None
    if isinstance(obj,(pd.Timestamp,datetime)): return obj.isoformat()
    return obj


def dump(name,obj):
    (OUT/name).write_text(json.dumps(clean(obj),indent=2,allow_nan=False)+'\n',encoding='utf-8')


def fetch_tradier():
    import requests
    token = os.environ.get('TRADIER_TOKEN','').strip()
    if not token: raise RuntimeError('TRADIER_TOKEN_MISSING')
    cur, end = pd.Timestamp(WARMUP), pd.Timestamp(END)
    rows, provenance = [], []
    while cur <= end:
        stop = min(end,cur+pd.DateOffset(years=8)-pd.Timedelta(days=1))
        params = {'symbol':'SOXL','interval':'daily','start':cur.date().isoformat(),'end':stop.date().isoformat()}
        r = requests.get('https://api.tradier.com/v1/markets/history',params=params,
            headers={'Authorization':'Bearer '+token,'Accept':'application/json'},timeout=40,allow_redirects=False)
        if r.status_code != 200: raise RuntimeError('TRADIER_HISTORY_HTTP_'+str(r.status_code))
        payload=r.json()
        if payload.get('errors') or payload.get('fault'): raise RuntimeError('TRADIER_HISTORY_ERROR')
        data=(payload.get('history') or {}).get('day')
        if not isinstance(data,(list,dict)): raise RuntimeError('TRADIER_HISTORY_SCHEMA')
        rows.extend([data] if isinstance(data,dict) else data)
        body=r.content
        name=f'tradier_raw_{params["start"]}_{params["end"]}.json'
        (OUT/name).write_bytes(body)
        provenance.append({'endpoint':'https://api.tradier.com/v1/markets/history','method':'GET',
            'parameters':params,'response_sha256':sha(body),'raw_file':name})
        cur=stop+pd.Timedelta(days=1)
    frame=pd.DataFrame(rows)
    frame['date']=pd.to_datetime(frame['date'])
    frame=frame.set_index('date').sort_index()
    for c in ['open','high','low','close','volume']:
        frame[c]=pd.to_numeric(frame[c],errors='coerce')
    return frame[['open','high','low','close','volume']],provenance


def validate_data(x,label,calendar=True):
    check(label+' unique dates',not x.index.has_duplicates)
    check(label+' sorted dates',x.index.is_monotonic_increasing)
    a=x[['open','high','low','close']].to_numpy(float)
    check(label+' finite positive OHLC',np.isfinite(a).all() and (a>0).all())
    tol=1e-6
    check(label+' OHLC order',bool(((x.high+tol>=x[['open','close','low']].max(axis=1)) &
          (x.low-tol<=x[['open','close']].min(axis=1))).all()))
    eval_x=x.loc[START:END]
    check(label+' evaluation endpoints',str(eval_x.index[0].date())==START and str(eval_x.index[-1].date())==END)
    if calendar:
        import exchange_calendars as xc
        sessions=xc.get_calendar('XNYS',start=WARMUP,end=END).sessions_in_range(START,END)
        if sessions.tz is not None: sessions=sessions.tz_localize(None)
        missing=sessions.difference(eval_x.index)
        extra=eval_x.index.difference(sessions)
        check(label+' NYSE calendar completeness',not len(missing) and not len(extra),
              f'missing={list(missing)}; extra={list(extra)}; sessions={len(eval_x)}')


def reference_functions():
    path=ROOT/'scripts/soxl_daily_tcar_yahoo_vs_tradier_10y.py'
    text=path.read_text(encoding='utf-8')
    blob=hashlib.sha1(b'blob '+str(len(text.encode())).encode()+b'\0'+text.encode()).hexdigest()
    check('Frozen reference source unchanged',blob=='1e1c5e72c6e9f2e04870e9b16ce68d4acf6c8739',blob)
    nodes=[n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name in ('prep','run')]
    ns={'pd':pd,'np':np,'math':math,'START':START,'END':END}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),ns)
    return ns['prep'],ns['run'],sha(text.encode())


def independent_indicators(x):
    h,l,c=[x[k].to_numpy(float) for k in ('high','low','close')]
    n=len(x); wr=np.full(n,np.nan); cci=wr.copy(); adx=wr.copy(); dx=wr.copy()
    tp=(h+l+c)/3
    tr=np.zeros(n); plus=np.zeros(n); minus=np.zeros(n)
    for i in range(n):
        tr[i]=h[i]-l[i] if i==0 else max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
        if i:
            up,dn=h[i]-h[i-1],l[i-1]-l[i]
            plus[i]=up if up>dn and up>0 else 0
            minus[i]=dn if dn>up and dn>0 else 0
        if i>=1:
            hi=max(h[i-1:i+1]); lo=min(l[i-1:i+1])
            if hi>lo: wr[i]=-100*(hi-c[i])/(hi-lo)
        if i>=4:
            a=tp[i-4:i+1]; m=sum(a)/5; dev=sum(abs(a-m))/5
            if dev>0: cci[i]=(tp[i]-m)/(.015*dev)
        if i>=19:
            atr=sum(tr[i-19:i+1])/20
            pdm=sum(plus[i-19:i+1])/20; mdm=sum(minus[i-19:i+1])/20
            if atr>0 and pdm+mdm>0:
                dx[i]=100*abs(pdm-mdm)/(pdm+mdm)
        if i>=38 and np.isfinite(dx[i-19:i+1]).all(): adx[i]=sum(dx[i-19:i+1])/20
    return pd.DataFrame({'wr':wr,'cci':cci,'adx20':adx,'prev_high':np.r_[np.nan,h[:-1]]},index=x.index)


def metrics(e,trades,initial=CAPITAL):
    eq=e.equity.to_numpy(float)
    r=np.r_[eq[0]/initial-1,eq[1:]/eq[:-1]-1]
    years=(e.index[-1]-e.index[0]).days/365.25
    total=eq[-1]/initial-1
    cagr=(1+total)**(1/years)-1 if years else np.nan
    peak=np.maximum.accumulate(np.r_[initial,eq])[1:]
    dd=eq/peak-1
    sd=np.std(r,ddof=1); downside=np.sqrt(np.mean(np.minimum(r,0)**2))
    t=pd.DataFrame(trades)
    if len(t):
        rr=t.net_return.to_numpy(float); pnl=t.pnl.to_numpy(float)
        pf=rr[rr>0].sum()/abs(rr[rr<0].sum()) if (rr<0).any() else np.inf
        pf_dollar=pnl[pnl>0].sum()/abs(pnl[pnl<0].sum()) if (pnl<0).any() else np.inf
    else: rr=np.array([]); pf=pf_dollar=np.nan
    return {'start':str(e.index[0].date()),'end':str(e.index[-1].date()),'sessions':len(e),
        'total_return':total,'cagr':cagr,'sharpe':np.mean(r)/sd*np.sqrt(252) if sd else np.nan,
        'sortino':np.mean(r)/downside*np.sqrt(252) if downside else np.nan,
        'annual_volatility':sd*np.sqrt(252),'max_drawdown':dd.min(),
        'calmar':cagr/abs(dd.min()) if dd.min()<0 else np.nan,
        'trades':len(t),'win_rate':float(np.mean(rr>0)) if len(t) else np.nan,
        'profit_factor_return':pf,'profit_factor_dollar':pf_dollar,
        'avg_trade':float(np.mean(rr)) if len(t) else np.nan,
        'median_trade':float(np.median(rr)) if len(t) else np.nan,
        'avg_holding_calendar_days':float(t.holding_calendar_days.mean()) if len(t) else np.nan,
        'avg_holding_sessions':float(t.holding_sessions.mean()) if len(t) else np.nan,
        'close_position_fraction':float(e.position.mean()),
        'exposed_return_days':float(((e.position>0)|(e.position.shift(1).fillna(0)>0)).mean()),
        'ending_equity':eq[-1],'open_position_at_end':bool(e.position.iloc[-1]),
        'sum_cost_dollars':float(e.cost.sum()),'worst_day':float(r.min())}


def simulate(x,bps=0,wr_entry=-90,cci_entry=-80,adx_entry=15,skip_signal=None):
    fee=bps/10000; cash=CAPITAL; qty=0.; pending=None; entry=None
    trades=[]; executions=[]; daily=[]
    for i,(dt,row) in enumerate(x.iterrows()):
        cost=0.; action=''
        if pending:
            side,signal=pending; op=float(row.open)
            if side=='buy':
                px=op*(1+fee); starting=cash; qty=cash/px; cash=0
                entry={'entry_signal_date':str(signal.date()),'entry_date':str(dt.date()),
                       'entry_index':i,'entry_reference_open':op,'entry_price':px,'quantity':qty,'entry_capital':starting}
                cost=qty*(px-op); action='buy'
            else:
                px=op*(1-fee); cash=qty*px; cost=qty*(op-px); action='sell'
                trades.append({**entry,'exit_signal_date':str(signal.date()),'exit_date':str(dt.date()),
                    'exit_reference_open':op,'exit_price':px,'net_return':px/entry['entry_price']-1,
                    'pnl':cash-entry['entry_capital'],'holding_calendar_days':(dt-pd.Timestamp(entry['entry_date'])).days,
                    'holding_sessions':i-entry['entry_index']})
                qty=0.; entry=None
            executions.append({'date':str(dt.date()),'signal_date':str(signal.date()),'action':action,'fill':px,'reference_open':op,'cost':cost})
            pending=None
        valid=np.isfinite([row.wr,row.cci,row.adx20,row.prev_high]).all()
        if valid:
            if qty>0 and (row.close>row.prev_high or row.wr>-30): pending=('sell',dt)
            elif qty==0 and row.wr<wr_entry and row.cci<cci_entry and row.adx20>=adx_entry:
                if skip_signal!=str(dt.date()): pending=('buy',dt)
        daily.append({'date':dt,'equity':cash+qty*float(row.close),'cash':cash,'quantity':qty,
            'position':int(qty>0),'execution':action,'cost':cost})
    e=pd.DataFrame(daily).set_index('date')
    return metrics(e,trades),trades,e,executions,entry


def independent_equity(x,executions,bps):
    fee=bps/10000; actions={v['date']:v['action'] for v in executions}; pos=False; last=None; returns=[]
    for dt,row in x.iterrows():
        action=actions.get(str(dt.date()))
        if action=='buy': r=row.close/(row.open*(1+fee))-1; pos=True
        elif action=='sell': r=row.open*(1-fee)/last-1; pos=False
        elif pos: r=row.close/last-1
        else: r=0.
        returns.append(r); last=row.close
    return CAPITAL*np.cumprod(1+np.asarray(returns))


def buy_hold(raw,bps):
    px=float(raw.open.iloc[0])*(1+bps/10000)
    qty=CAPITAL/px
    e=pd.DataFrame({'equity':qty*raw.close,'position':1,'cost':0.},index=raw.index)
    e.iloc[0,e.columns.get_loc('cost')]=qty*(px-float(raw.open.iloc[0]))
    return metrics(e,[]),e


def tests(raw,prep,full):
    indep=independent_indicators(raw)
    check('Independent numpy indicator arithmetic',np.allclose(prep(raw)[COLS].to_numpy(),indep.to_numpy(),
        rtol=1e-9,atol=1e-8,equal_nan=True))
    for cut in (60,260,800,1508,2000,2400):
        a=prep(raw.iloc[:cut])[COLS]; b=prep(raw)[COLS].iloc[:cut]
        check('Feature prefix no-lookahead '+str(cut),np.allclose(a,b,equal_nan=True,atol=1e-9))
    for cut in (200,800,1700,2300):
        a=simulate(full.iloc[:cut],10)[2].equity.to_numpy()
        b=simulate(full,10)[2].equity.iloc[:cut].to_numpy()
        check('Equity prefix no-lookahead '+str(cut),np.allclose(a,b,rtol=1e-12))
    changed=raw.copy()
    sel=changed.index<pd.Timestamp(PARTS['OOS_REUSED'][0])
    changed.loc[sel,['open','high','low','close']]*=np.linspace(0.2,4,int(sel.sum()))[:,None]
    a=prep(raw.loc[PARTS['OOS_REUSED'][0]:PARTS['OOS_REUSED'][1]])[COLS]
    b=prep(changed.loc[PARTS['OOS_REUSED'][0]:PARTS['OOS_REUSED'][1]])[COLS]
    check('OOS partition isolation under earlier-price mutation',np.allclose(a,b,equal_nan=True))
    syn=pd.DataFrame({'open':[9.,20.,19.,15.],'high':[11.,21.,20.,16.],
        'low':[8.,17.,18.,14.],'close':[9.,18.,19.,15.],
        'wr':[-95.,-70.,-20.,-50.],'cci':[-100.,-50.,20.,0.],
        'adx20':[20.,20.,20.,20.],'prev_high':[12.,11.,21.,20.]},
        index=pd.bdate_range('2020-01-06',periods=4))
    m,t,_,_,_=simulate(syn)
    check('Synthetic next-open entry and exit with gap',len(t)==1 and t[0]['entry_price']==20 and t[0]['exit_price']==19,
          'Price exit fires on entry close 18>prior high11; exit is next open19, not signal close18')
    syn['prev_high']=[22.,22.,22.,22.]
    m,t,_,_,_=simulate(syn)
    check('Synthetic overnight exit gap correctly charged',len(t)==1 and abs(t[0]['net_return']+.25)<1e-12)
    check('Synthetic trade dates ordered',t[0]['entry_signal_date']<t[0]['entry_date']<=t[0]['exit_signal_date']<t[0]['exit_date'])


def bootstrap(e):
    v=e.equity.to_numpy(); r=np.r_[v[0]/CAPITAL-1,v[1:]/v[:-1]-1]; n=len(r)
    rng=np.random.default_rng(20260906); years=(e.index[-1]-e.index[0]).days/365.25
    stats=[]
    for _ in range(2000):
        starts=rng.integers(0,n,size=math.ceil(n/20))
        ix=(starts[:,None]+np.arange(20)[None,:])%n
        z=r[ix.ravel()[:n]]; w=np.cumprod(1+z); dd=w/np.maximum.accumulate(np.r_[1.,w])[1:]-1
        sd=z.std(ddof=1)
        stats.append([w[-1]-1,w[-1]**(1/years)-1,z.mean()/sd*np.sqrt(252) if sd else np.nan,dd.min()])
    a=np.asarray(stats)
    result={k:{'p2_5':float(np.nanpercentile(a[:,i],2.5)),'median':float(np.nanpercentile(a[:,i],50)),
                'p97_5':float(np.nanpercentile(a[:,i],97.5))} for i,k in enumerate(['total_return','cagr','sharpe','max_drawdown'])}
    result['design']='Circular moving-block bootstrap of historical daily strategy returns; 20-session blocks, 2000 resamples. Conditional resampling uncertainty, NOT a future-return forecast or correction for prior strategy selection.'
    result['fraction_resamples_positive']=float((a[:,0]>0).mean())
    return result


def annual(e,initial=CAPITAL):
    q=e.equity; r=q.pct_change(); r.iloc[0]=q.iloc[0]/initial-1
    return [{'year':int(y),'return':float(np.prod(1+g)-1)} for y,g in r.groupby(r.index.year)]


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    spec=json.loads(SPEC_PATH.read_text())
    check('Specification is fixed no OOS optimization',spec['experiment_id']=='TCAR_SKILL_AUDIT_20260906_V1')
    prep,legacy,reference_sha=reference_functions()
    raw,provenance=fetch_tradier()
    validate_data(raw,'Tradier')
    raw.to_csv(OUT/'tradier_daily_snapshot.csv',float_format='%.12g')
    full_raw=raw.loc[START:END]
    full=prep(full_raw).replace([np.inf,-np.inf],np.nan)
    features=prep(raw).replace([np.inf,-np.inf],np.nan)
    tests(raw,prep,full)
    dataset_days=set(full_raw.index)
    partition_days=[d for a,b in PARTS.values() for d in full_raw.loc[a:b].index]
    check('Partition dates exhaustive and nonoverlapping',len(partition_days)==len(set(partition_days)) and set(partition_days)==dataset_days)
    rows=[]; annual_rows=[]; alltrades=[]; oos_e=None
    for label,(a,b) in {**PARTS,'FULL_CONTINUOUS':(START,END)}.items():
        x=prep(raw.loc[a:b]).replace([np.inf,-np.inf],np.nan)
        check(label+' local ADX warmup blocked',x.adx20.iloc[:38].isna().all())
        for cost in (0,5,10,25,50):
            m,t,e,execution,open_trade=simulate(x,cost)
            m.update({'period':label,'mode':'LOCAL_WARMUP','cost_bps_per_side':cost,'source':'Tradier'})
            rows.append(m)
            check(label+f' {cost}bp independent equity reconstruction',np.allclose(e.equity,independent_equity(x,execution,cost),rtol=1e-10,atol=1e-6))
            if cost in (0,10):
                pd.DataFrame(t).to_csv(OUT/f'{label.lower()}_{cost}bp_trades.csv',index=False,float_format='%.12g')
                e.to_csv(OUT/f'{label.lower()}_{cost}bp_equity.csv',float_format='%.12g')
                for tr in t: alltrades.append({'period':label,'mode':'LOCAL_WARMUP','cost_bps_per_side':cost,**tr})
                for ar in annual(e): annual_rows.append({'period':label,'cost_bps_per_side':cost,**ar})
            if label=='OOS_REUSED' and cost==10: oos_e=e
            if cost==10:
                dump(f'{label.lower()}_end_state.json',{'open_trade':open_trade,'ending_qty':e.quantity.iloc[-1]})
        h,he=buy_hold(raw.loc[a:b],10)
        h.update({'period':label,'mode':'BUY_HOLD','cost_bps_per_side':10,'source':'Tradier'})
        rows.append(h)
        he.to_csv(OUT/f'{label.lower()}_buy_hold_equity.csv',float_format='%.12g')
    legacy_stats,legacy_t,legacy_e=legacy(features,'Tradier')
    pd.DataFrame(legacy_t).to_csv(OUT/'legacy_continuous_gross_trades.csv',index=False,float_format='%.12g')
    lm,lt,le,lex,_=simulate(features.loc[START:END],0)
    check('Independent ledger reproduces legacy equity',np.allclose(le.equity,legacy_e.equity,rtol=1e-10))
    legacy_stats['source_note']='Fresh Tradier API snapshot; same frozen source function. Legacy external warmup is NOT primary partition-local evaluation.'
    for cost in (0,10):
        cm,ct,ce,cx,_=simulate(features.loc[START:END],cost)
        cm.update({'period':'FULL_CONTINUOUS','mode':'LEGACY_EXTERNAL_WARMUP','cost_bps_per_side':cost,'source':'Tradier'})
        rows.append(cm)
        if cost==10:
            ce.to_csv(OUT/'legacy_continuous_10bp_equity.csv',float_format='%.12g')
            pd.DataFrame(ct).to_csv(OUT/'legacy_continuous_10bp_trades.csv',index=False,float_format='%.12g')
        for label,(a,b) in PARTS.items():
            seg=ce.loc[a:b]; startloc=ce.index.get_loc(seg.index[0]); initial=CAPITAL if startloc==0 else ce.equity.iloc[startloc-1]
            ts=[v for v in ct if a<=v['exit_date']<=b]
            mm=metrics(seg,ts,initial)
            mm.update({'period':label,'mode':'CONTINUOUS_ATTRIBUTION','cost_bps_per_side':cost,'source':'Tradier'})
            rows.append(mm)
        for label,(a,b) in PARTS.items():
            mm,_,_,_,_=simulate(features.loc[a:b],cost)
            mm.update({'period':label,'mode':'STATE_RESET_ONLY_EXTERNAL_FEATURES','cost_bps_per_side':cost,'source':'Tradier'})
            rows.append(mm)
    is_frame=prep(raw.loc[PARTS['IS'][0]:PARTS['IS'][1]])
    neighbors=[]
    for w,c,a in itertools.product([-95,-90,-85],[-100,-80,-60],[10,15,20]):
        m,_,_,_,_=simulate(is_frame,10,w,c,a)
        neighbors.append({'wr_entry':w,'cci_entry':c,'adx_entry':a,**m})
    pd.DataFrame(neighbors).to_csv(OUT/'is_only_neighborhood.csv',index=False,float_format='%.12g')
    yearly=[]
    for year in range(2021,2026):
        a=f'{year}-09-06'; b=f'{year+1}-09-05' if year<2025 else END
        x=prep(raw.loc[a:b]); m,_,_,_,_=simulate(x,10)
        yearly.append({'block':'REUSED_ONE_YEAR_LOCAL_WARMUP','year_start':year,**m})
    pd.DataFrame(yearly).to_csv(OUT/'one_year_robustness.csv',index=False,float_format='%.12g')
    concentration=[]
    for label,(a,b) in {'FULL_CONTINUOUS':(START,END),'OOS_REUSED':PARTS['OOS_REUSED']}.items():
        x=prep(raw.loc[a:b]); m,t,_,_,_=simulate(x,10)
        best=max(t,key=lambda z:z['net_return'])
        omit,_,_,_,_=simulate(x,10,skip_signal=best['entry_signal_date'])
        rr=np.array([z['net_return'] for z in t]); logs=np.log1p(rr)
        concentration.append({'period':label,'best_trade_signal':best['entry_signal_date'],'best_trade_return':best['net_return'],
            'top3_share_positive_log_gains':float(np.sort(logs[logs>0])[-3:].sum()/logs[logs>0].sum()),
            'baseline_cagr':m['cagr'],'omit_best_trade_cagr':omit['cagr'],
            'baseline_sharpe':m['sharpe'],'omit_best_trade_sharpe':omit['sharpe']})
    yahoo_report={}
    try:
        import yfinance as yf
        y=yf.download('SOXL',start=WARMUP,end='2026-09-05',auto_adjust=False,actions=True,
                      progress=False,threads=False)
        if isinstance(y.columns,pd.MultiIndex): y.columns=y.columns.get_level_values(0)
        y.columns=[str(c).lower().replace(' ','_') for c in y.columns]
        y.index=pd.to_datetime(y.index).tz_localize(None)
        validate_data(y,'Yahoo')
        y.to_csv(OUT/'yahoo_daily_snapshot.csv',float_format='%.12g')
        for source,ydata in [('Yahoo_split_adjusted',y),('Yahoo_dividend_adjusted',y.assign(**{c:y[c]*y.adj_close/y.close for c in ['open','high','low','close']}))]:
            for label,(a,b) in {**PARTS,'FULL_CONTINUOUS':(START,END)}.items():
                mm,_,_,_,_=simulate(prep(ydata.loc[a:b]),10)
                mm.update({'period':label,'mode':'LOCAL_WARMUP','cost_bps_per_side':10,'source':source})
                rows.append(mm)
        ym,yt,_,_,_=simulate(prep(y).loc[START:END],0)
        td_dates={(v['entry_date'],v['exit_date']) for v in lt}; yd_dates={(v['entry_date'],v['exit_date']) for v in yt}
        yahoo_report={'status':'PASS','split_adjusted_legacy_metrics':ym,'tradier_trade_count':len(td_dates),
            'yahoo_split_adjusted_trade_count':len(yd_dates),'matching_trades':len(td_dates&yd_dates),
            'tradier_only':sorted(td_dates-yd_dates),'yahoo_only':sorted(yd_dates-td_dates),
            'price_diff_median_abs_pct':{c:float((y.loc[START:END,c]/full_raw[c]-1).abs().median()) for c in ['open','high','low','close']}}
    except Exception as ex:
        yahoo_report={'status':'NOT_COMPLETED','error_type':type(ex).__name__,'note':'No Yahoo substitution into Tradier results. See validation checks for any failed data integrity check.'}
    for period in PARTS:
        subset=[t for t in alltrades if t['period']==period and t['cost_bps_per_side']==10]
        check(period+' signals precede fills',all(t['entry_signal_date']<t['entry_date']<=t['exit_signal_date']<t['exit_date'] for t in subset))
    table=pd.DataFrame(rows)
    for period in [*PARTS,'FULL_CONTINUOUS']:
        z=table[(table.source=='Tradier')&(table.period==period)&(table['mode']=='LOCAL_WARMUP')].sort_values('cost_bps_per_side')
        check(period+' higher cost lowers wealth',bool((z.ending_equity.diff().dropna()<=0).all()))
    table.to_csv(OUT/'metrics.csv',index=False,float_format='%.12g')
    pd.DataFrame(alltrades).to_csv(OUT/'all_primary_trade_ledgers.csv',index=False,float_format='%.12g')
    pd.DataFrame(annual_rows).to_csv(OUT/'annual_returns.csv',index=False,float_format='%.12g')
    pd.DataFrame(CHECKS).to_csv(OUT/'validation_checks.csv',index=False)
    report={'experiment':spec['experiment_id'],'data_as_of':END,'fetched_at':datetime.now(ZoneInfo('UTC')).isoformat(),
        'oos_integrity':'COMPROMISED / REUSED','primary':'fixed TCAR no QQQ; partition-local warmup; 10bp/side sensitivity assumption',
        'legacy_gross_replication':legacy_stats,'bootstrap_oos_10bp':bootstrap(oos_e),
        'concentration':concentration,'yahoo_replication':yahoo_report,
        'is_neighborhood':{'candidates':len(neighbors),'selected':False,
            'cagr_min':min(n['cagr'] for n in neighbors),'cagr_max':max(n['cagr'] for n in neighbors),
            'sharpe_min':min(n['sharpe'] for n in neighbors),'sharpe_max':max(n['sharpe'] for n in neighbors),
            'fraction_positive_cagr':sum(n['cagr']>0 for n in neighbors)/len(neighbors)},
        'checks_passed':sum(c['passed'] for c in CHECKS),'checks_total':len(CHECKS),
        'reference_sha256':reference_sha,'spec_sha256':sha(SPEC_PATH.read_bytes()),'provenance':provenance,
        'metrics':clean(rows),'orders_submitted':0,'deployment_changed':False}
    dump('summary.json',report)
    dump('hashes.json',{p.name:sha(p.read_bytes()) for p in OUT.iterdir() if p.is_file() and p.name!='hashes.json'})
    primary=table[(table.source=='Tradier')&(table.cost_bps_per_side==10)&table['mode'].isin(['LOCAL_WARMUP','BUY_HOLD'])]
    cols=['period','mode','total_return','cagr','sharpe','sortino','max_drawdown','calmar','trades','win_rate','profit_factor_return','ending_equity']
    text='# TCAR daily backtesting audit\n\nResearch only. OOS is reused, not an untouched holdout. No deployment changes or orders.\n\n'
    text+='## Primary 10bp-per-side scenario\n\n'+primary[cols].to_markdown(index=False)+'\n\n'
    text+='## Gross legacy replication\n\n```json\n'+json.dumps(clean(legacy_stats),indent=2)+'\n```\n\n'
    text+='## Protocol and limitations\n\nDaily SOXL; WR2<-90 AND CCI5<-80 AND SMA-ADX20>=15. Exit close>prior high OR WR2>-30. No QQQ sizing. Next-session-open fills; no same-close fills. Primary indicators calculated only within each partition, cash during warmup. Ending positions marked to close, not invented liquidations.\n\n'
    text+='10bp per side is an assumed combined friction scenario, not an empirically estimated cost. Fractional adjusted research units; no explicit dividends, cash yield, tax, settlement delays, opening-auction latency or capacity model. Tradier dividend adjustments are not guaranteed. Original dates were retained; historical OOS has already influenced prior research.\n\n'
    text+='Legacy externally warmed continuous results are diagnostic only and are not the strict partition-local skill result. Full-window returns are one simulation, not multiplied reset partitions. Daily-close drawdowns omit intraday extremes. Continuous subperiod trade statistics attribute whole trades by exit date and can cross cuts.\n\n'
    text+='## Independent checks\n\n'+str(sum(c['passed'] for c in CHECKS))+'/'+str(len(CHECKS))+' checks passed. Offline NumPy indicators, separate equity reconstruction, prefix causality, partition isolation, synthetic gap fills, calendar and OHLC validation.\n\n'
    text+='## OOS block-bootstrap uncertainty\n\n```json\n'+json.dumps(clean(report['bootstrap_oos_10bp']),indent=2)+'\n```\n\n'
    text+='## Concentration\n\n'+pd.DataFrame(concentration).to_markdown(index=False)+'\n\n'
    text+='## Yahoo check\n\n```json\n'+json.dumps(clean(yahoo_report),indent=2)+'\n```\n'
    (OUT/'REPORT.md').write_text(text,encoding='utf-8')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f: f.write(text)
    print(primary[cols].to_string(index=False))
    print('AUDIT_COMPLETE',report['checks_passed'],report['checks_total'])

if __name__=='__main__':
    try: main()
    except Exception as exc:
        OUT.mkdir(parents=True,exist_ok=True)
        pd.DataFrame(CHECKS).to_csv(OUT/'validation_checks.csv',index=False)
        print('AUDIT_FAILED',type(exc).__name__,str(exc) if isinstance(exc,AssertionError) else 'See safe checks; no API response bodies logged')
        raise SystemExit(1)
