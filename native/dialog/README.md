# Dialogue demo

`volc_conv_ai_demo.c` is retained as a directly readable snapshot of the
modified dialogue entry point. It is not a standalone program: build it inside
the locked Volcengine SDK after applying the patches and overlay under
`integrations/volcengine-k1`.

The matching transport changes are consolidated in
`integrations/volcengine-k1/patches/0005-ws-response-create.patch` and are
applied by `integrations/volcengine-k1/scripts/prepare_sdk.sh`. They make PCM
sessions wait for initial configuration, enable client-controlled turn
detection, and separate `commit` from `response.create`.

Browser text questions use the fixed
`/var/lib/ai-cat-controller/dialog-text-request.json` handoff. FastAPI writes
the validated request atomically and signals this process with `SIGHUP`; the
native client sends an `input_text` item and keeps TTS, Function Calling,
status reporting, follow-up handling, and history on the existing session.

The file contains the K1 audio path, continuous conversation state machine,
service-mode wake/interrupt signals, atomic status output, disconnect handling
and Function Calling implementations for `shake_head`, `nod_head`, gated
`wag_tail`, weather lookup and `get_battery_status`. The battery tool reads the K1 `power_supply` sysfs values
at call time, so the model only speaks current device data. A follow-up starts
only after buffered TTS has drained; the short ready tone then opens a fresh
capture window. Its duration is read from
`/var/lib/ai-cat-controller/dialog-runtime-config.json` for every turn, with a
validated 5-120 second range and a 30-second fallback. It also contains a
repeated barge-in guard that ends a session when acoustic echo causes three
rapid answer-to-listening transitions. Final cloud transcripts are appended to
`/var/lib/ai-cat-controller/dialog-events.jsonl` for FastAPI to import into the
bound pet's persistent history.

Head and tail Function Calling never accepts motor parameters from the model.
They map to fixed native commands and share one motor mutex and cooldown. Tail
requests are rejected unless `AI_CAT_ENABLE_TAIL_MOTION=true`; the systemd unit
loads this value from `/etc/ai-cat-controller.env` so voice and FastAPI use the
same gate.

FastAPI writes the current pet personality to
`/var/lib/ai-cat-controller/personality-runtime.json`. At startup and while the
conversation is fully idle, the native client validates this bounded file and
sends its embedded `session.update` to override the current Volcengine system
prompt and TTS `voice_type`. The same file provides an allowlist for
`shake_head`, `nod_head`, and `wag_tail`; a disallowed Function Calling request
returns a normal tool result without starting a motor. The applied personality
ID, revision, and voice are included in `/run/ai-cat/dialog-status.json`.

- `SIGUSR1`: start listening, or interrupt the current response and listen again.
- `SIGUSR2`: interrupt the current response and end the continuous session.
- `SIGHUP`: consume one validated browser text question.
- `/run/ai-cat/dialog-status.json`: state consumed by FastAPI.
- `/run/ai-cat/dialog-session-active`: marker used to pause the wake-word capture.
