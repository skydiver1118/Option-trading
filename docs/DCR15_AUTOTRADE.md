# SOXL DCR-15 Tradier Auto-Trade

## Frozen strategy
- Symbol: SOXL
- Bars: 15-minute regular-session bars only (09:30-16:00 ET)
- Entry: WR(5) < -80 AND CCI(5) < -80
- Exit: Close > previous 15m high OR WR(5) > -30 OR CCI(5) > 0
- Execution: next 15-minute bar open
- Position: long only; one position; no pyramiding
- Tactical allocation: 100% of non-leveraged available account cash/equity

## Architecture
1. Tradier production market-data adapter downloads completed SOXL 15m bars.
2. Signal engine calculates WR(5), CCI(5), and prior-bar high only from completed RTH bars.
3. Persistent state stores the last processed bar and next-open pending action.
4. Risk layer enforces one SOXL position, allocation <=100%, no margin-buying-power sizing, and market-open checks.
5. Tradier broker adapter resolves the account from `/user/profile` unless `TRADIER_ACCOUNT_ID` is set.
6. Order layer submits whole-share day market orders at the next tradable bar open.
7. Reconciliation reads broker positions on startup/restart and prevents duplicate buy/sell actions.
8. Audit ledger writes every bar, signal, order, and error to `runtime/dcr15/audit.csv`.

## Execution modes
- `dryrun`: signals and simulated position state; no broker order call.
- `preview`: production Tradier API with `preview=true`; validates orders without submitting them.
- `paper`: Tradier sandbox orders using `TRADIER_SANDBOX_TOKEN` while using `TRADIER_TOKEN` for live market data when available.
- `live`: production orders. Code is intentionally hard-locked and requires `TRADIER_LIVE_ENABLE=YES_I_ACCEPT_REAL_ORDERS` to be set outside ChatGPT after paper validation.

## Environment variables
Required market-data token:
- `TRADIER_TOKEN`

Paper trading:
- `TRADIER_SANDBOX_TOKEN`

Optional when more than one active Tradier account exists:
- `TRADIER_ACCOUNT_ID`

Strategy/runtime:
- `DCR15_MODE=dryrun|preview|paper|live`
- `DCR15_ALLOCATION_PCT=1.0`
- `DCR15_POLL_SECONDS=5`
- `DCR15_STATE_PATH=runtime/dcr15/state.json`
- `DCR15_AUDIT_PATH=runtime/dcr15/audit.csv`

## Recommended deployment
Do not use GitHub Actions as the order-timing engine. Scheduled Actions can start late and therefore cannot reliably satisfy "next 15-minute bar open." Run `scripts/dcr15_tradier_bot.py` as a long-running process on the always-on desktop/server. GitHub Actions is used only for smoke tests/health validation.

### Windows paper launcher
Set `TRADIER_TOKEN` and `TRADIER_SANDBOX_TOKEN` as secure user/system environment variables, then run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_dcr15_paper.ps1
```

For unattended operation, configure Windows Task Scheduler to start that PowerShell launcher at system startup/login and restart it after failure. The launcher itself also restarts the Python process if it exits.

## Production gate
Before any live activation:
1. Run paper mode through multiple signals/trades.
2. Reconcile every theoretical signal with the paper broker order/fill.
3. Verify no duplicate entries after process restart.
4. Verify the 15:45 signal remains pending overnight and executes only when Tradier reports the next regular session open.
5. Review `runtime/dcr15/audit.csv` and broker positions/orders.
6. Only then enable production outside ChatGPT.

Tradier base URLs used by the implementation:
- Production: `https://api.tradier.com/v1`
- Sandbox: `https://sandbox.tradier.com/v1`
