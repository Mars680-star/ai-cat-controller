# Hardware interface status

## Confirmed

| Capability | Evidence | First-stage API |
|---|---|---|
| Head left/right routine | Old device accepted fixed `head_lr 1`; new device factory evidence records `head_lr 3`, a 30-degree range and 100 ms dwell. | Old profile accepted; new profile requires project-helper acceptance. |
| Head up/down routine | Old device accepted fixed `head_ud 2`; new device factory evidence records `head_ud 3`, a 20-degree range and 100 ms dwell. | Old profile accepted; new profile requires project-helper acceptance. |
| Motor stop | A running head action was stopped through FastAPI and the user confirmed that movement ended. | Enabled with fixed `/usr/bin/ai-toy_app motor stop`. |
| Dialog wake/new turn | `SIGUSR1` to `volc-conv-ai.service` is used by the wake-word process and was tested. | Enabled through a fixed allowlisted signal. |
| Dialog status | Native dialog writes atomic state to `/run/ai-cat/dialog-status.json`. | Read-only `/api/v1/dialog/status`. |
| Service status | Three unit files are tracked in the K1 overlay. | Read-only `is-active/is-enabled`. |
| Battery status | `cw-bat` exposes capacity, status, present and voltage through Linux `power_supply`; `ip2317-charger` exposes input online state. | Read-only `/api/v1/device/status`, also shown on the browser home page in `local_k1` mode. |

The fixed head programs run their own position sequences and finish in
`MOTOR_MODE_IDLE`. Their speed arguments are not equivalent to the HTTP
intensity and duration fields. FastAPI validates those fields but never maps
them to arbitrary native parameters.

The vendor `toy_main` still publishes autonomous DDS motor actions, but the
vendor `/usr/bin/toy_control` consumer must remain unused because it bypasses
`/run/ai-cat/motor.lock`. The installed `toy_motor.service` name now points to
this repository's safe HTTP autonomy worker, which selects only the verified
head presets and leaves the faulty tail disabled.

## Present in source but not sufficiently verified

- `ai-toy_app motor tail_lr 1`: the software path, lock, stop and configuration
  gate are present, but the provisional GPIO profile has not been physically
  tested because the tail hardware is faulty.

Source presence is not treated as board-level safety validation.

## Unconfirmed

- Required user/group permissions for non-root hardware access.
- Any DDS topic, ROS2 topic, message type, socket, or local IPC contract.

## Next investigation order

1. Repair the tail mechanism and run `tail-motion-validation.md` with the remote
   capability still disabled.
2. Confirm tail GPIO, direction, limits, stop, stall and temperature behavior.
3. Enable `AI_CAT_ENABLE_TAIL_MOTION` and validate REST, browser and voice paths.
4. Add a dedicated polkit rule if FastAPI must run as a non-root service user.

No unconfirmed command or topic should be added to the API adapter.
