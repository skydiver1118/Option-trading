$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$SecretDir = Join-Path $ScriptDir '.secrets'
$TokenFile = Join-Path $SecretDir 'tradier_token.txt'
$Runner = Join-Path $ScriptDir 'run_refresh.ps1'

Write-Host 'Option dashboard local scheduler setup'
Write-Host "Repository: $RepoRoot"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Python is not installed or not in PATH.' }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Git is not installed or not in PATH.' }

Set-Location $RepoRoot
Write-Host 'Installing/updating Python dependencies...'
python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'pip install failed.' }

New-Item -ItemType Directory -Force -Path $SecretDir | Out-Null
Write-Host ''
Write-Host 'Enter your Tradier production API token. It will be DPAPI-encrypted for this Windows user on this PC.'
$secure = Read-Host 'TRADIER_TOKEN' -AsSecureString
$secure | ConvertFrom-SecureString | Set-Content $TokenFile

# Verify token without storing plaintext on disk.
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
try {
    $headers = @{ Authorization = "Bearer $token"; Accept = 'application/json' }
    $q = Invoke-RestMethod -Uri 'https://api.tradier.com/v1/markets/quotes?symbols=SPY' -Headers $headers -Method Get -TimeoutSec 20
    if (-not $q.quotes.quote) { throw 'Tradier returned no quote.' }
    Write-Host 'Tradier token verified successfully.'
} finally {
    $token = $null
}

# Validate Git authentication. A browser sign-in may appear the first time.
Write-Host 'Checking GitHub push authentication...'
git push --dry-run origin HEAD:main
if ($LASTEXITCODE -ne 0) {
    Write-Warning 'GitHub push authentication is not ready. Open GitHub Desktop or run git push once and sign in, then rerun this installer.'
    throw 'GitHub authentication check failed.'
}

$UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`""

$times = @(
    @{ Name='OptionDashboard-1000'; At='10:00AM' },
    @{ Name='OptionDashboard-1200'; At='12:00PM' },
    @{ Name='OptionDashboard-1400'; At='2:00PM' }
)
$days = @('Monday','Tuesday','Wednesday','Thursday','Friday')
foreach ($item in $times) {
    $trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $days -At $item.At
    Register-ScheduledTask -TaskName $item.Name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Refresh Tradier option dashboard and publish to GitHub.' -Force | Out-Null
    Write-Host "Installed $($item.Name) at $($item.At) ET/local PC time."
}

Write-Host ''
Write-Host 'Running one forced test refresh now...'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Runner -Force
if ($LASTEXITCODE -ne 0) { throw 'Test refresh failed. Check windows\logs.' }

Write-Host ''
Write-Host 'Setup complete.'
Write-Host 'Keep this Windows user logged in (a disconnected Remote Desktop session is fine).'
Write-Host 'The PC may sleep because tasks are configured to wake it; it cannot run if the PC is powered off.'
Write-Host 'Logs: windows\logs'
