# Voice dialog testing

## Intended behavior

1. Say “小安小安” once, or click **开始或继续聆听** in `/control`.
2. Ask the first question within 30 seconds.
3. While the AI is thinking, speak directly to replace the current question.
   During audible playback, use the browser interrupt control while validating
   echo cancellation.
4. Wait for the short follow-up tone after an answer, then ask another
   question within 30 seconds without repeating the wake phrase.
5. Say the wake phrase again after that window expires.

The browser control is a test aid. It calls the same fixed service signals as
the local wake-word process, so it can separate wake-word failures from
dialogue failures.

## State meanings

| State | Meaning |
|---|---|
| `ready` | Cloud session is ready and waiting for the wake phrase. |
| `wake_detected` / `listening` | Wake succeeded and microphone audio is being uploaded. |
| `processing_audio` | Local speech ended; the device is waiting for server VAD/ASR. |
| `thinking` | The server accepted the question and is generating a response. |
| `answering` | TTS audio is being returned. |
| `followup_preparing` | The cloud answer ended; buffered speaker audio is still draining. |
| `followup_listening` | The answer ended; a follow-up can be asked without waking again. |
| `echo_guard` | Three rapid barge-ins were detected; the session was stopped to prevent a feedback loop. |
| `offline` / `unavailable` | The native service or its status file is unavailable. |

The “current phase” timer helps locate latency: a long `listening` phase points
to capture/VAD, a long `thinking` phase points to ASR/LLM/cloud, and
`answering` without sound points to playback.

## Required cloud settings

Keep these values in the Volcengine bot configuration:

```json
{
  "ASRConfig": {
    "VADConfig": {
      "SilenceTime": 800,
      "AIVAD": false
    },
    "InterruptConfig": {
      "InterruptSpeechDuration": 600
    }
  },
  "SubtitleConfig": {
    "DisableRTSSubtitle": false,
    "SubtitleMode": 1
  },
  "InterruptMode": 0
}
```

These settings are stored by the Volcengine bot, not in the K1
`conv_ai_config.json`. `SilenceTime: 800` keeps short commands responsive,
`AIVAD: false` prevents semantic endpoint detection from holding complete
questions for an excessive period, and
`InterruptSpeechDuration: 600` rejects very short noise before interrupting.
`InterruptMode: 0` keeps voice interruption enabled. Do not configure
interruption keywords during the basic test, because that would restrict
interruption to those words. Restart `volc-conv-ai.service` after saving the
bot so the device creates a new cloud session.

The explicit subtitle settings are required for real browser history. The K1
stores final user and assistant transcripts in
`/var/lib/ai-cat-controller/dialog-events.jsonl`; FastAPI imports them
idempotently into SQLite using the bound device serial number and binding
timestamp.

## API checks

Replace the address and API key with the K1 values:

```bash
BASE=http://192.168.1.112:8000
KEY=replace-with-your-api-key

curl -sS -H "X-API-Key: $KEY" "$BASE/api/v1/dialog/status"
curl -sS -X POST -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"request_id":"manual-wake-1"}' \
  "$BASE/api/v1/dialog/wake"
curl -sS -X POST -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"request_id":"manual-interrupt-1"}' \
  "$BASE/api/v1/dialog/interrupt"
```

On K1, inspect both the native status and logs:

```bash
watch -n 0.5 cat /run/ai-cat/dialog-status.json
journalctl -u volc-conv-ai.service -f
journalctl -u volc-k1-wake-word.service -f
```

## Acceptance sequence

1. Say the wake phrase and verify the state changes from `ready` to `listening`.
2. Ask one short question and verify `listening -> thinking -> answering`.
3. During `thinking`, ask a different question and verify the first request is
   interrupted. Use the browser interrupt button during audible playback.
4. After each short follow-up tone, ask two follow-ups without the wake phrase.
5. Wait more than 30 seconds, verify the state returns to `ready`, then confirm
   a new wake phrase is required.
6. Repeat step 2 with the browser wake button to isolate wake-word recognition.

Continuous microphone upload can expose speaker-to-microphone echo. If the AI
interrupts itself, verify that `pactl info` reports
`ai_cat_echo_cancel_source` and `ai_cat_echo_cancel_sink` as the defaults.
The native guard stops the session after three answer-to-listening transitions
within ten seconds instead of allowing repeated cloud requests.

The K1 PulseAudio profile sets the AEC source to 100% input volume. If the cloud
remains in `thinking` for 30 seconds, the native process exits and systemd
rebuilds the WebSocket session while the local wake-word model stays loaded.
Cloud VAD is the only component that commits a user utterance in the current
`server_vad` session. The local energy detector marks the visible transition
to `processing_audio` after 1.5 seconds of silence or an eight-second maximum
utterance window, but it deliberately does not send
`input_audio_buffer.commit`; mixing client commit with `server_vad` can submit
one utterance twice.

The device implements `shake_head`, `get_weather` aliases and
`get_battery_status`. The battery tool reads capacity, fuel-gauge state,
voltage and charger-online state directly from K1 sysfs for every call. Weather
accepts `location` or `city`, caches resolved coordinates, and returns a
concise current/day forecast. Any other Function Calling name receives an
immediate unsupported-tool result so the cloud agent can explain the missing
capability instead of waiting until the 30-second watchdog expires.

Configure one Function Calling tool on the Volcengine agent:

- Name: `get_battery_status`
- Type: `object`
- Parameters: none
- Description: `当用户询问这只AI猫当前电量、剩余电量、是否正在充电、是否连接充电器或电池电压时必须调用。工具返回K1设备实时电源信息，禁止自行猜测数值。`

Add the same constraint to the agent prompt: questions about the device's
current power state must call `get_battery_status`, and the spoken answer must
use the tool output without changing numeric values.

Microphone frames are not uploaded while a Function Calling tool is active or
while TTS audio is physically playing. After the cloud turn finishes, the
dialogue service waits for the local ring buffer and PulseAudio playback queue
to drain, plays a 140 ms follow-up tone, clears stale cloud/local capture data,
and opens a fresh 30-second follow-up window. This blocks motor noise and
speaker-loop self-interruption without losing the start of the next question.

The dialog process synchronously plays a 350 ms confirmation tone after a wake
signal, then opens microphone upload. Users should begin the question after
this tone.
