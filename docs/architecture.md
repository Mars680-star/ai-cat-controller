# Architecture

## Request path

```text
Browser / future WeChat mini program
                  |
             HTTP / HTTPS
                  |
             FastAPI API
                  |
       Motion and dialog services
                  |
        Mock or Local K1 adapter
                  |
   fixed hardware / service interfaces
```

The browser uses the same `/api/v1` endpoints intended for the future mini
program. The first stage serves HTML and API from one origin and does not
enable wildcard CORS.

## Layers

- `api/`: HTTP routing, validation, authentication and response models.
- `services/`: motion serialization, cancellation, cooldown and dialog state.
- `adapters/`: in-memory Mock behavior and the read-only Local K1 boundary.
- `core/`: settings, logging, errors, security and typed application state.
- `schemas/`: request and response contracts.
- `web/`, `templates/`, `static/`: browser controller.

Long-lived adapter and service objects are created by the FastAPI lifespan and
stored in `app.state.services`. Request handlers never create hardware objects.

## Motion lifecycle

1. Validate intensity, duration and optional request ID.
2. Reject unsupported adapter capability with `501`.
3. Reject overlapping or cooldown action with `409`.
4. Create one background action task and return `202`.
5. Cancel the task when the stop endpoint is called.
6. Use a generation counter so an old task cannot clear a newer action.
7. Stop active Mock motion during application shutdown.

## Security boundaries

- All `/api/v1` routes share optional `X-API-Key` authentication.
- `/health`, `/control` and static files are public.
- `local_k1` refuses to start unless API-key authentication is enabled.
- CommandRunner uses `asyncio.create_subprocess_exec`, never a shell.
- Only fixed systemctl paths, verbs and confirmed service names are accepted.
- The API has no endpoint for arbitrary commands, services, files or topics.

The first-stage Local K1 adapter exposes read-only service status. Real motion,
wake and interrupt methods intentionally return `501`.
