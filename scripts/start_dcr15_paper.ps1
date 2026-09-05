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
$env:DCR15_EXECUTION_GRACE_SECONDS = "60"
$env:DCR15_OFFHOURS_POLL_SECONDS = "60"

& $Python -m pip install -r requirements-dcr15.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

Write-Host "DCR-15 PAPER service starting. Live-order mode is not enabled by this launcher."
Write-Host "Production Tradier is used for market data; Tradier sandbox is used for paper orders."

while ($true) {
  & $Python scripts/dcr15_tradier_bot.py
  $code = $LASTEXITCODE
  Write-Warning "DCR-15 bot exited with code $code. Restarting in 10 seconds."
  Start-Sleep -Seconds 10
}
