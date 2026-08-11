"""Play allowlisted cached phrases on K1 without opening a cloud session.

Maintenance notes:
- Phrase keys and touch mappings are maintained here; WAV files live under the
  configured asset root and must be regenerated when spoken text changes.
- Playback is intentionally restricted to validated local files and ``paplay``.
"""

from __future__ import annotations

import os
import subprocess
import wave
from collections.abc import Callable
from pathlib import Path

from ai_cat_controller.domain.personalities import PERSONALITIES, PERSONALITY_BY_ID

DEFAULT_ASSET_ROOT = Path("/opt/ai-cat-controller/assets/local-speech")
DEFAULT_MARKER_PATH = Path("/run/ai-cat/local-speech-active")
DEFAULT_PLAYER_PATH = Path("/usr/bin/paplay")
PLAYER_TIMEOUT_SECONDS = 15.0
TOUCH_PHRASES = {
    "head": "摸摸头，好舒服呀。",
    "back": "轻轻摸背，我很喜欢。",
    "nose": "呀，鼻子有点痒。",
    "left_foot": "你碰到我的左爪啦。",
    "right_foot": "你碰到我的右爪啦。",
}


class LocalSpeechError(RuntimeError):
    """A fixed local phrase could not be generated or played safely."""


def phrase_asset_path(
    asset_root: Path,
    personality_id: str,
    phrase: str,
) -> Path:
    personality = PERSONALITY_BY_ID.get(personality_id)
    if personality is None:
        raise LocalSpeechError("unknown personality for local speech")
    try:
        phrase_index = personality.proactive_phrases.index(phrase)
    except ValueError as exc:
        raise LocalSpeechError("phrase is not allowlisted for this personality") from exc
    return asset_root / personality_id / f"phrase-{phrase_index + 1:02d}.wav"


def touch_phrase_asset_path(
    asset_root: Path,
    sensor: str,
) -> Path:
    if sensor not in TOUCH_PHRASES:
        raise LocalSpeechError("touch sensor is not allowlisted for local speech")
    return asset_root / "shared" / f"touch-{sensor}.wav"


class LocalPhrasePlayer:
    """Play only generated phrase assets from the fixed local asset tree."""

    def __init__(
        self,
        *,
        asset_root: Path = DEFAULT_ASSET_ROOT,
        marker_path: Path = DEFAULT_MARKER_PATH,
        player_path: Path = DEFAULT_PLAYER_PATH,
        timeout_seconds: float = PLAYER_TIMEOUT_SECONDS,
        run_command: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    ) -> None:
        self._asset_root = asset_root
        self._marker_path = marker_path
        self._player_path = player_path
        self._timeout_seconds = timeout_seconds
        self._run_command = run_command

    def play(self, personality_id: str, phrase: str) -> Path:
        expected_path = phrase_asset_path(
            self._asset_root,
            personality_id,
            phrase,
        )
        return self._play_asset(expected_path, "personality-phrase")

    def play_touch(self, sensor: str) -> Path:
        expected_path = touch_phrase_asset_path(
            self._asset_root,
            sensor,
        )
        return self._play_asset(expected_path, f"touch-{sensor}")

    def validate_personality_assets(self) -> tuple[Path, ...]:
        try:
            player = self._player_path.resolve(strict=True)
        except OSError as exc:
            raise LocalSpeechError(
                f"local phrase player is unavailable: {self._player_path}"
            ) from exc
        if not player.is_file() or not os.access(player, os.X_OK):
            raise LocalSpeechError(
                f"local phrase player is not executable: {self._player_path}"
            )

        paths = tuple(
            self._resolve_asset(
                phrase_asset_path(
                    self._asset_root,
                    personality.personality_id,
                    phrase,
                )
            )
            for personality in PERSONALITIES
            for phrase in personality.proactive_phrases
        )
        for path in paths:
            try:
                with wave.open(str(path), "rb") as stream:
                    audio_format = (
                        stream.getnchannels(),
                        stream.getsampwidth(),
                        stream.getframerate(),
                        stream.getnframes(),
                    )
            except (OSError, wave.Error) as exc:
                raise LocalSpeechError(
                    f"local phrase asset is not a readable WAV: {path}"
                ) from exc
            if audio_format[:3] != (2, 2, 48_000) or audio_format[3] <= 0:
                raise LocalSpeechError(
                    f"local phrase asset has an unsupported format: {path}"
                )
        return paths

    def _play_asset(self, expected_path: Path, stream_name: str) -> Path:
        asset = self._resolve_asset(expected_path)

        self._marker_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            marker_descriptor = os.open(self._marker_path, flags, 0o600)
        except FileExistsError as exc:
            raise LocalSpeechError("another local phrase is already playing") from exc

        try:
            with os.fdopen(marker_descriptor, "w", encoding="ascii") as marker:
                marker.write(f"{os.getpid()}\n")
                marker.flush()
                os.fsync(marker.fileno())
            try:
                result = self._run_command(
                    [
                        str(self._player_path),
                        "--client-name=ai-cat-local-speech",
                        f"--stream-name={stream_name}",
                        str(asset),
                    ],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=self._timeout_seconds,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise LocalSpeechError(f"local phrase playback failed: {exc}") from exc
            if result.returncode != 0:
                error = result.stderr.decode("utf-8", errors="replace").strip()
                raise LocalSpeechError(
                    f"local phrase player exited with {result.returncode}: {error[:256]}"
                )
        finally:
            self._marker_path.unlink(missing_ok=True)
        return asset

    def _resolve_asset(self, expected_path: Path) -> Path:
        try:
            root = self._asset_root.resolve(strict=True)
            asset = expected_path.resolve(strict=True)
        except OSError as exc:
            raise LocalSpeechError(
                f"local phrase asset is unavailable: {expected_path}"
            ) from exc
        if (
            not asset.is_relative_to(root)
            or not asset.is_file()
            or expected_path.is_symlink()
        ):
            raise LocalSpeechError("local phrase asset failed path validation")
        return asset
