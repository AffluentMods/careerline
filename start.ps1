# SPDX-License-Identifier: Apache-2.0
# Start Waypoint locally (Windows). First run installs dependencies.
# Usage: .\start.ps1            -> http://localhost:8080
#        .\start.ps1 -Dev       -> hot-reload dev servers
#        .\start.ps1 -Rebuild   -> rebuild the frontend first
#        .\start.ps1 -Demo      -> demo mode with fictional data (separate database)
#        .\start.ps1 -VerifyBackup <file>  -> check that a backup opens
#        .\start.ps1 -Restore <file>       -> restore a backup into an empty database (see docs/operations.md)
#        .\start.ps1 -RotateKey          -> new encryption key, re-encrypt everything (Waypoint stopped)
#        .\start.ps1 -KeyStatus          -> which encryption keys are still in use
param([switch]$Dev, [switch]$Rebuild, [switch]$Demo, [string]$Restore, [string]$VerifyBackup,
      [switch]$RotateKey, [switch]$Rekey, [switch]$KeyStatus)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Run a native program and fail on its exit code. Tools like uv and npm print
# progress to stderr, which Windows PowerShell 5.1 would otherwise treat as an
# error when output is redirected (for example to a log file).
function Invoke-Native([string]$What, [scriptblock]$Command) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Command 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $userScripts = Get-ChildItem "$env:APPDATA\Python\*\Scripts\uv.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($userScripts) { $env:PATH = "$($userScripts.DirectoryName);$env:PATH" }
    else { Write-Error "uv is not installed. Install it with:  winget install astral-sh.uv" }
}

Push-Location backend
try { Invoke-Native "Installing backend dependencies" { uv sync --frozen --python 3.12 } } finally { Pop-Location }
if (-not (Test-Path frontend\node_modules)) {
    Push-Location frontend
    try { Invoke-Native "Installing frontend dependencies" { npm ci } } finally { Pop-Location }
}

$runArgs = @()
if ($Dev) { $runArgs += "--dev" }
if ($Rebuild) { $runArgs += "--rebuild" }
if ($Demo) { $runArgs += "--demo" }
if ($Restore) { $runArgs += @("--restore", $Restore) }
if ($VerifyBackup) { $runArgs += @("--verify-backup", $VerifyBackup) }
if ($RotateKey) { $runArgs += "--rotate-key" }
if ($Rekey) { $runArgs += "--rekey" }
if ($KeyStatus) { $runArgs += "--key-status" }
$ErrorActionPreference = "Continue"
& backend\.venv\Scripts\python.exe backend\scripts\run_local.py @runArgs
exit $LASTEXITCODE
