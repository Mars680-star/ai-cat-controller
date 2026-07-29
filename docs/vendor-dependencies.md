# Vendor dependencies

## Volcengine conversational SDK

The full SDK is intentionally excluded. The integration is reproducible from:

- upstream URL and commit in `integrations/volcengine-k1/upstream.lock`;
- tracked-file changes in `integrations/volcengine-k1/patches/`;
- new K1 files in `integrations/volcengine-k1/overlay/`.

Run `integrations/volcengine-k1/scripts/prepare_sdk.sh` to reconstruct the
modified SDK tree.

## SpaceMIT hardware libraries

`native/ai-toy-app` is an application layer that links against SpaceMIT
peripheral components already available in the board SDK/image. Those
component implementations are not included.

The expected board executable is:

```text
/usr/bin/ai-toy_app
```

## Wake word dependencies

The wake-word entry requires PulseAudio, Ten-VAD, ONNX Runtime, SpaceMIT EP and
SenseVoice backend sources/models installed separately. Model files and copied
backend implementations are excluded from this repository.

## Secrets and generated files

Never add the following:

- a real `conv_ai_config.json`;
- `.volc_conv_ai_*.json`;
- ProductSecret, AccessToken or DeviceSecret;
- ONNX models, shared libraries or board build outputs.
