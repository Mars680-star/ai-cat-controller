# Tests

All default tests use the in-memory Mock adapter. Tests for the Local K1
adapter inject a fake command runner and never touch motors or systemd.

Run:

```bash
pytest -q
```
