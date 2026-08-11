from __future__ import annotations

from typing import Any

import pytest

from ai_cat_controller.core.config import Settings
from ai_cat_controller.services.touch_event_monitor import (
    TouchEventMonitor,
    touch_sensor_map_for_serial,
)


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


def monitor_settings(
    tmp_path,
    *,
    serial: str = "7c2b63fd4a128",
    **overrides: Any,
) -> Settings:
    serial_path = tmp_path / "serial-number"
    serial_path.write_bytes(f"{serial}\0".encode("ascii"))
    values = {
        "device_serial_path": serial_path,
        "touch_event_log_path": tmp_path / "main_log",
        "touch_monitor_poll_seconds": 10.0,
    }
    values.update(overrides)
    return Settings(
        **values,
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

        assert await monitor.poll_once() == 0
        with settings.touch_event_log_path.open("a", encoding="utf-8") as stream:
            stream.write(
                "[Right Foot Touch Handler] RIGHT_FOOT_SHORT_TOUCH detected\n"
            )
        assert await monitor.poll_once() == 1
    finally:
        await monitor.close()

    assert len(product.events) == 1
    assert product.events[0]["device_serial"] == "7c2b63fd4a128"
    assert product.events[0]["request_id"].startswith("k1-touch-")
    assert product.events[0]["metadata"] == {
        "source": "k1_touch_log",
        "sensor": "right_foot",
        "hardware_sensor": "right_foot",
        "sensor_mapping": "identity",
        "gesture": "short",
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

    assert product.events[0]["metadata"]["sensor"] == "nose"
    assert product.events[0]["metadata"]["hardware_sensor"] == "nose"
    assert product.events[0]["metadata"]["sensor_mapping"] == "identity"


@pytest.mark.parametrize(
    ("handler", "hardware_sensor", "sensor"),
    [
        ("Head", "HEAD", "head"),
        ("Nose", "NOSE", "nose"),
        ("Back", "BACK", "back"),
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
        "sensor_mapping": "identity",
        "gesture": "short",
    }


@pytest.mark.parametrize(
    ("handler", "hardware_sensor", "sensor"),
    [
        ("Left Foot", "LEFT_FOOT", "left_foot"),
        ("Right Foot", "RIGHT_FOOT", "right_foot"),
    ],
)
@pytest.mark.asyncio
async def test_paw_touch_requires_two_touches_within_confirmation_window(
    tmp_path,
    handler: str,
    hardware_sensor: str,
    sensor: str,
) -> None:
    settings = monitor_settings(tmp_path)
    settings.touch_event_log_path.touch()
    product = FakeProduct()
    now = [100.0]
    monitor = TouchEventMonitor(
        settings,
        product,  # type: ignore[arg-type]
        clock=lambda: now[0],
    )
    await monitor.start()
    try:
        with settings.touch_event_log_path.open("a", encoding="utf-8") as stream:
            stream.write(
                f"[{handler} Touch Handler] {hardware_sensor}_SHORT_TOUCH detected\n"
            )
        assert await monitor.poll_once() == 0
        assert product.events == []

        now[0] += 1.0
        with settings.touch_event_log_path.open("a", encoding="utf-8") as stream:
            stream.write(
                f"[{handler} Touch Handler] {hardware_sensor}_SHORT_TOUCH detected\n"
            )
        assert await monitor.poll_once() == 1
    finally:
        await monitor.close()

    assert len(product.events) == 1
    assert product.events[0]["metadata"]["sensor"] == sensor


@pytest.mark.asyncio
async def test_paw_confirmation_is_separate_per_paw_and_expires(tmp_path) -> None:
    settings = monitor_settings(
        tmp_path,
        paw_touch_confirmation_window_seconds=2.0,
    )
    settings.touch_event_log_path.touch()
    product = FakeProduct()
    now = [100.0]
    monitor = TouchEventMonitor(
        settings,
        product,  # type: ignore[arg-type]
        clock=lambda: now[0],
    )
    await monitor.start()
    try:
        with settings.touch_event_log_path.open("a", encoding="utf-8") as stream:
            stream.write(
                "[Left Foot Touch Handler] LEFT_FOOT_SHORT_TOUCH detected\n"
            )
            stream.write(
                "[Right Foot Touch Handler] RIGHT_FOOT_SHORT_TOUCH detected\n"
            )
        assert await monitor.poll_once() == 0

        now[0] += 2.1
        with settings.touch_event_log_path.open("a", encoding="utf-8") as stream:
            stream.write(
                "[Left Foot Touch Handler] LEFT_FOOT_SHORT_TOUCH detected\n"
            )
        assert await monitor.poll_once() == 0

        now[0] += 0.5
        with settings.touch_event_log_path.open("a", encoding="utf-8") as stream:
            stream.write(
                "[Left Foot Touch Handler] LEFT_FOOT_LONG_TOUCH detected\n"
            )
        assert await monitor.poll_once() == 1
    finally:
        await monitor.close()

    assert len(product.events) == 1
    assert product.events[0]["metadata"]["sensor"] == "left_foot"


def test_touch_sensor_mapping_is_selected_by_device_serial() -> None:
    current_map, current_profile = touch_sensor_map_for_serial("7c2b63fd4a128")
    legacy_map, legacy_profile = touch_sensor_map_for_serial("7c2b63fd4a138")

    assert current_profile == "identity"
    assert current_map == {
        "head": "head",
        "nose": "nose",
        "back": "back",
        "left_foot": "left_foot",
        "right_foot": "right_foot",
    }
    assert legacy_profile == "legacy_swapped"
    assert legacy_map == {
        "head": "nose",
        "nose": "head",
        "back": "left_foot",
        "left_foot": "back",
        "right_foot": "right_foot",
    }
