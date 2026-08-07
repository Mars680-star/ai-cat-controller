#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
VENV_DIR=${AI_CAT_VENV_DIR:-"$ROOT_DIR/.venv"}

"$PYTHON_BIN" -c '
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required")
'

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if ! "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; then
    "$VENV_DIR/bin/python" -m ensurepip --upgrade
fi

"$VENV_DIR/bin/python" -m pip install -e "$ROOT_DIR[dev]"
"$VENV_DIR/bin/python" -m compileall -q "$ROOT_DIR/src" "$ROOT_DIR/tests"
"$VENV_DIR/bin/python" -m pytest -q "$ROOT_DIR/tests"

printf '\nDevelopment environment is ready. Start the Mock with:\n  %s/scripts/run_mock.sh\n' "$ROOT_DIR"
