param(
  [string]$RepoPath = (Resolve-Path "$PSScriptRoot\.."),
  [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
Set-Location $RepoPath
$env:TRADIER_TOKEN = [Environment]::GetEnvironmentVariable("TRADIER_TOKEN", "User")
$env:TRADIER_SANDBOX_TOKEN = [Environment]::GetEnvironmentVariable("TRADIER_SANDBOX_TOKEN", "User")
if (-not $env:TRADIER_TOKEN) { throw "Local production data token missing." }
if (-not $env:TRADIER_SANDBOX_TOKEN) { throw "Local sandbox token missing." }
Remove-Item Env:\TCAR_LIVE_ENABLE -ErrorAction SilentlyContinue
Remove-Item Env:\TRADIER_LIVE_ENABLE -ErrorAction SilentlyContinue
$env:TCAR_MODE = "paper"
$env:TCAR_SYMBOL = "SOXL"
$env:TCAR_ALLOCATION_PCT = "1.0"
$env:TCAR_POLL_SECONDS = "5"
$env:TCAR_EXECUTION_GRACE_SECONDS = "90"
$env:TCAR_OFFHOURS_POLL_SECONDS = "300"
$env:TCAR_STATE_PATH = Join-Path $RepoPath "runtime\tcar_daily\paper-state.json"
$env:TCAR_AUDIT_PATH = Join-Path $RepoPath "runtime\tcar_daily\paper-audit.csv"
$oldProcesses = @(Get-CimInstance Win32_Process -Filter "name='python.exe' or name='pythonw.exe'" |
  Where-Object { $_.CommandLine -match 'dcr15_tradier_bot\.py' })
if ($oldProcesses.Count -gt 0) { throw "DCR-15 is still running; daily service will not run alongside it." }
& $Python -m pip install -r requirements-tcar-daily.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
Write-Host "SOXL TCAR DAILY PAPER service. No QQQ sizing. Live mode disabled."
while ($true) {
  & $Python scripts/tcar_daily_tradier_bot.py
  $code = $LASTEXITCODE
  Write-Warning "Daily TCAR process exited with code $code. Restarting in 10 seconds; saved halts are preserved."
  Start-Sleep -Seconds 10
}
