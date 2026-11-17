"""Real HTTP/product/SQLite flow with synthetic runtime files, never a K1."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from ai_cat_controller.core.config import Settings
from ai_cat_controller.main import create_app


SERIAL = "K1-PRIVACY-LIFECYCLE"


def _settings(tmp_path: Path, **changes) -> Settings:
    return Settings(
        hardware_driver="mock", enable_growth_personality_v1=True,
        motion_cooldown_seconds=0.0, service_status_cache_seconds=0.0,
        data_path=tmp_path / "product.db",
        personality_runtime_path=tmp_path / "personality-runtime.json",
        device_serial_path=tmp_path / "serial.txt",
        dialog_event_path=tmp_path / "dialog-events.jsonl",
        dialog_text_request_path=tmp_path / "dialog-text-request.json",
        dialog_config_path=tmp_path / "dialog-runtime-config.json",
        **changes,
    )


def _file_runtime(client: TestClient, settings: Settings) -> None:
    # The adapter stays Mock. Only the pure personality-file writer uses its
    # on-disk path, allowing us to check the native consumer's actual input.
    settings.device_serial_path.write_text(SERIAL, encoding="ascii")
    client.app.state.services.personality._settings = settings.model_copy(
        update={"hardware_driver": "local_k1"}
    )


def _login(client: TestClient, code: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/mock-login", json={"login_code": code, "nickname": "测试"})
    assert response.status_code == 200
    return {"X-Mock-Session": response.json()["data"]["session_token"]}


def _bind(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.post("/api/v1/pets/bind", headers=headers,
        json={"device_serial": SERIAL, "pet_name": "小安", "network_name": "Test"})
    assert response.status_code == 200
    return response.json()["data"]


def _say(client: TestClient, headers: dict[str, str], pet_id: str, content: str) -> None:
    response = client.post(f"/api/v1/pets/{pet_id}/dialogs", headers=headers,
        json={"content": content, "trigger_action": False})
    assert response.status_code == 200


def test_unbind_clears_old_owner_file_before_reply_but_preserves_device_history(tmp_path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        _file_runtime(client, settings)
        first = _login(client, "private-first-owner")
        bound = _bind(client, first)
        pet_id = bound["pet"]["pet_id"]
        _say(client, first, pet_id, "我叫旧主人小明，我喜欢蓝莓")
        original = json.loads(settings.personality_runtime_path.read_text())
        assert original["memory_message_count"] == 2
        assert original["long_term_memory_count"] >= 1
        assert "旧主人小明" in original["system_prompt"]

        unbound = client.post(f"/api/v1/pets/{pet_id}/unbind", headers=first)
        assert unbound.status_code == 200
        if settings.personality_runtime_path.exists():
            after = json.loads(settings.personality_runtime_path.read_text())
            assert after.get("memory_message_count", 0) == 0
            assert after.get("long_term_memory_count", 0) == 0
            assert "旧主人小明" not in after.get("system_prompt", "")
        assert client.get(f"/api/v1/pets/{pet_id}/dialogs", headers=first).status_code == 404
        with sqlite3.connect(settings.data_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM dialog_history").fetchone()[0] == 2

        second = _login(client, "private-second-owner")
        rebound = _bind(client, second)
        assert rebound["blind_box_revealed"] is False
        assert rebound["pet"]["personality_id"] == bound["pet"]["personality_id"]
        assert client.get(f"/api/v1/pets/{pet_id}/dialogs", headers=second).json()["data"] == []
        after_rebind = json.loads(settings.personality_runtime_path.read_text())
        assert after_rebind["memory_message_count"] == 0
        assert after_rebind["long_term_memory_count"] == 0
        assert "旧主人小明" not in after_rebind["system_prompt"]


def test_unbind_rejects_active_dialog_without_deleting_binding_or_runtime(tmp_path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        _file_runtime(client, settings)
        headers = _login(client, "active-unbind-owner")
        pet_id = _bind(client, headers)["pet"]["pet_id"]
        _say(client, headers, pet_id, "我喜欢雨天")
        before = settings.personality_runtime_path.read_bytes()
        assert client.portal is not None
        client.portal.call(client.app.state.services.adapter.wake_dialog)

        response = client.post(f"/api/v1/pets/{pet_id}/unbind", headers=headers)
        assert response.status_code == 409
        assert response.json()["data"]["code"] == "action_conflict"
        assert len(client.get("/api/v1/pets", headers=headers).json()["data"]) == 1
        assert settings.personality_runtime_path.read_bytes() == before


def test_memory_clear_blocks_old_native_facts_without_deleting_transcripts(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    clock = {"now": datetime(2026, 10, 21, tzinfo=timezone.utc)}
    monkeypatch.setattr("ai_cat_controller.persistence.sqlite_repository._utc_now", lambda: clock["now"].isoformat())
    with TestClient(create_app(settings)) as client:
        _file_runtime(client, settings)
        headers = _login(client, "late-native-owner")
        pet_id = _bind(client, headers)["pet"]["pet_id"]
        clock["now"] += timedelta(seconds=2)
        _say(client, headers, pet_id, "我叫清除前小雨")
        clock["now"] += timedelta(seconds=2)
        response = client.post(f"/api/v1/pets/{pet_id}/memory/clear", headers=headers,
            json={"request_id": "clear-before-late-import"})
        assert response.status_code == 200
        cutoff = datetime.fromisoformat(response.json()["data"]["cleared_at"])

        def events(key: str, content: str, moment: datetime) -> list[dict]:
            milliseconds = int(moment.timestamp() * 1000)
            return [
                {"event_id": key + "-user", "conversation_id": key, "device_serial": SERIAL,
                 "role": "user", "content": content, "created_at_ms": milliseconds},
                {"event_id": key + "-assistant", "conversation_id": key, "device_serial": SERIAL,
                 "role": "assistant", "content": "明白了", "created_at_ms": milliseconds + 1},
            ]

        late = events("late-old-turn", "我喜欢已清除的草莓", cutoff - timedelta(seconds=1))
        settings.dialog_event_path.write_text("\n".join(json.dumps(item) for item in late), encoding="utf-8")
        history = client.get(f"/api/v1/pets/{pet_id}/dialogs", headers=headers)
        assert history.status_code == 200
        assert len(history.json()["data"]) == 4
        runtime = json.loads(settings.personality_runtime_path.read_text())
        assert runtime["memory_message_count"] == 0
        assert runtime["long_term_memory_count"] == 0
        assert "已清除的草莓" not in runtime["system_prompt"]

        new = events("new-valid-turn", "我喜欢清除后的苹果", cutoff + timedelta(seconds=1))
        settings.dialog_event_path.write_text("\n".join(json.dumps(item) for item in late + new), encoding="utf-8")
        assert len(client.get(f"/api/v1/pets/{pet_id}/dialogs", headers=headers).json()["data"]) == 6
        fresh = json.loads(settings.personality_runtime_path.read_text())
        assert fresh["memory_message_count"] == 2
        assert fresh["long_term_memory_count"] == 1
        assert "清除后的苹果" in fresh["system_prompt"]
        assert "已清除的草莓" not in fresh["system_prompt"]


def test_post_commit_checkpoint_failure_never_restores_erased_runtime(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, enable_product_data_reset=True)
    with TestClient(create_app(settings)) as client:
        _file_runtime(client, settings)
        headers = _login(client, "checkpoint-reset-owner")
        pet_id = _bind(client, headers)["pet"]["pet_id"]
        _say(client, headers, pet_id, "我叫格式化前的主人")
        repository = client.app.state.services.product._repository
        original_connect = repository._connect

        class CheckpointFailure:
            def __init__(self):
                self.connection = original_connect()

            def __enter__(self):
                self.connection.__enter__()
                return self

            def __exit__(self, *args):
                return self.connection.__exit__(*args)

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def execute(self, sql, *args):
                if sql.strip().upper().startswith("PRAGMA WAL_CHECKPOINT"):
                    raise sqlite3.OperationalError("synthetic post-commit checkpoint failure")
                return self.connection.execute(sql, *args)

        monkeypatch.setattr(repository, "_connect", CheckpointFailure)
        result = client.post("/api/v1/admin/reset-product-data", headers=headers,
            json={"confirmation": "RESET_PRODUCT_DATA"})
        assert result.status_code == 200
        assert result.json()["data"]["reset"] is True
        assert not settings.personality_runtime_path.exists()
        assert client.get("/api/v1/pets", headers=headers).status_code == 401
        with sqlite3.connect(settings.data_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM pets").fetchone()[0] == 0
        backup = settings.data_path.parent / "backups" / result.json()["data"]["backup_id"]
        assert "格式化前的主人" in (backup / "personality-runtime.json").read_text()
        assert (backup / "product-data.db").is_file()
