# SpaceMIT K1 low-load demo

This example builds the WebSocket transport on Bianbu Linux/riscv64. The
bundled RTC library is x86-64-only, so RTC mode is intentionally disabled.

## Dependencies

```bash
apt update
apt install -y cmake build-essential pkg-config \
  libcurl4-openssl-dev libmbedtls-dev zlib1g-dev libpulse-dev pulseaudio
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

The K1 implementation executes `/usr/bin/ai-toy_app motor head_lr 1` and sends
a `function_call_output` event back to the bot. Verify that command manually
before testing the voice-triggered action.

## Weather function call

The demo also handles `get_weather`, `get_current_weather`, and
`check_weather`. The tool accepts either a `location` or `city` string. It
resolves the city and requests current/day forecast data from Open-Meteo over
HTTPS, then returns a short Chinese result to the bot. City coordinates are
cached for the lifetime of the process.

If the model sends `当前城市`, set `AI_CAT_DEFAULT_CITY` in the service
environment. Without it, the tool asks the model to request a city instead of
guessing the device location. Open-Meteo is suitable for development
validation; review provider licensing and availability before production.

## systemd service mode

The demo accepts two signals and can run without a terminal:

- `SIGUSR1`: start listening; while thinking/answering, interrupt and listen.
- `SIGUSR2`: interrupt and end the continuous session.

After the first wake, the user can ask follow-up questions for 30 seconds after
each answer without repeating the wake phrase. Install the supplied units after
building:

```bash
systemctl disable --now toy_voice.service
install -m 0644 systemd/volc-pulseaudio.service /etc/systemd/system/
install -m 0644 systemd/volc-conv-ai.service /etc/systemd/system/
install -m 0644 systemd/volc-k1-wake-word.service /etc/systemd/system/
install -m 0644 systemd/ai-cat-echo-cancel.pa \
  /etc/pulse/system.pa.d/ai-cat-echo-cancel.pa
systemctl daemon-reload
systemctl disable --now volc-conv-ai.service
systemctl enable --now volc-pulseaudio.service volc-k1-wake-word.service
```

To restore low-frequency autonomous behavior without restoring the unsafe DDS
motor executor, install the repository's replacement `toy_motor.service`. It
uses the local FastAPI API, selects only fixed head presets, skips all active
dialog states, and never selects the unverified tail. It does not pull in the
cloud dialog service. The private controller repository includes 15 allowlisted
WAV assets captured once from the matching Volcengine voices. Verify the assets,
then enable the worker with a fixed three-minute interval:

```bash
test "$(find /opt/ai-cat-controller/assets/local-speech -type f -name '*.wav' | wc -l)" -eq 15
install -m 0644 systemd/toy_motor.service /etc/systemd/system/toy_motor.service
systemctl daemon-reload
systemctl enable --now toy_motor.service
```

Set `AI_CAT_AUTONOMY_MIN_INTERVAL_SECONDS=180`,
`AI_CAT_AUTONOMY_MAX_INTERVAL_SECONDS=180`,
`AI_CAT_AUTONOMY_PHRASE_PROBABILITY=1.0`,
`AI_CAT_AUTONOMY_LOCAL_SPEECH_ENABLED=true`, and
`AI_CAT_AUTONOMY_CLOUD_SPEECH_ENABLED=false` in `/etc/ai-cat-controller.env`.
Playback uses PulseAudio and never starts `volc-conv-ai.service`. The wake-word
process releases capture while `/run/ai-cat/local-speech-active` exists and
resumes automatically after playback. Local playback waits 2.2 seconds after
the head action so motor noise does not mask the phrase. Run
`tools/capture_cloud_phrase_assets.py` as an administrator only when a fixed
phrase or mapped voice changes.

Do not change its `ExecStart` back to `/usr/bin/toy_control` while the FastAPI
and voice action paths are enabled.

Start the cloud service on demand and trigger one recording turn manually:

```bash
systemctl start volc-conv-ai.service
systemctl status volc-conv-ai.service --no-pager
systemctl kill -s SIGUSR1 volc-conv-ai.service
cat /run/ai-cat/dialog-status.json
systemctl kill -s SIGUSR2 volc-conv-ai.service
journalctl -u volc-conv-ai.service -f
```

The local wake-word process remains online without a cloud connection. After it
matches the phrase, it starts `volc-conv-ai.service`, waits for the native status
to report `ready`, sends `SIGUSR1`, and pauses its own capture while
`/run/ai-cat/dialog-session-active` exists. The cloud process exits normally
after 90 idle seconds once the continuous dialog has ended; only failures are
restarted. Ordinary local ASR transcripts are not logged by default; add
`--debug-transcripts` manually when diagnosing wake recognition. Audio is not
sent to the cloud until the phrase matches and the dialog service receives
`SIGUSR1`.

The PulseAudio snippet routes both playback and capture through a paired WebRTC
echo canceller. The dialog also stops a session if it still detects three
answer-to-listening transitions within ten seconds, preventing an acoustic
feedback loop from generating repeated cloud requests.
