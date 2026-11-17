# Wake word entry

Local “小安小安” wake-word process for K1. This directory intentionally contains
only the application entry and build definition.

Required but excluded dependencies:

- SenseVoice backend sources and model;
- Ten-VAD;
- ONNX Runtime and SpaceMIT EP;
- PulseAudio and FFTW.

The reproducible SDK overlay keeps this same entry under the original K1
example path. Third-party backend copies and models must be supplied locally.

After a match, the process sends fixed `SIGUSR1` arguments with `posix_spawn`
and releases its PulseAudio capture. It waits for
`/run/ai-cat/dialog-session-active` to disappear before listening for the next
wake phrase, so it no longer relies on a fixed 20-second cooldown.
