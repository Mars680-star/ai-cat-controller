# Hardware interface status

## Confirmed

| Capability | Evidence | First-stage API |
|---|---|---|
| Head left/right routine | Reduced to fixed low-speed `head_lr 1` with the board service's 30-degree range and right-left-center path; the user confirmed the repaired motion is smooth and its range is appropriate. | Enabled and physically accepted. |
| Head up/down routine | GPIO mapping was corrected against the original board binary; the FastAPI nod action was physically verified on K1. | Enabled with fixed `/usr/bin/ai-toy_app motor head_ud 2`. |
| Motor stop | A running head action was stopped through FastAPI and the user confirmed that movement ended. | Enabled with fixed `/usr/bin/ai-toy_app motor stop`. |
| Dialog wake/new turn | `SIGUSR1` to `volc-conv-ai.service` is used by the wake-word process and was tested. | Enabled through a fixed allowlisted signal. |
| Dialog status | Native dialog writes atomic state to `/run/ai-cat/dialog-status.json`. | Read-only `/api/v1/dialog/status`. |
| Service status | Three unit files are tracked in the K1 overlay. | Read-only `is-active/is-enabled`. |
| Battery status | `cw-bat` exposes capacity, status, present and voltage through Linux `power_supply`; `ip2317-charger` exposes input online state. | Read-only `/api/v1/device/status`, also shown on the browser home page in `local_k1` mode. |

The fixed head programs run their own position sequences and finish in
`MOTOR_MODE_IDLE`. Their speed arguments are not equivalent to the HTTP
intensity and duration fields. FastAPI validates those fields but never maps
them to arbitrary native parameters.

The vendor `toy_main` publishes autonomous DDS motor actions even when the
original voice service is disabled. `toy_motor.service` must remain disabled
while this controller owns the motor; otherwise the vendor process can bypass
the `/run/ai-cat/motor.lock` lock and drive the same GPIO concurrently.

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
