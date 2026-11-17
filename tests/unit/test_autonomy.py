import random
from pathlib import Path

import pytest

from ai_cat_controller.autonomy import (
    SAFE_ACTIONS,
    SHORT_PHRASES,
    AutonomyConfig,
    AutonomyWorker,
    ControllerApiError,
    LocalControllerClient,
)
from ai_cat_controller.core.config import Settings
from ai_cat_controller.services.autonomy_service import InProcessAutonomyService


class FakeClient:
    def __init__(
        self,
        status: dict[str, object],
        personality: dict[str, object] | None = None,
    ) -> None:
        self.status = status
        self.personality = personality or {
            "active": True,
            "native_applied": True,
            "personality_id": "sunny_explorer",
            "autonomy": {
                "action_weights": {"head/shake": 0.55, "head/nod": 0.45},
                "phrases": [
                    "要不要一起发现点新鲜事？",
                    "今天也要元气满满呀。",
                ],
            },
        }
        self.actions: list[tuple[str, str]] = []
        self.phrases: list[tuple[str, str]] = []

    def dialog_status(self) -> dict[str, object]:
        return self.status

    def personality_profile(self) -> dict[str, object]:
        return self.personality

    def start_head_action(self, action: str, request_id: str) -> None:
        self.actions.append((action, request_id))

    def speak(self, content: str, request_id: str) -> None:
        self.phrases.append((content, request_id))


class FakeLocalPlayer:
    def __init__(self) -> None:
        self.plays: list[tuple[str, str]] = []

    def play(self, personality_id: str, phrase: str) -> Path:
        self.plays.append((personality_id, phrase))
        return Path("/fixed/local/phrase.wav")

    def validate_personality_assets(self) -> tuple[Path, ...]:
        return tuple(
            Path(f"/fixed/local/phrase-{index:02d}.wav")
            for index in range(15)
        )


def autonomy_config(**overrides: object) -> AutonomyConfig:
    values = {
        "api_port": 8000,
        "api_key": "test-key",
        "initial_delay_seconds": 0.0,
        "minimum_interval_seconds": 60.0,
        "maximum_interval_seconds": 120.0,
        "phrase_probability": 1.0,
        "cloud_speech_enabled": False,
        "local_speech_enabled": False,
        "local_speech_motion_settle_seconds": 0.0,
    }
    values.update(overrides)
    return AutonomyConfig(**values)  # type: ignore[arg-type]


def test_autonomy_pause_does_not_read_state_submit_motion_or_play_speech():
    class NoCalls:
        def __getattr__(self, name):
            raise AssertionError("Paused autonomy must not invoke a client method")
    worker = AutonomyWorker(autonomy_config(enable_motion=False, conversation_motion_enabled=True,
        conversation_tail_enabled=True, has_tail=False, enable_tail_motion=True), NoCalls())
    assert worker.run_once() == {"executed": False, "reason": "motion_disabled"}
    assert worker.run_conversation_motion_once() == {"executed": False, "reason": "motion_disabled"}


@pytest.mark.parametrize("environment", ["AI_CAT_ENABLE_MOTION", "AI_CAT_HAS_TAIL", "AI_CAT_ENABLE_TAIL_MOTION"])
@pytest.mark.parametrize("value", ["", " ", " false", "maybe"])
def test_standalone_autonomy_rejects_ambiguous_safety_flags(environment, value):
    with pytest.raises(ValueError):
        AutonomyConfig.from_env({environment: value})


def test_inprocess_autonomy_uses_explicit_settings_flags_not_conflicting_environment():
    service = InProcessAutonomyService.from_settings(Settings(enable_motion=False, has_tail=False,
        enable_tail_motion=False), {"AI_CAT_ENABLE_MOTION": "true", "AI_CAT_HAS_TAIL": "true",
            "AI_CAT_ENABLE_TAIL_MOTION": "true"})
    assert service._worker._config.enable_motion is False
    assert service._worker._config.has_tail is False
    assert service._worker._config.enable_tail_motion is False


@pytest.mark.parametrize("options,reason", [({"enable_motion": False}, "动作已暂停"),
    ({"has_tail": False, "enable_tail_motion": True}, "没有尾部机构"),
    ({"enable_tail_motion": False}, "尚未开放")])
def test_autonomy_client_blocks_tail_before_http_even_if_called_directly(options, reason):
    client = LocalControllerClient(autonomy_config(**options))
    client._request = lambda *args, **kwargs: pytest.fail("Safety reject must not send HTTP")
    with pytest.raises(ControllerApiError, match=reason):
        client.start_tail_action("one")
    if options.get("enable_motion") is False:
        with pytest.raises(ControllerApiError, match="动作已暂停"):
            client.start_head_action("head/nod", "two")


def test_no_tail_conversation_autonomy_never_selects_a_tail_request():
    class ConversationClient(FakeClient):
        def start_head_action(self, action, request_id, **kwargs):
            self.actions.append((action, request_id))
        def start_tail_action(self, *args, **kwargs):
            pytest.fail("No-tail configuration must not submit tail motion")
    client = ConversationClient({"state": "answering", "session_active": True, "can_interrupt": True,
        "stale": False, "sequence": 1, "updated_at_ms": 1})
    worker = AutonomyWorker(autonomy_config(has_tail=False, enable_tail_motion=True,
        conversation_motion_enabled=True, conversation_tail_enabled=True, conversation_motion_delay_seconds=0,
        conversation_tail_delay_seconds=0), client, random_source=random.Random(7))
    result = worker.run_conversation_motion_once()
    assert result["executed"] and result["action"] in SAFE_ACTIONS


def test_autonomy_submits_only_safe_head_motion_and_allowlisted_phrase() -> None:
    client = FakeClient(
        {"state": "ready", "session_active": False, "stale": False}
    )
    worker = AutonomyWorker(
        autonomy_config(cloud_speech_enabled=True),
        client,  # type: ignore[arg-type]
        random_source=random.Random(7),
    )

    result = worker.run_once()

    assert result["executed"] is True
    assert client.actions[0][0] in SAFE_ACTIONS
    assert client.phrases[0][0] in SHORT_PHRASES
    assert all("tail" not in action for action, _ in client.actions)


def test_autonomy_plays_personality_phrase_locally_without_cloud() -> None:
    client = FakeClient(
        {"state": "offline", "session_active": False, "stale": True}
    )
    player = FakeLocalPlayer()
    worker = AutonomyWorker(
        autonomy_config(local_speech_enabled=True),
        client,  # type: ignore[arg-type]
        random_source=random.Random(7),
        local_player=player,  # type: ignore[arg-type]
    )

    result = worker.run_once()

    assert result["phrase"] in SHORT_PHRASES
    assert player.plays == [("sunny_explorer", result["phrase"])]
    assert client.phrases == []


def test_autonomy_validates_all_local_assets_when_local_speech_is_enabled() -> None:
    player = FakeLocalPlayer()
    worker = AutonomyWorker(
        autonomy_config(local_speech_enabled=True),
        FakeClient(
            {"state": "offline", "session_active": False, "stale": True}
        ),  # type: ignore[arg-type]
        local_player=player,  # type: ignore[arg-type]
    )

    assert worker.validate_startup() == 15


def test_autonomy_skips_asset_validation_when_local_speech_is_disabled() -> None:
    worker = AutonomyWorker(
        autonomy_config(local_speech_enabled=False),
        FakeClient(
            {"state": "offline", "session_active": False, "stale": True}
        ),  # type: ignore[arg-type]
        local_player=object(),  # type: ignore[arg-type]
    )

    assert worker.validate_startup() == 0


def test_autonomy_can_resume_after_user_ends_dialog() -> None:
    client = FakeClient(
        {"state": "interrupted", "session_active": False, "stale": False}
    )
    worker = AutonomyWorker(
        autonomy_config(phrase_probability=0.0),
        client,  # type: ignore[arg-type]
        random_source=random.Random(2),
    )

    result = worker.run_once()

    assert result["executed"] is True
    assert len(client.actions) == 1


@pytest.mark.parametrize(
    "status",
    [
        {"state": "listening", "session_active": True, "stale": False},
        {"state": "thinking", "session_active": True, "stale": False},
        {"state": "answering", "session_active": True, "stale": False},
        {"state": "followup_listening", "session_active": True, "stale": False},
        {"state": "ready", "session_active": False, "stale": True},
    ],
)
def test_autonomy_skips_when_dialog_is_not_idle(status: dict[str, object]) -> None:
    client = FakeClient(status)
    worker = AutonomyWorker(
        autonomy_config(),
        client,  # type: ignore[arg-type]
        random_source=random.Random(1),
    )

    result = worker.run_once()

    assert result["executed"] is False
    assert client.actions == []
    assert client.phrases == []


def test_autonomy_config_rejects_too_frequent_motion() -> None:
    with pytest.raises(ValueError, match="MIN_INTERVAL"):
        AutonomyConfig.from_env(
            {
                "AI_CAT_AUTONOMY_MIN_INTERVAL_SECONDS": "5",
                "AI_CAT_AUTONOMY_MAX_INTERVAL_SECONDS": "60",
            }
        )


def test_autonomy_config_defaults_to_three_minutes_and_rejects_two_speech_modes() -> None:
    config = AutonomyConfig.from_env({})

    assert config.minimum_interval_seconds == 180.0
    assert config.maximum_interval_seconds == 180.0

    with pytest.raises(ValueError, match="cannot both be enabled"):
        AutonomyConfig.from_env(
            {
                "AI_CAT_AUTONOMY_CLOUD_SPEECH_ENABLED": "true",
                "AI_CAT_AUTONOMY_LOCAL_SPEECH_ENABLED": "true",
            }
        )


def test_autonomy_waits_until_native_personality_is_applied() -> None:
    client = FakeClient(
        {"state": "ready", "session_active": False, "stale": False},
        {
            "active": True,
            "native_applied": False,
            "personality_id": "gentle_companion",
        },
    )
    worker = AutonomyWorker(
        autonomy_config(),
        client,  # type: ignore[arg-type]
        random_source=random.Random(1),
    )

    result = worker.run_once()

    assert result["reason"] == "personality_not_ready"
    assert client.actions == []


def test_autonomy_runs_motion_offline_without_starting_cloud_speech() -> None:
    client = FakeClient(
        {"state": "offline", "session_active": False, "stale": True},
        {
            "active": True,
            "native_applied": False,
            "personality_id": "gentle_companion",
            "autonomy": {
                "action_weights": {"head/nod": 1.0},
                "phrases": ["我在这里，慢慢来就好。"],
            },
        },
    )
    worker = AutonomyWorker(
        autonomy_config(cloud_speech_enabled=False),
        client,  # type: ignore[arg-type]
        random_source=random.Random(1),
    )

    result = worker.run_once()

    assert result == {"executed": True, "action": "head/nod", "phrase": None}
    assert len(client.actions) == 1
    assert client.phrases == []
