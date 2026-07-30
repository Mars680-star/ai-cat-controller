# SpaceMIT K1 low-load demo

This example builds the WebSocket transport on Bianbu Linux/riscv64. The
bundled RTC library is x86-64-only, so RTC mode is intentionally disabled.

## Dependencies

```bash
apt update
apt install -y cmake build-essential pkg-config \
  libmbedtls-dev zlib1g-dev libpulse-dev pulseaudio
```

## Configure

Build once, then edit `build/conv_ai_config.json` and replace every
`REPLACE_WITH_*` value with the Bot ID, Instance ID, ProductKey,
ProductSecret, and K1 serial number from the Volcengine console and board.

Do not commit or share the configured file because ProductSecret is a
credential.

## Build

```bash
cmake -S . -B build
cmake --build build -j2
vi build/conv_ai_config.json
```

## Run

Stop any service that owns the audio device, then start PulseAudio and run the
demo from the build directory so it can find `conv_ai_config.json`.

```bash
systemctl stop toy_voice.service
pulseaudio --check || pulseaudio --exit-idle-time=-1 --daemonize=yes
cd build
./volc_conv_ai_k1_demo
```

Press Space to start or end a continuous dialog session. Press `i` to interrupt
and end the session, and Ctrl+C to exit. Restore the AI Toy audio service after
testing:

```bash
systemctl start toy_voice.service
```

## Head shake function call

The demo handles a device-side function named `shake_head`. Add this tool to
the bot's `LLMConfig.Tools` in the Volcengine console:

```json
[
  {
    "type": "function",
    "function": {
      "name": "shake_head",
      "description": "控制实体AI玩具左右摇头。当用户要求摇头、左右摆头或用动作表示否定时调用。",
      "parameters": {
        "type": "object",
        "properties": {},
        "required": []
      }
    }
  }
]
```

The K1 implementation executes `/usr/bin/ai-toy_app motor head_lr 2` and sends
a `function_call_output` event back to the bot. Verify that command manually
before testing the voice-triggered action.

## systemd service mode

The demo accepts two signals and can run without a terminal:

- `SIGUSR1`: start listening; while thinking/answering, interrupt and listen.
- `SIGUSR2`: interrupt and end the continuous session.

After the first wake, the user can ask follow-up questions for 15 seconds after
each answer without repeating the wake phrase. Install the supplied units after
building:

```bash
systemctl disable --now toy_voice.service
install -m 0644 systemd/volc-pulseaudio.service /etc/systemd/system/
install -m 0644 systemd/volc-conv-ai.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now volc-conv-ai.service
```

Check service state and trigger one recording turn manually:

```bash
systemctl status volc-conv-ai.service --no-pager
systemctl kill -s SIGUSR1 volc-conv-ai.service
cat /run/ai-cat/dialog-status.json
systemctl kill -s SIGUSR2 volc-conv-ai.service
journalctl -u volc-conv-ai.service -f
```

The local wake-word process sends `SIGUSR1` after matching its phrase and pauses
its own capture while `/run/ai-cat/dialog-session-active` exists.
