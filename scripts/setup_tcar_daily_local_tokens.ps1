param(
  [string]$RepoPath = (Resolve-Path "$PSScriptRoot\.."),
  [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
Set-Location $RepoPath

function Set-TokenMasked {
  param([Parameter(Mandatory=$true)][string]$Name,[Parameter(Mandatory=$true)][string]$Label)
  $existing = [Environment]::GetEnvironmentVariable($Name, "User")
  if ($existing) {
    Write-Host "$Name already exists for this Windows user. Press Enter to keep it or enter a replacement."
  } else {
    Write-Host "Enter $Label. Input is masked."
  }
  $secure = Read-Host $Name -AsSecureString
  if ($secure.Length -eq 0) {
    if (-not $existing) { throw "$Name is required." }
    return
  }
  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    if ([string]::IsNullOrWhiteSpace($plain)) { throw "$Name cannot be blank." }
    [Environment]::SetEnvironmentVariable($Name, $plain.Trim(), "User")
  } finally {
    if ($ptr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
    $plain = $null
  }
  Write-Host "$Name saved for the current Windows user."
}

Write-Host "SOXL daily TCAR local credential setup"
Write-Host "Credentials are not written to the repository."
Set-TokenMasked -Name "TRADIER_TOKEN" -Label "Tradier production token for market data"
Set-TokenMasked -Name "TRADIER_SANDBOX_TOKEN" -Label "Tradier sandbox token for paper orders"

$env:TRADIER_TOKEN = [Environment]::GetEnvironmentVariable("TRADIER_TOKEN", "User")
$env:TRADIER_SANDBOX_TOKEN = [Environment]::GetEnvironmentVariable("TRADIER_SANDBOX_TOKEN", "User")
$env:TCAR_CHECK_REPORT = Join-Path $RepoPath "runtime\tcar_daily\local-preflight-status.json"

if (-not $env:TRADIER_TOKEN) { throw "Local TRADIER_TOKEN is missing." }
if (-not $env:TRADIER_SANDBOX_TOKEN) { throw "Local TRADIER_SANDBOX_TOKEN is missing." }

Write-Host "Running GET-only daily TCAR preflight. No orders can be submitted by this check."
& $Python scripts/tcar_daily_sandbox_preflight.py
if ($LASTEXITCODE -ne 0) { throw "TCAR Tradier preflight failed." }
Write-Host "TCAR LOCAL TOKEN SETUP: PASS"
