# SOXL Daily TCAR Tradier Auto-Trade

## Active strategy
This replaces DCR-15. The active paper-trading strategy is the finalized **daily TCAR** implementation derived from `scripts/soxl_daily_tcar_no_qqq_10y.py`.

### Frozen rules
- Symbol: SOXL
- Signal bars: completed **daily** regular-session bars from Tradier production
- Entry after daily close: `WR(2) < -90 AND CCI(5) < -80 AND ADX(20) >= 15`
- Exit after daily close: `Close > previous daily High OR WR(2) > -30`
- CCI is **not** an exit rule
- Execution: next trading-day regular-session open
- Position: long only, one strategy-owned position, no pyramiding
- Default allocation: 100% of non-leveraged available cash/equity, whole shares
- QQQ sizing overlay: not used in this deployment

## Data and broker separation
- Production Tradier token: SOXL daily market data
- Sandbox Tradier token: paper account, balances, positions, order preview/submission/status
- Real-money mode remains hard locked and is not enabled by any paper launcher/deployment script.

## Execution safety
The daily engine implements:
- unique per-signal order tags
- preview-before-submit in paper mode
- active order status reconciliation
- partial-fill accounting
- recovery after ambiguous submission results
- restart recovery when the broker accepted an order before local state was persisted
- exact broker-position versus strategy-owned-position reconciliation
- halt on manual/unrecognized SOXL positions
- no blind order retry
- strict next-session-open execution window; missed signals are not chased
- market calendar handling for weekends, holidays and early-close sessions

## Windows deployment
Use:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/deploy_tcar_daily_paper.ps1
```

The deployment:
1. compiles code and runs safety tests;
2. collects Tradier tokens through masked local PowerShell prompts;
3. runs a GET-only preflight that cannot place an order;
4. stops/disables the old `SOXL-DCR15-Tradier-Paper` Windows task if present;
5. installs `SOXL-TCAR-Daily-Tradier-Paper`;
6. starts the daily paper service.

Runtime files:
- `runtime/tcar_daily/paper-state.json`
- `runtime/tcar_daily/paper-audit.csv`
- `runtime/tcar_daily/local-preflight-status.json`

## Expected operating cycle
1. After each trading-day close, retrieve the latest completed SOXL daily bar.
2. Calculate WR(2), CCI(5), ADX(20), and previous daily high.
3. If an entry/exit signal exists, persist it with the next trading session's open time.
4. At that open, submit a market order only inside the configured execution grace window.
5. Reconcile order status/fills and broker position before accepting another signal.

## Stop/disable
```powershell
Stop-ScheduledTask -TaskName 'SOXL-TCAR-Daily-Tradier-Paper'
Disable-ScheduledTask -TaskName 'SOXL-TCAR-Daily-Tradier-Paper'
```

## Live boundary
Paper deployment never sets `TCAR_LIVE_ENABLE`. Real-money trading must remain disabled until paper execution has been reviewed across multiple complete entry/exit cycles.
