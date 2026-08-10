from __future__ import annotations

from typing import Any

import pytest

from ai_cat_controller.core.config import Settings
from ai_cat_controller.services.touch_event_monitor import TouchEventMonitor


class FakeProduct:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def add_device_touch(
        self,
        *,
        device_serial: str,
        request_id: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        event = {
            "device_serial": device_serial,
            "request_id": request_id,
            "metadata": metadata,
        }
        self.events.append(event)
        return event


def monitor_settings(tmp_path) -> Settings:
    serial_path = tmp_path / "serial-number"
    serial_path.write_bytes(b"K1-TOUCH-001\0")
    return Settings(
        device_serial_path=serial_path,
        touch_event_log_path=tmp_path / "main_log",
        touch_monitor_poll_seconds=10.0,
    )


@pytest.mark.asyncio
async def test_touch_monitor_ignores_history_and_imports_appended_event(
    tmp_path,
) -> None:
    settings = monitor_settings(tmp_path)
    settings.touch_event_log_path.write_text(
        "[Back Touch Handler] BACK_SHORT_TOUCH detected\n",
        encoding="utf-8",
    )
    product = FakeProduct()
    monitor = TouchEventMonitor(settings, product)  # type: ignore[arg-type]
    await monitor.start()
    try:
        assert await monitor.poll_once() == 0
        with settings.touch_event_log_path.open("a", encoding="utf-8") as stream:
            stream.write(
                "noise from another subsystem\n"
                "[Right Foot Touch Handler] RIGHT_FOOT_LONG_TOUCH detected\n"
            )

        assert await monitor.poll_once() == 1
    finally:
        await monitor.close()

    assert len(product.events) == 1
    assert product.events[0]["device_serial"] == "K1-TOUCH-001"
    assert product.events[0]["request_id"].startswith("k1-touch-")
    assert product.events[0]["metadata"] == {
        "source": "k1_touch_log",
        "sensor": "right_foot",
        "hardware_sensor": "right_foot",
        "gesture": "long",
    }


@pytest.mark.asyncio
async def test_touch_monitor_waits_for_complete_log_line(tmp_path) -> None:
    settings = monitor_settings(tmp_path)
    settings.touch_event_log_path.touch()
    product = FakeProduct()
    monitor = TouchEventMonitor(settings, product)  # type: ignore[arg-type]
    await monitor.start()
    try:
        settings.touch_event_log_path.write_text(
            "[Head Touch Handler] HEAD_SHORT_TOUCH detected",
            encoding="utf-8",
        )
        assert await monitor.poll_once() == 0
        with settings.touch_event_log_path.open("a", encoding="utf-8") as stream:
            stream.write("\n")
        assert await monitor.poll_once() == 1
        assert await monitor.poll_once() == 0
    finally:
        await monitor.close()

    assert len(product.events) == 1


@pytest.mark.asyncio
async def test_touch_monitor_reads_new_log_created_after_start(tmp_path) -> None:
    settings = monitor_settings(tmp_path)
    product = FakeProduct()
    monitor = TouchEventMonitor(settings, product)  # type: ignore[arg-type]
    await monitor.start()
    try:
        settings.touch_event_log_path.write_text(
            "[Nose Touch Handler] NOSE_SHORT_TOUCH detected\n",
            encoding="utf-8",
        )
        assert await monitor.poll_once() == 1
    finally:
        await monitor.close()

    assert product.events[0]["metadata"]["sensor"] == "head"
    assert product.events[0]["metadata"]["hardware_sensor"] == "nose"


@pytest.mark.parametrize(
    ("handler", "hardware_sensor", "sensor"),
    [
        ("Head", "HEAD", "nose"),
        ("Nose", "NOSE", "head"),
        ("Back", "BACK", "left_foot"),
        ("Left Foot", "LEFT_FOOT", "back"),
        ("Right Foot", "RIGHT_FOOT", "right_foot"),
    ],
)
@pytest.mark.asyncio
async def test_touch_monitor_normalizes_k1_physical_sensor_mapping(
    tmp_path,
    handler: str,
    hardware_sensor: str,
    sensor: str,
) -> None:
    settings = monitor_settings(tmp_path)
    settings.touch_event_log_path.touch()
    product = FakeProduct()
    monitor = TouchEventMonitor(settings, product)  # type: ignore[arg-type]
    await monitor.start()
    try:
        with settings.touch_event_log_path.open("a", encoding="utf-8") as stream:
            stream.write(
                f"[{handler} Touch Handler] {hardware_sensor}_SHORT_TOUCH detected\n"
            )
        assert await monitor.poll_once() == 1
    finally:
        await monitor.close()

    assert product.events[0]["metadata"] == {
        "source": "k1_touch_log",
        "sensor": sensor,
        "hardware_sensor": hardware_sensor.lower(),
        "gesture": "short",
    }
