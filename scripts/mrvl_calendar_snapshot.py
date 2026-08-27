from __future__ import annotations
import json, os
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')
TRADIER_BASE = 'https://api.tradier.com/v1'
TOKEN = os.getenv('TRADIER_TOKEN', '').strip()
SYMBOL = 'MRVL'
FRONT = '2026-08-28'
BACK = '2026-09-04'
TARGETS = [215, 220, 225, 260, 265, 270]


def get(path, params):
    if not TOKEN:
        raise RuntimeError('TRADIER_TOKEN not configured')
    req = Request(
        f"{TRADIER_BASE}{path}?{urlencode(params)}",
        headers={
            'Authorization': f'Bearer {TOKEN}',
            'Accept': 'application/json',
            'User-Agent': 'option-dashboard/1.0',
        },
    )
    with urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def quote(symbol):
    j = get('/markets/quotes', {'symbols': symbol, 'greeks': 'false'})
    q = ((j or {}).get('quotes') or {}).get('quote') or {}
    return q[0] if isinstance(q, list) else q


def chain(expiry):
    j = get('/markets/options/chains', {'symbol': SYMBOL, 'expiration': expiry, 'greeks': 'true'})
    opts = ((j or {}).get('options') or {}).get('option') or []
    return [opts] if isinstance(opts, dict) else opts


def clean_option(o):
    g = o.get('greeks') or {}
    bid = float(o.get('bid') or 0)
    ask = float(o.get('ask') or 0)
    mid = (bid + ask) / 2 if bid > 0 and ask > 0 else max(bid, ask)
    iv = g.get('mid_iv') if g.get('mid_iv') is not None else g.get('smv_vol')
    return {
        'symbol': o.get('symbol'),
        'strike': float(o.get('strike')),
        'type': str(o.get('option_type', '')).lower(),
        'bid': bid,
        'ask': ask,
        'mid': round(mid, 4),
        'last': o.get('last'),
        'volume': o.get('volume'),
        'open_interest': o.get('open_interest'),
        'iv': iv,
        'delta': g.get('delta'),
        'gamma': g.get('gamma'),
        'theta': g.get('theta'),
        'vega': g.get('vega'),
    }


def index_chain(opts):
    out = {}
    for raw in opts:
        try:
            o = clean_option(raw)
            key = (o['type'], int(round(o['strike'])))
            if key[1] in TARGETS:
                out[key] = o
        except Exception:
            pass
    return out


def calendar_row(kind, strike, front, back):
    f = front.get((kind, strike))
    b = back.get((kind, strike))
    if not f or not b:
        return {'type': kind, 'strike': strike, 'missing': True}
    # Buy back expiry, sell front expiry.
    mid_debit = b['mid'] - f['mid']
    natural_debit = b['ask'] - f['bid']
    optimistic_debit = b['bid'] - f['ask']
    return {
        'type': kind,
        'strike': strike,
        'front': f,
        'back': b,
        'calendar_mid_debit': round(mid_debit, 4),
        'calendar_natural_debit': round(natural_debit, 4),
        'calendar_optimistic_debit': round(optimistic_debit, 4),
    }


def main():
    now = datetime.now(ET)
    q = quote(SYMBOL)
    front = index_chain(chain(FRONT))
    back = index_chain(chain(BACK))
    rows = []
    for strike in TARGETS:
        kind = 'put' if strike <= 225 else 'call'
        rows.append(calendar_row(kind, strike, front, back))

    by = {(r['type'], r['strike']): r for r in rows if not r.get('missing')}
    structures = []
    for put_strike, call_strike in [(225, 260), (220, 265), (215, 270)]:
        p = by.get(('put', put_strike))
        c = by.get(('call', call_strike))
        if not p or not c:
            structures.append({'put_strike': put_strike, 'call_strike': call_strike, 'missing': True})
            continue
        structures.append({
            'put_strike': put_strike,
            'call_strike': call_strike,
            'combined_mid_debit': round(p['calendar_mid_debit'] + c['calendar_mid_debit'], 4),
            'combined_natural_debit': round(p['calendar_natural_debit'] + c['calendar_natural_debit'], 4),
            'put_calendar_mid_debit': p['calendar_mid_debit'],
            'call_calendar_mid_debit': c['calendar_mid_debit'],
        })

    payload = {
        'updated_et': now.strftime('%Y-%m-%d %I:%M:%S %p ET'),
        'source': 'Tradier production API',
        'symbol': SYMBOL,
        'underlying': {
            'last': q.get('last'),
            'bid': q.get('bid'),
            'ask': q.get('ask'),
            'close': q.get('close'),
        },
        'front_expiration': FRONT,
        'back_expiration': BACK,
        'calendar_definition': 'Buy back-expiry option and sell same-strike front-expiry option',
        'rows': rows,
        'structures': structures,
    }
    os.makedirs('data', exist_ok=True)
    with open('data/mrvl_calendar.json', 'w') as fh:
        json.dump(payload, fh, indent=2)
    print(json.dumps(payload, indent=2))


if __name__ == '__main__':
    main()
