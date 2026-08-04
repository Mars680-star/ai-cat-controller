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


def test_native_dialog_ids_are_unique_across_service_restarts() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")

    assert "dialog_runtime_started_ms = __get_time_ms();" in source
    assert '"native-%" PRIu64 "-%lu"' in source
    assert '"native-%s-%" PRIu64 "-%lu"' in source


def test_native_dialog_accepts_fixed_file_text_requests() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")

    assert '#define DIALOG_TEXT_REQUEST_PATH DIALOG_EVENT_DIR' in source
    assert "sigaction(SIGHUP, &sa, NULL);" in source
    assert 'return __send_dialog_text_item(demo, event_id, text, "input_text", 1);' in source
    assert "__write_dialog_event(\"user\", text_content, text_event_id);" in source
    assert "text dialog response.create sent" in source


def test_native_dialog_supports_low_priority_proactive_tts() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")

    assert 'strcmp(text_request_kind, "speak") == 0' in source
    assert 'return __send_dialog_text_item(demo, event_id, text, "input_tts", 3);' in source
    assert "proactive input_tts item sent" in source
    assert "wake_request || session_active || running || ai_playing" in source


def test_prepare_sdk_installs_canonical_dialog_source() -> None:
    script = PREPARE_SCRIPT_PATH.read_text(encoding="utf-8")

    assert 'REPO_DIR=$(CDPATH= cd -- "$INTEGRATION_DIR/../.." && pwd)' in script
    assert '"$REPO_DIR/native/dialog/volc_conv_ai_demo.c"' in script


def test_native_dialog_uses_fixed_motion_commands() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")

    assert '__is_motion_function(task->name)' in source
    assert 'strcmp(name, "shake_head") == 0' in source
    assert 'strcmp(name, "nod_head") == 0' in source
    assert 'strcmp(name, "wag_tail") == 0' in source
    assert 'strcmp(actuator, "head_lr") != 0' in source
    assert 'strcmp(actuator, "head_ud") != 0' in source
    assert 'strcmp(actuator, "head_lr") != 0 || strcmp(speed, "1") != 0' in source
    assert 'strcmp(actuator, "head_ud") != 0 || strcmp(speed, "2") != 0' in source
    assert 'const char* executable = "/usr/bin/ai-toy_app";' in source
    assert 'getenv("AI_CAT_ENABLE_TAIL_MOTION")' in source
    assert 'const char* speed = (is_tail || !is_nod) ? "1" : "2";' in source


def test_native_dialog_applies_personality_and_enforces_action_rules() -> None:
    source = SOURCE_PATH.read_text(encoding="utf-8")

    assert '#define PERSONALITY_RUNTIME_PATH' in source
    assert '__apply_personality_runtime(&demo, true)' in source
    assert '__apply_personality_runtime(&demo, false)' in source
    assert 'strcmp(update_type_obj->valuestring, "session.update") != 0' in source
    assert '__personality_allows_motion(' in source
    assert 'rejected by personality action rules' in source
    assert 'PERSONALITY_POLL_INTERVAL_MS' in source
