#!/usr/bin/env python3
"""Conditional research follow-up. Original data gate remains FAILED.

No repairs to the Tradier replay, no parameter selection, no broker account APIs.
Frozen inputs are downloaded from the first audit artifact and hash-verified.
"""
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import requests
import audit_tcar_skill_20260906 as audit

ROOT=Path(__file__).resolve().parents[1]
PLAN=ROOT/'research/tcar_skill_audit_20260906/data_quality_followup.json'
FROZEN=ROOT/'research/tcar_skill_audit_20260906/frozen_input'
OUT=audit.OUT


def frozen_data():
    plan=json.loads(PLAN.read_text())
    rows=[]; provenance=[]
    for name,expected in plan['frozen_inputs'].items():
        data=(FROZEN/name).read_bytes()
        actual=hashlib.sha256(data).hexdigest()
        audit.check('Frozen raw SHA256 '+name,actual==expected,actual)
        (OUT/name).write_bytes(data)
        rows.extend(json.loads(data)['history']['day'])
        provenance.append({'endpoint':'https://api.tradier.com/v1/markets/history','method':'GET',
            'source_run':34078685513,'response_sha256':actual,'raw_file':name,
            'note':'Immutable first-audit Tradier response, not a Yahoo proxy'})
    x=pd.DataFrame(rows)
    x['date']=pd.to_datetime(x.date)
    x=x.set_index('date').sort_index()
    for c in ('open','high','low','close','volume'): x[c]=pd.to_numeric(x[c],errors='coerce')
    return x[['open','high','low','close','volume']],provenance


def conditional_validation(x,label,calendar=True):
    audit.check(label+' unique dates',not x.index.has_duplicates)
    audit.check(label+' sorted dates',x.index.is_monotonic_increasing)
    arr=x[['open','high','low','close']].to_numpy(float)
    audit.check(label+' finite positive OHLC',np.isfinite(arr).all() and (arr>0).all())
    bad=((x.high+1e-6<x[['open','close','low']].max(axis=1)) | (x.low-1e-6>x[['open','close']].min(axis=1)))
    if bad.any() and label=='Tradier':
        # Explicitly retain the failure. Do NOT edit OHLC values or call this a pass.
        audit.CHECKS.append({'check':'Tradier OHLC order','passed':False,
            'detail':'CONDITIONAL replay of original feed; flagged dates='+','.join(str(t.date()) for t in x.index[bad])})
        x.loc[bad].to_csv(OUT/'tradier_ohlc_violations.csv',float_format='%.12g')
    else:
        audit.check(label+' OHLC order',not bad.any())
    z=x.loc[audit.START:audit.END]
    audit.check(label+' evaluation endpoints',str(z.index[0].date())==audit.START and str(z.index[-1].date())==audit.END)
    if calendar:
        import exchange_calendars as xc
        sessions=xc.get_calendar('XNYS',start=audit.WARMUP,end=audit.END).sessions_in_range(audit.START,audit.END)
        if sessions.tz is not None:sessions=sessions.tz_localize(None)
        missing=sessions.difference(z.index);extra=z.index.difference(sessions)
        audit.check(label+' NYSE calendar completeness',not len(missing) and not len(extra),
            f'missing={list(missing)}; extra={list(extra)}; sessions={len(z)}')


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    audit.fetch_tradier=frozen_data
    audit.validate_data=conditional_validation
    audit.main()
    raw=pd.read_csv(OUT/'tradier_daily_snapshot.csv',parse_dates=['date']).set_index('date')
    bad=pd.read_csv(OUT/'tradier_ohlc_violations.csv',parse_dates=['date']).set_index('date')
    comparison=[]; reread=[]; hybrid_metrics=[]
    ypath=OUT/'yahoo_daily_snapshot.csv'
    if ypath.exists():
        yahoo=pd.read_csv(ypath,index_col=0,parse_dates=True)
        yahoo.index.name='date'
        hybrid=raw.copy()
        for dt,row in bad.iterrows():
            item={'date':str(dt.date()),'inside_study':audit.START<=str(dt.date())<=audit.END}
            for c in ('open','high','low','close'):
                item['tradier_'+c]=float(row[c])
                item['yahoo_'+c]=float(yahoo.loc[dt,c])
            comparison.append(item)
            hybrid.loc[dt,['open','high','low','close']]=yahoo.loc[dt,['open','high','low','close']].to_numpy(float)
        prep,_,_=audit.reference_functions()
        for label,(a,b) in {**audit.PARTS,'FULL_CONTINUOUS':(audit.START,audit.END)}.items():
            for cost in (0,10):
                m,_,_,_,_=audit.simulate(prep(hybrid.loc[a:b]),cost)
                hybrid_metrics.append({'period':label,'cost_bps_per_side':cost,
                    'source':'HYBRID_YAHOO_ON_FLAGGED_ROWS_SENSITIVITY_NOT_AUTHORITATIVE',**m})
        m,_,_,_,_=audit.simulate(prep(hybrid).loc[audit.START:audit.END],0)
        hybrid_metrics.append({'period':'LEGACY_CONTINUOUS','cost_bps_per_side':0,
                    'source':'HYBRID_YAHOO_ON_FLAGGED_ROWS_SENSITIVITY_NOT_AUTHORITATIVE',**m})
        pd.DataFrame(hybrid_metrics).to_csv(OUT/'hybrid_flagged_row_sensitivity.csv',index=False,float_format='%.12g')
        pd.DataFrame(comparison).to_csv(OUT/'flagged_dates_vendor_comparison.csv',index=False,float_format='%.12g')
    token=os.environ.get('TRADIER_TOKEN','').strip()
    for dt,row in bad.iterrows():
        ds=str(dt.date())
        try:
            r=requests.get('https://api.tradier.com/v1/markets/history',
                params={'symbol':'SOXL','interval':'daily','start':ds,'end':ds},
                headers={'Authorization':'Bearer '+token,'Accept':'application/json'},timeout=30,allow_redirects=False)
            if r.status_code!=200: raise ValueError('HTTP')
            data=(r.json().get('history') or {}).get('day')
            if isinstance(data,list):data=data[0]
            same=all(abs(float(data[c])-float(row[c]))<=1e-8 for c in ('open','high','low','close'))
            name='tradier_single_date_'+ds+'.json'
            (OUT/name).write_bytes(r.content)
            reread.append({'date':ds,'status':'PASS','matches_initial_ohlc':same,
                          'response_sha256':hashlib.sha256(r.content).hexdigest()})
        except Exception as exc:
            reread.append({'date':ds,'status':'FAILED','error_type':type(exc).__name__})
    summary=json.loads((OUT/'summary.json').read_text())
    summary['audit_status']='CONDITIONAL_REPLAY_ONLY_DATA_GATE_FAILED'
    summary['data_quality']={'original_gate':'FAILED','ohlc_violations':len(bad),
        'inside_study':int(sum(audit.START<=str(d.date())<=audit.END for d in bad.index)),
        'vendor_comparison':comparison,'single_date_repull':reread,
        'hybrid_sensitivity':hybrid_metrics,
        'note':'Raw Tradier prices were not repaired. Hybrid substitution is disclosed sensitivity only. It does not validate the original feed.'}
    summary['skill_package_status']='Recovered protocol requirements applied; locally installed skill and its own test suite not accessible or executed.'
    summary['checks_passed']=sum(c['passed'] for c in audit.CHECKS)
    summary['checks_total']=len(audit.CHECKS)
    audit.dump('summary.json',summary)
    pd.DataFrame(audit.CHECKS).to_csv(OUT/'validation_checks.csv',index=False)
    text=(OUT/'REPORT.md').read_text()
    prefix='# DATA-QUALITY GATE FAILED: CONDITIONAL RESULTS ONLY\n\n'
    prefix+='Seven original Tradier bars have inconsistent OHLC bounds, including three inside the study. The initial strict run stopped. This follow-up preserves those prices for a conditional replication, not a validated backtest. OOS remains reused.\n\n'
    if comparison:prefix+=pd.DataFrame(comparison).to_markdown(index=False)+'\n\n'
    prefix+='Full local skill package was not available; recovered specification, partition-local warm-up and audit requirements were implemented. No claim is made that the local installed skill test suite ran.\n\n'
    (OUT/'REPORT.md').write_text(prefix+text)
    shutil.copy(PLAN,OUT/'data_quality_followup.json')
    audit.dump('hashes.json',{p.name:audit.sha(p.read_bytes()) for p in OUT.iterdir() if p.is_file() and p.name!='hashes.json'})
    print('CONDITIONAL_AUDIT_FINISHED: original data gate FAILED, no orders or deployment changes')
    print(json.dumps(audit.clean(summary['data_quality']),indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('\n\n'+prefix)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('SOURCE_DIAGNOSTIC_FAILED',type(exc).__name__)
        raise SystemExit(1)
