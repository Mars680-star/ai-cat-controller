# K1 motion profiles

## Device 7c2b63fd4a128

The second AI cat was inspected on 2026-08-10 before installing this project.
Its factory image is Bianbu 2.2.1 with Linux 6.6.63 built on 2026-03-10. The
factory `/usr/bin/toy_control` SHA-256 is:

```text
52c514be2aa7d3a79ea934ebaa085ab29c1430c6cb88872b03349644bdacf948
```

The values below were recovered from the device configuration, factory motor
log and RISC-V disassembly. No additional motor command was issued while
recording them.

| Physical actuator | Factory index | Step | Direction | Sleep | Stop | Effective range |
|---|---:|---:|---:|---:|---:|---:|
| Head left/right | 1 | 46 | 43 | 42 | 83 | 30 degrees |
| Head up/down | 2 | 34 | 35 | 36 | 82 | 20 degrees |
| Tail left/right | 3 | 37 | 38 | 39 | 61 | 50 degrees |

All three factory oscillation gestures use target values `180 -> 0 -> 90`,
speed level `3`, and a 100 ms dwell after each extreme. The motor driver's
configured maximum steps were 758, 659 and 964 respectively. These target
values are normalized positions; the effective physical travel remains bounded
by the ranges in the table.

Additional factory actions use the same fixed speed level:

| Factory action | Sequence |
|---|---|
| `head_down` | Head up/down to `0` |
| `head_up` | Head up/down to `180` |
| `head_center` | Head left/right to `90`, then up/down to `90` |
| `random_head_turn` | Head left/right to randomly selected `0` or `180` |
| `head_positioning` | Head up/down to `90`, wait 200 ms, then left/right to the DOA target |
| `head_up_random_turn` | Head up/down to `180`, wait 200 ms, then left/right to randomly selected `0` or `180` |

## Controller selection

`AI_CAT_MOTION_PROFILE` selects only reviewed fixed commands:

| Profile | Head left/right | Head up/down | Tail | Dwell |
|---|---|---|---|---:|
| `legacy_safe` | speed 1 | speed 2 | speed 1 | 500 ms |
| `k1_vendor_smooth` | speed 3 | speed 3 | speed 3 | 100 ms |

The default remains `legacy_safe` so an update cannot silently change an
already accepted device. Set the following only on device `7c2b63fd4a128` after
installing the updated native helper:

```bash
AI_CAT_MOTION_PROFILE=k1_vendor_smooth
```

The FastAPI and voice paths derive their fixed command from this profile. HTTP
intensity, angle and duration fields never become raw GPIO or speed arguments.
`AI_CAT_ENABLE_TAIL_MOTION` remains a separate gate and defaults to `false`.

The profile has been recovered from factory behavior and covered by automated
tests. Physical acceptance of the project-built `/usr/bin/ai-toy_app` on the
new device is still required before enabling autonomous or remote motion.
