"""Ginsu engine sounds (resource type 0x80): EA's granular engine synthesis data in the car bundles
(PC VEH_*_HI, PS3 VEH_*_EN and TRAFFICATTRIBS).

A recording of the engine sweeping through its rev range, cut into grains (one engine cycle each); the game plays
the grains around the current RPM. Layout (platform byte order for the header, the tables are always little-endian):

  f32 gain (0.5 / 1.0 / ...), PC: u32 0         then 'Gnsu20' / 'Gnsu30' + 2 zero bytes
  f32 min RPM, f32 max RPM, u32 steps (50), u32 grains, u32 samples, u32 sample rate, Gnsu30: u32 0
  u32 x (steps + 1)   sample position of each RPM step, min RPM .. max RPM evenly spaced
  u32 x (grains + 1)  start sample of each grain, then the end
  EA-XAS v0 mono audio: 19-byte frames of 32 samples (u32 LE header: filter, shift, two 12-bit start samples;
  15 bytes of 4-bit deltas, high nibble first)."""
import struct

import numpy as np

T_GINSU = 0x80
MAGIC = b'Gnsu'
_COEF = np.array([0, 240, 460, 392, 0, 0, -208, -220], np.int64)


class GinsuError(ValueError):
    pass


class Ginsu:
    def __init__(self, data, e='<'):
        m = data.find(MAGIC, 0, 16)
        if m < 0:
            raise GinsuError('not a Ginsu engine sound (no Gnsu header)')
        self.data = data
        self.e = e
        self.version = data[m + 4:m + 6].decode('ascii', 'replace')
        self.gain = struct.unpack_from(e + 'f', data, 0)[0]
        self.min_rpm, self.max_rpm = struct.unpack_from(e + 'ff', data, m + 8)
        self.steps, self.grains, self.samples, self.rate = struct.unpack_from(e + '4I', data, m + 16)
        t0 = m + (0x24 if self.version == '30' else 0x20)
        n = self.steps + 1 + self.grains + 1
        if t0 + 4 * n > len(data):
            raise GinsuError('Ginsu tables run past the end of the data')
        tab = np.frombuffer(data, '<u4', n, t0).astype(np.int64)
        self.rpm_pos = tab[:self.steps + 1]
        self.grain_pos = tab[self.steps + 1:]
        self.audio_offset = t0 + 4 * n
        frames = -(-self.samples // 32)
        if self.audio_offset + frames * 19 > len(data):
            raise GinsuError('Ginsu audio is shorter than its sample count')
        self._pcm = None

    @property
    def decel(self):
        """True for an off-load (engine braking) recording: the RPM steps run backwards through the audio."""
        return len(self.rpm_pos) > 1 and self.rpm_pos[-1] < self.rpm_pos[0]

    def describe(self):
        return (f"{'Off-load (deceleration)' if self.decel else 'On-load (acceleration)'} sweep, "
                f'{self.min_rpm:.0f} - {self.max_rpm:.0f} RPM, {self.grains} grains, {self.rate} Hz, '
                f'{self.samples / max(self.rate, 1):.2f} s, gain {self.gain:g} (Gnsu{self.version})')

    def pcm(self):
        """Mono int16 audio of the whole sweep."""
        if self._pcm is None:
            self._pcm = decode_xas0(self.data[self.audio_offset:], self.samples)
        return self._pcm

    def position(self, rpm):
        """Sample position of an RPM (interpolated between the RPM steps)."""
        f = (rpm - self.min_rpm) / max(self.max_rpm - self.min_rpm, 1e-6) * self.steps
        f = min(max(f, 0.0), float(self.steps))
        i = min(int(f), self.steps - 1)
        a, b = self.rpm_pos[i], self.rpm_pos[i + 1]
        return int(a + (b - a) * (f - i))

    def rpm_at(self, pos):
        """RPM at a sample position (inverse of position())."""
        p = self.rpm_pos.astype(np.float64)
        rpms = np.linspace(self.min_rpm, self.max_rpm, len(p))
        order = np.argsort(p)
        return float(np.interp(pos, p[order], rpms[order]))

    def grain_at(self, pos):
        return int(min(max(np.searchsorted(self.grain_pos, pos, 'right') - 1, 0), self.grains - 1))

    def hold(self, rpm, seconds=3.0, spread=4):
        """Audio of the engine held at `rpm`: the grains around that point of the sweep, played in turn (a rough
        stand-in for the game's granular player). Returns int16 (n, 1)."""
        pcm = self.pcm()
        g = self.grain_at(self.position(rpm))
        lo, hi = max(g - spread // 2, 0), min(g + spread // 2 + 1, self.grains)
        out, total, k = [], 0, 0
        need = int(seconds * self.rate)
        order = list(range(lo, hi))
        while total < need and order:
            gi = order[k % len(order)]
            a, b = int(self.grain_pos[gi]), int(self.grain_pos[gi + 1])
            piece = pcm[a:b]
            if not len(piece):
                break
            out.append(piece)
            total += len(piece)
            k += 1
        audio = np.concatenate(out)[:need] if out else np.zeros(0, np.int16)
        return audio.reshape(-1, 1)


def decode_xas0(data, samples):
    """EA-XAS v0: 19-byte frames of 32 mono samples."""
    nf = -(-samples // 32)
    if nf == 0:
        return np.zeros(0, np.int16)
    fr = np.frombuffer(data[:nf * 19], np.uint8).reshape(nf, 19)
    hdr = fr[:, :4].copy().view('<u4')[:, 0].astype(np.int64)
    idx = hdr & 3
    c1, c2 = _COEF[idx], _COEF[idx + 4]
    h2 = ((hdr & 0xFFF0) ^ 0x8000) - 0x8000
    h1 = (((hdr >> 16) & 0xFFF0) ^ 0x8000) - 0x8000
    sh = (hdr >> 16) & 0xF
    by = fr[:, 4:].astype(np.int64)
    nib = np.stack([by >> 4, by & 15], 2).reshape(nf, 30)
    nib = (nib ^ 8) - 8
    out = np.empty((nf, 32), np.int64)
    out[:, 0], out[:, 1] = h2, h1
    for i in range(30):
        s = (nib[:, i] << (20 - sh)) + c1 * h1 + c2 * h2 + 128 >> 8
        s = np.clip(s, -32768, 32767)
        out[:, i + 2] = s
        h2, h1 = h1, s
    return out.reshape(-1)[:samples].astype(np.int16)


def read(res, e):
    return Ginsu(res.data(0), e)
