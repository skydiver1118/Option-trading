param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$LogDir = Join-Path $ScriptDir 'logs'
$SecretDir = Join-Path $ScriptDir '.secrets'
$TokenFile = Join-Path $SecretDir 'tradier_token.txt'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stamp = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$LogFile = Join-Path $LogDir "refresh_$Stamp.log"

function Log([string]$Message) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')  $Message"
    $line | Tee-Object -FilePath $LogFile -Append
}

function Invoke-Retry {
    param([scriptblock]$Command, [string]$Name, [int]$Attempts = 3, [int]$DelaySeconds = 15)
    for ($i = 1; $i -le $Attempts; $i++) {
        try {
            & $Command
            if ($LASTEXITCODE -ne $null -and $LASTEXITCODE -ne 0) { throw "$Name exited with code $LASTEXITCODE" }
            return
        } catch {
            Log "$Name attempt $i/$Attempts failed: $($_.Exception.Message)"
            if ($i -eq $Attempts) { throw }
            Start-Sleep -Seconds $DelaySeconds
        }
    }
}

function Get-PlainText([Security.SecureString]$Secure) {
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

try {
    Set-Location $RepoRoot
    Log "Starting local option-dashboard refresh. Repo=$RepoRoot Force=$Force"

    if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Python is not available in PATH.' }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Git is not available in PATH.' }
    if (-not (Test-Path $TokenFile)) { throw "Tradier token file missing: $TokenFile. Run windows\install.ps1 first." }

    # Only update on NYSE trading days. -Force bypasses this check for testing.
    if (-not $Force) {
        $isTradingDay = & python -c "import pandas_market_calendars as mcal; from datetime import datetime; from zoneinfo import ZoneInfo; d=datetime.now(ZoneInfo('America/New_York')).date(); print('1' if not mcal.get_calendar('NYSE').schedule(start_date=d,end_date=d).empty else '0')"
        if ($LASTEXITCODE -ne 0) { throw 'Unable to determine NYSE trading-day status.' }
        if (($isTradingDay | Out-String).Trim() -ne '1') {
            Log 'NYSE is closed today. No refresh needed.'
            exit 0
        }
    }

    # Sync code first. A temporary GitHub/network failure should not prevent local data generation.
    try { Invoke-Retry -Name 'git pull' -Attempts 2 -DelaySeconds 10 -Command { git pull --rebase origin main 2>&1 | Tee-Object -FilePath $LogFile -Append } }
    catch { Log "Continuing with local code because git pull failed: $($_.Exception.Message)" }

    $secureToken = Get-Content $TokenFile | ConvertTo-SecureString
    $env:TRADIER_TOKEN = Get-PlainText $secureToken
    $env:GITHUB_EVENT_NAME = 'workflow_dispatch'
    $env:GITHUB_EVENT_SCHEDULE = ''

    $before = if (Test-Path 'data\dashboard.json') { (Get-Item 'data\dashboard.json').LastWriteTimeUtc } else { [datetime]::MinValue }
    Log 'Downloading market/options data and rebuilding dashboard.json...'
    & python 'scripts\update_dashboard.py' 2>&1 | Tee-Object -FilePath $LogFile -Append
    if ($LASTEXITCODE -ne 0) { throw "update_dashboard.py failed with exit code $LASTEXITCODE" }
    if (-not (Test-Path 'data\dashboard.json')) { throw 'data/dashboard.json was not created.' }

    $after = (Get-Item 'data\dashboard.json').LastWriteTimeUtc
    if ($after -le $before) { throw 'dashboard.json timestamp did not advance.' }
    $json = Get-Content 'data\dashboard.json' -Raw | ConvertFrom-Json
    Log "Dashboard generated: updated_et=$($json.updated_et), option_source=$($json.option_data_source)"

    # Publish through Git. pages.yml deploys GitHub Pages on every push to main.
    git add data/dashboard.json
    $changed = git status --porcelain -- data/dashboard.json
    if ($changed) {
        git config user.name 'option-dashboard-local'
        git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
        git commit -m "Local refresh option dashboard data $($json.updated_et)" 2>&1 | Tee-Object -FilePath $LogFile -Append
        if ($LASTEXITCODE -ne 0) { throw 'git commit failed.' }
        Invoke-Retry -Name 'git push' -Attempts 4 -DelaySeconds 15 -Command { git push origin HEAD:main 2>&1 | Tee-Object -FilePath $LogFile -Append }
        Log 'Dashboard data pushed to GitHub. GitHub Pages push-triggered deployment will publish it.'
    } else {
        Log 'No Git diff detected after refresh; nothing to push.'
    }

    # Keep logs bounded.
    Get-ChildItem $LogDir -Filter 'refresh_*.log' | Sort-Object LastWriteTime -Descending | Select-Object -Skip 60 | Remove-Item -Force -ErrorAction SilentlyContinue
    Log 'SUCCESS'
    exit 0
}
catch {
    Log "FAILED: $($_.Exception.Message)"
    exit 1
}
finally {
    Remove-Item Env:TRADIER_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:GITHUB_EVENT_NAME -ErrorAction SilentlyContinue
    Remove-Item Env:GITHUB_EVENT_SCHEDULE -ErrorAction SilentlyContinue
}
