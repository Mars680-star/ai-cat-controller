# K1 FastAPI deployment

The service can run on K1 in either Mock mode or Local K1 mode. Local K1 reads
dialog and battery status, sends two fixed signals to the dialogue service, and
executes only the audited head-motion and stop commands. Tail motion remains
disabled until repaired hardware passes manual validation.

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
AI_CAT_ENABLE_TAIL_MOTION=false
AI_CAT_ENABLE_TOUCH_MOTION=true
AI_CAT_TOUCH_MOTION_COOLDOWN_SECONDS=3.0
AI_CAT_ENABLE_TOUCH_SPEECH=true
AI_CAT_TOUCH_SPEECH_ASSET_ROOT=/opt/ai-cat-controller/assets/local-speech
AI_CAT_TOUCH_SPEECH_MARKER_PATH=/run/ai-cat/local-speech-active
AI_CAT_TOUCH_SPEECH_PLAYER_PATH=/usr/bin/paplay
AI_CAT_PULSEAUDIO_CTL_BINARY=/usr/bin/pactl
AI_CAT_PULSE_SERVER=unix:/var/run/pulse/native
AI_CAT_TOUCH_EVENT_LOG_PATH=/root/.log/main_log
AI_CAT_DIALOG_STATUS_PATH=/run/ai-cat/dialog-status.json
AI_CAT_DIALOG_CONFIG_PATH=/var/lib/ai-cat-controller/dialog-runtime-config.json
```

Copy `deploy/systemd/ai-cat-controller.service.example`, replace every
`AI_CAT_*` placeholder, and review the resulting unit before installation.
The example is not installed or enabled automatically.

The service account needs permission to query systemd and signal
`volc-conv-ai.service`. For board-only lab validation, `User=root` is the
shortest setup. A production deployment should use a dedicated account and a
polkit rule restricted to the two dialog signals.

Web volume changes are applied to PulseAudio's fixed `@DEFAULT_SINK@`. The
service must use `pulse-access` as its primary group; this board's PulseAudio
authentication rejects a process that has it only as a supplementary group.
The tracked unit template therefore uses `Group=pulse-access`. Validate access with:

```bash
sg pulse-access -c \
  'PULSE_SERVER=unix:/var/run/pulse/native pactl get-sink-volume @DEFAULT_SINK@'
```

Physical touches are imported from the existing `toy_main` log and add
intimacy points through the same idempotent daily-cap rules as browser events.
The monitor starts at the end of the file, so historical touches are not
replayed after a FastAPI restart. The service account must be able to read
`AI_CAT_TOUCH_EVENT_LOG_PATH`; the current K1 lab deployment uses `root`.
The current sample's verified wiring swaps the vendor labels `nose`/`head` and
`back`/`left_foot`; `right_foot` is unchanged. The monitor exposes the corrected
physical location as `sensor` and keeps the raw log label as `hardware_sensor`.
Touch feedback is submitted through the same serialized motion service used by
the REST API: head/back touches nod, while nose/foot touches shake. Dialog-busy,
motor-busy and cooldown cases keep the intimacy event but skip motor movement.
When touch speech is enabled, the controller waits for the accepted motion to
finish and plays only the allowlisted local WAV for that personality and sensor.
The 25 `touch-*.wav` assets must exist before enabling the flag; no cloud fallback
is used when an asset is unavailable.

## Validate

Before enabling Local K1 motion, replace the vendor DDS motor executor with the
safe unit from this repository. The vendor `/usr/bin/toy_control` does not use
this project's cross-process lock:

```bash
systemctl disable --now toy_motor.service
install -m 0644 \
  integrations/volcengine-k1/overlay/examples/low_load_solution/linux_k1/systemd/toy_motor.service \
  /etc/systemd/system/toy_motor.service
systemctl daemon-reload
systemctl enable --now toy_motor.service
systemctl is-active toy_motor.service   # expected: active
systemctl is-enabled toy_motor.service  # expected: enabled
systemctl cat toy_motor.service | grep ai_cat_controller.autonomy
```

Keep `toy_main.service` only if its display, touch and emotion behavior is still
needed; its DDS motor publications have no hardware consumer because the safe
replacement service does not subscribe to DDS.

For a board that must remain reachable by browser and SSH while idle, disable
Wi-Fi power saving for the active NetworkManager connection:

```bash
connection=$(nmcli -g GENERAL.CONNECTION device show wlan0)
nmcli connection modify "$connection" 802-11-wireless.powersave 2
iw dev wlan0 set power_save off
nmcli -g 802-11-wireless.powersave connection show "$connection"
iw dev wlan0 get power_save
```

The expected values are `disable` and `Power save: off`. This persists through
reboot but slightly increases idle power consumption. Repeat it if the board is
moved to a different saved Wi-Fi connection.

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS \
  -H 'X-API-Key: your-key' \
  http://127.0.0.1:8000/api/v1/device/status
curl -sS \
  -H 'X-API-Key: your-key' \
  http://127.0.0.1:8000/api/v1/dialog/status
```

For phone access, use the K1 LAN address. Plain HTTP with an API Key is only
appropriate on a trusted test network. Production remote control requires
HTTPS and a controlled gateway.

The browser test panel is available at `/control`. Its dialog-history view
shows wake, listening, thinking, answering and follow-up states and provides
fixed start/interrupt controls. See `voice-dialog-testing.md`.

## Tail motion after repair

Keep `AI_CAT_ENABLE_TAIL_MOTION=false` until every step in
`tail-motion-validation.md` passes. The flag must be injected into both
`ai-cat-controller.service` and `volc-conv-ai.service`; the tracked voice unit
reads `/etc/ai-cat-controller.env`. After changing it, restart both services so
the REST and Function Calling paths expose the same capability.
