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
$NativeCommandHelper = Join-Path $ScriptDir 'native_command.ps1'
. $NativeCommandHelper
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

    if (-not $Force) {
        $isTradingDay = & python -c "import pandas_market_calendars as mcal; from datetime import datetime; from zoneinfo import ZoneInfo; d=datetime.now(ZoneInfo('America/New_York')).date(); print('1' if not mcal.get_calendar('NYSE').schedule(start_date=d,end_date=d).empty else '0')"
        if ($LASTEXITCODE -ne 0) { throw 'Unable to determine NYSE trading-day status.' }
        if (($isTradingDay | Out-String).Trim() -ne '1') { Log 'NYSE is closed today. No refresh needed.'; exit 0 }
    }

    Invoke-Retry -Name 'git pull' -Attempts 3 -DelaySeconds 15 -Command {
        Invoke-NativeLogged -FilePath 'git' -ArgumentList @('pull', '--rebase', 'origin', 'main') -LogFile $LogFile -Name 'git pull'
    }
    Log 'Repository sync succeeded; using current Greek-aware dashboard code.'

    $secureToken = Get-Content $TokenFile | ConvertTo-SecureString
    $env:TRADIER_TOKEN = Get-PlainText $secureToken
    $env:GITHUB_EVENT_NAME = 'workflow_dispatch'

    $before = if (Test-Path 'data\dashboard.json') { (Get-Item 'data\dashboard.json').LastWriteTimeUtc } else { [datetime]::MinValue }
    Log 'Downloading market/options data and selecting standard monthly expiration by Greeks within 19-50 DTE...'
    Invoke-NativeLogged -FilePath 'python' -ArgumentList @('scripts\update_dashboard_dynamic.py') -LogFile $LogFile -Name 'update_dashboard_dynamic.py'
    if (-not (Test-Path 'data\dashboard.json')) { throw 'data/dashboard.json was not created.' }
    $after = (Get-Item 'data\dashboard.json').LastWriteTimeUtc
    if ($after -le $before) { throw 'dashboard.json timestamp did not advance.' }
    $json = Get-Content 'data\dashboard.json' -Raw | ConvertFrom-Json
    if ($json.ranking_basis -ne 'Option execution score descending') { throw "Unexpected ranking basis: $($json.ranking_basis)" }
    foreach ($a in $json.analysis) {
        if ($a.expiration_selection -and $a.expiration_selection.dte) {
            $dte=[int]$a.expiration_selection.dte
            if ($dte -lt 19 -or $dte -gt 50) { throw "Expiration DTE outside 19-50 for $($a.ticker): $dte" }
        }
    }
    Log "Dashboard generated: updated_et=$($json.updated_et), source=$($json.option_data_source), expiration=$($json.option_expiration), ranking=$($json.ranking_basis)"

    git add data/dashboard.json
    $changed = git status --porcelain -- data/dashboard.json
    if ($changed) {
        git config user.name 'option-dashboard-local'
        git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
        Invoke-NativeLogged -FilePath 'git' -ArgumentList @('commit', '-m', "Local Greek-aware dashboard refresh $($json.updated_et)") -LogFile $LogFile -Name 'git commit'
        Invoke-Retry -Name 'git push' -Attempts 4 -DelaySeconds 15 -Command {
            Invoke-NativeLogged -FilePath 'git' -ArgumentList @('push', 'origin', 'HEAD:main') -LogFile $LogFile -Name 'git push'
        }
        Log 'Dashboard data pushed to GitHub.'
    } else { Log 'No Git diff detected after refresh; nothing to push.' }

    Get-ChildItem $LogDir -Filter 'refresh_*.log' | Sort-Object LastWriteTime -Descending | Select-Object -Skip 60 | Remove-Item -Force -ErrorAction SilentlyContinue
    Log 'SUCCESS'; exit 0
}
catch { Log "FAILED: $($_.Exception.Message)"; exit 1 }
finally {
    Remove-Item Env:TRADIER_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:GITHUB_EVENT_NAME -ErrorAction SilentlyContinue
}
