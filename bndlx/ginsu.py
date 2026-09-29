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


# ---------------------------------------------------------------------------------------------------------------
# encoding (a new engine sound from a recording)
# ---------------------------------------------------------------------------------------------------------------
LEAD_IN = 721                  # every retail sound's lowest (accelerating: first) RPM step sits at this sample


def encode_xas0(pcm):
    """EA-XAS v0 encoding of mono int16 samples: per 32-sample frame the filter and shift with the least error
    (closed loop, as the decoder rebuilds it)."""
    x = np.asarray(pcm, np.int64)
    nf = max(1, -(-len(x) // 32))
    x = np.concatenate([x, np.zeros(nf * 32 - len(x), np.int64)]).reshape(nf, 32)
    q0 = np.clip((x[:, 0] + 8) >> 4 << 4, -32768, 32752)
    q1 = np.clip((x[:, 1] + 8) >> 4 << 4, -32768, 32752)
    cand = [(k, s) for k in range(4) for s in range(16)]
    c1 = np.array([_COEF[k] for k, _ in cand], np.int64)[None, :]
    c2 = np.array([_COEF[k + 4] for k, _ in cand], np.int64)[None, :]
    step = np.array([1 << (20 - s) for _, s in cand], np.int64)[None, :]
    h2 = np.repeat(q0[:, None], len(cand), 1)
    h1 = np.repeat(q1[:, None], len(cand), 1)
    err = np.zeros((nf, len(cand)))
    nibs = np.zeros((nf, len(cand), 30), np.int64)
    for i in range(30):
        p = c1 * h1 + c2 * h2
        target = x[:, i + 2][:, None]
        nib = np.clip(np.floor((256 * target - p) / step + 0.5), -8, 7).astype(np.int64)
        s = np.clip((nib * step + p + 128) >> 8, -32768, 32767)
        err += (s - target) ** 2
        nibs[:, :, i] = nib
        h2, h1 = h1, s
    best = err.argmin(1)
    k = np.array([c for c, _ in cand])[best]
    sh = np.array([s for _, s in cand])[best]
    nb = nibs[np.arange(nf), best] & 15
    hdr = (q0 & 0xFFF0) | k | ((q1 & 0xFFF0) << 16) | (sh << 16)
    out = np.zeros((nf, 19), np.uint8)
    out[:, :4] = (hdr & 0xFFFFFFFF).astype('<u4').view(np.uint8).reshape(nf, 4)
    out[:, 4:] = (nb[:, 0::2] << 4 | nb[:, 1::2]).astype(np.uint8)
    return out.tobytes()


def _frames_ac(x, hop, win):
    """Autocorrelation of Hann-windowed frames every hop, normalised and corrected by the window's own (Boersma):
    (frame centres, rows of win lags, loud frames)."""
    n = len(x)
    w = np.hanning(win)
    wf = np.fft.rfft(w, 2 * win)
    wac = np.fft.irfft(wf * np.conj(wf))[:win]
    wac = np.maximum(wac / wac[0], 0.05)
    starts = np.arange(0, max(1, n - win), hop)
    out = np.zeros((len(starts), win))
    loud = np.zeros(len(starts), bool)
    for c in range(0, len(starts), 64):
        idx = starts[c:c + 64, None] + np.arange(win)
        seg = np.where(idx < n, x[np.minimum(idx, n - 1)], 0.0) * w
        f = np.fft.rfft(seg, 2 * win, axis=1)
        ac = np.fft.irfft(f * np.conj(f), axis=1)[:, :win]
        loud[c:c + 64] = np.abs(seg).max(1) >= 1e-3
        out[c:c + 64] = ac / np.maximum(ac[:, :1], 1e-12) / wac
    return starts + win / 2, out, loud


def track_rpm(x, rate, cylinders, start_rpm, falling=False, hop=512):
    """RPM of a recorded engine sweeping through its revs, at every hop: the firing period (a four-stroke engine
    fires every 120 / RPM / (cylinders / 2) seconds) followed through the frames' autocorrelation from start_rpm.
    Its harmonics score about as well, so the path only takes peaks near where the sweep is heading: the RPM only
    rises (falls), at most ~3 % a hop; where the pitch fades the sweep carries on at its recent rate for a while.
    Returns (times in samples, rpm, strength = the autocorrelation at the peak taken, 0 where none: low = no clear
    engine pitch)."""
    x = np.asarray(x, np.float64)
    x = x - x.mean()
    fire = lambda rpm: 120.0 / rpm / (cylinders / 2.0) * rate               # noqa: E731 (samples)
    lo = min(start_rpm * 0.6, 600.0) if falling else start_rpm * 0.6
    win = 1 << int(np.ceil(np.log2(max(2048 * rate / 48000.0, fire(lo) * 1.6))))
    win = min(win, 1 << 15)
    lag_lo, lag_hi = max(2.0, fire(14000.0)), min(win * 0.62, fire(lo))
    if lag_hi <= lag_lo * 1.1:
        raise GinsuError('no room for the engine pitch: check the cylinders and the start RPM')
    times, ac, loud = _frames_ac(x, hop, win)
    if not loud.any():
        raise GinsuError('no engine pitch found in the recording (silent)')
    prev, slope, lost = np.log(fire(start_rpm)), 0.0, 0
    good_t, good_l, lags, strength = [], [], [], []
    for t in range(len(times)):
        pred = float(np.clip(prev + (slope if lost < 30 else 0.0), np.log(lag_lo), np.log(lag_hi)))
        if t == 0:
            a, b = pred - 0.3, pred + 0.3                    # the start: within 30 % of the given RPM
        else:
            w = min(0.15, 0.03 + 0.004 * lost)               # wider the longer the pitch was lost
            a, b = pred - w, pred + w
            if falling:
                a = max(a, prev - 0.01)
            else:
                b = min(b, prev + 0.01)
        ia = max(int(np.floor(np.exp(a))), int(lag_lo), 2)
        ib = min(int(np.ceil(np.exp(b))) + 1, int(lag_hi), win - 2)
        row = ac[t]
        found = None
        if loud[t] and ib > ia:
            i = np.arange(ia, ib + 1)
            pk = i[(row[i] >= row[i - 1]) & (row[i] >= row[i + 1]) & (row[i] >= 0.15)]
            if len(pk):
                j = pk[(row[pk] - 2.0 * np.abs(np.log(pk) - pred)).argmax()]
                l, m, r = row[j - 1], row[j], row[j + 1]
                d = l - 2 * m + r                             # parabolic refinement
                found = np.log(j + (float(np.clip(0.5 * (l - r) / d, -1, 1)) if d < 0 else 0.0))
                strength.append(float(m))
        if found is not None:
            prev, lost = found, 0
            good_t.append(t)
            good_l.append(found)
            if len(good_t) >= 8:                              # the sweep's recent rate (log lag a hop)
                s_ = float(np.polyfit(np.array(good_t[-40:], float), np.array(good_l[-40:]), 1)[0])
                slope = max(s_, 0.0) if falling else min(s_, 0.0)
        else:
            prev, lost = pred, lost + 1
            strength.append(0.0)
        lags.append(np.exp(prev))
    rpm = 120.0 / (np.array(lags) / rate) / (cylinders / 2.0)
    k = 5                                                     # median smoothing
    pad = np.pad(rpm, k // 2, mode='edge')
    rpm = np.array([np.median(pad[i:i + k]) for i in range(len(rpm))])
    return times, rpm, np.array(strength)


def weak_share(strength):
    """The share of the recording without a clear engine pitch on the tracked path (above ~0.3 the RPM found is
    probably wrong: other cylinders / start RPM, or a steady sweep)."""
    return float(np.mean(np.asarray(strength) < 0.3)) if len(strength) else 1.0


CYLINDERS = (4, 5, 6, 8, 10, 12)


def cylinders_of(g):
    """The cylinder count whose firing, tracked from the sound's first RPM, follows its own RPM table best (the
    default for a replacement; of about equally good ones the largest): (cylinders, median error)."""
    pcm = g.pcm().astype(np.float64)
    rp = np.linspace(g.min_rpm, g.max_rpm, g.steps + 1)
    pos = np.array([g.position(r) for r in rp])
    order = np.argsort(pos)
    errs = {}
    for cyl in CYLINDERS:
        try:
            t, rpm, _ = track_rpm(pcm, g.rate, cyl, g.max_rpm if g.decel else g.min_rpm, g.decel)
        except GinsuError:
            continue
        truth = np.interp(t, pos[order], rp[order])
        errs[cyl] = float(np.median(np.abs(rpm - truth) / truth))
    if not errs:
        return 8, None
    best = min(errs.values())
    cyl = max(c for c, e in errs.items() if e <= best + 0.01)
    return cyl, errs[cyl]


def build(template, pcm, rate, rpm_times, rpm_values, decel=None):
    """A Ginsu resource (bytes) for a new recording, laid out like `template` (platform, version, gain): pcm =
    mono int16 at `rate`; rpm_values at rpm_times (samples) = the engine's RPM through the recording (smoothed to
    a sweep). Grains are cut one engine cycle (120 / RPM s) long, the RPM steps placed where the sweep passes them."""
    pcm = np.asarray(pcm, np.int16)
    n = len(pcm)
    decel = template.decel if decel is None else decel
    rpm = np.maximum.accumulate(rpm_values) if not decel else np.minimum.accumulate(rpm_values)
    rpm_at = lambda p: float(np.interp(p, rpm_times, rpm))                  # noqa: E731
    lo, hi = float(rpm.min()), float(rpm.max())
    if hi - lo < 50:
        raise GinsuError(f'the recording does not sweep through the revs ({lo:.0f} - {hi:.0f} RPM)')
    grains = [0 if template.grain_pos[0] == 0 else 1]
    while True:
        nxt = grains[-1] + max(8, int(round(120.0 / rpm_at(grains[-1]) * rate)))
        if nxt >= n:
            break
        grains.append(nxt)
    grains.append(n)
    steps = template.steps
    # the RPM curve is monotonic: where does it pass each evenly spaced RPM
    order = np.argsort(rpm, kind='stable')
    targets = np.linspace(lo, hi, steps + 1)
    pos = np.interp(targets, rpm[order], np.asarray(rpm_times)[order])
    pos = np.clip(np.round(pos), min(LEAD_IN, n - 1), n - 1).astype(np.int64)
    e = template.e
    m = template.data.find(MAGIC, 0, 16)
    head = bytearray(template.data[:m + 8])
    head += struct.pack(e + 'ff', lo, hi)
    head += struct.pack(e + '4I', steps, len(grains) - 1, n, rate)
    if template.version == '30':
        head += struct.pack(e + 'I', 0)
    head += np.asarray(pos, '<u4').tobytes() + np.asarray(grains, '<u4').tobytes()
    data = bytes(head) + encode_xas0(pcm)
    return data + b'\0' * (-len(data) % 16)


def recording_mono(audio, rate, template):
    """A recording as mono float at the template's sample rate."""
    a = np.asarray(audio, np.float64)
    mono = a.mean(1) if a.ndim == 2 else a
    if rate != template.rate and len(mono):
        n = int(round(len(mono) * template.rate / rate))
        mono = np.interp(np.linspace(0, len(mono) - 1, n), np.arange(len(mono)), mono)
    return mono


def from_recording(template, audio, rate, mode='track', cylinders=6, start_rpm=None, rpm_range=None, curve=None):
    """A new engine sound from a recording of an engine sweeping through its revs, laid out like `template`:
    (resource bytes, summary). mode 'track' follows the pitch (cylinders, start_rpm = where the sweep begins);
    'linear' takes rpm_range = (low, high) as a steady sweep over the whole recording; curve = track_rpm's
    (times, rpm, strength) already tracked (the options window's analysis). summary['weak'] = weak_share."""
    mono = recording_mono(audio, rate, template)
    rate = template.rate
    pcm = np.clip(np.round(mono), -32768, 32767).astype(np.int16)
    if len(pcm) < rate // 2:
        raise GinsuError('the recording is too short (at least half a second)')
    decel = template.decel
    if mode == 'linear':
        lo, hi = rpm_range or (template.min_rpm, template.max_rpm)
        times = np.array([0.0, len(pcm) - 1.0])
        rpm = np.array([hi, lo] if decel else [lo, hi], np.float64)
        weak = 0.0
    elif curve is not None:
        times, rpm, strength = curve
        weak = weak_share(strength)
    else:
        start = start_rpm or (template.max_rpm if decel else template.min_rpm)
        times, rpm, strength = track_rpm(pcm, rate, cylinders, start, decel)
        weak = weak_share(strength)
    if mode != 'linear':                         # only where the pitch was clear (not the carried-on guesses)
        keep = np.asarray(strength) >= 0.3
        if keep.sum() >= max(8, len(keep) // 5):
            times, rpm = np.asarray(times)[keep], np.asarray(rpm)[keep]
    data = build(template, pcm, rate, times, rpm, decel)
    g = Ginsu(data, template.e)
    return data, {'min': g.min_rpm, 'max': g.max_rpm, 'grains': g.grains, 'seconds': g.samples / g.rate,
                  'rate': g.rate, 'weak': weak}
