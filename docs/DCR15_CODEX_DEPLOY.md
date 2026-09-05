# DCR-15 Windows/Codex Paper Deployment

Use this runbook on the Windows PC that will remain logged in while DCR-15 paper trading is active.

## Simplest deployment

From the current `Option-trading` repository root, one PowerShell command performs the local checks, prompts for tokens with masked input, runs the GET-only Tradier preflight, installs the scheduled task, and starts the paper service:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/deploy_dcr15_paper.ps1
```

The deployment script never enables the live-order gate.

## Single Codex handoff prompt

Paste this into local Codex:

```text
Deploy the latest SOXL DCR-15 PAPER trading service from my
skydiver1118/Option-trading repository on this Windows PC.

PAPER/SANDBOX ONLY. Do not enable DCR15 live mode and do not set
TRADIER_LIVE_ENABLE. Do not print, echo, log, commit, or otherwise expose
Tradier token values. Do not discard any uncommitted local work.

Find my local Option-trading clone. If the working tree is clean, update main
with `git pull --ff-only`; if it is not clean, preserve my changes and stop
rather than resetting anything.

Then run:
  powershell -ExecutionPolicy Bypass -File scripts/deploy_dcr15_paper.ps1

Let me enter the production and sandbox Tradier tokens only through the masked
PowerShell prompts, not in Codex chat text.

After deployment, verify the scheduled task
`SOXL-DCR15-Tradier-Paper` is running and there is only one instance. Read only
sanitized status from `runtime/dcr15/paper-state.json` and the tail of
`runtime/dcr15/paper-audit.csv`. Report state_mode, last_bar, pending,
active-order status, owned_qty, broker_qty, last_order_status, and
halted_reason. Never display credentials or account identifiers.

If the bot halts because an existing sandbox SOXL position/order is not
strategy-owned, stop and report the mismatch. Do not liquidate, cancel, adopt,
or delete state to bypass the safeguard.
```

## What `deploy_dcr15_paper.ps1` does

1. Installs DCR-15 Python dependencies.
2. Compiles the bot, preflight, and safety tests.
3. Runs the execution-safety tests.
4. Runs `setup_dcr15_local_tokens.ps1`.
   - token entry is masked;
   - tokens are stored as Windows **User** environment variables on this PC;
   - token values are not written to the repository.
5. Runs the GET-only local Tradier preflight.
   - production SOXL market-data access;
   - sandbox account/balance/position/order read access;
   - zero orders submitted.
6. Installs/updates the `SOXL-DCR15-Tradier-Paper` Windows scheduled task.
7. Starts that task.
8. Prints only task/runtime locations and non-secret status.

The paper launcher explicitly reloads the saved Windows User token values when it starts, so a logoff/reboot is not required merely to make the task see newly saved credentials.

## Expected runtime files

- `runtime/dcr15/paper-state.json`
- `runtime/dcr15/paper-audit.csv`
- `runtime/dcr15/local-preflight-status.json`

## Stop or disable paper trading

Stop the current task instance:

```powershell
Stop-ScheduledTask -TaskName 'SOXL-DCR15-Tradier-Paper'
```

Disable automatic start at logon:

```powershell
Disable-ScheduledTask -TaskName 'SOXL-DCR15-Tradier-Paper'
```

Re-enable it:

```powershell
Enable-ScheduledTask -TaskName 'SOXL-DCR15-Tradier-Paper'
```

## Live boundary

This deployment is paper-only. `start_dcr15_paper.ps1` sets `DCR15_MODE=paper`, sends paper orders only to the Tradier Sandbox endpoint, and does not set the separate production live-enable gate.
