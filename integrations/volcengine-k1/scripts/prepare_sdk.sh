#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INTEGRATION_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
REPO_DIR=$(CDPATH= cd -- "$INTEGRATION_DIR/../.." && pwd)

# shellcheck disable=SC1091
. "$INTEGRATION_DIR/upstream.lock"

SDK_DIR=${1:-"$HOME/ConversationalAI-Embedded-Kit-2.0"}

if [ ! -e "$SDK_DIR" ]; then
    git clone "$UPSTREAM_URL" "$SDK_DIR"
fi

if ! git -C "$SDK_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "error: destination is not a Git checkout: $SDK_DIR" >&2
    exit 1
fi

if [ -n "$(git -C "$SDK_DIR" status --porcelain)" ]; then
    echo "error: destination has local changes: $SDK_DIR" >&2
    exit 1
fi

if ! git -C "$SDK_DIR" cat-file -e "$UPSTREAM_COMMIT^{commit}" 2>/dev/null; then
    git -C "$SDK_DIR" fetch origin "$UPSTREAM_COMMIT"
fi

git -C "$SDK_DIR" checkout --detach "$UPSTREAM_COMMIT"

for patch in "$INTEGRATION_DIR"/patches/*.patch; do
    git -C "$SDK_DIR" apply --check "$patch"
    git -C "$SDK_DIR" apply "$patch"
done

cp -a "$INTEGRATION_DIR/overlay/." "$SDK_DIR/"
cp "$REPO_DIR/native/dialog/volc_conv_ai_demo.c" \
    "$SDK_DIR/examples/low_load_solution/macos/volc_conv_ai_demo.c"
cp "$REPO_DIR/native/wake-word/main.cpp" \
    "$SDK_DIR/examples/low_load_solution/linux_k1/wake_word/main.cpp"

echo "Prepared SDK at: $SDK_DIR"
echo "Next: cd $SDK_DIR/examples/low_load_solution/linux_k1"
echo "Then: cmake -S . -B build && cmake --build build -j2"
