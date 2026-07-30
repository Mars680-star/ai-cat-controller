# Volcengine K1 integration

This directory preserves the project-owned changes made while validating the
Volcengine ConversationalAI Embedded Kit on a SpaceMIT K1 board. It does not
contain the complete vendor SDK.

## Contents

- `upstream.lock`: tested vendor repository URL and commit.
- `patches/`: changes to tracked vendor SDK files.
- `overlay/`: new K1 example, service units, and wake-word entry point.
- `overlay/.../systemd/ai-cat-echo-cancel.pa`: paired PulseAudio WebRTC AEC
  routing for the K1 microphone and speaker.
- `scripts/prepare_sdk.sh`: reconstructs the tested SDK tree from upstream.

The directly readable dialog implementation is also kept at
`native/dialog/volc_conv_ai_demo.c`.

## Reconstruct the SDK

```bash
./integrations/volcengine-k1/scripts/prepare_sdk.sh \
  "$HOME/ConversationalAI-Embedded-Kit-2.0"
```

The destination must either not exist or be a clean Git checkout. The script
checks out the locked upstream commit, applies all patches, and copies the K1
overlay. It never copies credentials, generated authentication files, models,
or build output from the development board.

Then follow:

```bash
cd "$HOME/ConversationalAI-Embedded-Kit-2.0/examples/low_load_solution/linux_k1"
cmake -S . -B build
cmake --build build -j2
vi build/conv_ai_config.json
```

Replace all `REPLACE_WITH_*` values locally. Do not commit the configured file.
