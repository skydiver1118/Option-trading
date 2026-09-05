"""Check specific IS data defects directly with Tradier; never score returns."""
import json, os, hashlib
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from datetime import datetime, timezone

CASES = [('SPY','2016-12-14','2016-12-16'),
         ('SPMO','2016-09-01','2016-09-30'),
         ('SPMO','2017-01-01','2017-12-31'),
         ('VGT','2016-03-01','2016-04-08'),
         ('SMH','2018-12-18','2018-12-21'),
         ('QQQ','2016-04-04','2016-04-06'),
         ('TQQQ','2016-04-04','2016-04-06'),
         ('SOXL','2016-12-16','2016-12-21')]


def main():
    out=Path('data/cpa_v2_20260904_requery');out.mkdir(parents=True,exist_ok=True)
    records=[]
    for symbol,start,end in CASES:
        url='https://api.tradier.com/v1/markets/history?'+urlencode(dict(symbol=symbol,interval='daily',start=start,end=end))
        req=Request(url,headers={'Authorization':'Bearer '+os.environ['TRADIER_TOKEN'],'Accept':'application/json'})
        with urlopen(req,timeout=60) as res: payload=res.read()
        filename=f'{symbol}_{start}_{end}.json';(out/filename).write_bytes(payload)
        records.append(dict(file=filename,endpoint=url,retrieved_at=datetime.now(timezone.utc).isoformat(),sha256=hashlib.sha256(payload).hexdigest()))
    (out/'provenance.json').write_text(json.dumps(records,indent=2))
    print('Completed',len(records),'Tradier data integrity queries; no performance computed.')

if __name__=='__main__':main()
