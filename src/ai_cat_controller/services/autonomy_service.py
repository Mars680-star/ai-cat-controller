"""Lifecycle wrapper for running safe autonomy inside the FastAPI process.

The autonomy worker intentionally remains synchronous and continues to submit
actions through the loopback HTTP API.  A dedicated thread keeps its bounded
HTTP waits and local playback away from the asyncio event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Mapping

from ai_cat_controller.autonomy import (
    AutonomyConfig,
    AutonomyWorker,
    LocalControllerClient,
)
from ai_cat_controller.core.config import Settings

LOGGER = logging.getLogger(__name__)
DEFAULT_RESTART_DELAY_SECONDS = 5.0
DEFAULT_STOP_TIMEOUT_SECONDS = 6.0


class InProcessAutonomyService:
    """Own one restartable autonomy thread for a single FastAPI process."""

    def __init__(
        self,
        worker: AutonomyWorker,
        *,
        restart_delay_seconds: float = DEFAULT_RESTART_DELAY_SECONDS,
        stop_timeout_seconds: float = DEFAULT_STOP_TIMEOUT_SECONDS,
    ) -> None:
        if restart_delay_seconds < 0.0:
            raise ValueError("restart_delay_seconds must not be negative")
        if stop_timeout_seconds <= 0.0:
            raise ValueError("stop_timeout_seconds must be positive")
        self._worker = worker
        self._restart_delay_seconds = restart_delay_seconds
        self._stop_timeout_seconds = stop_timeout_seconds
        self._shutdown_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error: str | None = None

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        environ: Mapping[str, str] | None = None,
    ) -> "InProcessAutonomyService":
        source = dict(os.environ if environ is None else environ)
        # ``create_app`` also accepts an explicit Settings object in tests and
        # embedded launchers.  Keep the loopback client aligned with the actual
        # app even when those values differ from the ambient environment.
        source.update(
            {
                "AI_CAT_API_PORT": str(settings.api_port),
                "AI_CAT_API_KEY_ENABLED": str(settings.api_key_enabled).lower(),
                "AI_CAT_API_KEY": settings.api_key.get_secret_value(),
                "AI_CAT_ENABLE_MOTION": str(settings.enable_motion).lower(),
                "AI_CAT_HAS_TAIL": str(settings.has_tail).lower(),
                "AI_CAT_ENABLE_TAIL_MOTION": str(settings.enable_tail_motion).lower(),
            }
        )
        # Explicit app Settings also decide whether IPC coordination is enabled;
        # never let an ambient path silently override an explicit None.
        if settings.process_gate_path is None:
            source.pop("AI_CAT_PROCESS_GATE_PATH", None)
        else:
            source["AI_CAT_PROCESS_GATE_PATH"] = str(settings.process_gate_path)
        config = AutonomyConfig.from_env(source)
        return cls(AutonomyWorker(config, LocalControllerClient(config)))

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def start(self) -> None:
        if self.running:
            return
        if self._thread is not None or self._shutdown_event.is_set():
            raise RuntimeError("in-process autonomy service cannot be restarted")

        self._worker.validate_startup()
        thread = threading.Thread(
            target=self._run_with_restart,
            name="ai-cat-autonomy",
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def _run_with_restart(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                self._worker.run_forever()
            except Exception as exc:
                self._last_error = str(exc)
                LOGGER.exception("in-process autonomy worker failed")
            else:
                if self._shutdown_event.is_set():
                    break
                self._last_error = "autonomy worker exited unexpectedly"
                LOGGER.error(self._last_error)

            if self._shutdown_event.wait(self._restart_delay_seconds):
                break
            LOGGER.info("restarting in-process autonomy worker")

    async def close(self) -> None:
        self._shutdown_event.set()
        self._worker.stop()
        thread = self._thread
        if thread is not None:
            await asyncio.to_thread(thread.join, self._stop_timeout_seconds)
            if thread.is_alive():
                raise TimeoutError("in-process autonomy worker did not stop in time")
            self._thread = None
        close_worker = getattr(self._worker, "close", None)
        if close_worker is not None:
            close_worker()

