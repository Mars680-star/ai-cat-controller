import pytest
from pydantic import ValidationError

from ai_cat_controller.core.config import Settings


def test_default_settings_are_safe_mock() -> None:
    settings = Settings.from_env({})

    assert settings.hardware_driver == "mock"
    assert settings.api_host == "127.0.0.1"
    assert settings.api_key_enabled is False
    assert settings.enable_growth_personality_v1 is False
    assert settings.enable_debug_growth is False
    assert settings.enable_product_data_reset is False


def test_environment_values_are_parsed() -> None:
    settings = Settings.from_env(
        {
            "AI_CAT_HARDWARE_DRIVER": "mock",
            "AI_CAT_API_PORT": "9000",
            "AI_CAT_LOG_LEVEL": "debug",
            "AI_CAT_MOTION_COOLDOWN_SECONDS": "0.5",
            "AI_CAT_MOTION_PROFILE": "k1_vendor_smooth",
            "AI_CAT_ENABLE_TAIL_MOTION": "true",
            "AI_CAT_DEBUG_UNLOCK_ALL_ACTIONS": "true",
            "AI_CAT_DEBUG_UNLIMITED_TOUCH_INTIMACY": "true",
            "AI_CAT_ENABLE_GROWTH_PERSONALITY_V1": "true",
            "AI_CAT_ENABLE_DEBUG_GROWTH": "true",
            "AI_CAT_ENABLE_PRODUCT_DATA_RESET": "true",
            "AI_CAT_ENABLE_TOUCH_MOTION": "false",
            "AI_CAT_TOUCH_MOTION_COOLDOWN_SECONDS": "4.5",
            "AI_CAT_ENABLE_TOUCH_SPEECH": "true",
            "AI_CAT_TOUCH_SPEECH_COOLDOWN_SECONDS": "2.5",
            "AI_CAT_TOUCH_SPEECH_ASSET_ROOT": "/tmp/touch-assets",
            "AI_CAT_TOUCH_SPEECH_MARKER_PATH": "/tmp/touch-marker",
            "AI_CAT_TOUCH_SPEECH_PLAYER_PATH": "/bin/paplay",
            "AI_CAT_BATTERY_SUPPLY_PATH": "/tmp/test-cw-bat",
            "AI_CAT_CHARGER_SUPPLY_PATH": "/tmp/test-charger",
            "AI_CAT_DIALOG_CONFIG_PATH": "/tmp/dialog-config.json",
            "AI_CAT_DIALOG_TEXT_REQUEST_PATH": "/tmp/dialog-text.json",
            "AI_CAT_PERSONALITY_RUNTIME_PATH": "/tmp/personality.json",
            "AI_CAT_DEVICE_SERIAL_PATH": "/tmp/device-serial",
            "AI_CAT_PULSEAUDIO_CTL_BINARY": "/bin/pactl",
            "AI_CAT_PULSE_SERVER": "unix:/var/run/pulse/native",
            "AI_CAT_TOUCH_EVENT_LOG_PATH": "/tmp/main-log",
            "AI_CAT_TOUCH_MONITOR_POLL_SECONDS": "0.25",
            "AI_CAT_PAW_TOUCH_CONFIRMATION_COUNT": "3",
            "AI_CAT_PAW_TOUCH_CONFIRMATION_WINDOW_SECONDS": "4.0",
        }
    )

    assert settings.api_port == 9000
    assert settings.log_level == "DEBUG"
    assert settings.motion_cooldown_seconds == 0.5
    assert settings.motion_profile == "k1_vendor_smooth"
    assert settings.enable_tail_motion is True
    assert settings.debug_unlock_all_actions is True
    assert settings.debug_unlimited_touch_intimacy is True
    assert settings.enable_growth_personality_v1 is True
    assert settings.enable_debug_growth is True
    assert settings.enable_product_data_reset is True
    assert settings.enable_touch_motion is False
    assert settings.touch_motion_cooldown_seconds == 4.5
    assert settings.enable_touch_speech is True
    assert settings.touch_speech_cooldown_seconds == 2.5
    assert str(settings.touch_speech_asset_root) == "/tmp/touch-assets"
    assert str(settings.touch_speech_marker_path) == "/tmp/touch-marker"
    assert str(settings.touch_speech_player_path) == "/bin/paplay"
    assert str(settings.battery_supply_path) == "/tmp/test-cw-bat"
    assert str(settings.charger_supply_path) == "/tmp/test-charger"
    assert str(settings.dialog_config_path) == "/tmp/dialog-config.json"
    assert str(settings.dialog_text_request_path) == "/tmp/dialog-text.json"
    assert str(settings.personality_runtime_path) == "/tmp/personality.json"
    assert str(settings.device_serial_path) == "/tmp/device-serial"
    assert str(settings.pulseaudio_ctl_binary) == "/bin/pactl"
    assert settings.pulse_server == "unix:/var/run/pulse/native"
    assert str(settings.touch_event_log_path) == "/tmp/main-log"
    assert settings.touch_monitor_poll_seconds == 0.25
    assert settings.paw_touch_confirmation_count == 3
    assert settings.paw_touch_confirmation_window_seconds == 4.0


def test_local_k1_requires_api_key() -> None:
    with pytest.raises(ValidationError, match="requires AI_CAT_API_KEY_ENABLED"):
        Settings(hardware_driver="local_k1")


def test_unknown_motion_profile_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(motion_profile="unreviewed_fast_profile")


def test_enabled_auth_requires_nonempty_key() -> None:
    with pytest.raises(ValidationError, match="AI_CAT_API_KEY is required"):
        Settings(api_key_enabled=True, api_key="")


def test_unconfirmed_service_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unconfirmed systemd service"):
        Settings(dialog_service="user-input.service")


def test_unconfirmed_hardware_binary_is_rejected() -> None:
    with pytest.raises(
        ValidationError,
        match="AI_CAT_HARDWARE_BINARY is not allowlisted",
    ):
        Settings(hardware_binary="/tmp/user-controlled-program")


def test_unconfirmed_pulseaudio_socket_is_rejected() -> None:
    with pytest.raises(ValidationError, match="AI_CAT_PULSE_SERVER is not allowlisted"):
        Settings(pulse_server="tcp:attacker.example:4713")


def test_unconfirmed_touch_speech_player_is_rejected() -> None:
    with pytest.raises(
        ValidationError,
        match="AI_CAT_TOUCH_SPEECH_PLAYER_PATH is not allowlisted",
    ):
        Settings(touch_speech_player_path="/tmp/player")


def test_secret_is_masked_in_settings_representation() -> None:
    settings = Settings(api_key_enabled=True, api_key="do-not-print")

    assert "do-not-print" not in repr(settings)
