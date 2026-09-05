param(
  [string]$RepoPath = (Resolve-Path "$PSScriptRoot\.."),
  [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
Set-Location $RepoPath
$taskName = "SOXL-TCAR-Daily-Tradier-Paper"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and $existing.State -eq "Running") {
  throw "The daily task is already running. Review and stop it before redeploying. Preserve its state."
}
Write-Host "Deploying SOXL DAILY TCAR, no QQQ sizing, PAPER ONLY."
Write-Host "DCR-15 will only be disabled after a safe migration check."

& $Python -m pip install -r requirements-tcar-daily.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
& $Python -m py_compile scripts/tcar_daily_tradier_bot.py scripts/tcar_daily_sandbox_preflight.py scripts/tcar_daily_migration_check.py tests/test_tcar_daily_execution_safety.py tests/test_tcar_daily_replication_parity.py
if ($LASTEXITCODE -ne 0) { throw "Python compile failed." }
& $Python tests/test_tcar_daily_execution_safety.py
if ($LASTEXITCODE -ne 0) { throw "Daily safety tests failed." }
& $Python tests/test_tcar_daily_replication_parity.py
if ($LASTEXITCODE -ne 0) { throw "Daily replication parity failed." }

# The existing helper reuses saved local tokens or prompts with masked input.
# GitHub Actions secrets do not automatically become Windows credentials.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/setup_tcar_daily_local_tokens.ps1 -RepoPath $RepoPath -Python $Python
if ($LASTEXITCODE -ne 0) { throw "Local credential/preflight setup failed." }
$env:TRADIER_TOKEN = [Environment]::GetEnvironmentVariable("TRADIER_TOKEN", "User")
$env:TRADIER_SANDBOX_TOKEN = [Environment]::GetEnvironmentVariable("TRADIER_SANDBOX_TOKEN", "User")
Remove-Item Env:\TCAR_LIVE_ENABLE -ErrorAction SilentlyContinue
Remove-Item Env:\TRADIER_LIVE_ENABLE -ErrorAction SilentlyContinue
$env:TCAR_MODE = "paper"
$env:TCAR_SYMBOL = "SOXL"
$env:TCAR_ALLOCATION_PCT = "1.0"

& $Python scripts/tcar_daily_migration_check.py
if ($LASTEXITCODE -ne 0) {
  throw "Migration blocked by positions/orders or unresolved state. Nothing was liquidated, adopted or canceled; DCR-15 has not been stopped."
}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/install_tcar_daily_paper_task.ps1 -RepoPath $RepoPath
if ($LASTEXITCODE -ne 0) { throw "Scheduled-task installation failed." }
Start-Sleep -Seconds 3
$oldProcesses = @(Get-CimInstance Win32_Process -Filter "name='python.exe' or name='pythonw.exe'" |
  Where-Object { $_.CommandLine -match 'dcr15_tradier_bot\.py' })
if ($oldProcesses.Count -gt 0) {
  throw "An old DCR-15 Python process remains. Stop it locally before proceeding. The daily task has NOT been started."
}
# Recheck after stopping the old scheduled task, before the daily service starts.
& $Python scripts/tcar_daily_migration_check.py
if ($LASTEXITCODE -ne 0) { throw "Post-switch migration check failed. Daily task NOT started; inspect sandbox orders/state." }

$started = Get-Date
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 10
$task = Get-ScheduledTask -TaskName $taskName
$statePath = Join-Path $RepoPath "runtime\tcar_daily\paper-state.json"
if ($task.State -ne "Running" -or -not (Test-Path $statePath)) {
  throw "Daily task did not confirm startup. Inspect Task Scheduler; do not assume trading is running."
}
$state = Get-Content $statePath -Raw | ConvertFrom-Json
if ($state.strategy -ne "SOXL_TCAR_DAILY_V1" -or $state.state_mode -ne "paper") {
  Stop-ScheduledTask -TaskName $taskName
  throw "Daily strategy or mode mismatch. Task stopped; state preserved."
}
if ($state.halted_reason) { throw "Daily bot halted safely. Inspect halted_reason locally; do not delete state." }
if ((Get-Item $statePath).LastWriteTime -lt $started) { throw "Daily state was not refreshed; inspect startup." }
Write-Host "SOXL TCAR DAILY PAPER task started and state refreshed."
Write-Host "Rules: WR2<-90 AND CCI5<-80 AND ADX_SMA20>=15; exit Close>prior daily High OR WR2>-30."
Write-Host "Daily bars; next-session-open execution; no QQQ sizing."
Write-Host "State: runtime\tcar_daily\paper-state.json"
Write-Host "Audit: runtime\tcar_daily\paper-audit.csv"
Write-Host "Keep this Windows user signed in and the PC awake. No live trading was enabled."
