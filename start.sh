#!/usr/bin/env bash
# Optional shell wrapper around the same portable launcher used on every OS.
set -euo pipefail
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then PYTHON=python3; else PYTHON=python; fi
exec "$PYTHON" run.py "$@"
