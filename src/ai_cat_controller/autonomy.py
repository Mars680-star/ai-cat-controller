"""Safe K1 autonomous behavior and answer-time micro-motion worker.

Maintenance notes:
- Tune intervals and probabilities through the documented environment values.
- Submit all motion through the FastAPI motion endpoint; never drive GPIO here.
- This module runs as ``toy_motor.service`` from the deployed project copy.
"""

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
from pathlib import Path
from typing import Any

from ai_cat_controller.domain.personalities import PERSONALITIES
from ai_cat_controller.local_speech import (
    DEFAULT_ASSET_ROOT,
    DEFAULT_MARKER_PATH,
    DEFAULT_PLAYER_PATH,
    LocalPhrasePlayer,
    LocalSpeechError,
)

LOGGER = logging.getLogger("ai_cat_controller.autonomy")
MAX_RESPONSE_BYTES = 65_536
SAFE_ACTIONS = ("head/nod", "head/shake")
CONVERSATION_TAIL_ACTION = "tail/wag"
AUTONOMY_READY_STATES = frozenset({"ready", "interrupted"})
AUTONOMY_OFFLINE_STATES = frozenset({"offline", "unavailable"})
CONVERSATION_MOTION_STATE = "answering"
SHORT_PHRASES = tuple(
    dict.fromkeys(
        phrase
        for personality in PERSONALITIES
        for phrase in personality.proactive_phrases
    )
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
    conversation_motion_enabled: bool = False
    conversation_motion_probability: float = 1.0
    conversation_motion_delay_seconds: float = 1.0
    conversation_poll_interval_seconds: float = 0.5
    conversation_head_minimum_interval_seconds: float = 7.0
    conversation_head_maximum_interval_seconds: float = 10.0
    conversation_tail_enabled: bool = False
    conversation_tail_delay_seconds: float = 2.0
    conversation_tail_minimum_interval_seconds: float = 3.0
    conversation_tail_maximum_interval_seconds: float = 4.5
    cloud_speech_enabled: bool = False
    local_speech_enabled: bool = False
    local_speech_asset_root: Path = DEFAULT_ASSET_ROOT
    local_speech_marker_path: Path = DEFAULT_MARKER_PATH
    local_speech_player_path: Path = DEFAULT_PLAYER_PATH
    local_speech_motion_settle_seconds: float = 2.2

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
            180.0,
            15.0,
            3600.0,
        )
        maximum_interval = _bounded_float(
            source,
            "AI_CAT_AUTONOMY_MAX_INTERVAL_SECONDS",
            180.0,
            15.0,
            3600.0,
        )
        if maximum_interval < minimum_interval:
            raise ValueError(
                "AI_CAT_AUTONOMY_MAX_INTERVAL_SECONDS must be greater than or equal "
                "to AI_CAT_AUTONOMY_MIN_INTERVAL_SECONDS"
            )
        cloud_speech_enabled = (
            source.get("AI_CAT_AUTONOMY_CLOUD_SPEECH_ENABLED", "false").lower()
            == "true"
        )
        local_speech_enabled = (
            source.get("AI_CAT_AUTONOMY_LOCAL_SPEECH_ENABLED", "false").lower()
            == "true"
        )
        if cloud_speech_enabled and local_speech_enabled:
            raise ValueError("cloud and local autonomy speech cannot both be enabled")
        conversation_head_minimum_interval = _bounded_float(
            source,
            "AI_CAT_CONVERSATION_HEAD_MIN_INTERVAL_SECONDS",
            7.0,
            3.0,
            60.0,
        )
        conversation_head_maximum_interval = _bounded_float(
            source,
            "AI_CAT_CONVERSATION_HEAD_MAX_INTERVAL_SECONDS",
            10.0,
            3.0,
            60.0,
        )
        if conversation_head_maximum_interval < conversation_head_minimum_interval:
            raise ValueError(
                "AI_CAT_CONVERSATION_HEAD_MAX_INTERVAL_SECONDS must be greater "
                "than or equal to AI_CAT_CONVERSATION_HEAD_MIN_INTERVAL_SECONDS"
            )
        conversation_tail_minimum_interval = _bounded_float(
            source,
            "AI_CAT_CONVERSATION_TAIL_MIN_INTERVAL_SECONDS",
            3.0,
            1.5,
            60.0,
        )
        conversation_tail_maximum_interval = _bounded_float(
            source,
            "AI_CAT_CONVERSATION_TAIL_MAX_INTERVAL_SECONDS",
            4.5,
            1.5,
            60.0,
        )
        if conversation_tail_maximum_interval < conversation_tail_minimum_interval:
            raise ValueError(
                "AI_CAT_CONVERSATION_TAIL_MAX_INTERVAL_SECONDS must be greater "
                "than or equal to AI_CAT_CONVERSATION_TAIL_MIN_INTERVAL_SECONDS"
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
                1.0,
                0.0,
                1.0,
            ),
            conversation_motion_enabled=(
                source.get("AI_CAT_CONVERSATION_MOTION_ENABLED", "false").lower()
                == "true"
            ),
            conversation_motion_probability=_bounded_float(
                source,
                "AI_CAT_CONVERSATION_MOTION_PROBABILITY",
                1.0,
                0.0,
                1.0,
            ),
            conversation_motion_delay_seconds=_bounded_float(
                source,
                "AI_CAT_CONVERSATION_MOTION_DELAY_SECONDS",
                1.0,
                0.0,
                5.0,
            ),
            conversation_poll_interval_seconds=_bounded_float(
                source,
                "AI_CAT_CONVERSATION_POLL_INTERVAL_SECONDS",
                0.5,
                0.2,
                5.0,
            ),
            conversation_head_minimum_interval_seconds=(
                conversation_head_minimum_interval
            ),
            conversation_head_maximum_interval_seconds=(
                conversation_head_maximum_interval
            ),
            conversation_tail_enabled=(
                source.get("AI_CAT_CONVERSATION_TAIL_ENABLED", "false").lower()
                == "true"
            ),
            conversation_tail_delay_seconds=_bounded_float(
                source,
                "AI_CAT_CONVERSATION_TAIL_DELAY_SECONDS",
                2.0,
                0.5,
                10.0,
            ),
            conversation_tail_minimum_interval_seconds=(
                conversation_tail_minimum_interval
            ),
            conversation_tail_maximum_interval_seconds=(
                conversation_tail_maximum_interval
            ),
            cloud_speech_enabled=cloud_speech_enabled,
            local_speech_enabled=local_speech_enabled,
            local_speech_asset_root=Path(
                source.get(
                    "AI_CAT_AUTONOMY_LOCAL_SPEECH_ASSET_ROOT",
                    str(DEFAULT_ASSET_ROOT),
                )
            ),
            local_speech_marker_path=Path(
                source.get(
                    "AI_CAT_AUTONOMY_LOCAL_SPEECH_MARKER_PATH",
                    str(DEFAULT_MARKER_PATH),
                )
            ),
            local_speech_player_path=Path(
                source.get(
                    "AI_CAT_AUTONOMY_LOCAL_SPEECH_PLAYER_PATH",
                    str(DEFAULT_PLAYER_PATH),
                )
            ),
            local_speech_motion_settle_seconds=_bounded_float(
                source,
                "AI_CAT_AUTONOMY_LOCAL_SPEECH_MOTION_SETTLE_SECONDS",
                2.2,
                0.0,
                10.0,
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

    def personality_profile(self) -> dict[str, Any]:
        return self._request("GET", "/personality/runtime")

    def start_head_action(
        self,
        action: str,
        request_id: str,
        *,
        intensity: float = 0.35,
        duration_ms: int = 1800,
    ) -> None:
        if action not in SAFE_ACTIONS:
            raise ValueError("autonomy action is not allowlisted")
        self._request(
            "POST",
            f"/motion/{action}",
            {
                "intensity": intensity,
                "duration_ms": duration_ms,
                "request_id": request_id,
            },
        )

    def start_tail_action(
        self,
        request_id: str,
        *,
        intensity: float = 0.35,
        duration_ms: int = 600,
    ) -> None:
        self._request(
            "POST",
            f"/motion/{CONVERSATION_TAIL_ACTION}",
            {
                "intensity": intensity,
                "duration_ms": duration_ms,
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
        local_player: LocalPhrasePlayer | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._random = random_source or random.Random()
        self._stop_event = stop_event or threading.Event()
        self._local_player = local_player or LocalPhrasePlayer(
            asset_root=config.local_speech_asset_root,
            marker_path=config.local_speech_marker_path,
            player_path=config.local_speech_player_path,
        )
        self._conversation_token: tuple[int, int] | None = None
        self._next_conversation_head_at = 0.0
        self._next_conversation_tail_at = 0.0

    def stop(self) -> None:
        self._stop_event.set()

    def validate_startup(self) -> int:
        if not self._config.local_speech_enabled:
            return 0
        paths = self._local_player.validate_personality_assets()
        LOGGER.info("validated %s local autonomy phrase assets", len(paths))
        return len(paths)

    def run_once(self) -> dict[str, Any]:
        status = self._client.dialog_status()
        dialog_state = str(status.get("state", "unavailable"))
        dialog_is_offline = dialog_state in AUTONOMY_OFFLINE_STATES
        if (
            status.get("session_active")
            or (
                not dialog_is_offline
                and (
                    status.get("stale")
                    or dialog_state not in AUTONOMY_READY_STATES
                )
            )
        ):
            result = {
                "executed": False,
                "reason": "dialog_busy",
                "dialog_state": dialog_state,
            }
            LOGGER.info("autonomous behavior skipped: %s", result)
            return result

        personality = self._client.personality_profile()
        if not personality.get("active") or (
            not personality.get("native_applied") and not dialog_is_offline
        ):
            result = {
                "executed": False,
                "reason": "personality_not_ready",
                "personality_id": personality.get("personality_id"),
            }
            LOGGER.info("autonomous behavior skipped: %s", result)
            return result
        autonomy = personality.get("autonomy")
        action_weights = (
            autonomy.get("action_weights") if isinstance(autonomy, dict) else None
        )
        if not isinstance(action_weights, dict):
            raise ControllerApiError("personality has no autonomy action profile")
        actions: list[str] = []
        weights: list[float] = []
        for action, weight in action_weights.items():
            if (
                action in SAFE_ACTIONS
                and isinstance(weight, (int, float))
                and weight > 0
            ):
                actions.append(action)
                weights.append(float(weight))
        if not actions:
            raise ControllerApiError("personality has no safe autonomous action")

        action = self._random.choices(actions, weights=weights, k=1)[0]
        event_id = f"auto-{int(time.time())}-{uuid.uuid4().hex[:10]}"
        self._client.start_head_action(action, f"{event_id}-motion")

        phrase: str | None = None
        if (
            (
                self._config.cloud_speech_enabled
                or self._config.local_speech_enabled
            )
            and self._random.random() < self._config.phrase_probability
        ):
            profile_phrases = autonomy.get("phrases")
            phrases = (
                [item for item in profile_phrases if item in SHORT_PHRASES]
                if isinstance(profile_phrases, list)
                else []
            )
            phrase = self._random.choice(phrases) if phrases else None
        if phrase is not None:
            try:
                if self._config.local_speech_enabled:
                    personality_id = str(personality.get("personality_id", ""))
                    if self._stop_event.wait(
                        self._config.local_speech_motion_settle_seconds
                    ):
                        phrase = None
                    else:
                        self._local_player.play(personality_id, phrase)
                else:
                    self._client.speak(phrase, f"{event_id}-speech")
            except (ControllerApiError, LocalSpeechError) as exc:
                LOGGER.warning("autonomous phrase failed after motion start: %s", exc)
                phrase = None

        result = {"executed": True, "action": action, "phrase": phrase}
        LOGGER.info("autonomous behavior submitted: %s", result)
        return result

    def run_conversation_motion_once(self) -> dict[str, Any]:
        status = self._client.dialog_status()
        state = str(status.get("state", "unavailable"))
        if (
            not self._config.conversation_motion_enabled
            or state != CONVERSATION_MOTION_STATE
            or not status.get("session_active")
            or not status.get("can_interrupt")
            or status.get("stale")
        ):
            self._conversation_token = None
            self._next_conversation_head_at = 0.0
            self._next_conversation_tail_at = 0.0
            return {"executed": False, "reason": "not_answering"}

        sequence = int(status.get("sequence", 0) or 0)
        updated_at_ms = int(status.get("updated_at_ms", 0) or 0)
        token = (sequence, updated_at_ms)
        now = time.monotonic()
        if token != self._conversation_token:
            self._conversation_token = token
            self._next_conversation_head_at = (
                now + self._config.conversation_motion_delay_seconds
            )
            self._next_conversation_tail_at = (
                now + self._config.conversation_tail_delay_seconds
            )

        head_due = now >= self._next_conversation_head_at
        tail_due = (
            self._config.conversation_tail_enabled
            and now >= self._next_conversation_tail_at
        )
        if not head_due and not tail_due:
            return {"executed": False, "reason": "waiting_for_interval"}

        if self._random.random() >= self._config.conversation_motion_probability:
            if tail_due:
                self._schedule_next_conversation_tail(now)
            else:
                self._schedule_next_conversation_head(now)
            return {"executed": False, "reason": "probability_skip"}

        if tail_due:
            event_id = f"dialog-tail-{int(time.time())}-{uuid.uuid4().hex[:10]}"
            self._client.start_tail_action(
                event_id,
                intensity=0.35,
                duration_ms=600,
            )
            self._schedule_next_conversation_tail(now)
            result = {
                "executed": True,
                "action": CONVERSATION_TAIL_ACTION,
                "token": token,
            }
            LOGGER.info("conversation motion submitted: %s", result)
            return result

        personality = self._client.personality_profile()
        autonomy = personality.get("autonomy")
        action_weights = (
            autonomy.get("action_weights") if isinstance(autonomy, dict) else None
        )
        if not personality.get("active") or not isinstance(action_weights, dict):
            self._schedule_next_conversation_head(now)
            return {"executed": False, "reason": "personality_not_ready"}

        actions: list[str] = []
        weights: list[float] = []
        for action, weight in action_weights.items():
            if (
                action in SAFE_ACTIONS
                and isinstance(weight, (int, float))
                and weight > 0
            ):
                actions.append(action)
                weights.append(float(weight))
        if not actions:
            self._schedule_next_conversation_head(now)
            return {"executed": False, "reason": "no_safe_action"}

        action = self._random.choices(actions, weights=weights, k=1)[0]
        event_id = f"dialog-motion-{int(time.time())}-{uuid.uuid4().hex[:10]}"
        self._client.start_head_action(
            action,
            event_id,
            intensity=0.2,
            duration_ms=900,
        )
        self._schedule_next_conversation_head(now)
        result = {"executed": True, "action": action, "token": token}
        LOGGER.info("conversation motion submitted: %s", result)
        return result

    def _schedule_next_conversation_head(self, now: float) -> None:
        self._next_conversation_head_at = now + self._random.uniform(
            self._config.conversation_head_minimum_interval_seconds,
            self._config.conversation_head_maximum_interval_seconds,
        )

    def _schedule_next_conversation_tail(self, now: float) -> None:
        self._next_conversation_tail_at = now + self._random.uniform(
            self._config.conversation_tail_minimum_interval_seconds,
            self._config.conversation_tail_maximum_interval_seconds,
        )

    def run_forever(self) -> None:
        LOGGER.info(
            "safe autonomy started; initial_delay=%.1fs interval=%.1f..%.1fs "
            "local_speech=%s cloud_speech=%s phrase_probability=%.2f "
            "conversation_motion=%s conversation_tail=%s",
            self._config.initial_delay_seconds,
            self._config.minimum_interval_seconds,
            self._config.maximum_interval_seconds,
            self._config.local_speech_enabled,
            self._config.cloud_speech_enabled,
            self._config.phrase_probability,
            self._config.conversation_motion_enabled,
            self._config.conversation_tail_enabled,
        )
        next_autonomy_at = time.monotonic() + self._config.initial_delay_seconds
        while not self._stop_event.is_set():
            if self._config.conversation_motion_enabled:
                try:
                    self.run_conversation_motion_once()
                except ControllerApiError as exc:
                    LOGGER.warning("conversation motion skipped: %s", exc)
                except Exception:
                    LOGGER.exception("unexpected conversation motion failure")

            now = time.monotonic()
            if now >= next_autonomy_at:
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
                next_autonomy_at = time.monotonic() + delay
                LOGGER.info("next autonomous behavior in %.1f seconds", delay)

            wait_seconds = max(next_autonomy_at - time.monotonic(), 0.05)
            if self._config.conversation_motion_enabled:
                wait_seconds = min(
                    wait_seconds,
                    self._config.conversation_poll_interval_seconds,
                )
            self._stop_event.wait(wait_seconds)


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
    worker.validate_startup()
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
