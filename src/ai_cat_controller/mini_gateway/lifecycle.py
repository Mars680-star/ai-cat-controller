"""Fail-closed lifecycle confirmation and independent private intent ledger.

This module is NOT a device verifier or an HTTP adapter for legacy dangerous
routes. A trusted deployment component must supply an executor whose probe and
execute methods perform the real controlled-maintenance protocol. By default no
executor exists, no proof is accepted from client JSON, and every write is shut.
"""

import asyncio
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
from typing import Protocol
from urllib.parse import quote

from .lifecycle_models import Challenge, Command, Receipt, SCOPES, ScopeRequest, Snapshot, validate_lifecycle

_ID = re.compile(r'[A-Za-z0-9_-]{1,128}')
_HEX = re.compile(r'[0-9a-f]{64}')
_CHALLENGE_TTL = 60.0
_PROOF_TTL = 5.0
_MAX_CHALLENGES = 32
_SCHEMA = {
    'lc_meta': [('key', 'TEXT', 0, 1), ('value', 'TEXT', 1, 0)],
    'lc_requests': [('user_id', 'TEXT', 1, 1), ('pet_id', 'TEXT', 1, 2),
                    ('request_id', 'TEXT', 1, 3), ('kind', 'TEXT', 1, 0),
                    ('scope', 'TEXT', 1, 0), ('digest', 'TEXT', 1, 0),
                    ('status', 'TEXT', 1, 0), ('updated_at', 'TEXT', 1, 0)],
}


class LifecycleError(RuntimeError):
    def __init__(self, status=503, code='LIFECYCLE_UNAVAILABLE', message='数据操作条件尚未验证，本次未发送。'):
        super().__init__(message)
        self.status_code, self.code = status, code


@dataclass(frozen=True)
class LifecycleProof:
    """Private opaque evidence, produced only by a trusted server-side executor.

    Holding this type alone does not prove a device is ready. The deployment
    adapter must bind evidence to live peer/cohort identity and reverify it inside
    actual device maintenance. Never deserialize this object from an HTTP body.
    """
    nonce: str
    kind: str
    scope: str
    serial_sha256: str
    owner_sha256: str
    release_sha256: str
    revision_sha256: str
    expires_at: float
    evidence: object


@dataclass(frozen=True)
class LifecycleOutcome:
    request_id: str
    kind: str
    scope: str
    status: str


class LifecycleExecutor(Protocol):
    async def probe(self, *, nonce: str, user_id: str, pet_id: str, kind: str, scope: str) -> LifecycleProof:
        """Verify current controlled cohort and issue a <=5s private proof."""

    async def execute(self, *, proof: LifecycleProof, user_id: str, pet_id: str,
                      request_id: str, kind: str, scope: str) -> LifecycleOutcome:
        """Reverify inside real maintenance; never replay an uncertain intent."""


def _require(value):
    if not value:
        raise ValueError('Unverified lifecycle conditions')


def _utc():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


async def _call(function, **kwargs):
    result = function(**kwargs)
    return await result if inspect.isawaitable(result) else result


class LocalLifecycleService:
    def __init__(self, *, db_path, expected_serial, product_pet_id,
                 expected_owner_sha256=None, enabled_kinds=(), executor=None,
                 operator_check=None, retire_authorization=None, clock=time.monotonic):
        _require(isinstance(expected_serial, str) and re.fullmatch(r'[A-Za-z0-9_-]{6,64}', expected_serial))
        _require(isinstance(product_pet_id, str) and _ID.fullmatch(product_pet_id))
        _require(expected_owner_sha256 is None or isinstance(expected_owner_sha256, str) and _HEX.fullmatch(expected_owner_sha256))
        _require(isinstance(enabled_kinds, (tuple, list, set, frozenset)) and
                 all(isinstance(kind, str) and kind in SCOPES for kind in enabled_kinds))
        self.path = Path(db_path).absolute()
        self.expected_serial = expected_serial
        self._serial = hashlib.sha256(expected_serial.encode()).hexdigest()
        self._product_pet, self._owner = product_pet_id, expected_owner_sha256
        self.enabled_kinds = frozenset(enabled_kinds)
        self._executor, self._operator, self._retire = executor, operator_check, retire_authorization
        self._clock, self._closed, self._lock_descriptor = clock, False, None
        self._tasks, self._signatures, self._challenges = {}, {}, {}
        self._prepare_store()

    @property
    def execution_configured(self):
        """Configuration only, never a device/cohort readiness assertion."""
        return bool(self.enabled_kinds and self._executor is not None and self._owner is not None)

    def bind_retirement_handler(self, callback):
        """Bind the gateway registry before serving; no client-selectable hook."""
        _require(callable(callback) and not self._tasks and not self._challenges and not self._closed)
        self._retire = callback

    def close(self):
        if any(not task.done() for task in self._tasks.values()):
            raise RuntimeError('Lifecycle submissions must settle before closing')
        self._closed = True
        self._challenges.clear()
        descriptor, self._lock_descriptor = self._lock_descriptor, None
        if descriptor is not None:
            os.close(descriptor)

    async def aclose(self):
        if self._tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in list(self._tasks.values())), return_exceptions=True)
        self.close()

    def __del__(self):
        descriptor = getattr(self, '_lock_descriptor', None)
        if descriptor is not None:
            os.close(descriptor)

    def _connect(self):
        if self._closed:
            raise LifecycleError()
        connection = sqlite3.connect(self.path, timeout=1)
        connection.row_factory = sqlite3.Row
        return connection

    def _prepare_store(self):
        try:
            _require(not any(path.is_symlink() for path in (self.path, *self.path.parents)))
            _require(not any(part.lower() in {'miniprogram', 'wechat-miniprogram'} for part in self.path.parts))
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            info = self.path.parent.stat()
            _require(info.st_uid == os.getuid() and not info.st_mode & 0o077)
            for parent in self.path.parent.parents:
                info = parent.stat()
                _require(info.st_uid in (0, os.getuid()) and
                         (not info.st_mode & 0o022 or info.st_uid == 0 and info.st_mode & stat.S_ISVTX))
            descriptor = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            self._lock_descriptor = descriptor
            info = os.fstat(descriptor)
            _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1 and info.st_size == 0 and not info.st_mode & 0o077)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            created = not self.path.exists()
            descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(descriptor)
                _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1 and not info.st_mode & 0o077)
            finally:
                os.close(descriptor)
            _require(not any(Path(str(self.path) + suffix).exists() for suffix in ('-wal', '-shm')))
            pin = hashlib.sha256(json.dumps([self._serial, self._product_pet, self._owner], separators=(',', ':')).encode()).hexdigest()
            if not created:
                uri = 'file:' + quote(str(self.path), safe='/') + '?mode=ro&immutable=1'
                with closing(sqlite3.connect(uri, uri=True)) as connection:
                    _require({row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")} == set(_SCHEMA))
                    _require(not connection.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')").fetchone())
                    for table, expected in _SCHEMA.items():
                        _require([(row[1], row[2], row[3], row[5]) for row in connection.execute('PRAGMA table_info(' + table + ')')] == expected)
                    _require(connection.execute("SELECT value FROM lc_meta WHERE key='schema'").fetchone() == ('lifecycle_v1',))
                    _require(connection.execute("SELECT value FROM lc_meta WHERE key='pin'").fetchone() == (pin,))
            with closing(self._connect()) as connection, connection:
                if created:
                    connection.executescript('''
                        CREATE TABLE lc_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                        CREATE TABLE lc_requests(user_id TEXT NOT NULL,pet_id TEXT NOT NULL,
                            request_id TEXT NOT NULL,kind TEXT NOT NULL,scope TEXT NOT NULL,
                            digest TEXT NOT NULL,status TEXT NOT NULL,updated_at TEXT NOT NULL,
                            PRIMARY KEY(user_id,pet_id,request_id));
                    ''')
                    connection.executemany('INSERT INTO lc_meta VALUES(?,?)',
                        [('schema', 'lifecycle_v1'), ('pin', pin), ('retired', '0')])
                connection.execute("UPDATE lc_requests SET status='unknown',updated_at=? WHERE status='sending'", (_utc(),))
        except Exception:
            self.close()
            raise ValueError('A separate private lifecycle intent ledger is required') from None

    def _find(self, key):
        with closing(self._connect()) as connection:
            return connection.execute('SELECT * FROM lc_requests WHERE user_id=? AND pet_id=? AND request_id=?', key).fetchone()

    def _review(self):
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT user_id,pet_id,request_id,status FROM lc_requests WHERE status IN ('sending','unknown')").fetchall()
        return any(row['status'] == 'unknown' or (row['user_id'], row['pet_id'], row['request_id']) not in self._tasks for row in rows)

    def _retired(self):
        with closing(self._connect()) as connection:
            value = connection.execute("SELECT value FROM lc_meta WHERE key='retired'").fetchone()
        _require(value is not None and value[0] in {'0', '1'})
        return value[0] == '1'

    @staticmethod
    def _identity(user_id, pet_id):
        _require(isinstance(user_id, str) and _ID.fullmatch(user_id) and isinstance(pet_id, str) and _ID.fullmatch(pet_id))

    async def _authenticate(self, callback):
        try:
            result = await asyncio.wait_for(_call(callback), _PROOF_TTL)
            _require(result is None)
        except Exception:
            raise LifecycleError(401, 'LIFECYCLE_AUTH_REQUIRED', '请重新验证微信登录。') from None

    async def _authorize(self, callback):
        try:
            result = await asyncio.wait_for(_call(callback), _PROOF_TTL)
            _require(result is None)
        except Exception:
            raise LifecycleError(403, 'LIFECYCLE_FORBIDDEN', '当前设备归属未验证，本次未发送。') from None

    async def _operator_allowed(self, user_id, pet_id, kind):
        if kind != 'product_reset':
            return True
        if self._operator is None:
            return False
        try:
            return await asyncio.wait_for(_call(self._operator, user_id=user_id, pet_id=pet_id, kind=kind), _PROOF_TTL) is True
        except Exception:
            return False

    def _validate_proof(self, proof, nonce, kind):
        try:
            _require(isinstance(proof, LifecycleProof) and proof.evidence is not None)
            _require(proof.nonce == nonce and proof.kind == kind and proof.scope == SCOPES[kind])
            _require(all(isinstance(value, str) and _HEX.fullmatch(value) for value in
                         (proof.serial_sha256, proof.owner_sha256, proof.release_sha256, proof.revision_sha256)))
            _require(proof.serial_sha256 == self._serial and self._owner is not None and proof.owner_sha256 == self._owner)
            now = self._clock()
            _require(type(proof.expires_at) in (int, float) and math.isfinite(proof.expires_at) and now < proof.expires_at <= now + _PROOF_TTL)
            return proof
        except Exception:
            raise LifecycleError(503, 'LIFECYCLE_NOT_READY', '设备安全执行组件尚未验证，本次未发送。') from None

    async def _probe(self, user_id, pet_id, kind):
        if self._executor is None or self._owner is None:
            raise LifecycleError(503, 'LIFECYCLE_NOT_READY', '设备安全执行组件尚未验证，本次未发送。')
        nonce = secrets.token_urlsafe(32)
        try:
            proof = await asyncio.wait_for(self._executor.probe(nonce=nonce, user_id=user_id,
                pet_id=pet_id, kind=kind, scope=SCOPES[kind]), _PROOF_TTL)
            return self._validate_proof(proof, nonce, kind)
        except Exception:
            raise LifecycleError(503, 'LIFECYCLE_NOT_READY', '设备安全执行组件尚未验证，本次未发送。') from None

    def _enabled(self, kind):
        return (kind in self.enabled_kinds and self._executor is not None and self._owner is not None
                and (kind == 'memory_clear' or self._retire is not None) and not self._retired())

    async def snapshot(self, *, user_id, pet_id, authenticate, authorize):
        self._identity(user_id, pet_id)
        await self._authenticate(authenticate)
        await self._authorize(authorize)
        review, busy, operations = self._review(), bool(self._tasks), []
        verified = False
        for kind, scope in SCOPES.items():
            available = False
            if self._enabled(kind) and not review and not busy and await self._operator_allowed(user_id, pet_id, kind):
                try:
                    await self._probe(user_id, pet_id, kind)
                    await self._authenticate(authenticate)
                    await self._authorize(authorize)
                    verified, available = True, True
                except LifecycleError:
                    pass
            operations.append(dict(kind=kind, scope=scope, enabled=kind in self.enabled_kinds,
                                   available=available, operatorRequired=kind == 'product_reset'))
        # A later probe/auth check must not leave an earlier operation apparently
        # available after the caller's identity was revoked during this read.
        await self._authenticate(authenticate)
        await self._authorize(authorize)
        reason = ('存在结果未确认的数据操作，请仅核对原编号。' if review else
                  '数据操作正在处理，请勿重复提交。' if busy else
                  '原设备授权已撤销，仅可查询自己的原请求回执。' if self._retired() else
                  '设备安全条件或独立管理授权未验证，操作保持禁用。' if not verified else
                  '仅开放已核验的操作，每次执行须独立确认。')
        return validate_lifecycle(Snapshot, dict(verified=verified, operations=operations,
            manualReviewRequired=review, reason=reason, sampledAt=_utc()))

    async def challenge(self, *, user_id, pet_id, kind, scope, authenticate, authorize):
        self._identity(user_id, pet_id)
        ScopeRequest.model_validate(dict(kind=kind, scope=scope))
        await self._authenticate(authenticate)
        await self._authorize(authorize)
        if self._executor is None or self._owner is None:
            raise LifecycleError(503, 'LIFECYCLE_NOT_READY', '设备安全执行组件尚未验证，本次未发送。')
        if not self._enabled(kind) or self._review() or self._tasks or not await self._operator_allowed(user_id, pet_id, kind):
            raise LifecycleError(409, 'LIFECYCLE_NOT_READY', '操作尚未开放或仍有待核查请求，本次未发送。')
        proof = await self._probe(user_id, pet_id, kind)
        await self._authenticate(authenticate)
        await self._authorize(authorize)
        if not await self._operator_allowed(user_id, pet_id, kind):
            raise LifecycleError(403, 'LIFECYCLE_OPERATOR_REQUIRED', '缺少独立管理授权，本次未发送。')
        self._validate_proof(proof, proof.nonce, kind)
        now = self._clock()
        self._challenges = {key: value for key, value in self._challenges.items() if value['expires'] > now}
        if len(self._challenges) >= _MAX_CHALLENGES:
            raise LifecycleError(429, 'LIFECYCLE_CONFIRM_LIMIT', '确认过于频繁，请稍后重试。')
        token = secrets.token_urlsafe(32)
        hashed = hashlib.sha256(token.encode()).hexdigest()
        self._challenges[hashed] = dict(user=user_id, pet=pet_id, kind=kind, scope=scope,
            revision=proof.revision_sha256, release=proof.release_sha256, expires=now + _CHALLENGE_TTL)
        expires = (datetime.now(timezone.utc) + timedelta(seconds=_CHALLENGE_TTL)).isoformat().replace('+00:00', 'Z')
        return validate_lifecycle(Challenge, dict(kind=kind, scope=scope, challengeId=token,
                                                   confirmVersion=1, expiresAt=expires))

    def _receipt(self, key):
        row = self._find(key)
        if row is None:
            raise LifecycleError(404, 'LIFECYCLE_REQUEST_NOT_FOUND', '尚未找到原请求回执；不能据此判断未执行。')
        return validate_lifecycle(Receipt, dict(requestId=row['request_id'], kind=row['kind'],
            scope=row['scope'], status=row['status'], updatedAt=row['updated_at']))

    async def receipt(self, *, user_id, pet_id, request_id, authenticate):
        self._identity(user_id, pet_id)
        _require(isinstance(request_id, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}', request_id))
        await self._authenticate(authenticate)
        # Deliberately no pet-ownership check: unbind/reset revoke that ownership.
        # The authenticated subject can read only its own original tuple.
        return self._receipt((user_id, pet_id, request_id))

    async def submit(self, *, user_id, pet_id, command, authenticate, authorize):
        self._identity(user_id, pet_id)
        body = Command.model_validate(command)
        await self._authenticate(authenticate)
        key = user_id, pet_id, body.requestId
        digest = hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        prior = self._find(key)
        if prior is not None:
            if not secrets.compare_digest(prior['digest'], digest):
                raise LifecycleError(409, 'LIFECYCLE_CONFLICT', '同一请求编号不能更换操作或确认。')
            return self._receipt(key)
        if key in self._tasks:
            if not secrets.compare_digest(self._signatures[key], digest):
                raise LifecycleError(409, 'LIFECYCLE_CONFLICT', '同一请求编号不能更换操作或确认。')
            return await asyncio.shield(self._tasks[key])
        if self._executor is None or self._owner is None:
            raise LifecycleError(503, 'LIFECYCLE_NOT_READY', '设备安全执行组件尚未验证，本次未发送。')
        if self._tasks or self._review() or not self._enabled(body.kind):
            raise LifecycleError(409, 'LIFECYCLE_NOT_READY', '操作未开放或有待核查请求，本次未发送。')
        task = asyncio.create_task(self._submit(key, body, digest, authenticate, authorize))
        self._tasks[key], self._signatures[key] = task, digest
        def done(future):
            self._tasks.pop(key, None)
            self._signatures.pop(key, None)
            if not future.cancelled():
                future.exception()
        task.add_done_callback(done)
        return await asyncio.shield(task)

    async def _submit(self, key, command, digest, authenticate, authorize):
        hashed = hashlib.sha256(command.challengeId.encode()).hexdigest()
        prepared = self._challenges.get(hashed)
        if not prepared or (prepared['user'], prepared['pet'], prepared['kind'], prepared['scope']) != (*key[:2], command.kind, command.scope) or self._clock() >= prepared['expires']:
            raise LifecycleError(409, 'LIFECYCLE_CONFIRM_EXPIRED', '确认已失效，本次未发送，请重新确认。')
        # One explicit confirmation permits one attempt, including a rejected
        # preflight. Restoring permissions later must not revive this old intent.
        self._challenges.pop(hashed, None)
        await self._authorize(authorize)
        if not await self._operator_allowed(*key[:2], command.kind):
            raise LifecycleError(403, 'LIFECYCLE_OPERATOR_REQUIRED', '缺少独立管理授权，本次未发送。')
        proof = await self._probe(*key[:2], command.kind)
        await self._authenticate(authenticate)
        await self._authorize(authorize)
        if not await self._operator_allowed(*key[:2], command.kind):
            raise LifecycleError(403, 'LIFECYCLE_OPERATOR_REQUIRED', '缺少独立管理授权，本次未发送。')
        self._validate_proof(proof, proof.nonce, command.kind)
        if (not self._enabled(command.kind) or self._review() or self._clock() >= prepared['expires'] or
                proof.revision_sha256 != prepared['revision'] or proof.release_sha256 != prepared['release']):
            raise LifecycleError(409, 'LIFECYCLE_CONFIRM_CHANGED', '确认期间设备或安全条件已变化，本次未发送。')
        with closing(self._connect()) as connection, connection:
            connection.execute("INSERT INTO lc_requests VALUES(?,?,?,?,?,?,'sending',?)",
                               (*key, command.kind, command.scope, digest, _utc()))
        # Tokens/proofs are never persisted and cannot survive a gateway restart.
        status = 'unknown'
        try:
            outcome = await self._executor.execute(proof=proof, user_id=key[0], pet_id=key[1],
                request_id=key[2], kind=command.kind, scope=command.scope)
            _require(isinstance(outcome, LifecycleOutcome) and
                     (outcome.request_id, outcome.kind, outcome.scope) == (key[2], command.kind, command.scope) and
                     outcome.status in {'completed', 'not_sent', 'unknown'})
            status = outcome.status
            if status == 'completed' and command.kind in {'unbind', 'product_reset'}:
                await _call(self._retire, user_id=key[0], pet_id=key[1], kind=command.kind)
        except BaseException:
            status = 'unknown'
        with closing(self._connect()) as connection, connection:
            connection.execute('UPDATE lc_requests SET status=?,updated_at=? WHERE user_id=? AND pet_id=? AND request_id=?', (status, _utc(), *key))
            if status == 'completed' and command.kind in {'unbind', 'product_reset'}:
                connection.execute("UPDATE lc_meta SET value='1' WHERE key='retired'")
        return self._receipt(key)
