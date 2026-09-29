"""Sound playback for the Wave preview: Windows waveOut through ctypes (no extra packages). Plays int16 audio from
any sample, pauses, resumes, stops, and reports the current position for the play head."""
import ctypes
from ctypes import wintypes

import numpy as np

WAVE_MAPPER = 0xFFFFFFFF
WHDR_DONE = 0x1
TIME_SAMPLES, TIME_BYTES = 0x2, 0x4


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [('wFormatTag', wintypes.WORD), ('nChannels', wintypes.WORD), ('nSamplesPerSec', wintypes.DWORD),
                ('nAvgBytesPerSec', wintypes.DWORD), ('nBlockAlign', wintypes.WORD),
                ('wBitsPerSample', wintypes.WORD), ('cbSize', wintypes.WORD)]


class WAVEHDR(ctypes.Structure):
    _fields_ = [('lpData', ctypes.c_void_p), ('dwBufferLength', wintypes.DWORD), ('dwBytesRecorded', wintypes.DWORD),
                ('dwUser', ctypes.c_size_t), ('dwFlags', wintypes.DWORD), ('dwLoops', wintypes.DWORD),
                ('lpNext', ctypes.c_void_p), ('reserved', ctypes.c_size_t)]


class _MMTimeU(ctypes.Union):
    _fields_ = [('ms', wintypes.DWORD), ('sample', wintypes.DWORD), ('cb', wintypes.DWORD),
                ('ticks', wintypes.DWORD), ('pad', ctypes.c_ubyte * 8)]


class MMTIME(ctypes.Structure):
    _fields_ = [('wType', wintypes.UINT), ('u', _MMTimeU)]


def stereo(audio):
    """int16 (n, ch) -> something waveOut plays everywhere: mono and stereo as they are, more channels mixed down
    (5.1: front + 0.7 x centre + 0.7 x surround per side, no LFE)."""
    if audio.ndim == 1:
        audio = audio[:, None]
    ch = audio.shape[1]
    if ch <= 2:
        return np.ascontiguousarray(audio, np.int16)
    x = audio.astype(np.float32)
    if ch == 6:
        left = x[:, 0] + 0.7 * x[:, 2] + 0.7 * x[:, 4]
        right = x[:, 1] + 0.7 * x[:, 2] + 0.7 * x[:, 5]
        mixed = np.stack([left, right], 1) / 1.6
    else:
        mixed = np.stack([x[:, 0::2].mean(1), x[:, 1::2].mean(1)], 1)
    return np.clip(mixed, -32768, 32767).astype(np.int16)


class Player:
    """One sound at a time. key identifies what is playing (so the caller can stop it when the selection changes)."""

    def __init__(self):
        self.h = ctypes.c_void_p()
        self.hdr = None
        self.buf = None
        self.opened = False
        self.key = None
        self.rate = 1
        self.start = 0
        self.total = 0
        self.paused = False
        self.block = 2
        try:
            self.mm = ctypes.windll.winmm
        except (AttributeError, OSError):
            self.mm = None

    def play(self, audio, rate, start=0, key=None):
        """Play int16 (n, ch) audio from sample `start`."""
        self.stop()
        if self.mm is None:
            raise OSError('sound output is only available on Windows')
        data = stereo(audio)
        total = len(data)
        start = max(0, min(int(start), total))
        buf = np.ascontiguousarray(data[start:])
        if not len(buf):
            return
        ch = buf.shape[1]
        wfx = WAVEFORMATEX(1, ch, rate, rate * ch * 2, ch * 2, 16, 0)
        res = self.mm.waveOutOpen(ctypes.byref(self.h), ctypes.c_uint(WAVE_MAPPER), ctypes.byref(wfx), None, None, 0)
        if res:
            raise OSError(f'the sound device could not be opened (waveOutOpen error {res})')
        self.buf = buf
        self.hdr = WAVEHDR()
        self.hdr.lpData = buf.ctypes.data
        self.hdr.dwBufferLength = buf.nbytes
        self.mm.waveOutPrepareHeader(self.h, ctypes.byref(self.hdr), ctypes.sizeof(WAVEHDR))
        self.mm.waveOutWrite(self.h, ctypes.byref(self.hdr), ctypes.sizeof(WAVEHDR))
        self.opened = True
        self.key = key
        self.rate = rate
        self.start = start
        self.total = total
        self.paused = False
        self.block = ch * 2

    @property
    def done(self):
        return self.opened and bool(self.hdr.dwFlags & WHDR_DONE)

    @property
    def playing(self):
        return self.opened and not self.done

    def position(self):
        """Current sample (of the whole sound), or None when nothing is open."""
        if not self.opened:
            return None
        if self.done:
            return self.total
        t = MMTIME()
        t.wType = TIME_SAMPLES
        self.mm.waveOutGetPosition(self.h, ctypes.byref(t), ctypes.sizeof(MMTIME))
        if t.wType == TIME_SAMPLES:
            done = t.u.sample
        elif t.wType == TIME_BYTES:
            done = t.u.cb // self.block
        else:
            done = int(t.u.ms * self.rate / 1000)
        return min(self.total, self.start + done)

    def pause(self):
        if self.playing and not self.paused:
            self.mm.waveOutPause(self.h)
            self.paused = True

    def resume(self):
        if self.playing and self.paused:
            self.mm.waveOutRestart(self.h)
            self.paused = False

    def stop(self):
        if self.opened:
            self.mm.waveOutReset(self.h)
            self.mm.waveOutUnprepareHeader(self.h, ctypes.byref(self.hdr), ctypes.sizeof(WAVEHDR))
            self.mm.waveOutClose(self.h)
        self.opened = False
        self.paused = False
        self.key = None
        self.hdr = None
        self.buf = None
