# Voice dialog testing

## Intended behavior

1. Say “小安小安” once, or click **开始或继续聆听** in `/control`.
2. Ask the first question within 15 seconds.
3. While the AI is thinking or answering, speak directly to interrupt it.
4. After an answer finishes, ask another question within 15 seconds without
   repeating the wake phrase.
5. Say the wake phrase again after that window expires.

The browser control is a test aid. It calls the same fixed service signals as
the local wake-word process, so it can separate wake-word failures from
dialogue failures.

## State meanings

| State | Meaning |
|---|---|
| `ready` | Cloud session is ready and waiting for the wake phrase. |
| `wake_detected` / `listening` | Wake succeeded and microphone audio is being uploaded. |
| `thinking` | The server accepted the question and is generating a response. |
| `answering` | TTS audio is being returned; direct speech can interrupt it. |
| `followup_listening` | The answer ended; a follow-up can be asked without waking again. |
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
      "SilenceTime": 500
    },
    "InterruptConfig": {
      "InterruptSpeechDuration": 0
    }
  },
  "InterruptMode": 0
}
```

`InterruptMode: 0` enables voice interruption. Do not configure interruption
keywords during the basic test, because that would restrict interruption to
those words.

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
3. During the answer, ask a different question and verify the first audio stops.
4. After the answer ends, ask two follow-ups without the wake phrase.
5. Wait more than 15 seconds, verify the state returns to `ready`, then confirm
   a new wake phrase is required.
6. Repeat step 2 with the browser wake button to isolate wake-word recognition.

Continuous microphone upload can expose speaker-to-microphone echo. If the AI
interrupts itself, first reduce speaker volume and confirm the board audio path
has echo suppression before changing interruption thresholds.
