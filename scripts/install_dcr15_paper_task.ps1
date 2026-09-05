param(
  [string]$TaskName = "SOXL-DCR15-Tradier-Paper",
  [string]$RepoPath = (Resolve-Path "$PSScriptRoot\..")
)
$ErrorActionPreference = "Stop"
$launcher = Join-Path $RepoPath "scripts\start_dcr15_paper.ps1"
if (-not (Test-Path $launcher)) { throw "Launcher not found: $launcher" }
$ps = (Get-Command powershell.exe).Source
$arg = "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`" -RepoPath `"$RepoPath`""
$action = New-ScheduledTaskAction -Execute $ps -Argument $arg -WorkingDirectory $RepoPath
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 20 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "SOXL DCR-15 Tradier sandbox auto-trading service" -Force | Out-Null
Write-Host "Installed task: $TaskName"
Write-Host "It will start at logon. To start now: Start-ScheduledTask -TaskName '$TaskName'"
