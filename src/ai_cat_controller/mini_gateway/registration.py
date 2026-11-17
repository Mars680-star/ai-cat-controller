"""Explicit local administration only; this is not QR binding or Wi-Fi setup."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
from urllib.parse import quote

from . import status_reader as _status_reader
from .auth import digest
from .cli import read_private_text
from .status_reader import LocalStatusReader, _snapshot
from .store import SessionStore


class RegistrationError(ValueError):
    """Safe registration failure with no bridge/identity diagnostics attached."""


def _outside_package(path: Path) -> bool:
    return not any(part.lower() == "miniprogram" or part.lower().startswith("wechat-miniprogram") for part in path.parts)


def _backup_path(source: Path, destination: Path, *, require_new: bool) -> Path:
    path = Path(destination).absolute()
    if not _outside_package(path) or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Invalid private backup destination")
    if not path.parent.is_dir() or path == source or (path.exists() and os.path.samefile(path, source)):
        raise ValueError("Invalid private backup destination")
    # SQLite reopens the exclusive backup by pathname. A 0600 file is not enough
    # if another user can replace that path through a writable parent/ancestor.
    # A root-owned sticky ancestor such as /tmp is safe only above our own
    # private leaf directory; the leaf itself must never be shared.
    owner = os.getuid()
    leaf = path.parent.stat()
    if leaf.st_uid != owner or leaf.st_mode & 0o077:
        raise ValueError("Backup directory must be private and owned by the operator")
    for directory in path.parent.parents:
        info = directory.stat()
        if info.st_uid not in {0, owner}:
            raise ValueError("Untrusted backup directory ancestor")
        if info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
            raise ValueError("Replaceable backup directory ancestor")
    if path.exists() and (require_new or not path.is_file()):
        raise ValueError("Backup destination already exists")
    return path


def _backup_database(source: Path, destination: Path) -> None:
    """Online backup while another connection holds the registration write lock."""
    path = _backup_path(source, destination, require_new=True)
    inode = None
    complete = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            inode = os.fstat(descriptor).st_ino
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        start = time.monotonic()

        def bounded_progress(_status, _remaining, _total):
            if time.monotonic() - start > 5.0:
                raise ValueError("Backup deadline exceeded")

        source_uri = "file:" + quote(str(source), safe="/") + "?mode=ro"
        target_uri = "file:" + quote(str(path), safe="/") + "?mode=rw"
        with closing(sqlite3.connect(source_uri, uri=True, timeout=3.0)) as original:
            with closing(sqlite3.connect(target_uri, uri=True, timeout=3.0)) as target:
                original.backup(target, pages=128, progress=bounded_progress, sleep=0.01)
                if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("Backup integrity verification failed")
                if target.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                    raise ValueError("Backup journal verification failed")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if info.st_ino != inode or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o777 != 0o600:
                raise ValueError("Backup file verification failed")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        complete = True
    finally:
        if not complete and inode is not None and path.exists() and not path.is_symlink() and path.stat().st_ino == inode:
            path.unlink()  # Only this operation's incomplete, exclusively created backup.


async def register_local_device(store: SessionStore, *, app_id: str, user_id: str,
                                pet_id: str, name: str, expected_serial: str,
                                reader: LocalStatusReader, backup_file: Path | None = None) -> dict:
    """Validate a verified identity/live pinned device, snapshot, then atomically grant.

    Identical existing registrations return registered=False without another write
    or backup. Even that case must still pass the live, pinned device check.
    """
    try:
        if not isinstance(app_id, str) or re.fullmatch(r"wx[0-9a-fA-F]{16}", app_id) is None:
            raise ValueError
        for identifier in (user_id, pet_id):
            if not isinstance(identifier, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier) is None:
                raise ValueError
        if not isinstance(name, str) or not 1 <= len(name) <= 80 or name != name.strip() or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise ValueError
        if not isinstance(expected_serial, str) or re.fullmatch(r"[A-Za-z0-9_-]{6,64}", expected_serial) is None:
            raise ValueError
        if not isinstance(reader, _status_reader.LocalStatusReader) or reader.expected_serial != expected_serial:
            raise ValueError
        if not _outside_package(store.path):
            raise ValueError
        await asyncio.to_thread(store.validate_existing)
        fingerprint = digest(expected_serial)
        existing = await asyncio.to_thread(store.check_registration, app_id, user_id, pet_id, name, fingerprint=fingerprint)
        destination = backup_file if backup_file is not None else store.path.with_name(store.path.stem + ".before-registration-" + secrets.token_hex(8) + ".sqlite3")
        destination = _backup_path(store.path, destination, require_new=not existing)
        observed = await reader.read()
        canonical = _snapshot({"success": True, "data": observed})
        if canonical != observed or canonical["device"]["connected"] is not True or canonical["device"]["deviceSerial"] != expected_serial[:3] + "****" + expected_serial[-4:]:
            raise ValueError
        registered = await asyncio.to_thread(
            store.register_pet_atomically, app_id, user_id, pet_id, name,
            fingerprint=fingerprint,
            before_insert=lambda: _backup_database(store.path, destination),
        )
        return {"petId": pet_id, "name": name, "registered": registered}
    except Exception:
        pass
    raise RegistrationError("Local registration failed; verify the explicit user, device and private backup settings") from None


class _SafeParser(argparse.ArgumentParser):
    def error(self, _message):
        self.exit(2, "Invalid registration arguments; use --help. No argument values were printed.\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeParser(description="Explicit local gateway registration; not QR binding or network provisioning", allow_abbrev=False)
    parser.add_argument("--database", type=Path, required=True)
    for option in ("app-id", "user-id", "pet-id", "name", "expected-serial"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--bridge-token-file", type=Path, required=True)
    parser.add_argument("--backup-file", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        store = SessionStore.open_existing(args.database)
        token = read_private_text(args.bridge_token_file)
        reader = LocalStatusReader(bridge_token=token, expected_serial=args.expected_serial)
        result = asyncio.run(register_local_device(
            store, app_id=args.app_id, user_id=args.user_id, pet_id=args.pet_id, name=args.name,
            expected_serial=args.expected_serial, reader=reader, backup_file=args.backup_file,
        ))
    except (OSError, ValueError):
        parser.exit(2, "Local registration failed; no ownership was reassigned and no private diagnostics were printed.\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
