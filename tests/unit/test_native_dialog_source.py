from pathlib import Path


SOURCE_PATH = Path(__file__).parents[2] / "native/dialog/volc_conv_ai_demo.c"
PREPARE_SCRIPT_PATH = (
    Path(__file__).parents[2]
    / "integrations/volcengine-k1/scripts/prepare_sdk.sh"
)


def test_native_dialog_commits_once_then_requests_response_after_ack() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")

    assert '\\\"TurnDetectionMode\\\":1' in source
    assert '\\\"ExpireTime\\\":1200' in source
    assert "info.commit = should_commit;" in source
    assert "client audio commit acknowledged" in source
    assert "response.create sent after commit acknowledgement" in source
    assert "final transcript timeout: cancelling response" in source
    assert 'WS_RESPONSE_CANCEL "{\\\"type\\\":\\\"response.cancel\\\"}"' in source
    assert "client audio commit timeout: restarting cloud session" in source


def test_native_dialog_clears_cloud_audio_on_fresh_wake() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")

    assert source.count("_ws_clear_buffer(&demo);") >= 5


def test_native_dialog_reopens_capture_after_ready_tone() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")

    assert "pa_simple_free(demo->p_capture);" in source
    assert "capture stream reopened; live listening starts now" in source
    assert "pa_simple_flush(demo->p_capture" not in source


def test_prepare_sdk_installs_canonical_dialog_source() -> None:
    script = PREPARE_SCRIPT_PATH.read_text(encoding="utf-8")

    assert 'REPO_DIR=$(CDPATH= cd -- "$INTEGRATION_DIR/../.." && pwd)' in script
    assert '"$REPO_DIR/native/dialog/volc_conv_ai_demo.c"' in script
