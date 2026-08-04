import subprocess
from pathlib import Path

import pytest

from ai_cat_controller.domain.personalities import PERSONALITIES
from ai_cat_controller.local_speech import (
    LocalPhrasePlayer,
    LocalSpeechError,
    generate_assets,
    phrase_asset_path,
)


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


def test_generate_assets_creates_all_fifteen_fixed_files(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def run_command(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        output = Path(command[command.index("-w") + 1])
        output.write_bytes(b"RIFF-generated")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    generated = generate_assets(
        tmp_path,
        generator_path=Path("/usr/bin/espeak-ng"),
        run_command=run_command,
    )

    assert generated == 15
    assert len(commands) == 15
    assert len(list(tmp_path.glob("*/*.wav"))) == 15
