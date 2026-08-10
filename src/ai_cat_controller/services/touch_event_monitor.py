"""Import physical K1 touch events without taking ownership of its GPIO lines."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from pathlib import Path

from ai_cat_controller.core.config import Settings
from ai_cat_controller.services.product_mock_service import ProductMockService

LOGGER = logging.getLogger(__name__)
MAX_READ_BYTES = 64 * 1024
TOUCH_PATTERN = re.compile(
    r"^\[(?:Head|Back|Left Foot|Right Foot|Nose) Touch Handler\]\s+"
    r"(?P<sensor>HEAD|BACK|LEFT_FOOT|RIGHT_FOOT|NOSE)_"
    r"(?P<gesture>SHORT|LONG)_TOUCH detected\s*$"
)


class TouchEventMonitor:
    """Tail the vendor touch log and convert new events into intimacy records."""

    def __init__(self, settings: Settings, product: ProductMockService) -> None:
        self._settings = settings
        self._product = product
        self._identity: tuple[int, int] | None = None
        self._epoch = ""
        self._offset = 0
        self._pending_events: list[tuple[str, int, str]] = []
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await asyncio.to_thread(self._baseline_at_end)
        self._task = asyncio.create_task(self._run(), name="k1-touch-event-monitor")

    def _baseline_at_end(self) -> None:
        try:
            status = self._settings.touch_event_log_path.stat()
        except FileNotFoundError:
            return
        except OSError as exc:
            LOGGER.warning("cannot inspect K1 touch log: %s", exc)
            return
        self._identity = (status.st_dev, status.st_ino)
        self._epoch = (
            f"{status.st_dev}:{status.st_ino}:{status.st_ctime_ns}:{status.st_size}"
        )
        self._offset = status.st_size

    def _read_serial(self) -> str:
        raw = self._settings.device_serial_path.read_bytes()
        serial = raw.replace(b"\0", b"").decode("ascii").strip()
        if not serial or len(serial) > 128:
            raise ValueError("invalid K1 device serial")
        return serial

    def _read_new_lines(self) -> list[tuple[str, int, str]]:
        path: Path = self._settings.touch_event_log_path
        try:
            with path.open("rb") as stream:
                file_status = os.fstat(stream.fileno())
                identity = (file_status.st_dev, file_status.st_ino)
                if self._identity != identity or file_status.st_size < self._offset:
                    self._identity = identity
                    self._epoch = (
                        f"{file_status.st_dev}:{file_status.st_ino}:"
                        f"{file_status.st_ctime_ns}:{file_status.st_size}"
                    )
                    self._offset = 0
                stream.seek(self._offset)
                results: list[tuple[str, int, str]] = []
                consumed = 0
                while consumed < MAX_READ_BYTES:
                    line_offset = stream.tell()
                    raw = stream.readline(MAX_READ_BYTES - consumed)
                    if not raw:
                        break
                    if not raw.endswith(b"\n"):
                        stream.seek(line_offset)
                        break
                    consumed += len(raw)
                    self._offset = stream.tell()
                    results.append(
                        (
                            self._epoch,
                            line_offset,
                            raw.decode("utf-8", errors="replace").rstrip("\r\n"),
                        )
                    )
                return results
        except FileNotFoundError:
            return []
        except OSError as exc:
            LOGGER.warning("cannot read K1 touch log: %s", exc)
            return []

    async def poll_once(self) -> int:
        try:
            serial = await asyncio.to_thread(self._read_serial)
        except (OSError, UnicodeError, ValueError) as exc:
            LOGGER.warning("cannot read K1 serial for touch event: %s", exc)
            return 0

        lines = self._pending_events + await asyncio.to_thread(self._read_new_lines)
        self._pending_events = []
        imported = 0
        for epoch, offset, line in lines:
            match = TOUCH_PATTERN.match(line)
            if match is None:
                continue
            digest = hashlib.sha256(
                f"{serial}:{epoch}:{offset}:{line}".encode()
            ).hexdigest()[:32]
            try:
                event = await self._product.add_device_touch(
                    device_serial=serial,
                    request_id=f"k1-touch-{digest}",
                    metadata={
                        "source": "k1_touch_log",
                        "sensor": match.group("sensor").lower(),
                        "gesture": match.group("gesture").lower(),
                    },
                )
            except Exception:
                LOGGER.exception("failed to import K1 touch event")
                self._pending_events.append((epoch, offset, line))
                continue
            if event is not None:
                imported += 1
        return imported

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._settings.touch_monitor_poll_seconds)
            await self.poll_once()

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
