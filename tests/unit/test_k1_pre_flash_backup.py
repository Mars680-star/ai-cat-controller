from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import runpy
import sqlite3
import stat
import tarfile

import pytest


ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def backup_api():
    # Loading helpers must not invoke the device-only command-line workflow.
    return runpy.run_path(str(ROOT / "scripts/k1_pre_flash_backup.py"))


def create_database(path):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE messages (body TEXT NOT NULL)")
        connection.execute("INSERT INTO messages VALUES ('previous conversation')")
        connection.commit()


def test_sqlite_snapshot_is_standalone_and_private(backup_api, tmp_path):
    source = tmp_path / "source.db"
    destination = tmp_path / "snapshot.db"
    create_database(source)

    result = backup_api["snapshot_sqlite"](source, destination)

    assert result["integrity"] == "ok"
    assert result["journal_mode"] == "delete"
    assert result["table_counts"]["messages"] == 1
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert not Path(str(destination) + "-wal").exists()
    assert not Path(str(destination) + "-shm").exists()
    with closing(sqlite3.connect(f"file:{destination}?mode=ro", uri=True)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert connection.execute("SELECT body FROM messages").fetchall() == [
            ("previous conversation",)
        ]


def test_live_wal_is_included_without_checkpointing_source(backup_api, tmp_path):
    source = tmp_path / "live.db"
    destination = tmp_path / "snapshot.db"
    with closing(sqlite3.connect(source)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE messages (body TEXT NOT NULL)")
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.execute("INSERT INTO messages VALUES ('still in the WAL')")
        writer.commit()
        source.chmod(0o640)
        wal = Path(str(source) + "-wal")
        before_main = hashlib.sha256(source.read_bytes()).hexdigest()
        before_wal = hashlib.sha256(wal.read_bytes()).hexdigest()
        # An immutable reader deliberately ignores WAL: the row is not in main.
        with closing(sqlite3.connect(f"file:{source}?immutable=1", uri=True)) as main_only:
            assert main_only.execute("SELECT COUNT(*) FROM messages").fetchone() == (0,)

        result = backup_api["snapshot_sqlite"](source, destination)

        assert result["table_counts"]["messages"] == 1
        assert writer.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert writer.execute("SELECT body FROM messages").fetchall() == [
            ("still in the WAL",)
        ]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before_main
        assert hashlib.sha256(wal.read_bytes()).hexdigest() == before_wal
        assert stat.S_IMODE(source.stat().st_mode) == 0o640
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600
        assert not Path(str(destination) + "-wal").exists()
        assert not Path(str(destination) + "-shm").exists()
        with closing(sqlite3.connect(f"file:{destination}?mode=ro", uri=True)) as snapshot:
            assert snapshot.execute("SELECT body FROM messages").fetchall() == [
                ("still in the WAL",)
            ]


def test_sqlite_source_connection_is_read_only(backup_api, tmp_path, monkeypatch):
    source = tmp_path / "source.db"
    destination = tmp_path / "snapshot.db"
    create_database(source)
    connect = sqlite3.connect
    calls = []

    def record_connection(database, *args, **kwargs):
        calls.append((database, kwargs))
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", record_connection)

    backup_api["snapshot_sqlite"](source, destination)

    assert any(
        str(database).startswith("file:")
        and source.name in str(database)
        and "mode=ro" in str(database)
        and kwargs.get("uri") is True
        for database, kwargs in calls
    )
    assert not any(str(database) == str(source) for database, _ in calls)


def test_missing_sqlite_source_is_not_created(backup_api, tmp_path):
    source = tmp_path / "missing.db"
    destination = tmp_path / "snapshot.db"

    with pytest.raises((RuntimeError, ValueError)):
        backup_api["snapshot_sqlite"](source, destination)

    assert not source.exists()
    assert not destination.exists()
    assert not (tmp_path / ".complete").exists()


@pytest.mark.parametrize("existing_content", [b"", b"keep my backup unchanged"])
def test_existing_sqlite_destination_is_never_overwritten(
    backup_api, tmp_path, existing_content
):
    source = tmp_path / "source.db"
    destination = tmp_path / "snapshot.db"
    create_database(source)
    destination.write_bytes(existing_content)

    with pytest.raises((RuntimeError, ValueError)):
        backup_api["snapshot_sqlite"](source, destination)

    assert destination.read_bytes() == existing_content


def test_sqlite_table_names_are_quoted_safely(backup_api, tmp_path):
    source = tmp_path / "source.db"
    destination = tmp_path / "snapshot.db"
    names = ['name with space', 'quoted"name', 'facts; DROP TABLE messages; --']
    create_database(source)
    with closing(sqlite3.connect(source)) as connection:
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            connection.execute(f"CREATE TABLE {quoted} (value TEXT)")
            connection.execute(f"INSERT INTO {quoted} VALUES ('preserved')")
        connection.commit()

    result = backup_api["snapshot_sqlite"](source, destination)

    assert result["table_counts"]["messages"] == 1
    assert all(result["table_counts"][name] == 1 for name in names)


def test_sqlite_snapshot_handles_uri_special_path_characters(backup_api, tmp_path):
    source = tmp_path / "source ?#% 猫.db"
    destination = tmp_path / "snapshot ?#% 猫.db"
    create_database(source)

    result = backup_api["snapshot_sqlite"](source, destination)

    assert result["integrity"] == "ok"
    assert result["table_counts"]["messages"] == 1
    with closing(sqlite3.connect(destination)) as connection:
        assert connection.execute("SELECT body FROM messages").fetchall() == [
            ("previous conversation",)
        ]


def test_corrupt_sqlite_source_cannot_be_declared_complete(backup_api, tmp_path):
    source = tmp_path / "corrupt.db"
    destination = tmp_path / "snapshot.db"
    contents = b"not a SQLite database\n" * 100
    source.write_bytes(contents)

    with pytest.raises((RuntimeError, ValueError)):
        backup_api["snapshot_sqlite"](source, destination)

    assert source.read_bytes() == contents
    assert not (tmp_path / ".complete").exists()


def test_matching_device_identity_is_accepted(backup_api):
    assert backup_api["validate_identity"]("7c2b63fd4a120", "7c2b63fd4a120") is None


@pytest.mark.parametrize("actual", ["7c2b63fd4a128", "", "7c2b63fd4a120-extra"])
def test_wrong_device_identity_is_rejected(backup_api, actual):
    with pytest.raises(RuntimeError):
        backup_api["validate_identity"]("7c2b63fd4a120", actual)


def test_tar_preserves_metadata_without_archiving_live_database(backup_api):
    command = backup_api["build_tar_command"](
        Path("/root/ai-cat-backups/before-firmware-test-abc")
    )

    assert isinstance(command, list)
    assert all(isinstance(argument, str) for argument in command)
    assert command[0] == "tar"
    assert "--one-file-system" in command
    assert "--acls" in command
    assert "--xattrs" in command
    assert "--exclude=var/lib/ai-cat-controller/product-data.db*" in command


def test_tar_excludes_only_this_backup_not_history(backup_api):
    directory = Path("/root/ai-cat-backups/before-firmware-test-abc")
    command = backup_api["build_tar_command"](directory)
    excludes = [argument for argument in command if argument.startswith("--exclude=")]

    assert "--exclude=root/ai-cat-backups/before-firmware-test-abc" in excludes
    assert "--exclude=root/ai-cat-backups" not in excludes
    assert "--exclude=root/ai-cat-backups/*" not in excludes
    assert "--exclude=/root/ai-cat-backups" not in excludes
    assert "--exclude=/root/ai-cat-backups/*" not in excludes


@pytest.mark.parametrize("directory", [
    "/",
    "/root/ai-cat-backups",
    "/tmp/before-firmware-test-abc",
    "root/ai-cat-backups/before-firmware-test-abc",
    "/root/ai-cat-backups/before-firmware-test-abc/../../outside",
    "/root/ai-cat-backups/before-firmware-test-abc/nested",
])
def test_tar_rejects_unsafe_or_non_dedicated_backup_output(backup_api, directory):
    with pytest.raises((RuntimeError, ValueError)):
        backup_api["build_tar_command"](Path(directory))


def write_fake_manifest(directory):
    payloads = sorted(
        path for path in directory.iterdir()
        if path.is_file() and path.name not in ("SHA256SUMS", ".complete")
    )
    (directory / "SHA256SUMS").write_text("".join(
        hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n"
        for path in payloads
    ), encoding="utf-8")


@pytest.fixture
def fake_backup(backup_api, tmp_path):
    def create(*, omit=None, wrong_critical=False, live_database=None):
        directory = tmp_path / "backup"
        directory.mkdir()
        source = tmp_path / "source.db"
        create_database(source)
        sqlite = backup_api["snapshot_sqlite"](
            source, directory / "product-data-standalone.db"
        )
        contents = {
            "etc/ai-cat-controller.env": b"AI_CAT_API_KEY=fake-test-key\n",
            "opt/ai-cat-controller/src/ai_cat_controller/main.py": b"# fake code\n",
            "boot/vmlinuz-6.6.63": b"fake kernel image\n",
            "usr/bin/toy_ui": b"fake original display binary\n",
        }
        critical = {
            name: hashlib.sha256(contents[name]).hexdigest()
            for name in ("etc/ai-cat-controller.env", "usr/bin/toy_ui")
        }
        if wrong_critical:
            contents["usr/bin/toy_ui"] = b"unexpected replacement binary\n"
        if live_database is not None:
            contents["var/lib/ai-cat-controller/" + live_database] = b"live DB data\n"
        with tarfile.open(directory / "device-files.tar.gz", "w:gz") as archive:
            for name in ("root/.cache/sensevoice", "root/ai-cat-backups"):
                if name == omit:
                    continue
                member = tarfile.TarInfo(name)
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
            for name, content in contents.items():
                if name == omit:
                    continue
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        (directory / "metadata.json").write_text(json.dumps({
            "serial": "7c2b63fd4a120",
            "critical_files_sha256": critical,
            "sqlite": sqlite,
        }), encoding="utf-8")
        write_fake_manifest(directory)
        return directory

    return create


def test_verify_accepts_complete_consistent_fake_backup(backup_api, fake_backup):
    directory = fake_backup()

    report = backup_api["verify_backup"](directory)

    assert report == {
        "serial": "7c2b63fd4a120",
        "checksums": "ok",
        "archive": "ok",
        "sqlite": "ok",
        "members": 6,
    }
    assert not (directory / ".complete").exists()


def test_verify_rejects_missing_manifest_payload(backup_api, fake_backup):
    directory = fake_backup()
    manifest = directory / "SHA256SUMS"
    manifest.write_text("".join(
        line + "\n" for line in manifest.read_text().splitlines()
        if not line.endswith("  product-data-standalone.db")
    ), encoding="utf-8")

    with pytest.raises(RuntimeError, match="cover every payload"):
        backup_api["verify_backup"](directory)


def test_verify_rejects_changed_payload_checksum(backup_api, fake_backup):
    directory = fake_backup()
    metadata = directory / "metadata.json"
    metadata.write_text(metadata.read_text() + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        backup_api["verify_backup"](directory)


def test_verify_rejects_missing_critical_archive_member(backup_api, fake_backup):
    directory = fake_backup(omit="usr/bin/toy_ui")

    with pytest.raises(RuntimeError, match="Critical files missing"):
        backup_api["verify_backup"](directory)


def test_verify_rejects_mismatched_critical_archive_content(backup_api, fake_backup):
    directory = fake_backup(wrong_critical=True)

    with pytest.raises(RuntimeError, match="Critical archive content mismatch"):
        backup_api["verify_backup"](directory)


def test_verify_rejects_missing_required_recovery_content(backup_api, fake_backup):
    directory = fake_backup(omit="opt/ai-cat-controller/src/ai_cat_controller/main.py")

    with pytest.raises(RuntimeError, match="Required recovery content missing"):
        backup_api["verify_backup"](directory)


@pytest.mark.parametrize("database_name", [
    "product-data.db", "product-data.db-wal", "product-data.db-shm",
])
def test_verify_rejects_live_database_archive_members(
    backup_api, fake_backup, database_name
):
    directory = fake_backup(live_database=database_name)

    with pytest.raises(RuntimeError, match="Live SQLite database unexpectedly archived"):
        backup_api["verify_backup"](directory)


def test_verify_rejects_sqlite_report_mismatch(backup_api, fake_backup):
    directory = fake_backup()
    metadata_path = directory / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["sqlite"]["table_counts"]["messages"] = 42
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    write_fake_manifest(directory)

    with pytest.raises(RuntimeError, match="SQLite snapshot report mismatch"):
        backup_api["verify_backup"](directory)
