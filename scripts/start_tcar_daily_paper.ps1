param(
  [string]$RepoPath = (Resolve-Path "$PSScriptRoot\.."),
  [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
Set-Location $RepoPath

# Read saved USER credentials explicitly so deployment can start immediately
# without requiring a new terminal/login session.
$env:TRADIER_TOKEN = [Environment]::GetEnvironmentVariable("TRADIER_TOKEN", "User")
$env:TRADIER_SANDBOX_TOKEN = [Environment]::GetEnvironmentVariable("TRADIER_SANDBOX_TOKEN", "User")
if (-not $env:TRADIER_TOKEN) { throw "TRADIER_TOKEN is required for production daily market data." }
if (-not $env:TRADIER_SANDBOX_TOKEN) { throw "TRADIER_SANDBOX_TOKEN is required for Tradier sandbox paper orders." }

$env:TCAR_MODE = "paper"
$env:TCAR_ALLOCATION_PCT = "1.0"
$env:TCAR_POLL_SECONDS = "5"
$env:TCAR_EXECUTION_GRACE_SECONDS = "90"
$env:TCAR_OFFHOURS_POLL_SECONDS = "300"
$env:TCAR_STATE_PATH = Join-Path $RepoPath "runtime\tcar_daily\paper-state.json"
$env:TCAR_AUDIT_PATH = Join-Path $RepoPath "runtime\tcar_daily\paper-audit.csv"

& $Python -m pip install -r requirements-dcr15.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

Write-Host "SOXL TCAR DAILY PAPER service starting."
Write-Host "Signals: completed daily bars. Orders: Tradier sandbox only. Live mode is not enabled."
while ($true) {
  & $Python scripts/tcar_daily_tradier_bot.py
  $code = $LASTEXITCODE
  Write-Warning "Daily TCAR bot exited with code $code. Restarting in 10 seconds."
  Start-Sleep -Seconds 10
}
