#!/usr/bin/env python3
"""GET-only connectivity check for daily SOXL TCAR. Never submits orders."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')
BASES = {'sandbox': 'https://sandbox.tradier.com/v1', 'market': 'https://api.tradier.com/v1'}
TOKENS = {'sandbox': 'TRADIER_SANDBOX_TOKEN', 'market': 'TRADIER_TOKEN'}


class CheckError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CheckError('REDIRECT_BLOCKED')


def get_json(env: str, path: str, params=None):
    allowed = (env == 'sandbox' and (path == '/user/profile' or re.fullmatch(r'/accounts/[A-Za-z0-9_-]+/(balances|positions|orders)', path))) or (env == 'market' and path in ('/markets/quotes', '/markets/history'))
    if not allowed:
        raise CheckError('ENDPOINT_NOT_ALLOWLISTED')
    token = os.environ.get(TOKENS[env], '').strip()
    if not token:
        raise CheckError('SECRET_MISSING')
    url = BASES[env] + path + (('?' + urlencode(params)) if params else '')
    req = Request(url, headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}, method='GET')
    try:
        with build_opener(NoRedirect()).open(req, timeout=25) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise CheckError(f'HTTP_{int(exc.code)}') from None
    except (URLError, TimeoutError, OSError):
        raise CheckError('NETWORK_OR_TLS_ERROR') from None
    if not isinstance(payload, dict) or payload.get('errors') or payload.get('fault'):
        raise CheckError('API_RESPONSE_ERROR')
    return payload


def rows(parent, key):
    if parent in (None, {}, 'null'):
        return []
    value = parent.get(key) if isinstance(parent, dict) else None
    if value in (None, [], 'null'):
        return []
    return [value] if isinstance(value, dict) else value


def main():
    report = {'checked_at_et': datetime.now(ET).isoformat(), 'check_type': 'TCAR_DAILY_READ_ONLY',
              'orders_submitted': 0, 'bot_started': False, 'http_methods': ['GET'], 'checks': {}}
    checks = report['checks']
    for key in TOKENS.values():
        checks[key] = 'PRESENT' if os.environ.get(key, '').strip() else 'MISSING'
    account = None
    try:
        profile = get_json('sandbox', '/user/profile')
        accounts = rows(profile.get('profile') or {}, 'account')
        active = [a for a in accounts if str(a.get('status', '')).lower() == 'active']
        checks['sandbox_authentication'] = 'PASS'
        if len(active) == 1 and re.fullmatch(r'[A-Za-z0-9_-]+', str(active[0].get('account_number', ''))):
            account = str(active[0]['account_number'])
            checks['sandbox_account_resolution'] = 'PASS'
        else:
            checks['sandbox_account_resolution'] = 'MULTIPLE_OR_NO_ACTIVE_ACCOUNT'
    except CheckError as exc:
        checks['sandbox_authentication'] = str(exc)
    if account:
        for endpoint in ('balances', 'positions', 'orders'):
            try:
                payload = get_json('sandbox', f'/accounts/{account}/{endpoint}')
                if endpoint not in payload:
                    raise CheckError('MISSING_RESPONSE_FIELD')
                checks['sandbox_' + endpoint] = 'PASS'
            except CheckError as exc:
                checks['sandbox_' + endpoint] = str(exc)
    else:
        for endpoint in ('balances', 'positions', 'orders'):
            checks['sandbox_' + endpoint] = 'NOT_TESTED'
    try:
        q = rows((get_json('market', '/markets/quotes', {'symbols': 'SOXL'}).get('quotes') or {}), 'quote')
        checks['production_SOXL_quote'] = 'PASS' if len(q) == 1 and q[0].get('symbol') == 'SOXL' else 'INVALID_QUOTE'
    except CheckError as exc:
        checks['production_SOXL_quote'] = str(exc)
    try:
        now = datetime.now(ET)
        hist = get_json('market', '/markets/history', {'symbol': 'SOXL', 'interval': 'daily',
                                                       'start': (now.date() - timedelta(days=180)).isoformat(),
                                                       'end': now.date().isoformat()})
        bars = rows(hist.get('history') or {}, 'day')
        checks['production_SOXL_daily_bars'] = 'PASS' if len(bars) >= 45 else 'INSUFFICIENT_DAILY_BARS'
        report['daily_bar_count'] = len(bars)
        if bars:
            report['latest_daily_bar_date'] = str(bars[-1].get('date'))
    except CheckError as exc:
        checks['production_SOXL_daily_bars'] = str(exc)
    report['status'] = 'PASS' if all(v in ('PASS', 'PRESENT') for v in checks.values()) else 'FAILED'
    text = json.dumps(report, indent=2)
    path = Path(os.environ.get('TCAR_CHECK_REPORT', 'runtime/tcar_daily/preflight-status.json'))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + '\n', encoding='utf-8')
    print(text)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        print('{"status":"FAILED","error":"INTERNAL_CHECK_ERROR","orders_submitted":0,"bot_started":false}')
        sys.exit(2)
