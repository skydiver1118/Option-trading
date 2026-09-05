# SOXL DCR-15 Tradier Auto-Trade

## Current deployment status

DCR-15 is **paper-trading ready, live-disabled**. Production Tradier is used for real-time market data and Tradier Sandbox is used for paper orders. GitHub Actions is used only for CI/preflight; it is not the order-timing engine.

## Frozen strategy

- Symbol: SOXL
- Bars: completed 15-minute regular-session bars only
- Entry: `WR(5) < -80 AND CCI(5) < -80`
- Exit: `Close > previous 15m high OR WR(5) > -30 OR CCI(5) > 0`
- Execution: next regular-session 15-minute bar open
- Position: long only; one strategy-owned position; no pyramiding
- Same-day re-entry: allowed after a completed exit and a later independent entry signal
- Tactical allocation: up to 100% of non-leveraged available cash/equity; margin stock-buying-power is not used for sizing
- Third indicator: none

## v2-safe execution architecture

1. Production Tradier market-data adapter reads completed SOXL 15-minute bars.
2. Signal engine calculates WR(5), CCI(5), and prior-bar high only from completed regular-session bars.
3. Tradier market calendar supplies the actual session open/close, including holidays and early closes.
4. Each signal is persisted before execution and receives a unique DCR-15 order tag.
5. A strict execution window begins at the intended next bar open. Default grace is 60 seconds; a stale signal is logged and not chased.
6. Every paper order is previewed before submission.
7. A successful POST is treated only as an API submission result, never as a fill.
8. Order status is reconciled through the broker order endpoint. Pending, open, partial-fill, fill, rejection, expiration, cancellation, and unexpected states are handled explicitly.
9. Partial fills update strategy-owned quantity incrementally and idempotently.
10. Exact-tag recovery prevents a blind resubmission if a network failure or process restart makes the original submission result uncertain.
11. If the process dies immediately after an order is accepted, the persisted pending tag is reconciled against the broker before any new submission.
12. Broker SOXL quantity must reconcile to DCR-15 strategy-owned quantity. A mismatch halts DCR-15 instead of touching an unowned/manual SOXL position.
13. All bars, signals, previews, submissions, status changes, fill deltas, terminal states, stale signals, halts, and errors are written to the audit ledger.

## Final-bar and early-close behavior

The last bar of a regular session has no same-day next 15-minute bar. Therefore its signal is scheduled for the next actual regular-session open.

Examples:
- Normal 16:00 close: a 15:45 signal executes at the next trading-session 09:30 open.
- 13:00 early close: a 12:45 signal executes at the next trading-session 09:30 open.

Weekends and market holidays are resolved through the Tradier market calendar rather than by weekday assumptions alone.

## Execution modes

- `dryrun`: real market data, simulated DCR-15 position state; no broker order call.
- `preview`: production Tradier order preview only; no submitted order.
- `paper`: Tradier Sandbox order submission with `TRADIER_SANDBOX_TOKEN`; production `TRADIER_TOKEN` supplies market data.
- `live`: production order code path exists but remains hard-locked. The paper launcher never sets the live-enable variable.

## Runtime state and audit

Mode-specific defaults prevent dry-run state from becoming executable paper state:

- Paper state: `runtime/dcr15/paper-state.json`
- Paper audit: `runtime/dcr15/paper-audit.csv`
- Dry-run state: `runtime/dcr15/dryrun-state.json`
- Dry-run audit: `runtime/dcr15/dryrun-audit.csv`

Important state fields include:
- `pending`
- `active_order`
- `submission_unknown`
- `owned_qty`
- `broker_qty`
- `last_order_status`
- `last_fill_at`
- `halted_reason`

Do not manually edit executable paper state while the service is running.

## Environment variables

Required locally on the Windows execution PC:

- `TRADIER_TOKEN` — production market data
- `TRADIER_SANDBOX_TOKEN` — sandbox/paper brokerage

Optional:

- `TRADIER_ACCOUNT_ID` — required only if account auto-resolution cannot select exactly one active account
- `DCR15_ALLOCATION_PCT` — default `1.0`, allowed `>0` and `<=1`
- `DCR15_POLL_SECONDS` — default `5`
- `DCR15_EXECUTION_GRACE_SECONDS` — default `60`
- `DCR15_OFFHOURS_POLL_SECONDS` — default `60`
- `DCR15_STATE_PATH`
- `DCR15_AUDIT_PATH`

The simple Windows setup helper is:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup_dcr15_local_tokens.ps1
```

It prompts for both tokens with masked input, saves them as Windows **User** environment variables, and runs the GET-only connectivity preflight. Tokens are not written to the repository.

## Windows paper deployment

After opening a fresh PowerShell/Codex process so it inherits the user environment variables:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_dcr15_paper_task.ps1
Start-ScheduledTask -TaskName 'SOXL-DCR15-Tradier-Paper'
```

The scheduled task:
- starts at Windows logon;
- blocks duplicate task instances;
- uses the limited/non-elevated user context;
- launches paper mode only;
- restarts the Python service after a process failure;
- keeps the service available across overnight/weekend pending signals;
- reduces off-hours polling rather than force-killing the strategy process after the close.

## Why GitHub Actions is not the execution clock

GitHub Actions scheduling can start late and cannot reliably satisfy a strict next-15-minute-bar-open requirement. Actions is used for:

- code compilation;
- execution-safety tests;
- PowerShell syntax validation;
- production-market-data dry-run tests;
- sandbox GET-only connectivity checks.

The Windows desktop/server is the paper execution engine.

## Fail-safe behavior

DCR-15 stops issuing new orders when it encounters conditions such as:

- broker SOXL position not equal to strategy-owned SOXL quantity;
- multiple active DCR-15-tagged orders;
- duplicate orders with the same unique signal tag;
- ambiguous order submission that cannot be reconciled;
- unexpected order side/status;
- active order missing from the broker;
- strategy-owned quantity becoming internally inconsistent.

A halt is recorded in `halted_reason` and the audit ledger. A halt should be investigated; it should not be bypassed by deleting state while a broker position/order exists.

## Paper-validation gate before any future live consideration

1. Observe multiple independent DCR-15 paper entries and exits.
2. Reconcile each signal to the intended next-bar execution time.
3. Reconcile every Tradier order ID, status transition, fill quantity, fill time, and fill price.
4. Verify restart recovery while flat, while holding a paper position, and with an active/pending order.
5. Verify final-bar/overnight behavior and at least one market-holiday or simulated early-close test.
6. Compare paper fills/slippage with the frozen backtest execution assumption.
7. Review `paper-state.json`, `paper-audit.csv`, and Tradier Sandbox positions/orders for parity.
8. Keep production live trading disabled until paper validation is complete and separately approved outside this paper deployment.

Tradier base URLs used by the implementation:
- Production: `https://api.tradier.com/v1`
- Sandbox: `https://sandbox.tradier.com/v1`
