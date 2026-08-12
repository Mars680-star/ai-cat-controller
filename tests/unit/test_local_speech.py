import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from ai_cat_controller.domain.personalities import PERSONALITIES
from ai_cat_controller.local_speech import (
    EVENT_PHRASES,
    LocalPhrasePlayer,
    LocalSpeechError,
    TOUCH_PHRASES,
    event_phrase_asset_path,
    phrase_asset_path,
    touch_phrase_asset_path,
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
            "--device=alsa_output.platform-snd-card_1.stereo-fallback",
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


def test_local_player_uses_fixed_touch_asset(tmp_path: Path) -> None:
    marker = tmp_path / "run" / "local-speech-active"
    asset = touch_phrase_asset_path(tmp_path, "nose")
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"RIFF-touch")
    commands: list[list[str]] = []

    def run_command(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    player = LocalPhrasePlayer(
        asset_root=tmp_path,
        marker_path=marker,
        player_path=Path("/usr/bin/paplay"),
        run_command=run_command,
    )

    played = player.play_touch("nose")

    assert played == asset
    assert commands[0][1:4] == [
        "--device=alsa_output.platform-snd-card_1.stereo-fallback",
        "--client-name=ai-cat-local-speech",
        "--stream-name=touch-nose",
    ]
    assert not marker.exists()


def test_local_player_rejects_unknown_touch_sensor(tmp_path: Path) -> None:
    player = LocalPhrasePlayer(asset_root=tmp_path)

    with pytest.raises(LocalSpeechError, match="not allowlisted"):
        player.play_touch("arbitrary_gpio")


def test_local_player_uses_fixed_level_up_event_asset(tmp_path: Path) -> None:
    marker = tmp_path / "run" / "local-speech-active"
    asset = event_phrase_asset_path(tmp_path, "level_up")
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"RIFF-event")
    commands: list[list[str]] = []

    def run_command(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    player = LocalPhrasePlayer(
        asset_root=tmp_path,
        marker_path=marker,
        player_path=Path("/usr/bin/paplay"),
        run_command=run_command,
    )

    played = player.play_event("level_up")

    assert played == asset
    assert commands[0][1:4] == [
        "--device=alsa_output.platform-snd-card_1.stereo-fallback",
        "--client-name=ai-cat-local-speech",
        "--stream-name=event-level_up",
    ]
    assert not marker.exists()


def test_local_player_rejects_unknown_event(tmp_path: Path) -> None:
    player = LocalPhrasePlayer(asset_root=tmp_path)

    with pytest.raises(LocalSpeechError, match="not allowlisted"):
        player.play_event("arbitrary_event")


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


def test_local_player_validates_all_personality_assets(tmp_path: Path) -> None:
    player = LocalPhrasePlayer(
        asset_root=REPOSITORY_ASSETS,
        marker_path=tmp_path / "marker",
        player_path=Path("/bin/true"),
    )

    paths = player.validate_personality_assets()

    assert len(paths) == 15
    assert all(path.is_file() for path in paths)


def test_local_player_startup_validation_reports_missing_asset(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    player = LocalPhrasePlayer(
        asset_root=assets,
        marker_path=tmp_path / "marker",
        player_path=Path("/bin/true"),
    )

    with pytest.raises(LocalSpeechError, match="asset is unavailable"):
        player.validate_personality_assets()


def test_local_player_startup_validation_rejects_corrupt_wav(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    shutil.copytree(REPOSITORY_ASSETS, assets)
    phrase_asset_path(
        assets,
        PERSONALITIES[0].personality_id,
        PERSONALITIES[0].proactive_phrases[0],
    ).write_bytes(b"not-a-wave-file")
    player = LocalPhrasePlayer(
        asset_root=assets,
        marker_path=tmp_path / "marker",
        player_path=Path("/bin/true"),
    )

    with pytest.raises(LocalSpeechError, match="not a readable WAV"):
        player.validate_personality_assets()


def test_repository_contains_five_shared_touch_voice_assets() -> None:
    paths = [
        touch_phrase_asset_path(
            REPOSITORY_ASSETS,
            sensor,
        )
        for sensor in TOUCH_PHRASES
    ]

    assert len(paths) == 5
    for path in paths:
        assert path.is_file()
        with wave.open(str(path), "rb") as stream:
            assert stream.getnchannels() == 2
            assert stream.getsampwidth() == 2
            assert stream.getframerate() == 48_000
            assert stream.getnframes() > 48_000


def test_repository_contains_shared_event_voice_assets() -> None:
    paths = [
        event_phrase_asset_path(REPOSITORY_ASSETS, event)
        for event in EVENT_PHRASES
    ]

    assert len(paths) == 1
    for path in paths:
        assert path.is_file()
        with wave.open(str(path), "rb") as stream:
            assert stream.getnchannels() == 2
            assert stream.getsampwidth() == 2
            assert stream.getframerate() == 48_000
            assert stream.getnframes() > 48_000
