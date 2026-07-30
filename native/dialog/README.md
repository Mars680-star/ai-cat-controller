# Dialogue demo

`volc_conv_ai_demo.c` is retained as a directly readable snapshot of the
modified dialogue entry point. It is not a standalone program: build it inside
the locked Volcengine SDK after applying the patches and overlay under
`integrations/volcengine-k1`.

The file contains the K1 audio path, continuous conversation state machine,
service-mode wake/interrupt signals, atomic status output, disconnect handling
and `shake_head` Function Calling implementation.

- `SIGUSR1`: start listening, or interrupt the current response and listen again.
- `SIGUSR2`: interrupt the current response and end the continuous session.
- `/run/ai-cat/dialog-status.json`: state consumed by FastAPI.
- `/run/ai-cat/dialog-session-active`: marker used to pause the wake-word capture.
