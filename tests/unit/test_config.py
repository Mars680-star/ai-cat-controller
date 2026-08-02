import pytest
from pydantic import ValidationError

from ai_cat_controller.core.config import Settings


def test_default_settings_are_safe_mock() -> None:
    settings = Settings.from_env({})

    assert settings.hardware_driver == "mock"
    assert settings.api_host == "127.0.0.1"
    assert settings.api_key_enabled is False


def test_environment_values_are_parsed() -> None:
    settings = Settings.from_env(
        {
            "AI_CAT_HARDWARE_DRIVER": "mock",
            "AI_CAT_API_PORT": "9000",
            "AI_CAT_LOG_LEVEL": "debug",
            "AI_CAT_MOTION_COOLDOWN_SECONDS": "0.5",
            "AI_CAT_BATTERY_SUPPLY_PATH": "/tmp/test-cw-bat",
            "AI_CAT_CHARGER_SUPPLY_PATH": "/tmp/test-charger",
        }
    )

    assert settings.api_port == 9000
    assert settings.log_level == "DEBUG"
    assert settings.motion_cooldown_seconds == 0.5
    assert str(settings.battery_supply_path) == "/tmp/test-cw-bat"
    assert str(settings.charger_supply_path) == "/tmp/test-charger"


def test_local_k1_requires_api_key() -> None:
    with pytest.raises(ValidationError, match="requires AI_CAT_API_KEY_ENABLED"):
        Settings(hardware_driver="local_k1")


def test_enabled_auth_requires_nonempty_key() -> None:
    with pytest.raises(ValidationError, match="AI_CAT_API_KEY is required"):
        Settings(api_key_enabled=True, api_key="")


def test_unconfirmed_service_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unconfirmed systemd service"):
        Settings(dialog_service="user-input.service")


def test_secret_is_masked_in_settings_representation() -> None:
    settings = Settings(api_key_enabled=True, api_key="do-not-print")

    assert "do-not-print" not in repr(settings)
