param(
  [string]$TaskName = "SOXL-DCR15-Tradier-Paper",
  [string]$RepoPath = (Resolve-Path "$PSScriptRoot\..")
)
$ErrorActionPreference = "Stop"

$launcher = Join-Path $RepoPath "scripts\start_dcr15_paper.ps1"
if (-not (Test-Path $launcher)) { throw "Launcher not found: $launcher" }

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$ps = (Get-Command powershell.exe).Source
$arg = "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`" -RepoPath `"$RepoPath`""
$action = New-ScheduledTaskAction -Execute $ps -Argument $arg -WorkingDirectory $RepoPath
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$settings = New-ScheduledTaskSettingsSet `
  -RestartCount 20 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -StartWhenAvailable `
  -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Principal $principal `
  -Description "SOXL DCR-15 Tradier sandbox paper-trading service; persistent after logon with reduced off-hours polling" `
  -Force | Out-Null

Write-Host "Installed task: $TaskName"
Write-Host "Trigger: logon for $currentUser. Duplicate task instances are blocked."
Write-Host "To start the PAPER service now: Start-ScheduledTask -TaskName '$TaskName'"
