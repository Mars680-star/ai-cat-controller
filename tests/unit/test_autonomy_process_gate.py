"""Real local IPC leases; all controller/player effects remain synthetic."""

from __future__ import annotations

import multiprocessing
import os
import random
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ai_cat_controller.autonomy import AutonomyConfig, AutonomyWorker
from ai_cat_controller.core.config import Settings
from ai_cat_controller.core.process_gate import ProcessGateError, ProcessOperationGate
from ai_cat_controller.domain.personalities import PERSONALITIES
from ai_cat_controller.local_speech import LocalPhrasePlayer, LocalSpeechError, phrase_asset_path
from ai_cat_controller.services.autonomy_service import InProcessAutonomyService


def config(**changes):
    values = {
        "api_port": 8000,
        "api_key": "",
        "initial_delay_seconds": 0.0,
        "minimum_interval_seconds": 60.0,
        "maximum_interval_seconds": 60.0,
        "phrase_probability": 1.0,
        "local_speech_motion_settle_seconds": 0.0,
    }
    values.update(changes)
    return AutonomyConfig(**values)


class Client:
    def __init__(self, *, conversation=False):
        self.calls = []
        self.conversation = conversation

    def dialog_status(self):
        self.calls.append("status")
        return {
            "state": "answering" if self.conversation else "ready",
            "session_active": self.conversation,
            "can_interrupt": self.conversation,
            "stale": False,
            "sequence": 1,
            "updated_at_ms": 1,
        }

    def personality_profile(self):
        self.calls.append("profile")
        return {
            "active": True,
            "native_applied": True,
            "personality_id": PERSONALITIES[0].personality_id,
            "autonomy": {
                "action_weights": {"head/nod": 1.0},
                "phrases": [PERSONALITIES[0].proactive_phrases[0]],
            },
        }

    def start_head_action(self, *args, **kwargs):
        self.calls.append("head")

    def start_tail_action(self, *args, **kwargs):
        self.calls.append("tail")

    def speak(self, *args, **kwargs):
        self.calls.append("speak")


@pytest.fixture
def process_gate(tmp_path):
    directory = tmp_path / "gate"
    gate = ProcessOperationGate.provision(directory)
    yield gate
    gate.close()


def start_thread(function):
    result = []
    errors = []

    def run():
        try:
            result.append(function())
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, result, errors


def asset_player(tmp_path, gate, run_command):
    phrase = PERSONALITIES[0].proactive_phrases[0]
    asset = phrase_asset_path(tmp_path / "assets", PERSONALITIES[0].personality_id, phrase)
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"RIFF-test")
    marker = tmp_path / "run" / "playing"
    return LocalPhrasePlayer(
        asset_root=tmp_path / "assets", marker_path=marker,
        run_command=run_command, process_gate=gate,
    ), marker, phrase


@pytest.mark.parametrize("raw", ["", " ", "relative", "./gate", " /tmp/gate"])
def test_gate_path_explicit_invalid_values_never_disable_coordination(raw):
    with pytest.raises(ValueError, match="AI_CAT_PROCESS_GATE_PATH"):
        AutonomyConfig.from_env({"AI_CAT_PROCESS_GATE_PATH": raw})


def test_gate_path_unset_is_legacy_and_absolute_is_preserved(tmp_path):
    assert AutonomyConfig.from_env({}).process_gate_path is None
    path = tmp_path / "gate"
    assert AutonomyConfig.from_env({"AI_CAT_PROCESS_GATE_PATH": str(path)}).process_gate_path == path


def test_worker_never_provisions_missing_configured_directory(tmp_path):
    path = tmp_path / "missing"
    with pytest.raises(ProcessGateError):
        AutonomyWorker(config(process_gate_path=path), Client())
    assert not path.exists()


def test_settings_none_overrides_ambient_path_without_opening_it(tmp_path):
    service = InProcessAutonomyService.from_settings(
        Settings(process_gate_path=None),
        {"AI_CAT_PROCESS_GATE_PATH": str(tmp_path / "missing")},
    )
    assert service._worker._process_gate is None


@pytest.mark.asyncio
async def test_explicit_settings_path_overrides_environment_and_close_before_start(tmp_path):
    path = tmp_path / "gate"
    provisioned = ProcessOperationGate.provision(path)
    provisioned.close()
    service = InProcessAutonomyService.from_settings(
        Settings(process_gate_path=path), {"AI_CAT_PROCESS_GATE_PATH": ""},
    )
    assert service._worker._config.process_gate_path == path
    assert service._worker._process_gate.snapshot() is not None
    await service.close()
    with pytest.raises(ProcessGateError):
        with service._worker._process_gate.normal():
            pytest.fail("A stopped worker cannot keep admitting work")


@pytest.mark.parametrize("conversation", [False, True])
def test_closing_gate_skips_before_any_status_or_profile_read(process_gate, conversation):
    client = Client(conversation=conversation)
    worker = AutonomyWorker(config(), client, process_gate=process_gate)
    maintenance = process_gate.begin_maintenance()
    try:
        function = worker.run_conversation_motion_once if conversation else worker.run_once
        assert function() == {"executed": False, "reason": "maintenance"}
        assert client.calls == []
        assert maintenance.try_exclusive()
    finally:
        maintenance.finish()


@pytest.mark.parametrize("conversation", [False, True])
def test_profile_read_cannot_submit_old_decision_after_maintenance_starts(process_gate, conversation):
    client = Client(conversation=conversation)
    original_profile = client.personality_profile
    maintenance = []

    def profile_then_close():
        result = original_profile()
        maintenance.append(process_gate.begin_maintenance())
        return result

    client.personality_profile = profile_then_close
    worker = AutonomyWorker(config(
        conversation_motion_enabled=True, conversation_motion_delay_seconds=0,
    ), client, process_gate=process_gate)
    try:
        function = worker.run_conversation_motion_once if conversation else worker.run_once
        assert function() == {"executed": False, "reason": "maintenance"}
        assert client.calls == ["status", "profile"]
        assert maintenance[0].try_exclusive()
    finally:
        for item in maintenance:
            item.finish()


def test_local_phrase_wait_keeps_lease_but_never_plays_old_profile(process_gate):
    entered = threading.Event()
    release = threading.Event()

    class SettleEvent(threading.Event):
        def wait(self, timeout=None):
            entered.set()
            assert release.wait(2)
            return False

    class NoPlayer:
        def play(self, *args, **kwargs):
            pytest.fail("An invalidated profile must not reach playback")

    client = Client()
    worker = AutonomyWorker(config(local_speech_enabled=True), client,
                            process_gate=process_gate, stop_event=SettleEvent(), local_player=NoPlayer())
    thread, result, errors = start_thread(worker.run_once)
    maintenance = None
    try:
        assert entered.wait(2)
        maintenance = process_gate.begin_maintenance()
        assert not maintenance.try_exclusive()
        release.set()
        thread.join(2)
        assert not thread.is_alive()
        assert errors == []
        assert result == [{"executed": True, "action": "head/nod", "phrase": None}]
        assert client.calls == ["status", "profile", "head"]
        assert maintenance.try_exclusive()
    finally:
        release.set()
        thread.join(2)
        if maintenance is not None:
            maintenance.finish()


def test_cloud_phrase_is_not_submitted_after_motion_started_then_maintenance(process_gate):
    client = Client()
    maintenance = []
    original_action = client.start_head_action

    def action_then_close(*args, **kwargs):
        original_action(*args, **kwargs)
        maintenance.append(process_gate.begin_maintenance())

    client.start_head_action = action_then_close
    worker = AutonomyWorker(config(cloud_speech_enabled=True), client, process_gate=process_gate)
    try:
        assert worker.run_once() == {"executed": True, "action": "head/nod", "phrase": None}
        assert client.calls == ["status", "profile", "head"]
        assert maintenance[0].try_exclusive()
    finally:
        for item in maintenance:
            item.finish()


def test_conversation_tail_checks_maintenance_after_status_before_http(process_gate):
    client = Client(conversation=True)
    original_status = client.dialog_status
    maintenance = []

    def status_then_close():
        result = original_status()
        maintenance.append(process_gate.begin_maintenance())
        return result

    client.dialog_status = status_then_close
    worker = AutonomyWorker(config(conversation_motion_enabled=True, conversation_tail_enabled=True,
        enable_tail_motion=True, conversation_tail_delay_seconds=0), client, process_gate=process_gate)
    try:
        assert worker.run_conversation_motion_once() == {"executed": False, "reason": "maintenance"}
        assert client.calls == ["status"]
        assert worker._conversation_token is None
        assert maintenance[0].try_exclusive()
    finally:
        for item in maintenance:
            item.finish()


@pytest.mark.parametrize("outcome", ["success", "timeout", "os_error", "nonzero"])
def test_real_thread_playback_and_cleanup_hold_shared_lease(tmp_path, process_gate, outcome):
    entered = threading.Event()
    release = threading.Event()

    def run(command, **kwargs):
        entered.set()
        assert release.wait(2)
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, 0.01)
        if outcome == "os_error":
            raise OSError("synthetic player unavailable")
        return subprocess.CompletedProcess(command, 2 if outcome == "nonzero" else 0, b"", b"")

    player, marker, phrase = asset_player(tmp_path, process_gate, run)
    thread, result, errors = start_thread(lambda: player.play(PERSONALITIES[0].personality_id, phrase))
    maintenance = None
    try:
        assert entered.wait(2)
        assert marker.exists()
        maintenance = process_gate.begin_maintenance()
        assert not maintenance.try_exclusive()
        release.set()
        thread.join(2)
        assert not thread.is_alive()
        assert not marker.exists()
        assert maintenance.try_exclusive()
        if outcome == "success":
            assert len(result) == 1 and errors == []
        else:
            assert result == [] and len(errors) == 1 and isinstance(errors[0], LocalSpeechError)
    finally:
        release.set()
        thread.join(2)
        if maintenance is not None:
            maintenance.finish()


def test_player_rejects_expired_ticket_instead_of_acquiring_new_epoch(tmp_path, process_gate):
    calls = []
    player, marker, phrase = asset_player(tmp_path, process_gate, lambda *args, **kw: calls.append(args))
    ticket = process_gate.snapshot()
    assert ticket is not None
    maintenance = process_gate.begin_maintenance()
    try:
        assert maintenance.try_exclusive()
    finally:
        maintenance.finish()
    with pytest.raises(LocalSpeechError, match="maintenance"):
        player.play(PERSONALITIES[0].personality_id, phrase, expected_ticket=ticket)
    assert not marker.exists()
    assert calls == []


def test_player_cannot_ignore_expected_ticket_without_a_gate(tmp_path, process_gate):
    player = LocalPhrasePlayer(asset_root=tmp_path)
    with pytest.raises(LocalSpeechError, match="gate is unavailable"):
        player.play(PERSONALITIES[0].personality_id, PERSONALITIES[0].proactive_phrases[0],
                    expected_ticket=process_gate.snapshot())


def _child_play(directory, asset_root, marker, entered, release, outcome):
    gate = ProcessOperationGate(Path(directory))

    def run(command, **kwargs):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("synthetic playback release timed out")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    player = LocalPhrasePlayer(asset_root=Path(asset_root), marker_path=Path(marker),
                               process_gate=gate, run_command=run)
    try:
        player.play(PERSONALITIES[0].personality_id, PERSONALITIES[0].proactive_phrases[0])
        outcome.put("completed")
    except BaseException:
        outcome.put("failed")
    finally:
        gate.close()


def test_independent_player_process_blocks_exclusive_until_it_really_finishes(tmp_path):
    directory = tmp_path / "gate"
    gate = ProcessOperationGate.provision(directory)
    _, marker, _ = asset_player(tmp_path, gate, lambda *args, **kwargs: None)
    context = multiprocessing.get_context("spawn")
    entered, release, outcome = context.Event(), context.Event(), context.Queue()
    process = context.Process(target=_child_play, args=(str(directory), str(tmp_path / "assets"),
                                                       str(marker), entered, release, outcome))
    process.start()
    maintenance = None
    try:
        assert entered.wait(5)
        maintenance = gate.begin_maintenance()
        assert not maintenance.try_exclusive()
        release.set()
        process.join(5)
        assert process.exitcode == 0
        assert outcome.get(timeout=1) == "completed"
        assert not marker.exists()
        assert maintenance.try_exclusive()
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(2)
        if maintenance is not None:
            maintenance.finish()
        gate.close()
        outcome.close()


@pytest.mark.asyncio
async def test_join_timeout_never_closes_worker_gate_or_releases_live_playback(tmp_path):
    directory = tmp_path / "gate"
    gate = ProcessOperationGate.provision(directory)
    entered, release = threading.Event(), threading.Event()

    def run(command, **kwargs):
        entered.set()
        assert release.wait(3)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    player, marker, _ = asset_player(tmp_path, gate, run)
    worker = AutonomyWorker(config(process_gate_path=directory, local_speech_enabled=True),
                            Client(), local_player=player)
    worker.validate_startup = lambda: 0
    service = InProcessAutonomyService(worker, stop_timeout_seconds=0.02)
    maintenance = None
    service.start()
    try:
        assert entered.wait(2)
        with pytest.raises(TimeoutError, match="did not stop"):
            await service.close()
        assert service.running
        assert worker._process_gate.snapshot() is not None
        assert marker.exists()
        maintenance = gate.begin_maintenance()
        assert not maintenance.try_exclusive()
        release.set()
        await service.close()
        assert not service.running
        assert not marker.exists()
        assert maintenance.try_exclusive()
        with pytest.raises(ProcessGateError):
            with worker._process_gate.normal():
                pytest.fail("Worker gate must close after its actual thread exits")
    finally:
        release.set()
        await service.close()
        if maintenance is not None:
            maintenance.finish()
        gate.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux flock/process-reaping contract")
def test_opaque_player_keeps_shared_lease_after_its_python_keeper_abruptly_exits(tmp_path):
    gate = ProcessOperationGate.provision(tmp_path / "gate")
    try:
        _, marker, _ = asset_player(tmp_path, gate, lambda *args, **kwargs: None)
        result = subprocess.run(
            [sys.executable, __file__, "--crash-harness", str(tmp_path / "gate"),
             str(tmp_path / "assets"), str(marker)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
        # The harness is a subreaper and reaps both the abrupt keeper and the
        # otherwise-orphan opaque child before returning, even on assertion failure.
        assert gate.snapshot() is not None
    finally:
        gate.close()


def _opaque_player(ready, release):
    os.write(ready, f"{os.getpid()}\n".encode("ascii"))
    if not select.select([release], [], [], 8)[0] or os.read(release, 1) != b"x":
        raise RuntimeError("synthetic opaque player was not released")


def _crashing_keeper(directory, assets, marker, ready, release, crash):
    def exit_without_cleanup():
        if os.read(crash, 1) == b"x":
            os._exit(0)

    threading.Thread(target=exit_without_cleanup, daemon=True).start()
    gate = ProcessOperationGate(Path(directory))

    def real_opaque_player(command, **kwargs):
        # Preserve whatever work lease the player supplied, while also passing
        # this synthetic player's two control pipes (no audio executable runs).
        kwargs["pass_fds"] = (*kwargs.get("pass_fds", ()), ready, release)
        return subprocess.run(
            [sys.executable, __file__, "--opaque-player", str(ready), str(release)],
            **kwargs,
        )

    player = LocalPhrasePlayer(asset_root=Path(assets), marker_path=Path(marker),
        process_gate=gate, run_command=real_opaque_player)
    try:
        player.play(PERSONALITIES[0].personality_id, PERSONALITIES[0].proactive_phrases[0])
    finally:
        gate.close()


def _crash_harness(directory, assets, marker):
    # Scope subreaping to this disposable harness, never the pytest process.
    # This lets us reliably reap the opaque child after its keeper os._exit's.
    import ctypes

    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "cannot enable synthetic-process subreaping")
    ready_read, ready_write = os.pipe()
    release_read, release_write = os.pipe()
    crash_read, crash_write = os.pipe()
    gate = ProcessOperationGate(Path(directory))
    keeper = None
    child = None
    maintenance = None
    reaped = False
    try:
        keeper = subprocess.Popen(
            [sys.executable, __file__, "--crashing-keeper", directory, assets, marker,
             str(ready_write), str(release_read), str(crash_read)],
            pass_fds=(ready_write, release_read, crash_read),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True,
        )
        for descriptor in (ready_write, release_read, crash_read):
            os.close(descriptor)
        assert select.select([ready_read], [], [], 5)[0], "opaque child did not start"
        child = int(os.read(ready_read, 128).decode("ascii").strip())
        maintenance = gate.begin_maintenance()
        assert not maintenance.try_exclusive(), "keeper's live playback lease was not held"
        os.write(crash_write, b"x")
        assert keeper.wait(timeout=3) == 0
        os.kill(child, 0)
        assert not maintenance.try_exclusive(), "opaque child lost SH when keeper abruptly exited"
    finally:
        try:
            os.write(release_write, b"x")
        except BrokenPipeError:
            pass
        if keeper is not None:
            if keeper.poll() is None:
                keeper.kill()
            keeper.wait(timeout=3)
            if keeper.stderr is not None:
                keeper.stderr.close()
        if child is not None:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if os.waitpid(child, os.WNOHANG)[0] == child:
                    reaped = True
                    break
                time.sleep(0.01)
            if not reaped:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
                reaped = True
        if keeper is not None:
            # Include failures before the ready PID arrived: kill only this
            # disposable synthetic process group, then reap every adopted
            # child. No opaque descendant can outlive the harness fixture.
            try:
                os.killpg(keeper.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            while True:
                try:
                    os.waitpid(-1, 0)
                except ChildProcessError:
                    break
        for descriptor in (ready_read, release_write, crash_write):
            os.close(descriptor)
        if maintenance is not None:
            assert reaped and maintenance.try_exclusive(), "maintenance must wait for real child exit"
            maintenance.finish()
        gate.close()


if __name__ == "__main__":
    mode, *arguments = sys.argv[1:]
    if mode == "--opaque-player":
        _opaque_player(*(int(value) for value in arguments))
    elif mode == "--crashing-keeper":
        _crashing_keeper(*arguments[:3], *(int(value) for value in arguments[3:]))
    elif mode == "--crash-harness":
        _crash_harness(*arguments)
    else:
        raise RuntimeError("unknown synthetic process mode")
