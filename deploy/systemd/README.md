# systemd template

`ai-cat-controller.service.example` is intentionally not directly installable.
Copy it to a deployment-only location, replace every `AI_CAT_*` placeholder,
and review the service user, paths and environment file.

This repository does not enable, start or restart the unit automatically.
