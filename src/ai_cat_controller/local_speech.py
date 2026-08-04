"""Play allowlisted cached personality phrases on K1."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from ai_cat_controller.domain.personalities import PERSONALITY_BY_ID

DEFAULT_ASSET_ROOT = Path("/opt/ai-cat-controller/assets/local-speech")
DEFAULT_MARKER_PATH = Path("/run/ai-cat/local-speech-active")
DEFAULT_PLAYER_PATH = Path("/usr/bin/paplay")
PLAYER_TIMEOUT_SECONDS = 15.0


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
        try:
            root = self._asset_root.resolve(strict=True)
            asset = expected_path.resolve(strict=True)
        except OSError as exc:
            raise LocalSpeechError(f"local phrase asset is unavailable: {expected_path}") from exc
        if (
            not asset.is_relative_to(root)
            or not asset.is_file()
            or expected_path.is_symlink()
        ):
            raise LocalSpeechError("local phrase asset failed path validation")

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
                        "--stream-name=personality-phrase",
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
