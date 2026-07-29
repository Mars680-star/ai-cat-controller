# ai-toy-app

Small K1 application layer for exercising the AI cat peripherals. It depends
on SpaceMIT component headers and libraries supplied by the board SDK/image;
those dependencies are not included here.

Supported command groups include motor, fan, power, Wi-Fi, NFC, GPIO and light
sensor operations. The controller API will expose only an audited subset.

Verified motor command:

```bash
/usr/bin/ai-toy_app motor head_lr 2
```

The original SPDX and copyright notice is retained in `src/main.c`.
