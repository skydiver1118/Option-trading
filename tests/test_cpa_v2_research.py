"""Synthetic bookkeeping/causality tests; never performance evidence."""
import sys
from pathlib import Path
import unittest
import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from cpa_v2_engine import Parameters,features,detect,simulate,metrics,configurations


def bars(n=400):
    dates=pd.bdate_range('2019-01-01',periods=n)
    c=100+np.arange(n)*.05+4*np.sin(np.arange(n)/10)
    return pd.DataFrame(dict(open=c-.1,high=c+1,low=c-1,close=c,volume=10000),index=dates)


def signals(index,orders):
    x=pd.DataFrame(dict(signal_target=np.nan,signal_reason='',cycle_id=1),index=index)
    for j,w,reason in orders:x.loc[index[j],['signal_target','signal_reason']]=[w,reason]
    return x


class LedgerTests(unittest.TestCase):
    def test_overnight_exit_loss_and_cost_reconcile(self):
        x=bars(4);x['open']=[100.,100.,90.,90.];x['close']=[100.,110.,90.,90.]
        e=signals(x.index,[(0,1.,'WEDGE_POP'),(1,0.,'CYCLE_FAILURE')])
        lg,tr,fl=simulate(x,e,x.index[0],x.index[-1],cost=.001)
        self.assertAlmostEqual(lg.equity.iloc[-1],.9*.999/1.001)
        self.assertLess(lg.return_daily.iloc[2],-.18)
        self.assertEqual(len(fl),2);self.assertFalse(tr.censored.iloc[0])
        self.assertEqual(fl.date.iloc[0],x.index[1])
        self.assertEqual(fl.signal_date.iloc[0],x.index[0])
        self.assertAlmostEqual(tr.pnl.sum(),lg.equity.iloc[-1]-1)

    def test_partial_exit_has_real_cash_and_costs(self):
        x=bars(5);x['open']=[100.,100.,120.,60.,60.];x['close']=x.open
        e=signals(x.index,[(0,1.,'WEDGE_POP'),(1,.5,'EXHAUSTION_EXTENSION_2'),(2,0.,'WEDGE_DROP')])
        lg,tr,fl=simulate(x,e,x.index[0],x.index[-1],cost=0)
        self.assertAlmostEqual(lg.cash.iloc[2],.6)
        self.assertAlmostEqual(lg.equity.iloc[-1],.9)
        self.assertEqual(len(tr),1);self.assertEqual(len(fl),3)
        self.assertEqual(metrics(lg,tr)['IndependentCycles'],1)

    def test_trim_cannot_open_or_add(self):
        x=bars(5);e=signals(x.index,[(0,.5,'EXHAUSTION_EXTENSION_2')])
        lg,tr,fl=simulate(x,e,x.index[0],x.index[-1])
        self.assertEqual(len(fl),0);self.assertTrue((lg.equity==1).all())

    def test_no_free_daily_rebalance(self):
        x=bars(6);e=signals(x.index,[(0,1.,'WEDGE_POP'),(1,.5,'EXHAUSTION_EXTENSION_2')])
        lg,tr,fl=simulate(x,e,x.index[0],x.index[-1])
        self.assertEqual(lg.shares.iloc[2],lg.shares.iloc[4])
        self.assertTrue(tr.censored.iloc[-1]);self.assertEqual(metrics(lg,tr)['Trades'],0)

    def test_partition_starts_flat(self):
        x=bars(7);e=signals(x.index,[(0,1.,'WEDGE_POP')])
        lg,tr,fl=simulate(x,e,x.index[2],x.index[-1])
        self.assertEqual(len(fl),0)

    def test_signal_asset_price_never_used_as_execution_price(self):
        x=bars(5);e=signals(x.index,[(0,1.,'WEDGE_POP'),(2,0.,'WEDGE_DROP')])
        a=simulate(x,e,x.index[0],x.index[-1])[0]
        y=x.copy();y[['open','high','low','close']]*=17
        b=simulate(y,e,y.index[0],y.index[-1])[0]
        np.testing.assert_allclose(a.equity,b.equity)

    def test_terminal_cost_and_first_loss_in_drawdown(self):
        x=bars(3);x[['open','close']]=100.
        lg,tr,fl=simulate(x,signals(x.index,[]),x.index[0],x.index[-1],cost=.001,benchmark=True)
        self.assertAlmostEqual(lg.equity.iloc[-1],.999/1.001)
        self.assertAlmostEqual(metrics(lg,tr)['MaxDD'],.999/1.001-1)


class CausalityTests(unittest.TestCase):
    def test_reference_is_cycle_has_real_ordered_transitions(self):
        path=Path(__file__).resolve().parents[1]/'data/cpa_v2_20260904_source/TSLA.csv'
        x=pd.read_csv(path,parse_dates=['date']).set_index('date')
        p=Parameters();a=detect(x,p).loc['2020-01-01':'2020-12-31']
        events=a[a.cpa_event!=''].cpa_event.tolist()
        self.assertEqual(events,['REVERSAL_EXTENSION','WEDGE_POP','EMA_CROSSBACK',
                                'BASE_N_BREAK_1','EXHAUSTION_EXTENSION_1','BASE_N_BREAK_2',
                                'EXHAUSTION_EXTENSION_2','WEDGE_DROP'])
        # This checks an active real-data state, not only a vacuous no-event prefix.
        truncated=detect(x.loc[:'2020-06-15'],p)
        pd.testing.assert_frame_equal(a.loc[:'2020-06-15'],truncated.loc['2020-01-01':],check_freq=False)

    def test_prefix_features_and_states_are_identical(self):
        x=bars(650);p=Parameters(support_weeks=13)
        whole=detect(x,p)
        for cutoff in [151,277,439,603]:
            prefix=detect(x.iloc[:cutoff],p)
            pd.testing.assert_frame_equal(whole.iloc[:cutoff],prefix,check_freq=False)

    def test_future_mutation_cannot_change_past(self):
        x=bars(650);p=Parameters(support_weeks=13)
        before=detect(x,p);y=x.copy();y.iloc[401:,y.columns.get_indexer(['open','high','low','close'])]*=9
        after=detect(y,p)
        pd.testing.assert_frame_equal(before.iloc[:401],after.iloc[:401])

    def test_weekly_features_not_from_current_friday(self):
        x=bars(400);p=Parameters(support_weeks=13);a=features(x,p)
        for date in x.index[100:]:
            if pd.notna(a.loc[date,'weekly_available']):
                self.assertLessEqual(a.loc[date,'weekly_available'],date)
        friday=next(d for d in x.index[200:] if d.dayofweek==4)
        y=x.copy();y.loc[friday,'close']*=2;b=features(y,p)
        self.assertEqual(a.loc[friday,'weekly_ema10'],b.loc[friday,'weekly_ema10'])
        monday=x.index[x.index>friday][0]
        self.assertNotEqual(a.loc[monday,'weekly_ema10'],b.loc[monday,'weekly_ema10'])

    def test_pivot_and_volume_baseline_exclude_today(self):
        x=bars(400);p=Parameters();a=features(x,p)
        y=x.copy();y.iloc[-1,y.columns.get_loc('high')]*=2;y.iloc[-1,y.columns.get_loc('volume')]*=10
        b=features(y,p)
        self.assertEqual(a.pivot_hi.iloc[-1],b.pivot_hi.iloc[-1])
        self.assertEqual(a.volume_ref.iloc[-1],b.volume_ref.iloc[-1])

    def test_search_reproducible_and_within_registered_space(self):
        a=configurations();b=configurations()
        self.assertEqual([p.id for p in a],[p.id for p in b]);self.assertEqual(len(a),256)


if __name__=='__main__':unittest.main()
