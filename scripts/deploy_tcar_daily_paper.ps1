param(
  [string]$RepoPath = (Resolve-Path "$PSScriptRoot\.."),
  [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
Set-Location $RepoPath

Write-Host "Deploying SOXL DAILY TCAR paper service. DCR-15 will be disabled."
Write-Host "This script does not enable real-money trading."

& $Python -m pip install -r requirements-dcr15.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

& $Python -m py_compile scripts/tcar_daily_tradier_bot.py scripts/tcar_daily_sandbox_preflight.py tests/test_tcar_daily_execution_safety.py
if ($LASTEXITCODE -ne 0) { throw "Python compile failed." }

& $Python tests/test_tcar_daily_execution_safety.py
if ($LASTEXITCODE -ne 0) { throw "Daily TCAR safety tests failed." }

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/setup_tcar_daily_local_tokens.ps1 -RepoPath $RepoPath -Python $Python
if ($LASTEXITCODE -ne 0) { throw "Token/preflight setup failed." }

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/install_tcar_daily_paper_task.ps1 -RepoPath $RepoPath
if ($LASTEXITCODE -ne 0) { throw "Scheduled-task installation failed." }

Start-ScheduledTask -TaskName "SOXL-TCAR-Daily-Tradier-Paper"
Start-Sleep -Seconds 3
$info = Get-ScheduledTask -TaskName "SOXL-TCAR-Daily-Tradier-Paper" | Get-ScheduledTaskInfo
Write-Host "TCAR task last result: $($info.LastTaskResult)"
Write-Host "DAILY TCAR PAPER DEPLOYMENT: STARTED"
Write-Host "State: runtime\tcar_daily\paper-state.json"
Write-Host "Audit: runtime\tcar_daily\paper-audit.csv"
