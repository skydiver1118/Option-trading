param(
    [string]$SourceRepo = "$env:USERPROFILE\Documents\Option-trading",
    [string]$DashboardRepo = "$env:USERPROFILE\Documents\Option-trading-dashboard-bot",
    [string]$TcarFolder = "$env:USERPROFILE\Documents\TCAR-trading",
    [string]$TcarRepository = "skydiver1118/TCAR-trading",
    [ValidateSet('private','public')]
    [string]$Visibility = 'private',
    [switch]$SkipDashboardCleanupPR
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Invoke-Native {
    param([string]$FilePath, [string[]]$ArgumentList, [string]$Name)
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

function Get-GhPath {
    $command = Get-Command gh -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }

    $candidates = @(
        "$env:ProgramFiles\GitHub CLI\gh.exe",
        "$env:LOCALAPPDATA\Programs\GitHub CLI\gh.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'GitHub CLI is not installed and winget is unavailable.'
    }
    Invoke-Native 'winget' @(
        'install','--id','GitHub.cli','--exact','--source','winget',
        '--accept-package-agreements','--accept-source-agreements'
    ) 'Install GitHub CLI'

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw 'GitHub CLI was installed but gh.exe could not be located. Reopen PowerShell and rerun.'
}

function Ensure-GhAuthentication {
    param([string]$Gh)
    & $Gh auth status --hostname github.com
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'GitHub authentication is required. A browser window will open.'
        Invoke-Native $Gh @(
            'auth','login','--hostname','github.com',
            '--git-protocol','https','--web'
        ) 'GitHub authentication'
    }
}

function Copy-CurrentWorkingTree {
    param([string]$Source, [string]$Destination)

    $files = & git -C $Source ls-files --cached --others --exclude-standard
    if ($LASTEXITCODE -ne 0) { throw 'Unable to enumerate the TCAR working tree.' }

    foreach ($relativeRaw in $files) {
        $relative = $relativeRaw -replace '\\','/'
        $sourcePath = Join-Path $Source ($relative -replace '/','\')
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) { continue }

        $destinationPath = Join-Path $Destination ($relative -replace '/','\')
        $parent = Split-Path -Parent $destinationPath
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
        Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Force
    }

    $deleted = & git -C $Source ls-files --deleted
    if ($LASTEXITCODE -ne 0) { throw 'Unable to enumerate deleted TCAR files.' }
    foreach ($relativeRaw in $deleted) {
        $relative = $relativeRaw -replace '\\','/'
        $destinationPath = Join-Path $Destination ($relative -replace '/','\')
        Remove-Item -LiteralPath $destinationPath -Force -ErrorAction SilentlyContinue
    }
}

function Remove-DashboardFilesFromTcar {
    param([string]$RepositoryPath)

    $paths = @(
        'README.md','app.js','index.html','styles.css','requirements.txt','.nojekyll',
        'windows','runtime','logs','.secrets',
        'data/dashboard.json','data/mrvl_calendar.json',
        'scripts/update_dashboard.py','scripts/update_dashboard_dynamic.py',
        'scripts/mrvl_calendar_snapshot.py','tests/test_update_dashboard.py',
        '.github/workflows/refresh.yml','.github/workflows/pages.yml',
        '.github/workflows/enforce_october.yml',
        '.github/workflows/guard-dashboard-expiration.yml',
        '.github/workflows/deploy-dashboard.yml',
        '.github/workflows/refresh-option-dashboard.yml'
    )
    foreach ($relative in $paths) {
        Remove-Item -LiteralPath (Join-Path $RepositoryPath $relative) -Recurse -Force -ErrorAction SilentlyContinue
    }

    $workflowDir = Join-Path $RepositoryPath '.github\workflows'
    if (Test-Path $workflowDir) {
        Get-ChildItem $workflowDir -File | Where-Object {
            $_.Name -match '(?i)(dashboard|pages|enforce[_-]?october)'
        } | Remove-Item -Force
    }
}

function Write-TcarReadme {
    param([string]$RepositoryPath)
    $content = @'
# TCAR Trading Research

Dedicated repository for TCAR strategy research, backtesting, deployment,
execution-safety checks, reporting, and related systematic-trading work.

This repository was split from `skydiver1118/Option-trading` so active TCAR
work cannot block or modify the automated option-dashboard refresh process.

## Repository boundary

- TCAR and related strategy-development code belongs here.
- The live option dashboard remains in `skydiver1118/Option-trading`.
- Runtime state, credentials, local logs, and encrypted token files are not committed.
'@
    Set-Content -Path (Join-Path $RepositoryPath 'README.md') -Value $content -Encoding utf8

    $ignorePath = Join-Path $RepositoryPath '.gitignore'
    $extra = @'

# Local runtime, credentials and caches
.env
.env.*
runtime/
logs/
**/.secrets/
__pycache__/
.pytest_cache/
*.pyc
'@
    Add-Content -Path $ignorePath -Value $extra -Encoding utf8
}

function Set-RepositorySecretFromDpapi {
    param([string]$Gh, [string]$Repository, [string]$TokenFile)
    if (-not (Test-Path $TokenFile)) {
        Write-Warning "Tradier token file not found: $TokenFile"
        return
    }

    $secure = Get-Content $TokenFile | ConvertTo-SecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        $plain | & $Gh secret set TRADIER_TOKEN --repo $Repository
        if ($LASTEXITCODE -ne 0) { throw 'Unable to create TRADIER_TOKEN in the TCAR repository.' }
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        $plain = $null
    }
}

function Create-DashboardCleanupPR {
    param([string]$Gh, [string]$RepositoryPath)

    $dirty = (& git -C $RepositoryPath status --porcelain | Out-String).Trim()
    if ($dirty) {
        throw "Dashboard automation clone is not clean: $RepositoryPath"
    }

    Invoke-Native 'git' @('-C',$RepositoryPath,'fetch','origin','main') 'Fetch dashboard main'

    $branch = 'split/dashboard-only-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
    $worktree = Join-Path $env:TEMP ('option-dashboard-cleanup-' + $PID)
    if (Test-Path $worktree) { Remove-Item $worktree -Recurse -Force }

    try {
        Invoke-Native 'git' @(
            '-C',$RepositoryPath,'worktree','add','-B',$branch,$worktree,'origin/main'
        ) 'Create dashboard cleanup worktree'

        Invoke-Native 'git' @('-C',$worktree,'rm','-r','--ignore-unmatch','.') 'Clear mixed repository tree'

        $keep = @(
            '.gitignore','README.md','app.js','index.html','styles.css','requirements.txt',
            'data/dashboard.json','data/mrvl_calendar.json',
            'scripts/update_dashboard.py','scripts/update_dashboard_dynamic.py',
            'scripts/mrvl_calendar_snapshot.py','tests/test_update_dashboard.py',
            'windows','.github/workflows/refresh.yml','.github/workflows/pages.yml'
        )

        foreach ($relative in $keep) {
            & git -C $worktree cat-file -e "origin/main:$relative" 2>$null
            if ($LASTEXITCODE -eq 0) {
                Invoke-Native 'git' @('-C',$worktree,'checkout','origin/main','--',$relative) "Restore $relative"
            }
        }

        Invoke-Native 'git' @('-C',$worktree,'add','-A') 'Stage dashboard-only split'
        Invoke-Native 'git' @(
            '-C',$worktree,'commit','-m','Separate option dashboard from TCAR research'
        ) 'Commit dashboard-only split'
        Invoke-Native 'git' @(
            '-C',$worktree,'push','-u','origin',$branch
        ) 'Push dashboard cleanup branch'

        $body = @"
TCAR and strategy-development files have been copied to
https://github.com/$TcarRepository.

This PR leaves `Option-trading` as the dashboard-only repository so:

- GitHub Pages keeps its current URL;
- the Windows 10:00/12:00/14:00 refresh remains isolated;
- TCAR development cannot dirty or block the dashboard automation clone.
"@
        $prUrl = & $Gh pr create --repo 'skydiver1118/Option-trading' `
            --base main --head $branch `
            --title 'Separate dashboard repository from TCAR development' `
            --body $body
        if ($LASTEXITCODE -ne 0) { throw 'Unable to create the dashboard cleanup pull request.' }
        Write-Host "Dashboard cleanup PR: $prUrl"
    }
    finally {
        & git -C $RepositoryPath worktree remove $worktree --force 2>$null
    }
}

if (-not (Test-Path (Join-Path $SourceRepo '.git'))) {
    throw "TCAR source repository not found: $SourceRepo"
}
if (-not (Test-Path (Join-Path $DashboardRepo '.git'))) {
    throw "Dashboard automation clone not found: $DashboardRepo"
}
if (Test-Path $TcarFolder) {
    throw "Destination already exists: $TcarFolder"
}

$gh = Get-GhPath
Ensure-GhAuthentication -Gh $gh

& $gh repo view $TcarRepository --json name 2>$null
if ($LASTEXITCODE -eq 0) {
    throw "GitHub repository already exists: $TcarRepository"
}

Write-Host "Cloning TCAR history from $SourceRepo..."
Invoke-Native 'git' @('clone','--no-local',$SourceRepo,$TcarFolder) 'Clone TCAR history'
Invoke-Native 'git' @('-C',$TcarFolder,'remote','remove','origin') 'Remove temporary local remote'
Copy-CurrentWorkingTree -Source $SourceRepo -Destination $TcarFolder
Remove-DashboardFilesFromTcar -RepositoryPath $TcarFolder
Write-TcarReadme -RepositoryPath $TcarFolder

$name = (& git -C $SourceRepo config user.name | Out-String).Trim()
$email = (& git -C $SourceRepo config user.email | Out-String).Trim()
if (-not $name) { $name = 'skydiver1118' }
if (-not $email) { $email = '149340462+skydiver1118@users.noreply.github.com' }
Invoke-Native 'git' @('-C',$TcarFolder,'config','user.name',$name) 'Set TCAR Git user name'
Invoke-Native 'git' @('-C',$TcarFolder,'config','user.email',$email) 'Set TCAR Git email'
Invoke-Native 'git' @('-C',$TcarFolder,'add','-A') 'Stage TCAR split'
Invoke-Native 'git' @(
    '-C',$TcarFolder,'commit','-m','Split TCAR development from option dashboard'
) 'Commit TCAR split'

$visibilityFlag = if ($Visibility -eq 'public') { '--public' } else { '--private' }
Invoke-Native $gh @(
    'repo','create',$TcarRepository,$visibilityFlag,
    '--source',$TcarFolder,'--remote','origin',
    '--description','TCAR strategy research, backtesting, deployment and reporting'
) 'Create TCAR GitHub repository'

Set-RepositorySecretFromDpapi -Gh $gh -Repository $TcarRepository `
    -TokenFile (Join-Path $SourceRepo 'windows\.secrets\tradier_token.txt')
'1118xmb@gmail.com' | & $gh secret set STOCK_EMAIL_TO --repo $TcarRepository
if ($LASTEXITCODE -ne 0) { Write-Warning 'Unable to create STOCK_EMAIL_TO in the TCAR repository.' }

Invoke-Native 'git' @('-C',$TcarFolder,'push','-u','origin','main') 'Push TCAR repository'
Write-Host "TCAR repository created: https://github.com/$TcarRepository"
Write-Host "Local TCAR folder: $TcarFolder"

if (-not $SkipDashboardCleanupPR) {
    Create-DashboardCleanupPR -Gh $gh -RepositoryPath $DashboardRepo
}

Write-Host 'Repository split preparation completed successfully.'
