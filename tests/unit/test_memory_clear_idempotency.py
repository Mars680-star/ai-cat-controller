"""Temporary SQLite only: durable clear receipts and source-time cutoffs."""

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Barrier, Event, Thread

import pytest

from ai_cat_controller.core.errors import ResourceNotFoundError
from ai_cat_controller.persistence import sqlite_repository as repository_module
from ai_cat_controller.persistence.sqlite_repository import SQLiteRepository


BASE = datetime(2026, 10, 21, tzinfo=timezone.utc)
RECEIPTS = "dialog_memory_clear_requests"
PUBLIC_RESET_TABLES = {
    "mijia_gateway_requests", "mijia_integrations", "feedback", "action_executions",
    "dialog_long_term_memories", "dialog_memory_state", "dialog_history",
    "growth_tags", "growth_events", "growth_attributes", "interaction_events",
    "pets", "devices", "users",
}


def at(second):
    return (BASE + timedelta(seconds=second)).isoformat()


@pytest.fixture
def state(tmp_path, monkeypatch):
    clock = {"second": 0}
    monkeypatch.setattr(repository_module, "_utc_now", lambda: at(clock["second"]))
    repository = SQLiteRepository(tmp_path / "memory.db")
    repository.initialize()
    user = repository.login_user("owner-a", "用户甲")
    pet, _ = repository.bind_pet(
        user_id=user["user_id"], serial_number="K1-MEMORY-A", pet_name="小安",
        network_name="Synthetic Wi-Fi", personality_id="gentle_companion",
    )
    return repository, clock, user["user_id"], pet["pet_id"]


def pair(repository, user_id, pet_id, content="我喜欢蓝色"):
    return repository.add_dialog_pair(
        user_id=user_id, pet_id=pet_id, user_content=content,
        assistant_content="记住了。", voice_id="synthetic_tts",
    )


def clear(repository, user_id, pet_id, request_id):
    return repository.clear_dialog_memory(
        user_id=user_id, pet_id=pet_id, request_id=request_id,
    )


def memory_state(repository, user_id, pet_id):
    with repository._connect() as connection:
        row = connection.execute(
            "SELECT * FROM dialog_memory_state WHERE pet_id = ? AND user_id = ?",
            (pet_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def events(second, suffix="old", content="我喜欢旧颜色"):
    return [
        {"event_id": f"{suffix}-user", "conversation_id": f"turn-{suffix}",
         "device_serial": "K1-MEMORY-A", "role": "user", "content": content,
         "created_at": at(second)},
        {"event_id": f"{suffix}-assistant", "conversation_id": f"turn-{suffix}",
         "device_serial": "K1-MEMORY-A", "role": "assistant", "content": "记住了。",
         "created_at": at(second + 0.1)},
    ]


def test_older_clear_id_replay_after_another_clear_keeps_new_memories(state):
    repository, clock, user_id, pet_id = state
    clock["second"] = 1
    pair(repository, user_id, pet_id)
    clock["second"] = 2
    first = clear(repository, user_id, pet_id, "clear-A")
    clock["second"] = 3
    pair(repository, user_id, pet_id, "我喜欢绿色")
    clock["second"] = 4
    second = clear(repository, user_id, pet_id, "clear-B")
    clock["second"] = 5
    pair(repository, user_id, pet_id, "我叫小雨，我每天七点跑步")
    before = memory_state(repository, user_id, pet_id)
    facts = repository.list_long_term_memories(user_id, pet_id)
    recent = repository.list_recent_dialog_memory(user_id, pet_id)
    clock["second"] = 6

    for request_id, original in (("clear-A", first), ("clear-B", second), ("clear-A", first)):
        duplicate = clear(repository, user_id, pet_id, request_id)
        assert duplicate == {
            "cleared": False, "duplicate": True, "excluded_message_count": 0,
            "deleted_long_term_memory_count": 0, "cleared_at": original["cleared_at"],
        }
        assert memory_state(repository, user_id, pet_id) == before
        assert repository.list_long_term_memories(user_id, pet_id) == facts
        assert repository.list_recent_dialog_memory(user_id, pet_id) == recent
    assert len(repository.list_dialogs(user_id, pet_id)) == 6


@pytest.mark.parametrize("source_second", [1, 2])
def test_late_import_before_or_equal_clear_keeps_history_without_reviving_facts(state, source_second):
    repository, clock, user_id, pet_id = state
    clock["second"] = 2
    clear(repository, user_id, pet_id, "cutoff")
    clock["second"] = 100  # Import time must not substitute for source event time.
    imported = repository.import_native_dialog_events(
        user_id=user_id, pet_id=pet_id, events=events(source_second),
    )

    assert imported == 2
    assert len(repository.list_dialogs(user_id, pet_id)) == 2
    assert repository.list_long_term_memories(user_id, pet_id) == []
    assert repository.list_recent_dialog_memory(user_id, pet_id) == []
    assert repository.import_native_dialog_events(
        user_id=user_id, pet_id=pet_id, events=events(source_second),
    ) == 0


def test_clear_receipts_survive_repository_restart_and_keep_original_result(state):
    repository, clock, user_id, pet_id = state
    clock["second"] = 1
    pair(repository, user_id, pet_id)
    clock["second"] = 2
    original = clear(repository, user_id, pet_id, "durable-A")
    clock["second"] = 3
    clear(repository, user_id, pet_id, "durable-B")
    clock["second"] = 4
    pair(repository, user_id, pet_id, "我喜欢新颜色")
    restarted = SQLiteRepository(repository.path)
    restarted.initialize()

    duplicate = clear(restarted, user_id, pet_id, "durable-A")
    assert duplicate["duplicate"] is True
    assert duplicate["cleared_at"] == original["cleared_at"]
    assert len(restarted.list_recent_dialog_memory(user_id, pet_id)) == 2
    assert len(restarted.list_long_term_memories(user_id, pet_id)) == 1


def test_legacy_initialization_migrates_only_known_last_id_without_inventing_counts(state):
    repository, clock, user_id, pet_id = state
    clock["second"] = 1
    clear(repository, user_id, pet_id, "legacy-unknown-A")
    clock["second"] = 2
    latest = clear(repository, user_id, pet_id, "legacy-known-B")
    with repository._connect() as connection:
        connection.execute(f"DROP TABLE {RECEIPTS}")  # Exact legacy schema.
    clock["second"] = 3
    pair(repository, user_id, pet_id, "我喜欢新形成的记忆")
    repository.initialize()
    repository.initialize()
    with repository._connect() as connection:
        migrated = [dict(row) for row in connection.execute(f"SELECT * FROM {RECEIPTS}")]
    assert migrated == [{
        "pet_id": pet_id, "user_id": user_id, "request_id": "legacy-known-B",
        "cleared_at": latest["cleared_at"], "excluded_message_count": None,
        "deleted_long_term_memory_count": None,
    }]
    assert clear(repository, user_id, pet_id, "legacy-known-B")["duplicate"] is True
    assert len(repository.list_long_term_memories(user_id, pet_id)) == 1
    assert len(repository.list_recent_dialog_memory(user_id, pet_id)) == 2
    clock["second"] = 4
    result = clear(repository, user_id, pet_id, "new-C")
    repository.initialize()
    with repository._connect() as connection:
        recorded = connection.execute(
            f"SELECT * FROM {RECEIPTS} WHERE request_id = ?", ("new-C",),
        ).fetchone()
        assert connection.execute(f"SELECT COUNT(*) FROM {RECEIPTS}").fetchone()[0] == 2
    assert recorded["excluded_message_count"] == result["excluded_message_count"] == 2
    assert recorded["deleted_long_term_memory_count"] == 1


def test_clear_request_scope_is_per_owner_and_pet_with_authorization_before_receipt_lookup(state):
    repository, clock, owner_a, pet_a = state
    owner_b = repository.login_user("owner-b", "用户乙")["user_id"]
    pet_b, _ = repository.bind_pet(
        user_id=owner_a, serial_number="K1-MEMORY-B", pet_name="另一只",
        network_name="Synthetic Wi-Fi", personality_id="gentle_companion",
    )
    pet_c, _ = repository.bind_pet(
        user_id=owner_b, serial_number="K1-MEMORY-C", pet_name="第三只",
        network_name="Synthetic Wi-Fi", personality_id="gentle_companion",
    )
    scopes = [(owner_a, pet_a), (owner_a, pet_b["pet_id"]), (owner_b, pet_c["pet_id"])]
    original = []
    for second, (user_id, pet_id) in enumerate(scopes, start=1):
        clock["second"] = second
        original.append(clear(repository, user_id, pet_id, "same-request"))
        assert original[-1]["cleared"] is True
    clock["second"] = 5
    for user_id, pet_id in scopes:
        pair(repository, user_id, pet_id)
    for (user_id, pet_id), result in zip(scopes, original):
        repeated = clear(repository, user_id, pet_id, "same-request")
        assert repeated["duplicate"] is True and repeated["cleared_at"] == result["cleared_at"]
        assert len(repository.list_long_term_memories(user_id, pet_id)) == 1
    with pytest.raises(ResourceNotFoundError):
        clear(repository, owner_b, pet_a, "same-request")
    with pytest.raises(ResourceNotFoundError):
        clear(repository, owner_b, pet_a, "unknown-request")
    with repository._connect() as connection:
        assert connection.execute(f"SELECT COUNT(*) FROM {RECEIPTS}").fetchone()[0] == 3


def test_rebinding_same_pet_does_not_leak_or_reuse_prior_owners_clear_receipts(state):
    repository, clock, owner_a, pet_id = state
    clock["second"] = 1
    prior = clear(repository, owner_a, pet_id, "shared-id")
    repository.unbind_pet(owner_a, pet_id)
    owner_b = repository.login_user("owner-b", "用户乙")["user_id"]
    clock["second"] = 3
    rebound, _ = repository.bind_pet(
        user_id=owner_b, serial_number="K1-MEMORY-A", pet_name="新主人的猫",
        network_name="Synthetic Wi-Fi", personality_id="gentle_companion",
    )
    assert rebound["pet_id"] == pet_id
    with pytest.raises(ResourceNotFoundError):
        clear(repository, owner_a, pet_id, "shared-id")
    with pytest.raises(ResourceNotFoundError):
        repository.import_native_dialog_events(user_id=owner_a, pet_id=pet_id, events=events(4))
    # Old source before the new binding belongs to neither new-owner memory layer.
    assert repository.import_native_dialog_events(user_id=owner_b, pet_id=pet_id, events=events(2)) == 0
    assert repository.list_long_term_memories(owner_b, pet_id) == []
    clock["second"] = 100
    assert repository.import_native_dialog_events(user_id=owner_b, pet_id=pet_id, events=events(4, "new-owner", "我喜欢新颜色")) == 2
    assert len(repository.list_long_term_memories(owner_b, pet_id)) == 1
    result = clear(repository, owner_b, pet_id, "shared-id")
    assert result["cleared"] is True and result["duplicate"] is False
    assert result["cleared_at"] != prior["cleared_at"]


def test_post_clear_native_source_can_form_facts_but_late_older_sources_and_assistant_cannot(state):
    repository, clock, user_id, pet_id = state
    clock["second"] = 2
    clear(repository, user_id, pet_id, "cutoff")
    clock["second"] = 100
    new_events = events(3, "new", "我喜欢苹果")
    new_events[1]["content"] = "我叫助手，这不是用户事实"
    assert repository.import_native_dialog_events(user_id=user_id, pet_id=pet_id, events=new_events) == 2
    facts = repository.list_long_term_memories(user_id, pet_id)
    assert [item["content"] for item in facts] == ["喜欢苹果"]
    assert facts[0]["source_created_at"] == at(3)
    assert facts[0]["updated_at"] == at(100)
    assert repository.import_native_dialog_events(user_id=user_id, pet_id=pet_id, events=events(1, "late-old", "我不喜欢苹果")) == 2
    assert repository.import_native_dialog_events(user_id=user_id, pet_id=pet_id, events=events(2, "exact-cutoff", "我不喜欢苹果")) == 2
    assert repository.list_long_term_memories(user_id, pet_id) == facts
    assert len(repository.list_dialogs(user_id, pet_id)) == 6
    assert len(repository.list_recent_dialog_memory(user_id, pet_id)) == 2


def test_same_clear_id_concurrent_requests_have_one_effect_and_one_durable_receipt(state):
    repository, clock, user_id, pet_id = state
    clock["second"] = 1
    pair(repository, user_id, pet_id)
    clock["second"] = 2
    barrier = Barrier(2)

    def run():
        barrier.wait(5)
        return clear(SQLiteRepository(repository.path), user_id, pet_id, "parallel-once")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.submit(run), executor.submit(run)
        results = [first.result(timeout=8), second.result(timeout=8)]
    assert sorted(result["cleared"] for result in results) == [False, True]
    assert sorted(result["excluded_message_count"] for result in results) == [0, 2]
    assert sorted(result["deleted_long_term_memory_count"] for result in results) == [0, 1]
    assert len({result["cleared_at"] for result in results}) == 1
    with repository._connect() as connection:
        assert connection.execute(f"SELECT COUNT(*) FROM {RECEIPTS}").fetchone()[0] == 1


def test_clock_rollback_cannot_move_clear_cutoff_back_or_revive_cleared_raw_memory(state):
    repository, clock, user_id, pet_id = state
    clock["second"] = 4
    pair(repository, user_id, pet_id, "我喜欢原本已清的颜色")
    clock["second"] = 5
    first = clear(repository, user_id, pet_id, "before-clock-rollback")
    assert repository.list_recent_dialog_memory(user_id, pet_id) == []
    clock["second"] = 2
    second = clear(repository, user_id, pet_id, "after-clock-rollback")

    assert second["cleared_at"] == first["cleared_at"] == at(5)
    assert memory_state(repository, user_id, pet_id)["updated_at"] == at(2)
    assert repository.list_recent_dialog_memory(user_id, pet_id) == []
    clock["second"] = 3
    pair(repository, user_id, pet_id, "我喜欢时间仍不可信时的颜色")
    assert repository.list_long_term_memories(user_id, pet_id) == []
    assert repository.list_recent_dialog_memory(user_id, pet_id) == []


def test_clear_includes_future_owned_source_but_never_other_owners_future_history(state):
    repository, clock, owner_a, pet_a = state
    owner_b = repository.login_user("future-owner-b", "用户乙")["user_id"]
    pet_b, _ = repository.bind_pet(
        user_id=owner_b, serial_number="K1-FUTURE-OTHER", pet_name="另一只猫",
        network_name="Synthetic Wi-Fi", personality_id="gentle_companion",
    )
    clock["second"] = 10000
    pair(repository, owner_b, pet_b["pet_id"], "我喜欢别人的未来颜色")
    clock["second"] = 1
    assert repository.import_native_dialog_events(user_id=owner_a, pet_id=pet_a, events=events(100, "future-owned")) == 2
    result = clear(repository, owner_a, pet_a, "clear-future-source")

    assert result["excluded_message_count"] == 2
    assert result["cleared_at"] == at(100.1)
    assert memory_state(repository, owner_a, pet_a)["updated_at"] == at(1)
    assert repository.list_recent_dialog_memory(owner_a, pet_a) == []
    assert repository.list_long_term_memories(owner_a, pet_a) == []
    assert len(repository.list_long_term_memories(owner_b, pet_b["pet_id"])) == 1
    clock["second"] = 101
    pair(repository, owner_a, pet_a, "我喜欢可信新时间的颜色")
    assert len(repository.list_recent_dialog_memory(owner_a, pet_a)) == 2
    assert len(repository.list_long_term_memories(owner_a, pet_a)) == 1


def test_failed_receipt_insert_rolls_back_fact_deletion_and_cutoff_in_one_transaction(state):
    repository, clock, user_id, pet_id = state
    clock["second"] = 1
    pair(repository, user_id, pet_id)
    facts = repository.list_long_term_memories(user_id, pet_id)
    with repository._connect() as connection:
        connection.execute(f"""
            CREATE TRIGGER fail_clear_receipt BEFORE INSERT ON {RECEIPTS}
            BEGIN SELECT RAISE(ABORT, 'synthetic receipt failure'); END
        """)
    clock["second"] = 2
    with pytest.raises(sqlite3.IntegrityError, match="synthetic receipt failure"):
        clear(repository, user_id, pet_id, "atomic-clear")
    assert memory_state(repository, user_id, pet_id) is None
    assert repository.list_long_term_memories(user_id, pet_id) == facts
    assert len(repository.list_recent_dialog_memory(user_id, pet_id)) == 2
    with repository._connect() as connection:
        assert connection.execute(f"SELECT COUNT(*) FROM {RECEIPTS}").fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_clear_receipt")
    assert clear(repository, user_id, pet_id, "atomic-clear")["cleared"] is True


def test_backup_keeps_receipts_and_reset_clears_them_without_public_contract_changes(state):
    repository, clock, user_id, pet_id = state
    clock["second"] = 1
    pair(repository, user_id, pet_id)
    clock["second"] = 2
    clear(repository, user_id, pet_id, "backup-A")
    clock["second"] = 3
    pair(repository, user_id, pet_id)
    clock["second"] = 4
    clear(repository, user_id, pet_id, "backup-B")
    with repository._connect() as connection:
        original = [tuple(row) for row in connection.execute(f"SELECT * FROM {RECEIPTS} ORDER BY request_id")]
    backup = repository.backup_product_data()
    with sqlite3.connect(backup["database_path"]) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute(f"SELECT * FROM {RECEIPTS} ORDER BY request_id").fetchall() == original
    assert backup["database_path"].stat().st_mode & 0o777 == 0o600
    deleted = repository.clear_product_data()
    assert set(deleted) == PUBLIC_RESET_TABLES and len(deleted) == 14
    assert deleted["dialog_memory_state"] == 1 and deleted["dialog_history"] == 4
    repository.initialize()
    with repository._connect() as connection:
        assert connection.execute(f"SELECT COUNT(*) FROM {RECEIPTS}").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM dialog_memory_state").fetchone()[0] == 0


def test_reset_failure_before_commit_rolls_back_internal_receipts_with_all_public_tables(state):
    repository, clock, user_id, pet_id = state
    clock["second"] = 1
    clear(repository, user_id, pet_id, "preserve-if-reset-rollback")
    clock["second"] = 2
    pair(repository, user_id, pet_id)
    with repository._connect() as connection:
        connection.execute("""
            CREATE TRIGGER fail_reset BEFORE DELETE ON users
            BEGIN SELECT RAISE(ABORT, 'synthetic reset failure'); END
        """)
    with pytest.raises(sqlite3.IntegrityError, match="synthetic reset failure"):
        repository.clear_product_data()
    with repository._connect() as connection:
        assert connection.execute(f"SELECT COUNT(*) FROM {RECEIPTS}").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM dialog_history").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM dialog_memory_state").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert len(repository.list_long_term_memories(user_id, pet_id)) == 1


def test_committed_reset_does_not_report_failure_when_optional_checkpoint_errors(state, monkeypatch, caplog):
    repository, _, user_id, pet_id = state
    pair(repository, user_id, pet_id)
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

        def execute(self, statement, *args):
            if statement == "PRAGMA wal_checkpoint(TRUNCATE)":
                assert not self.connection.in_transaction
                raise sqlite3.OperationalError("private/path must not be logged")
            return self.connection.execute(statement, *args)

    monkeypatch.setattr(repository, "_connect", CheckpointFailure)
    result = repository.clear_product_data()
    assert set(result) == PUBLIC_RESET_TABLES
    assert result["users"] == 1 and result["dialog_history"] == 2
    with original_connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    assert "private/path" not in caplog.text
    assert "checkpoint" in caplog.text.lower()


def test_stale_unbind_cannot_clear_a_new_owners_binding_in_a_threaded_race(state, monkeypatch):
    repository, _, owner_a, pet_id = state
    other = SQLiteRepository(repository.path)
    owner_b = other.login_user("owner-b", "用户乙")["user_id"]
    checked, transferred = Event(), Event()
    original_owned = repository._owned_pet_row
    failures = []

    def held_owner(connection, user_id, requested_pet):
        row = original_owned(connection, user_id, requested_pet)
        # If the owner SELECT has no write lock, deterministically transfer
        # before the stale unbind UPDATE. With BEGIN IMMEDIATE the transfer
        # writer is naturally serialized after this transaction's commit.
        locked = connection.in_transaction
        checked.set()
        if not locked:
            assert transferred.wait(5)
        return row

    monkeypatch.setattr(repository, "_owned_pet_row", held_owner)

    def stale_unbind():
        try:
            repository.unbind_pet(owner_a, pet_id)
        except Exception as error:
            failures.append(error)

    def transfer():
        try:
            assert checked.wait(5)
            try:
                other.unbind_pet(owner_a, pet_id)
            except ResourceNotFoundError:
                pass  # The first legitimate unbind may have committed first.
            other.bind_pet(
                user_id=owner_b, serial_number="K1-MEMORY-A", pet_name="新主人的猫",
                network_name="Synthetic Wi-Fi", personality_id="gentle_companion",
            )
        except Exception as error:
            failures.append(error)
        finally:
            transferred.set()

    first, second = Thread(target=stale_unbind), Thread(target=transfer)
    first.start()
    second.start()
    first.join(8)
    second.join(8)
    assert not first.is_alive() and not second.is_alive()
    assert failures == []
    assert other.get_owned_pet(owner_b, pet_id)["owner_user_id"] == owner_b
    with pytest.raises(ResourceNotFoundError):
        other.unbind_pet(owner_a, pet_id)
