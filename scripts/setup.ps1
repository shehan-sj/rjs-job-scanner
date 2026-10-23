<#
.SYNOPSIS
    One-command setup for JobFinder on Windows.

.DESCRIPTION
    Checks for Python 3.12+ and Node.js, offers to install anything missing,
    creates the virtualenv, installs dependencies, and builds the web UI.

    Re-runnable: every step detects what is already done and skips it, so you
    can run this again after a `git pull` without tearing anything down.

    Nothing is installed system-wide without asking. Pass -Yes to accept those
    prompts in advance (for CI or an unattended machine).

.PARAMETER Yes
    Answer yes to every install prompt. Without it, the script asks before
    installing Python or Node.js and exits if you decline.

.PARAMETER WithChromium
    Also download the Chromium build FillerBot drives. ~150 MB. FillerBot is
    the only feature that needs it; everything else works without.

.PARAMETER Bundle
    Also build the standalone dist\JobFinder folder with PyInstaller. Only
    needed if you want to copy JobFinder to a machine with no Python.

.PARAMETER SkipFrontend
    Skip `npm ci` and the Vite build. Only useful if you just built the UI and
    changed Python code; without a built UI the server serves a blank page.

.EXAMPLE
    .\scripts\setup.ps1

.EXAMPLE
    .\scripts\setup.ps1 -Yes -WithChromium

.NOTES
    Run from anywhere; the script locates the repository itself.
#>

[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$WithChromium,
    [switch]$Bundle,
    [switch]$SkipFrontend
)

$ErrorActionPreference = 'Stop'

# Minimum supported interpreter. Keep in step with pyproject.toml's
# requires-python; the two drifting apart is how you get a venv that builds
# fine and then fails at import time.
$MinPythonMajor = 3
$MinPythonMinor = 12

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'

function Write-Step  { param([string]$m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok    { param([string]$m) Write-Host "    OK  $m" -ForegroundColor Green }
function Write-Note  { param([string]$m) Write-Host "    --  $m" -ForegroundColor DarkGray }
function Write-Warn2 { param([string]$m) Write-Host "    !!  $m" -ForegroundColor Yellow }

function Fail {
    param([string]$Message, [string]$Fix)
    Write-Host ""
    Write-Host "SETUP FAILED: $Message" -ForegroundColor Red
    if ($Fix) {
        Write-Host ""
        Write-Host "To fix it:" -ForegroundColor Yellow
        Write-Host $Fix
    }
    exit 1
}

function Confirm-Install {
    param([string]$What)
    if ($Yes) {
        Write-Note "-Yes given, installing $What without asking"
        return $true
    }
    Write-Host ""
    Write-Host "    $What is not installed." -ForegroundColor Yellow
    $answer = Read-Host "    Install it now with winget? [y/N]"
    return ($answer -eq 'y' -or $answer -eq 'Y')
}

function Test-CommandExists {
    param([string]$Name)
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    return ($null -ne $cmd)
}

# Returns the path to a python.exe meeting the minimum, or $null.
# Tries the `py` launcher first because on Windows it resolves real
# installations, while a bare `python` is often the Microsoft Store stub that
# exits 9009 and opens the Store instead of running anything.
function Find-SystemPython {
    $candidates = @()
    if (Test-CommandExists 'py') {
        $candidates += ,@('py', @('-3', '-c', 'import sys; print(sys.executable)'))
    }
    foreach ($name in @('python3', 'python')) {
        if (Test-CommandExists $name) {
            $candidates += ,@($name, @('-c', 'import sys; print(sys.executable)'))
        }
    }

    foreach ($candidate in $candidates) {
        $exe = $candidate[0]
        $argv = $candidate[1]
        try {
            $resolved = & $exe @argv 2>$null
        } catch {
            continue
        }
        if (-not $resolved) { continue }
        $resolved = $resolved.Trim()
        if (-not (Test-Path $resolved)) { continue }

        try {
            $ver = & $resolved -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        } catch {
            continue
        }
        if (-not $ver) { continue }
        $parts = $ver.Trim().Split('.')
        $maj = [int]$parts[0]
        $min = [int]$parts[1]
        if ($maj -gt $MinPythonMajor -or ($maj -eq $MinPythonMajor -and $min -ge $MinPythonMinor)) {
            return $resolved
        }
        Write-Note "found Python $ver at $resolved (too old, need $MinPythonMajor.$MinPythonMinor+)"
    }
    return $null
}

Write-Host ""
Write-Host "JobFinder setup (Windows)" -ForegroundColor White
Write-Host "Repository: $RepoRoot" -ForegroundColor DarkGray

# ---------------------------------------------------------------- Python ----
Write-Step "Checking for Python $MinPythonMajor.$MinPythonMinor or newer"

$python = Find-SystemPython
if (-not $python) {
    if (-not (Test-CommandExists 'winget')) {
        Fail "No Python $MinPythonMajor.$MinPythonMinor+ found, and winget is not available to install it." @"
    Install Python $MinPythonMajor.$MinPythonMinor or newer by hand, then run this script again:
        https://www.python.org/downloads/windows/

    On the installer's first screen, tick "Add python.exe to PATH".
"@
    }
    if (-not (Confirm-Install "Python $MinPythonMajor.$MinPythonMinor")) {
        Fail "Python $MinPythonMajor.$MinPythonMinor+ is required." @"
    Install it yourself and re-run this script:
        winget install --id Python.Python.3.12 -e
"@
    }
    Write-Note "running: winget install --id Python.Python.3.12 -e"
    winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        Fail "winget could not install Python (exit $LASTEXITCODE)." "    Install it by hand from https://www.python.org/downloads/windows/ and re-run."
    }

    # winget updates the machine PATH, but this already-running shell keeps the
    # environment block it started with, so re-read it rather than telling the
    # user to open a new terminal.
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [System.Environment]::GetEnvironmentVariable('Path', 'User')

    $python = Find-SystemPython
    if (-not $python) {
        Fail "Python was installed but is still not on PATH in this shell." "    Close this terminal, open a new one, and run this script again."
    }
}
$pyver = & $python -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
Write-Ok "Python $($pyver.Trim()) at $python"

# ------------------------------------------------------------------ Node ----
if (-not $SkipFrontend) {
    Write-Step "Checking for Node.js and npm"
    if (-not (Test-CommandExists 'npm')) {
        if (-not (Test-CommandExists 'winget')) {
            Fail "Node.js is not installed and winget is not available." @"
    Install the Node.js LTS by hand, then run this script again:
        https://nodejs.org/en/download
"@
        }
        if (-not (Confirm-Install "Node.js LTS")) {
            Fail "Node.js is required to build the web UI." @"
    Either install it:
        winget install --id OpenJS.NodeJS.LTS -e
    or re-run this script with -SkipFrontend if the UI is already built.
"@
        }
        Write-Note "running: winget install --id OpenJS.NodeJS.LTS -e"
        winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0) {
            Fail "winget could not install Node.js (exit $LASTEXITCODE)." "    Install it by hand from https://nodejs.org/en/download and re-run."
        }
        $env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                    [System.Environment]::GetEnvironmentVariable('Path', 'User')
        if (-not (Test-CommandExists 'npm')) {
            Fail "Node.js was installed but npm is still not on PATH in this shell." "    Close this terminal, open a new one, and run this script again."
        }
    }
    $nodeVersion = (& node --version).Trim()
    $npmVersion = (& npm --version).Trim()
    Write-Ok "Node $nodeVersion / npm $npmVersion"
}

# ----------------------------------------------------------------- venv -----
Write-Step "Creating the virtualenv (.venv)"
if (Test-Path $VenvPython) {
    Write-Ok ".venv already exists"
} else {
    & $python -m venv (Join-Path $RepoRoot '.venv')
    if ($LASTEXITCODE -ne 0) { Fail "python -m venv failed (exit $LASTEXITCODE)." }
    Write-Ok "created .venv"
}

Write-Step "Installing Python dependencies"
& $VenvPython -m pip install --upgrade pip --quiet
& $VenvPython -m pip install -r (Join-Path $RepoRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Fail "pip install -r requirements.txt failed (exit $LASTEXITCODE)." }
Write-Ok "requirements.txt installed"

if ($Bundle) {
    Write-Step "Installing build dependencies (PyInstaller)"
    & $VenvPython -m pip install -r (Join-Path $RepoRoot 'requirements-build.txt')
    if ($LASTEXITCODE -ne 0) { Fail "pip install -r requirements-build.txt failed (exit $LASTEXITCODE)." }
    Write-Ok "requirements-build.txt installed"
}

# -------------------------------------------------------------- frontend ----
if (-not $SkipFrontend) {
    Write-Step "Building the web UI"
    Push-Location (Join-Path $RepoRoot 'frontend')
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) {
            Fail "npm ci failed (exit $LASTEXITCODE)." @"
    If this failed with ERESOLVE, frontend/package.json's `overrides` entry for
    TypeScript has been lost. openapi-typescript 7.x declares a peer on
    TypeScript 5 while this project is on TypeScript 6; that override is what
    reconciles them.
"@
        }
        npm run build
        if ($LASTEXITCODE -ne 0) { Fail "npm run build failed (exit $LASTEXITCODE)." }
    } finally {
        Pop-Location
    }
    Write-Ok "frontend/dist built"
}

# -------------------------------------------------------------- Chromium ----
if ($WithChromium) {
    Write-Step "Downloading Chromium for FillerBot"
    # Playwright does not read JOBFINDER_DATA_DIR, and jobfinder/paths.py points
    # PLAYWRIGHT_BROWSERS_PATH at <data dir>/browsers at startup -- so the
    # download has to be aimed at that exact directory or FillerBot will not
    # find it at runtime.
    $browsersDir = & $VenvPython -c "import jobfinder.paths as p; print(p.get_data_dir() / 'browsers')"
    if ($LASTEXITCODE -ne 0) { Fail "could not resolve the data directory (exit $LASTEXITCODE)." }
    $browsersDir = $browsersDir.Trim()
    Write-Note "installing into $browsersDir"
    $env:PLAYWRIGHT_BROWSERS_PATH = $browsersDir
    & $VenvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { Fail "playwright install chromium failed (exit $LASTEXITCODE)." }
    Write-Ok "Chromium installed"
}

# ---------------------------------------------------------------- bundle ----
if ($Bundle) {
    Write-Step "Building the standalone bundle"
    & $VenvPython (Join-Path $RepoRoot 'scripts\build_app.py')
    if ($LASTEXITCODE -ne 0) { Fail "scripts/build_app.py failed (exit $LASTEXITCODE)." }
    Write-Ok "bundle at $(Join-Path $RepoRoot 'dist\JobFinder')"
}

# ----------------------------------------------------------------- check ----
Write-Step "Verifying the install"
& $VenvPython (Join-Path $RepoRoot 'scripts\smoke.py')
if ($LASTEXITCODE -ne 0) {
    Fail "the smoke check failed (exit $LASTEXITCODE)." "    The output above names the failing step."
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "Start JobFinder:" -ForegroundColor White
Write-Host "    .\jobfinder.ps1"
Write-Host ""
Write-Host "Use that launcher rather than calling python directly: -m jobfinder" -ForegroundColor DarkGray
Write-Host "needs both the venv's interpreter and the repo root as the working" -ForegroundColor DarkGray
Write-Host "directory, and getting either wrong reports 'No module named jobfinder'." -ForegroundColor DarkGray
Write-Host ""
Write-Host "It binds a free loopback port and opens your browser on an"
Write-Host "authenticated URL. Add --no-browser to print the URL instead."
if (-not $WithChromium) {
    Write-Host ""
    Write-Warn2 "FillerBot needs Chromium, which was not installed."
    Write-Host "        Re-run with -WithChromium when you want it (~150 MB)."
}
Write-Host ""
