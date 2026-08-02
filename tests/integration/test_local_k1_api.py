from fastapi.testclient import TestClient

from ai_cat_controller.adapters.command_runner import CommandResult, CommandRunner
from ai_cat_controller.core.config import Settings
from ai_cat_controller.main import create_app


def test_local_k1_head_motion_uses_allowlisted_command(
    monkeypatch,
) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    async def fake_run(
        self: CommandRunner, executable: str, args: list[str]
    ) -> CommandResult:
        del self
        calls.append((executable, tuple(args)))
        return CommandResult(0, "motion completed", "", False)

    monkeypatch.setattr(CommandRunner, "run", fake_run)
    app = create_app(
        Settings(
            hardware_driver="local_k1",
            api_key_enabled=True,
            api_key="test-only-key",
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/motion/head/shake",
            headers={"X-API-Key": "test-only-key"},
            json={"intensity": 0.5, "duration_ms": 600},
        )
        client.get(
            "/api/v1/device/status",
            headers={"X-API-Key": "test-only-key"},
        )

    assert response.status_code == 202
    assert ("/usr/bin/ai-toy_app", ("motor", "head_lr", "2")) in calls


def test_local_k1_tail_motion_remains_disabled() -> None:
    app = create_app(
        Settings(
            hardware_driver="local_k1",
            api_key_enabled=True,
            api_key="test-only-key",
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/motion/tail/wag",
            headers={"X-API-Key": "test-only-key"},
            json={"intensity": 0.5, "duration_ms": 600},
        )

    assert response.status_code == 501
    assert "尾部动作默认禁用" in response.json()["message"]


def test_local_k1_nod_motion_uses_fixed_command(monkeypatch) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    async def fake_run(
        self: CommandRunner, executable: str, args: list[str]
    ) -> CommandResult:
        del self
        calls.append((executable, tuple(args)))
        return CommandResult(0, "motion completed", "", False)

    monkeypatch.setattr(CommandRunner, "run", fake_run)
    app = create_app(
        Settings(
            hardware_driver="local_k1",
            api_key_enabled=True,
            api_key="test-only-key",
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/motion/head/nod",
            headers={"X-API-Key": "test-only-key"},
            json={"intensity": 0.5, "duration_ms": 600},
        )

    assert response.status_code == 202
    assert ("/usr/bin/ai-toy_app", ("motor", "head_ud", "2")) in calls


def test_local_k1_tail_motion_uses_low_speed_only_when_enabled(monkeypatch) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    async def fake_run(
        self: CommandRunner, executable: str, args: list[str]
    ) -> CommandResult:
        del self
        calls.append((executable, tuple(args)))
        return CommandResult(0, "motion completed", "", False)

    monkeypatch.setattr(CommandRunner, "run", fake_run)
    app = create_app(
        Settings(
            hardware_driver="local_k1",
            api_key_enabled=True,
            api_key="test-only-key",
            enable_tail_motion=True,
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/motion/tail/wag",
            headers={"X-API-Key": "test-only-key"},
            json={"intensity": 1.0, "duration_ms": 3000},
        )

    assert response.status_code == 202
    assert ("/usr/bin/ai-toy_app", ("motor", "tail_lr", "1")) in calls
