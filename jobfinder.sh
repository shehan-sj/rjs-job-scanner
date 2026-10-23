#!/usr/bin/env bash
#
# Start JobFinder from anywhere, with the right interpreter.
#
# `python -m jobfinder` has two requirements that are easy to miss, and missing
# either gives a confusing error:
#
#   1. It must be the virtualenv's interpreter, not whichever python is on
#      PATH. A system python reports "No module named jobfinder".
#
#   2. The working directory must be the repository root. The package is not
#      pip-installed into the venv; it is found because the repo root is on
#      sys.path as the current directory.
#
# This script resolves its own location, so neither depends on where you happen
# to be standing. Every argument is passed straight through.
#
# Usage:
#   ./jobfinder.sh [--no-browser]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"

if [ ! -x "$VENV_PYTHON" ]; then
  printf '\n\033[31mJobFinder is not set up yet: no virtualenv at .venv\033[0m\n\n' >&2
  printf '\033[33mRun this first:\033[0m\n' >&2
  printf '    ./scripts/setup.sh\n\n' >&2
  exit 1
fi

cd "$REPO_ROOT"
exec "$VENV_PYTHON" -m jobfinder "$@"
