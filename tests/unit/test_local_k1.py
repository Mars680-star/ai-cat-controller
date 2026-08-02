import os
import time
from typing import Any

import pytest

from ai_cat_controller.adapters.command_runner import CommandResult
from ai_cat_controller.adapters.local_k1 import LocalK1Adapter
from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.errors import AdapterNotImplementedError


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def run(self, executable: str, args: list[str]) -> CommandResult:
        self.calls.append((executable, tuple(args)))
        output = "active" if args[0] == "is-active" else "enabled"
        return CommandResult(0, output, "", False)


class PartiallyFailingRunner(FakeRunner):
    async def run(self, executable: str, args: list[str]) -> CommandResult:
        if args[1] == "volc-conv-ai.service":
            raise FileNotFoundError("simulated query failure")
        return await super().run(executable, args)


def local_settings() -> Settings:
    return Settings(
        hardware_driver="local_k1",
        api_key_enabled=True,
        api_key="test-only-key",
    )


@pytest.mark.asyncio
async def test_local_status_uses_only_confirmed_read_commands() -> None:
    runner = FakeRunner()
    adapter = LocalK1Adapter(local_settings(), runner)  # type: ignore[arg-type]
    await adapter.connect()

    statuses = await adapter.get_services_status()

    assert len(statuses) == 3
    assert all(item["active"] and item["enabled"] for item in statuses)
    assert {args[0] for _, args in runner.calls} == {"is-active", "is-enabled"}
    assert {args[1] for _, args in runner.calls} == {
        "volc-pulseaudio.service",
        "volc-conv-ai.service",
        "volc-k1-wake-word.service",
    }


@pytest.mark.asyncio
async def test_one_service_failure_does_not_fail_status_response() -> None:
    adapter = LocalK1Adapter(
        local_settings(),
        PartiallyFailingRunner(),  # type: ignore[arg-type]
    )

    statuses = await adapter.get_services_status()
    by_name = {item["service_name"]: item for item in statuses}

    assert len(statuses) == 3
    assert by_name["volc-conv-ai.service"]["active"] is False
    assert "simulated query failure" in by_name["volc-conv-ai.service"]["error"]
    assert by_name["volc-pulseaudio.service"]["active"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("shake_head", (0.5, 600)),
        ("nod_head", (0.5, 600)),
        ("wag_tail", (0.5, 600)),
        ("stop_motion", ()),
    ],
)
async def test_local_actions_are_not_implemented(
    method: str, args: tuple[Any, ...]
) -> None:
    adapter = LocalK1Adapter(local_settings(), FakeRunner())  # type: ignore[arg-type]

    with pytest.raises(AdapterNotImplementedError):
        await getattr(adapter, method)(*args)


@pytest.mark.asyncio
async def test_local_dialog_control_uses_fixed_signals() -> None:
    runner = FakeRunner()
    adapter = LocalK1Adapter(local_settings(), runner)  # type: ignore[arg-type]

    await adapter.wake_dialog()
    await adapter.interrupt_dialog()

    assert runner.calls == [
        (
            "/usr/bin/systemctl",
            ("kill", "--signal=SIGUSR1", "volc-conv-ai.service"),
        ),
        (
            "/usr/bin/systemctl",
            ("kill", "--signal=SIGUSR2", "volc-conv-ai.service"),
        ),
    ]


@pytest.mark.asyncio
async def test_local_device_status_reads_k1_power_supply(tmp_path) -> None:
    battery_path = tmp_path / "cw-bat"
    charger_path = tmp_path / "ip2317-charger"
    battery_path.mkdir()
    charger_path.mkdir()
    (battery_path / "capacity").write_text("27\n", encoding="ascii")
    (battery_path / "status").write_text("Charging\n", encoding="ascii")
    (battery_path / "present").write_text("1\n", encoding="ascii")
    (battery_path / "voltage_now").write_text("3543000\n", encoding="ascii")
    (charger_path / "online").write_text("1\n", encoding="ascii")
    settings = local_settings().model_copy(
        update={
            "battery_supply_path": battery_path,
            "charger_supply_path": charger_path,
            "dialog_status_path": tmp_path / "missing-dialog-status.json",
        }
    )
    adapter = LocalK1Adapter(settings, FakeRunner())  # type: ignore[arg-type]
    await adapter.connect()

    result = await adapter.get_device_status()

    assert result["battery_available"] is True
    assert result["battery_percent"] == 27
    assert result["battery_status"] == "charging"
    assert result["battery_present"] is True
    assert result["battery_voltage_mv"] == 3543
    assert result["charging"] is True
    assert result["charger_online"] is True
    assert result["battery_error"] is None


@pytest.mark.asyncio
async def test_local_device_status_handles_missing_power_supply(tmp_path) -> None:
    settings = local_settings().model_copy(
        update={
            "battery_supply_path": tmp_path / "missing-battery",
            "charger_supply_path": tmp_path / "missing-charger",
            "dialog_status_path": tmp_path / "missing-dialog-status.json",
        }
    )
    adapter = LocalK1Adapter(settings, FakeRunner())  # type: ignore[arg-type]

    result = await adapter.get_device_status()

    assert result["battery_available"] is False
    assert result["battery_percent"] is None
    assert result["battery_status"] == "unavailable"
    assert result["battery_present"] is None
    assert result["battery_voltage_mv"] is None
    assert result["charging"] is None
    assert result["charger_online"] is None
    assert "capacity" in result["battery_error"]


@pytest.mark.asyncio
async def test_local_device_status_rejects_invalid_capacity(tmp_path) -> None:
    battery_path = tmp_path / "cw-bat"
    charger_path = tmp_path / "ip2317-charger"
    battery_path.mkdir()
    charger_path.mkdir()
    (battery_path / "capacity").write_text("127\n", encoding="ascii")
    (battery_path / "status").write_text("Discharging\n", encoding="ascii")
    (battery_path / "present").write_text("1\n", encoding="ascii")
    (battery_path / "voltage_now").write_text("3800000\n", encoding="ascii")
    (charger_path / "online").write_text("0\n", encoding="ascii")
    settings = local_settings().model_copy(
        update={
            "battery_supply_path": battery_path,
            "charger_supply_path": charger_path,
        }
    )
    adapter = LocalK1Adapter(settings, FakeRunner())  # type: ignore[arg-type]

    result = await adapter.get_device_status()

    assert result["battery_available"] is False
    assert result["battery_percent"] is None
    assert result["battery_status"] == "discharging"
    assert result["charging"] is False
    assert "capacity" in result["battery_error"]


@pytest.mark.asyncio
async def test_local_dialog_status_reads_fixed_json_file(tmp_path) -> None:
    status_path = tmp_path / "dialog-status.json"
    status_path.write_text(
        """
        {
          "state": "thinking",
          "message": "问题已收到，正在思考",
          "session_active": true,
          "can_interrupt": true,
          "follow_up_deadline_ms": 0,
          "updated_at_ms": 9999999999999,
          "sequence": 7
        }
        """,
        encoding="utf-8",
    )
    settings = local_settings().model_copy(
        update={"dialog_status_path": status_path}
    )
    adapter = LocalK1Adapter(settings, FakeRunner())  # type: ignore[arg-type]

    result = await adapter.get_dialog_status()

    assert result["state"] == "thinking"
    assert result["session_active"] is True
    assert result["sequence"] == 7
    assert result["source"] == "local_k1"


@pytest.mark.asyncio
async def test_local_dialog_status_tolerates_invalid_numeric_fields(tmp_path) -> None:
    status_path = tmp_path / "dialog-status.json"
    status_path.write_text(
        """
        {
          "state": "listening",
          "follow_up_deadline_ms": "invalid",
          "updated_at_ms": null,
          "sequence": {}
        }
        """,
        encoding="utf-8",
    )
    settings = local_settings().model_copy(
        update={"dialog_status_path": status_path}
    )
    adapter = LocalK1Adapter(settings, FakeRunner())  # type: ignore[arg-type]

    result = await adapter.get_dialog_status()

    assert result["state"] == "listening"
    assert result["follow_up_deadline_ms"] == 0
    assert result["updated_at_ms"] == 0
    assert result["sequence"] == 0


@pytest.mark.asyncio
async def test_old_dialog_status_is_current_while_native_pid_exists(tmp_path) -> None:
    status_path = tmp_path / "dialog-status.json"
    status_path.write_text(
        f"""
        {{
          "state": "ready",
          "updated_at_ms": {int(time.time() * 1000) - 60_000},
          "pid": {os.getpid()}
        }}
        """,
        encoding="utf-8",
    )
    settings = local_settings().model_copy(
        update={"dialog_status_path": status_path}
    )
    adapter = LocalK1Adapter(settings, FakeRunner())  # type: ignore[arg-type]

    result = await adapter.get_dialog_status()

    assert result["stale"] is False


@pytest.mark.asyncio
async def test_old_dialog_status_is_stale_when_native_pid_is_gone(tmp_path) -> None:
    status_path = tmp_path / "dialog-status.json"
    status_path.write_text(
        f"""
        {{
          "state": "ready",
          "updated_at_ms": {int(time.time() * 1000) - 60_000},
          "pid": 99999999
        }}
        """,
        encoding="utf-8",
    )
    settings = local_settings().model_copy(
        update={"dialog_status_path": status_path}
    )
    adapter = LocalK1Adapter(settings, FakeRunner())  # type: ignore[arg-type]

    result = await adapter.get_dialog_status()

    assert result["stale"] is True
