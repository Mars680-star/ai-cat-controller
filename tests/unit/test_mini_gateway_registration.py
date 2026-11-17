"""Explicit local registration, using only temporary DBs and mock bridge reads."""

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from ai_cat_controller.mini_gateway.store import SessionStore


APP_ID = "wx0123456789abcdef"
SERIAL = "7c2b63fd4a128"
TOKEN = "test-local-bridge-token-not-real-0123456789"


@pytest.fixture
def store(tmp_path):
    result = SessionStore(tmp_path / "sessions.sqlite3")
    result.issue_session(APP_ID, "test-openid", 3600)
    return result


def user(store, *, app_id=APP_ID, openid="test-openid"):
    return store.issue_session(app_id, openid, 3600).user_id


def snapshot():
    return {
        "readOnly": True, "sampledAt": "2026-10-20T00:00:00Z",
        "health": {"status": "ok", "version": "0.1.0", "adapter": "local_k1"},
        "device": {"connected": True, "deviceSerial": "7c2****a128", "networkName": None,
                   "batteryPercent": 93, "batteryState": "charging", "batteryVoltage": 4.1,
                   "chargerOnline": True, "batteryPresent": True, "lastSeenAt": None},
        "dialog": {"state": "offline", "elapsedSeconds": None, "followUpRemaining": 0,
                   "canInterrupt": False, "sessionActive": False, "stale": False},
    }


def reader(*, serial=SERIAL, data=None, actual_serial=SERIAL, fail=False):
    from ai_cat_controller.mini_gateway.status_reader import LocalStatusReader

    requests = []

    def dispatch(request):
        requests.append(request)
        if fail:
            raise httpx.ConnectError("private token=" + TOKEN, request=request)
        return httpx.Response(200, json={"success": True, "data": snapshot() if data is None else data},
                              headers={"X-AI-Cat-Device-SHA256": hashlib.sha256(actual_serial.encode("ascii")).hexdigest()})

    return LocalStatusReader(bridge_token=TOKEN, expected_serial=serial, transport=httpx.MockTransport(dispatch)), requests


async def register(store, tmp_path, *, owner=None, bridge=None, **changes):
    from ai_cat_controller.mini_gateway.registration import register_local_device

    bridge = bridge if bridge is not None else reader()[0]
    options = dict(app_id=APP_ID, user_id=owner or user(store), pet_id="pet-current", name="小安",
                   expected_serial=SERIAL, reader=bridge, backup_file=tmp_path / "before-register.sqlite3")
    options.update(changes)
    return await register_local_device(store, **options)


@pytest.mark.asyncio
async def test_registration_backs_up_before_write_and_returns_no_private_identity(store, tmp_path):
    owner = user(store)
    result = await register(store, tmp_path, owner=owner)
    assert result == {"petId": "pet-current", "name": "小安", "registered": True}
    assert store.authorize(owner, "pet-current")
    backup = tmp_path / "before-register.sqlite3"
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT count(*) FROM mg_pets").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM mg_users").fetchone()[0] == 1
        assert connection.execute("SELECT value FROM mg_meta WHERE key='device:pet-current'").fetchone() is None
    assert SERIAL not in json.dumps(result) and TOKEN not in json.dumps(result)


@pytest.mark.asyncio
async def test_registration_persists_full_identity_hash_with_ownership(store, tmp_path):
    await register(store, tmp_path)
    expected = hashlib.sha256(SERIAL.encode("ascii")).hexdigest()
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT value FROM mg_meta WHERE key='device:pet-current'").fetchone() == (expected,)
    assert store.registered_device_matches("pet-current", SERIAL)
    assert not store.registered_device_matches("pet-current", "7c2b63fd4a120")
    assert not store.registered_device_matches("unknown", SERIAL)
    assert SERIAL.encode() not in store.path.read_bytes()


@pytest.mark.asyncio
async def test_same_pet_cannot_silently_change_serial(store, tmp_path):
    owner = user(store)
    await register(store, tmp_path, owner=owner)
    different = "7c2b63fd4a120"
    data = snapshot()
    data["device"]["deviceSerial"] = "7c2****a120"
    bridge, requests = reader(serial=different, actual_serial=different, data=data)
    with pytest.raises(ValueError):
        await register(store, tmp_path, owner=owner, expected_serial=different, bridge=bridge,
                       backup_file=tmp_path / "different-device.sqlite3")
    assert not requests
    assert store.registered_device_matches("pet-current", SERIAL)
    assert not (tmp_path / "different-device.sqlite3").exists()


@pytest.mark.asyncio
async def test_existing_grant_without_fingerprint_cannot_be_silently_upgraded(store, tmp_path):
    owner = user(store)
    store.grant_pet(owner, "pet-current", "小安")
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        await register(store, tmp_path, owner=owner)
    assert store.path.read_bytes() == before


@pytest.mark.asyncio
async def test_other_pet_id_cannot_claim_already_registered_physical_device(store, tmp_path):
    await register(store, tmp_path)
    other = user(store, openid="another-owner")
    with pytest.raises(ValueError):
        await register(store, tmp_path, owner=other, pet_id="pet-alias",
                       backup_file=tmp_path / "alias-before.sqlite3")
    assert not store.authorize(other, "pet-alias")
    assert not (tmp_path / "alias-before.sqlite3").exists()


@pytest.mark.asyncio
async def test_failed_backup_never_writes_grant_or_fingerprint(store, tmp_path, monkeypatch):
    from ai_cat_controller.mini_gateway import registration

    owner = user(store)

    def fail(*_arguments):
        raise OSError("private: " + TOKEN)

    monkeypatch.setattr(registration, "_backup_database", fail)
    with pytest.raises(ValueError) as error:
        await register(store, tmp_path, owner=owner)
    assert TOKEN not in str(error.value)
    assert error.value.__context__ is None
    assert not store.authorize(owner, "pet-current")
    assert not store.registered_device_matches("pet-current", SERIAL)


@pytest.mark.asyncio
async def test_identical_registration_is_idempotent_without_overwriting_backup(store, tmp_path):
    owner = user(store)
    await register(store, tmp_path, owner=owner)
    before = (tmp_path / "before-register.sqlite3").read_bytes()
    result = await register(store, tmp_path, owner=owner)
    assert result["registered"] is False
    assert (tmp_path / "before-register.sqlite3").read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["missing_user", "wrong_app", "different_owner", "different_name"])
async def test_registration_rejects_invalid_identity_or_existing_grant_without_change(store, tmp_path, invalid):
    owner = user(store)
    changes = {}
    if invalid == "missing_user":
        owner = "usr_missing_user"
    elif invalid == "wrong_app":
        changes["app_id"] = "wxfedcba9876543210"
    else:
        store.grant_pet(owner, "pet-current", "原名")
        if invalid == "different_owner":
            owner = user(store, openid="different-openid")
        else:
            changes["name"] = "不许覆盖原名"
    before = store.path.read_bytes()
    bridge, requests = reader()
    with pytest.raises(ValueError):
        await register(store, tmp_path, owner=owner, bridge=bridge, **changes)
    assert store.path.read_bytes() == before
    assert not (tmp_path / "before-register.sqlite3").exists()
    assert not requests


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["offline", "unconfigured_pin", "wrong_pin", "other_device", "wrong_mask", "mock", "schema", "read_failure"])
async def test_bridge_must_be_online_strict_and_full_identity_pinned(store, tmp_path, failure):
    data = snapshot()
    kwargs = {}
    if failure == "offline":
        data["device"]["connected"] = False
    elif failure == "unconfigured_pin":
        kwargs["serial"] = None
    elif failure == "wrong_pin":
        kwargs["serial"] = "7c2b63fd4a120"
    elif failure == "other_device":
        kwargs["actual_serial"] = "7c2b63fd4a120"
    elif failure == "wrong_mask":
        data["device"]["deviceSerial"] = "7c2****a120"
    elif failure == "mock":
        data["health"]["adapter"] = "mock"
    elif failure == "schema":
        data["device"]["batteryPercent"] = True
    else:
        kwargs["fail"] = True
    bridge, _ = reader(data=data, **kwargs)
    owner = user(store)
    with pytest.raises(ValueError) as error:
        await register(store, tmp_path, owner=owner, bridge=bridge)
    assert not store.authorize(owner, "pet-current")
    assert not (tmp_path / "before-register.sqlite3").exists()
    assert TOKEN not in str(error.value) and SERIAL not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe", ["existing", "symlink", "package", "source", "missing_parent"])
async def test_backup_path_must_be_new_private_and_outside_package(store, tmp_path, unsafe):
    target = tmp_path / "snapshot.sqlite3"
    existing = tmp_path / "preserve-this-file"
    existing.write_text("keep", encoding="ascii")
    if unsafe == "existing":
        target.write_text("preserve", encoding="ascii")
    elif unsafe == "symlink":
        target.symlink_to(existing)
    elif unsafe == "package":
        folder = tmp_path / "miniprogram"
        folder.mkdir()
        target = folder / "snapshot.sqlite3"
    elif unsafe == "source":
        target = store.path
    else:
        target = tmp_path / "absent-parent" / "snapshot.sqlite3"
    owner = user(store)
    source_before = store.path.read_bytes()
    with pytest.raises(ValueError):
        await register(store, tmp_path, owner=owner, backup_file=target)
    assert store.path.read_bytes() == source_before
    assert existing.read_text(encoding="ascii") == "keep"
    assert not store.authorize(owner, "pet-current")


@pytest.mark.asyncio
async def test_same_owner_concurrent_registration_writes_once(store, tmp_path):
    owner = user(store)
    results = await asyncio.gather(*[
        register(store, tmp_path, owner=owner, backup_file=tmp_path / f"before-{index}.sqlite3")
        for index in range(2)
    ])
    assert sorted(result["registered"] for result in results) == [False, True]
    assert len(list(tmp_path.glob("before-*.sqlite3"))) == 1


@pytest.mark.asyncio
async def test_two_owners_cannot_race_to_claim_same_device(store, tmp_path):
    owners = [user(store), user(store, openid="other-openid")]
    results = await asyncio.gather(*[
        register(store, tmp_path, owner=owner, backup_file=tmp_path / f"before-{index}.sqlite3")
        for index, owner in enumerate(owners)
    ], return_exceptions=True)
    assert sum(type(result) is dict and result["registered"] is True for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert sum(store.authorize(owner, "pet-current") for owner in owners) == 1


def test_open_existing_never_creates_missing_db_or_changes_wrong_db(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(ValueError):
        SessionStore.open_existing(missing)
    assert not missing.exists()
    wrong = tmp_path / "business.sqlite3"
    with sqlite3.connect(wrong) as connection:
        connection.execute("CREATE TABLE product_users(id TEXT)")
    wrong.chmod(0o600)
    before = wrong.read_bytes()
    with pytest.raises(ValueError):
        SessionStore.open_existing(wrong)
    assert wrong.read_bytes() == before


def test_cli_only_uses_private_token_file_and_prints_safe_result(store, tmp_path, monkeypatch, capsys):
    from ai_cat_controller.mini_gateway import registration

    owner = user(store)
    token = tmp_path / "bridge-token"
    token.write_text(TOKEN, encoding="ascii")
    token.chmod(0o600)
    fake_reader, requests = reader()
    monkeypatch.setattr(registration, "LocalStatusReader", lambda **kwargs: fake_reader)
    registration.main([
        "--database", str(store.path), "--app-id", APP_ID, "--user-id", owner,
        "--pet-id", "pet-current", "--name", "小安", "--expected-serial", SERIAL,
        "--bridge-token-file", str(token), "--backup-file", str(tmp_path / "cli-before.sqlite3"),
    ])
    output = capsys.readouterr()
    assert json.loads(output.out) == {"petId": "pet-current", "name": "小安", "registered": True}
    assert TOKEN not in output.out + output.err
    assert SERIAL not in output.out + output.err and owner not in output.out + output.err
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_backup_permission_failure_cleans_only_its_incomplete_file(store, tmp_path, monkeypatch):
    from ai_cat_controller.mini_gateway import registration

    owner = user(store)

    def fail(*_arguments):
        raise OSError("permission unavailable")

    monkeypatch.setattr(registration.os, "fchmod", fail)
    with pytest.raises(ValueError):
        await register(store, tmp_path, owner=owner)
    assert not (tmp_path / "before-register.sqlite3").exists()
    assert not store.authorize(owner, "pet-current")
    assert not store.registered_device_matches("pet-current", SERIAL)


def test_cli_unknown_arguments_never_echo_accidentally_supplied_secrets(capsys):
    from ai_cat_controller.mini_gateway import registration

    with pytest.raises(SystemExit) as error:
        registration.main([
            "--database", "unused-db", "--app-id", APP_ID, "--user-id", "explicit-user",
            "--pet-id", "pet-current", "--name", "demo", "--expected-serial", SERIAL,
            "--bridge-token-file", "unused-token-file", "--backup-file", "unused-backup",
            "--bridge-token", TOKEN,
        ])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert TOKEN not in output.out + output.err


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [0o777, 0o770])
@pytest.mark.parametrize("nested", [False, True])
async def test_backup_rejects_replaceable_directory_or_ancestor_before_reading(store, tmp_path, mode, nested):
    owner = user(store)
    unsafe = tmp_path / "replaceable"
    unsafe.mkdir(mode=0o700)
    private = unsafe / "private"
    private.mkdir(mode=0o700)
    unsafe.chmod(mode)
    destination = (private if nested else unsafe) / "backup.sqlite3"
    bridge, requests = reader()
    before = store.path.read_bytes()
    try:
        with pytest.raises(ValueError):
            await register(store, tmp_path, owner=owner, bridge=bridge, backup_file=destination)
        assert not requests
        assert not destination.exists()
        assert store.path.read_bytes() == before
        assert not store.authorize(owner, "pet-current")
    finally:
        unsafe.chmod(0o700)
