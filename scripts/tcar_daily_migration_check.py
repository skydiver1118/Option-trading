#!/usr/bin/env python3
"""GET-only migration guard. Never imports a trading service or places orders."""
import json
import math
import os
import re
import sys
from pathlib import Path
from tcar_daily_sandbox_preflight import get_json

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {'filled','rejected','expired','canceled','error'}

def rows(parent,key):
    if parent is None or parent == 'null': return []
    if not isinstance(parent,dict): raise ValueError('SCHEMA')
    value=parent.get(key)
    if value is None or value == 'null': return []
    if isinstance(value,dict): return [value]
    if isinstance(value,list) and all(isinstance(v,dict) for v in value): return value
    raise ValueError('SCHEMA')

def main():
    accounts=rows(get_json('sandbox','/user/profile').get('profile'),'account')
    active=[a for a in accounts if a.get('status')=='active']
    requested=os.environ.get('TRADIER_ACCOUNT_ID')
    if requested: active=[a for a in active if a.get('account_number')==requested]
    if len(active)!=1: raise ValueError('ACCOUNT_AMBIGUOUS')
    account=str(active[0].get('account_number',''))
    if not re.fullmatch('[A-Za-z0-9_-]+',account): raise ValueError('ACCOUNT_SCHEMA')
    payload=get_json('sandbox',f'/accounts/{account}/positions')
    if 'positions' not in payload: raise ValueError('POSITION_SCHEMA')
    soxl=[p for p in rows(payload['positions'],'position') if p.get('symbol')=='SOXL']
    flat=True
    for p in soxl:
        qty=float(p.get('quantity'))
        if not math.isfinite(qty): raise ValueError('QUANTITY_SCHEMA')
        flat=flat and qty==0
    all_orders=[]
    for page in range(1,11):
        payload=get_json('sandbox',f'/accounts/{account}/orders',{'includeTags':'true','limit':1000,'page':page})
        if 'orders' not in payload: raise ValueError('ORDER_SCHEMA')
        batch=rows(payload['orders'],'order'); all_orders.extend(batch)
        if len(batch)<1000: break
    else: raise ValueError('ORDER_PAGINATION_LIMIT')
    orders_clear=not any(o.get('symbol')=='SOXL' and o.get('status') not in TERMINAL for o in all_orders)
    state_clear=True
    for file in (ROOT/'runtime/dcr15/paper-state.json',ROOT/'runtime/tcar_daily/paper-state.json'):
        if not file.exists(): continue
        state=json.loads(file.read_text(encoding='utf-8'))
        for key in ('owned_qty','broker_qty'):
            value=float(state.get(key,0) or 0)
            state_clear=state_clear and math.isfinite(value) and value==0
        state_clear=state_clear and not any(state.get(k) for k in ('active_order','submission_unknown','halted_reason'))
    clear=flat and orders_clear and state_clear
    report={'status':'PASS' if clear else 'BLOCKED','check':'DAILY_MIGRATION_GET_ONLY',
        'soxl_position_flat':flat,'soxl_orders_clear':orders_clear,'saved_state_clear':state_clear,
        'orders_submitted':0,'bot_started':False}
    print(json.dumps(report,indent=2))
    return 0 if clear else 1

if __name__=='__main__':
    try: sys.exit(main())
    except Exception:
        print('{"status":"FAILED","error":"MIGRATION_CHECK_FAILED","orders_submitted":0,"bot_started":false}')
        sys.exit(2)
