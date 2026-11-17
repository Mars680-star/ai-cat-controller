"""Actual Linux file-lock protocol checks; all paths and children are synthetic."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
from pathlib import Path
import select
import stat
import struct
import subprocess
import sys
import threading
import time

import pytest

from ai_cat_controller.core.process_gate import (
    ProcessGateError,
    ProcessOperationGate,
    ProcessTicket,
)


_SOURCE = Path(__file__).resolve().parents[2] / "src"
_RECORD = struct.Struct(">4s16sQc3s")


@pytest.fixture
def provisioned(tmp_path):
    path = tmp_path / "process-fence"
    gate = ProcessOperationGate.provision(path)
    try:
        yield path, gate
    finally:
        gate.close()


def _state(path):
    return _RECORD.unpack((path / "state").read_bytes())


def _write_state(path, *, magic=b"ACG1", incarnation=None, epoch=None, phase=b"O", reserved=b"\0\0\0"):
    previous = _state(path)
    (path / "state").write_bytes(_RECORD.pack(
        magic, previous[1] if incarnation is None else incarnation,
        previous[2] if epoch is None else epoch, phase, reserved,
    ))


def _line(process):
    readable, _, _ = select.select([process.stdout], [], [], 3)
    assert readable, "synthetic child did not signal before the bounded deadline"
    line = process.stdout.readline()
    assert line, "synthetic child exited before signalling"
    return line.rstrip("\n")


def _waitpid(child_pid):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        pid, status = os.waitpid(child_pid, os.WNOHANG)
        if pid:
            return status
        time.sleep(0.005)
    raise AssertionError("synthetic fork child did not exit before the bounded deadline")


@contextmanager
def _child(tmp_path, path, body, *, close_fds=True):
    # Deliberately do not inherit credentials or the developer's environment.
    process = subprocess.Popen(
        [sys.executable, "-c", body, str(path)], cwd=tmp_path,
        env={"PYTHONPATH": str(_SOURCE), "PYTHONDONTWRITEBYTECODE": "1"},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, close_fds=close_fds,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=3)


_HOLD_NORMAL = """
import sys
from pathlib import Path
from ai_cat_controller.core.process_gate import ProcessOperationGate
gate = ProcessOperationGate(Path(sys.argv[1]))
lease = gate.normal()
print('held', flush=True)
sys.stdin.readline()
lease.close()
gate.close()
print('released', flush=True)
"""


def test_explicit_provision_is_private_exact_binary_and_never_overwrites(provisioned):
    path, gate = provisioned
    ticket = gate.snapshot()
    assert isinstance(ticket, ProcessTicket)
    assert len(ticket.incarnation) == 32 and bytes.fromhex(ticket.incarnation)
    assert ticket.epoch == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o700
    assert {child.name for child in path.iterdir()} == {"admission.lock", "work.lock", "state"}
    before = {child.name: (child.stat().st_ino, child.read_bytes()) for child in path.iterdir()}
    for child in path.iterdir():
        assert stat.S_IMODE(child.stat().st_mode) == 0o600
        assert child.stat().st_nlink == 1 and child.stat().st_uid == os.geteuid()
    assert _state(path) == (b"ACG1", bytes.fromhex(ticket.incarnation), 0, b"O", b"\0\0\0")
    with pytest.raises(ProcessGateError):
        ProcessOperationGate.provision(path)
    assert before == {child.name: (child.stat().st_ino, child.read_bytes()) for child in path.iterdir()}


def test_constructor_never_creates_missing_directory_or_files(tmp_path):
    missing = tmp_path / "not-provisioned"
    with pytest.raises(ProcessGateError):
        ProcessOperationGate(missing)
    assert not missing.exists()
    missing.mkdir(mode=0o700)
    with pytest.raises(ProcessGateError):
        ProcessOperationGate(missing)
    assert list(missing.iterdir()) == []


def test_real_other_process_shared_lease_blocks_exclusive_and_closing_blocks_new(provisioned, tmp_path):
    path, gate = provisioned
    with _child(tmp_path, path, _HOLD_NORMAL) as child:
        assert _line(child) == "held"
        original = gate.snapshot()
        maintenance = gate.begin_maintenance(expected=original)
        try:
            assert maintenance.ticket.epoch == original.epoch + 1
            assert gate.snapshot() is None
            assert maintenance.try_exclusive() is False
            with pytest.raises(ProcessGateError):
                gate.normal()
            with pytest.raises(ProcessGateError):
                gate.checkpoint(original)
            child.stdin.write("release\n")
            child.stdin.flush()
            assert _line(child) == "released"
            assert child.wait(timeout=3) == 0
            assert maintenance.try_exclusive() is True
            assert maintenance.try_exclusive() is True
        finally:
            maintenance.finish()
        current = gate.snapshot()
        assert current.epoch == original.epoch + 2
        assert current.incarnation == original.incarnation
        with pytest.raises(ProcessGateError):
            gate.normal(expected=original)
        with gate.normal(expected=current) as lease:
            lease.checkpoint()


def test_distinct_roots_never_unlock_each_other_and_close_is_idempotent(provisioned):
    _, gate = provisioned
    first, second = gate.normal(), gate.normal()
    maintenance = gate.begin_maintenance()
    try:
        first.close()
        first.close()
        assert maintenance.try_exclusive() is False
        second.close()
        assert maintenance.try_exclusive() is True
    finally:
        first.close()
        second.close()
        maintenance.finish()


def test_closing_a_coordinator_does_not_drop_its_still_live_work_descriptor(provisioned):
    path, gate = provisioned
    lease = gate.normal()
    observer = ProcessOperationGate(path)
    gate.close()
    gate.close()
    maintenance = observer.begin_maintenance()
    try:
        assert maintenance.try_exclusive() is False
        lease.close()
        assert maintenance.try_exclusive() is True
        with pytest.raises(ProcessGateError):
            gate.normal()
    finally:
        lease.close()
        maintenance.finish()
        observer.close()


def test_required_child_can_duplicate_admitted_parent_during_closing(provisioned):
    _, gate = provisioned
    parent = gate.normal()
    maintenance = gate.begin_maintenance()
    child = gate.normal(inherit=parent, expected=parent.ticket)
    try:
        assert child.ticket == parent.ticket
        parent.close()
        with pytest.raises(ProcessGateError):
            child.checkpoint()
        assert maintenance.try_exclusive() is False
        child.close()
        assert maintenance.try_exclusive() is True
    finally:
        parent.close()
        child.close()
        maintenance.finish()


def test_inheritance_cannot_ignore_mismatched_expected_or_closed_or_foreign_parent(provisioned, tmp_path):
    _, gate = provisioned
    other = ProcessOperationGate.provision(tmp_path / "other-fence")
    parent = gate.normal()
    wrong = ProcessTicket(parent.ticket.incarnation, parent.ticket.epoch + 1)
    try:
        with pytest.raises(ProcessGateError):
            gate.normal(inherit=parent, expected=wrong)
        with pytest.raises(ProcessGateError):
            other.normal(inherit=parent)
        parent.close()
        with pytest.raises(ProcessGateError):
            gate.normal(inherit=parent)
    finally:
        parent.close()
        other.close()


def test_concurrent_parent_close_cannot_duplicate_a_reused_unlocked_descriptor(provisioned, tmp_path, monkeypatch):
    import ai_cat_controller.core.process_gate as process_gate

    _, gate = provisioned
    parent = gate.normal()
    duplicating, closing, closed = threading.Event(), threading.Event(), threading.Event()
    unrelated = tmp_path / "unrelated-file"
    unrelated.touch(mode=0o600)
    reused = []
    real_dup = os.dup

    def delayed_dup(descriptor):
        duplicating.set()
        assert closing.wait(timeout=3)
        if closed.wait(timeout=0.03):
            # If close raced past the lease check, the numeric fd can already
            # identify unrelated work. The actual kernel lock must still win.
            replacement = os.open(unrelated, os.O_RDONLY | os.O_CLOEXEC)
            reused.append(replacement)
            if replacement != descriptor:
                os.dup2(replacement, descriptor, inheritable=False)
                reused.append(descriptor)
        return real_dup(descriptor)

    def close_parent():
        assert duplicating.wait(timeout=3)
        closing.set()
        parent.close()
        closed.set()

    monkeypatch.setattr(process_gate.os, "dup", delayed_dup)
    child, maintenance = None, None
    with ThreadPoolExecutor(max_workers=1) as pool:
        closer = pool.submit(close_parent)
        try:
            child = gate.normal(inherit=parent)
            closer.result(timeout=3)
            maintenance = gate.begin_maintenance()
            assert maintenance.try_exclusive() is False
            child.close()
            assert maintenance.try_exclusive() is True
        finally:
            parent.close()
            if child:
                child.close()
            if maintenance:
                maintenance.finish()
            for descriptor in reused:
                os.close(descriptor)


def test_forked_required_child_keeps_open_file_description_after_parent_closes(provisioned):
    _, gate = provisioned
    parent = gate.normal()
    ready_read, ready_write = os.pipe()
    release_read, release_write = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        try:
            os.close(ready_read)
            os.close(release_write)
            child = gate.normal(inherit=parent)
            parent.close()
            os.write(ready_write, b"1")
            os.read(release_read, 1)
            child.close()
            os._exit(0)
        except BaseException:
            os._exit(71)
    os.close(ready_write)
    os.close(release_read)
    maintenance = None
    try:
        assert select.select([ready_read], [], [], 3)[0]
        assert os.read(ready_read, 1) == b"1"
        parent.close()
        maintenance = gate.begin_maintenance()
        assert maintenance.try_exclusive() is False
        os.write(release_write, b"1")
        status = _waitpid(child_pid)
        child_pid = 0
        assert os.waitstatus_to_exitcode(status) == 0
        assert maintenance.try_exclusive() is True
    finally:
        parent.close()
        os.close(ready_read)
        os.close(release_write)
        if child_pid:
            os.kill(child_pid, 9)
            os.waitpid(child_pid, 0)
        if maintenance:
            maintenance.finish()


def test_competing_maintenance_never_queues_and_stale_expected_does_not_change_state(provisioned, tmp_path):
    path, gate = provisioned
    previous = gate.snapshot()
    first = gate.begin_maintenance(expected=previous)
    with _child(tmp_path, path, """
import sys
from pathlib import Path
from ai_cat_controller.core.process_gate import ProcessOperationGate, ProcessGateError
gate = ProcessOperationGate(Path(sys.argv[1]))
try:
    gate.begin_maintenance()
except ProcessGateError:
    print('rejected', flush=True)
else:
    raise AssertionError('competing maintenance was admitted')
""") as child:
        try:
            assert _line(child) == "rejected"
            assert child.wait(timeout=3) == 0
        finally:
            first.finish()
    before = (path / "state").read_bytes()
    with pytest.raises(ProcessGateError):
        gate.begin_maintenance(expected=previous)
    assert (path / "state").read_bytes() == before
    assert gate.snapshot().epoch == previous.epoch + 2


def test_cancelled_drain_can_finish_without_exclusive_but_never_claims_a_drain(provisioned):
    _, gate = provisioned
    lease = gate.normal()
    original = lease.ticket
    maintenance = gate.begin_maintenance()
    assert maintenance.try_exclusive() is False
    maintenance.finish()
    maintenance.finish()
    try:
        assert gate.snapshot().epoch == original.epoch + 2
        with pytest.raises(ProcessGateError):
            lease.checkpoint()
        with gate.normal():
            pass
    finally:
        lease.close()


@pytest.mark.parametrize("exclusive", [False, True])
def test_crash_keeps_durable_closing_without_pid_or_ttl_recovery(provisioned, tmp_path, exclusive):
    path, gate = provisioned
    script = """
import os, sys
from pathlib import Path
from ai_cat_controller.core.process_gate import ProcessOperationGate
gate = ProcessOperationGate(Path(sys.argv[1]))
maintenance = gate.begin_maintenance()
if EXCLUSIVE:
    assert maintenance.try_exclusive()
print('closing', flush=True)
os._exit(0)
""".replace("EXCLUSIVE", repr(exclusive))
    with _child(tmp_path, path, script) as child:
        assert _line(child) == "closing"
        assert child.wait(timeout=3) == 0
    assert _state(path)[3] == b"C"
    reopened = ProcessOperationGate(path)
    try:
        assert reopened.snapshot() is None
        for coordinator in (gate, reopened):
            with pytest.raises(ProcessGateError):
                coordinator.normal()
            with pytest.raises(ProcessGateError):
                coordinator.begin_maintenance()
        with pytest.raises(ProcessGateError):
            ProcessOperationGate.provision(path)
        assert _state(path)[3] == b"C"
    finally:
        reopened.close()


def test_abandon_never_reopens_a_closing_record(provisioned):
    path, gate = provisioned
    maintenance = gate.begin_maintenance()
    maintenance.abandon()
    maintenance.abandon()
    maintenance.finish()
    assert gate.snapshot() is None
    assert _state(path)[3] == b"C"
    with pytest.raises(ProcessGateError):
        maintenance.try_exclusive()


@pytest.mark.parametrize("filename", ["admission.lock", "work.lock", "state"])
@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "mode", "directory", "fifo", "missing"])
def test_unsafe_protocol_entries_fail_closed_without_repair(provisioned, tmp_path, filename, unsafe):
    path, gate = provisioned
    target = path / filename
    original = target.read_bytes()
    if unsafe == "mode":
        target.chmod(0o640)
    elif unsafe == "hardlink":
        os.link(target, tmp_path / "untrusted-alias")
    else:
        target.unlink()
        if unsafe == "symlink":
            outside = tmp_path / "outside"
            outside.write_bytes(original)
            outside.chmod(0o600)
            target.symlink_to(outside)
        elif unsafe == "directory":
            target.mkdir(mode=0o700)
        elif unsafe == "fifo":
            os.mkfifo(target, 0o600)
    with pytest.raises(ProcessGateError) as failure:
        gate.normal()
    assert str(tmp_path) not in str(failure.value)
    with pytest.raises(ProcessGateError):
        ProcessOperationGate(path)
    if unsafe == "missing":
        assert not target.exists()


@pytest.mark.parametrize("bad_record", [
    b"", b"x" * 31, b"x" * 33,
    _RECORD.pack(b"ACG2", b"a" * 16, 0, b"O", b"\0\0\0"),
    _RECORD.pack(b"ACG1", b"a" * 16, 0, b"X", b"\0\0\0"),
    _RECORD.pack(b"ACG1", b"a" * 16, 0, b"O", b"\0\0\1"),
])
def test_malformed_state_is_not_replaced_or_interpreted_as_open(provisioned, bad_record):
    path, gate = provisioned
    (path / "state").write_bytes(bad_record)
    with pytest.raises(ProcessGateError):
        gate.normal()
    with pytest.raises(ProcessGateError):
        gate.begin_maintenance()
    with pytest.raises(ProcessGateError):
        ProcessOperationGate(path)
    assert (path / "state").read_bytes() == bad_record


@pytest.mark.parametrize("filename", ["admission.lock", "work.lock"])
def test_lock_inode_replacement_invalidates_existing_coordinator(provisioned, filename):
    path, gate = provisioned
    old = path / filename
    old.rename(path / (filename + ".removed"))
    old.touch(mode=0o600)
    with pytest.raises(ProcessGateError):
        gate.normal()
    with pytest.raises(ProcessGateError):
        gate.begin_maintenance()


@pytest.mark.parametrize("filename", ["admission.lock", "work.lock"])
def test_lock_contents_are_not_accepted_as_a_legacy_pid_or_marker(provisioned, filename):
    path, gate = provisioned
    (path / filename).write_bytes(b"1234\n")
    with pytest.raises(ProcessGateError):
        gate.normal()
    with pytest.raises(ProcessGateError):
        ProcessOperationGate(path)
    assert (path / filename).read_bytes() == b"1234\n"


def test_directory_replacement_does_not_split_a_live_coordinator(provisioned, tmp_path):
    path, gate = provisioned
    path.rename(tmp_path / "old-directory")
    replacement = ProcessOperationGate.provision(path)
    try:
        with pytest.raises(ProcessGateError):
            gate.snapshot()
        with pytest.raises(ProcessGateError):
            gate.normal()
    finally:
        replacement.close()


def test_symlink_ancestor_and_nonprivate_directory_are_rejected_without_creation(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ProcessGateError):
        ProcessOperationGate.provision(alias / "fence")
    assert not (real / "fence").exists()
    gate = ProcessOperationGate.provision(real / "fence")
    gate.close()
    with pytest.raises(ProcessGateError):
        ProcessOperationGate(alias / "fence")
    (real / "fence").chmod(0o750)
    with pytest.raises(ProcessGateError):
        ProcessOperationGate(real / "fence")


def test_incarnation_changes_and_epoch_exhaustion_never_revalidate_old_work(provisioned):
    path, gate = provisioned
    original = gate.snapshot()
    _write_state(path, incarnation=b"z" * 16)
    with pytest.raises(ProcessGateError):
        gate.normal(expected=original)
    _write_state(path, incarnation=bytes.fromhex(original.incarnation), epoch=(1 << 64) - 2)
    before = (path / "state").read_bytes()
    with pytest.raises(ProcessGateError):
        gate.begin_maintenance()
    assert (path / "state").read_bytes() == before


def test_many_threads_hold_independent_shared_leases_until_their_actual_close(provisioned):
    _, gate = provisioned
    ready = threading.Barrier(5)
    release = threading.Event()

    def work():
        with gate.normal():
            ready.wait(timeout=3)
            assert release.wait(timeout=3)

    with ThreadPoolExecutor(max_workers=4) as pool:
        workers = [pool.submit(work) for _ in range(4)]
        ready.wait(timeout=3)
        maintenance = gate.begin_maintenance()
        try:
            assert maintenance.try_exclusive() is False
            release.set()
            for worker in workers:
                worker.result(timeout=3)
            assert maintenance.try_exclusive() is True
        finally:
            release.set()
            maintenance.finish()


def test_lock_files_are_never_replaced_across_state_changes_or_product_backup_reset(provisioned, tmp_path):
    from ai_cat_controller.persistence.sqlite_repository import SQLiteRepository

    path, gate = provisioned
    before = {name: (path / name).stat().st_ino for name in ("admission.lock", "work.lock")}
    repository = SQLiteRepository(tmp_path / "product-data" / "product.db")
    repository.initialize()
    backup = repository.backup_product_data()
    assert {entry.name for entry in backup["backup_directory"].iterdir()} == {"product-data.db"}
    repository.clear_product_data()
    maintenance = gate.begin_maintenance()
    assert maintenance.try_exclusive()
    maintenance.finish()
    assert before == {name: (path / name).stat().st_ino for name in before}
    assert gate.snapshot().epoch == 2


def test_protocol_descriptors_do_not_leak_to_an_unrelated_exec(provisioned, tmp_path):
    path, gate = provisioned
    lease = gate.normal()
    with _child(tmp_path, path, """
import sys
print('running', flush=True)
sys.stdin.readline()
""", close_fds=False) as child:
        assert _line(child) == "running"
        # close_fds=False makes this an actual CLOEXEC check, not a Popen cleanup check.
        lease.close()
        maintenance = gate.begin_maintenance()
        try:
            assert child.poll() is None
            assert maintenance.try_exclusive()
        finally:
            maintenance.finish()
