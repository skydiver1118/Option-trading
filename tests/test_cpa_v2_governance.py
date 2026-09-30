"""One-way stage and artifact-integrity contracts, without real OOS access."""
import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import cpa_v2_study as study


class GovernanceTests(unittest.TestCase):
    def test_failed_gate_does_not_start_is_or_read_price_partitions(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(study,'OUT',Path(tmp)),patch.object(study,'gate',side_effect=RuntimeError('blocked')),patch.object(study,'load_stage') as load:
                with self.assertRaisesRegex(RuntimeError,'blocked'):study.run_is()
                self.assertFalse((Path(tmp)/'IS_STARTED.json').exists());load.assert_not_called()

    def test_empty_oos_batch_does_not_read_prices_and_cannot_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);vis=root/'visual_review.json';vis.write_text('{}')
            ism=root/'IS_frozen_candidates.json';ism.write_text('{}')
            val=root/'validation_results.json';val.write_text('[]')
            freeze=dict(snapshot={},IS_manifest_sha256=study.sha(ism),validation_sha256=study.sha(val),
                        visual_sha256=study.sha(vis),candidates=[])
            (root/'frozen_manifest.json').write_text(json.dumps(freeze))
            with patch.object(study,'OUT',root),patch.object(study,'STUDY',root),patch.object(study,'gate'),patch.object(study,'check_snapshot'),patch.object(study,'load_stage') as load:
                study.run_oos();load.assert_not_called()
                with self.assertRaises(FileExistsError):study.run_oos()
                self.assertTrue((root/'OOS_STARTED.json').exists())

    def test_changed_hash_stops_progress(self):
        with patch.object(study,'snapshot',return_value={'prices.csv':'changed'}):
            with self.assertRaisesRegex(RuntimeError,'changed'):study.check_snapshot({'prices.csv':'frozen'})

    def test_validation_rejection_does_not_relax_constraints(self):
        v=dict(IndependentCycles=5,CAGR=.4,Sharpe=2.,MaxDD=-.1,ProfitFactor=2.,
               GrossProfit=.5,GrossLoss=.25,ProfitConcentration=.4,
               Halves=[dict(total_return=.1),dict(total_return=.1)])
        checks=study.validation_pass(v,dict(Sharpe=1.5,MaxDD=-.15),dict(CAGR=.3))
        self.assertFalse(checks['cycles']);self.assertFalse(all(checks.values()))


if __name__=='__main__':unittest.main()
