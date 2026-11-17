"""Durable no-TTY coordination of the existing encrypted SoftAP protocol.

Only a trusted caller that has already authenticated the device's physical card
and a LocalPrincipal may call this module. It is not an HTTP server, discovery
endpoint, physical-presence proof or authorization mechanism. prepare() returns
the private invitation to that caller WITHOUT changing any network. The caller
must send its encrypted response before running start() as a background task.

The old private-terminal CLI is unchanged. All target Wi-Fi changes still use
ProvisioningWindow.start_pending() after the TCP acceptance has actually drained.
Target SSIDs/passwords are never stored here; original invitations and encrypted
final receipts remain in one exclusive private device-pinned SQLite ledger.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import closing, contextmanager
import fcntl
import inspect
import logging
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
from urllib.parse import quote

from ai_cat_controller.provisioning.cli import LOCAL_TEST_AP_PASSWORD, diagnostic_line
from ai_cat_controller.provisioning.nm_backend import NetworkManagerBackend, CHECKPOINT_SETTLE_SECONDS
from .provision_reviews import ProvisionReviewLedger, acknowledgements, digest
from ai_cat_controller.provisioning.protocol import (
    DEVICE_ID, REQUEST_ID, Invitation, decode_packet, parse_json,
)
from ai_cat_controller.provisioning.server import ProvisioningTCPServer
from ai_cat_controller.provisioning.window import ProvisioningWindow


WINDOW_SECONDS = 180
LOGGER = logging.getLogger(__name__)
MAX_WINDOWS = 256
_ACTIVE = {'prepared', 'opening', 'open', 'accepted', 'connecting', 'unknown'}
_TERMINAL = {'connected', 'failed', 'not_sent', 'unknown'}
_ALL = _ACTIVE | _TERMINAL
_SCHEMA = {
    'lp_meta': [('key', 'TEXT', 0, 1), ('value', 'TEXT', 1, 0)],
    'lp_requests': [('request_id', 'TEXT', 0, 1), ('device_id', 'TEXT', 1, 0),
        ('window_id', 'TEXT', 1, 0), ('invitation', 'TEXT', 1, 0),
        ('status', 'TEXT', 1, 0), ('ap_name', 'TEXT', 1, 0),
        ('created', 'REAL', 1, 0), ('deadline', 'REAL', 1, 0),
        ('network_request_id', 'TEXT', 0, 0), ('receipt_qr', 'TEXT', 0, 0),
        ('updated', 'REAL', 1, 0)],
}


class LocalProvisioningError(RuntimeError):
    def __init__(self, status=503, code='LOCAL_PROVISION_UNAVAILABLE'):
        super().__init__('Local provisioning is unavailable; no request was retried')
        self.status_code, self.code = status, code


class _OfflineGuardDeclined(Exception):
    """Private boot preflight declined before any network-capable operation."""


def _require(value):
    if not value:
        raise LocalProvisioningError()


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


class _RecordedWindow:
    """Persist acceptance before the existing TCP server can expose its reply."""
    def __init__(self, window, coordinator, request_id, invitation):
        self.window, self.coordinator = window, coordinator
        self.request_id, self.invitation = request_id, invitation

    def __getattr__(self, name):
        return getattr(self.window, name)

    async def process_packet(self, packet):
        response = await self.window.process_packet(packet)
        _, body = decode_packet(self.invitation, response, direction='response')
        if packet['kind'] == 'configure' and body == {'status': 'accepted'}:
            self.coordinator._accepted(self.request_id, packet['requestId'])
        return response


class LocalProvisioningCoordinator:
    def __init__(self, *, db_path, device_id, backend_factory=NetworkManagerBackend,
                 server_factory=ProvisioningTCPServer, clock=time.time,
                 monotonic=time.monotonic, notify=None, review_identity=None):
        _require(type(device_id) is str and DEVICE_ID.fullmatch(device_id))
        _require(all(callable(item) for item in (backend_factory, server_factory, clock, monotonic)))
        self.path, self.device_id = Path(db_path).absolute(), device_id
        self._backend_factory, self._server_factory = backend_factory, server_factory
        self._clock, self._monotonic = clock, monotonic
        _require(notify is None or callable(notify))
        self._notify_callback = notify
        self._review_store = ProvisionReviewLedger(self.path.parent / 'provision-reviews',
            device_id=device_id, review_identity=review_identity)
        self._review_intents = {}
        self._fd, self._inode = None, None
        self._closed = self._closing = self._faulted = self._degraded = False
        self._lock, self._stop = asyncio.Lock(), asyncio.Event()
        self._contexts, self._runner, self._running_id = {}, None, None
        try:
            self._prepare_store()
        except BaseException:
            self.close()
            raise LocalProvisioningError() from None

    @staticmethod
    def _private(path, *, directory=False):
        info = path.stat()
        _require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
                 and info.st_uid == os.geteuid() and not info.st_mode & 0o077)
        if not directory:
            _require(info.st_nlink == 1)
        return info

    def _now(self):
        value = self._clock()
        _require(_number(value))
        return float(value)

    @contextmanager
    def _connect(self):
        try:
            _require(not self._closed and not self._faulted)
            _require(not any(path.is_symlink() for path in (self.path, *self.path.parents)))
            info = self._private(self.path)
            _require(self._inode == (info.st_dev, info.st_ino))
            with closing(sqlite3.connect('file:' + quote(str(self.path), safe='/') + '?mode=rw',
                                         uri=True, timeout=1)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute('PRAGMA synchronous=FULL')
                with connection:
                    yield connection
        except (sqlite3.Error, OSError):
            self._faulted = True
            raise LocalProvisioningError() from None

    def _prepare_store(self):
        _require(not any(path.is_symlink() for path in (self.path, *self.path.parents)))
        _require(not any(part.lower() in {'miniprogram', 'wechat-miniprogram'} for part in self.path.parts))
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._private(self.path.parent, directory=True)
        for parent in self.path.parent.parents:
            info = parent.stat()
            _require(info.st_uid in (0, os.geteuid()) and
                     (not info.st_mode & 0o022 or info.st_uid == 0 and info.st_mode & stat.S_ISVTX))
        lock = Path(str(self.path) + '.lock')
        self._fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self._private(lock)
        _require(os.fstat(self._fd).st_size == 0)
        fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        created = not self.path.exists()
        descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600) if created else None
        if descriptor is not None:
            os.close(descriptor)
        info = self._private(self.path)
        _require(info.st_size <= 2 * 1024 * 1024)
        self._inode = info.st_dev, info.st_ino
        _require(not any(Path(str(self.path) + suffix).exists() for suffix in ('-wal', '-shm')))
        pins = {'schema': 'local_provisioning_v1', 'device': self.device_id}
        if not created:
            with closing(sqlite3.connect('file:' + quote(str(self.path), safe='/') + '?mode=ro&immutable=1', uri=True)) as c:
                self._schema(c)
                _require(dict(c.execute('SELECT key,value FROM lp_meta')) == pins)
                _require(c.execute('PRAGMA integrity_check').fetchone() == ('ok',))
                rows = {}
                columns = [item[0] for item in c.execute('SELECT * FROM lp_requests LIMIT 0').description]
                for row in c.execute('SELECT * FROM lp_requests'):
                    value = dict(zip(columns, row))
                    self._validate_row(value)
                    rows[value['request_id']] = value
                reviewed = self._review_store.read(rows)
                _require(c.execute('SELECT COUNT(*) FROM lp_requests').fetchone()[0] <= MAX_WINDOWS)
                _require(sum(row['status'] in _ACTIVE and row['request_id'] not in reviewed for row in rows.values()) <= 1)
        with self._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if created:
                c.execute('CREATE TABLE lp_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                c.execute('''CREATE TABLE lp_requests(request_id TEXT PRIMARY KEY,device_id TEXT NOT NULL,
                    window_id TEXT NOT NULL UNIQUE,invitation TEXT NOT NULL,status TEXT NOT NULL,
                    ap_name TEXT NOT NULL,created REAL NOT NULL,deadline REAL NOT NULL,
                    network_request_id TEXT,receipt_qr TEXT,updated REAL NOT NULL)''')
                c.executemany('INSERT INTO lp_meta VALUES(?,?)', pins.items())
            # Nothing can have changed Wi-Fi from prepared: start() persists
            # opening before constructing/calling any network-capable backend.
            c.execute("UPDATE lp_requests SET status='not_sent',updated=? WHERE status='prepared'", (self._now(),))
            c.execute("UPDATE lp_requests SET status='unknown',updated=? WHERE status IN ('opening','open','accepted','connecting')", (self._now(),))

    @staticmethod
    def _schema(c):
        _require({row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")} == set(_SCHEMA))
        _require(not c.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')").fetchone())
        for table, columns in _SCHEMA.items():
            _require([(row[1], row[2], row[3], row[5]) for row in c.execute('PRAGMA table_info(' + table + ')')] == columns)
            indexes = c.execute('PRAGMA index_list(' + table + ')').fetchall()
            expected = {('key',)} if table == 'lp_meta' else {('request_id',), ('window_id',)}
            actual = set()
            for index in indexes:
                _require(index[2] == 1 and index[3] in {'pk', 'u'} and index[4] == 0)
                actual.add(tuple(row[2] for row in c.execute('PRAGMA index_info("' + index[1] + '")')))
            _require(actual == expected and len(indexes) == len(expected))

    def _validate_row(self, row):
        _require(type(row['request_id']) is str and REQUEST_ID.fullmatch(row['request_id']))
        invitation = Invitation.parse(row['invitation'])
        _require(row['device_id'] == self.device_id == invitation.device_id and row['window_id'] == invitation.session_id)
        _require(row['status'] in _ALL and type(row['ap_name']) is str and re.fullmatch(r'AI-CAT-[A-F0-9]{6}', row['ap_name']))
        _require(all(_number(row[key]) for key in ('created', 'deadline', 'updated')))
        _require(row['deadline'] == row['created'] + WINDOW_SECONDS)
        network = row['network_request_id']
        _require(network is None or type(network) is str and REQUEST_ID.fullmatch(network))
        _require(row['status'] not in {'accepted', 'connecting', 'connected', 'failed'} or network is not None)
        qr = row['receipt_qr']
        if qr is not None:
            _require(type(qr) is str and qr.startswith('AICAT-PROVISION-RECEIPT:1:') and len(qr) <= 2030)
            encoded = qr[len('AICAT-PROVISION-RECEIPT:1:'):]
            _require(re.fullmatch(r'[A-Za-z0-9_-]+', encoded))
            packet = parse_json(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
            _, body = decode_packet(invitation, packet, direction='response')
            _require(packet['requestId'] == network and packet['kind'] == 'configure' and body == {'status': row['status']})

    def _row(self, request_id):
        _require(type(request_id) is str and REQUEST_ID.fullmatch(request_id))
        with self._connect() as c:
            row = c.execute('SELECT * FROM lp_requests WHERE request_id=?', (request_id,)).fetchone()
        if row is None:
            raise LocalProvisioningError(404, 'LOCAL_PROVISION_NOT_FOUND')
        value = dict(row)
        self._validate_row(value)
        return value

    def _update(self, request_id, status, *, network_request_id=None, receipt_qr=None):
        _require(status in _ALL)
        with self._connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM lp_requests WHERE request_id=?', (request_id,)).fetchone()
            _require(row is not None)
            values = dict(row)
            values.update(status=status, updated=self._now())
            if network_request_id is not None:
                _require(values['network_request_id'] in (None, network_request_id))
                values['network_request_id'] = network_request_id
            values['receipt_qr'] = receipt_qr
            self._validate_row(values)
            c.execute('UPDATE lp_requests SET status=?,network_request_id=?,receipt_qr=?,updated=? WHERE request_id=?',
                      (status, values['network_request_id'], receipt_qr, values['updated'], request_id))

    def _accepted(self, request_id, network_request_id):
        _require(not self._closing)
        row = self._row(request_id)
        _require(row['status'] in {'opening', 'open', 'accepted'})
        self._update(request_id, 'accepted', network_request_id=network_request_id)

    @staticmethod
    def _view(row):
        return {'requestId': row['request_id'], 'deviceId': row['device_id'], 'windowId': row['window_id'],
                'status': row['status'], 'apName': row['ap_name'], 'expiresAt': row['deadline'],
                'networkRequestId': row['network_request_id'], 'receiptQr': row['receipt_qr']}

    def _public_ready(self):
        _require(not self._closed and not self._closing and not self._faulted and not self._review_store.faulted)

    def _review_rows(self):
        with self._connect() as c:
            rows = {row['request_id']: dict(row) for row in c.execute('SELECT * FROM lp_requests')}
        for row in rows.values():
            self._validate_row(row)
        return rows

    def _reviews(self, rows=None):
        try:
            return self._review_store.read(self._review_rows() if rows is None else rows)
        except Exception:
            raise LocalProvisioningError() from None

    @staticmethod
    def _review_original(row):
        return {'deviceId': row['device_id'], 'requestId': row['request_id'],
                'windowId': row['window_id'], 'networkRequestId': row['network_request_id']}

    def _review_eligible(self, row):
        return bool(self._review_store.identity is not None and row['status'] == 'unknown' and
                    row['network_request_id'] is not None and row['receipt_qr'] is not None and
                    self._now() >= row['deadline'] + CHECKPOINT_SETTLE_SECONDS and
                    (self._runner is None or self._runner.done()) and not self._contexts and not self._degraded)

    def _live_review_intent(self):
        now, monotonic = self._now(), self._monotonic()
        _require(_number(monotonic))
        return any(intent['created'] <= now < intent['expires'] and
                   intent['monotonic'] <= monotonic < intent['monoExpires'] for intent in self._review_intents.values())

    def _archive_view(self, row, audit=None):
        return {'eligible': False if audit is not None else self._review_eligible(row) and not self._live_review_intent(),
                'cardId': self._review_store.identity['cardId'], 'original': self._review_original(row),
                'status': 'unknown', 'resolution': 'operator_archived' if audit is not None else 'pending',
                'reviewId': audit['reviewId'] if audit is not None else None,
                'archivedAt': audit['archivedAt'] if audit is not None else None,
                'acknowledgements': list(audit['acknowledgements']) if audit is not None else []}

    async def archive_status(self, request_id):
        async with self._lock:
            self._public_ready()
            _require(self._review_store.identity is not None)
            row = self._row(request_id)
            _require(row['status'] == 'unknown' and row['network_request_id'] is not None and row['receipt_qr'] is not None)
            return self._archive_view(row, self._reviews().get(request_id))

    async def prepare_archive(self, request_id, *, window_id, network_request_id, acks):
        async with self._lock:
            self._public_ready()
            try:
                normalized = acknowledgements(acks)
            except Exception:
                raise LocalProvisioningError() from None
            row = self._row(request_id)
            _require(type(window_id) is str and type(network_request_id) is str and
                     row['window_id'] == window_id and row['network_request_id'] == network_request_id)
            _require(request_id not in self._reviews() and self._review_eligible(row) and not self._live_review_intent())
            now, monotonic = self._now(), self._monotonic()
            _require(_number(monotonic))
            token, review_id = secrets.token_urlsafe(32), secrets.token_hex(16)
            # At most one same-visit intent. Only the public reviewId persists;
            # this opaque confirmation token never enters the audit directory.
            self._review_intents = {token: {'requestId': request_id, 'source': digest(row),
                'acks': normalized, 'reviewId': review_id, 'created': now, 'expires': now + 30,
                'monotonic': monotonic, 'monoExpires': monotonic + 30}}
            return {'reviewToken': token, 'expiresAt': now + 30, 'reviewId': review_id,
                    'cardId': self._review_store.identity['cardId'], 'original': self._review_original(row)}

    async def confirm_archive(self, request_id, *, review_token, window_id, network_request_id, acks, guard):
        async with self._lock:
            self._public_ready()
            _require(type(review_token) is str and callable(guard))
            try:
                normalized = acknowledgements(acks)
            except Exception:
                raise LocalProvisioningError() from None
            intent = self._review_intents.get(review_token)
            _require(intent is not None and intent['requestId'] == request_id and intent['acks'] == normalized)
            row = self._row(request_id)
            _require(type(window_id) is str and type(network_request_id) is str and
                     row['window_id'] == window_id and row['network_request_id'] == network_request_id and digest(row) == intent['source'])
            previous = self._reviews().get(request_id)
            if previous is not None:
                _require(previous['reviewId'] == intent['reviewId'] and previous['acknowledgements'] == normalized)
                return self._archive_view(row, previous)
            now, monotonic = self._now(), self._monotonic()
            _require(_number(monotonic) and intent['created'] <= now < intent['expires'] and
                     intent['monotonic'] <= monotonic < intent['monoExpires'] and self._review_eligible(row))
            try:
                allowed = guard()
                if inspect.isawaitable(allowed):
                    allowed = await allowed
            except BaseException:
                allowed = False
            # The guard is read-only and supplied by the trusted application,
            # never deserialized from client data. It is not an NM atomic CAS.
            self._public_ready()
            fresh = self._row(request_id)
            now, monotonic = self._now(), self._monotonic()
            _require(allowed is True and self._review_intents.get(review_token) is intent and
                     _number(monotonic) and digest(fresh) == intent['source'] and
                     intent['created'] <= now < intent['expires'] and intent['monotonic'] <= monotonic < intent['monoExpires'] and
                     self._review_eligible(fresh) and request_id not in self._reviews())
            try:
                audit = self._review_store.append(fresh, review_id=intent['reviewId'],
                    archived_at=int(now * 1000), acks=normalized, rows=self._review_rows())
            except Exception:
                raise LocalProvisioningError() from None
            return self._archive_view(fresh, audit)

    def _expire_prepared(self):
        now, monotonic = self._now(), self._monotonic()
        _require(_number(monotonic))
        with self._connect() as c:
            rows = c.execute("SELECT request_id,deadline FROM lp_requests WHERE status='prepared'").fetchall()
            expired = [row[0] for row in rows if row[1] <= now or
                       row[0] in self._contexts and self._contexts[row[0]]['deadline'] <= monotonic]
            c.executemany("UPDATE lp_requests SET status='not_sent',updated=? WHERE request_id=? AND status='prepared'",
                          [(now, request_id) for request_id in expired])
        for request_id in expired:
            self._contexts.pop(request_id, None)

    async def prepare(self, request_id, confirmation='open-local-provisioning'):
        async with self._lock:
            self._public_ready()
            _require(not self._degraded)
            _require(type(request_id) is str and REQUEST_ID.fullmatch(request_id))
            _require(type(confirmation) is str and confirmation == 'open-local-provisioning')
            self._expire_prepared()
            reviewed = self._reviews()
            with self._connect() as c:
                old = c.execute('SELECT * FROM lp_requests WHERE request_id=?', (request_id,)).fetchone()
                if old is None:
                    _require(self._runner is None or self._runner.done())
                    active = c.execute("SELECT request_id FROM lp_requests WHERE status IN ('prepared','opening','open','accepted','connecting','unknown')").fetchall()
                    if any(row[0] not in reviewed for row in active):
                        raise LocalProvisioningError(409, 'LOCAL_PROVISION_PENDING')
                    _require(c.execute('SELECT COUNT(*) FROM lp_requests').fetchone()[0] < MAX_WINDOWS)
                    now = self._now()
                    async def apply(credentials, network_request_id):
                        return await self._apply(request_id, credentials, network_request_id)
                    window = ProvisioningWindow(self.device_id, apply, ttl_seconds=WINDOW_SECONDS, clock=self._monotonic)
                    invitation = window.open()
                    parsed = Invitation.parse(invitation)
                    ap_name = 'AI-CAT-' + secrets.token_hex(3).upper()
                    c.execute('INSERT INTO lp_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (request_id, self.device_id, parsed.session_id, invitation, 'prepared', ap_name,
                         now, now + WINDOW_SECONDS, None, None, now))
                    self._contexts[request_id] = {'window': window, 'deadline': self._monotonic() + WINDOW_SECONDS,
                        'complete': asyncio.Event(), 'backend': None, 'result': None}
            row = self._row(request_id)
            result = self._view(row)
            if row['status'] == 'prepared':
                result['invitation'] = row['invitation']
            return result

    async def status(self, request_id):
        async with self._lock:
            self._public_ready()
            self._expire_prepared()
            self._reviews()
            return self._view(self._row(request_id))

    async def get_receipt(self, request_id):
        return await self.status(request_id)

    async def resume(self, request_id):
        """Private authenticated RPC ONLY: recover the original possession key.

        This reads an existing record, even unknown/terminal, so a newly paired
        owner can verify its original encrypted receipt. It does not re-open a
        window, renew its deadline, clear unknown or dispatch any network task.
        The result must remain in caller-private closures, never Page data,
        browser storage, discovery output or logs.
        """
        async with self._lock:
            self._public_ready()
            self._expire_prepared()
            row = self._row(request_id)
            self._reviews()
            return {**self._view(row), 'invitation': row['invitation']}

    async def current_window(self):
        """Metadata only; discovery is not possession/ownership authorization."""
        async with self._lock:
            self._public_ready()
            self._expire_prepared()
            self._reviews()
            with self._connect() as c:
                row = c.execute('SELECT * FROM lp_requests ORDER BY rowid DESC LIMIT 1').fetchone()
            if row is None:
                return {'window': None}
            value = dict(row)
            self._validate_row(value)
            return {'window': self._view(value)}

    async def start(self, request_id, offline_guard=None):
        # A deployment-only read-only hook, never deserialized from RPC JSON.
        # This reduces an offline-autostart race; it is NOT a NetworkManager
        # atomic compare-and-swap or a physical-presence attestation.
        _require(offline_guard is None or callable(offline_guard))
        async with self._lock:
            self._public_ready()
            self._expire_prepared()
            row = self._row(request_id)
            _require(request_id not in self._reviews())
            if self._runner is not None and not self._runner.done() and self._running_id == request_id:
                task = self._runner
            elif row['status'] != 'prepared':
                return self._view(row)
            else:
                _require(request_id in self._contexts and (self._runner is None or self._runner.done()))
                _require(not self._degraded)
                if offline_guard is None:
                    self._update(request_id, 'opening')
                self._running_id = request_id
                task = self._runner = asyncio.create_task(self._run(request_id, offline_guard))
        return await asyncio.shield(task)

    async def _notify(self, event):
        if self._notify_callback is None:
            return
        try:
            result = await asyncio.wait_for(self._notify_callback(event), timeout=2.5)
            if result is False:
                LOGGER.warning('Local network cue unavailable: %s', event)
        except Exception:
            # Never log raw exceptions, credentials, addresses or process output.
            LOGGER.warning('Local network cue unavailable: %s', event)

    async def _apply(self, request_id, credentials, network_request_id):
        context = self._contexts[request_id]
        try:
            if self._stop.is_set():
                raise LocalProvisioningError()
            self._update(request_id, 'connecting', network_request_id=network_request_id)
            await self._notify('connecting')
            result = await context['backend'](credentials, network_request_id)
            context['result'] = result if result in _TERMINAL else 'unknown'
        except BaseException:
            context['result'] = 'unknown'
        finally:
            if context['result'] in {'failed', 'unknown'}:
                # Same strict four-field formatter as the private operator
                # CLI. Persist a fixed diagnostic to journal before this
                # context is discarded; never retain/log credentials, native
                # exceptions, IDs or settings and never alter the real receipt.
                try:
                    diagnostic = diagnostic_line(context['backend'])
                    if diagnostic:
                        LOGGER.warning('%s', diagnostic.rstrip('\n'))
                except BaseException:
                    pass
            context['complete'].set()
        return context['result']

    async def _run(self, request_id, offline_guard=None):
        context = self._contexts[request_id]
        row = self._row(request_id)
        invitation = Invitation.parse(row['invitation'])
        window = context['window']
        server, backend, receipt, result = None, None, None, 'unknown'
        waiters = []
        opened = False
        try:
            if offline_guard is not None:
                try:
                    allowed = offline_guard()
                    if inspect.isawaitable(allowed):
                        allowed = await allowed
                except BaseException:
                    allowed = False
                if (allowed is not True or self._stop.is_set() or
                        self._now() >= row['deadline'] or self._monotonic() >= context['deadline']):
                    raise _OfflineGuardDeclined()
                # No other await occurs between this live guard, the durable
                # opening intent and backend.begin(). A final NM association
                # race still requires production-level atomic coordination.
                self._update(request_id, 'opening')
            backend = context['backend'] = self._backend_factory()
            server = self._server_factory(_RecordedWindow(window, self, request_id, invitation))
            if not self._stop.is_set():
                await backend.begin(row['ap_name'], LOCAL_TEST_AP_PASSWORD, timeout=WINDOW_SECONDS)
            if self._stop.is_set():
                raise LocalProvisioningError()
            await server.start()
            opened = True
            current = self._row(request_id)
            if current['status'] == 'opening':
                self._update(request_id, 'open')
            await self._notify('open')
            waiters = [asyncio.create_task(context['complete'].wait()), asyncio.create_task(self._stop.wait())]
            await asyncio.wait(waiters, timeout=max(0.0, context['deadline'] - self._monotonic()),
                               return_when=asyncio.FIRST_COMPLETED)
            await window.settle()
            current = self._row(request_id)
            if context['result'] is not None:
                result = context['result']
            elif self._stop.is_set():
                result = 'unknown'
            else:
                result = 'not_sent'
                if current['network_request_id'] is not None:
                    window.abort_pending(current['network_request_id'])
            if current['network_request_id'] is not None:
                # One authenticated receipt is minted, then reads return the
                # same bytes instead of consuming the window's 64-nonce budget.
                receipt = window.receipt_qr()
                candidate = dict(current, status=result, receipt_qr=receipt)
                try:
                    self._validate_row(candidate)
                except Exception:
                    receipt, result = None, 'unknown'
        except _OfflineGuardDeclined:
            result, receipt = 'not_sent', None
        except BaseException:
            result, receipt = 'unknown', None
        finally:
            for task in waiters:
                if not task.done():
                    task.cancel()
            if waiters:
                await asyncio.gather(*waiters, return_exceptions=True)
            cleanup_failed = False
            try:
                if server is not None:
                    await server.close()
                else:
                    await window.close()
            except BaseException:
                cleanup_failed = True
            finally:
                try:
                    if backend is not None:
                        await backend.close()
                except BaseException:
                    cleanup_failed = True
            if cleanup_failed:
                self._degraded = True
                if result != 'connected':
                    result, receipt = 'unknown', None
            self._contexts.pop(request_id, None)
        self._update(request_id, result, receipt_qr=receipt)
        if opened:
            await self._notify(result)
        return self._view(self._row(request_id))

    async def aclose(self):
        self._closing = True
        self._stop.set()
        if self._runner is not None and not self._runner.done():
            await asyncio.shield(self._runner)
        self.close()

    def close(self):
        if self._runner is not None and not self._runner.done():
            raise LocalProvisioningError()
        self._closed = self._closing = True
        self._contexts.clear()
        self._review_intents.clear()
        descriptor, self._fd = self._fd, None
        if descriptor is not None:
            os.close(descriptor)

    def __del__(self):
        descriptor = getattr(self, '_fd', None)
        if descriptor is not None:
            os.close(descriptor)
