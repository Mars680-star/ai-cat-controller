# K1 FastAPI deployment

The first stage can run on K1 in either safe Mock mode or read-only Local K1
mode. Local K1 does not execute motion, wake, interrupt, or service mutations.

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
```

Copy `deploy/systemd/ai-cat-controller.service.example`, replace every
`AI_CAT_*` placeholder, and review the resulting unit before installation.
The example is not installed or enabled automatically.

The service account needs only enough permission to run the application and
read systemd service status in this stage. Do not run it as root by default.

## Validate

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS \
  -H 'X-API-Key: your-key' \
  http://127.0.0.1:8000/api/v1/device/status
```

For phone access, use the K1 LAN address. Plain HTTP with an API Key is only
appropriate on a trusted test network. Production remote control requires
HTTPS and a controlled gateway.
