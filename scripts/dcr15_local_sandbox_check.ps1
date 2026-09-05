#requires -Version 5.1
<#
Read-only Windows sandbox credential check. Does not import or start a bot.
Reads windows.secrets/tradier_sandbox_token.txt; never displays its contents.
No production endpoint, broker-order submission, or environment-secret persistence.
#>
param(
    [string]$RepoPath = (Split-Path -Parent $PSScriptRoot),
    [string]$TokenPath = ''
)
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($TokenPath)) {
    $TokenPath = Join-Path $RepoPath 'windows.secrets\tradier_sandbox_token.txt'
}
$report = [ordered]@{
    checked_at = [DateTimeOffset]::Now.ToString('o')
    check_type = 'LOCAL_SANDBOX_READ_ONLY'
    status = 'FAILED'
    orders_submitted = 0
    bot_started = $false
    production_checked = $false
    checks = [ordered]@{}
}
$headers = $null
$token = $null
$exitCode = 1
$oldProtocol = [Net.ServicePointManager]::SecurityProtocol

function Invoke-SandboxGet {
    param([string]$Path, [hashtable]$AuthHeaders)
    if (($Path -ne '/user/profile') -and
        ($Path -notmatch '^/accounts/[A-Za-z0-9_-]+/(balances|positions|orders)$')) {
        throw 'ENDPOINT_NOT_ALLOWED'
    }
    try {
        $data = Invoke-RestMethod -Uri ('https://sandbox.tradier.com/v1' + $Path) `
            -Method Get -Headers $AuthHeaders -TimeoutSec 25 -MaximumRedirection 0
    }
    catch {
        # Do not expose exception text: it can include account URLs or response bodies.
        $status = 0
        try { $status = [int]$_.Exception.Response.StatusCode } catch { $status = 0 }
        if ($status -in @(401, 403)) { throw 'SANDBOX_AUTHENTICATION_REJECTED' }
        if ($status -eq 429) { throw 'SANDBOX_RATE_LIMITED' }
        throw 'SANDBOX_HTTP_NETWORK_OR_TLS_ERROR'
    }
    if (($null -eq $data) -or $data.errors -or $data.fault) { throw 'SANDBOX_RESPONSE_ERROR' }
    return $data
}

try {
    if (-not (Test-Path -LiteralPath $TokenPath -PathType Leaf)) { throw 'TOKEN_FILE_NOT_FOUND' }
    # Inspect Git metadata only, never token contents. Stop if the token is tracked.
    if ((Test-Path -LiteralPath (Join-Path $RepoPath '.git')) -and (Get-Command git -ErrorAction SilentlyContinue)) {
        $tracked = @(& git -C $RepoPath ls-files -- 'windows.secrets/tradier_sandbox_token.txt')
        if ($LASTEXITCODE -ne 0) { throw 'GIT_TRACKING_CHECK_FAILED' }
        if ($tracked.Count -gt 0) { throw 'TOKEN_FILE_IS_GIT_TRACKED' }
        $report.checks['token_git_tracking'] = 'NOT_TRACKED'
    } else {
        $report.checks['token_git_tracking'] = 'NOT_CHECKED'
    }
    $token = [IO.File]::ReadAllText($TokenPath).Trim().TrimStart([char]0xFEFF).Trim()
    if ([string]::IsNullOrWhiteSpace($token)) { throw 'TOKEN_FILE_EMPTY' }
    if (($token -match '[^\x21-\x7E]') -or $token.StartsWith('Bearer ', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'TOKEN_FILE_FORMAT_INVALID'
    }
    $headers = @{ Authorization = ('Bearer ' + $token); Accept = 'application/json' }
    [Net.ServicePointManager]::SecurityProtocol = $oldProtocol -bor [Net.SecurityProtocolType]::Tls12
    $profile = Invoke-SandboxGet -Path '/user/profile' -AuthHeaders $headers
    if (($null -eq $profile.profile) -or ($null -eq $profile.profile.account)) { throw 'SANDBOX_ACCOUNT_SCHEMA_INVALID' }
    $report.checks['sandbox_authentication'] = 'PASS'
    $accounts = @($profile.profile.account | Where-Object { $_.status -eq 'active' })
    if ($accounts.Count -eq 0) { throw 'NO_ACTIVE_SANDBOX_ACCOUNT' }
    $report.checks['sandbox_account_resolution'] = if ($accounts.Count -eq 1) { 'SINGLE_ACTIVE_ACCOUNT' } else { 'MULTIPLE_ACTIVE_ACCOUNTS' }
    foreach ($account in $accounts) {
        $number = [string]$account.account_number
        if ($number -notmatch '^[A-Za-z0-9_-]+$') { throw 'SANDBOX_ACCOUNT_SCHEMA_INVALID' }
        foreach ($endpoint in @('balances', 'positions', 'orders')) {
            $payload = Invoke-SandboxGet -Path ('/accounts/' + $number + '/' + $endpoint) -AuthHeaders $headers
            if ($null -eq $payload.PSObject.Properties[$endpoint]) { throw 'SANDBOX_ACCOUNT_SCHEMA_INVALID' }
            if (($endpoint -eq 'balances') -and ($null -eq $payload.balances)) { throw 'SANDBOX_ACCOUNT_SCHEMA_INVALID' }
            $payload = $null
        }
    }
    foreach ($endpoint in @('balances', 'positions', 'orders')) { $report.checks['sandbox_' + $endpoint] = 'PASS' }
    $report.status = 'SANDBOX_CONNECTION_OK'
    $exitCode = 0
}
catch {
    $knownCodes = @('TOKEN_FILE_NOT_FOUND', 'TOKEN_FILE_EMPTY', 'TOKEN_FILE_FORMAT_INVALID',
        'TOKEN_FILE_IS_GIT_TRACKED', 'GIT_TRACKING_CHECK_FAILED', 'ENDPOINT_NOT_ALLOWED',
        'SANDBOX_AUTHENTICATION_REJECTED', 'SANDBOX_RATE_LIMITED', 'SANDBOX_HTTP_NETWORK_OR_TLS_ERROR',
        'SANDBOX_RESPONSE_ERROR', 'SANDBOX_ACCOUNT_SCHEMA_INVALID', 'NO_ACTIVE_SANDBOX_ACCOUNT')
    $code = $_.Exception.Message
    $report['error'] = if ($code -in $knownCodes) { $code } else { 'LOCAL_CHECK_ERROR' }
}
finally {
    if ($null -ne $headers) { $headers.Clear() }
    $token = $null
    $profile = $null
    $accounts = $null
    $account = $null
    [Net.ServicePointManager]::SecurityProtocol = $oldProtocol
}

# Persist only status codes, never accounts, financial data, or credentials.
try {
    $folder = Join-Path $env:LOCALAPPDATA 'DCR15\status'
    [void][IO.Directory]::CreateDirectory($folder)
    $reportPath = Join-Path $folder 'local-sandbox-check.json'
    $report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $reportPath -Encoding UTF8
    Write-Host ('Status file: ' + $reportPath)
} catch {
    Write-Host 'Status file not saved. Use the status lines below.'
}
$report | ConvertTo-Json -Depth 5 | Write-Output
Write-Host 'Orders submitted: 0. Trading bot: NOT STARTED.'
if ($report.error -eq 'TOKEN_FILE_IS_GIT_TRACKED') {
    Write-Host 'Stop: this file is tracked by Git. Do not push it; rotate the token if it was already shared.'
}
exit $exitCode
