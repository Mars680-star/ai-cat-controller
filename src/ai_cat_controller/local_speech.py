"""Generate and play allowlisted offline personality phrases on K1."""

from __future__ import annotations

import argparse
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from ai_cat_controller.domain.personalities import PERSONALITIES, PERSONALITY_BY_ID

DEFAULT_ASSET_ROOT = Path("/opt/ai-cat-controller/assets/local-speech")
DEFAULT_MARKER_PATH = Path("/run/ai-cat/local-speech-active")
DEFAULT_PLAYER_PATH = Path("/usr/bin/paplay")
DEFAULT_GENERATOR_PATH = Path("/usr/bin/espeak-ng")
PLAYER_TIMEOUT_SECONDS = 15.0
VOICE_SETTINGS = {
    "sunny_explorer": (175, 65),
    "gentle_companion": (135, 55),
    "proud_star": (155, 35),
    "curious_scholar": (165, 50),
    "calm_guardian": (125, 30),
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


def generate_assets(
    asset_root: Path = DEFAULT_ASSET_ROOT,
    *,
    generator_path: Path = DEFAULT_GENERATOR_PATH,
    run_command: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> int:
    """Generate the fixed WAV set once; runtime playback performs no synthesis."""

    generated = 0
    for personality in PERSONALITIES:
        speed, pitch = VOICE_SETTINGS[personality.personality_id]
        directory = asset_root / personality.personality_id
        directory.mkdir(parents=True, exist_ok=True)
        for phrase in personality.proactive_phrases:
            target = phrase_asset_path(
                asset_root,
                personality.personality_id,
                phrase,
            )
            temporary = target.with_suffix(".wav.tmp")
            try:
                result = run_command(
                    [
                        str(generator_path),
                        "-v",
                        "cmn",
                        "-s",
                        str(speed),
                        "-p",
                        str(pitch),
                        "-a",
                        "150",
                        "-w",
                        str(temporary),
                        phrase,
                    ],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=30.0,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                temporary.unlink(missing_ok=True)
                raise LocalSpeechError(f"offline phrase generation failed: {exc}") from exc
            if result.returncode != 0 or not temporary.is_file():
                error = result.stderr.decode("utf-8", errors="replace").strip()
                temporary.unlink(missing_ok=True)
                raise LocalSpeechError(
                    f"offline generator exited with {result.returncode}: {error[:256]}"
                )
            os.chmod(temporary, 0o644)
            os.replace(temporary, target)
            generated += 1
    return generated


def main() -> None:
    parser = argparse.ArgumentParser(description="AI cat offline phrase assets")
    parser.add_argument(
        "--generate-assets",
        action="store_true",
        help="generate all fixed personality WAV files with espeak-ng",
    )
    parser.add_argument(
        "--asset-root",
        type=Path,
        default=DEFAULT_ASSET_ROOT,
        help="fixed output directory",
    )
    args = parser.parse_args()
    if not args.generate_assets:
        parser.error("--generate-assets is required")
    count = generate_assets(args.asset_root)
    print(f"generated {count} local personality phrases in {args.asset_root}")


if __name__ == "__main__":
    main()
