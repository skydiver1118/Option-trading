"""Capture only Tradier OHLCV; no indicators, backtests, or brokerage actions."""
import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

END = '2026-09-04'
SYMBOLS = ['SPY', 'SPMO', 'VGT', 'SMH', 'QQQ', 'TQQQ', 'SOXL']
EXAMPLES = ['TSLA', 'NOW', 'LLY', 'NIO', 'NET']


def main():
    token = os.environ.get('TRADIER_TOKEN')
    if not token:
        raise SystemExit('TRADIER_TOKEN is required; no alternate data provider permitted')
    out = Path('artifacts/cpa_v2_tradier_capture')
    out.mkdir(parents=True, exist_ok=True)
    records = []
    for symbol in SYMBOLS + EXAMPLES:
        params = dict(symbol=symbol, interval='daily',
                      start='2015-01-01' if symbol in SYMBOLS else '2018-01-01',
                      end=END if symbol in SYMBOLS else '2021-12-31')
        url = 'https://api.tradier.com/v1/markets/history?' + urlencode(params)
        request = Request(url, headers={'Authorization': 'Bearer ' + token,
                                       'Accept': 'application/json'})
        with urlopen(request, timeout=60) as response:
            payload = response.read()
        rows = (json.loads(payload).get('history') or {}).get('day')
        if isinstance(rows, dict):
            rows = [rows]
        if not rows:
            raise RuntimeError('Tradier returned no daily data for ' + symbol)
        raw = out / (symbol + '_response.json')
        raw.write_bytes(payload)
        rows = sorted(rows, key=lambda row: row['date'])
        with (out / (symbol + '.csv')).open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=['date', 'open', 'high', 'low', 'close', 'volume'], extrasaction='ignore')
            writer.writeheader()
            writer.writerows(rows)
        records.append(dict(symbol=symbol, provider='Tradier', endpoint=url,
                            retrieved_at=datetime.now(timezone.utc).isoformat(),
                            first=rows[0]['date'], last=rows[-1]['date'], rows=len(rows),
                            response_sha256=hashlib.sha256(payload).hexdigest(),
                            purpose='study' if symbol in SYMBOLS else 'IS-era morphology only'))
    (out / 'provenance.json').write_text(json.dumps(records, indent=2))
    print(json.dumps(records, indent=2))


if __name__ == '__main__':
    main()
