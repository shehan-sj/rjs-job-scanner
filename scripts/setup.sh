#!/usr/bin/env bash
#
# One-command setup for JobFinder on macOS and Linux.
#
# Checks for Python 3.12+ and Node.js, offers to install anything missing,
# creates the virtualenv, installs dependencies, and builds the web UI.
#
# Re-runnable: every step detects what is already done and skips it, so you can
# run this again after a `git pull` without tearing anything down.
#
# Nothing is installed system-wide without asking. Pass --yes to accept those
# prompts in advance (for CI or an unattended machine).
#
# Usage:
#   ./scripts/setup.sh [--yes] [--with-chromium] [--bundle] [--skip-frontend]
#
#   --yes             Answer yes to every install prompt.
#   --with-chromium   Also download the Chromium build FillerBot drives (~150 MB).
#   --bundle          Also build the standalone dist/JobFinder folder.
#   --skip-frontend   Skip `npm ci` and the Vite build.
#   -h, --help        Show this help.

set -euo pipefail

# Keep in step with pyproject.toml's requires-python; the two drifting apart is
# how you get a venv that builds fine and then fails at import time.
MIN_MAJOR=3
MIN_MINOR=12

ASSUME_YES=0
WITH_CHROMIUM=0
BUILD_BUNDLE=0
SKIP_FRONTEND=0

while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y)         ASSUME_YES=1 ;;
    --with-chromium)  WITH_CHROMIUM=1 ;;
    --bundle)         BUILD_BUNDLE=1 ;;
    --skip-frontend)  SKIP_FRONTEND=1 ;;
    # Print the header comment block, stopping at the first line that is not a
    # comment, so this stays correct when the header is edited.
    -h|--help)        awk 'NR>1 && /^#/ { sub(/^# ?/, ""); print; next } NR>1 { exit }' "$0"; exit 0 ;;
    *) echo "Unknown option: $1  (try --help)" >&2; exit 2 ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"

if [ -t 1 ]; then
  C_CYAN=$'\033[36m'; C_GREEN=$'\033[32m'; C_RED=$'\033[31m'
  C_YELLOW=$'\033[33m'; C_GREY=$'\033[90m'; C_OFF=$'\033[0m'
else
  C_CYAN=''; C_GREEN=''; C_RED=''; C_YELLOW=''; C_GREY=''; C_OFF=''
fi

step() { printf '\n%s==> %s%s\n' "$C_CYAN" "$1" "$C_OFF"; }
ok()   { printf '    %sOK  %s%s\n' "$C_GREEN" "$1" "$C_OFF"; }
note() { printf '    %s--  %s%s\n' "$C_GREY" "$1" "$C_OFF"; }
warn() { printf '    %s!!  %s%s\n' "$C_YELLOW" "$1" "$C_OFF"; }

fail() {
  printf '\n%sSETUP FAILED: %s%s\n' "$C_RED" "$1" "$C_OFF" >&2
  if [ -n "${2:-}" ]; then
    printf '\n%sTo fix it:%s\n%s\n' "$C_YELLOW" "$C_OFF" "$2" >&2
  fi
  exit 1
}

confirm_install() {
  if [ "$ASSUME_YES" -eq 1 ]; then
    note "--yes given, installing $1 without asking"
    return 0
  fi
  printf '\n    %s%s is not installed.%s\n' "$C_YELLOW" "$1" "$C_OFF"
  printf '    Install it now with %s? [y/N] ' "$2"
  read -r answer </dev/tty || answer=""
  case "$answer" in y|Y) return 0 ;; *) return 1 ;; esac
}

have() { command -v "$1" >/dev/null 2>&1; }

OS="$(uname -s)"
case "$OS" in
  Darwin) PLATFORM="macOS" ;;
  Linux)  PLATFORM="Linux" ;;
  *) fail "Unsupported platform: $OS" "    This script covers macOS and Linux. On Windows use scripts/setup.ps1." ;;
esac

# Package manager, and the command that installs a named package with it.
PKG=""
if [ "$PLATFORM" = "macOS" ]; then
  if have brew; then PKG="brew"; fi
else
  if   have apt-get; then PKG="apt"
  elif have dnf;     then PKG="dnf"
  elif have pacman;  then PKG="pacman"
  elif have zypper;  then PKG="zypper"
  fi
fi

# Echoes the install command for a logical package, or nothing if unsupported.
install_cmd() {
  case "$1:$PKG" in
    python:brew)   echo "brew install python@3.12" ;;
    python:apt)    echo "sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip" ;;
    python:dnf)    echo "sudo dnf install -y python3 python3-pip" ;;
    python:pacman) echo "sudo pacman -S --noconfirm python python-pip" ;;
    python:zypper) echo "sudo zypper install -y python3 python3-pip" ;;
    node:brew)     echo "brew install node" ;;
    node:apt)      echo "sudo apt-get update && sudo apt-get install -y nodejs npm" ;;
    node:dnf)      echo "sudo dnf install -y nodejs npm" ;;
    node:pacman)   echo "sudo pacman -S --noconfirm nodejs npm" ;;
    node:zypper)   echo "sudo zypper install -y nodejs npm" ;;
    *) echo "" ;;
  esac
}

# Prints the path of the first python meeting the minimum, or nothing.
find_python() {
  local candidate resolved ver maj min
  for candidate in python3.14 python3.13 python3.12 python3 python; do
    have "$candidate" || continue
    resolved="$("$candidate" -c 'import sys; print(sys.executable)' 2>/dev/null)" || continue
    [ -n "$resolved" ] || continue
    ver="$("$resolved" -c 'import sys; print("%d %d" % sys.version_info[:2])' 2>/dev/null)" || continue
    maj="${ver% *}"; min="${ver#* }"
    if [ "$maj" -gt "$MIN_MAJOR" ] || { [ "$maj" -eq "$MIN_MAJOR" ] && [ "$min" -ge "$MIN_MINOR" ]; }; then
      echo "$resolved"
      return 0
    fi
    note "found Python $maj.$min at $resolved (too old, need $MIN_MAJOR.$MIN_MINOR+)" >&2
  done
  return 1
}

printf '\nJobFinder setup (%s)\n' "$PLATFORM"
printf '%sRepository: %s%s\n' "$C_GREY" "$REPO_ROOT" "$C_OFF"

# ----------------------------------------------------------------- Python ---
step "Checking for Python $MIN_MAJOR.$MIN_MINOR or newer"

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
  cmd="$(install_cmd python)"
  if [ -z "$cmd" ]; then
    if [ "$PLATFORM" = "macOS" ]; then
      fail "No Python $MIN_MAJOR.$MIN_MINOR+ found, and Homebrew is not installed." \
"    Install Homebrew, then re-run this script:
        /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"

    Or install Python $MIN_MAJOR.$MIN_MINOR+ directly from https://www.python.org/downloads/macos/"
    else
      fail "No Python $MIN_MAJOR.$MIN_MINOR+ found, and no supported package manager was detected." \
"    Install Python $MIN_MAJOR.$MIN_MINOR+ with your distribution's package manager, then re-run.
    If your distribution does not ship it yet, pyenv is the usual way:
        https://github.com/pyenv/pyenv#installation"
    fi
  fi
  if ! confirm_install "Python $MIN_MAJOR.$MIN_MINOR" "$PKG"; then
    fail "Python $MIN_MAJOR.$MIN_MINOR+ is required." "    Install it yourself and re-run this script:
        $cmd"
  fi
  note "running: $cmd"
  eval "$cmd" || fail "the package manager could not install Python." "    Install it by hand and re-run:
        $cmd"

  PYTHON="$(find_python || true)"
  if [ -z "$PYTHON" ]; then
    fail "Python was installed but no interpreter at $MIN_MAJOR.$MIN_MINOR+ is on PATH." \
"    Your distribution's default 'python3' may still be older than $MIN_MAJOR.$MIN_MINOR.
    Install a newer one (pyenv, deadsnakes on Debian/Ubuntu, or python.org) and re-run."
  fi
fi
ok "Python $("$PYTHON" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])') at $PYTHON"

# ------------------------------------------------------------------- Node ---
if [ "$SKIP_FRONTEND" -eq 0 ]; then
  step "Checking for Node.js and npm"
  if ! have npm; then
    cmd="$(install_cmd node)"
    if [ -z "$cmd" ]; then
      fail "Node.js is not installed and no supported package manager was detected." \
"    Install the Node.js LTS from https://nodejs.org/en/download and re-run,
    or re-run this script with --skip-frontend if the UI is already built."
    fi
    if ! confirm_install "Node.js" "$PKG"; then
      fail "Node.js is required to build the web UI." "    Either install it:
        $cmd
    or re-run this script with --skip-frontend if the UI is already built."
    fi
    note "running: $cmd"
    eval "$cmd" || fail "the package manager could not install Node.js." "    Install it by hand and re-run:
        $cmd"
    have npm || fail "Node.js was installed but npm is still not on PATH." "    Open a new shell and run this script again."
  fi
  ok "Node $(node --version) / npm $(npm --version)"
fi

# ------------------------------------------------------------------- venv ---
step "Creating the virtualenv (.venv)"
if [ -x "$VENV_PYTHON" ]; then
  ok ".venv already exists"
else
  if ! "$PYTHON" -m venv "$REPO_ROOT/.venv" 2>/dev/null; then
    # Debian and Ubuntu ship venv as a separate package, and the error you get
    # without it ("ensurepip is not available") does not say so.
    fail "python -m venv failed." \
"    On Debian or Ubuntu the venv module ships separately:
        sudo apt-get install -y python3-venv
    Then run this script again."
  fi
  ok "created .venv"
fi

step "Installing Python dependencies"
"$VENV_PYTHON" -m pip install --upgrade pip --quiet
"$VENV_PYTHON" -m pip install -r "$REPO_ROOT/requirements.txt" \
  || fail "pip install -r requirements.txt failed."
ok "requirements.txt installed"

if [ "$BUILD_BUNDLE" -eq 1 ]; then
  step "Installing build dependencies (PyInstaller)"
  "$VENV_PYTHON" -m pip install -r "$REPO_ROOT/requirements-build.txt" \
    || fail "pip install -r requirements-build.txt failed."
  ok "requirements-build.txt installed"
fi

# --------------------------------------------------------------- frontend ---
if [ "$SKIP_FRONTEND" -eq 0 ]; then
  step "Building the web UI"
  (
    cd "$REPO_ROOT/frontend"
    if ! npm ci; then
      fail "npm ci failed." \
"    If this failed with ERESOLVE, frontend/package.json's 'overrides' entry for
    TypeScript has been lost. openapi-typescript 7.x declares a peer on
    TypeScript 5 while this project is on TypeScript 6; that override is what
    reconciles them."
    fi
    npm run build || fail "npm run build failed."
  )
  ok "frontend/dist built"
fi

# --------------------------------------------------------------- Chromium ---
if [ "$WITH_CHROMIUM" -eq 1 ]; then
  step "Downloading Chromium for FillerBot"
  # Playwright does not read JOBFINDER_DATA_DIR, and jobfinder/paths.py points
  # PLAYWRIGHT_BROWSERS_PATH at <data dir>/browsers at startup -- so the
  # download has to be aimed at that exact directory or FillerBot will not find
  # it at runtime.
  BROWSERS_DIR="$("$VENV_PYTHON" -c "import jobfinder.paths as p; print(p.get_data_dir() / 'browsers')")" \
    || fail "could not resolve the data directory."
  note "installing into $BROWSERS_DIR"
  PLAYWRIGHT_BROWSERS_PATH="$BROWSERS_DIR" "$VENV_PYTHON" -m playwright install chromium \
    || fail "playwright install chromium failed."
  ok "Chromium installed"
fi

# ----------------------------------------------------------------- bundle ---
if [ "$BUILD_BUNDLE" -eq 1 ]; then
  step "Building the standalone bundle"
  "$VENV_PYTHON" "$REPO_ROOT/scripts/build_app.py" || fail "scripts/build_app.py failed."
  ok "bundle at $REPO_ROOT/dist/JobFinder"
fi

# ------------------------------------------------------------------ check ---
step "Verifying the install"
"$VENV_PYTHON" "$REPO_ROOT/scripts/smoke.py" \
  || fail "the smoke check failed." "    The output above names the failing step."

printf '\n%sSetup complete.%s\n\n' "$C_GREEN" "$C_OFF"
echo "Start JobFinder:"
echo "    ./jobfinder.sh"
echo ""
note "Use that launcher rather than calling python directly: -m jobfinder needs"
note "both the venv's interpreter and the repo root as the working directory,"
note "and getting either wrong reports 'No module named jobfinder'."
echo ""
echo "It binds a free loopback port and opens your browser on an"
echo "authenticated URL. Add --no-browser to print the URL instead."
if [ "$WITH_CHROMIUM" -eq 0 ]; then
  echo ""
  warn "FillerBot needs Chromium, which was not installed."
  echo "        Re-run with --with-chromium when you want it (~150 MB)."
fi
echo ""
