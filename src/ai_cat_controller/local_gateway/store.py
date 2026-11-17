"""Exclusive, device-pinned local sessions and durable AES nonce allocation."""
from contextlib import closing, contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import time
from urllib.parse import quote

from ai_cat_controller.mini_gateway.auth import IssuedSession, Session, digest


class LocalPairingStore:
    def __init__(self, db_path, *, card, serial, name='本地 AI 伙伴', clock=time.time, initialize=False):
        self.path, self.card, self.serial, self.clock = Path(db_path).absolute(), card, serial, clock
        self.user_id = 'loc_' + card.device_id[4:] + '_' + card.card_id
        self.pet_id, self.name = 'lp_' + card.device_id[4:], name
        self._fd, self._closed = None, False
        if not isinstance(serial, str) or not 6 <= len(serial) <= 64 or not isinstance(name, str) or not 1 <= len(name) <= 80:
            raise ValueError('Invalid local device identity')
        if any(p.is_symlink() for p in (self.path, *self.path.parents)):
            raise ValueError('Private local store required')
        if type(initialize) is not bool or (initialize and self.path.exists()) or (not initialize and not self.path.exists()):
            raise ValueError('Runtime requires the existing commissioned nonce store; rotate a lost card/store')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._private(self.path.parent, directory=True)
        lock_path = Path(str(self.path) + '.lock')
        try:
            self._fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            self._private(lock_path)
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            created = False
            if not self.path.exists():
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                os.close(fd)
                created = True
            self._private(self.path)
            info = self.path.stat()
            self._file_identity = (info.st_dev, info.st_ino)
            if any(Path(str(self.path) + suffix).exists() for suffix in ('-wal', '-shm')):
                raise ValueError('Independent local store required')
            pins = {'schema': 'local_pairing_v1', 'device': card.device_id, 'card': card.card_id,
                'serial': digest(serial), 'key': digest(card.key_hex)}
            with closing(sqlite3.connect('file:' + quote(str(self.path), safe='/') + '?mode=ro&immutable=1', uri=True)) as c:
                tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not created and (tables != {'lg_meta', 'lg_sessions', 'lg_counter'} or
                        dict(c.execute('SELECT key,value FROM lg_meta')) != pins):
                    raise ValueError('Local store identity mismatch')
                if c.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')").fetchone():
                    raise ValueError('Invalid local store')
                if not created:
                    counters = c.execute('SELECT id,value FROM lg_counter').fetchall()
                    if (len(counters) != 1 or counters[0][0] != 1 or type(counters[0][1]) is not int
                            or not 0 <= counters[0][1] < 2**63 - 1):
                        raise ValueError('Existing nonce counter is missing or invalid; rotate the card')
            with self._connect() as c:
                c.executescript('''BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS lg_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS lg_sessions(token_hash TEXT PRIMARY KEY,expires INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS lg_counter(id INTEGER PRIMARY KEY CHECK(id=1),value INTEGER NOT NULL);
                    COMMIT;''')
                if created:
                    c.execute('INSERT INTO lg_counter VALUES(1,0)')
                c.executemany('INSERT OR IGNORE INTO lg_meta VALUES(?,?)', pins.items())
        except Exception:
            self.close()
            raise ValueError('Cannot open the independent private local store') from None

    @staticmethod
    def _private(path, directory=False):
        info = path.stat()
        if (not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) or
                info.st_uid != os.geteuid() or info.st_mode & 0o077 or not directory and info.st_nlink != 1):
            raise ValueError('Private local store required')

    @contextmanager
    def _connect(self):
        if self._closed:
            raise ValueError('Local store is closed')
        if any(p.is_symlink() for p in (self.path, *self.path.parents)):
            raise ValueError('Private local store required')
        self._private(self.path)
        info = self.path.stat()
        if (info.st_dev, info.st_ino) != self._file_identity:
            raise ValueError('Local store identity changed')
        with closing(sqlite3.connect('file:' + quote(str(self.path), safe='/') + '?mode=rw', uri=True, timeout=3)) as c:
            c.execute('PRAGMA synchronous=FULL')
            with c:
                yield c

    def next_counter(self):
        with self._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT value FROM lg_counter WHERE id=1').fetchone()
            if not row or type(row[0]) is not int or not 0 <= row[0] < 2**63 - 1:
                raise ValueError('Local nonce allocation unavailable')
            value = row[0] + 1
            c.execute('UPDATE lg_counter SET value=? WHERE id=1', (value,))
        return value

    def issue_local_session(self):
        now = int(self.clock())
        token = secrets.token_urlsafe(32)
        with self._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('DELETE FROM lg_sessions WHERE expires<=?', (now,))
            if c.execute('SELECT COUNT(*) FROM lg_sessions').fetchone()[0] >= 64:
                raise ValueError('Local pairing limit reached')
            c.execute('INSERT INTO lg_sessions VALUES(?,?)', (digest(token), now + 3600))
        return IssuedSession(self.user_id, now + 3600, token)

    def resolve_session(self, token, app_id):
        if app_id != '' or not isinstance(token, str):
            return None
        with self._connect() as c:
            row = c.execute('SELECT expires FROM lg_sessions WHERE token_hash=? AND expires>?', (digest(token), int(self.clock()))).fetchone()
        return Session(self.user_id, row[0]) if row else None

    def revoke_session(self, token, app_id):
        if app_id == '' and isinstance(token, str):
            with self._connect() as c:
                c.execute('DELETE FROM lg_sessions WHERE token_hash=?', (digest(token),))

    def authorize(self, user_id, pet_id):
        return not self._closed and user_id == self.user_id and pet_id == self.pet_id

    def list_pets(self, user_id):
        return [{'petId': self.pet_id, 'name': self.name}] if user_id == self.user_id and not self._closed else []

    def registered_device_matches(self, pet_id, serial):
        return not self._closed and pet_id == self.pet_id and serial == self.serial

    def user_belongs_to_app(self, user_id, app_id):
        return not self._closed and app_id == '' and user_id == self.user_id

    def close(self):
        self._closed = True
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
