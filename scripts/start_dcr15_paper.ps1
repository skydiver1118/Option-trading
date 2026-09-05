param(
  [string]$RepoPath = (Resolve-Path "$PSScriptRoot\.."),
  [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
Set-Location $RepoPath
if (-not $env:TRADIER_TOKEN) { throw "TRADIER_TOKEN is required for production market data." }
if (-not $env:TRADIER_SANDBOX_TOKEN) { throw "TRADIER_SANDBOX_TOKEN is required for Tradier paper trading." }
$env:DCR15_MODE = "paper"
$env:DCR15_ALLOCATION_PCT = "1.0"
$env:DCR15_POLL_SECONDS = "5"
& $Python -m pip install -r requirements-dcr15.txt
while ($true) {
  & $Python scripts/dcr15_tradier_bot.py
  Start-Sleep -Seconds 10
}
