"""Safe, low-frequency autonomous behavior for the K1 prototype."""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import signal
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

LOGGER = logging.getLogger("ai_cat_controller.autonomy")
MAX_RESPONSE_BYTES = 65_536
SAFE_ACTIONS = ("head/nod", "head/shake")
AUTONOMY_READY_STATES = frozenset({"ready", "interrupted"})
SHORT_PHRASES = (
    "我在这里呀。",
    "要不要陪我玩一会儿？",
    "今天也要开心哦。",
    "记得休息一下呀。",
    "我刚刚想起你啦。",
)


class ControllerApiError(RuntimeError):
    """The local FastAPI controller rejected or failed a request."""


def _bounded_float(
    source: Mapping[str, str],
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw_value = source.get(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class AutonomyConfig:
    api_port: int
    api_key: str
    initial_delay_seconds: float
    minimum_interval_seconds: float
    maximum_interval_seconds: float
    phrase_probability: float

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "AutonomyConfig":
        source = os.environ if environ is None else environ
        try:
            api_port = int(source.get("AI_CAT_API_PORT", "8000"))
        except ValueError as exc:
            raise ValueError("AI_CAT_API_PORT must be an integer") from exc
        if not 1 <= api_port <= 65_535:
            raise ValueError("AI_CAT_API_PORT must be between 1 and 65535")
        api_key = source.get("AI_CAT_API_KEY", "")
        if source.get("AI_CAT_API_KEY_ENABLED", "false").lower() == "true" and not api_key:
            raise ValueError("AI_CAT_API_KEY is required")
        initial_delay = _bounded_float(
            source,
            "AI_CAT_AUTONOMY_INITIAL_DELAY_SECONDS",
            45.0,
            0.0,
            600.0,
        )
        minimum_interval = _bounded_float(
            source,
            "AI_CAT_AUTONOMY_MIN_INTERVAL_SECONDS",
            60.0,
            15.0,
            3600.0,
        )
        maximum_interval = _bounded_float(
            source,
            "AI_CAT_AUTONOMY_MAX_INTERVAL_SECONDS",
            120.0,
            15.0,
            3600.0,
        )
        if maximum_interval < minimum_interval:
            raise ValueError(
                "AI_CAT_AUTONOMY_MAX_INTERVAL_SECONDS must be greater than or equal "
                "to AI_CAT_AUTONOMY_MIN_INTERVAL_SECONDS"
            )
        return cls(
            api_port=api_port,
            api_key=api_key,
            initial_delay_seconds=initial_delay,
            minimum_interval_seconds=minimum_interval,
            maximum_interval_seconds=maximum_interval,
            phrase_probability=_bounded_float(
                source,
                "AI_CAT_AUTONOMY_PHRASE_PROBABILITY",
                0.7,
                0.0,
                1.0,
            ),
        )


class LocalControllerClient:
    """Small bounded HTTP client restricted to the loopback controller."""

    def __init__(self, config: AutonomyConfig) -> None:
        self._base_url = f"http://127.0.0.1:{config.api_port}/api/v1"
        self._api_key = config.api_key

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = None
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        if payload is not None:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=5.0) as response:
                raw_response = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            error_body = exc.read(2048).decode("utf-8", errors="replace")
            raise ControllerApiError(
                f"controller returned HTTP {exc.code}: {error_body}"
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise ControllerApiError(f"controller request failed: {exc}") from exc
        if len(raw_response) > MAX_RESPONSE_BYTES:
            raise ControllerApiError("controller response exceeded the size limit")
        try:
            envelope = json.loads(raw_response.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ControllerApiError("controller returned invalid JSON") from exc
        data = envelope.get("data") if isinstance(envelope, dict) else None
        if not isinstance(data, dict):
            raise ControllerApiError("controller response is missing data")
        return data

    def dialog_status(self) -> dict[str, Any]:
        return self._request("GET", "/dialog/status")

    def start_head_action(self, action: str, request_id: str) -> None:
        if action not in SAFE_ACTIONS:
            raise ValueError("autonomy action is not allowlisted")
        self._request(
            "POST",
            f"/motion/{action}",
            {
                "intensity": 0.35,
                "duration_ms": 1800,
                "request_id": request_id,
            },
        )

    def speak(self, content: str, request_id: str) -> None:
        if content not in SHORT_PHRASES:
            raise ValueError("autonomy phrase is not allowlisted")
        self._request(
            "POST",
            "/dialog/speak",
            {"content": content, "request_id": request_id},
        )


class AutonomyWorker:
    def __init__(
        self,
        config: AutonomyConfig,
        client: LocalControllerClient,
        *,
        random_source: random.Random | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._random = random_source or random.Random()
        self._stop_event = stop_event or threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run_once(self) -> dict[str, Any]:
        status = self._client.dialog_status()
        if (
            status.get("stale")
            or status.get("session_active")
            or status.get("state") not in AUTONOMY_READY_STATES
        ):
            result = {
                "executed": False,
                "reason": "dialog_busy",
                "dialog_state": status.get("state", "unknown"),
            }
            LOGGER.info("autonomous behavior skipped: %s", result)
            return result

        action = self._random.choice(SAFE_ACTIONS)
        event_id = f"auto-{int(time.time())}-{uuid.uuid4().hex[:10]}"
        self._client.start_head_action(action, f"{event_id}-motion")

        phrase: str | None = None
        if self._random.random() < self._config.phrase_probability:
            phrase = self._random.choice(SHORT_PHRASES)
            try:
                self._client.speak(phrase, f"{event_id}-speech")
            except ControllerApiError as exc:
                LOGGER.info("autonomous phrase skipped after motion start: %s", exc)
                phrase = None

        result = {"executed": True, "action": action, "phrase": phrase}
        LOGGER.info("autonomous behavior submitted: %s", result)
        return result

    def run_forever(self) -> None:
        LOGGER.info(
            "safe autonomy started; initial_delay=%.1fs interval=%.1f..%.1fs",
            self._config.initial_delay_seconds,
            self._config.minimum_interval_seconds,
            self._config.maximum_interval_seconds,
        )
        if self._stop_event.wait(self._config.initial_delay_seconds):
            return
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except ControllerApiError as exc:
                LOGGER.warning("autonomous behavior skipped: %s", exc)
            except Exception:
                LOGGER.exception("unexpected autonomous behavior failure")
            delay = self._random.uniform(
                self._config.minimum_interval_seconds,
                self._config.maximum_interval_seconds,
            )
            LOGGER.info("next autonomous behavior in %.1f seconds", delay)
            self._stop_event.wait(delay)


def main() -> None:
    parser = argparse.ArgumentParser(description="AI cat safe autonomous behavior")
    parser.add_argument(
        "--once",
        action="store_true",
        help="submit at most one behavior immediately and exit",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=os.environ.get("AI_CAT_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = AutonomyConfig.from_env()
    worker = AutonomyWorker(config, LocalControllerClient(config))
    if args.once:
        worker.run_once()
        return

    def request_stop(signum: int, frame: object) -> None:
        del signum, frame
        worker.stop()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    worker.run_forever()


if __name__ == "__main__":
    main()
