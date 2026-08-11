from pathlib import Path


SYSTEMD_DIR = (
    Path(__file__).parents[2]
    / "integrations/volcengine-k1/overlay/examples/low_load_solution/linux_k1/systemd"
)
FASTAPI_UNIT = (
    Path(__file__).parents[2]
    / "deploy/systemd/ai-cat-controller.service.example"
)
ENV_EXAMPLE = Path(__file__).parents[2] / ".env.example"


def test_fastapi_unit_can_control_system_pulseaudio() -> None:
    unit = FASTAPI_UNIT.read_text(encoding="utf-8")

    assert "Group=pulse-access" in unit
    assert "SupplementaryGroups=pulse-access" not in unit
    assert "PULSE_SERVER=unix:/var/run/pulse/native" in unit


def test_dialog_service_preserves_runtime_status_across_restart() -> None:
    unit = (SYSTEMD_DIR / "volc-conv-ai.service").read_text(
        encoding="utf-8"
    )

    assert "RuntimeDirectory=ai-cat" in unit
    assert "RuntimeDirectoryPreserve=restart" in unit
    assert "Restart=on-failure" in unit
    assert "Restart=always" not in unit
    assert "WantedBy=multi-user.target" not in unit
    assert "StartLimitBurst=3" in unit
    assert "EnvironmentFile=-/etc/ai-cat-controller.env" in unit


def test_pulseaudio_service_can_be_enabled_at_boot() -> None:
    unit = (SYSTEMD_DIR / "volc-pulseaudio.service").read_text(
        encoding="utf-8"
    )

    assert "WantedBy=multi-user.target" in unit


def test_toy_motor_unit_runs_only_safe_autonomy_worker() -> None:
    unit = (SYSTEMD_DIR / "toy_motor.service").read_text(encoding="utf-8")

    assert "ai_cat_controller.autonomy" in unit
    assert "/usr/bin/toy_control" not in unit
    assert "EnvironmentFile=/etc/ai-cat-controller.env" in unit
    assert "volc-conv-ai.service" not in unit
    assert "Group=pulse-access" in unit
    assert "PULSE_SERVER=unix:/var/run/pulse/native" in unit
    assert "Requires=ai-cat-controller.service volc-pulseaudio.service" in unit
    assert "StartLimitIntervalSec=60" in unit
    assert "StartLimitBurst=3" in unit
    assert unit.count("/run/ai-cat/local-speech-active") == 2
    assert "AI_CAT_AUTONOMY_MIN_INTERVAL_SECONDS=180" in unit
    assert "AI_CAT_AUTONOMY_MAX_INTERVAL_SECONDS=180" in unit
    assert "AI_CAT_AUTONOMY_CLOUD_SPEECH_ENABLED=false" in unit
    assert "AI_CAT_AUTONOMY_LOCAL_SPEECH_ENABLED=true" in unit
    assert "AI_CAT_AUTONOMY_LOCAL_SPEECH_MOTION_SETTLE_SECONDS=2.2" in unit
    assert "AI_CAT_CONVERSATION_MOTION_ENABLED=true" in unit
    assert "AI_CAT_CONVERSATION_MOTION_PROBABILITY=1.0" in unit
    assert "AI_CAT_CONVERSATION_MOTION_DELAY_SECONDS=1.0" in unit
    assert "AI_CAT_CONVERSATION_POLL_INTERVAL_SECONDS=0.5" in unit


def test_k1_environment_example_keeps_local_autonomy_speech_enabled() -> None:
    environment = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "AI_CAT_AUTONOMY_CLOUD_SPEECH_ENABLED=false" in environment
    assert "AI_CAT_AUTONOMY_LOCAL_SPEECH_ENABLED=true" in environment
    assert "AI_CAT_CONVERSATION_MOTION_ENABLED=true" in environment


def test_wake_word_service_does_not_pull_in_cloud_dialog() -> None:
    unit = (SYSTEMD_DIR / "volc-k1-wake-word.service").read_text(
        encoding="utf-8"
    )

    assert "Requires=volc-pulseaudio.service" in unit
    assert "Wants=volc-conv-ai.service" not in unit
    assert "After=volc-conv-ai.service" not in unit
