"""Offline parity tests: existing daily service vs frozen replication source."""
import ast
import importlib.util
import os
from pathlib import Path
import sys
import unittest
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
os.environ['TCAR_MODE'] = 'dryrun'
spec = importlib.util.spec_from_file_location('tcar_parity_bot', ROOT/'scripts/tcar_daily_tradier_bot.py')
bot = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bot
spec.loader.exec_module(bot)

class ReplicationParity(unittest.TestCase):
    def test_indicator_arithmetic_matches_reference(self):
        path = ROOT/'scripts/soxl_daily_tcar_yahoo_vs_tradier_10y.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        prep = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'prep')
        ns = {'pd':pd, 'np':np}
        exec(compile(ast.Module(body=[prep], type_ignores=[]), str(path), 'exec'), ns)
        rng = np.random.default_rng(456)
        c = 30*np.exp(np.cumsum(rng.normal(0, .035, 280)))
        o = c*np.exp(rng.normal(0, .01, 280))
        data = pd.DataFrame({'open':o,'high':np.maximum(o,c)*1.03,
            'low':np.minimum(o,c)*.97,'close':c}, index=pd.bdate_range('2024-01-01', periods=280))
        ref, actual = ns['prep'](data), bot.indicators(data)
        for old,new in [('wr','wr2'),('cci','cci5'),('adx20','adx20'),('prev_high','prev_high')]:
            np.testing.assert_allclose(ref[old], actual[new], rtol=1e-12, atol=1e-12, equal_nan=True)

    def test_three_factor_entry_strict_boundaries(self):
        row = pd.Series({'wr2':-91,'cci5':-81,'adx20':15,'close':20,'prev_high':21})
        self.assertEqual(bot.signal_for_row(row,False)[0], 'buy')
        for k,value in [('wr2',-90),('cci5',-80),('adx20',14.999)]:
            changed = row.copy(); changed[k] = value
            self.assertIsNone(bot.signal_for_row(changed,False)[0])

    def test_cci_is_not_an_exit(self):
        row = pd.Series({'wr2':-40,'cci5':150,'adx20':20,'close':20,'prev_high':21})
        self.assertIsNone(bot.signal_for_row(row,True)[0])

    def test_price_or_wr_exit(self):
        row = pd.Series({'wr2':-40,'cci5':-150,'adx20':20,'close':22,'prev_high':21})
        self.assertEqual(bot.signal_for_row(row,True)[0],'sell')
        row['close']=20; row['wr2']=-29.9
        self.assertEqual(bot.signal_for_row(row,True)[0],'sell')

    def test_daily_constants_not_intraday(self):
        self.assertEqual((bot.WR_N,bot.WR_ENTRY,bot.WR_EXIT),(2,-90.,-30.))
        self.assertEqual((bot.CCI_N,bot.CCI_ENTRY),(5,-80.))
        self.assertEqual((bot.ADX_N,bot.ADX_ENTRY),(20,15.))
        self.assertEqual(bot.TAG_PREFIX,'TCAR-')
        self.assertIn('DAILY',bot.default_state()['strategy'])

if __name__ == '__main__':
    unittest.main()
