"""Hash-only, binding-scoped authority for a device's local sidecar.

This is NOT a WeChat or webpage session, and never assigns product ownership.
The caller must re-read the real product binding before every resolution.
"""
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
from urllib.parse import quote

_TABLES = {'lg_meta', 'lg_delegation'}
_SCHEMA = 'local_gateway_delegation_v1'
_ID = re.compile(r'[A-Za-z0-9_-]{1,128}')
_SERIAL = re.compile(r'[A-Za-z0-9_-]{6,64}')
_TOKEN = re.compile(r'lgd_[A-Za-z0-9_-]{43}')
_ACTIONS = '(?:head_nod|head_shake|tail_wag|proud_pose|quiet_companion|greeting_combo|celebration_combo)'


def _require(value):
    if not value:
        raise ValueError('Local gateway authorization is unavailable')


@dataclass(frozen=True)
class LocalPrincipal:
    user_id: str
    pet_id: str
    serial: str
    binding_epoch: str


def _binding(value):
    _require(type(value) is dict)
    owner, pet, serial, epoch = (value.get(k) for k in ('owner_user_id','pet_id','serial_number','bound_at'))
    _require(isinstance(owner,str) and _ID.fullmatch(owner) and isinstance(pet,str) and _ID.fullmatch(pet))
    _require(isinstance(serial,str) and _SERIAL.fullmatch(serial))
    _require(isinstance(epoch,str) and 1 <= len(epoch) <= 64 and all(ord(c) >= 32 for c in epoch))
    _require(datetime.fromisoformat(epoch.replace('Z','+00:00')).tzinfo is not None)
    return LocalPrincipal(owner, pet, serial, epoch)


def allowed_route(principal, method, path):
    """Exact device-side routes needed by existing bounded business bridges."""
    if (not isinstance(path,str) or '%' in path or '?' in path or '#' in path or
            any(segment in {'.','..'} for segment in path.split('/'))):
        return False
    base = '/api/v1/pets/' + principal.pet_id
    if method == 'GET':
        return (path in {'/api/v1/device/status','/api/v1/dialog/status','/api/v1/dialog/config',
            '/api/v1/pets',base+'/dashboard',base+'/intimacy',base+'/growth',base+'/actions',
            base+'/actions/executions',base+'/dialog-readonly/status',base+'/dialog-readonly/conversations'}
            or bool(re.fullmatch(re.escape(base) + r'/dialog-readonly/conversations/[A-Za-z0-9_.:-]{4,256}',path)))
    if method == 'POST':
        return (path in {'/api/v1/motion/stop','/api/v1/dialog/wake','/api/v1/dialog/interrupt',
            '/api/v1/dialog/text',base+'/interactions',base+'/feedback'}
            or bool(re.fullmatch(re.escape(base)+'/actions/'+_ACTIONS+'/execute',path)))
    return method == 'PATCH' and path in {'/api/v1/dialog/config',base+'/settings'}


def bridge_authorization(upstream_url, product_session, local_delegation, *, required=True):
    """Keep webpage and local-server credential domains mutually exclusive."""
    _require(not (product_session is not None and local_delegation is not None))
    if local_delegation is not None:
        _require(upstream_url == 'http://127.0.0.1:8000' and
                 isinstance(local_delegation,str) and _TOKEN.fullmatch(local_delegation))
        return 'X-Local-Gateway',local_delegation
    if product_session is None:
        _require(not required)
        return None,None
    _require(isinstance(product_session,str) and re.fullmatch(r'[A-Za-z0-9_-]{16,256}',product_session))
    return 'X-Mock-Session',product_session


class DelegationStore:
    def __init__(self, path):
        self.path = Path(path)
        _require(self.path.is_absolute() and not any(p.is_symlink() for p in (self.path,*self.path.parents)))
        _require(not any(p.lower() in {'miniprogram','wechat-miniprogram'} for p in self.path.parts))
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = self.path.parent.stat()
        _require(parent.st_uid == os.getuid() and not parent.st_mode & 0o077)
        created = False
        if not self.path.exists():
            fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
            os.close(fd); created=True
        self._identity = self._private_identity()
        _require(not any(Path(str(self.path)+s).exists() for s in ('-wal','-shm')))
        uri='file:'+quote(str(self.path),safe='/')+'?mode=ro&immutable=1'
        try:
            with closing(sqlite3.connect(uri,uri=True)) as db:
                tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                _require(created or tables == _TABLES)
                if tables:
                    _require(tables == _TABLES and db.execute("SELECT value FROM lg_meta WHERE key='schema'").fetchone()==(_SCHEMA,))
                _require(not db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')").fetchone())
            with self._connect() as db:
                db.executescript("""BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS lg_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS lg_delegation(
                        slot INTEGER PRIMARY KEY CHECK(slot=1), token_hash TEXT NOT NULL,
                        owner TEXT NOT NULL, pet TEXT NOT NULL, serial TEXT NOT NULL,
                        binding_epoch TEXT NOT NULL, created_at INTEGER NOT NULL);
                    COMMIT;""")
                db.execute("INSERT OR IGNORE INTO lg_meta VALUES('schema',?)",(_SCHEMA,))
        except sqlite3.Error:
            raise ValueError('Local gateway authorization store is invalid') from None

    def _private_identity(self):
        _require(not any(p.is_symlink() for p in (self.path,*self.path.parents)))
        info=self.path.stat()
        _require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and info.st_nlink==1 and not info.st_mode & 0o077)
        return info.st_dev,info.st_ino

    @contextmanager
    def _connect(self):
        _require(self._private_identity()==self._identity)
        try:
            with closing(sqlite3.connect('file:'+quote(str(self.path),safe='/')+'?mode=rw',uri=True,timeout=3)) as db:
                with db:
                    yield db
            _require(self._private_identity()==self._identity)
        except sqlite3.Error:
            raise ValueError('Local gateway authorization store is unavailable') from None

    def issue(self, binding):
        principal=_binding(binding)
        token='lgd_'+secrets.token_urlsafe(32)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR REPLACE INTO lg_delegation VALUES(1,?,?,?,?,?,?)',
                (hashlib.sha256(token.encode()).hexdigest(),principal.user_id,principal.pet_id,
                 principal.serial,principal.binding_epoch,int(time.time())))
        return token

    def resolve(self, token, binding, method, path):
        _require(isinstance(token,str) and _TOKEN.fullmatch(token))
        principal=_binding(binding)
        _require(allowed_route(principal,method,path))
        with self._connect() as db:
            row=db.execute('SELECT owner,pet,serial,binding_epoch FROM lg_delegation WHERE slot=1 AND token_hash=?',
                (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        _require(row is not None and all(secrets.compare_digest(a,b) for a,b in zip(row,
            (principal.user_id,principal.pet_id,principal.serial,principal.binding_epoch))))
        return principal

    def revoke(self, binding):
        principal=_binding(binding)
        with self._connect() as db:
            db.execute('DELETE FROM lg_delegation WHERE owner=? AND pet=? AND serial=? AND binding_epoch=?',
                (principal.user_id,principal.pet_id,principal.serial,principal.binding_epoch))
