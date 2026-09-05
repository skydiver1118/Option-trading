# CURRENT DEPLOYMENT: SOXL TCAR DAILY - NO QQQ SIZING - PAPER ONLY

This supersedes the earlier DCR-15 handoff. Historical intraday code, research and
state are preserved, but scripts/deploy_dcr15_paper.ps1 no longer starts it.
Remote Desktop Commander is not part of this deployment.

## Exact daily rules

- SOXL daily regular-session OHLC from Tradier production, never 15-minute bars.
- Entry while flat: WR(2) < -90 AND CCI(5) < -80 AND ADX(20) >= 15.
- Exit while long: Close > previous DAILY High OR WR(2) > -30.
- CCI is entry-only. No CCI exit or intraday signal exit.
- QQQ sizing: OFF. Target 100% non-margin available tactical cash, whole shares.
- One strategy-owned long position; no pyramiding; positions can stay overnight
  for several sessions until a daily exit signal.
- Indicators use completed regular-session DAILY bars. Next-open action means
  the NEXT TRADING SESSION, respecting holidays and early closes.
- The existing daily engine permits submission within 90 seconds after the
  scheduled regular-session open. A market order then does NOT guarantee the
  exact historical daily opening price. Log actual fills and timing differences.

IMPORTANT IMPLEMENTATION DETAIL: ADX is the rolling-SMA version used by
scripts/soxl_daily_tcar_yahoo_vs_tradier_10y.py, not standard Wilder ADX. The
replication parity tests compare all four indicator columns to that reference.
Do not substitute TA-Lib/Wilder defaults without a new backtest and version.

## One instruction for local Codex

```text
Change my Option-trading deployment to SOXL DAILY TCAR, not DCR-15.
Read docs/TCAR_DAILY_CODEX_DEPLOY.md. Use the no-QQQ-sizing version and PAPER ONLY.
Locate the local skydiver1118/Option-trading clone (or clone it if absent).
Preserve all uncommitted work; update a clean clone with git pull --ff-only.
Run from the repository root:
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/deploy_tcar_daily_paper.ps1
Use existing locally saved credentials or let me enter missing credentials only
in masked LOCAL PowerShell prompts. Never print tokens or put them in chat/git.
Do not enable TCAR_LIVE_ENABLE or TRADIER_LIVE_ENABLE.
The deployer must pass tests and a GET-only migration check, retire the old
SOXL-DCR15-Tradier-Paper task, verify no DCR-15 Python process remains, and recheck
migration before starting SOXL-TCAR-Daily-Tradier-Paper. Do not automatically
sell, cancel or adopt old positions/orders. Preserve both strategies' state.
Report the actual daily task state, daily strategy ID/mode, last processed daily
bar, latest reconciliation, owned/broker quantity, pending action and any halt.
Verify there is only one daily Python process. Report only sanitized output.
```

## Storage and runtime

The verified GitHub secrets remain TRADIER_TOKEN and TRADIER_SANDBOX_TOKEN.
They allow CI checks but do NOT automatically populate Windows. The current
local setup helper uses masked input and Windows USER environment variables;
those persistent variables are not encrypted credential storage. Never echo
values. Existing local credentials can be reused; do not extract GitHub secrets
into source files or logs.

Deployment runs daily rule/safety tests plus separate replication-parity tests,
then a GET-only sandbox/production DAILY data preflight. The migration gate
requires SOXL to be flat, no working SOXL orders, and no unresolved saved order
or halt. It runs before retiring DCR-15 and again before daily startup. Existing
positions/orders stop migration rather than being liquidated or adopted.

Daily task: SOXL-TCAR-Daily-Tradier-Paper
Daily state: runtime/tcar_daily/paper-state.json
Daily audit: runtime/tcar_daily/paper-audit.csv
The daily and intraday states/tags are separate. Do not copy intraday state into
the daily service. Do not delete state to bypass a halt.

Keep the Windows PC awake and the same user signed in; the task starts at logon.
GitHub is used for version control and checks, NOT the timed order engine.
Run the launcher through the task, not a second manually launched instance.

Stop the service:
  Stop-ScheduledTask -TaskName 'SOXL-TCAR-Daily-Tradier-Paper'
Disable automatic logon startup:
  Disable-ScheduledTask -TaskName 'SOXL-TCAR-Daily-Tradier-Paper'
Stopping the process does not close a position or cancel broker orders.

## Validation boundaries

Tests and a successful CI preflight are not proof of Windows installation or
real-time fill parity. No real-money trading is enabled by this deployment.
Daily historical performance is not re-estimated as part of this migration.
The previous 15-minute performance discrepancy is unrelated to the daily rules.
Before relying on unattended operation, verify next-open timing, recovery and
several complete sandbox trade cycles using actual logs/fills.

API references:
- https://docs.tradier.com/reference/brokerage-api-markets-get-history
- https://docs.tradier.com/reference/brokerage-api-markets-get-clock
- https://docs.tradier.com/docs/trading
