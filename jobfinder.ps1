<#
.SYNOPSIS
    Start JobFinder from anywhere, with the right interpreter.

.DESCRIPTION
    `python -m jobfinder` has two requirements that are easy to miss, and
    missing either gives a confusing error:

      1. It must be the virtualenv's interpreter. A bare `python.exe` typed
         inside .venv\Scripts does NOT resolve to the one sitting there --
         PowerShell never searches the current directory, so it silently picks
         whatever Python is on PATH and reports "No module named jobfinder".

      2. The working directory must be the repository root. The package is not
         pip-installed into the venv; it is found because the repo root is on
         sys.path as the current directory.

    This script resolves its own location, so neither depends on where you
    happen to be standing.

.EXAMPLE
    .\jobfinder.ps1

.EXAMPLE
    .\jobfinder.ps1 --no-browser

.NOTES
    Every argument is passed straight through to `python -m jobfinder`.
#>

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $VenvPython)) {
    Write-Host ""
    Write-Host "JobFinder is not set up yet: no virtualenv at .venv" -ForegroundColor Red
    Write-Host ""
    Write-Host "Run this first:" -ForegroundColor Yellow
    Write-Host "    .\scripts\setup.ps1"
    Write-Host ""
    exit 1
}

# Push-Location rather than Set-Location so the caller's directory is restored
# even if the server exits non-zero.
Push-Location $RepoRoot
try {
    & $VenvPython -m jobfinder @args
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
