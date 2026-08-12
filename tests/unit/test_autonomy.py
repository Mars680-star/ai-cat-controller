import random
import time
from pathlib import Path

import pytest

from ai_cat_controller.autonomy import (
    SAFE_ACTIONS,
    SHORT_PHRASES,
    AutonomyConfig,
    AutonomyWorker,
)


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
        self.actions: list[tuple[str, str, float, int]] = []
        self.tail_actions: list[tuple[str, float, int]] = []
        self.phrases: list[tuple[str, str]] = []

    def dialog_status(self) -> dict[str, object]:
        return self.status

    def personality_profile(self) -> dict[str, object]:
        return self.personality

    def start_head_action(
        self,
        action: str,
        request_id: str,
        *,
        intensity: float = 0.35,
        duration_ms: int = 1800,
    ) -> None:
        self.actions.append((action, request_id, intensity, duration_ms))

    def speak(self, content: str, request_id: str) -> None:
        self.phrases.append((content, request_id))

    def start_tail_action(
        self,
        request_id: str,
        *,
        intensity: float = 0.35,
        duration_ms: int = 600,
    ) -> None:
        self.tail_actions.append((request_id, intensity, duration_ms))


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
    assert all("tail" not in action for action, *_ in client.actions)


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


def test_conversation_motion_repeats_head_motion_at_safe_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 100.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])
    now_ms = int(time.time() * 1000)
    client = FakeClient(
        {
            "state": "answering",
            "session_active": True,
            "can_interrupt": True,
            "stale": False,
            "sequence": 12,
            "updated_at_ms": now_ms - 1500,
        }
    )
    worker = AutonomyWorker(
        autonomy_config(
            conversation_motion_enabled=True,
            conversation_motion_delay_seconds=0.0,
            conversation_head_minimum_interval_seconds=7.0,
            conversation_head_maximum_interval_seconds=7.0,
        ),
        client,  # type: ignore[arg-type]
        random_source=random.Random(7),
    )

    first = worker.run_conversation_motion_once()
    second = worker.run_conversation_motion_once()
    clock["now"] += 7.0
    third = worker.run_conversation_motion_once()

    assert first["executed"] is True
    assert second == {"executed": False, "reason": "waiting_for_interval"}
    assert third["executed"] is True
    assert len(client.actions) == 2
    action, request_id, intensity, duration_ms = client.actions[0]
    assert action in SAFE_ACTIONS
    assert request_id.startswith("dialog-motion-")
    assert intensity == 0.2
    assert duration_ms == 900


def test_conversation_tail_runs_more_frequently_than_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 200.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])
    client = FakeClient(
        {
            "state": "answering",
            "session_active": True,
            "can_interrupt": True,
            "stale": False,
            "sequence": 30,
            "updated_at_ms": int(time.time() * 1000),
        }
    )
    worker = AutonomyWorker(
        autonomy_config(
            conversation_motion_enabled=True,
            conversation_motion_delay_seconds=0.0,
            conversation_head_minimum_interval_seconds=7.0,
            conversation_head_maximum_interval_seconds=7.0,
            conversation_tail_enabled=True,
            conversation_tail_delay_seconds=2.0,
            conversation_tail_minimum_interval_seconds=3.0,
            conversation_tail_maximum_interval_seconds=3.0,
        ),
        client,  # type: ignore[arg-type]
        random_source=random.Random(7),
    )

    worker.run_conversation_motion_once()
    for timestamp in (202.0, 205.0, 207.0, 208.0, 211.0):
        clock["now"] = timestamp
        worker.run_conversation_motion_once()

    assert len(client.actions) == 2
    assert len(client.tail_actions) == 4
    assert all(item[0].startswith("dialog-tail-") for item in client.tail_actions)
    assert all(item[1:] == (0.35, 600) for item in client.tail_actions)


def test_conversation_motion_schedule_resets_after_answer() -> None:
    client = FakeClient(
        {
            "state": "answering",
            "session_active": True,
            "can_interrupt": True,
            "stale": False,
            "sequence": 31,
            "updated_at_ms": int(time.time() * 1000),
        }
    )
    worker = AutonomyWorker(
        autonomy_config(conversation_motion_enabled=True),
        client,  # type: ignore[arg-type]
    )

    worker.run_conversation_motion_once()
    client.status = {**client.status, "state": "followup_listening"}

    assert worker.run_conversation_motion_once() == {
        "executed": False,
        "reason": "not_answering",
    }
    assert worker._conversation_token is None


def test_conversation_motion_waits_for_answer_and_configured_delay() -> None:
    now_ms = int(time.time() * 1000)
    client = FakeClient(
        {
            "state": "answering",
            "session_active": True,
            "can_interrupt": True,
            "stale": False,
            "sequence": 20,
            "updated_at_ms": now_ms - 100,
        }
    )
    worker = AutonomyWorker(
        autonomy_config(
            conversation_motion_enabled=True,
            conversation_motion_delay_seconds=1.0,
        ),
        client,  # type: ignore[arg-type]
    )

    waiting = worker.run_conversation_motion_once()
    client.status = {
        **client.status,
        "state": "followup_listening",
        "updated_at_ms": now_ms - 2000,
    }
    listening = worker.run_conversation_motion_once()

    assert waiting == {"executed": False, "reason": "waiting_for_interval"}
    assert listening == {"executed": False, "reason": "not_answering"}
    assert client.actions == []


def test_conversation_motion_config_is_bounded_and_disabled_by_default() -> None:
    assert AutonomyConfig.from_env({}).conversation_motion_enabled is False

    config = AutonomyConfig.from_env(
        {
            "AI_CAT_CONVERSATION_MOTION_ENABLED": "true",
            "AI_CAT_CONVERSATION_MOTION_PROBABILITY": "0.75",
            "AI_CAT_CONVERSATION_MOTION_DELAY_SECONDS": "1.2",
            "AI_CAT_CONVERSATION_POLL_INTERVAL_SECONDS": "0.4",
            "AI_CAT_CONVERSATION_HEAD_MIN_INTERVAL_SECONDS": "8",
            "AI_CAT_CONVERSATION_HEAD_MAX_INTERVAL_SECONDS": "11",
            "AI_CAT_CONVERSATION_TAIL_ENABLED": "true",
            "AI_CAT_CONVERSATION_TAIL_DELAY_SECONDS": "2.5",
            "AI_CAT_CONVERSATION_TAIL_MIN_INTERVAL_SECONDS": "3.5",
            "AI_CAT_CONVERSATION_TAIL_MAX_INTERVAL_SECONDS": "5",
        }
    )

    assert config.conversation_motion_enabled is True
    assert config.conversation_motion_probability == 0.75
    assert config.conversation_motion_delay_seconds == 1.2
    assert config.conversation_poll_interval_seconds == 0.4
    assert config.conversation_head_minimum_interval_seconds == 8.0
    assert config.conversation_head_maximum_interval_seconds == 11.0
    assert config.conversation_tail_enabled is True
    assert config.conversation_tail_delay_seconds == 2.5
    assert config.conversation_tail_minimum_interval_seconds == 3.5
    assert config.conversation_tail_maximum_interval_seconds == 5.0

    with pytest.raises(ValueError, match="POLL_INTERVAL"):
        AutonomyConfig.from_env(
            {"AI_CAT_CONVERSATION_POLL_INTERVAL_SECONDS": "0.1"}
        )

    with pytest.raises(ValueError, match="TAIL_MIN_INTERVAL"):
        AutonomyConfig.from_env(
            {"AI_CAT_CONVERSATION_TAIL_MIN_INTERVAL_SECONDS": "1.0"}
        )
