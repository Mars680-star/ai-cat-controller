# Hardware interface status

## Confirmed

| Capability | Evidence | First-stage API |
|---|---|---|
| Head left/right routine | `/usr/bin/ai-toy_app motor head_lr 2` was run on K1. | Disabled; returns `501`. |
| Dialog wake/new turn | `SIGUSR1` to `volc-conv-ai.service` is used by the wake-word process and was tested. | Disabled; returns `501`. |
| Service status | Three unit files are tracked in the K1 overlay. | Read-only `is-active/is-enabled`. |

The fixed `head_lr` program runs its own position sequence and finishes in
`MOTOR_MODE_IDLE`. Its speed argument is not equivalent to the HTTP intensity
and duration fields.

## Present in source but not sufficiently verified

- `ai-toy_app motor head_ud <speed>`
- `ai-toy_app motor tail_lr <speed>`

Source presence is not treated as board-level safety validation.

## Unconfirmed

- A standalone emergency stop command that can interrupt a running motor.
- Mapping API intensity and duration to safe motor limits.
- A dialog interrupt entry that does not immediately begin another turn.
- Required user/group permissions for non-root hardware access.
- Any DDS topic, ROS2 topic, message type, socket, or local IPC contract.

## Next investigation order

1. Add and manually verify an idempotent emergency motor stop primitive.
2. Test head-up/down and tail routines in a clear area with conservative limits.
3. Separate dialog wake and interrupt signals in the native dialogue process.
4. Replace the wake-word program's existing shell-based system call with a
   direct, fixed exec or local IPC mechanism.
5. Add one Local K1 capability at a time with a fixed command registry and
   dedicated integration test.

No unconfirmed command or topic should be added to the API adapter.
