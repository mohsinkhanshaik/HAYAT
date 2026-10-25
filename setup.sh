#!/usr/bin/env bash
# Cross-platform setup for macOS and Linux. Windows users run setup.ps1.
# This is a thin wrapper around the portable bootstrap.
set -euo pipefail
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "Python 3.11+ was not found on PATH. Install it, then rerun ./setup.sh" >&2
  exit 1
fi

exec "$PYTHON" bootstrap.py "$@"
