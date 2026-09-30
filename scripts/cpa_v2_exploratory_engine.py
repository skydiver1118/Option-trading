"""Experimental daily CPA_v2 descendant. No import-time IO or optimization.

Kell defines a discretionary framework, not these numerical cutoffs. See
research/cpa_v2_20260904/SOURCE_DEFINITIONS.md for the source/assumption boundary.
Only completed bars enter signals; orders produced here execute next session.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Parameters:
    atr_bars: int = 14
    volume_bars: int = 20
    support_weeks: int = 26
    support_tolerance_atr: float = 2.0
    reversal_atr: float = 1.5
    reversal_clv: float = 0.60
    reversal_volume: float = 1.25
    reversal_timeout: int = 40
    pivot_bars: int = 5
    contraction: float = 0.85
    ema_band_atr: float = 1.0
    breakout_volume: float = 1.0
    crossback_delay: int = 2
    touch_atr: float = 0.25
    base_bars: int = 10
    base_width_atr: float = 4.0
    base_support_fraction: float = 0.7
    extension_atr: float = 1.5
    weekly_extension: float = 0.04
    extension_reset_atr: float = 0.25
    drop_pivot_bars: int = 3
    stop_atr: float = 0.5
    entry_stage: str = 'WEDGE_POP'
    trim_extension: int = 2
    trim_fraction: float = 0.5

    @property
    def id(self):
        return hashlib.sha256(json.dumps(asdict(self),sort_keys=True).encode()).hexdigest()[:16]


# Every researcher-selected numeric decision appears here, or is an explicit
# structural convention below. No values are calibrated from Validation/OOS.
SPACE = {
    'atr_bars': [10,14,20], 'volume_bars': [10,20,30],
    'support_weeks': [13,26,52], 'support_tolerance_atr': [1.0,2.0,3.0],
    'reversal_atr': [1.0,1.5,2.0], 'reversal_clv': [0.55,0.60,0.75],
    'reversal_volume': [1.0,1.25,1.5], 'reversal_timeout': [20,40,60],
    'pivot_bars': [3,5,8], 'contraction': [0.7,0.85,1.0],
    'ema_band_atr': [0.5,1.0,1.5], 'breakout_volume': [1.0,1.25,1.5],
    'crossback_delay': [2,3,5], 'touch_atr': [0.0,0.25,0.5],
    'base_bars': [5,10,15], 'base_width_atr': [2.5,4.0,6.0],
    'base_support_fraction': [0.7,0.85,1.0],
    'extension_atr': [1.0,1.5,2.0], 'weekly_extension': [0.02,0.04,0.06],
    'extension_reset_atr': [0.0,0.25,0.5], 'drop_pivot_bars': [2,3,5],
    'stop_atr': [0.0,0.5,1.0],
    'entry_stage': ['WEDGE_POP','EMA_CROSSBACK','BASE_N_BREAK'],
    'trim_extension': [1,2,3], 'trim_fraction': [0.0,0.25,0.5],
}


def configurations(count=256,seed=20260904):
    """Fixed bounded random screen, including the morphology-only reference."""
    rng=np.random.default_rng(seed)
    result={Parameters().id: Parameters()}
    while len(result)<count:
        p=Parameters(**{k:values[int(rng.integers(len(values)))] for k,values in SPACE.items()})
        result[p.id]=p
    return list(result.values())


def neighbors(p):
    """One adjacent grid step, one parameter at a time; IS diagnostics only."""
    result=[]
    for k,values in SPACE.items():
        if k=='entry_stage':
            continue
        i=values.index(getattr(p,k))
        for j in [i-1,i+1]:
            if 0<=j<len(values):
                d=asdict(p);d[k]=values[j];result.append(Parameters(**d))
    return result


def features(raw,p):
    """Forward-only indicators; the current week is unavailable until Saturday.

    Rolling reference ranges/volume/pivots exclude the signal bar. This avoids
    the ancestor's mistake of demanding contraction on a breakout bar itself.
    """
    x=raw.copy()
    pc=x.close.shift(1)
    tr=pd.concat([x.high-x.low,(x.high-pc).abs(),(x.low-pc).abs()],axis=1).max(axis=1)
    x['atr']=tr.rolling(p.atr_bars).mean()
    x['ema10']=x.close.ewm(span=10,adjust=False).mean()
    x['ema20']=x.close.ewm(span=20,adjust=False).mean()
    x['range']=x.high-x.low
    x['volume_ref']=x.volume.shift(1).rolling(p.volume_bars).mean()
    x['volume_ratio']=x.volume/x.volume_ref.replace(0,np.nan)
    x['pivot_hi']=x.high.shift(1).rolling(p.pivot_bars).max()
    x['pivot_lo']=x.low.shift(1).rolling(p.drop_pivot_bars).min()
    # Contraction before a breakout: prior pivot window versus the preceding
    # pivot window. Fixed equal-window comparison is structural, not optimized.
    x['contraction_ratio']=(x['range'].shift(1).rolling(p.pivot_bars).mean()/
                            x['range'].shift(p.pivot_bars+1).rolling(p.pivot_bars).mean())
    w=x[['open','high','low','close','volume']].resample('W-FRI').agg(
        dict(open='first',high='max',low='min',close='last',volume='sum')).dropna()
    w['weekly_ema10']=w.close.ewm(span=10,adjust=False).mean()
    w['weekly_ema20']=w.close.ewm(span=20,adjust=False).mean()
    w['weekly_support']=w.low.rolling(p.support_weeks).min()
    w['weekly_ext']=(w.close-w.weekly_ema10)/w.close
    # Label availability, not observation: Friday week can inform Monday,
    # including Thursday-final holiday weeks, never earlier days of that week.
    w['weekly_available']=w.index+pd.Timedelta(days=1)
    w.index=w.weekly_available
    for col in ['weekly_ema10','weekly_ema20','weekly_support','weekly_ext','weekly_available']:
        x[col]=w[col].reindex(x.index,method='ffill')
    return x


def detect(raw,p):
    x=features(raw,p)
    out=[]
    state='DOWNTREND'; rev=-1; wp=-1; cross=-1; last_base=-1
    bases=0; extensions=0; cycle=0; revlow=np.nan; stop=np.nan
    last_support=np.nan; extension_armed=True
    rows=list(x.itertuples())
    for i,r in enumerate(rows):
        date=r.Index
        before=state; event=''; order=None; reason=''; pivot=np.nan
        ready=(i>=max(100,p.atr_bars,p.volume_bars,2*p.pivot_bars) and
               all(np.isfinite(v) for v in [r.open,r.high,r.low,r.close,r.volume]) and
               min(r.open,r.high,r.low,r.close)>0 and pd.notna(r.weekly_support) and np.isfinite(r.atr) and r.atr>0)
        active=state in ['WEDGE_POP','EMA_CROSSBACK','BASE_N_BREAK','EXHAUSTION_EXTENSION']
        if ready:
            prior=rows[i-1]
            # Risk failure may abort any incomplete cycle; never fabricate
            # missing source stages just because twelve/twenty days elapsed.
            drop=(active and r.close<min(r.ema10,r.ema20) and r.close<r.pivot_lo)
            failed=(active and np.isfinite(stop) and r.close<stop)
            if drop or failed:
                if drop and extensions>0:
                    state='WEDGE_DROP';event='WEDGE_DROP'
                else:
                    state='DOWNTREND';event='CYCLE_FAILURE'
                order=0.0;reason=event
                rev=wp=cross=last_base=-1;bases=extensions=0
                stop=np.nan;extension_armed=True
            elif state in ['DOWNTREND','WEDGE_DROP']:
                support=min(abs(r.low-level) for level in
                            [r.weekly_support,r.weekly_ema10,r.weekly_ema20])
                clv=(r.close-r.low)/max(r.high-r.low,1e-12)
                reversal=(r.ema10<r.ema20 and r.close<r.ema20 and
                          r.high<r.ema10 and (r.ema10-r.low)/r.atr>=p.reversal_atr and
                          clv>=p.reversal_clv and r.volume_ratio>=p.reversal_volume and
                          support<=p.support_tolerance_atr*r.atr)
                if reversal:
                    state='REVERSAL_EXTENSION';event='REVERSAL_EXTENSION'
                    rev=i;revlow=r.low;cycle+=1
            elif state=='REVERSAL_EXTENSION':
                if i-rev>p.reversal_timeout or r.close<revlow-p.stop_atr*r.atr:
                    state='DOWNTREND';event='REVERSAL_FAILED';rev=-1
                elif i-rev>=p.pivot_bars:
                    b=x.iloc[i-p.pivot_bars:i]
                    tight=abs(prior.ema10-prior.ema20)<=p.ema_band_atr*prior.atr
                    reclaim=r.close>max(r.ema10,r.ema20) and (b.close<=b.ema20).any()
                    if (reclaim and tight and r.contraction_ratio<=p.contraction and
                        r.close>r.pivot_hi and r.volume_ratio>=p.breakout_volume and
                        b.low.min()>=revlow):
                        state='WEDGE_POP';event='WEDGE_POP';wp=i
                        bases=extensions=0;cross=last_base=-1;extension_armed=True
                        stop=b.low.min()-p.stop_atr*r.atr;last_support=b.low.min()
                        pivot=r.pivot_hi
                        if p.entry_stage=='WEDGE_POP':order=1.0;reason=event
            elif active:
                if cross<0 and i-wp>=p.crossback_delay:
                    touched=r.low<=max(r.ema10,r.ema20)+p.touch_atr*r.atr
                    supported=r.close>r.ema20 and (r.close>r.open or r.close>prior.close)
                    if touched and supported:
                        cross=i;state='EMA_CROSSBACK';event='EMA_CROSSBACK'
                        last_support=r.low;stop=max(stop,r.low-p.stop_atr*r.atr)
                        if p.entry_stage=='EMA_CROSSBACK':order=1.0;reason=event
                elif cross>=0:
                    anchor=max(cross,last_base)
                    if i-anchor>p.base_bars:
                        b=x.iloc[i-p.base_bars:i]
                        old=x.iloc[i-2*p.base_bars:i-p.base_bars]
                        support=(b.close>=b.ema20).mean()>=p.base_support_fraction
                        touch=(b.low<=b.ema10+p.touch_atr*b.atr).any()
                        tight=(b.high.max()-b.low.min())<=p.base_width_atr*prior.atr
                        contract=b['range'].mean()<=old['range'].mean()*p.contraction
                        higher=b.low.min()>=last_support-p.touch_atr*prior.atr
                        if (support and touch and tight and contract and higher and
                            r.close>b.high.max() and r.close>max(r.ema10,r.ema20) and
                            r.volume_ratio>=p.breakout_volume):
                            bases+=1;last_base=i;state='BASE_N_BREAK';event=f'BASE_N_BREAK_{bases}'
                            pivot=b.high.max();last_support=b.low.min()
                            stop=max(stop,last_support-p.stop_atr*r.atr)
                            extension_armed=True
                            if p.entry_stage=='BASE_N_BREAK' and bases==1:order=1.0;reason=event
                    if not event and bases>0:
                        if r.low<=r.ema10+p.extension_reset_atr*r.atr:
                            extension_armed=True
                        ext=((r.low-r.ema10)/r.atr>=p.extension_atr and
                             r.weekly_ext>=p.weekly_extension and
                             r.weekly_ema10>r.weekly_ema20)
                        if ext and extension_armed:
                            extensions+=1;extension_armed=False
                            state='EXHAUSTION_EXTENSION';event=f'EXHAUSTION_EXTENSION_{extensions}'
                            if extensions==p.trim_extension and p.trim_fraction>0:
                                order=1.0-p.trim_fraction;reason=event
                # Source EMAs trail an existing cycle; breaches checked above
                # against yesterday's stop, never a future or same-bar level.
                stop=max(stop,r.ema20-p.stop_atr*r.atr)
        out.append(dict(date=date,state_before=before,cpa_state=state,cpa_event=event,
                        cycle_id=cycle,base_count=bases,extension_count=extensions,
                        signal_target=order,signal_reason=reason,signal_stop=stop,
                        pivot=pivot,features_ready=bool(ready)))
    return x.join(pd.DataFrame(out).set_index('date'))


def simulate(execution,events,start,end,cost=0.0005,benchmark=False):
    """Next-open cash/share ledger, fully marking overnight and exit returns.

    Initial capital is one dollar; fractional shares make results independent
    of arbitrary account size. Only new events inside this partition trade.
    Trims are sell-only and cannot open a position. Terminal trades are censored.
    """
    ex=execution.loc[start:end]
    if len(ex)<2:raise ValueError('Need at least two sessions')
    if not ex.index.isin(events.index).all():raise ValueError('Missing signal session')
    ev=events.reindex(ex.index)
    cash=1.; shares=0.; previous=1.; pending=None; trade=None
    ledger=[];fills=[];trades=[]
    last_close=np.nan
    previous_rows=execution.loc[execution.index<pd.Timestamp(start),'close']
    valid_previous=previous_rows[np.isfinite(previous_rows)&(previous_rows>0)]
    if len(valid_previous):last_close=float(valid_previous.iloc[-1])
    event_rows=list(ev.itertuples())
    for j,r in enumerate(ex.itertuples()):
        date=r.Index
        open_valid=np.isfinite(r.open) and r.open>0
        close_valid=np.isfinite(r.close) and r.close>0
        mark=float(r.close) if close_valid else last_close
        if shares>0 and not np.isfinite(mark):raise ValueError('No causal valuation available')
        before_open=cash+(shares*(r.open if open_valid else last_close) if shares else 0.)
        fees_today=0.;turnover=0.
        if benchmark and shares==0 and trade is None and not fills:pending=(1.,'BUY_HOLD',None,-1)
        deferred=None
        if pending is not None and not open_valid:
            if pending[0]<1.:deferred=pending
            pending=None
        if pending is not None:
            weight,reason,signal_date,cycle=pending
            if reason.startswith('EXHAUSTION') and shares<=0:
                pending=None
            else:
                # Target is measured against post-cost NAV at this open.
                holding=shares*r.open
                target=weight*before_open
                if target>holding:
                    dollars=(target-holding)/(1+weight*cost)
                    qty=dollars/r.open
                else:
                    dollars=(holding-target)/(1-weight*cost)
                    qty=-dollars/r.open
                if reason.startswith('EXHAUSTION') and qty>0:qty=0.
                if abs(qty)>1e-14:
                    notional=qty*r.open;fee=abs(notional)*cost
                    if shares<=1e-14 and qty>0:
                        trade=dict(entry_date=date,entry_session=j,entry_signal_date=signal_date,
                                   cycle_id=cycle,cash_before=cash,net_cash_flow=0.,
                                   entry_nav=before_open,entry_price=r.open,
                                   entry_reason=reason,fills=0)
                    cash-=notional+fee;shares+=qty
                    if abs(shares)<1e-12:shares=0.
                    fees_today+=fee;turnover+=abs(notional)
                    fills.append(dict(date=date,signal_date=signal_date,cycle_id=cycle,
                                      reason=reason,quantity=qty,reference_price=r.open,fill_basis='OPEN',
                                      effective_price=r.open*(1+cost*np.sign(qty)),
                                      fee=fee,cash=cash,shares=shares))
                    if trade is not None:
                        trade['net_cash_flow']-=notional+fee;trade['fills']+=1
                        if shares==0:
                            tr=dict(trade,exit_date=date,exit_reason=reason,censored=False,
                                    duration_sessions=j-trade['entry_session'],
                                    duration_calendar_days=(date-trade['entry_date']).days,
                                    pnl=trade['net_cash_flow'],return_on_entry_nav=trade['net_cash_flow']/trade['entry_nav'])
                            trades.append(tr);trade=None
                pending=None
        equity=cash+(shares*mark if shares else 0.)
        if close_valid:last_close=float(r.close)
        pending=deferred
        if cash<-1e-10 or shares<-1e-10:raise AssertionError('Borrowed cash or negative shares')
        if j==len(ex)-1 and shares>0:
            if not close_valid:raise ValueError('Cannot liquidate at a missing terminal close')
            notional=shares*r.close;fee=notional*cost
            fills.append(dict(date=date,signal_date=None,cycle_id=trade['cycle_id'],
                              reason='TERMINAL_LIQUIDATION',quantity=-shares,
                              reference_price=r.close,fill_basis='TERMINAL_CLOSE',effective_price=r.close*(1-cost),
                              fee=fee,cash=cash+notional-fee,shares=0.))
            trade['net_cash_flow']+=notional-fee;trade['fills']+=1
            trades.append(dict(trade,exit_date=date,exit_reason='TERMINAL_LIQUIDATION',
                               duration_sessions=j-trade['entry_session'],
                               duration_calendar_days=(date-trade['entry_date']).days,
                               censored=True,pnl=trade['net_cash_flow'],
                               return_on_entry_nav=trade['net_cash_flow']/trade['entry_nav']))
            fees_today+=fee;turnover+=notional;cash+=notional-fee;shares=0.;equity=cash
        ledger.append(dict(date=date,equity=equity,return_daily=equity/previous-1,
                           cash=cash,shares=shares,exposure=(equity-cash)/equity,
                           equity_before_open=before_open,cost=fees_today,
                           turnover=turnover/previous,stale_close=bool(not close_valid),
                           unavailable_open=bool(not open_valid)))
        previous=equity
        if j<len(ex)-1 and not benchmark:
            row=event_rows[j]
            if pd.notna(row.signal_target) and not (pending is not None and pending[0]==0.):
                pending=(float(row.signal_target),row.signal_reason,date,int(row.cycle_id))
    lg=pd.DataFrame(ledger).set_index('date')
    fl=pd.DataFrame(fills);tr=pd.DataFrame(trades)
    pnl=tr.pnl.sum() if len(tr) else 0.
    if not np.isclose(lg.equity.iloc[-1]-1,pnl,rtol=1e-9,atol=1e-10):
        raise AssertionError('Trades do not reconcile with account equity')
    return lg,tr,fl


def metrics(ledger,trades):
    r=ledger.return_daily.to_numpy();n=len(r)
    years=((ledger.index[-1]-ledger.index[0]).days+1)/365.25
    final=float(ledger.equity.iloc[-1]);cagr=final**(1/years)-1
    path=np.r_[1.,ledger.equity.to_numpy()]
    dd=float((path/np.maximum.accumulate(path)-1).min())
    std=np.std(r,ddof=1);mean=np.mean(r)
    sh=float(mean/std*np.sqrt(252)) if std>0 else 0.
    downside=np.sqrt(np.mean(np.minimum(r,0)**2))
    so=float(mean/downside*np.sqrt(252)) if downside>0 else None
    z=r-mean;variance=np.dot(z,z)/n
    # Fixed Newey-West lag 5, descriptive weekly autocorrelation adjustment.
    for lag in range(1,min(6,n)):
        variance+=2*(1-lag/6)*np.dot(z[lag:],z[:-lag])/n
    hac=float(mean/math.sqrt(variance)*math.sqrt(252)) if variance>0 else 0.
    completed=trades[~trades.censored] if len(trades) else pd.DataFrame()
    pnl=completed.pnl if len(completed) else pd.Series(dtype=float)
    wins=float(pnl[pnl>0].sum());losses=float(-pnl[pnl<0].sum())
    pf=wins/losses if losses>0 else (None if wins>0 else 0.)
    halves=[]
    for arr in np.array_split(r,2):
        sd=np.std(arr,ddof=1) if len(arr)>1 else 0.
        halves.append(dict(total_return=float(np.prod(1+arr)-1),
                           sharpe=float(np.mean(arr)/sd*np.sqrt(252)) if sd>0 else 0.))
    return dict(CAGR=cagr,TotalReturn=final-1,MaxDD=dd,Sharpe=sh,HACSharpe=hac,
                Sortino=so,Calmar=cagr/abs(dd) if dd<0 else None,
                Trades=len(completed),IndependentCycles=int(completed.cycle_id.nunique()) if len(completed) else 0,
                CensoredTrades=len(trades)-len(completed),ProfitFactor=pf,
                MedianTradeSessions=float(completed.duration_sessions.median()) if len(completed) else None,
                GrossProfit=wins,GrossLoss=losses,
                WinRate=float((pnl>0).mean()) if len(pnl) else None,
                ProfitConcentration=float(pnl.max()/wins) if wins>0 else None,
                Exposure=float(ledger.exposure.mean()),Turnover=float(ledger.turnover.sum()),
                Costs=float(ledger.cost.sum()),Halves=halves,
                CalendarYearReturns={str(year):float(np.prod(1+group.return_daily)-1)
                                     for year,group in ledger.groupby(ledger.index.year)})
