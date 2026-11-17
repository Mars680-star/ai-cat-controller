"""Direct collaborators retain the original request and actual-work owner."""

import asyncio
from contextvars import copy_context
import os
import subprocess
import threading

import pytest

from ai_cat_controller.core.errors import ActionConflictError
from ai_cat_controller.core.operation_gate import DeviceOperationGate, current_process_work_lease
from ai_cat_controller.core.process_gate import ProcessGateError, ProcessOperationGate
from ai_cat_controller.domain.personalities import PERSONALITIES
from ai_cat_controller.local_speech import LocalPhrasePlayer, LocalSpeechError, phrase_asset_path


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,closed_capture", [
    ("local", False), ("same", False), ("peer", False),
    ("same", True), ("peer", True),
])
async def test_nested_scope_never_refreshes_original_ticket(cohort, mode, closed_capture):
    first, second, _ = cohort
    ingress = DeviceOperationGate(process_gate=None if mode == "local" else first)
    nested = DeviceOperationGate(process_gate=second) if mode == "peer" else ingress
    effects = []
    maintenance = None

    async def effect():
        effects.append("old request wrote after maintenance")

    if closed_capture:
        maintenance = second.begin_maintenance()
        assert maintenance.try_exclusive()
    try:
        with ingress.request_scope():
            if closed_capture:
                maintenance.finish()
            elif mode == "local":
                await ingress.maintenance(asyncio.sleep(0))
            else:
                maintenance = second.begin_maintenance()
                assert maintenance.try_exclusive()
                maintenance.finish()
            with nested.request_scope():
                # Scope entry is always legal for reads, but must preserve the
                # original owner/epoch/None instead of granting a fresh intent.
                with pytest.raises(ActionConflictError):
                    await nested.process_context_gate().operation(effect())
        assert effects == []
    finally:
        if maintenance is not None:
            maintenance.finish()
        await ingress.shutdown()
        if nested is not ingress:
            await nested.shutdown()


@pytest.mark.asyncio
async def test_peer_scope_preserves_origin_object_not_its_own_local_generation(cohort):
    first, second, _ = cohort
    ingress = DeviceOperationGate(process_gate=first)
    nested = DeviceOperationGate(process_gate=second)
    await nested.maintenance(asyncio.sleep(0))
    assert ingress.epoch != nested.epoch
    try:
        with ingress.request_scope():
            origin = ingress._ticket()
            with nested.request_scope():
                assert nested._ticket() is origin
                assert origin.gate is ingress
                assert nested.process_context_gate() is ingress
                # Even a direct peer operation validates the original owner's
                # generation, not the unrelated numeric peer generation.
                assert await nested.operation(asyncio.sleep(0, result="current origin")) == "current origin"
    finally:
        await ingress.shutdown()
        await nested.shutdown()


@pytest.mark.asyncio
async def test_peer_direct_entries_cannot_outlive_origin_shutdown(cohort):
    first, second, _ = cohort
    ingress = DeviceOperationGate(process_gate=first)
    nested = DeviceOperationGate(process_gate=second)
    effects = []

    async def effect():
        effects.append(True)

    try:
        with ingress.request_scope(), nested.request_scope():
            await ingress.shutdown()  # No IPC epoch change: owner identity matters.
            with pytest.raises(ActionConflictError):
                await nested.operation(effect())
            with pytest.raises(ActionConflictError):
                nested.checkpoint()
            with pytest.raises(ActionConflictError):
                await nested.maintenance(effect())
        assert effects == []
        assert await nested.operation(asyncio.sleep(0, result="new request")) == "new request"
    finally:
        await ingress.shutdown()
        await nested.shutdown()


@pytest.mark.asyncio
async def test_unrelated_domain_scope_does_not_replace_or_inherit_original_origin(cohort):
    first, second, unrelated = cohort
    ingress = DeviceOperationGate(process_gate=first)
    nested = DeviceOperationGate(process_gate=second)
    other = DeviceOperationGate(process_gate=unrelated)
    try:
        with ingress.request_scope():
            origin = ingress._ticket()
            maintenance = second.begin_maintenance()
            try:
                assert maintenance.try_exclusive()
            finally:
                maintenance.finish()
            with other.request_scope():
                assert other._ticket().gate is other
                assert await other.operation(asyncio.sleep(0, result="other device")) == "other device"
                with nested.request_scope():
                    assert nested._ticket() is origin
                    with pytest.raises(ActionConflictError):
                        await nested.operation(asyncio.sleep(0))
    finally:
        await ingress.shutdown()
        await nested.shutdown()
        await other.shutdown()


@pytest.mark.asyncio
async def test_scope_reads_remain_available_with_invalid_pins_and_writes_fail_closed(cohort):
    first, second, _ = cohort
    ingress = DeviceOperationGate(process_gate=first)
    nested = DeviceOperationGate(process_gate=second)
    try:
        with ingress.request_scope():
            os.rename(first.path / "work.lock", first.path / "retired-work")
            (first.path / "work.lock").touch(mode=0o600)
            with nested.request_scope():
                assert nested.active_operations == 0
                with pytest.raises(ActionConflictError):
                    await nested.operation(asyncio.sleep(0))
    finally:
        await ingress.shutdown()
        await nested.shutdown()


@pytest.mark.asyncio
async def test_unavailable_origin_none_is_not_refreshed_when_same_pins_recover(cohort):
    first, second, _ = cohort
    ingress = DeviceOperationGate(process_gate=first)
    nested = DeviceOperationGate(process_gate=second)
    os.rename(first.path / "work.lock", first.path / "retired-work")
    try:
        with ingress.request_scope():
            origin = ingress._ticket()
            assert origin.process_ticket is None
            os.rename(first.path / "retired-work", first.path / "work.lock")
            with nested.request_scope():
                assert nested._ticket() is origin
                with pytest.raises(ActionConflictError):
                    await nested.operation(asyncio.sleep(0))
        assert await nested.operation(asyncio.sleep(0, result="fresh ingress")) == "fresh ingress"
    finally:
        await ingress.shutdown()
        await nested.shutdown()


@pytest.mark.asyncio
async def test_active_work_and_required_child_survive_nested_none_scope_during_closing(cohort):
    first, second, _ = cohort
    ingress = DeviceOperationGate(process_gate=first)
    nested = DeviceOperationGate(process_gate=second)
    maintenance = []
    effects = []

    async def required_effect():
        effects.append("settled")

    async def actual():
        maintenance.append(second.begin_maintenance())
        assert not maintenance[0].try_exclusive()
        with ingress.request_scope(), nested.request_scope():
            assert ingress._ticket().process_ticket is None
            selected = nested.process_context_gate()
            assert selected is ingress
            await selected.operation(required_effect())
            await selected.spawn(required_effect())
            with pytest.raises(ActionConflictError):
                selected.checkpoint()  # Optional work still must stop.

    try:
        await ingress.operation(actual())
        assert effects == ["settled", "settled"]
        assert maintenance[0].try_exclusive()
    finally:
        for item in maintenance:
            item.finish()
        await ingress.shutdown()
        await nested.shutdown()


@pytest.fixture
def cohort(tmp_path):
    first = ProcessOperationGate.provision(tmp_path / "coordination")
    second = ProcessOperationGate(first.path)
    unrelated = ProcessOperationGate.provision(tmp_path / "unrelated")
    yield first, second, unrelated
    first.close()
    second.close()
    unrelated.close()


def player(tmp_path, run_command, process_gate=None):
    personality = PERSONALITIES[0]
    phrase = personality.proactive_phrases[0]
    path = phrase_asset_path(tmp_path / "assets", personality.personality_id, phrase)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic-only")
    return LocalPhrasePlayer(asset_root=tmp_path / "assets", marker_path=tmp_path / "marker",
        run_command=run_command, process_gate=process_gate), personality.personality_id, phrase


def test_domain_requires_live_pins_not_path_alone(cohort):
    first, second, unrelated = cohort
    assert first.same_domain(second)
    assert not first.same_domain(unrelated)
    os.rename(first.path / "work.lock", first.path / "retired-work")
    (first.path / "work.lock").touch(mode=0o600)
    with pytest.raises(ProcessGateError):
        first.same_domain(second)


def test_closed_peer_cannot_match_same_path(cohort):
    first, second, _ = cohort
    second.close()
    with pytest.raises(ProcessGateError):
        first.same_domain(second)


def test_domain_rejects_changed_incarnation(cohort):
    first, second, _ = cohort
    path = first.path / "state"
    data = bytearray(path.read_bytes())
    data[4] ^= 1
    path.write_bytes(data)
    with pytest.raises(ProcessGateError):
        first.same_domain(second)


@pytest.mark.asyncio
async def test_context_selection_preserves_old_ingress_without_taking_new_ticket(cohort):
    first, second, unrelated = cohort
    ingress = DeviceOperationGate(process_gate=first)
    direct = DeviceOperationGate(process_gate=second)
    other_device = DeviceOperationGate(process_gate=unrelated)
    called = []
    async def effect():
        called.append(True)
    with ingress.request_scope():
        assert direct.process_context_gate() is ingress
        assert other_device.process_context_gate() is other_device
        maintenance = second.begin_maintenance()
        assert maintenance.try_exclusive()
        maintenance.finish()
        with pytest.raises(ActionConflictError):
            await direct.process_context_gate().operation(effect())
    assert not called
    await direct.process_context_gate().operation(effect())
    assert called == [True]
    await ingress.shutdown()
    await direct.shutdown()
    await other_device.shutdown()


@pytest.mark.asyncio
async def test_old_scope_captured_when_closed_cannot_refresh_after_reopening(cohort):
    first, second, _ = cohort
    ingress = DeviceOperationGate(process_gate=first)
    direct = DeviceOperationGate(process_gate=second)
    maintenance = second.begin_maintenance()
    assert maintenance.try_exclusive()
    with ingress.request_scope():
        maintenance.finish()
        with pytest.raises(ActionConflictError):
            await direct.process_context_gate().operation(asyncio.sleep(0))
    await ingress.shutdown()
    await direct.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("external_maintenance", [False, True])
async def test_delayed_unreserved_child_cannot_refresh_released_parent_scope(cohort, external_maintenance):
    first, second, _ = cohort
    ingress = DeviceOperationGate(process_gate=first)
    direct = DeviceOperationGate(process_gate=second)
    resume = asyncio.Event()
    children, effects = [], []
    async def effect():
        effects.append(True)
    async def delayed():
        await resume.wait()
        with pytest.raises(ActionConflictError):
            await direct.process_context_gate().operation(effect())
        with pytest.raises(ActionConflictError):
            ingress.checkpoint()
        with pytest.raises(ActionConflictError):
            await ingress.maintenance(effect())
    async def capture():
        # Not a gate.spawn reservation: inheriting context is not admission.
        children.append(asyncio.create_task(delayed()))
    try:
        await ingress.operation(capture())
        if external_maintenance:
            maintenance = second.begin_maintenance()
            assert maintenance.try_exclusive()
            maintenance.finish()
        resume.set()
        await children[0]
        assert not effects
    finally:
        resume.set()
        await asyncio.gather(*children, return_exceptions=True)
        await ingress.shutdown()
        await direct.shutdown()


@pytest.mark.asyncio
async def test_injected_gate_must_match_explicit_configured_path(cohort):
    first, _, unrelated = cohort
    absent = DeviceOperationGate()
    wrong = DeviceOperationGate(process_gate=unrelated)
    for gate in (absent, wrong):
        with pytest.raises(ActionConflictError):
            gate.process_context_gate(expected_path=first.path)
        await gate.shutdown()


@pytest.mark.asyncio
async def test_required_nested_call_uses_original_owner_while_global_closing(cohort):
    first, second, _ = cohort
    ingress = DeviceOperationGate(process_gate=first)
    direct = DeviceOperationGate(process_gate=second)
    entered, resume, child_started, child_release = [asyncio.Event() for _ in range(4)]
    children = []
    async def child():
        child_started.set()
        await child_release.wait()
    async def actual():
        entered.set()
        await resume.wait()
        selected = direct.process_context_gate()
        assert selected is ingress
        await selected.operation(asyncio.sleep(0))  # same-task atomic continuation
        children.append(asyncio.create_task(selected.operation(child())))
        await child_started.wait()
    original = asyncio.create_task(ingress.operation(actual()))
    await entered.wait()
    maintenance = second.begin_maintenance()
    try:
        resume.set()
        await original
        assert not maintenance.try_exclusive()
        child_release.set()
        await children[0]
        assert maintenance.try_exclusive()
    finally:
        resume.set()
        child_release.set()
        await asyncio.gather(original, *children, return_exceptions=True)
        maintenance.finish()
        await ingress.shutdown()
        await direct.shutdown()


@pytest.mark.asyncio
async def test_service_player_inherits_shared_fd_without_explicit_player_coordinator(tmp_path, cohort):
    first, second, _ = cohort
    gate = DeviceOperationGate(process_gate=first)
    retained = []
    def synthetic_player(_, **kwargs):
        inherited, = kwargs["pass_fds"]
        retained.append(os.dup(inherited))
        return subprocess.CompletedProcess([], 0, b"", b"")
    local, personality, phrase = player(tmp_path, synthetic_player)
    try:
        await gate.operation(asyncio.to_thread(local.play, personality, phrase))
        assert not local._marker_path.exists()
        maintenance = second.begin_maintenance()
        try:
            assert not maintenance.try_exclusive()  # opaque child still owns dup
            os.close(retained.pop())
            assert maintenance.try_exclusive()
        finally:
            maintenance.finish()
    finally:
        for descriptor in retained:
            os.close(descriptor)
        await gate.shutdown()


@pytest.mark.asyncio
async def test_service_player_does_not_drop_real_thread_lease_when_caller_leaves(tmp_path, cohort):
    first, second, _ = cohort
    gate = DeviceOperationGate(process_gate=first)
    entered, release = threading.Event(), threading.Event()
    def synthetic_player(_, **kwargs):
        assert len(kwargs["pass_fds"]) == 1
        entered.set()
        assert release.wait(3)
        return subprocess.CompletedProcess([], 0, b"", b"")
    local, personality, phrase = player(tmp_path, synthetic_player)
    caller = asyncio.create_task(gate.operation(asyncio.to_thread(local.play, personality, phrase)))
    assert await asyncio.to_thread(entered.wait, 2)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    maintenance = second.begin_maintenance()
    try:
        assert not maintenance.try_exclusive()
        assert local._marker_path.exists()
        release.set()
        await gate.shutdown()
        assert not local._marker_path.exists()
        assert maintenance.try_exclusive()
    finally:
        release.set()
        await gate.shutdown()
        maintenance.finish()


@pytest.mark.asyncio
async def test_player_refuses_mismatched_explicit_domain_before_marker(tmp_path, cohort):
    first, _, unrelated = cohort
    gate = DeviceOperationGate(process_gate=first)
    def forbidden(*_, **__):
        pytest.fail("wrong domain must not play")
    local, personality, phrase = player(tmp_path, forbidden, unrelated)
    with pytest.raises(LocalSpeechError, match="maintenance"):
        await gate.operation(asyncio.to_thread(local.play, personality, phrase))
    assert not local._marker_path.exists()
    await gate.shutdown()


@pytest.mark.asyncio
async def test_player_never_turns_request_only_or_expired_context_into_legacy_playback(tmp_path, cohort):
    first, _, _ = cohort
    gate = DeviceOperationGate(process_gate=first)
    def forbidden(*_, **__):
        pytest.fail("unleased context must not play")
    local, personality, phrase = player(tmp_path, forbidden)
    with gate.request_scope(), pytest.raises(LocalSpeechError):
        local.play(personality, phrase)
    contexts = []
    async def capture():
        contexts.append(copy_context())
    await gate.operation(capture())
    with pytest.raises(LocalSpeechError):
        contexts[0].run(local.play, personality, phrase)
    assert not local._marker_path.exists()
    await gate.shutdown()


@pytest.mark.asyncio
async def test_maintenance_context_cannot_pass_exclusive_fd_to_player(tmp_path, cohort):
    first, _, _ = cohort
    gate = DeviceOperationGate(process_gate=first)
    local, personality, phrase = player(tmp_path, lambda *a, **k: pytest.fail("must not play"))
    async def maintenance_body():
        assert current_process_work_lease() is None
        with pytest.raises(LocalSpeechError):
            await asyncio.to_thread(local.play, personality, phrase)
    await gate.maintenance(maintenance_body())
    assert not local._marker_path.exists()
    await gate.shutdown()
