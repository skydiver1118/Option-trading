#!/usr/bin/env python3
"""DCR-15 automated execution service for SOXL.

Frozen strategy
- RTH 15-minute bars only.
- Entry signal on completed bar: WR(5)<-80 AND CCI(5)<-80.
- Exit signal on completed bar: Close>previous-bar High OR WR(5)>-30 OR CCI(5)>0.
- Signal executes at the NEXT 15-minute bar open.
- One long SOXL position only; no pyramiding.

Execution modes
- dryrun  : no broker order call; logs intended orders.
- preview : Tradier production order preview=true only; no order submitted.
- paper   : submit to Tradier sandbox using TRADIER_SANDBOX_TOKEN.
- live    : production orders; additionally requires TRADIER_LIVE_ENABLE=YES_I_ACCEPT_REAL_ORDERS.

Market data uses TRADIER_TOKEN (production) when available. Paper brokerage uses the
sandbox token independently so paper execution can still use production market data.
"""
from __future__ import annotations
import csv, json, math, os, sys, time
from dataclasses import dataclass
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
import pandas as pd
import numpy as np

ET=ZoneInfo('America/New_York')
SYMBOL=os.getenv('DCR15_SYMBOL','SOXL')
MODE=os.getenv('DCR15_MODE','dryrun').lower()
ALLOC=float(os.getenv('DCR15_ALLOCATION_PCT','1.0'))
POLL=int(os.getenv('DCR15_POLL_SECONDS','5'))
STATE_PATH=Path(os.getenv('DCR15_STATE_PATH','runtime/dcr15/state.json'))
AUDIT_PATH=Path(os.getenv('DCR15_AUDIT_PATH','runtime/dcr15/audit.csv'))
LIVE_BASE='https://api.tradier.com/v1'
SANDBOX_BASE='https://sandbox.tradier.com/v1'
WR_N, WR_ENTRY, WR_EXIT = 5, -80.0, -30.0
CCI_N, CCI_ENTRY, CCI_EXIT = 5, -80.0, 0.0

@dataclass
class BrokerCfg:
    base: str
    token: str
    account_id: str|None
    submit: bool
    preview: bool


def jdump(p:Path,obj):
    p.parent.mkdir(parents=True,exist_ok=True); tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps(obj,indent=2,default=str)); tmp.replace(p)

def load_state():
    if STATE_PATH.exists(): return json.loads(STATE_PATH.read_text())
    return {'strategy':'SOXL_DCR15_V1','last_bar':None,'pending':None,'last_order':None,'last_order_status':None,'last_reconcile':None}

def audit(event, **kw):
    AUDIT_PATH.parent.mkdir(parents=True,exist_ok=True)
    row={'ts_et':datetime.now(ET).isoformat(),'event':event,**kw}
    fields=['ts_et','event','mode','bar_dt','wr','cci','close','prev_high','action','qty','order_id','order_status','note']
    exists=AUDIT_PATH.exists()
    with AUDIT_PATH.open('a',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore')
        if not exists: w.writeheader()
        w.writerow(row)
    print(json.dumps(row,default=str),flush=True)

def session(token, base):
    s=requests.Session(); s.headers.update({'Authorization':f'Bearer {token}','Accept':'application/json'}); s.base=base; return s

def resolve_account(s, requested=None):
    if requested: return requested
    r=s.get(f'{s.base}/user/profile',timeout=20); r.raise_for_status(); prof=r.json().get('profile') or {}; accts=prof.get('account') or []
    if isinstance(accts,dict): accts=[accts]
    active=[a for a in accts if a.get('status','active')=='active']
    if len(active)!=1:
        raise RuntimeError(f'Expected exactly one active account; found {len(active)}. Set TRADIER_ACCOUNT_ID explicitly.')
    return active[0]['account_number']

def broker_cfg():
    live_token=os.getenv('TRADIER_TOKEN','')
    sandbox_token=os.getenv('TRADIER_SANDBOX_TOKEN','')
    requested=os.getenv('TRADIER_ACCOUNT_ID')
    if MODE=='dryrun': return BrokerCfg(LIVE_BASE,live_token,requested,False,False)
    if MODE=='preview':
        if not live_token: raise RuntimeError('TRADIER_TOKEN required for preview mode')
        s=session(live_token,LIVE_BASE); return BrokerCfg(LIVE_BASE,live_token,resolve_account(s,requested),False,True)
    if MODE=='paper':
        if not sandbox_token: raise RuntimeError('TRADIER_SANDBOX_TOKEN required for paper mode')
        s=session(sandbox_token,SANDBOX_BASE); return BrokerCfg(SANDBOX_BASE,sandbox_token,resolve_account(s,requested),True,False)
    if MODE=='live':
        if os.getenv('TRADIER_LIVE_ENABLE')!='YES_I_ACCEPT_REAL_ORDERS': raise RuntimeError('Live mode is hard-locked. Set TRADIER_LIVE_ENABLE=YES_I_ACCEPT_REAL_ORDERS outside ChatGPT.')
        if not live_token: raise RuntimeError('TRADIER_TOKEN required for live mode')
        s=session(live_token,LIVE_BASE); return BrokerCfg(LIVE_BASE,live_token,resolve_account(s,requested),True,False)
    raise RuntimeError(f'Unknown DCR15_MODE={MODE}')

def market_session():
    token=os.getenv('TRADIER_TOKEN') or os.getenv('TRADIER_SANDBOX_TOKEN')
    if not token: raise RuntimeError('TRADIER_TOKEN or TRADIER_SANDBOX_TOKEN required for market data')
    return session(token, LIVE_BASE if os.getenv('TRADIER_TOKEN') else SANDBOX_BASE)

def fetch_bars(ms, days=10):
    now=datetime.now(ET); start=(now-timedelta(days=days)).strftime('%Y-%m-%d 09:30'); end=now.strftime('%Y-%m-%d %H:%M')
    r=ms.get(f'{ms.base}/markets/timesales',params={'symbol':SYMBOL,'interval':'15min','start':start,'end':end,'session_filter':'open'},timeout=20); r.raise_for_status()
    d=((r.json().get('series') or {}).get('data') or []); d=[d] if isinstance(d,dict) else d
    x=pd.DataFrame(d)
    if x.empty: return x
    tc='time' if 'time' in x else 'timestamp'; dt=pd.to_datetime(x[tc],errors='coerce')
    if dt.dt.tz is None: dt=dt.dt.tz_localize(ET,nonexistent='shift_forward',ambiguous='NaT')
    else: dt=dt.dt.tz_convert(ET)
    x['dt']=dt
    for c in ['open','high','low','close','volume']:
        if c in x: x[c]=pd.to_numeric(x[c],errors='coerce')
    x=x.dropna(subset=['dt','open','high','low','close']).sort_values('dt').drop_duplicates('dt').set_index('dt')
    t=x.index.time; return x[(t>=dtime(9,30))&(t<=dtime(15,45))]

def indicators(x):
    y=x.copy(); hh=y.high.rolling(WR_N).max(); ll=y.low.rolling(WR_N).min(); den=hh-ll; y['wr']=np.where(den.ne(0),-100*(hh-y.close)/den,np.nan)
    tp=(y.high+y.low+y.close)/3; ma=tp.rolling(CCI_N).mean(); md=tp.rolling(CCI_N).apply(lambda z:np.mean(np.abs(z-np.mean(z))),raw=True); y['cci']=np.where(md.ne(0),(tp-ma)/(0.015*md),np.nan); y['prev_high']=y.high.shift(1); return y

def get_positions(bs, cfg):
    if not cfg.account_id or not cfg.token: return []
    r=bs.get(f'{cfg.base}/accounts/{cfg.account_id}/positions',timeout=20)
    if r.status_code==404: return []
    r.raise_for_status(); p=(r.json().get('positions') or {}).get('position') or []
    return [p] if isinstance(p,dict) else p

def soxl_qty(bs,cfg):
    for p in get_positions(bs,cfg):
        if p.get('symbol')==SYMBOL: return int(float(p.get('quantity',0)))
    return 0

def buying_power(bs,cfg):
    r=bs.get(f'{cfg.base}/accounts/{cfg.account_id}/balances',timeout=20); r.raise_for_status(); b=r.json().get('balances') or {}
    vals=[b.get('stock_buying_power'), b.get('total_cash'), (b.get('cash') or {}).get('cash_available')]
    vals=[float(v) for v in vals if v not in (None,'')]
    if not vals: raise RuntimeError('No usable stock buying power/cash field returned by Tradier')
    return max(vals)

def quote(ms):
    r=ms.get(f'{ms.base}/markets/quotes',params={'symbols':SYMBOL,'greeks':'false'},timeout=20); r.raise_for_status(); q=(r.json().get('quotes') or {}).get('quote') or {}; return q

def order_qty(ms,bs,cfg):
    bp=buying_power(bs,cfg); q=quote(ms); px=float(q.get('ask') or q.get('last') or q.get('close'))
    qty=math.floor(bp*ALLOC/px)
    if qty<1: raise RuntimeError(f'Insufficient buying power: bp={bp}, px={px}')
    return qty

def place(bs,cfg,side,qty,tag):
    payload={'class':'equity','symbol':SYMBOL,'side':side,'quantity':qty,'type':'market','duration':'day','preview':'true' if cfg.preview else 'false','tag':tag}
    if MODE=='dryrun': return {'id':'DRYRUN','status':'dryrun','payload':payload}
    r=bs.post(f'{cfg.base}/accounts/{cfg.account_id}/orders',data=payload,headers={'Content-Type':'application/x-www-form-urlencoded'},timeout=20); r.raise_for_status(); return r.json().get('order') or r.json()

def reconcile(bs,cfg,state):
    if MODE=='dryrun': return
    qty=soxl_qty(bs,cfg)
    state['broker_qty']=qty; state['last_reconcile']=datetime.now(ET).isoformat()
    # If a broker position exists, do not allow a pending buy; if flat, do not allow a pending sell.
    if qty>0 and state.get('pending',{}).get('action')=='buy': state['pending']=None
    if qty==0 and state.get('pending',{}).get('action')=='sell': state['pending']=None

def process_once(ms,bs,cfg,state):
    x=indicators(fetch_bars(ms))
    if x.empty or len(x)<max(WR_N,CCI_N)+1: return
    now=datetime.now(ET)
    # Tradier may include the currently-forming bar. A bar timestamp is its start; only accept it after +15 min.
    complete=x[x.index+pd.Timedelta(minutes=15) <= pd.Timestamp(now)]
    if complete.empty: return
    bar_dt=complete.index[-1]; row=complete.iloc[-1]
    # Execute pending action when the next bar has begun.
    p=state.get('pending')
    if p and pd.Timestamp(now) >= pd.Timestamp(p['execute_after']):
        broker_qty=soxl_qty(bs,cfg) if MODE!='dryrun' else int(state.get('sim_qty',0))
        if p['action']=='buy' and broker_qty==0:
            qty=order_qty(ms,bs,cfg) if MODE!='dryrun' else int(os.getenv('DCR15_DRYRUN_QTY','1'))
            o=place(bs,cfg,'buy',qty,f'DCR15-{bar_dt:%Y%m%d%H%M}-BUY'); state['last_order']=o.get('id'); state['last_order_status']=o.get('status'); state['sim_qty']=qty if MODE=='dryrun' else state.get('sim_qty',0); audit('order',mode=MODE,action='buy',qty=qty,order_id=o.get('id'),order_status=o.get('status'),note='pending next-bar-open execution')
        elif p['action']=='sell' and broker_qty>0:
            o=place(bs,cfg,'sell',broker_qty,f'DCR15-{bar_dt:%Y%m%d%H%M}-SELL'); state['last_order']=o.get('id'); state['last_order_status']=o.get('status'); state['sim_qty']=0 if MODE=='dryrun' else state.get('sim_qty',0); audit('order',mode=MODE,action='sell',qty=broker_qty,order_id=o.get('id'),order_status=o.get('status'),note='pending next-bar-open execution')
        state['pending']=None; jdump(STATE_PATH,state)
    if state.get('last_bar')==bar_dt.isoformat(): return
    broker_qty=soxl_qty(bs,cfg) if MODE!='dryrun' else int(state.get('sim_qty',0))
    wr,cci,cl,ph=float(row.wr),float(row.cci),float(row.close),float(row.prev_high)
    action=None; reason=''
    if broker_qty>0:
        reasons=[]
        if cl>ph: reasons.append('close>prev_high')
        if wr>WR_EXIT: reasons.append('wr_exit')
        if cci>CCI_EXIT: reasons.append('cci_exit')
        if reasons: action='sell'; reason='+'.join(reasons)
    else:
        if wr<WR_ENTRY and cci<CCI_ENTRY: action='buy'; reason='wr_entry+cci_entry'
    state['last_bar']=bar_dt.isoformat()
    if action:
        execute_after=(bar_dt+pd.Timedelta(minutes=15)).isoformat(); state['pending']={'action':action,'signal_bar':bar_dt.isoformat(),'execute_after':execute_after,'reason':reason}
        audit('signal',mode=MODE,bar_dt=bar_dt.isoformat(),wr=wr,cci=cci,close=cl,prev_high=ph,action=action,note=reason)
    else: audit('bar',mode=MODE,bar_dt=bar_dt.isoformat(),wr=wr,cci=cci,close=cl,prev_high=ph,note='no signal')
    jdump(STATE_PATH,state)

def main():
    cfg=broker_cfg(); ms=market_session(); bs=session(cfg.token,cfg.base) if cfg.token else ms; state=load_state(); reconcile(bs,cfg,state); jdump(STATE_PATH,state)
    once='--once' in sys.argv
    while True:
        try: process_once(ms,bs,cfg,state)
        except Exception as e: audit('error',mode=MODE,note=repr(e));
        if once: break
        time.sleep(POLL)

if __name__=='__main__': main()
