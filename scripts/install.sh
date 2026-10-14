#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
VENV="${JOBZOOM_VENV:-$ROOT/.venv}"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3,11), "JobZoom requires Python 3.11+"'
"$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/pip" install -e "$ROOT"
echo "JobZoom installed in $VENV"
echo "Next: $VENV/bin/jobzoom init"
