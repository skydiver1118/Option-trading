param(
  [string]$RepoPath = (Resolve-Path "$PSScriptRoot\.."),
  [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
Set-Location $RepoPath

function Set-TokenMasked {
  param(
    [Parameter(Mandatory=$true)][string]$Name,
    [Parameter(Mandatory=$true)][string]$Label
  )

  $existing = [Environment]::GetEnvironmentVariable($Name, "User")
  if ($existing) {
    Write-Host "$Name is already set for this Windows user."
    Write-Host "Press Enter to keep it, or paste a replacement token below. Input is masked."
  } else {
    Write-Host "Paste your $Label token below. Input is masked."
  }

  $secure = Read-Host $Name -AsSecureString
  if ($secure.Length -eq 0) {
    if (-not $existing) { throw "$Name was not supplied and no existing user value was found." }
    return
  }

  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    if ([string]::IsNullOrWhiteSpace($plain)) { throw "$Name cannot be blank." }
    [Environment]::SetEnvironmentVariable($Name, $plain.Trim(), "User")
  }
  finally {
    if ($ptr -ne [IntPtr]::Zero) {
      [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
    $plain = $null
  }
  Write-Host "$Name saved locally for the current Windows user."
}

Write-Host ""
Write-Host "DCR-15 local credential setup"
Write-Host "Tokens are saved as Windows USER environment variables on this PC."
Write-Host "They are not written to the Option-trading repository."
Write-Host ""

Set-TokenMasked -Name "TRADIER_TOKEN" -Label "Tradier PRODUCTION / market-data"
Set-TokenMasked -Name "TRADIER_SANDBOX_TOKEN" -Label "Tradier SANDBOX / paper-trading"

# Make the newly saved user values available to this setup process so the
# read-only preflight can run immediately. Values are never printed.
$env:TRADIER_TOKEN = [Environment]::GetEnvironmentVariable("TRADIER_TOKEN", "User")
$env:TRADIER_SANDBOX_TOKEN = [Environment]::GetEnvironmentVariable("TRADIER_SANDBOX_TOKEN", "User")
$env:DCR15_CHECK_REPORT = Join-Path $RepoPath "runtime\dcr15\local-preflight-status.json"

if (-not $env:TRADIER_TOKEN) { throw "Local TRADIER_TOKEN is missing." }
if (-not $env:TRADIER_SANDBOX_TOKEN) { throw "Local TRADIER_SANDBOX_TOKEN is missing." }

Write-Host ""
Write-Host "Running GET-only Tradier preflight. This check cannot submit orders."
& $Python scripts/dcr15_sandbox_preflight.py
if ($LASTEXITCODE -ne 0) {
  throw "Tradier local preflight failed. Review runtime\dcr15\local-preflight-status.json."
}

Write-Host ""
Write-Host "LOCAL TOKEN SETUP: PASS"
Write-Host "Close and reopen PowerShell/Codex before installing the scheduled task so new processes inherit the saved user variables."
