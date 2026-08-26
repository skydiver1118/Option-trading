function Invoke-NativeLogged {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [string[]]$ArgumentList = @(),

        [Parameter(Mandatory = $true)]
        [string]$LogFile,

        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    # Windows PowerShell 5.1 converts redirected native stderr into ErrorRecord
    # objects. With ErrorActionPreference=Stop, harmless diagnostics then become
    # terminating PowerShell errors even when the native process exits zero.
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $nativeOutput = @(& $FilePath @ArgumentList 2>&1)
        $nativeExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    $nativeLines = @($nativeOutput | ForEach-Object { $_.ToString() })
    if ($nativeLines.Count -gt 0) {
        $nativeLines | Tee-Object -FilePath $LogFile -Append
    }

    if ($nativeExitCode -ne 0) {
        throw "$Name exited with code $nativeExitCode"
    }
}
