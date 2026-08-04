import subprocess
import wave
from pathlib import Path

import pytest

from ai_cat_controller.domain.personalities import PERSONALITIES
from ai_cat_controller.local_speech import (
    LocalPhrasePlayer,
    LocalSpeechError,
    phrase_asset_path,
)

REPOSITORY_ASSETS = Path(__file__).parents[2] / "assets/local-speech"


def test_local_player_uses_fixed_asset_and_removes_marker(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    marker = tmp_path / "run" / "local-speech-active"
    phrase = PERSONALITIES[0].proactive_phrases[0]
    asset = phrase_asset_path(assets, "sunny_explorer", phrase)
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"RIFF-test")
    commands: list[list[str]] = []

    def run_command(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    player = LocalPhrasePlayer(
        asset_root=assets,
        marker_path=marker,
        player_path=Path("/usr/bin/paplay"),
        run_command=run_command,
    )

    played = player.play("sunny_explorer", phrase)

    assert played == asset
    assert commands == [
        [
            "/usr/bin/paplay",
            "--client-name=ai-cat-local-speech",
            "--stream-name=personality-phrase",
            str(asset),
        ]
    ]
    assert not marker.exists()


def test_local_player_rejects_phrase_from_another_personality(tmp_path: Path) -> None:
    player = LocalPhrasePlayer(asset_root=tmp_path)

    with pytest.raises(LocalSpeechError, match="not allowlisted"):
        player.play("gentle_companion", "今天也要元气满满呀。")


def test_repository_contains_all_fifteen_cloud_voice_assets() -> None:
    paths = [
        phrase_asset_path(
            REPOSITORY_ASSETS,
            personality.personality_id,
            phrase,
        )
        for personality in PERSONALITIES
        for phrase in personality.proactive_phrases
    ]

    assert len(paths) == 15
    for path in paths:
        assert path.is_file()
        with wave.open(str(path), "rb") as stream:
            assert stream.getnchannels() == 2
            assert stream.getsampwidth() == 2
            assert stream.getframerate() == 48_000
            assert stream.getnframes() > 48_000
