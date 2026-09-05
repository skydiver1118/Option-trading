param(
  [string]$TaskName = "SOXL-TCAR-Daily-Tradier-Paper",
  [string]$RepoPath = (Resolve-Path "$PSScriptRoot\..")
)
$ErrorActionPreference = "Stop"

# The user explicitly replaced DCR-15 with daily TCAR. Stop/disable the old
# task if it exists so both strategies cannot trade the same sandbox account.
$old = Get-ScheduledTask -TaskName "SOXL-DCR15-Tradier-Paper" -ErrorAction SilentlyContinue
if ($old) {
  Stop-ScheduledTask -TaskName "SOXL-DCR15-Tradier-Paper" -ErrorAction SilentlyContinue
  Disable-ScheduledTask -TaskName "SOXL-DCR15-Tradier-Paper" | Out-Null
  Write-Host "Disabled old SOXL-DCR15-Tradier-Paper task."
}

$launcher = Join-Path $RepoPath "scripts\start_tcar_daily_paper.ps1"
if (-not (Test-Path $launcher)) { throw "Launcher not found: $launcher" }
$ps = (Get-Command powershell.exe).Source
$arg = "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`" -RepoPath `"$RepoPath`""
$action = New-ScheduledTaskAction -Execute $ps -Argument $arg -WorkingDirectory $RepoPath
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
  -RestartCount 20 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -StartWhenAvailable `
  -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
  -Description "SOXL daily TCAR Tradier sandbox paper service; daily close signals, next-session-open execution" -Force | Out-Null
Write-Host "Installed task: $TaskName"
Write-Host "Old DCR-15 task is disabled if it existed."
Write-Host "To start now: Start-ScheduledTask -TaskName '$TaskName'"
