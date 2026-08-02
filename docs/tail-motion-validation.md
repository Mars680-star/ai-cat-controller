# Tail motion validation

The tail software path is complete but disabled because the current sample's
tail hardware is faulty. The provisional native profile is `tail_lr`, motor
index 2, GPIO `37/38/39/61`; this mapping is not board-validated.

## Before enabling

1. Repair the tail mechanism and inspect wiring, end stops and free movement.
2. Keep `AI_CAT_ENABLE_TAIL_MOTION=false` in `/etc/ai-cat-controller.env`.
3. Put the sample in a clear area where an unexpected full sweep cannot hit a
   person, cable or object.
4. Run the fixed low-speed command locally as root:

   ```bash
   /usr/bin/ai-toy_app motor tail_lr 1
   ```

5. While it is moving, verify the independent stop path in another terminal:

   ```bash
   /usr/bin/ai-toy_app motor stop
   ```

6. Confirm direction, limit switches, idle state, repeated-call lock, stall and
   temperature behavior. Correct the native profile and repeat if any result is
   abnormal.

Do not continue if the direct command or stop behavior is uncertain.

## Enable remote paths

After the low-speed and stop tests pass:

```bash
sed -i 's/^AI_CAT_ENABLE_TAIL_MOTION=.*/AI_CAT_ENABLE_TAIL_MOTION=true/' \
  /etc/ai-cat-controller.env
systemctl restart ai-cat-controller.service volc-conv-ai.service
```

Then check the REST path before adding the cloud Function Calling tool:

```bash
curl -sS -X POST \
  -H "X-API-Key: $AI_CAT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"intensity":0.3,"duration_ms":600,"request_id":"tail-acceptance-1"}' \
  http://127.0.0.1:8000/api/v1/motion/tail/wag
```

The HTTP fields cannot change motor speed or GPIO values. A second simultaneous
motion must return `409`; stop must leave `tail_state` as `idle`. Only after
these checks pass should the Volcengine agent expose a parameterless `wag_tail`
tool.

To disable immediately:

```bash
sed -i 's/^AI_CAT_ENABLE_TAIL_MOTION=.*/AI_CAT_ENABLE_TAIL_MOTION=false/' \
  /etc/ai-cat-controller.env
systemctl restart ai-cat-controller.service volc-conv-ai.service
```
