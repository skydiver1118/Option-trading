# DCR-15 Windows/Codex Paper Deployment

Use this runbook on the Windows PC that will remain logged in while DCR-15 paper trading is active.

## Single Codex handoff prompt

Paste the following into local Codex:

```text
Deploy the latest SOXL DCR-15 PAPER trading service from my
skydiver1118/Option-trading repository on this Windows PC.

Rules:
- PAPER/SANDBOX ONLY. Do not enable DCR15 live mode and do not set
  TRADIER_LIVE_ENABLE.
- Do not print, echo, log, commit, or otherwise expose token values.
- Do not discard any uncommitted local work.

Procedure:
1. Find my local Option-trading clone. If none exists, clone
   https://github.com/skydiver1118/Option-trading.git into an appropriate
   folder under my Codex workspace.
2. Run git status. If the working tree is clean, update main with
   `git pull --ff-only`. If it is not clean, preserve the changes and report
   the conflict instead of resetting anything.
3. Confirm these files exist:
   - scripts/dcr15_tradier_bot.py
   - scripts/dcr15_sandbox_preflight.py
   - scripts/setup_dcr15_local_tokens.ps1
   - scripts/start_dcr15_paper.ps1
   - scripts/install_dcr15_paper_task.ps1
   - tests/test_dcr15_execution_safety.py
4. Run the Python compile and execution-safety tests. Stop if either fails.
5. Run `scripts/setup_dcr15_local_tokens.ps1`. Let me enter the production
   Tradier token and sandbox Tradier token interactively with masked input.
   Do not ask me to paste either token into Codex chat text.
6. Verify the GET-only local preflight returns PASS. It must show production
   SOXL quote/15-minute data access and sandbox account/balance/position/order
   read access. This step must submit zero orders.
7. Inspect `runtime/dcr15/paper-state.json` if it exists. Never delete or
   rewrite executable state merely to bypass a halt or position mismatch.
8. Install/update the Windows scheduled task using
   `scripts/install_dcr15_paper_task.ps1`.
9. Start `SOXL-DCR15-Tradier-Paper`.
10. Verify only one task/service instance is running, DCR15_MODE is paper,
    and no live-enable environment variable has been introduced by this
    deployment.
11. Read only sanitized status from `runtime/dcr15/paper-state.json` and the
    tail of `runtime/dcr15/paper-audit.csv`. Report strategy state, pending
    action if any, active order status if any, owned quantity, broker quantity,
    last processed bar, and halted_reason. Do not show credentials or account
    identifiers.
12. If the bot halts because an existing sandbox SOXL position or order is not
    strategy-owned, stop and report the mismatch. Do not liquidate, cancel, or
    adopt that position automatically.
```

## Direct PowerShell equivalent

From the repository root:

```powershell
python -m pip install -r requirements-dcr15.txt
python -m py_compile scripts/dcr15_tradier_bot.py tests/test_dcr15_execution_safety.py
python tests/test_dcr15_execution_safety.py
powershell -ExecutionPolicy Bypass -File scripts/setup_dcr15_local_tokens.ps1
```

Close and reopen PowerShell after the token setup, then:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_dcr15_paper_task.ps1
Start-ScheduledTask -TaskName 'SOXL-DCR15-Tradier-Paper'
Get-ScheduledTask -TaskName 'SOXL-DCR15-Tradier-Paper' | Get-ScheduledTaskInfo
```

## Expected runtime files

- `runtime/dcr15/paper-state.json`
- `runtime/dcr15/paper-audit.csv`
- `runtime/dcr15/local-preflight-status.json`

## Stop paper service

```powershell
Stop-ScheduledTask -TaskName 'SOXL-DCR15-Tradier-Paper'
```

To disable automatic start at logon without deleting the task:

```powershell
Disable-ScheduledTask -TaskName 'SOXL-DCR15-Tradier-Paper'
```

Re-enable it with:

```powershell
Enable-ScheduledTask -TaskName 'SOXL-DCR15-Tradier-Paper'
```

## Live boundary

This runbook does not activate real-money trading. The paper launcher sets `DCR15_MODE=paper`, and no deployment step here sets the separate live-enable gate.
