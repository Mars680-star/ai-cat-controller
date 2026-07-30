# Tests

All default tests use the in-memory Mock adapter. Tests for the Local K1
adapter inject a fake command runner and never touch motors or systemd.

Product workflow tests use one temporary SQLite database per test. They cover
binding, personality persistence, intimacy idempotency, safe action presets,
dialog ownership, device transfer and application restart recovery.

Run:

```bash
pytest -q
```
