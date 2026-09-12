import importlib.util
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "tools" / "capture_cloud_phrase_assets.py"
)
SPEC = importlib.util.spec_from_file_location("capture_cloud_phrase_assets", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
capture_runtime = MODULE.capture_runtime


def test_capture_runtime_removes_tts_override_without_mutating_source() -> None:
    original = {
        "personality_id": "sunny_explorer",
        "personality_name": "元气探险家",
        "voice_name": "legacy voice",
        "voice_type": "legacy_voice_id",
        "revision": "original-revision",
        "session_update": {
            "event_id": "event-original",
            "type": "session.update",
            "session": {
                "object": "realtime.session",
                "config": {
                    "LLMConfig": {"SystemMessages": ["keep this prompt"]},
                    "SubtitleConfig": {
                        "DisableRTSSubtitle": False,
                        "SubtitleMode": 1,
                    },
                    "TTSConfig": {
                        "ProviderParams": {
                            "audio": {"voice_type": "legacy_voice_id"}
                        }
                    },
                },
            },
        },
    }

    result = capture_runtime(original)
    config = result["session_update"]["session"]["config"]

    assert result["personality_id"] == "sunny_explorer"
    assert result["voice_name"] == "火山引擎控制台当前音色"
    assert result["voice_type"] == "volcengine_console"
    assert result["voice_source"] == "volcengine_console"
    assert config["LLMConfig"] == {"SystemMessages": ["keep this prompt"]}
    assert config["SubtitleConfig"] == {
        "DisableRTSSubtitle": False,
        "SubtitleMode": 1,
    }
    assert "TTSConfig" not in config
    assert original["voice_type"] == "legacy_voice_id"
    assert "TTSConfig" in original["session_update"]["session"]["config"]
