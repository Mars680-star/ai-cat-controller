# ai-toy-app

Small K1 application layer for exercising the AI cat peripherals. It depends
on SpaceMIT component headers and libraries supplied by the board SDK/image;
those dependencies are not included here.

Supported command groups include motor, fan, power, Wi-Fi, NFC, GPIO and light
sensor operations. The controller API will expose only an audited subset.

Verified K1 motor commands:

```bash
/usr/bin/ai-toy_app motor head_lr 1
/usr/bin/ai-toy_app motor head_ud 2
/usr/bin/ai-toy_app motor stop
```

The routines use fixed board profiles, a cross-process lock at
`/run/ai-cat/motor.lock`, and a PID file at `/run/ai-cat/motor.pid`. `SIGTERM`
requests a clean exit and the active routine switches the motor to
`MOTOR_MODE_IDLE` before releasing the lock. `motor stop` validates the target
process command line before signalling it.

The head-left/right profile is limited to 30 degrees and runs only
right-left-center at speed 1. Keep the vendor `toy_motor.service` disabled while
this program owns motion; the vendor DDS process does not honor this program's
lock and can otherwise drive the same motor concurrently.

`motor tail_lr 1` exists only for post-repair low-speed validation. Its current
GPIO profile is provisional and has not been physically accepted; remote API
and voice access remain disabled unless `AI_CAT_ENABLE_TAIL_MOTION=true`.

The original SPDX and copyright notice is retained in `src/main.c`.
