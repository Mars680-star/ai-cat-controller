# API

## Response envelope

Success:

```json
{
  "success": true,
  "message": "操作成功",
  "data": {}
}
```

Error:

```json
{
  "success": false,
  "message": "错误说明",
  "data": {
    "code": "machine_readable_code",
    "details": {}
  }
}
```

## Endpoints

| Method | Path | Result |
|---|---|---|
| GET | `/health` | Process health; no API Key required. |
| GET | `/api/v1/device/status` | Adapter, motion, dialog and process state. |
| GET | `/api/v1/services/status` | Fixed local service states. |
| POST | `/api/v1/motion/head/shake` | Accept a head-shake task. |
| POST | `/api/v1/motion/head/nod` | Accept a head-nod task. |
| POST | `/api/v1/motion/tail/wag` | Accept a tail-wag task. |
| POST | `/api/v1/motion/stop` | Cancel current motion. |
| POST | `/api/v1/dialog/wake` | Enter dialog state. |
| POST | `/api/v1/dialog/interrupt` | Interrupt dialog state. |

Motion body:

```json
{
  "intensity": 0.5,
  "duration_ms": 600,
  "request_id": "optional-identifier"
}
```

`intensity` is `0.1..1.0`; `duration_ms` is `100..3000`. Motion acceptance
returns `202`. Unsupported Local K1 operations return `501`.

When authentication is enabled, send:

```text
X-API-Key: configured-secret
```

Validation errors use `422`, conflicts `409`, unavailable devices `503`,
timeouts `504`, and invalid authentication `401`.
