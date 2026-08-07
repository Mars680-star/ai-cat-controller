#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
VENV_DIR=${AI_CAT_VENV_DIR:-"$ROOT_DIR/.venv"}

if [[ ! -x "$VENV_DIR/bin/ai-cat-server" ]]; then
    printf 'Development environment not found. Run %s/scripts/bootstrap_dev.sh first.\n' "$ROOT_DIR" >&2
    exit 1
fi

export AI_CAT_HARDWARE_DRIVER=mock
export AI_CAT_API_HOST=${AI_CAT_API_HOST:-127.0.0.1}
export AI_CAT_API_PORT=${AI_CAT_API_PORT:-8000}
export AI_CAT_DATA_PATH=${AI_CAT_DATA_PATH:-"$ROOT_DIR/.data/ai-cat-mock.db"}

cd "$ROOT_DIR"
exec "$VENV_DIR/bin/ai-cat-server"
