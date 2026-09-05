param(
  [string]$RepoPath = (Resolve-Path "$PSScriptRoot\.."),
  [string]$Python = "python"
)
# Historical intraday research and runtime state are preserved.
# Do not silently start a different strategy from an old deployment command.
throw "DCR-15 deployment has been retired. Use scripts/deploy_tcar_daily_paper.ps1 for SOXL DAILY TCAR, PAPER ONLY. Existing positions/orders must pass the migration check."
