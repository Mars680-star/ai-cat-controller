# Dialogue demo

`volc_conv_ai_demo.c` is retained as a directly readable snapshot of the
modified dialogue entry point. It is not a standalone program: build it inside
the locked Volcengine SDK after applying the patches and overlay under
`integrations/volcengine-k1`.

The file contains the K1 audio path, continuous conversation state machine,
service-mode wake/interrupt signals, atomic status output, disconnect handling
and Function Calling implementations for `shake_head` and weather lookup. A
follow-up starts only after buffered TTS has drained; the short ready tone then
opens a fresh 30-second capture window. It also contains a repeated barge-in
guard that ends a session when acoustic echo causes three rapid
answer-to-listening transitions. Final cloud transcripts are appended to
`/var/lib/ai-cat-controller/dialog-events.jsonl` for FastAPI to import into the
bound pet's persistent history.

- `SIGUSR1`: start listening, or interrupt the current response and listen again.
- `SIGUSR2`: interrupt the current response and end the continuous session.
- `/run/ai-cat/dialog-status.json`: state consumed by FastAPI.
- `/run/ai-cat/dialog-session-active`: marker used to pause the wake-word capture.
