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

For device `7c2b63fd4a128`, the project helper also implements the recovered
factory-smooth fixed profile:

```bash
/usr/bin/ai-toy_app motor head_lr 3
/usr/bin/ai-toy_app motor head_ud 3
```

Speed 3 keeps the same bounded `180 -> 0 -> 90` normalized target sequence but
uses the factory 100 ms dwell. This was recovered from factory behavior; the
project-built helper still requires physical acceptance on the new device. See
`docs/k1-motion-profiles.md` before enabling it through
`AI_CAT_MOTION_PROFILE=k1_vendor_smooth`.

The routines use fixed board profiles, a cross-process lock at
`/run/ai-cat/motor.lock`, and a PID file at `/run/ai-cat/motor.pid`. `SIGTERM`
requests a clean exit and the active routine switches the motor to
`MOTOR_MODE_IDLE` before releasing the lock. `motor stop` validates the target
process command line before signalling it.

The head-left/right profile is limited to 30 degrees and runs only
right-left-center. `legacy_safe` uses speed 1 with a 500 ms dwell;
`k1_vendor_smooth` uses the recorded speed 3 with a 100 ms dwell. Keep the
vendor `toy_motor.service` disabled while this program owns motion; the vendor
DDS process does not honor this program's lock and can otherwise drive the same
motor concurrently.

The tail GPIO and motor index now match the factory configuration recorded from
device `7c2b63fd4a128`. The project helper has not been physically accepted on
that device, and the older sample still has a tail hardware fault. Remote API
and voice access therefore remain disabled unless
`AI_CAT_ENABLE_TAIL_MOTION=true`.

The original SPDX and copyright notice is retained in `src/main.c`.
