"""Synthetic PCM/player tests: no speaker, cloud, device or real subprocess."""
import asyncio
import os
import struct

import pytest

from ai_cat_controller.local_gateway.network_cues import NetworkCuePlayer, PATTERNS, tone_pcm


def test_fixed_short_faded_pcm_and_event_allowlist():
    for event in PATTERNS:
        pcm = tone_pcm(event)
        values = struct.unpack('<' + 'h' * (len(pcm) // 2), pcm)
        assert 0 < len(pcm) <= 48000 * 4 / 2
        assert values[0] == values[-1] == 0
        assert max(map(abs, values)) <= 2600
        assert values[::2] == values[1::2]
    with pytest.raises(ValueError):
        tone_pcm('arbitrary text/SSID')


@pytest.mark.asyncio
async def test_player_fixed_output_timeout_marker_and_no_volume_change(tmp_path):
    marker = tmp_path / 'marker'
    calls = []
    class Process:
        returncode = None
        async def communicate(self, pcm):
            assert marker.exists() and marker.stat().st_mode & 0o777 == 0o600
            assert pcm == tone_pcm('connected')
            self.returncode = 0
        async def wait(self):
            raise AssertionError('already exited')
    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return Process()
    player = NetworkCuePlayer(marker=marker, spawn=spawn)
    assert await player('connected') is True
    assert not marker.exists()
    argv, options = calls[0]
    assert argv[0] == '/usr/bin/paplay'
    assert '--device=alsa_output.platform-snd-card_1.stereo-fallback' in argv
    assert not any('volume' in arg or 'sink' in arg for arg in argv)
    assert options['stderr'] == options['stdout'] == asyncio.subprocess.DEVNULL
    assert options['env']['PULSE_SERVER'] == 'unix:/var/run/pulse/native'
    assert options['group'] == 'pulse-access'  # child only; same group as existing main speech.


@pytest.mark.asyncio
async def test_busy_marker_or_missing_player_is_nonfatal_and_others_marker_survives(tmp_path):
    marker = tmp_path / 'marker'
    async def spawn(*args, **kwargs):
        raise FileNotFoundError('private process details')
    player = NetworkCuePlayer(marker=marker, spawn=spawn)
    marker.write_bytes(b'original')
    assert await player('open') is False
    assert marker.read_bytes() == b'original'
    marker.unlink()
    assert await player('open') is False
    assert not marker.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [True, False])
async def test_cancel_or_timeout_kills_player_and_removes_only_own_marker(tmp_path, monkeypatch, cancel):
    marker = tmp_path / 'marker'
    ready = asyncio.Event()
    class Process:
        returncode = None
        killed = False
        async def communicate(self, pcm):
            ready.set()
            await asyncio.Event().wait()
        def kill(self):
            self.killed = True
            self.returncode = -9
        async def wait(self):
            assert self.killed
    process = Process()
    async def spawn(*args, **kwargs):
        return process
    if not cancel:
        real_wait = asyncio.wait_for
        async def short_wait(awaitable, timeout):
            return await real_wait(awaitable, timeout=.001)
        monkeypatch.setattr('ai_cat_controller.local_gateway.network_cues.asyncio.wait_for', short_wait)
    task = asyncio.create_task(NetworkCuePlayer(marker=marker, spawn=spawn)('open'))
    await ready.wait()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert await task is False
    assert process.killed and not marker.exists()


@pytest.mark.asyncio
async def test_unconfirmed_exit_is_bounded_and_retains_marker_after_cancel(tmp_path, monkeypatch):
    marker = tmp_path / 'marker'
    ready = asyncio.Event()
    class Process:
        returncode = None
        killed = False
        async def communicate(self, pcm):
            ready.set()
            await asyncio.Event().wait()
        def kill(self):
            self.killed = True
        async def wait(self):
            await asyncio.Event().wait()
    process = Process()
    async def spawn(*args, **kwargs):
        return process
    closed = []
    original_close = os.close
    def close(descriptor):
        closed.append(descriptor)
        original_close(descriptor)
    monkeypatch.setattr('ai_cat_controller.local_gateway.network_cues.os.close', close)
    monkeypatch.setattr('ai_cat_controller.local_gateway.network_cues.REAP_TIMEOUT', .001)
    task = asyncio.create_task(NetworkCuePlayer(marker=marker, spawn=spawn)('open'))
    await ready.wait()
    task.cancel()
    done, pending = await asyncio.wait([task], timeout=.5)
    assert task in done and not pending
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed and marker.exists() and len(closed) == 1
    with pytest.raises(OSError):
        os.fstat(closed[0])


@pytest.mark.asyncio
@pytest.mark.parametrize('wait_error', [True, False])
async def test_kill_or_wait_errors_cannot_skip_descriptor_cleanup_or_allow_overlap(tmp_path, monkeypatch, wait_error):
    marker = tmp_path / 'marker'
    class Process:
        returncode = None
        async def communicate(self, pcm):
            raise OSError('PRIVATE_COMMUNICATE')
        def kill(self):
            raise PermissionError('PRIVATE_KILL')
        async def wait(self):
            if wait_error:
                raise OSError('PRIVATE_WAIT')
            self.returncode = 3
            return 3
    async def spawn(*args, **kwargs):
        return Process()
    closed = []
    original_close = os.close
    def close(descriptor):
        closed.append(descriptor)
        original_close(descriptor)
    monkeypatch.setattr('ai_cat_controller.local_gateway.network_cues.os.close', close)
    assert await NetworkCuePlayer(marker=marker, spawn=spawn)('connected') is False
    assert len(closed) == 1 and marker.exists() is wait_error


@pytest.mark.asyncio
async def test_second_cancel_during_reap_closes_descriptor_but_keeps_uncertain_marker(tmp_path, monkeypatch):
    marker = tmp_path / 'marker'
    ready, reaping = asyncio.Event(), asyncio.Event()
    class Process:
        returncode = None
        async def communicate(self, pcm):
            ready.set()
            await asyncio.Event().wait()
        def kill(self):
            pass
        async def wait(self):
            reaping.set()
            await asyncio.Event().wait()
    async def spawn(*args, **kwargs):
        return Process()
    closed = []
    original_close = os.close
    def close(descriptor):
        closed.append(descriptor)
        original_close(descriptor)
    monkeypatch.setattr('ai_cat_controller.local_gateway.network_cues.os.close', close)
    task = asyncio.create_task(NetworkCuePlayer(marker=marker, spawn=spawn)('open'))
    await ready.wait()
    task.cancel()
    await reaping.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert marker.exists() and len(closed) == 1


@pytest.mark.asyncio
async def test_cancelled_spawn_handoff_closes_descriptor_without_claiming_no_child(tmp_path, monkeypatch):
    marker = tmp_path / 'marker'
    ready = asyncio.Event()
    async def spawn(*args, **kwargs):
        ready.set()
        await asyncio.Event().wait()
    closed = []
    original_close = os.close
    def close(descriptor):
        closed.append(descriptor)
        original_close(descriptor)
    monkeypatch.setattr('ai_cat_controller.local_gateway.network_cues.os.close', close)
    task = asyncio.create_task(NetworkCuePlayer(marker=marker, spawn=spawn)('open'))
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert marker.exists() and len(closed) == 1


@pytest.mark.asyncio
async def test_playback_does_not_unlink_replaced_marker_or_follow_existing_symlink(tmp_path):
    marker = tmp_path / 'marker'
    class Process:
        returncode = None
        async def communicate(self, pcm):
            marker.unlink()
            marker.write_bytes(b'another player')
            self.returncode = 0
    calls = []
    async def spawn(*args, **kwargs):
        calls.append(True)
        return Process()
    player = NetworkCuePlayer(marker=marker, spawn=spawn)
    assert await player('open') is True
    assert marker.read_bytes() == b'another player'
    marker.unlink()
    target = tmp_path / 'other-marker'
    target.write_bytes(b'original')
    marker.symlink_to(target)
    assert await player('open') is False
    assert marker.is_symlink() and target.read_bytes() == b'original' and len(calls) == 1
