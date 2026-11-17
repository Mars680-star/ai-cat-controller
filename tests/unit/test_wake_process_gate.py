"""Compile the real wake main with inert SDK/audio/process boundaries only.

The tests never load a model, access a sound device, signal a real device or
start a service. Protocol locks are the real host kernel flock implementation.
"""

from __future__ import annotations

import os
from pathlib import Path
import select
import shutil
import subprocess
import time

import pytest


ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "native/wake-word/main.cpp"


@pytest.fixture(scope="module")
def wake_host_binary(tmp_path_factory):
    directory = tmp_path_factory.mktemp("wake-process-inert")
    compiler = shutil.which("g++")
    assert compiler, "A C++17 compiler is required for the real wake main regression"
    includes = directory / "includes"
    (includes / "pulse").mkdir(parents=True)
    (includes / "backends/sensevoice").mkdir(parents=True)
    (includes / "pulse/simple.h").write_text(r'''
#pragma once
#include <cstddef>
#include <cstdint>
struct pa_simple { int direction; };
struct pa_sample_spec { int format; uint32_t rate; uint8_t channels; };
enum { PA_STREAM_RECORD = 1, PA_STREAM_PLAYBACK = 2, PA_SAMPLE_S16LE = 3 };
pa_simple* pa_simple_new(const char*, const char*, int, const char*, const char*,
    const pa_sample_spec*, const void*, const void*, int*);
int pa_simple_read(pa_simple*, void*, size_t, int*);
int pa_simple_write(pa_simple*, const void*, size_t, int*);
int pa_simple_drain(pa_simple*, int*);
int pa_simple_flush(pa_simple*, int*);
void pa_simple_free(pa_simple*);
''')
    (includes / "pulse/error.h").write_text('#pragma once\nconst char* pa_strerror(int);\n')
    (includes / "ten_vad.h").write_text(r'''
#pragma once
#include <cstddef>
#include <cstdint>
using ten_vad_handle_t = void*;
int ten_vad_create(ten_vad_handle_t*, int, float);
int ten_vad_process(ten_vad_handle_t, const int16_t*, size_t, float*, int*);
void ten_vad_destroy(ten_vad_handle_t*);
''')
    (includes / "backends/sensevoice/sensevoice_model.hpp").write_text(r'''
#pragma once
#include <initializer_list>
#include <string>
#include <vector>
std::string inert_recognize();
bool inert_initialize();
void inert_model_destroy();
namespace asr { namespace sensevoice {
class SenseVoiceModel {
public:
    struct Config {
        std::string model_path, cmvn_path, vocab_path, decoder_path, language, provider;
        int num_threads;
    };
    explicit SenseVoiceModel(const Config&) {}
    ~SenseVoiceModel() { inert_model_destroy(); }
    bool initialize() { return inert_initialize(); }
    void setHotwords(std::initializer_list<std::string>, float) {}
    std::string recognize(const std::vector<float>&) { return inert_recognize(); }
};
}}
''')
    # Redirect only the fixed runtime paths. The tested state machine, guards,
    # capture/recognize decisions and trigger functions are otherwise unchanged.
    runtime = directory / "runtime"
    runtime.mkdir()
    copied = directory / "wake-under-test.cpp"
    copied.write_text(SOURCE.read_text().replace('"/run/ai-cat', '"' + str(runtime)))
    harness = directory / "wake.cpp"
    harness.write_text(r'''
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cerrno>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <fcntl.h>
#include <iostream>
#include <spawn.h>
#include <string>
#include <sys/stat.h>
#include <sys/file.h>
#include <sys/wait.h>
#include <vector>
#include <unistd.h>
static int spawns, tones, wake_signals, interrupt_signals, capture_opens, capture_closes;
static int vad_creates, vad_destroys;
static const char* hook_mode() { const char* value = std::getenv("WAKE_FIXTURE_BLOCK"); return value ? value : ""; }
static void hook_wait(const char* message) { std::cout << message << std::endl; (void)std::getchar(); }
static int inert_spawn(pid_t* pid, const char*, const posix_spawn_file_actions_t*,
    const posix_spawnattr_t*, char* const[], char* const[]) {
    *pid = 123; ++spawns; std::cout << "HOOK spawn" << std::endl; return 0;
}
static pid_t inert_waitpid(pid_t pid, int* status, int) {
    if (!std::strcmp(hook_mode(), "service")) hook_wait("HOOK service-wait");
    *status = 0; return pid;
}
static int inert_kill(pid_t, int signal) {
    static int pid_probes;
    if (signal == 0 && ++pid_probes == 4 && !std::strcmp(hook_mode(), "final-signal"))
        hook_wait("HOOK final-signal");
    if (signal == SIGUSR1) ++wake_signals;
    if (signal == SIGUSR2) ++interrupt_signals;
    return 0;
}
static int inert_close(int descriptor) {
    int result = ::close(descriptor);
    if (!std::strcmp(hook_mode(), "publish")) hook_wait("HOOK before-publish");
    return result;
}
static int inert_flock(int descriptor, int operation) {
    int result = ::flock(descriptor, operation);
    if (result < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
        std::cout << "HOOK gate-busy" << std::endl;
    return result;
}
#define flock inert_flock
#include "ai_cat_process_gate.h"
#undef flock
#define posix_spawn inert_spawn
#define waitpid inert_waitpid
#define kill inert_kill
#define close inert_close
#define main actual_wake_main
''' + '#include "' + str(copied) + '"\n' + r'''
#undef main
#undef kill
#undef waitpid
#undef posix_spawn
#undef close
pa_simple* pa_simple_new(const char*, const char*, int direction, const char*, const char*,
    const pa_sample_spec*, const void*, const void*, int*) {
    if (direction == PA_STREAM_RECORD) { ++capture_opens; std::cout << "HOOK open" << std::endl; }
    return new pa_simple{direction};
}
int pa_simple_read(pa_simple*, void* data, size_t size, int*) {
    const char* mode = std::getenv("WAKE_FIXTURE_BLOCK");
    if (mode && !std::strcmp(mode, "read")) {
        std::cout << "HOOK read" << std::endl;
        if (std::getchar() == EOF) return -1;
    }
    std::fill_n(static_cast<int16_t*>(data), size / sizeof(int16_t), 1000);
    return 0;
}
int pa_simple_write(pa_simple*, const void*, size_t, int*) {
    ++tones; std::cout << "HOOK tone" << std::endl;
    if (!std::strcmp(hook_mode(), "tone")) hook_wait("HOOK tone-wait");
    return 0;
}
int pa_simple_drain(pa_simple*, int*) { return 0; }
int pa_simple_flush(pa_simple*, int*) { std::cout << "HOOK flush" << std::endl; return 0; }
void pa_simple_free(pa_simple* stream) {
    if (stream->direction == PA_STREAM_RECORD && !std::strcmp(hook_mode(), "free"))
        hook_wait("HOOK free-wait");
    if (stream->direction == PA_STREAM_RECORD) { ++capture_closes; std::cout << "HOOK free" << std::endl; }
    delete stream;
}
const char* pa_strerror(int) { return "inert"; }
int ten_vad_create(ten_vad_handle_t* handle, int, float) {
    ++vad_creates; *handle = new int(0);
    if (!std::strcmp(hook_mode(), "init-vad") && vad_creates == 1)
        hook_wait("HOOK vad-initialize");
    if (!std::strcmp(hook_mode(), "vad-window") && wake_signals > 0 && vad_creates == 2)
        hook_wait("HOOK wake-window-cleanup");
    return 0;
}
int ten_vad_process(ten_vad_handle_t handle, const int16_t*, size_t, float* probability, int* speech) {
    int& frames = *static_cast<int*>(handle);
    *speech = frames++ % 40 < 20;
    *probability = *speech ? 1.f : 0.f;
    return 0;
}
void ten_vad_destroy(ten_vad_handle_t* handle) {
    if (*handle) ++vad_destroys;
    delete static_cast<int*>(*handle); *handle = nullptr;
}
std::string inert_recognize() {
    std::cout << "HOOK recognize" << std::endl;
    int command = std::getchar();
    if (command == 'p') return "小安";
    if (command == 's') return "开启对话模式";
    if (command == 'i') return "小安停下";
    if (command == 'w') return "小安开启对话模式";
    if (command == EOF) exit_requested = 1;
    return "无关内容";
}
bool inert_initialize() {
    if (!std::strcmp(hook_mode(), "init-model")) hook_wait("HOOK model-initialize");
    return true;
}
void inert_model_destroy() {
    std::cout << "HOOK model-destroy" << std::endl;
    if (!std::strcmp(hook_mode(), "model-free") ||
        !std::strcmp(hook_mode(), "init-model") || !std::strcmp(hook_mode(), "init-vad"))
        (void)std::getchar();
}
int main(int argc, char** argv) {
    std::signal(SIGINT, handleSignal);
    std::signal(SIGTERM, handleSignal);
    int result;
    if (argc > 1 && !std::strcmp(argv[1], "interrupt")) result = triggerInterruption() ? 0 : 1;
    else if (argc > 1 && !std::strcmp(argv[1], "interrupt-after-exit")) {
        handleSignal(SIGTERM);
        result = triggerInterruption() ? 0 : 1;
    }
    else if (argc > 1 && (!std::strcmp(argv[1], "effect") || !std::strcmp(argv[1], "window"))) {
        pa_simple* capture = nullptr;
        WakeProcessWindow window(capture);
        int admitted = window.enter();
        std::cout << "ADMITTED " << admitted << std::endl;
        if (admitted) return 2;
        pa_sample_spec spec{PA_SAMPLE_S16LE, 16000, 1};
        int error = 0;
        if (!std::strcmp(argv[1], "window")) capture = openCaptureStream(spec, &error, window);
        hook_wait("HOOK admitted");
        if (!std::strcmp(argv[1], "window")) { window.release(); result = 0; }
        else {
            std::string effect = argc > 2 ? argv[2] : "trigger";
            if (effect == "service") result = startDialogService(window) ? 0 : 1;
            else if (effect == "tone") result = playImmediateReadyTone(spec, window) ? 0 : 1;
            else if (effect == "write") result = writeWakePrerollAtomically(std::vector<int16_t>(256), window) ? 0 : 1;
            else if (effect == "preroll") {
                std::vector<int16_t> samples; bool captured = false;
                result = captureWakePrerollUntilReady(FileVersion{}, false, spec, samples, captured, window) ? 0 : 1;
                std::cout << "PREROLL size=" << samples.size() << " captured=" << captured << std::endl;
            } else result = triggerConversation(spec, window) ? 0 : 1;
        }
    }
    else result = actual_wake_main(argc, argv);
    std::cout << "VAD creates=" << vad_creates << " destroys=" << vad_destroys << std::endl;
    std::cout << "COUNTERS spawn=" << spawns << " tone=" << tones
        << " wake=" << wake_signals << " interrupt=" << interrupt_signals
        << " open=" << capture_opens << " free=" << capture_closes << std::endl;
    return result;
}
''')
    binary = directory / "wake"
    built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-pthread",
        "-I", str(includes), "-I", str(ROOT / "native/common"), str(harness), "-o", str(binary)],
        capture_output=True, text=True, timeout=30)
    assert built.returncode == 0, built.stderr
    return binary, runtime


def _environment(path=None, block="read"):
    result = dict(os.environ)
    result.pop("AI_CAT_PROCESS_GATE_PATH", None)
    if path is not None:
        result["AI_CAT_PROCESS_GATE_PATH"] = str(path)
    result["WAKE_FIXTURE_BLOCK"] = block
    return result


def test_real_wake_main_builds_without_sdk_and_preserves_safe_interrupt_bypass(wake_host_binary, tmp_path):
    binary, runtime = wake_host_binary
    status = runtime / "dialog-status.json"
    status.write_text('{"pid":321,"state":"answering","session_active":true,"can_interrupt":true}')
    try:
        result = subprocess.run([str(binary), "interrupt"], env=_environment(tmp_path / "missing"),
            capture_output=True, text=True, timeout=3)
        assert result.returncode == 0, result.stderr
        assert "COUNTERS spawn=0 tone=0 wake=0 interrupt=1 open=0 free=0" in result.stdout
    finally:
        status.unlink()


@pytest.fixture(autouse=True)
def empty_runtime(wake_host_binary):
    _, runtime = wake_host_binary
    for path in runtime.iterdir():
        path.unlink()
    yield
    for path in runtime.iterdir():
        path.unlink()


def _start(binary, path, block="read", args=()):
    child = subprocess.Popen([str(binary), *args], env=_environment(path, block),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    child.wake_buffer = b""
    child.wake_output = []
    return child


def _until(child, marker):
    deadline = time.monotonic() + 3
    while True:
        while b"\n" in child.wake_buffer:
            line, child.wake_buffer = child.wake_buffer.split(b"\n", 1)
            text = line.decode()
            child.wake_output.append(text)
            if marker in text:
                return text
        remaining = deadline - time.monotonic()
        assert remaining > 0 and select.select([child.stdout], [], [], remaining)[0], (
            marker, child.wake_output)
        chunk = os.read(child.stdout.fileno(), 4096)
        assert chunk, (marker, child.wake_output, child.poll())
        child.wake_buffer += chunk


def _send(child, value):
    child.stdin.write(value)
    child.stdin.flush()


def _finish(child, *, stop=False):
    if stop and child.poll() is None:
        child.terminate()
    stdout, stderr = child.communicate("" if child.poll() is None else None, timeout=3)
    output = "\n".join(child.wake_output) + "\n" + child.wake_buffer.decode() + stdout
    return output, stderr


def _exclusive(maintenance):
    deadline = time.monotonic() + 3
    while not maintenance.try_exclusive():
        assert time.monotonic() < deadline, "Real wake capture/cleanup retained its lease"
        time.sleep(0.001)


def _gate(tmp_path):
    from ai_cat_controller.core.process_gate import ProcessOperationGate

    return ProcessOperationGate.provision(tmp_path / "coordination")


@pytest.mark.parametrize("block,marker", [("read", "HOOK read"), ("asr", "HOOK recognize")])
def test_actual_main_drops_work_crossing_capture_or_asr_closing(wake_host_binary, tmp_path, block, marker):
    binary, _ = wake_host_binary
    gate = _gate(tmp_path)
    child = _start(binary, gate.path, block)
    maintenance = None
    try:
        _until(child, marker)
        maintenance = gate.begin_maintenance()
        assert maintenance.try_exclusive() is False
        _send(child, "w")
        _until(child, "HOOK free")
        _exclusive(maintenance)
        output, stderr = _finish(child, stop=True)
        assert child.returncode == 0, stderr
        assert "COUNTERS spawn=0 tone=0 wake=0 interrupt=0 open=1 free=1" in output
        assert "[WakeWord] Matched:" not in output
    finally:
        if child.poll() is None:
            _finish(child, stop=True)
        if maintenance is not None:
            maintenance.abandon()
        gate.close()


@pytest.mark.parametrize("effect", ["service", "tone", "preroll", "write", "trigger"])
def test_every_direct_wake_effect_rejects_the_original_revoked_window(wake_host_binary, tmp_path, effect):
    binary, runtime = wake_host_binary
    gate = _gate(tmp_path)
    child = _start(binary, gate.path, "effect", ("effect", effect))
    maintenance = None
    try:
        _until(child, "HOOK admitted")
        maintenance = gate.begin_maintenance()
        assert maintenance.try_exclusive() is False
        _send(child, "x")
        output, stderr = _finish(child)
        assert child.returncode == 1, stderr
        assert "COUNTERS spawn=0 tone=0 wake=0 interrupt=0 open=0 free=0" in output
        assert not list(runtime.glob("wake-preroll*"))
        assert maintenance.try_exclusive() is True
    finally:
        if child.poll() is None:
            _finish(child, stop=True)
        if maintenance is not None:
            maintenance.abandon()
        gate.close()


def test_real_pa_free_must_finish_before_python_can_acquire_exclusive(wake_host_binary, tmp_path):
    binary, _ = wake_host_binary
    gate = _gate(tmp_path)
    child = _start(binary, gate.path, "free", ("window",))
    maintenance = None
    try:
        _until(child, "HOOK admitted")
        maintenance = gate.begin_maintenance()
        _send(child, "r")
        _until(child, "HOOK free-wait")
        assert maintenance.try_exclusive() is False
        _send(child, "f")
        output, stderr = _finish(child)
        assert child.returncode == 0, stderr
        assert "open=1 free=1" in output
        assert maintenance.try_exclusive() is True
    finally:
        if child.poll() is None:
            _send(child, "f")
            _finish(child, stop=True)
        if maintenance is not None:
            maintenance.abandon()
        gate.close()


def test_startup_closing_parks_without_opening_any_audio(wake_host_binary, tmp_path):
    binary, _ = wake_host_binary
    gate = _gate(tmp_path)
    maintenance = gate.begin_maintenance()
    child = _start(binary, gate.path)
    try:
        _until(child, "HOOK gate-busy")
        assert maintenance.try_exclusive() is True
        output, stderr = _finish(child, stop=True)
        assert child.returncode == 0, stderr
        assert "COUNTERS spawn=0 tone=0 wake=0 interrupt=0 open=0 free=0" in output
    finally:
        if child.poll() is None:
            _finish(child, stop=True)
        maintenance.abandon()
        gate.close()


@pytest.mark.parametrize("path", ["", "relative", "/not-a-real-wake-gate"])
def test_explicit_invalid_gate_never_silently_uses_legacy_capture(wake_host_binary, path):
    binary, _ = wake_host_binary
    result = subprocess.run([str(binary)], env=_environment(path), capture_output=True,
        text=True, timeout=3)
    assert result.returncode == 1
    assert "Invalid process gate" in result.stderr
    assert "open=0 free=0" in result.stdout


def test_prefix_and_pending_utterance_are_not_replayed_after_epoch_changes(wake_host_binary, tmp_path):
    binary, _ = wake_host_binary
    gate = _gate(tmp_path)
    child = _start(binary, gate.path, "asr")
    maintenance = None
    try:
        _until(child, "HOOK recognize")
        _send(child, "p")
        _until(child, "Activation prefix accepted")
        _until(child, "HOOK recognize")
        maintenance = gate.begin_maintenance()
        _send(child, "w")  # A complete command, but decoded in the old window.
        _until(child, "HOOK free")
        _exclusive(maintenance)
        maintenance.finish()
        _until(child, "HOOK recognize")
        _send(child, "s")  # Must not combine with the previous epoch's prefix.
        _until(child, "HOOK recognize")
        output, stderr = _finish(child, stop=True)
        assert child.returncode == 0, stderr
        assert "COUNTERS spawn=0 tone=0 wake=0 interrupt=0 open=2 free=2" in output
        assert "[WakeWord] Matched:" not in output
    finally:
        if child.poll() is None:
            _finish(child, stop=True)
        if maintenance is not None:
            maintenance.abandon()
        gate.close()


@pytest.mark.parametrize("replaced", [False, True])
def test_final_signal_revocation_cleans_own_published_preroll_but_preserves_observed_replacement(wake_host_binary, tmp_path, replaced):
    binary, runtime = wake_host_binary
    (runtime / "dialog-status.json").write_text('{"pid":321,"state":"ready","session_active":false}')
    gate = _gate(tmp_path)
    child = _start(binary, gate.path, "final-signal", ("effect", "trigger"))
    maintenance = None
    handoff = runtime / "wake-preroll.pcm"
    try:
        _until(child, "HOOK admitted")
        _send(child, "x")
        _until(child, "HOOK final-signal")
        assert handoff.exists()
        if replaced:
            replacement = runtime / "replacement"
            replacement.write_bytes(b"another-producer")
            replacement.replace(handoff)
        maintenance = gate.begin_maintenance()
        assert maintenance.try_exclusive() is False
        _send(child, "r")
        output, stderr = _finish(child)
        assert child.returncode == 1, stderr
        assert "COUNTERS spawn=0 tone=1 wake=0 interrupt=0 open=1 free=1" in output
        assert maintenance.try_exclusive() is True
        if replaced:
            assert handoff.read_bytes() == b"another-producer"
        else:
            assert not handoff.exists()
        assert not (runtime / "wake-preroll.pcm.tmp").exists()
    finally:
        if child.poll() is None:
            _finish(child, stop=True)
        if maintenance is not None:
            maintenance.abandon()
        gate.close()


@pytest.mark.parametrize("effect,block,marker", [
    ("service", "service", "HOOK service-wait"),
    ("tone", "tone", "HOOK tone-wait"),
    ("write", "publish", "HOOK before-publish"),
])
def test_blocked_effect_completion_keeps_original_lease_and_does_not_publish_or_trigger_later(wake_host_binary, tmp_path, effect, block, marker):
    binary, runtime = wake_host_binary
    gate = _gate(tmp_path)
    child = _start(binary, gate.path, block, ("effect", effect))
    maintenance = None
    try:
        _until(child, "HOOK admitted")
        _send(child, "x")
        _until(child, marker)
        maintenance = gate.begin_maintenance()
        assert maintenance.try_exclusive() is False
        _send(child, "r")
        output, stderr = _finish(child)
        assert child.returncode == 1, stderr
        assert "wake=0 interrupt=0" in output
        assert maintenance.try_exclusive() is True
        assert not list(runtime.glob("wake-preroll*"))
        if effect == "tone":
            assert "HOOK flush" in output
    finally:
        if child.poll() is None:
            _finish(child, stop=True)
        if maintenance is not None:
            maintenance.abandon()
        gate.close()


@pytest.mark.parametrize("configured", [False, True])
def test_normal_wake_still_plays_tone_publishes_preroll_and_signals_once(wake_host_binary, tmp_path, configured):
    binary, runtime = wake_host_binary
    (runtime / "dialog-status.json").write_text('{"pid":321,"state":"ready","session_active":false}')
    gate = _gate(tmp_path) if configured else None
    child = _start(binary, gate.path if gate else None, "vad-window")
    maintenance = None
    try:
        _until(child, "HOOK recognize")
        _send(child, "w")
        _until(child, "Conversation triggered")
        # The real window is now inside required VAD cleanup, not concurrently
        # taking an admission-SH snapshot. This handshake avoids treating a
        # legitimate nonblocking admission conflict as a failed positive case.
        _until(child, "HOOK wake-window-cleanup")
        if gate:
            maintenance = gate.begin_maintenance()
            assert maintenance.try_exclusive() is False
        _send(child, "v")
        if gate:
            _exclusive(maintenance)
        output, stderr = _finish(child, stop=True)
        assert child.returncode == 0, stderr
        assert "COUNTERS spawn=0 tone=1 wake=1 interrupt=0 open=2 free=2" in output
        # A successful trigger closes a window too: acoustic history must be
        # reset before its SH is released, not only on a later failed check.
        assert "VAD creates=2 destroys=2" in output
        assert (runtime / "wake-preroll.pcm").stat().st_size == 256 * 2
    finally:
        if child.poll() is None:
            _finish(child, stop=True)
        if maintenance:
            maintenance.abandon()
        if gate:
            gate.close()


def test_fresh_interrupt_recognition_works_after_old_window_was_revoked(wake_host_binary, tmp_path):
    binary, runtime = wake_host_binary
    (runtime / "dialog-status.json").write_text('{"pid":321,"state":"answering","session_active":true,"can_interrupt":true}')
    gate = _gate(tmp_path)
    child = _start(binary, gate.path, "asr")
    maintenance = None
    try:
        _until(child, "HOOK recognize")
        maintenance = gate.begin_maintenance()
        _send(child, "i")
        _until(child, "HOOK free")
        _exclusive(maintenance)
        maintenance.finish()
        _until(child, "HOOK recognize")
        _send(child, "i")
        _until(child, "Conversation interrupted by local keyword")
        output, stderr = _finish(child, stop=True)
        assert child.returncode == 0, stderr
        assert "COUNTERS spawn=0 tone=0 wake=0 interrupt=1 open=2 free=2" in output
    finally:
        if child.poll() is None:
            _finish(child, stop=True)
        if maintenance:
            maintenance.abandon()
        gate.close()


def test_cloud_pause_does_not_open_capture_or_keep_a_work_lease(wake_host_binary, tmp_path):
    binary, runtime = wake_host_binary
    (runtime / "dialog-status.json").write_text('{"pid":321,"state":"listening","session_active":true,"can_interrupt":false}')
    gate = _gate(tmp_path)
    child = _start(binary, gate.path)
    maintenance = None
    try:
        _until(child, "Capture paused for active conversation")
        maintenance = gate.begin_maintenance()
        _exclusive(maintenance)
        output, stderr = _finish(child, stop=True)
        assert child.returncode == 0, stderr
        assert "open=0 free=0" in output
        assert "VAD creates=1 destroys=1" in output  # No pointless 20-Hz resets.
    finally:
        if child.poll() is None:
            _finish(child, stop=True)
        if maintenance:
            maintenance.abandon()
        gate.close()


def test_repo_and_sdk_cmake_find_the_same_frozen_gate_header():
    canonical = (ROOT / "native/wake-word/CMakeLists.txt").read_text()
    overlay = (ROOT / "integrations/volcengine-k1/overlay/examples/low_load_solution/linux_k1/wake_word/CMakeLists.txt").read_text()
    script = (ROOT / "integrations/volcengine-k1/scripts/prepare_sdk.sh").read_text()
    assert "${CMAKE_CURRENT_SOURCE_DIR}/../common" in canonical
    assert "${CMAKE_CURRENT_SOURCE_DIR}/../../common" in overlay
    assert '"$SDK_DIR/examples/low_load_solution/common/ai_cat_process_gate.h"' in script
    assert script.index('cp -a "$INTEGRATION_DIR/overlay/."') < script.index('cp "$REPO_DIR/native/wake-word/main.cpp"')


def test_active_normal_exit_keeps_work_lease_through_model_destruction(wake_host_binary, tmp_path):
    binary, _ = wake_host_binary
    gate = _gate(tmp_path)
    child = _start(binary, gate.path, "model-free")
    maintenance = None
    try:
        _until(child, "HOOK recognize")
        child.terminate()  # Set real main's SIGTERM flag while decode is active.
        _send(child, "x")
        _until(child, "HOOK model-destroy")
        maintenance = gate.begin_maintenance()
        assert maintenance.try_exclusive() is False
        _send(child, "m")
        output, stderr = _finish(child)
        assert child.returncode == 0, stderr
        assert "open=1 free=1" in output
        assert maintenance.try_exclusive() is True
    finally:
        if child.poll() is None:
            _finish(child, stop=True)
        if maintenance:
            maintenance.abandon()
        gate.close()


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("boundary", ["read", "asr"])
def test_sigterm_during_blocked_read_or_asr_does_not_start_a_new_normal_effect(wake_host_binary, tmp_path, configured, boundary):
    binary, runtime = wake_host_binary
    gate = _gate(tmp_path) if configured else None
    child = _start(binary, gate.path if gate else None, boundary)
    try:
        if boundary == "read":
            # Release the first 37 original frames. The 38th finishes the same
            # unchanged 20 speech + 18 silence VAD window and would decode.
            for _ in range(37):
                _until(child, "HOOK read")
                _send(child, "r")
            _until(child, "HOOK read")
            child.terminate()
            _send(child, "rw")
        else:
            _until(child, "HOOK recognize")
            child.terminate()
            _send(child, "w")
        output, stderr = _finish(child)
        assert child.returncode == 0, stderr
        if gate:
            assert gate.snapshot() is not None  # No maintenance/C transition.
        assert "COUNTERS spawn=0 tone=0 wake=0 interrupt=0 open=1 free=1" in output
        assert "[WakeWord] Matched:" not in output
        assert not list(runtime.glob("wake-preroll*"))
        if boundary == "read":
            assert "HOOK recognize" not in output
    finally:
        if child.poll() is None:
            _finish(child, stop=True)
        if gate:
            gate.close()


@pytest.mark.parametrize("effect", ["service", "tone", "preroll", "write", "trigger"])
def test_sigterm_requested_before_direct_normal_effect_is_not_ignored(wake_host_binary, tmp_path, effect):
    binary, runtime = wake_host_binary
    gate = _gate(tmp_path)
    child = _start(binary, gate.path, "effect", ("effect", effect))
    try:
        _until(child, "HOOK admitted")
        child.terminate()
        _send(child, "x")
        output, stderr = _finish(child)
        assert child.returncode == 1, stderr
        assert gate.snapshot() is not None
        assert "COUNTERS spawn=0 tone=0 wake=0 interrupt=0 open=0 free=0" in output
        assert not list(runtime.glob("wake-preroll*"))
    finally:
        if child.poll() is None:
            _finish(child, stop=True)
        gate.close()


@pytest.mark.parametrize("effect,block,marker", [
    ("service", "service", "HOOK service-wait"),
    ("tone", "tone", "HOOK tone-wait"),
    ("write", "publish", "HOOK before-publish"),
    ("trigger", "final-signal", "HOOK final-signal"),
])
def test_sigterm_crossing_an_effect_boundary_cancels_remaining_publication_and_signal(wake_host_binary, tmp_path, effect, block, marker):
    binary, runtime = wake_host_binary
    if effect == "trigger":
        (runtime / "dialog-status.json").write_text('{"pid":321,"state":"ready","session_active":false}')
    gate = _gate(tmp_path)
    child = _start(binary, gate.path, block, ("effect", effect))
    try:
        _until(child, "HOOK admitted")
        _send(child, "x")
        _until(child, marker)
        child.terminate()
        _send(child, "r")
        output, stderr = _finish(child)
        assert child.returncode == 1, stderr
        assert gate.snapshot() is not None
        assert "wake=0 interrupt=0" in output
        assert not list(runtime.glob("wake-preroll*"))
        if effect == "tone":
            assert "HOOK flush" in output
    finally:
        if child.poll() is None:
            _finish(child, stop=True)
        gate.close()


def test_exit_requested_does_not_disable_independent_safe_interrupt(wake_host_binary, tmp_path):
    binary, runtime = wake_host_binary
    (runtime / "dialog-status.json").write_text('{"pid":321,"state":"answering","session_active":true,"can_interrupt":true}')
    result = subprocess.run([str(binary), "interrupt-after-exit"], env=_environment(tmp_path / "missing"),
        capture_output=True, text=True, timeout=3)
    assert result.returncode == 0, result.stderr
    assert "COUNTERS spawn=0 tone=0 wake=0 interrupt=1 open=0 free=0" in result.stdout


@pytest.mark.parametrize("stage", ["model", "vad"])
def test_initialization_exit_preserves_the_original_lease_through_model_destruction(wake_host_binary, tmp_path, stage):
    binary, _ = wake_host_binary
    gate = _gate(tmp_path)
    child = _start(binary, gate.path, "init-" + stage)
    maintenance = None
    try:
        _until(child, "HOOK " + stage + "-initialize")
        assert gate.snapshot() is not None  # No maintenance has started yet.
        child.terminate()
        _send(child, "i")
        _until(child, "HOOK model-destroy")
        maintenance = gate.begin_maintenance()
        # initialize returned, but real model destruction is still blocked.
        # The initial idle release must not have dropped this actual-work SH.
        assert maintenance.try_exclusive() is False
        _send(child, "d")
        output, stderr = _finish(child)
        assert child.returncode == 0, stderr
        assert maintenance.try_exclusive() is True
        assert "COUNTERS spawn=0 tone=0 wake=0 interrupt=0 open=0 free=0" in output
        if stage == "model":
            assert "VAD creates=0 destroys=0" in output
        else:
            assert "VAD creates=1 destroys=1" in output
    finally:
        if child.poll() is None:
            _finish(child, stop=True)
        if maintenance:
            maintenance.abandon()
        gate.close()
