# K1 FastAPI deployment

The service can run on K1 in either Mock mode or Local K1 mode. Local K1 reads
dialog and battery status, sends two fixed signals to the dialogue service, and
executes only the audited head-motion and stop commands. Tail motion remains
disabled until repaired hardware passes manual validation.

## Install

```bash
python3 -m venv /opt/ai-cat-controller/.venv
/opt/ai-cat-controller/.venv/bin/pip install -e "/opt/ai-cat-controller[dev]"
```

Create an environment file outside Git:

```dotenv
AI_CAT_HARDWARE_DRIVER=local_k1
AI_CAT_API_HOST=0.0.0.0
AI_CAT_API_PORT=8000
AI_CAT_API_KEY_ENABLED=true
AI_CAT_API_KEY=REPLACE_WITH_A_RANDOM_SECRET
AI_CAT_ENABLE_TAIL_MOTION=false
AI_CAT_DIALOG_STATUS_PATH=/run/ai-cat/dialog-status.json
AI_CAT_DIALOG_CONFIG_PATH=/var/lib/ai-cat-controller/dialog-runtime-config.json
```

Copy `deploy/systemd/ai-cat-controller.service.example`, replace every
`AI_CAT_*` placeholder, and review the resulting unit before installation.
The example is not installed or enabled automatically.

The service account needs permission to query systemd and signal
`volc-conv-ai.service`. For board-only lab validation, `User=root` is the
shortest setup. A production deployment should use a dedicated account and a
polkit rule restricted to the two dialog signals.

## Validate

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS \
  -H 'X-API-Key: your-key' \
  http://127.0.0.1:8000/api/v1/device/status
curl -sS \
  -H 'X-API-Key: your-key' \
  http://127.0.0.1:8000/api/v1/dialog/status
```

For phone access, use the K1 LAN address. Plain HTTP with an API Key is only
appropriate on a trusted test network. Production remote control requires
HTTPS and a controlled gateway.

The browser test panel is available at `/control`. Its dialog-history view
shows wake, listening, thinking, answering and follow-up states and provides
fixed start/interrupt controls. See `voice-dialog-testing.md`.

## Tail motion after repair

Keep `AI_CAT_ENABLE_TAIL_MOTION=false` until every step in
`tail-motion-validation.md` passes. The flag must be injected into both
`ai-cat-controller.service` and `volc-conv-ai.service`; the tracked voice unit
reads `/etc/ai-cat-controller.env`. After changing it, restart both services so
the REST and Function Calling paths expose the same capability.
