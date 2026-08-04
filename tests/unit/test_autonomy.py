import random

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


def autonomy_config(**overrides: object) -> AutonomyConfig:
    values = {
        "api_port": 8000,
        "api_key": "test-key",
        "initial_delay_seconds": 0.0,
        "minimum_interval_seconds": 60.0,
        "maximum_interval_seconds": 120.0,
        "phrase_probability": 1.0,
        "cloud_speech_enabled": False,
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
    assert all("tail" not in action for action, _ in client.actions)


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
