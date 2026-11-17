"""Root-private append-only operator reviews, not network-result replacements.

The source provisioning SQLite rows/ciphertexts and nonce domains are never
modified. A torn/invalid/missing-after-observation audit fails closed; checksums
detect corruption, not a malicious root capable of replacing every trust root.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import stat


ACKNOWLEDGEMENTS = ['window-ended', 'network-stable', 'device-idle', 'accept-unknown']
MAX_REVIEWS = 256
FIELDS = {'v', 'deviceId', 'cardId', 'userId', 'bindingEpoch', 'requestId', 'windowId',
          'networkRequestId', 'status', 'resolution', 'reviewId', 'archivedAt',
          'acknowledgements', 'sourceSha256', 'receiptSha256', 'auditSha256'}


class ProvisionReviewError(ValueError):
    def __init__(self):
        super().__init__('Private provisioning review is unavailable')


def require(value):
    if not value:
        raise ProvisionReviewError()


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def identity(value):
    require(type(value) is dict and set(value) == {'cardId', 'userId', 'bindingEpoch'})
    require(type(value['cardId']) is str and re.fullmatch(r'[0-9a-f]{32}', value['cardId']))
    require(type(value['userId']) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value['userId']))
    epoch = value['bindingEpoch']
    require(type(epoch) is str and 1 <= len(epoch) <= 64)
    parsed = datetime.fromisoformat(epoch.replace('Z', '+00:00'))
    require(parsed.utcoffset() == timedelta(0))
    return dict(value)


def acknowledgements(values):
    require(type(values) is list and len(values) == len(ACKNOWLEDGEMENTS) and
            all(type(value) is str for value in values) and
            len(set(values)) == len(values) and set(values) == set(ACKNOWLEDGEMENTS))
    return list(ACKNOWLEDGEMENTS)


class ProvisionReviewLedger:
    def __init__(self, path, *, device_id, review_identity=None):
        self.path, self.device_id = Path(path), device_id
        self.identity = None if review_identity is None else identity(review_identity)
        self.faulted = False
        self._observed_inode = None
        self._observed_records = {}

    def _private(self, info, *, directory=False):
        require(info.st_uid == os.geteuid() and not info.st_mode & 0o077 and
                (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1))

    @contextmanager
    def _directory(self, *, create=False):
        require(not self.faulted)
        require(not any(part.is_symlink() for part in (self.path, *self.path.parents)))
        if not self.path.exists():
            require(self._observed_inode is None)
            if not create:
                yield None
                return
            require(self.identity is not None)
            os.mkdir(self.path, mode=0o700)
        descriptor = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            self._private(info, directory=True)
            inode = info.st_dev, info.st_ino
            require(self._observed_inode in (None, inode))
            self._observed_inode = inode
            require(self.identity is not None)
            yield descriptor
            require((self.path.lstat().st_dev, self.path.lstat().st_ino) == inode)
        finally:
            os.close(descriptor)

    def _validate(self, value, row):
        require(type(value) is dict and set(value) == FIELDS and type(value['v']) is int and value['v'] == 1)
        require(row is not None and row['status'] == 'unknown' and row['network_request_id'] is not None and row['receipt_qr'] is not None)
        require(value['deviceId'] == self.device_id == row['device_id'])
        for key in ('cardId', 'userId', 'bindingEpoch'):
            require(type(value[key]) is str and value[key] == self.identity[key])
        for key, source in (('requestId', 'request_id'), ('windowId', 'window_id'), ('networkRequestId', 'network_request_id')):
            require(type(value[key]) is str and value[key] == row[source])
        require(value['status'] == 'unknown' and value['resolution'] == 'operator_archived')
        require(type(value['reviewId']) is str and re.fullmatch(r'[0-9a-f]{32}', value['reviewId']))
        require(type(value['archivedAt']) is int and int((row['deadline'] + 90) * 1000) <= value['archivedAt'] < 2**53)
        require(value['acknowledgements'] == ACKNOWLEDGEMENTS and all(type(item) is str for item in value['acknowledgements']))
        require(value['sourceSha256'] == digest(row) and
                value['receiptSha256'] == hashlib.sha256(row['receipt_qr'].encode('ascii')).hexdigest())
        require(value['auditSha256'] == digest({key: item for key, item in value.items() if key != 'auditSha256'}))

    def _read(self, descriptor, rows):
        if descriptor is None:
            return {}
        names = os.listdir(descriptor)
        require(len(names) <= MAX_REVIEWS)
        require(set(self._observed_records) <= set(names))
        result, review_ids = {}, set()
        for name in names:
            require(re.fullmatch(r'[0-9a-f]{64}\.json', name))
            handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
            try:
                info = os.fstat(handle)
                self._private(info)
                require(0 < info.st_size <= 8192)
                raw = os.read(handle, 8193)
                require(len(raw) == info.st_size)
                after = os.fstat(handle)
                require((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) ==
                        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns))
            finally:
                os.close(handle)
            def unique(pairs):
                value = {}
                for key, item in pairs:
                    require(key not in value)
                    value[key] = item
                return value
            value = json.loads(raw, object_pairs_hook=unique,
                               parse_constant=lambda _: (_ for _ in ()).throw(ProvisionReviewError()))
            require(type(value) is dict and canonical(value) == raw)
            fingerprint = hashlib.sha256(raw).hexdigest()
            require(name not in self._observed_records or self._observed_records[name] == fingerprint)
            self._validate(value, rows.get(value.get('requestId')))
            require(name == hashlib.sha256(value['requestId'].encode('ascii')).hexdigest() + '.json')
            require(value['requestId'] not in result and value['reviewId'] not in review_ids)
            result[value['requestId']] = value
            review_ids.add(value['reviewId'])
        self._observed_records = {hashlib.sha256(request_id.encode('ascii')).hexdigest() + '.json':
            hashlib.sha256(canonical(value)).hexdigest() for request_id, value in result.items()}
        return result

    def read(self, rows):
        try:
            with self._directory() as descriptor:
                return self._read(descriptor, rows)
        except BaseException:
            self.faulted = True
            raise ProvisionReviewError() from None

    def append(self, row, *, review_id, archived_at, acks, rows):
        try:
            require(self.identity is not None)
            with self._directory(create=True) as descriptor:
                previous = self._read(descriptor, rows)
                require(row['request_id'] not in previous and len(previous) < MAX_REVIEWS)
                value = {'v': 1, 'deviceId': self.device_id, **self.identity,
                         'requestId': row['request_id'], 'windowId': row['window_id'],
                         'networkRequestId': row['network_request_id'], 'status': 'unknown',
                         'resolution': 'operator_archived', 'reviewId': review_id,
                         'archivedAt': archived_at, 'acknowledgements': acknowledgements(acks),
                         'sourceSha256': digest(row),
                         'receiptSha256': hashlib.sha256(row['receipt_qr'].encode('ascii')).hexdigest()}
                value['auditSha256'] = digest(value)
                self._validate(value, row)
                name = hashlib.sha256(row['request_id'].encode('ascii')).hexdigest() + '.json'
                handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
                try:
                    raw = canonical(value)
                    written = 0
                    while written < len(raw):
                        count = os.write(handle, raw[written:])
                        require(count > 0)
                        written += count
                    os.fsync(handle)
                finally:
                    os.close(handle)
                os.fsync(descriptor)
                after = self._read(descriptor, rows)
                require(after == {**previous, row['request_id']: value})
                return value
        except BaseException:
            self.faulted = True
            raise ProvisionReviewError() from None
