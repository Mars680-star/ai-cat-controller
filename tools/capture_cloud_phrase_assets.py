#!/usr/bin/env python3
"""Capture fixed local speech assets with the current Volcengine console voice."""

from __future__ import annotations

import argparse
import array
import hashlib
import json
import os
import signal
import subprocess
import time
import urllib.request
import wave
from pathlib import Path
from typing import Any

from ai_cat_controller.domain.personalities import (
    PERSONALITIES,
    VOLCENGINE_CONSOLE_VOICE_ID,
    VOLCENGINE_CONSOLE_VOICE_NAME,
)
from ai_cat_controller.local_speech import (
    DEFAULT_ASSET_ROOT,
    TOUCH_PHRASES,
    phrase_asset_path,
    touch_phrase_asset_path,
)

ENV_PATH = Path("/etc/ai-cat-controller.env")
RUNTIME_PATH = Path("/var/lib/ai-cat-controller/personality-runtime.json")
STATUS_PATH = Path("/run/ai-cat/dialog-status.json")
CAPTURE_SOURCE = "ai_cat_echo_cancel_sink.monitor"
CLOUD_SERVICE = "volc-conv-ai.service"
PARECORD = "/usr/bin/parecord"
SYSTEMCTL = "/usr/bin/systemctl"
API_URL = "http://127.0.0.1:8000/api/v1/dialog/speak"
CAPTURE_TIMEOUT_SECONDS = 30.0
CAPTURE_FALLBACK_SECONDS = 8.0
CAPTURE_ATTEMPTS = 2


def read_env_value(path: Path, name: str) -> str:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip().strip("'\"")
    raise RuntimeError(f"{name} is missing from {path}")


def write_runtime(payload: dict[str, Any]) -> None:
    temporary = RUNTIME_PATH.with_name(RUNTIME_PATH.name + ".capture.tmp")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, RUNTIME_PATH)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def capture_runtime(original: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(original, ensure_ascii=False))
    revision = "capture-" + hashlib.sha256(
        b"volcengine-console-voice"
    ).hexdigest()[:20]
    payload.update(
        {
            "voice_name": VOLCENGINE_CONSOLE_VOICE_NAME,
            "voice_type": VOLCENGINE_CONSOLE_VOICE_ID,
            "voice_source": "volcengine_console",
            "revision": revision,
        }
    )
    update = payload["session_update"]
    update["event_id"] = f"event_{revision}"
    update["session"]["config"].pop("TTSConfig", None)
    return payload


def read_status() -> dict[str, Any]:
    try:
        payload = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def wait_for_runtime(revision: str) -> None:
    deadline = time.monotonic() + CAPTURE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = read_status()
        if (
            status.get("personality_revision") == revision
            and status.get("state") == "ready"
            and not status.get("session_active")
        ):
            return
        time.sleep(0.2)
    raise RuntimeError(f"cloud did not apply personality revision {revision}")


def post_speak(api_key: str, phrase: str, request_id: str) -> None:
    body = json.dumps(
        {"content": phrase, "request_id": request_id},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10.0) as response:
        if response.status != 200:
            raise RuntimeError(f"speak API returned HTTP {response.status}")


def stop_recorder(recorder: subprocess.Popen[bytes]) -> bytes:
    if recorder.poll() is None:
        recorder.send_signal(signal.SIGINT)
    try:
        _, stderr = recorder.communicate(timeout=5.0)
    except subprocess.TimeoutExpired:
        recorder.kill()
        _, stderr = recorder.communicate(timeout=2.0)
    return stderr


def trim_capture(source: Path, target: Path) -> float:
    with wave.open(str(source), "rb") as stream:
        channels = stream.getnchannels()
        sample_width = stream.getsampwidth()
        rate = stream.getframerate()
        frames = stream.readframes(stream.getnframes())
    if sample_width != 2 or channels != 2 or rate != 48_000:
        raise RuntimeError("captured WAV format is not s16le stereo 48 kHz")
    samples = array.array("h")
    samples.frombytes(frames)
    frame_count = len(samples) // channels
    window = max(1, rate // 100)
    active: list[tuple[int, int]] = []
    for start in range(0, frame_count, window):
        end = min(frame_count, start + window)
        peak = max(
            (
                abs(samples[index])
                for index in range(start * channels, end * channels)
            ),
            default=0,
        )
        if peak >= 180:
            active.append((start, end))
    if not active:
        raise RuntimeError("captured WAV contains no TTS audio")
    padding = rate // 5
    start = max(0, active[0][0] - padding)
    end = min(frame_count, active[-1][1] + padding)
    trimmed = samples[start * channels : end * channels]
    temporary = target.with_suffix(".wav.tmp")
    with wave.open(str(temporary), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(sample_width)
        stream.setframerate(rate)
        stream.writeframes(trimmed.tobytes())
    os.chmod(temporary, 0o644)
    os.replace(temporary, target)
    return (end - start) / rate


def capture_phrase(
    api_key: str,
    personality_id: str,
    phrase_index: int,
    phrase: str,
    *,
    target: Path | None = None,
    request_name: str | None = None,
) -> tuple[Path, float]:
    target = target or phrase_asset_path(DEFAULT_ASSET_ROOT, personality_id, phrase)
    target.parent.mkdir(parents=True, exist_ok=True)
    raw_capture = target.with_suffix(".capture.wav")
    raw_capture.unlink(missing_ok=True)
    recorder_env = os.environ.copy()
    recorder_env["PULSE_SERVER"] = "unix:/var/run/pulse/native"
    recorder = subprocess.Popen(
        [
            PARECORD,
            f"--device={CAPTURE_SOURCE}",
            "--format=s16le",
            "--rate=48000",
            "--channels=2",
            "--file-format=wav",
            str(raw_capture),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=recorder_env,
    )
    try:
        time.sleep(0.5)
        if recorder.poll() is not None:
            raise RuntimeError("parecord exited before TTS started")
        request_id = (
            f"asset-{personality_id}-{request_name}"
            if request_name
            else f"asset-{personality_id}-{phrase_index + 1:02d}"
        )
        request_id = f"{request_id}-{time.time_ns():x}"
        post_speak(api_key, phrase, request_id)
        saw_playback = False
        deadline = time.monotonic() + CAPTURE_TIMEOUT_SECONDS
        fallback_deadline = time.monotonic() + CAPTURE_FALLBACK_SECONDS
        while time.monotonic() < deadline:
            state = read_status().get("state")
            if state in {"answering", "proactive_finishing"}:
                saw_playback = True
            if saw_playback and state == "ready":
                break
            # The vendor demo can receive response.audio.done just after its
            # acknowledgement timer expires without publishing the answering
            # state. Keep recording for a bounded window and validate the WAV.
            if time.monotonic() >= fallback_deadline:
                break
            time.sleep(0.1)
        time.sleep(0.4)
    finally:
        recorder_error = stop_recorder(recorder)
    if not raw_capture.is_file():
        error = recorder_error.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"parecord did not create output: {error[:256]}")
    try:
        duration = trim_capture(raw_capture, target)
    finally:
        raw_capture.unlink(missing_ok=True)
    return target, duration


def capture_phrase_with_retry(
    api_key: str,
    personality_id: str,
    phrase_index: int,
    phrase: str,
    *,
    target: Path | None = None,
    request_name: str | None = None,
) -> tuple[Path, float]:
    for attempt in range(1, CAPTURE_ATTEMPTS + 1):
        try:
            return capture_phrase(
                api_key,
                personality_id,
                phrase_index,
                phrase,
                target=target,
                request_name=request_name,
            )
        except RuntimeError:
            if attempt == CAPTURE_ATTEMPTS:
                raise
            time.sleep(1.0)
    raise AssertionError("capture retry loop exited unexpectedly")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--kind",
        choices=("proactive", "touch", "all"),
        default="proactive",
    )
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("run this capture tool as root on the provisioned K1")
    os.umask(0o077)
    api_key = read_env_value(ENV_PATH, "AI_CAT_API_KEY")
    original = json.loads(RUNTIME_PATH.read_text(encoding="utf-8"))
    try:
        runtime = capture_runtime(original)
        write_runtime(runtime)
        subprocess.run(
            [SYSTEMCTL, "start", CLOUD_SERVICE],
            check=True,
            timeout=40.0,
        )
        wait_for_runtime(runtime["revision"])
        if args.kind in {"proactive", "all"}:
            for personality in PERSONALITIES:
                for index, phrase in enumerate(personality.proactive_phrases):
                    path, duration = capture_phrase_with_retry(
                        api_key,
                        personality.personality_id,
                        index,
                        phrase,
                    )
                    print(
                        f"captured {personality.personality_id}/"
                        f"{path.name}: {duration:.2f}s"
                    )
        if args.kind in {"touch", "all"}:
            for index, (sensor, phrase) in enumerate(TOUCH_PHRASES.items()):
                target = touch_phrase_asset_path(DEFAULT_ASSET_ROOT, sensor)
                path, duration = capture_phrase_with_retry(
                    api_key,
                    "shared",
                    index,
                    phrase,
                    target=target,
                    request_name=f"touch-{sensor}",
                )
                print(f"captured shared/{path.name}: {duration:.2f}s")
    finally:
        try:
            write_runtime(original)
        finally:
            subprocess.run(
                [SYSTEMCTL, "stop", CLOUD_SERVICE],
                check=False,
                timeout=20.0,
            )


if __name__ == "__main__":
    main()
