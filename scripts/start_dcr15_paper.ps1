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

& $Python -m pip install -r requirements-dcr15.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

function Get-EasternNow {
  return [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId((Get-Date), "Eastern Standard Time")
}

$stopAt = [TimeSpan]::FromHours(16) + [TimeSpan]::FromMinutes(20)

while ($true) {
  $et = Get-EasternNow
  if ($et.DayOfWeek -in @([DayOfWeek]::Saturday, [DayOfWeek]::Sunday) -or $et.TimeOfDay -ge $stopAt) {
    Write-Host "Outside DCR-15 paper operating window; launcher exiting at $($et.ToString('u'))."
    exit 0
  }

  Write-Host "Starting DCR-15 paper bot at $($et.ToString('u'))."
  $proc = Start-Process -FilePath $Python -ArgumentList @("scripts/dcr15_tradier_bot.py") -WorkingDirectory $RepoPath -PassThru -NoNewWindow

  while (-not $proc.HasExited) {
    Start-Sleep -Seconds 10
    $et = Get-EasternNow
    if ($et.TimeOfDay -ge $stopAt) {
      Write-Host "Stopping DCR-15 paper bot after the final regular-session bar window."
      Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
      exit 0
    }
  }

  Write-Warning "DCR-15 bot exited with code $($proc.ExitCode). Restarting in 10 seconds while within the operating window."
  Start-Sleep -Seconds 10
}
