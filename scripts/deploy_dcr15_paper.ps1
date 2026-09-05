param(
  [string]$RepoPath = (Resolve-Path "$PSScriptRoot\.."),
  [string]$Python = "python",
  [string]$TaskName = "SOXL-DCR15-Tradier-Paper"
)
$ErrorActionPreference = "Stop"
Set-Location $RepoPath

Write-Host "DCR-15 PAPER deployment"
Write-Host "This deployment does not enable live trading."
Write-Host ""

& $Python -m pip install -r requirements-dcr15.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

& $Python -m py_compile scripts/dcr15_tradier_bot.py scripts/dcr15_sandbox_preflight.py tests/test_dcr15_execution_safety.py
if ($LASTEXITCODE -ne 0) { throw "Python compile check failed." }

& $Python tests/test_dcr15_execution_safety.py
if ($LASTEXITCODE -ne 0) { throw "DCR-15 execution-safety tests failed." }

Write-Host ""
Write-Host "Configure/verify local Tradier tokens. Token input is masked."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $RepoPath "scripts\setup_dcr15_local_tokens.ps1") -RepoPath $RepoPath -Python $Python
if ($LASTEXITCODE -ne 0) { throw "Local Tradier credential/preflight setup failed." }

Write-Host ""
Write-Host "Installing/updating Windows paper-trading task."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $RepoPath "scripts\install_dcr15_paper_task.ps1") -RepoPath $RepoPath -TaskName $TaskName
if ($LASTEXITCODE -ne 0) { throw "Scheduled-task installation failed." }

# Never allow this deployment helper to carry a live-enable flag into the task.
Remove-Item Env:\TRADIER_LIVE_ENABLE -ErrorAction SilentlyContinue

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
$task = Get-ScheduledTask -TaskName $TaskName
$info = $task | Get-ScheduledTaskInfo

Write-Host ""
Write-Host "DCR-15 PAPER DEPLOYMENT COMPLETE"
Write-Host "Task name: $TaskName"
Write-Host "Task state: $($task.State)"
Write-Host "Last task result: $($info.LastTaskResult)"
Write-Host "Paper state: runtime\dcr15\paper-state.json"
Write-Host "Paper audit: runtime\dcr15\paper-audit.csv"
Write-Host ""
Write-Host "If paper-state.json contains halted_reason, investigate it; do not delete state to bypass a broker-position/order mismatch."
