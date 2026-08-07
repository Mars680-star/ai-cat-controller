import json
import stat

import pytest

from ai_cat_controller.core.config import Settings
from ai_cat_controller.domain.personalities import PERSONALITIES
from ai_cat_controller.services.personality_service import PersonalityService


def _pet(personality_id: str, *, points: int = 0) -> dict[str, object]:
    return {
        "serial_number": "K1-PERSONALITY-TEST",
        "pet_id": "pet-test",
        "name": "小安",
        "personality_id": personality_id,
        "intimacy_points": points,
    }


def test_five_personalities_have_distinct_volcengine_runtime_profiles() -> None:
    service = PersonalityService(Settings())
    profiles = [
        service._build_profile(_pet(personality.personality_id))
        for personality in PERSONALITIES
    ]

    assert len(profiles) == 5
    assert len({profile["voice_type"] for profile in profiles}) == 5
    assert len({profile["revision"] for profile in profiles}) == 5
    for personality, profile in zip(PERSONALITIES, profiles, strict=True):
        update_config = profile["session_update"]["session"]["config"]
        assert set(personality.voice_function_triggers.values()).issubset(
            personality.allowed_voice_functions
        )
        assert profile["voice_type"] == personality.voice_id
        assert profile["personality_name"] in profile["system_prompt"]
        assert update_config["LLMConfig"]["SystemMessages"] == [
            profile["system_prompt"]
        ]
        assert (
            update_config["TTSConfig"]["ProviderParams"]["audio"]["voice_type"]
            == personality.voice_id
        )
        assert update_config["TTSConfig"]["Provider"] == "volcano_bidirection"
        assert (
            update_config["TTSConfig"]["ProviderParams"]["ResourceId"]
            == "volc.service_type.10029"
        )


def test_intimacy_level_changes_address_prompt_and_revision() -> None:
    service = PersonalityService(Settings())

    first = service._build_profile(_pet("sunny_explorer", points=0))
    familiar = service._build_profile(_pet("sunny_explorer", points=20))

    assert first["intimacy_level"] == 0
    assert first["address"] == "新朋友"
    assert familiar["intimacy_level"] == 1
    assert familiar["address"] == "搭档"
    assert "称呼用户为“搭档”" in familiar["system_prompt"]
    assert first["revision"] != familiar["revision"]


@pytest.mark.asyncio
async def test_local_profile_is_atomic_private_and_filters_disabled_tail(
    tmp_path,
) -> None:
    serial_path = tmp_path / "serial-number"
    serial_path.write_bytes(b"K1-PERSONALITY-TEST\x00")
    runtime_path = tmp_path / "runtime" / "personality.json"
    settings = Settings(
        hardware_driver="local_k1",
        api_key_enabled=True,
        api_key="test-only-key",
        device_serial_path=serial_path,
        personality_runtime_path=runtime_path,
        enable_tail_motion=False,
    )
    service = PersonalityService(settings)

    result = await service.sync_pet(_pet("sunny_explorer"))
    payload = json.loads(runtime_path.read_text(encoding="utf-8"))

    assert result["sync_state"] == "configured"
    assert result["revision"] == payload["revision"]
    assert "wag_tail" not in payload["allowed_functions"]
    assert "wag_tail" not in payload["system_prompt"]
    assert "celebration_combo" not in payload["system_prompt"]
    assert stat.S_IMODE(runtime_path.stat().st_mode) == 0o600
    assert not runtime_path.with_name("personality.json.tmp").exists()


def test_tail_voice_action_requires_hardware_and_intimacy_level_one() -> None:
    disabled = PersonalityService(Settings(enable_tail_motion=False))
    enabled = PersonalityService(Settings(enable_tail_motion=True))

    disabled_profile = disabled._build_profile(_pet("sunny_explorer", points=20))
    new_pet_profile = enabled._build_profile(_pet("sunny_explorer", points=0))
    familiar_profile = enabled._build_profile(_pet("sunny_explorer", points=20))

    assert "wag_tail" not in disabled_profile["allowed_functions"]
    assert "wag_tail" not in new_pet_profile["allowed_functions"]
    assert "wag_tail" in familiar_profile["allowed_functions"]


@pytest.mark.asyncio
async def test_mock_profile_is_visible_but_not_written(tmp_path) -> None:
    runtime_path = tmp_path / "personality.json"
    service = PersonalityService(
        Settings(personality_runtime_path=runtime_path)
    )

    result = await service.sync_pet(_pet("gentle_companion"))
    status = await service.status()

    assert result["sync_state"] == "mock_only"
    assert status["active"] is True
    assert status["personality_id"] == "gentle_companion"
    assert not runtime_path.exists()
