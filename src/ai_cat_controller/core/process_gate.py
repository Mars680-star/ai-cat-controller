"""Cooperative Linux process fence; provision separately, never repair on open.

admission.lock is a turnstile, work.lock covers actual work, and state is a
32-byte atomic record. Neither lock inode may be replaced. This is not proof
that legacy binaries, another namespace, or a physical motor obey the fence.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
from functools import wraps
import os
from pathlib import Path
import secrets
import stat
import struct
import threading


class ProcessGateError(RuntimeError):
    def __init__(self):
        super().__init__("进程维护协议未就绪或请求已失效，拒绝操作")


def _guarded(method):
    @wraps(method)
    def checked(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except (OSError, ValueError, TypeError, OverflowError) as error:
            raise ProcessGateError() from error
    return checked


def _path(value) -> Path:
    raw = os.fspath(value)
    if (not isinstance(raw, str) or not raw.startswith("/") or
            any(part in {"", ".", ".."} for part in raw.split("/")[1:]) or
            any(ord(char) < 32 or ord(char) == 127 for char in raw)):
        raise ProcessGateError()
    return Path(raw)


def _directory(path: Path) -> int:
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _identity(info):
    return info.st_dev, info.st_ino


def _private(info, *, directory=False):
    if (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or not directory and info.st_nlink != 1):
        raise ProcessGateError()


@dataclass(frozen=True, slots=True)
class ProcessTicket:
    incarnation: str
    epoch: int


class ProcessLease:
    def __init__(self, gate, descriptor, ticket):
        self._gate, self._descriptor, self.ticket = gate, descriptor, ticket
        self._lock = threading.Lock()

    def _duplicate(self, gate, expected):
        with self._lock:
            if (self._gate is not gate or self._descriptor < 0 or
                    expected is not None and expected != self.ticket):
                raise ProcessGateError()
            gate._check_locks()
            return os.dup(self._descriptor)

    def checkpoint(self):
        if self._descriptor < 0:
            raise ProcessGateError()
        self._gate.checkpoint(self)

    def duplicate_fd(self) -> int:
        """Required opaque child may inherit this via an explicit pass_fds.

        The caller closes its duplicate after real child cleanup. Merely
        relying on a Python keeper would lose the lease if that keeper dies.
        """
        return self._duplicate(self._gate, None)

    def fork(self, *, expected: ProcessTicket | None = None) -> ProcessLease:
        """Reserve required cleanup on this same coordinator, never re-admit."""
        return self._gate.normal(expected=expected, inherit=self)

    def same_domain(self, gate: ProcessOperationGate) -> bool:
        return self._gate.same_domain(gate)

    def close(self):
        # dup/inherited descriptors share an open-file-description. LOCK_UN
        # here would silently unlock the still-running parent's actual work.
        with self._lock:
            if self._descriptor >= 0:
                descriptor, self._descriptor = self._descriptor, -1
                os.close(descriptor)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class ProcessMaintenance:
    def __init__(self, gate, admission, ticket):
        self._gate, self._admission, self.ticket = gate, admission, ticket
        self._work = -1

    def _validate(self):
        if self._admission < 0:
            raise ProcessGateError()
        self._gate._check_locks()
        current, phase = self._gate._read_state()
        if current != self.ticket or phase != b"C":
            raise ProcessGateError()

    @_guarded
    def try_exclusive(self) -> bool:
        self._validate()
        if self._work >= 0:
            return True
        descriptor = self._gate._open_file("work.lock", expected=self._gate._pins["work.lock"], size=0)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            self._validate()
            self._work, descriptor = descriptor, -1
            return True
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @_guarded
    def finish(self):
        if self._admission < 0:
            return
        try:
            self._validate()
            self._gate._write_state(ProcessTicket(self.ticket.incarnation, self.ticket.epoch + 1), b"O")
        finally:
            # A failed state publication remains fail-closed. Never recreate or
            # overwrite it in a recovery guess based on PID, time, or TTL.
            self.abandon()

    def abandon(self):
        for name in ("_work", "_admission"):
            descriptor = getattr(self, name)
            if descriptor >= 0:
                setattr(self, name, -1)
                os.close(descriptor)


class ProcessOperationGate:
    @_guarded
    def __init__(self, directory: Path):
        self.path = _path(directory)
        self._directory = _directory(self.path)
        self._closed = False
        try:
            info = os.fstat(self._directory)
            _private(info, directory=True)
            self._root_identity = _identity(info)
            self._pins = {}
            for name in ("admission.lock", "work.lock"):
                descriptor = self._open_file(name, size=0)
                try:
                    self._pins[name] = _identity(os.fstat(descriptor))
                finally:
                    os.close(descriptor)
            ticket, _ = self._read_state()
            self._incarnation = ticket.incarnation
        except BaseException:
            self.close()
            raise

    @classmethod
    @_guarded
    def provision(cls, directory: Path):
        """Explicit new-directory initialization, never an existing-state reset."""
        path = _path(directory)
        parent = _directory(path.parent)
        root = -1
        try:
            os.mkdir(path.name, 0o700, dir_fd=parent)
            root = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent)
            _private(os.fstat(root), directory=True)
            for name in ("admission.lock", "work.lock", "state"):
                descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600, dir_fd=root)
                try:
                    if name == "state":
                        data = b"ACG1" + secrets.token_bytes(16) + struct.pack(">Q", 0) + b"O\0\0\0"
                        offset = 0
                        while offset < len(data):
                            count = os.write(descriptor, data[offset:])
                            if count <= 0:
                                raise ProcessGateError()
                            offset += count
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            os.fsync(root)
            os.fsync(parent)
        finally:
            if root >= 0:
                os.close(root)
            os.close(parent)
        return cls(path)

    def _check_root(self):
        if self._closed:
            raise ProcessGateError()
        descriptor = _directory(self.path)
        try:
            info, held = os.fstat(descriptor), os.fstat(self._directory)
            _private(info, directory=True)
            _private(held, directory=True)
            if _identity(info) != self._root_identity or _identity(held) != self._root_identity:
                raise ProcessGateError()
        finally:
            os.close(descriptor)

    def _open_file(self, name, *, expected=None, size=None):
        self._check_root()
        descriptor = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=self._directory)
        try:
            info = os.fstat(descriptor)
            path_info = os.stat(name, dir_fd=self._directory, follow_symlinks=False)
            _private(info)
            _private(path_info)
            if (_identity(info) != _identity(path_info) or
                    expected is not None and _identity(info) != expected or
                    size is not None and info.st_size != size):
                raise ProcessGateError()
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _check_locks(self):
        for name, identity in self._pins.items():
            descriptor = self._open_file(name, expected=identity, size=0)
            os.close(descriptor)

    def _read_state(self):
        descriptor = self._open_file("state", size=32)
        try:
            data = os.read(descriptor, 33)
        finally:
            os.close(descriptor)
        if (len(data) != 32 or data[:4] != b"ACG1" or data[28:29] not in (b"O", b"C")
                or data[29:] != b"\0\0\0"):
            raise ProcessGateError()
        ticket = ProcessTicket(data[4:20].hex(), struct.unpack(">Q", data[20:28])[0])
        if hasattr(self, "_incarnation") and ticket.incarnation != self._incarnation:
            raise ProcessGateError()
        return ticket, data[28:29]

    def _write_state(self, ticket, phase):
        self._check_locks()
        data = b"ACG1" + bytes.fromhex(ticket.incarnation) + struct.pack(">Q", ticket.epoch) + phase + b"\0\0\0"
        if len(data) != 32:
            raise ProcessGateError()
        name = ".state-tmp-" + secrets.token_hex(12)
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600, dir_fd=self._directory)
        try:
            offset = 0
            while offset < len(data):
                count = os.write(descriptor, data[offset:])
                if count <= 0:
                    raise ProcessGateError()
                offset += count
            os.fsync(descriptor)
            self._check_locks()
            os.replace(name, "state", src_dir_fd=self._directory, dst_dir_fd=self._directory)
            os.fsync(self._directory)
        finally:
            os.close(descriptor)
            try:
                os.unlink(name, dir_fd=self._directory)
            except FileNotFoundError:
                pass

    @_guarded
    def snapshot(self) -> ProcessTicket | None:
        descriptor = self._open_file("admission.lock", expected=self._pins["admission.lock"], size=0)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return None
            self._check_locks()
            ticket, phase = self._read_state()
            return ticket if phase == b"O" else None
        finally:
            os.close(descriptor)

    @_guarded
    def same_domain(self, other: ProcessOperationGate) -> bool:
        """Compare live pinned objects, not just a configured path string.

        Used only to select an existing task-aware admission owner. This does
        not grant a lease, refresh a ticket, or prove participant readiness.
        """
        self._check_locks()
        if not isinstance(other, ProcessOperationGate) or self.path != other.path:
            return False
        other._check_locks()
        self._read_state()
        other._read_state()
        return (self._root_identity == other._root_identity and
            self._pins == other._pins and self._incarnation == other._incarnation)

    @_guarded
    def normal(self, expected: ProcessTicket | None = None, *, inherit: ProcessLease | None = None) -> ProcessLease:
        if inherit is not None:
            descriptor = inherit._duplicate(self, expected)
            return ProcessLease(self, descriptor, inherit.ticket)
        admission = self._open_file("admission.lock", expected=self._pins["admission.lock"], size=0)
        work = -1
        try:
            fcntl.flock(admission, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self._check_locks()
            ticket, phase = self._read_state()
            if phase != b"O" or expected is not None and (not isinstance(expected, ProcessTicket) or ticket != expected):
                raise ProcessGateError()
            work = self._open_file("work.lock", expected=self._pins["work.lock"], size=0)
            fcntl.flock(work, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self._check_locks()
            lease, work = ProcessLease(self, work, ticket), -1
            return lease
        finally:
            if work >= 0:
                os.close(work)
            os.close(admission)

    @_guarded
    def checkpoint(self, ticket_or_lease=None):
        if isinstance(ticket_or_lease, ProcessLease):
            if ticket_or_lease._gate is not self or ticket_or_lease._descriptor < 0:
                raise ProcessGateError()
            expected = ticket_or_lease.ticket
        else:
            expected = ticket_or_lease
        current = self.snapshot()
        if current is None or expected is not None and current != expected:
            raise ProcessGateError()

    @_guarded
    def begin_maintenance(self, expected: ProcessTicket | None = None) -> ProcessMaintenance:
        admission = self._open_file("admission.lock", expected=self._pins["admission.lock"], size=0)
        try:
            fcntl.flock(admission, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._check_locks()
            ticket, phase = self._read_state()
            if (phase != b"O" or ticket.epoch > (1 << 64) - 3 or
                    expected is not None and (not isinstance(expected, ProcessTicket) or expected != ticket)):
                raise ProcessGateError()
            closing = ProcessTicket(ticket.incarnation, ticket.epoch + 1)
            self._write_state(closing, b"C")
            maintenance, admission = ProcessMaintenance(self, admission, closing), -1
            return maintenance
        finally:
            if admission >= 0:
                os.close(admission)

    def close(self):
        if not self._closed:
            self._closed = True
            os.close(self._directory)


def main():
    parser = argparse.ArgumentParser(description="Explicitly provision a NEW private process-fence directory")
    parser.add_argument("--init", type=Path, required=True)
    args = parser.parse_args()
    try:
        gate = ProcessOperationGate.provision(args.init)
        gate.close()
    except ProcessGateError:
        parser.exit(1, "process gate initialization refused; existing/unsafe state is never repaired automatically\n")


if __name__ == "__main__":
    main()
