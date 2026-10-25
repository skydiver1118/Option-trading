"""Guarded study stages. Run help for the deliberate one-way workflow.

No stage can bypass data or visual gates. An OOS error leaves its once-only
lock in place; repairing a defective OOS calculation does not restore virginity.
"""
import argparse
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib,json,math,sys
from pathlib import Path
import numpy as np
import pandas as pd
from cpa_v2_engine import Parameters,SPACE,configurations,neighbors,detect,simulate,metrics

ROOT=Path(__file__).resolve().parents[1]
STUDY=ROOT/'research/cpa_v2_20260904'
RAW=ROOT/'data/cpa_v2_20260904_source'
OUT=STUDY/'results'
PAIRS=[('SPY','SPY'),('SPMO','SPMO'),('VGT','VGT'),('SMH','SMH'),
       ('QQQ','QQQ'),('TQQQ','TQQQ'),('SMH','SOXL'),('QQQ','TQQQ')]
CODE=[ROOT/'scripts/cpa_v2_engine.py',Path(__file__),STUDY/'PROTOCOL.md',STUDY/'SOURCE_DEFINITIONS.md']


def now():return datetime.now(timezone.utc).isoformat()
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def write_json(path,value,exclusive=False):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x' if exclusive else 'w') as f:json.dump(value,f,indent=2,sort_keys=True,allow_nan=False,default=str)
def read_json(path):return json.loads(path.read_text())
def snapshot():
    files=[*CODE,STUDY/'search_space.json',OUT/'partitions.json',*sorted(RAW.glob('*')),
           *sorted((OUT/'prepared').glob('*/*.csv'))]
    return {str(p.relative_to(ROOT)):sha(p) for p in files if p.is_file()}
def check_snapshot(expected):
    if snapshot()!=expected:raise RuntimeError('Source, protocol, or code changed after stage registration')
def dataset(symbol):return pd.read_csv(RAW/(symbol+'.csv'),parse_dates=['date']).set_index('date')
def load_stage(symbol,stage):
    # Separated files: IS/Validation code never parses an OOS bar.
    return pd.read_csv(OUT/'prepared'/stage/(symbol+'.csv'),parse_dates=['date']).set_index('date')
def cost(symbol):return .001 if symbol in ['SOXL','TQQQ'] else .0005


def prepare():
    if any((OUT/f).exists() for f in ['IS_STARTED.json','OOS_STARTED.json']):
        raise RuntimeError('Preparation cannot overwrite a started study')
    OUT.mkdir(parents=True,exist_ok=True)
    spy=dataset('SPY');cal=spy.loc['2016-09-05':'2026-09-04'].index
    n=len(cal);a=math.floor(n*.6);b=math.floor(n*.8)
    parts={k:dict(start=str(ix[0].date()),end=str(ix[-1].date()),rows=len(ix))
           for k,ix in [('IS',cal[:a]),('Validation',cal[a:b]),('OOS',cal[b:])]}
    issues=[];summary=[]
    for symbol in sorted(set(sum(([s,t] for s,t in PAIRS),[]))):
        x=dataset(symbol)
        valid=((x[['open','high','low','close']]>0).all(axis=1)&
               (x.low<=x[['open','close','high']].min(axis=1))&
               (x.high>=x[['open','close','low']].max(axis=1))&
               (x.volume>=0)&np.isfinite(x[['open','high','low','close','volume']]).all(axis=1))
        missing=cal.difference(x.index)
        for date in missing:issues.append(dict(symbol=symbol,date=str(date.date()),issue='MISSING_SESSION'))
        for date in x.index[~valid]:issues.append(dict(symbol=symbol,date=str(date.date()),issue='INVALID_OHLCV'))
        for date in x.index[x.index.duplicated()]:issues.append(dict(symbol=symbol,date=str(date.date()),issue='DUPLICATE_SESSION'))
        if not x.index.is_monotonic_increasing:issues.append(dict(symbol=symbol,date='',issue='UNSORTED_SOURCE'))
        if x.index[-1]!=pd.Timestamp('2026-09-04'):issues.append(dict(symbol=symbol,date='',issue='ENDPOINT_UNAVAILABLE'))
        summary.append(dict(symbol=symbol,rows=len(x),first=str(x.index[0].date()),last=str(x.index[-1].date()),
                            missing_study_sessions=len(missing),invalid_bars=int((~valid).sum()),
                            zero_volume_bars=int((x.volume==0).sum())))
        for stage,part in parts.items():
            dest=OUT/'prepared'/stage/(symbol+'.csv');dest.parent.mkdir(parents=True,exist_ok=True)
            # Historical warm-up kept, later rows physically excluded.
            x.loc[:part['end']].to_csv(dest,index_label='date')
    write_json(OUT/'partitions.json',parts)
    write_json(STUDY/'search_space.json',dict(status='REGISTERED_NOT_TESTED',seed=20260904,
                initial_configurations=256,parameter_space=SPACE,
                reference_configuration=asdict(Parameters()),source_constants={'daily_EMAs':[10,20],'weekly_EMAs':[10,20]},
                structural_conventions={'minimum_history_bars':100,'contraction_reference':'two non-overlapping equal pivot windows',
                                        'HAC_lag':5,'bar_range_epsilon':1e-12},
                candidates=[dict(id=p.id,parameters=asdict(p)) for p in configurations()],
                IS_selection='At most 3 per pair by Sharpe, Calmar, worst-half Sharpe, lower MaxDD; adjacent-parameter rejection without refill',
                neighborhood_gate='>=70% neighbors positive CAGR and Sharpe; median neighbor Sharpe >=0.5*anchor; no retuning',
                validation='PROTOCOL.md, all criteria mandatory',OOS='PROTOCOL.md, once only'))
    pd.DataFrame(issues,columns=['symbol','date','issue']).to_csv(OUT/'data_issues.csv',index=False)
    write_json(OUT/'input_quality.json',dict(registered_at=now(),accepted=not issues,symbols=summary,
                issue_count=len(issues),source_hashes=snapshot(),
                statement='Input integrity only. No strategy returns, Sharpe, trades, or rankings computed.'))
    print(json.dumps(dict(partitions=parts,quality=summary,accepted=not issues,issue_count=len(issues)),indent=2))


def gate():
    quality=read_json(OUT/'input_quality.json');check_snapshot(quality['source_hashes'])
    if not quality['accepted']:raise RuntimeError('BLOCKED: unresolved Tradier OHLCV/session integrity defects; see data_issues.csv')
    visual=read_json(STUDY/'visual_review.json')
    if not visual.get('accepted',False):raise RuntimeError('BLOCKED: source-example visual validation has not passed')
    if visual.get('engine_sha256')!=sha(ROOT/'scripts/cpa_v2_engine.py'):
        raise RuntimeError('Visual review is not bound to current engine')


def case(p,s,t,stage,save=True,stress=False):
    part=read_json(OUT/'partitions.json')[stage]
    signal=load_stage(s,stage);execution=load_stage(t,stage)
    events=detect(signal,p)
    lg,tr,fl=simulate(execution,events,part['start'],part['end'],cost(t)*(2 if stress else 1))
    m=metrics(lg,tr)
    if save:
        folder=OUT/stage/f'{s}_{t}'/p.id;folder.mkdir(parents=True,exist_ok=True)
        label='stress_' if stress else ''
        lg.to_csv(folder/(label+'equity.csv.gz'))
        tr.to_csv(folder/(label+'trades.csv.gz'),index=False);fl.to_csv(folder/(label+'fills.csv.gz'),index=False)
        if not stress:events.loc[part['start']:part['end']].to_csv(folder/'states_events.csv.gz')
        write_json(folder/(label+'metrics.json'),m)
    return m


def is_ok(m):
    # Half-cycle counts are calculated from actual half portfolios separately.
    return m['IndependentCycles']>=20 and m['CAGR']>0 and m['Sharpe']>0


def run_is():
    gate();write_json(OUT/'IS_STARTED.json',dict(started_at=now(),snapshot=snapshot()),exclusive=True)
    allrows=[];provisional=[];pars={p.id:p for p in configurations()}
    for s,t in PAIRS:
        rows=[]
        for p in configurations():
            m=case(p,s,t,'IS');row=dict(signal=s,trade=t,id=p.id,kind='initial',**m)
            allrows.append(row);rows.append(row)
        ranked=sorted([r for r in rows if is_ok(r)],key=lambda r:(r['Sharpe'],r['Calmar'] or 0,
                        min(h['sharpe'] for h in r['Halves']),r['MaxDD']),reverse=True)[:3]
        for r in ranked:
            p=pars[r['id']];near=[]
            for q in neighbors(p):
                pars[q.id]=q;m=case(q,s,t,'IS')
                allrows.append(dict(signal=s,trade=t,id=q.id,kind='IS_neighbor',anchor=p.id,**m));near.append(m)
            fraction=np.mean([m['CAGR']>0 and m['Sharpe']>0 for m in near])
            median=float(np.median([m['Sharpe'] for m in near]))
            events=detect(load_stage(s,'IS'),p);ex=load_stage(t,'IS')
            part=read_json(OUT/'partitions.json')['IS'];ix=ex.loc[part['start']:part['end']].index
            halfcycles=[]
            for half in np.array_split(ix,2):
                lg,tr,fl=simulate(ex,events,half[0],half[-1],cost(t));halfcycles.append(metrics(lg,tr)['IndependentCycles'])
            accepted=bool(fraction>=.7 and median>=.5*r['Sharpe'] and min(halfcycles)>=6)
            write_json(OUT/'IS'/f'{s}_{t}'/p.id/'stability.json',dict(accepted=accepted,
                         positive_neighbor_fraction=float(fraction),median_neighbor_Sharpe=median,
                         IS_half_independent_cycles=halfcycles))
            if accepted:provisional.append(dict(signal=s,trade=t,id=p.id,parameters=asdict(p),IS=r))
    pd.DataFrame(allrows).to_csv(OUT/'IS_all_results.csv',index=False)
    write_json(OUT/'IS_all_tested_parameters.json',{k:asdict(v) for k,v in pars.items()})
    write_json(OUT/'IS_frozen_candidates.json',dict(frozen_at=now(),snapshot=snapshot(),
                search_space_sha256=sha(STUDY/'search_space.json'),candidates=provisional),exclusive=True)
    print('IS candidates frozen:',len(provisional))


def validation_pass(v,ins,stress):
    pf_ok=(v['ProfitFactor'] is not None and v['ProfitFactor']>1) or (v['GrossProfit']>0 and v['GrossLoss']==0)
    checks=dict(cycles=v['IndependentCycles']>=6,positive_CAGR=v['CAGR']>0,
                Sharpe=v['Sharpe']>=.5 and v['Sharpe']>=.5*ins['Sharpe'],
                drawdown=abs(v['MaxDD'])<=.35 and abs(v['MaxDD'])<=max(.10,1.5*abs(ins['MaxDD'])),
                profit_factor=pf_ok,both_halves_positive=all(h['total_return']>0 for h in v['Halves']),
                profit_concentration=v['ProfitConcentration'] is not None and v['ProfitConcentration']<=.5,
                doubled_cost_positive=stress['CAGR']>0)
    return {k:bool(value) for k,value in checks.items()}


def run_validation():
    gate();manifest=read_json(OUT/'IS_frozen_candidates.json');check_snapshot(manifest['snapshot'])
    write_json(OUT/'VALIDATION_STARTED.json',dict(started_at=now(),IS_manifest_sha256=sha(OUT/'IS_frozen_candidates.json')),exclusive=True)
    rows=[];survivors=[]
    for c in manifest['candidates']:
        p=Parameters(**c['parameters']);s=c['signal'];t=c['trade']
        v=case(p,s,t,'Validation');stress=case(p,s,t,'Validation',stress=True)
        checks=validation_pass(v,c['IS'],stress)
        rows.append(dict(signal=s,trade=t,id=p.id,metrics=v,stress=stress,checks=checks,accepted=all(checks.values())))
        if all(checks.values()):survivors.append(c)
    write_json(OUT/'validation_results.json',rows,exclusive=True)
    write_json(OUT/'frozen_manifest.json',dict(frozen_at=now(),snapshot=snapshot(),
               IS_manifest_sha256=sha(OUT/'IS_frozen_candidates.json'),
               validation_sha256=sha(OUT/'validation_results.json'),
               visual_sha256=sha(STUDY/'visual_review.json'),candidates=survivors,
               OOS_inspected=False),exclusive=True)
    print('Validation survivors frozen:',len(survivors))


def run_oos():
    gate();frozen=read_json(OUT/'frozen_manifest.json');check_snapshot(frozen['snapshot'])
    if frozen['IS_manifest_sha256']!=sha(OUT/'IS_frozen_candidates.json') or frozen['validation_sha256']!=sha(OUT/'validation_results.json'):
        raise RuntimeError('Frozen predecessor manifest changed')
    if frozen['visual_sha256']!=sha(STUDY/'visual_review.json'):raise RuntimeError('Visual gate changed after freeze')
    write_json(OUT/'OOS_STARTED.json',dict(started_at=now(),manifest_sha256=sha(OUT/'frozen_manifest.json'),
               warning='One-time lock; exceptions do not authorize reruns'),exclusive=True)
    rows=[];benchmark_rows=[];strategy_returns={}
    try:
        for c in frozen['candidates']:
            p=Parameters(**c['parameters']);s=c['signal'];t=c['trade']
            m=case(p,s,t,'OOS');stress=case(p,s,t,'OOS',stress=True)
            # Descriptive correlation is part of this same terminal batch.
            # It cannot nominate parameters or candidates for another run.
            curve=pd.read_csv(OUT/'OOS'/f'{s}_{t}'/p.id/'equity.csv.gz',parse_dates=['date']).set_index('date')
            strategy_returns[f'{s}_{t}_{p.id}']=curve.return_daily
            eligible=m['Sharpe']>1.0 and m['CAGR']>.30 and m['IndependentCycles']>=20
            rows.append(dict(signal=s,trade=t,id=p.id,eligible=bool(eligible),StressCAGR=stress['CAGR'],**m))
        # Every benchmark is evaluated in this same terminal batch, only after
        # parameters and the entire candidate set have been frozen.
        if frozen['candidates']:
            part=read_json(OUT/'partitions.json')['OOS']
            for symbol in sorted(set(sum(([c['signal'],c['trade']] for c in frozen['candidates']),[]))):
                x=load_stage(symbol,'OOS');events=detect(x,Parameters())
                lg,tr,fl=simulate(x,events,part['start'],part['end'],cost(symbol),benchmark=True)
                folder=OUT/'OOS'/'benchmarks';folder.mkdir(parents=True,exist_ok=True)
                lg.to_csv(folder/(symbol+'_equity.csv.gz'));tr.to_csv(folder/(symbol+'_trades.csv.gz'),index=False)
                benchmark_rows.append(dict(symbol=symbol,**metrics(lg,tr)))
        df=pd.DataFrame(rows)
        if strategy_returns:
            pd.DataFrame(strategy_returns).corr().to_csv(OUT/'OOS_strategy_return_correlation.csv')
        if len(df):
            eligible=df[df.eligible].copy()
            if len(eligible):
                eligible['WorstHalfSharpe']=eligible.Halves.map(lambda h:min(v['sharpe'] for v in h))
                eligible['PositiveHalves']=eligible.Halves.map(lambda h:sum(v['total_return']>0 for v in h))
                eligible['CappedPF']=eligible.apply(lambda r:min(r.ProfitFactor,5.) if pd.notna(r.ProfitFactor) else 5.,axis=1)
                cols=['HACSharpe','Calmar','Sortino','MaxDD','WorstHalfSharpe','IndependentCycles','CappedPF','PositiveHalves']
                eligible['RobustnessRankScore']=eligible[cols].fillna(0).rank(pct=True).mean(axis=1)
                eligible.sort_values(['RobustnessRankScore','Exposure','id'],ascending=[False,True,True]).to_csv(OUT/'OOS_final_selection.csv',index=False)
            df.to_csv(OUT/'OOS_untouched_results.csv',index=False)
        write_json(OUT/'OOS_benchmarks.json',benchmark_rows,exclusive=True)
        write_json(OUT/'OOS_COMPLETED.json',dict(completed_at=now(),candidates=len(rows),
                   eligible=sum(r['eligible'] for r in rows),
                   outcome='NO CPA STRATEGY PASSED' if not any(r['eligible'] for r in rows) else 'SEE FROZEN FINAL SELECTION',
                   prior_use_caveat='Historical dates were previously tested by ancestor; this protocol ran once only.'),exclusive=True)
    except Exception as exc:
        write_json(OUT/'OOS_INVALID.json',dict(at=now(),error=str(exc),status='INVALID; DO NOT RERUN FOR SELECTION'))
        raise
    print('OOS terminal evaluation completed; further revisions cannot restore this holdout.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['prepare','is','validation','oos'])
    args=parser.parse_args()
    {'prepare':prepare,'is':run_is,'validation':run_validation,'oos':run_oos}[args.stage]()


if __name__=='__main__':main()
