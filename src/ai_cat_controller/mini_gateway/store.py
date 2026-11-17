"""Private SQLite identities, hash-only sessions, consumed codes and ownership.

This store intentionally rejects other application databases instead of migrating
or opening product data. Ownership grants have no public HTTP equivalent.
"""

from collections.abc import Callable
from contextlib import closing, contextmanager
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
from urllib.parse import quote

from .auth import IssuedSession, Session, digest


class CodeConsumed(Exception):
    pass


class LoginRateLimited(Exception):
    pass


_TABLES = {"mg_meta", "mg_users", "mg_sessions", "mg_codes", "mg_login_limits", "mg_pets"}


class SessionStore:
    @classmethod
    def open_existing(cls, db_path: Path, *, clock: Callable[[], float] = time.time):
        """Open the dedicated database for administration without create/migrate."""
        result = cls.__new__(cls)
        result.path = Path(db_path).absolute()
        result.clock = clock
        result.validate_existing()
        return result

    def validate_existing(self) -> None:
        """Read-only identity/schema check; never create a missing database."""
        try:
            if self.path.name.lower() in {"product-data.db", "product.db"} or any(path.is_symlink() for path in (self.path, *self.path.parents)):
                raise ValueError
            info = self.path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError
            if any(Path(str(self.path) + suffix).exists() for suffix in ("-wal", "-shm")):
                raise ValueError
            uri = "file:" + quote(str(self.path), safe="/") + "?mode=ro&immutable=1"
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables != _TABLES or connection.execute("SELECT value FROM mg_meta WHERE key='schema'").fetchone() != ("mini_gateway_v1",):
                    raise ValueError
        except (OSError, sqlite3.Error, ValueError):
            raise ValueError("An existing private gateway database is required") from None

    def __init__(self, db_path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(db_path).absolute()
        self.clock = clock
        if self.path.name.lower() in {"product-data.db", "product.db"} or any(path.is_symlink() for path in (self.path, *self.path.parents)):
            raise ValueError("A separate private gateway database is required")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        created = False
        if not self.path.exists():
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(descriptor)
            created = True
        info = self.path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("Gateway database must be a private regular file")
        # Inspect before any initialization/write, so a mistaken product DB is intact.
        try:
            # Our store uses DELETE journaling. Immutable preflight cannot create
            # another app's WAL/SHM files if the supplied path was a mistake.
            if any(Path(str(self.path) + suffix).exists() for suffix in ("-wal", "-shm")):
                raise ValueError("A dedicated non-WAL gateway database is required")
            uri = "file:" + quote(str(self.path), safe="/") + "?mode=ro&immutable=1"
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not created and tables != _TABLES:
                    raise ValueError("Not a dedicated gateway database")
                if tables and connection.execute("SELECT value FROM mg_meta WHERE key='schema'").fetchone() != ("mini_gateway_v1",):
                    raise ValueError("Not a dedicated gateway database")
            with self._connect() as connection:
                connection.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS mg_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    INSERT OR IGNORE INTO mg_meta VALUES('schema','mini_gateway_v1');
                    CREATE TABLE IF NOT EXISTS mg_users(
                        id TEXT PRIMARY KEY, app_id TEXT NOT NULL, openid TEXT NOT NULL,
                        created_at INTEGER NOT NULL, UNIQUE(app_id,openid));
                    CREATE TABLE IF NOT EXISTS mg_sessions(
                        token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES mg_users(id),
                        created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, revoked_at INTEGER);
                    CREATE TABLE IF NOT EXISTS mg_codes(code_hash TEXT PRIMARY KEY, consumed_at INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS mg_login_limits(client_hash TEXT PRIMARY KEY,
                        window_start INTEGER NOT NULL, attempts INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS mg_pets(user_id TEXT NOT NULL REFERENCES mg_users(id),
                        pet_id TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY(user_id,pet_id));
                    CREATE UNIQUE INDEX IF NOT EXISTS mg_pet_ownership ON mg_pets(pet_id);
                    COMMIT;
                """)
        except sqlite3.Error:
            raise ValueError("Cannot initialize dedicated gateway database") from None

    @contextmanager
    def _connect(self):
        uri = "file:" + quote(str(self.path), safe="/") + "?mode=rw"
        with closing(sqlite3.connect(uri, uri=True, timeout=3.0)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            with connection:
                yield connection

    def consume_code(self, app_id: str, code: str, client: str) -> None:
        now = int(self.clock())
        client_hash = digest(app_id + "\0" + client)
        code_hash = digest(app_id + "\0" + code)
        outcome = None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT window_start,attempts FROM mg_login_limits WHERE client_hash=?", (client_hash,)).fetchone()
            if row and 0 <= now - row[0] < 60 and row[1] >= 10:
                outcome = LoginRateLimited
            else:
                start, count = (row[0], row[1] + 1) if row and 0 <= now - row[0] < 60 else (now, 1)
                connection.execute("INSERT INTO mg_login_limits VALUES(?,?,?) ON CONFLICT(client_hash) DO UPDATE SET window_start=excluded.window_start,attempts=excluded.attempts", (client_hash, start, count))
                inserted = connection.execute("INSERT OR IGNORE INTO mg_codes VALUES(?,?)", (code_hash, now)).rowcount
                if not inserted:
                    outcome = CodeConsumed
        if outcome:
            raise outcome()

    def issue_session(self, app_id: str, openid: str, ttl_seconds: int) -> IssuedSession:
        if not isinstance(openid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", openid):
            raise ValueError("Invalid identity provider result")
        now = int(self.clock())
        token = secrets.token_urlsafe(32)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT OR IGNORE INTO mg_users VALUES(?,?,?,?)", ("usr_" + secrets.token_urlsafe(18), app_id, openid, now))
            user_id = connection.execute("SELECT id FROM mg_users WHERE app_id=? AND openid=?", (app_id, openid)).fetchone()[0]
            connection.execute("INSERT INTO mg_sessions VALUES(?,?,?,?,NULL)", (digest(token), user_id, now, now + ttl_seconds))
        return IssuedSession(user_id=user_id, expires_at=now + ttl_seconds, access_token=token)

    def resolve_session(self, token: str, app_id: str) -> Session | None:
        with self._connect() as connection:
            row = connection.execute("SELECT s.user_id,s.expires_at FROM mg_sessions s JOIN mg_users u ON u.id=s.user_id WHERE s.token_hash=? AND u.app_id=? AND s.revoked_at IS NULL AND s.expires_at>?", (digest(token), app_id, int(self.clock()))).fetchone()
        return Session(*row) if row else None

    def revoke_session(self, token: str, app_id: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE mg_sessions SET revoked_at=? WHERE token_hash=? AND user_id IN (SELECT id FROM mg_users WHERE app_id=?)", (int(self.clock()), digest(token), app_id))

    def grant_pet(self, user_id: str, pet_id: str, name: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", pet_id) or not isinstance(name, str) or not 1 <= len(name) <= 80 or any(ord(c) < 32 for c in name):
            raise ValueError("Invalid internal ownership grant")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            owner = connection.execute("SELECT user_id FROM mg_pets WHERE pet_id=?", (pet_id,)).fetchone()
            if owner and owner[0] != user_id:
                raise ValueError("Pet already has an owner")
            connection.execute("INSERT INTO mg_pets VALUES(?,?,?) ON CONFLICT(user_id,pet_id) DO UPDATE SET name=excluded.name", (user_id, pet_id, name))

    def revoke_pet(self, user_id: str, pet_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM mg_pets WHERE user_id=? AND pet_id=?", (user_id, pet_id))

    def list_pets(self, user_id: str) -> list[dict[str, str]]:
        with self._connect() as connection:
            return [{"petId": row[0], "name": row[1]} for row in connection.execute("SELECT pet_id,name FROM mg_pets WHERE user_id=? ORDER BY pet_id", (user_id,))]

    def authorize(self, user_id: str, pet_id: str) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1 FROM mg_pets WHERE user_id=? AND pet_id=?", (user_id, pet_id)).fetchone() is not None

    def user_belongs_to_app(self, user_id: str, app_id: str) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1 FROM mg_users WHERE id=? AND app_id=?", (user_id, app_id)).fetchone() is not None

    @staticmethod
    def _registration_state(connection, app_id: str, user_id: str, pet_id: str, name: str,
                            fingerprint: str | None = None) -> bool:
        if connection.execute("SELECT 1 FROM mg_users WHERE id=? AND app_id=?", (user_id, app_id)).fetchone() is None:
            raise ValueError("An existing user from the configured app is required")
        existing = connection.execute("SELECT user_id,name FROM mg_pets WHERE pet_id=?", (pet_id,)).fetchone()
        if existing is not None and existing != (user_id, name):
            raise ValueError("Registration cannot replace an existing owner or name")
        if fingerprint is not None:
            recorded = connection.execute("SELECT value FROM mg_meta WHERE key=?", ("device:" + pet_id,)).fetchone()
            if (existing is not None and recorded != (fingerprint,)) or (recorded is not None and recorded != (fingerprint,)):
                raise ValueError("Registration cannot replace or infer a device identity")
            if connection.execute("SELECT 1 FROM mg_meta m JOIN mg_pets p ON m.key=('device:' || p.pet_id) WHERE m.value=? AND p.pet_id<>? LIMIT 1", (fingerprint, pet_id)).fetchone():
                raise ValueError("The physical device already has a registration")
        return existing is not None

    def check_registration(self, app_id: str, user_id: str, pet_id: str, name: str,
                           *, fingerprint: str | None = None) -> bool:
        with self._connect() as connection:
            return self._registration_state(connection, app_id, user_id, pet_id, name, fingerprint)

    def register_pet_atomically(self, app_id: str, user_id: str, pet_id: str, name: str,
                                *, fingerprint: str, before_insert: Callable[[], None]) -> bool:
        """Recheck identity/ownership under the same lock as backup and insert."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._registration_state(connection, app_id, user_id, pet_id, name, fingerprint):
                return False
            before_insert()
            connection.execute("INSERT INTO mg_pets VALUES(?,?,?)", (user_id, pet_id, name))
            connection.execute("INSERT INTO mg_meta VALUES(?,?) ON CONFLICT(key) DO NOTHING", ("device:" + pet_id, fingerprint))
        return True

    def registered_device_matches(self, pet_id: str, expected_serial: str) -> bool:
        if not isinstance(expected_serial, str) or re.fullmatch(r"[A-Za-z0-9_-]{6,64}", expected_serial) is None:
            return False
        with self._connect() as connection:
            recorded = connection.execute("SELECT value FROM mg_meta WHERE key=?", ("device:" + pet_id,)).fetchone()
        return recorded is not None and isinstance(recorded[0], str) and secrets.compare_digest(recorded[0], digest(expected_serial))
