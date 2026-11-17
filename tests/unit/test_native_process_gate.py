"""Run the actual C gate against inert files and real host flock contention."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import shutil
import select
import struct
import subprocess

import pytest

from test_native_motion_gate_runtime import function, native_harnesses, run


HEADER = Path(__file__).parents[2] / "native/common/ai_cat_process_gate.h"


@pytest.fixture(scope="module")
def process_gate_binary(tmp_path_factory):
    directory = tmp_path_factory.mktemp("native-process-gate")
    compiler = shutil.which("cc")
    assert compiler, "A C compiler is required for the native process-gate regression"
    harness = directory / "gate.c"
    harness.write_text(r'''
#define _DEFAULT_SOURCE
#include <stdio.h>
''' + '#include "' + str(HEADER) + '"\n' + r'''
int main(int argc, char **argv) {
    struct ai_cat_process_gate gate;
    ai_cat_process_gate_init(&gate);
    int result = ai_cat_process_gate_enter(&gate);
    printf("enter=%d enabled=%d lease=%d cloexec=%d\n", result, gate.enabled,
        gate.work_fd >= 0, gate.work_fd >= 0 ? !!(fcntl(gate.work_fd, F_GETFD) & FD_CLOEXEC) : 0);
    fflush(stdout);
    if (argc > 1 && !strcmp(argv[1], "hold") && result == 0) {
        int command;
        while ((command = getchar()) != EOF && command != 'q') {
            if (command != 'c') continue;
            printf("check=%d\n", ai_cat_process_gate_check(&gate));
            fflush(stdout);
        }
    } else if (result == 0) printf("check=%d\n", ai_cat_process_gate_check(&gate));
    ai_cat_process_gate_close(&gate);
    return 0;
}
''')
    binary = directory / "gate"
    built = subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
        str(harness), "-o", str(binary)], capture_output=True, text=True, timeout=20)
    assert built.returncode == 0, built.stderr
    return binary


def _state(epoch=0, phase=b"O", incarnation=b"0123456789abcdef"):
    return b"ACG1" + incarnation + struct.pack(">Q", epoch) + phase + b"\0\0\0"


@pytest.fixture
def gate_directory(tmp_path):
    directory = tmp_path / "gate"
    directory.mkdir(mode=0o700)
    for name in ("admission.lock", "work.lock", "state"):
        path = directory / name
        path.write_bytes(_state() if name == "state" else b"")
        path.chmod(0o600)
    return directory


def _environment(path):
    result = dict(os.environ)
    result.pop("AI_CAT_PROCESS_GATE_PATH", None)
    if path is not None:
        result["AI_CAT_PROCESS_GATE_PATH"] = str(path)
    return result


def _run(binary, path):
    result = subprocess.run([str(binary)], env=_environment(path), capture_output=True,
        text=True, timeout=2)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _start(binary, path):
    child = subprocess.Popen([str(binary), "hold"], env=_environment(path), stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert _line(child) == "enter=0 enabled=1 lease=1 cloexec=1"
    return child


def _check(child):
    child.stdin.write("c\n")
    child.stdin.flush()
    return _line(child)


def _line(child):
    assert select.select([child.stdout], [], [], 2)[0], "C worker failed to reach the bounded handshake"
    return child.stdout.readline().strip()


def _finish(child):
    child.communicate("q\n", timeout=2)
    assert child.returncode == 0


def test_unset_is_the_only_legacy_disabled_mode(process_gate_binary):
    assert _run(process_gate_binary, None) == "enter=0 enabled=0 lease=0 cloexec=0\ncheck=0"
    assert _run(process_gate_binary, "").startswith("enter=-1 enabled=1 lease=0")


def test_native_gate_accepts_exact_open_protocol(process_gate_binary, gate_directory):
    assert _run(process_gate_binary, gate_directory) == "enter=0 enabled=1 lease=1 cloexec=1\ncheck=0"


@pytest.mark.parametrize("suffix", ["/", "//child", "/.", "/..", "/x\ny"])
def test_native_gate_rejects_unsafe_components(process_gate_binary, gate_directory, suffix):
    assert _run(process_gate_binary, str(gate_directory) + suffix).startswith("enter=-1")


@pytest.mark.parametrize("payload", [b"", b"x" * 32, _state() + b"x", _state()[:-1],
    _state()[:28] + b"X\0\0\0", _state()[:29] + b"\0\0x"])
def test_native_gate_never_downgrades_bad_state(process_gate_binary, gate_directory, payload):
    (gate_directory / "state").write_bytes(payload)
    assert _run(process_gate_binary, gate_directory).startswith("enter=-1 enabled=1 lease=0")


@pytest.mark.parametrize("name", ["admission.lock", "work.lock", "state"])
def test_native_gate_rejects_insecure_file_mode(process_gate_binary, gate_directory, name):
    (gate_directory / name).chmod(0o644)
    assert _run(process_gate_binary, gate_directory).startswith("enter=-1")


def test_native_gate_rejects_symlink_ancestor_and_hardlinked_lock(process_gate_binary, gate_directory, tmp_path):
    link = tmp_path / "alias"
    link.symlink_to(gate_directory, target_is_directory=True)
    assert _run(process_gate_binary, link).startswith("enter=-1")
    os.link(gate_directory / "work.lock", tmp_path / "another-work")
    assert _run(process_gate_binary, gate_directory).startswith("enter=-1")


@pytest.mark.parametrize("name", ["admission.lock", "work.lock", "state"])
def test_native_gate_rejects_symlink_or_nonregular_protocol_member(process_gate_binary, gate_directory, tmp_path, name):
    target = gate_directory / name
    target.rename(tmp_path / "saved")
    target.symlink_to(tmp_path / "saved")
    assert _run(process_gate_binary, gate_directory).startswith("enter=-1")
    target.unlink()
    target.mkdir(mode=0o600)
    assert _run(process_gate_binary, gate_directory).startswith("enter=-1")


def test_native_gate_rejects_nonfinal_symlink_and_insecure_directory(process_gate_binary, gate_directory, tmp_path):
    alias = tmp_path / "ancestor"
    alias.symlink_to(tmp_path, target_is_directory=True)
    assert _run(process_gate_binary, alias / "gate").startswith("enter=-1")
    gate_directory.chmod(0o755)
    assert _run(process_gate_binary, gate_directory).startswith("enter=-1")


def test_closing_does_not_hide_missing_or_unsafe_work_lock(process_gate_binary, gate_directory):
    (gate_directory / "state").write_bytes(_state(epoch=1, phase=b"C"))
    (gate_directory / "work.lock").unlink()
    assert _run(process_gate_binary, gate_directory).startswith("enter=-1")
    (gate_directory / "work.lock").write_bytes(b"")
    (gate_directory / "work.lock").chmod(0o644)
    assert _run(process_gate_binary, gate_directory).startswith("enter=-1")


def test_relative_missing_and_root_paths_never_enable_legacy(process_gate_binary, tmp_path):
    for path in ("relative", "/", tmp_path / "missing"):
        assert _run(process_gate_binary, path).startswith("enter=-1 enabled=1 lease=0")


@pytest.mark.parametrize("name", ["admission.lock", "work.lock"])
def test_nonempty_lock_is_not_a_valid_protocol_member(process_gate_binary, gate_directory, name):
    (gate_directory / name).write_bytes(b"bad")
    assert _run(process_gate_binary, gate_directory).startswith("enter=-1")


@pytest.mark.parametrize("name", ["admission.lock", "work.lock"])
def test_python_exclusive_lock_prevents_c_admission(process_gate_binary, gate_directory, name):
    with (gate_directory / name).open("rb+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert _run(process_gate_binary, gate_directory).startswith("enter=1 enabled=1 lease=0")


def test_c_work_lease_blocks_python_maintenance_until_actual_close(process_gate_binary, gate_directory):
    child = _start(process_gate_binary, gate_directory)
    try:
        with (gate_directory / "work.lock").open("rb+") as lock:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert _check(child) == "check=0"
            _finish(child)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        if child.poll() is None:
            _finish(child)


@pytest.mark.parametrize("change", ["closing", "epoch", "incarnation", "admission_inode", "work_inode", "directory_inode"])
def test_live_revocation_rejects_new_effects_without_dropping_actual_lease(process_gate_binary, gate_directory, change):
    child = _start(process_gate_binary, gate_directory)
    original_work = (gate_directory / "work.lock").open("rb+")
    try:
        if change in {"closing", "epoch", "incarnation"}:
            (gate_directory / "state").write_bytes(_state(
                epoch=1 if change == "epoch" else 0,
                phase=b"C" if change == "closing" else b"O",
                incarnation=b"fedcba9876543210" if change == "incarnation" else b"0123456789abcdef"))
            expected = "check=1"
        elif change == "directory_inode":
            gate_directory.rename(gate_directory.with_name("old-gate"))
            gate_directory.mkdir(mode=0o700)
            expected = "check=-1"
        else:
            name = "admission.lock" if change == "admission_inode" else "work.lock"
            replacement = gate_directory / "replacement"
            replacement.write_bytes(b"")
            replacement.chmod(0o600)
            replacement.replace(gate_directory / name)
            expected = "check=-1"
        assert _check(child) == expected
        with pytest.raises(BlockingIOError):
            fcntl.flock(original_work, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _finish(child)
        fcntl.flock(original_work, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        original_work.close()
        if child.poll() is None:
            _finish(child)


def test_closed_state_parks_even_without_a_live_lock_owner(process_gate_binary, gate_directory):
    (gate_directory / "state").write_bytes(_state(epoch=4, phase=b"C"))
    assert _run(process_gate_binary, gate_directory).startswith("enter=1 enabled=1 lease=0")


@pytest.mark.parametrize("kind", ["toy", "voice"])
def test_closed_protocol_blocks_actual_motor_entrypoint_before_driver_or_spawn(native_harnesses, gate_directory, kind):
    (gate_directory / "state").write_bytes(_state(epoch=3, phase=b"C"))
    result = run(native_harnesses, kind, ["head_lr"], {"AI_CAT_PROCESS_GATE_PATH": str(gate_directory)})
    assert result["rc"] != 0
    assert result["alloc" if kind == "toy" else "spawn"] == 0


def test_motor_stop_still_bypasses_invalid_process_gate(native_harnesses, tmp_path):
    result = run(native_harnesses, "toy", ["stop"], {"AI_CAT_PROCESS_GATE_PATH": str(tmp_path / "absent")})
    assert result["rc"] == 0 and result["alloc"] == 0


def test_closing_between_voice_dispatch_and_final_spawn_never_starts_motor(native_harnesses, gate_directory):
    child = subprocess.Popen([str(native_harnesses["voice"]), "head_lr", "hold"],
        env=_environment(gate_directory), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    try:
        assert _line(child) == "READY"
        (gate_directory / "state").write_bytes(_state(epoch=1, phase=b"C"))
        stdout, stderr = child.communicate("c", timeout=2)
        assert child.returncode == 0, stderr
        assert "HARNESS rc=-1 spawn=0" in stdout
    finally:
        if child.poll() is None:
            child.communicate("q", timeout=2)


@pytest.fixture(scope="module")
def voice_worker_binary(tmp_path_factory):
    directory = tmp_path_factory.mktemp("native-tool-drain")
    compiler = shutil.which("cc")
    assert compiler
    source = (HEADER.parents[1] / "dialog/volc_conv_ai_demo.c").read_text()
    prefix = r'''
#define _DEFAULT_SOURCE
#include <stdio.h>
#include <pthread.h>
''' + '#include "' + str(HEADER) + '"\n' + r'''
typedef struct { int unused; } function_call_task_t;
static pthread_mutex_t function_call_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t function_call_finished = PTHREAD_COND_INITIALIZER;
static unsigned int function_call_workers;
static bool function_call_shutdown;
static bool process_gate_closing, clean_exit_requested, exit_request, running, ai_playing, discard_playback_audio;
static struct ai_cat_process_gate process_gate = {.root_fd = -1, .work_fd = -1};
'''
    main = r'''
static void *worker(void *unused) {
    (void)unused;
    (void)getchar();
    __finish_function_call_task(calloc(1, sizeof(function_call_task_t)));
    return NULL;
}
int main(void) {
    pthread_t task;
    if (ai_cat_process_gate_enter(&process_gate)) return 2;
    if (!__process_gate_allows_effect()) return 3;
    function_call_workers = 1;
    if (pthread_create(&task, NULL, worker, NULL)) return 4;
    puts("READY"); fflush(stdout);
    __drain_function_call_workers();
    pthread_join(task, NULL);
    int allowed = __process_gate_allows_effect();
    printf("DRAINED count=%u shutdown=%d allowed=%d exit=%d clean=%d discard=%d\n",
        function_call_workers, function_call_shutdown, allowed,
        exit_request, clean_exit_requested, discard_playback_audio);
    fflush(stdout);
    /* No explicit close: the real native process holds the FD until exit. */
    return 0;
}
'''
    harness = directory / "worker.c"
    harness.write_text(prefix + "\n".join(function(source, name) for name in (
        "__process_gate_allows_effect", "__finish_function_call_task", "__drain_function_call_workers")) + main)
    binary = directory / "worker"
    built = subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pthread",
        str(harness), "-o", str(binary)], capture_output=True, text=True, timeout=20)
    assert built.returncode == 0, built.stderr
    return binary


def test_actual_voice_tool_drain_keeps_lease_and_finishes_cleanup_after_closing(voice_worker_binary, gate_directory):
    child = subprocess.Popen([str(voice_worker_binary)], env=_environment(gate_directory),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert _line(child) == "READY"
        with (gate_directory / "work.lock").open("rb+") as lock:
            (gate_directory / "state").write_bytes(_state(epoch=1, phase=b"C"))
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            stdout, stderr = child.communicate("q", timeout=2)
            assert child.returncode == 0, stderr
            assert "DRAINED count=0 shutdown=1 allowed=0 exit=1 clean=1 discard=1" in stdout
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        if child.poll() is None:
            child.communicate("q", timeout=2)


def test_voice_source_admission_and_cleanup_are_wired_to_real_boundaries():
    source = (HEADER.parents[1] / "dialog/volc_conv_ai_demo.c").read_text()
    main = source[source.index("int main(void)"):]
    assert main.index("ai_cat_process_gate_enter(&process_gate)") < main.index("__load_device_serial()")
    assert main.index("ai_cat_process_gate_enter(&process_gate)") < main.index("unlink(DIALOG_SESSION_MARKER)")
    assert "while ((gate_result = ai_cat_process_gate_enter(&process_gate)) == 1" in main
    admitted_startup = main[main.index("ai_cat_process_gate_enter(&process_gate)"):main.index("dialog_runtime_started_ms =")]
    assert "wake_request = false;" in admitted_startup
    assert "text_dialog_request = false;" in admitted_startup
    assert main.index("if (gate_result < 0)") < main.index("pa_simple_new(")
    assert "ai_cat_process_gate_close(&process_gate)" not in main
    cleanup = main[main.index("err_out_label:"):]
    assert cleanup.index("__drain_function_call_workers()") < cleanup.index("volc_stop(")
    assert cleanup.index("pthread_join(") < cleanup.index("volc_destroy(")
    assert "if (process_gate_closing) pa_simple_flush" in cleanup
    arguments = function(source, "__handle_function_call_arguments_done")
    assert arguments.index("function_call_workers++") < arguments.index("pthread_create(")
    assert "function_call_shutdown || !__process_gate_allows_effect()" in arguments
    assert "__finish_function_call_task(task)" in arguments
    for name in ("__write_dialog_event", "__write_dialog_status", "_on_volc_event",
        "_on_volc_conversation_status", "_on_volc_audio_data", "__handle_ws_message",
        "__audio_playback_task", "__run_motor_motion", "__send_dialog_text_item",
        "__send_function_call_output", "_ws_response_create"):
        assert "__process_gate_allows_effect()" in function(source, name), name
    capture = main[main.index("pa_simple_read("):main.index("send_result = volc_send_audio_data(")]
    assert "if (!__process_gate_allows_effect()) break;" in capture
    prepare = (HEADER.parents[2] / "integrations/volcengine-k1/scripts/prepare_sdk.sh").read_text()
    assert '"$REPO_DIR/native/common/ai_cat_process_gate.h"' in prepare
    assert '"$SDK_DIR/examples/low_load_solution/common/ai_cat_process_gate.h"' in prepare


def test_actual_python_coordinator_and_c_lease_share_exclusive_maintenance(process_gate_binary, tmp_path):
    from ai_cat_controller.core.process_gate import ProcessOperationGate

    coordinator = ProcessOperationGate.provision(tmp_path / "coordinated")
    child = _start(process_gate_binary, coordinator.path)
    lease = coordinator.normal()
    maintenance = None
    try:
        ticket = coordinator.snapshot()
        maintenance = coordinator.begin_maintenance(expected=ticket)
        assert maintenance.try_exclusive() is False
        assert _check(child) == "check=1"
        assert _run(process_gate_binary, coordinator.path).startswith("enter=1")
        lease.close()
        assert maintenance.try_exclusive() is False  # C still owns its real FD.
        _finish(child)
        assert maintenance.try_exclusive() is True
        maintenance.finish()
        assert coordinator.snapshot().epoch == ticket.epoch + 2
        assert _run(process_gate_binary, coordinator.path).endswith("check=0")
    finally:
        lease.close()
        if child.poll() is None:
            _finish(child)
        if maintenance is not None:
            maintenance.abandon()
        coordinator.close()


def test_abandoned_python_maintenance_remains_closed_for_new_c_process(process_gate_binary, tmp_path):
    from ai_cat_controller.core.process_gate import ProcessOperationGate

    coordinator = ProcessOperationGate.provision(tmp_path / "coordinated")
    maintenance = coordinator.begin_maintenance()
    assert maintenance.try_exclusive() is True
    maintenance.abandon()
    coordinator.close()
    reopened = ProcessOperationGate(tmp_path / "coordinated")
    try:
        assert reopened.snapshot() is None
        assert _run(process_gate_binary, reopened.path).startswith("enter=1")
    finally:
        reopened.close()
