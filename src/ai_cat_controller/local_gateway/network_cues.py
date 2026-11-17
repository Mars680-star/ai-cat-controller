"""Short offline provisioning tones, never cloud TTS or an audio reconfiguration.

Only fixed events are accepted. Playback shares the existing local-speech marker
and output; a busy/missing player is non-fatal and never changes network results.
"""
from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path
import struct


PATTERNS = {
    'open': (660, 880),
    'connecting': (660,),
    'connected': (660, 880, 1100),
    'failed': (440, 330),
    'not_sent': (440,),
    'unknown': (440, 440),
}
PLAYER = '/usr/bin/paplay'
OUTPUT = 'alsa_output.platform-snd-card_1.stereo-fallback'
PULSE_SERVER = 'unix:/var/run/pulse/native'
MARKER = Path('/run/ai-cat/local-speech-active')
REAP_TIMEOUT = 0.2


def tone_pcm(event):
    """Bounded stereo 48 kHz/16-bit PCM, with fades to avoid clicks."""
    if event not in PATTERNS:
        raise ValueError('Unknown network cue')
    samples = bytearray()
    count, fade = 5760, 480  # 120 ms per tone, 10 ms fades, 40 ms gaps.
    for frequency in PATTERNS[event]:
        for index in range(count):
            envelope = min(1.0, index / fade, (count - 1 - index) / fade)
            value = round(2600 * envelope * math.sin(2 * math.pi * frequency * index / 48000))
            samples.extend(struct.pack('<hh', value, value))
        samples.extend(b'\0' * (1920 * 4))
    return bytes(samples)


class NetworkCuePlayer:
    def __init__(self, *, marker=MARKER, spawn=asyncio.create_subprocess_exec):
        self.marker, self.spawn = Path(marker), spawn

    async def __call__(self, event):
        pcm = tone_pcm(event)
        descriptor = None
        process = None
        identity = None
        spawn_pending = False
        exited = True
        try:
            # Do not remove stale/other players' markers or start a second sound.
            descriptor = os.open(self.marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            info = os.fstat(descriptor)
            identity = info.st_dev, info.st_ino
            os.write(descriptor, f'{os.getpid()}\n'.encode('ascii'))
            # Cancellation during a spawn handoff cannot prove that no child
            # started. Keep the marker in that uncertain case, rather than
            # allowing another sound to overlap an unobserved player.
            spawn_pending = True
            try:
                process = await self.spawn(PLAYER, '--raw', '--format=s16le', '--channels=2', '--rate=48000',
                    '--device=' + OUTPUT, '--client-name=ai-cat-local-speech', '--stream-name=network-' + event,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    env={**os.environ, 'PULSE_SERVER': PULSE_SERVER}, group='pulse-access')
            except OSError:
                spawn_pending = False
                raise
            spawn_pending = False
            exited = False
            await asyncio.wait_for(process.communicate(pcm), timeout=2)
            return process.returncode == 0
        except (OSError, asyncio.TimeoutError):
            return False
        finally:
            try:
                if process is not None:
                    exited = process.returncode is not None
                    if not exited:
                        try:
                            process.kill()
                        except OSError:
                            # Even a failed kill must not skip bounded reap or
                            # local descriptor cleanup. No raw errors escape.
                            pass
                        try:
                            result = await asyncio.wait_for(process.wait(), timeout=REAP_TIMEOUT)
                            exited = process.returncode is not None or type(result) is int
                        except (OSError, asyncio.TimeoutError):
                            exited = process.returncode is not None
            finally:
                if descriptor is not None:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                    # An unreaped player or interrupted spawn must retain the
                    # marker: failure is non-fatal to networking, but not proof
                    # that it is safe for another local sound to start.
                    if exited and not spawn_pending:
                        try:
                            info = self.marker.lstat()
                            if (info.st_dev, info.st_ino) == identity:
                                self.marker.unlink()
                        except OSError:
                            pass
