# Architecture

## Deployment model

The recommended production path runs FastAPI directly on the K1 board:

```text
Phone / desktop browser
          |
       HTTP API
          |
  ai-cat-controller on K1
          |
  fixed command adapters
     |             |
ai-toy_app     systemd dialogue services
```

Desktop development uses a mock adapter. The HTTP request path must never
accept an arbitrary executable or shell fragment.

## Planned modules

```text
src/ai_cat_controller/
├── main.py
├── api/
│   ├── health.py
│   ├── device.py
│   ├── motion.py
│   └── dialog.py
├── core/
│   ├── config.py
│   ├── security.py
│   └── errors.py
├── services/
│   ├── cat_controller.py
│   ├── motion_service.py
│   └── dialog_service.py
└── adapters/
    ├── command_runner.py
    ├── local_k1.py
    └── mock.py
```

## Initial API surface

```text
GET  /api/v1/health
GET  /api/v1/device/status
GET  /api/v1/services/status
POST /api/v1/motion/head/shake
POST /api/v1/motion/head/nod
POST /api/v1/motion/tail/wag
POST /api/v1/dialog/wake
POST /api/v1/dialog/interrupt
```

## Safety boundaries

- Fixed command registry; no generic command endpoint.
- One motor command at a time.
- Per-command timeout and captured exit status.
- API-key authentication and explicit CORS allowlist.
- A dedicated service account with the smallest possible device and systemd
  permissions should replace root before non-development deployment.
