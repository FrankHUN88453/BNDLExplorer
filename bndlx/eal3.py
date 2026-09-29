"""EA "SPS" audio streams with the EALayer3 v1 codec (all Wave resources of NFS Most Wanted 2012, PC and PS3).

Stream = blocks {u8 id, u24 size (incl. this header)}, big-endian:
  'H' (0x48) header: SNR header, u32 h1 = version(4) codec(4) channel_config(6) sample_rate(18),
                     u32 h2 = type(2) loop(1) num_samples(29), [u32 loop start]
  'D' (0x44) data:   u32 samples in the block, then EALayer3 frames
  'E' (0x45) end.
EALayer3 v1 frame = one MPEG Layer III granule of one "stream" (mono or stereo); multichannel audio is several
streams whose frames alternate per granule (6 channels = 3 stereo streams):
  u8 flag (0x00, or 0xEE = with a PCM part)
  MPEG header bits: version(2) sample rate index(2) channel mode(2) mode extension(2)
  granule index(1); MPEG-1 granule 1: scfsi(4) per channel
  per channel: part2_3_length(12) + the rest of the granule side info (47 bits MPEG-1, 51 bits MPEG-2)
  main data (sum of part2_3_length bits), padded to a byte
  flag 0xEE: u16 samples to drop at the granule start, u16 PCM samples, u32 0, then PCM s16 BE interleaved
             (the PCM samples follow the decoded granule).
Decoding rebuilds standard MP3 frames (with a bit reservoir) and decodes them with libsndfile / mpg123;
encoding does the reverse with LAME's MP3 frames.
"""
import io
import struct

import numpy as np

CODEC_EALAYER3_V1 = 5
MPEG1_RATES = (44100, 48000, 32000)
MPEG2_RATES = (22050, 24000, 16000)
MPEG25_RATES = (11025, 12000, 8000)
BITRATES_V1 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
BITRATES_V2 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)


class AudioError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------------------------
# bits
# ---------------------------------------------------------------------------------------------------------------
class BitReader:
    """MSB-first bit reader; fields of any width are read at once with int.from_bytes."""

    def __init__(self, data, pos=0):
        self.d = data
        self.b = pos * 8

    def read(self, n):
        if n == 0:
            return 0
        b = self.b
        first = b >> 3
        last = (b + n + 7) >> 3
        v = int.from_bytes(self.d[first:last], 'big')
        v >>= (last - first) * 8 - (b & 7) - n
        self.b = b + n
        return v & ((1 << n) - 1)

    def field(self, n):
        """(value, width) pair, as BitWriter.write takes it."""
        return (self.read(n), n)


class BitWriter:
    def __init__(self):
        self.out = bytearray()
        self.acc = 0
        self.n = 0

    def write(self, v, n):
        if n == 0:
            return
        self.acc = (self.acc << n) | (v & ((1 << n) - 1))
        self.n += n
        if self.n >= 64:
            k = self.n >> 3
            rest = self.n - k * 8
            self.out += (self.acc >> rest).to_bytes(k, 'big')
            self.acc &= (1 << rest) - 1
            self.n = rest

    def bytes(self):
        out = bytes(self.out)
        if self.n:
            pad = (-self.n) % 8
            out += (self.acc << pad).to_bytes((self.n + pad) >> 3, 'big')
        return out

    def bitlen(self):
        return len(self.out) * 8 + self.n


# ---------------------------------------------------------------------------------------------------------------
# stream parsing
# ---------------------------------------------------------------------------------------------------------------
class Granule:
    __slots__ = ('flag', 'vi', 'si', 'cm', 'me', 'gr', 'scfsi', 'side', 'main', 'drop', 'pcm', 'channels')


def snr_header(h1, h2, loop_start=None):
    return {'version': h1 >> 28, 'codec': (h1 >> 24) & 15, 'channels': ((h1 >> 18) & 0x3F) + 1,
            'rate': h1 & 0x3FFFF, 'type': h2 >> 30, 'loop': bool((h2 >> 29) & 1), 'samples': h2 & 0x1FFFFFFF,
            'loop_start': loop_start}


def parse_frame(data, pos):
    br = BitReader(data, pos)
    g = Granule()
    g.flag = br.read(8)
    if g.flag not in (0x00, 0xEE):
        raise AudioError(f'unexpected EALayer3 frame flag {g.flag:#x}')
    g.vi, g.si, g.cm, g.me = br.read(2), br.read(2), br.read(2), br.read(2)
    if g.vi == 1:
        raise AudioError('invalid MPEG version in EALayer3 frame')
    mpeg1 = g.vi == 3
    ch = 1 if g.cm == 3 else 2
    g.channels = ch
    g.gr = br.read(1)
    g.scfsi = [br.read(4) for _ in range(ch)] if (mpeg1 and g.gr == 1) else [0] * ch
    rest = 47 if mpeg1 else 51
    g.side = []
    sizes = []
    for _ in range(ch):
        size = br.read(12)
        sizes.append(size)
        g.side.append((size, br.field(rest)))
    g.main = br.field(sum(sizes))
    end = (br.b + 7) // 8
    g.drop = 0
    g.pcm = None
    if g.flag == 0xEE:
        drop, n, _ = struct.unpack_from('>HHI', data, end)
        g.drop = drop
        g.pcm = np.frombuffer(data[end + 8:end + 8 + 2 * n * ch], '>i2').reshape(n, ch).astype(np.int16)
        end += 8 + 2 * n * ch
    return g, end


def parse_sps(sps):
    """-> (SNR header dict, [granule frames in stream order])."""
    pos = 0
    head = None
    frames = []
    while pos + 4 <= len(sps):
        bid = sps[pos]
        size = struct.unpack_from('>I', sps, pos)[0] & 0xFFFFFF
        if size < 4:
            raise AudioError('bad block size')
        if bid == 0x48:
            h1, h2 = struct.unpack_from('>II', sps, pos + 4)
            loop = struct.unpack_from('>I', sps, pos + 12)[0] if size >= 16 else None
            head = snr_header(h1, h2, loop)
        elif bid == 0x44:
            fp = pos + 8
            while fp < pos + size:
                g, fp = parse_frame(sps, fp)
                frames.append(g)
        elif bid == 0x45:
            break
        pos += size
    if head is None:
        if sps[:4] == b'irf1':
            raise AudioError('this is a reverb impulse response (irf1), not a sound')
        raise AudioError('no SNR header block')
    if head['codec'] != CODEC_EALAYER3_V1:
        raise AudioError(f'codec {head["codec"]} is not supported (only EALayer3 v1)')
    return head, frames


def split_streams(frames, channels):
    """Frames alternate between streams per granule: group them until the channel count is reached."""
    groups = []
    i = 0
    while i < len(frames):
        grp = []
        n = 0
        while i < len(frames) and n < channels:
            grp.append(frames[i])
            n += frames[i].channels
            i += 1
        groups.append(grp)
    nstreams = max(len(g) for g in groups) if groups else 0
    streams = [[] for _ in range(nstreams)]
    for grp in groups:
        for k, f in enumerate(grp):
            streams[k].append(f)
    return streams


# ---------------------------------------------------------------------------------------------------------------
# EALayer3 -> MP3
# ---------------------------------------------------------------------------------------------------------------
def to_mp3(granules):
    """One stream's granules -> an MP3 byte stream (bit reservoir, highest bitrate) + granules per frame."""
    if not granules:
        return b'', 1
    g0 = granules[0]
    mpeg1 = g0.vi == 3
    per_frame = 2 if mpeg1 else 1
    ch = g0.channels
    rate = (MPEG1_RATES if mpeg1 else MPEG2_RATES if g0.vi == 2 else MPEG25_RATES)[g0.si]
    br_idx = 14
    kbps = (BITRATES_V1 if mpeg1 else BITRATES_V2)[br_idx]
    fsize = (144 if mpeg1 else 72) * kbps * 1000 // rate
    side_len = (17 if ch == 1 else 32) if mpeg1 else (9 if ch == 1 else 17)
    cap = fsize - 4 - side_len
    max_back = 511 if mpeg1 else 255
    frames = [granules[i:i + per_frame] for i in range(0, len(granules), per_frame)]
    if len(frames[-1]) < per_frame:            # pad the last MPEG-1 frame with an empty granule
        last = frames[-1][0]
        empty = Granule()
        for k in Granule.__slots__:
            setattr(empty, k, getattr(last, k))
        empty.gr, empty.main, empty.drop, empty.pcm = 1, (0, 0), 0, None
        empty.side = [(0, (0, s[1][1])) for s in last.side]
        frames[-1] = frames[-1] + [empty]
    main_stream = bytearray()
    headers = []
    used = 0
    for i, fr in enumerate(frames):
        start = i * cap                          # main-data stream position where this frame's slot begins
        nbits = sum(g.main[1] for g in fr)
        need = (nbits + 7) // 8
        begin = max(used, start - max_back)
        if begin + need > start + cap:
            raise AudioError('granule data does not fit the MP3 frame')
        if begin > len(main_stream):
            main_stream += b'\0' * (begin - len(main_stream))
        bw = BitWriter()
        for g in fr:
            bw.write(*g.main)
        main_stream[begin:begin + need] = bw.bytes()
        used = begin + need
        headers.append((fr, start - begin))
    total = len(frames) * cap
    main_stream += b'\0' * (total - len(main_stream))
    out = bytearray()
    for i, (fr, back) in enumerate(headers):
        g = fr[0]
        bw = BitWriter()
        bw.write(0x7FF, 11)
        bw.write(g.vi, 2)
        bw.write(1, 2)                           # layer III
        bw.write(1, 1)                           # no CRC
        bw.write(br_idx, 4)
        bw.write(g.si, 2)
        bw.write(0, 1)                           # padding
        bw.write(0, 1)
        bw.write(g.cm, 2)
        bw.write(g.me, 2)
        bw.write(0, 1)
        bw.write(1, 1)
        bw.write(0, 2)
        if mpeg1:
            bw.write(back, 9)
            bw.write(0, 5 if ch == 1 else 3)
            scfsi = fr[1].scfsi if len(fr) > 1 else [0] * ch
            for c in range(ch):
                bw.write(scfsi[c], 4)
        else:
            bw.write(back, 8)
            bw.write(0, 1 if ch == 1 else 2)
        for gg in fr:
            for size, rest in gg.side:
                bw.write(size, 12)
                bw.write(*rest)
        hdr = bw.bytes()
        assert len(hdr) == 4 + side_len, (len(hdr), side_len)
        out += hdr + main_stream[i * cap:(i + 1) * cap]
    return bytes(out), per_frame


START_SKIP = 1152          # decoded samples dropped at the start: the first MPEG frame (encoder + decoder delay)
DECODER_DELAY = 529
MPEG_RATES = (48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000)


def decode_sps(sps):
    """-> (int16 array (samples, channels), sample rate, header).
    Output = decoded granules with each PCM part after its granule, minus the first 1152 samples (that is how
    the block sample counts add up in every game file), cut to the header's sample count."""
    import soundfile as sf
    head, frames = parse_sps(sps)
    streams = split_streams(frames, head['channels'])
    outs = []
    for st in streams:
        mp3, _ = to_mp3(st)
        pcm, rate = sf.read(io.BytesIO(mp3), dtype='int16', always_2d=True)
        pieces = []
        pos = 0
        for g in st:
            seg = pcm[pos:pos + 576]
            pos += 576
            if g.drop:
                seg = seg[g.drop:]
            pieces.append(seg)
            if g.pcm is not None and len(g.pcm):
                pieces.append(g.pcm[:, :seg.shape[1]])
        out = np.concatenate(pieces) if pieces else np.zeros((0, st[0].channels), np.int16)
        outs.append(out[START_SKIP:])
    n = min(len(o) for o in outs) if outs else 0
    if head['samples']:
        n = min(n, head['samples'])
    audio = np.concatenate([o[:n] for o in outs], axis=1) if outs else np.zeros((0, 1), np.int16)
    return audio, head['rate'], head


def wav_bytes(audio, rate):
    buf = io.BytesIO()
    ch = audio.shape[1] if audio.ndim == 2 else 1
    data = np.ascontiguousarray(audio, '<i2').tobytes()
    buf.write(b'RIFF' + struct.pack('<I', 36 + len(data)) + b'WAVE')
    buf.write(b'fmt ' + struct.pack('<IHHIIHH', 16, 1, ch, rate, rate * ch * 2, ch * 2, 16))
    buf.write(b'data' + struct.pack('<I', len(data)) + data)
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------------------------
# encoding: audio -> LAME MP3 -> EALayer3 granules -> SPS stream
# ---------------------------------------------------------------------------------------------------------------
def read_audio(path=None, data=None):
    """Any file libsndfile reads (WAV, FLAC, OGG, MP3, AIFF, ...) -> (int16 (n, ch), rate)."""
    import soundfile as sf
    src = io.BytesIO(data) if data is not None else path
    audio, rate = sf.read(src, dtype='int16', always_2d=True)
    return audio, rate


def resample(audio, src, dst):
    if src == dst or not len(audio):
        return audio
    n = int(round(len(audio) * dst / src))
    x = np.linspace(0, len(audio) - 1, n)
    out = np.empty((n, audio.shape[1]), np.float64)
    for c in range(audio.shape[1]):
        out[:, c] = np.interp(x, np.arange(len(audio)), audio[:, c].astype(np.float64))
    return np.clip(np.rint(out), -32768, 32767).astype(np.int16)


def mpeg_rate(rate):
    """The MPEG Layer III sample rate to use for `rate` (the closest one not below it, else 48000)."""
    ok = sorted(r for r in MPEG_RATES if r >= rate)
    return rate if rate in MPEG_RATES else (ok[0] if ok else 48000)


def _mp3_encode(audio, rate, quality):
    import soundfile as sf
    buf = io.BytesIO()
    with sf.SoundFile(buf, 'w', rate, audio.shape[1], 'MPEG_LAYER_III', format='MP3',
                      compression_level=quality, bitrate_mode='VARIABLE') as f:
        # float input: libsndfile 1.2 garbles interleaved int16 stereo when encoding MP3
        f.write(np.ascontiguousarray(audio, np.float32) / 32768.0)
    return buf.getvalue()


class _Frame:
    __slots__ = ('vi', 'si', 'cm', 'me', 'ch', 'scfsi', 'granules')


def parse_mp3(data):
    """MP3 bytes -> ([_Frame with granules [[(part2_3_length, rest bits, main bits) per channel]]],
    encoder delay or None). The Xing / Info / LAME tag frame is skipped."""
    pos = 0
    if data[:3] == b'ID3':
        sz = data[6:10]
        pos = 10 + ((sz[0] & 0x7F) << 21 | (sz[1] & 0x7F) << 14 | (sz[2] & 0x7F) << 7 | (sz[3] & 0x7F))
    frames = []
    reservoir = bytearray()
    delay = None
    while pos + 4 <= len(data):
        h = struct.unpack_from('>I', data, pos)[0]
        if (h >> 21) != 0x7FF:
            if data[pos:pos + 3] == b'TAG':
                break
            pos += 1
            continue
        vi = (h >> 19) & 3
        layer = (h >> 17) & 3
        prot = (h >> 16) & 1
        bri = (h >> 12) & 15
        si = (h >> 10) & 3
        pad = (h >> 9) & 1
        cm = (h >> 6) & 3
        me = (h >> 4) & 3
        if vi == 1 or layer != 1 or bri in (0, 15) or si == 3:
            pos += 1
            continue
        mpeg1 = vi == 3
        rate = (MPEG1_RATES if mpeg1 else MPEG2_RATES if vi == 2 else MPEG25_RATES)[si]
        kbps = (BITRATES_V1 if mpeg1 else BITRATES_V2)[bri]
        flen = (144 if mpeg1 else 72) * kbps * 1000 // rate + pad
        ch = 1 if cm == 3 else 2
        side_len = (17 if ch == 1 else 32) if mpeg1 else (9 if ch == 1 else 17)
        body = pos + 4 + (0 if prot else 2)
        frame = data[pos:pos + flen]
        tag_at = frame.find(b'Xing')
        if tag_at < 0:
            tag_at = frame.find(b'Info')
        if 0 <= tag_at <= 4 + 2 + side_len + 4:
            lame = frame.find(b'LAME')
            if lame >= 0 and lame + 24 <= len(frame):
                d = frame[lame + 21:lame + 24]
                delay = (d[0] << 4) | (d[1] >> 4)
            pos += flen
            continue
        br = BitReader(data, body)
        main_begin = br.read(9 if mpeg1 else 8)
        br.read((5 if ch == 1 else 3) if mpeg1 else (1 if ch == 1 else 2))
        scfsi = [br.read(4) for _ in range(ch)] if mpeg1 else [0] * ch
        ngr = 2 if mpeg1 else 1
        side = []
        for _ in range(ngr):
            row = []
            for _ in range(ch):
                row.append((br.read(12), br.field(47 if mpeg1 else 51)))
            side.append(row)
        start = len(reservoir) - main_begin
        reservoir += data[body + side_len:pos + flen]
        mr = BitReader(reservoir, start)
        grans = []
        for row in side:
            grans.append([(size, rest, mr.field(size)) for size, rest in row])
        fr = _Frame()
        fr.vi, fr.si, fr.cm, fr.me, fr.ch, fr.scfsi, fr.granules = vi, si, cm, me, ch, scfsi, grans
        frames.append(fr)
        pos += flen
    return frames, delay


def _ea_frame(fr, gi, gran, pcm=None):
    """One EALayer3 v1 frame (bytes) for granule `gi` of MP3 frame `fr`."""
    mpeg1 = fr.vi == 3
    bw = BitWriter()
    bw.write(0xEE if pcm is not None else 0x00, 8)
    bw.write(fr.vi, 2)
    bw.write(fr.si, 2)
    bw.write(fr.cm, 2)
    bw.write(fr.me, 2)
    bw.write(gi if mpeg1 else 0, 1)
    if mpeg1 and gi == 1:
        for c in range(fr.ch):
            bw.write(fr.scfsi[c], 4)
    for size, rest, _ in gran:
        bw.write(size, 12)
        bw.write(*rest)
    for _, _, main in gran:
        bw.write(*main)
    out = bw.bytes()
    if pcm is not None:
        out += struct.pack('>HHI', 0, len(pcm), 0) + np.ascontiguousarray(pcm, '>i2').tobytes()
    return out


def encode_sps(audio, rate, loop=False, loop_start=0, quality=0.2):
    """int16 (n, channels) -> (SPS stream bytes, header dict). Channels are encoded as stereo pairs (+ a mono
    stream for an odd channel); the rate must be an MPEG rate (see mpeg_rate / resample)."""
    if rate not in MPEG_RATES:
        raise AudioError(f'{rate} Hz is not an MPEG Layer III rate')
    n, nch = audio.shape
    groups = [list(range(c, min(c + 2, nch))) for c in range(0, nch, 2)]
    streams = []
    for grp in groups:
        frames, delay = parse_mp3(_mp3_encode(audio[:, grp], rate, quality))
        total_delay = (576 if delay is None else delay) + DECODER_DELAY
        grans = [(fr, gi, g) for fr in frames for gi, g in enumerate(fr.granules)]
        streams.append((grp, grans, total_delay))
    total_delay = streams[0][2]
    npcm = START_SKIP - total_delay
    if npcm < 0:
        raise AudioError(f'unexpected encoder delay {total_delay}')
    ngr = min(len(s[1]) for s in streams)
    # blocks of about 0.216 s, the first one two granules longer (as in the game files)
    per_block = max(1, int(0.216 * rate / 576))
    sizes = [min(ngr, per_block + 2)]
    while sum(sizes) < ngr:
        sizes.append(min(per_block, ngr - sum(sizes)))
    available = ngr * 576 + npcm - START_SKIP
    total = min(n, available)
    blocks = []
    g0 = 0
    done = 0
    for bi, cnt in enumerate(sizes):
        body = bytearray()
        for g in range(g0, g0 + cnt):
            for grp, grans, _ in streams:
                fr, gi, gran = grans[g]
                pcm = audio[:npcm, grp] if g == 1 else None
                body += _ea_frame(fr, gi, gran, pcm)
        raw = cnt * 576 + (npcm if g0 <= 1 < g0 + cnt else 0) - (START_SKIP if bi == 0 else 0)
        samples = max(0, min(raw, total - done))
        done += samples
        blocks.append(struct.pack('>II', 0x44000000 | (8 + len(body)), samples) + bytes(body))
        g0 += cnt
    h1 = (1 << 28) | (CODEC_EALAYER3_V1 << 24) | ((nch - 1) << 18) | rate
    h2 = (1 << 30) | ((1 << 29) if loop else 0) | done
    hdr = struct.pack('>III', 0x48000000 | (16 if loop else 12), h1, h2) + (struct.pack('>I', loop_start) if loop else b'')
    sps = hdr + b''.join(blocks) + struct.pack('>I', 0x45000004)
    return sps, snr_header(h1, h2, loop_start if loop else None)


# ---------------------------------------------------------------------------------------------------------------
# Wave resources (type 0x81)
# ---------------------------------------------------------------------------------------------------------------
WAVE_HEADER = 0x80


def build_wave(old, sps, head, e):
    """New Wave chunk 0: the old header with duration / stream size / channels updated + the SPS stream."""
    hdr = bytearray(old[:WAVE_HEADER]) if old is not None and len(old) >= WAVE_HEADER else bytearray(WAVE_HEADER)
    if old is None:
        struct.pack_into(e + 'I', hdr, 0, WAVE_HEADER)
        struct.pack_into(e + 'f', hdr, 0x10, 1.0)
    struct.pack_into((_dur_order(hdr, e) if old is not None else '<') + 'f', hdr, 0x14, head['samples'] * 1000.0 / head['rate'])
    struct.pack_into(e + 'I', hdr, 0x18, len(sps))
    if e == '>' and struct.unpack_from('>I', hdr, 4)[0]:
        struct.pack_into('>I', hdr, 4, (len(sps) + 128 + 127) // 128 * 128)
    if struct.unpack_from(e + 'I', hdr, 0x20)[0] == 2:
        struct.pack_into(e + 'I', hdr, 8, (WAVE_HEADER + len(sps) + 7) // 8 * 8)
    hdr[0x24] = head['channels']
    data = bytes(hdr) + sps
    return data + b'\0' * ((-len(data)) % 16)


# ---------------------------------------------------------------------------------------------------------------
# Wave header (0x80 bytes, platform byte order unless noted), then the SPS stream
#   0x00 u32 stream offset (0x80; 0 for a stream reference)     0x04 u32 PS3: memory size, aligned to 128
#   0x08 u32 stream reference: path offset (0x28); prefetch: 0x80 + prefetch size, aligned to 8
#   0x0C u32 prefetch: 0x28                                      0x10 f32 volume (linear)
#   0x14 f32 duration in ms (little-endian on PS3 too)           0x18 u32 size of the stream in the resource
#   0x20 u32 type: 0 in memory, 1 stream file (path at 0x28), 2 prefetched stream (<GameChanger id>.SPS)
#   0x24 u8 channels, u8 flag                                    0x28 type 2: f32 prefetch length in ms
# ---------------------------------------------------------------------------------------------------------------
AUDIO_EXT = ('.wav', '.flac', '.ogg', '.oga', '.mp3', '.aif', '.aiff', '.w64', '.caf')


def _dur_order(c, e):
    if e == '<':
        return '<'
    be = struct.unpack_from('>f', c, 0x14)[0]
    return '>' if 0.01 < abs(be) < 1e8 else '<'


def wave_fields(c, e):
    """kind: 'memory' (the whole sound is in the resource), 'stream' (plays the .SPS file named at 0x28),
    'prefetch' (the first prefetch_ms are in the resource, the rest in <GameChanger id>.SPS)."""
    de = _dur_order(c, e)
    f = {'offset': struct.unpack_from(e + 'I', c, 0)[0], 'volume': struct.unpack_from(e + 'f', c, 0x10)[0],
         'duration': struct.unpack_from(de + 'f', c, 0x14)[0], 'size': struct.unpack_from(e + 'I', c, 0x18)[0],
         'type': struct.unpack_from(e + 'I', c, 0x20)[0] if len(c) >= 0x28 else 0,
         'channels': c[0x24] if len(c) > 0x24 else 0, 'stream_ref': None, 'kind': 'memory'}
    if len(c) <= 0x88 or f['offset'] == 0:
        off = struct.unpack_from(e + 'I', c, 8)[0] or 0x28
        f['stream_ref'] = bytes(c[off:]).split(b'\0')[0].decode('latin1', 'replace')
        f['kind'] = 'stream'
    elif f['type'] == 2:
        f['kind'] = 'prefetch'
        f['prefetch_ms'] = struct.unpack_from(e + 'f', c, 0x28)[0]
    return f


def wave_stream(c, e):
    """SPS bytes kept in the resource (None for a stream reference)."""
    f = wave_fields(c, e)
    if f['kind'] == 'stream':
        return None
    return bytes(c[f['offset']:f['offset'] + f['size']]) if f['size'] else bytes(c[WAVE_HEADER:])


def convert_wave(c, src_e, dst_e):
    """Wave chunk 0 in the other platform's byte order (the SPS stream is big-endian on both)."""
    f = wave_fields(c, src_e)
    hdr = bytearray(c[:WAVE_HEADER].ljust(WAVE_HEADER, b'\0'))
    for o in (0x00, 0x04, 0x08, 0x0C, 0x18, 0x1C, 0x20):
        struct.pack_into(dst_e + 'I', hdr, o, struct.unpack_from(src_e + 'I', c, o)[0])
    struct.pack_into(dst_e + 'f', hdr, 0x10, f['volume'])
    struct.pack_into('<f', hdr, 0x14, f['duration'])
    if f['kind'] == 'prefetch':
        struct.pack_into(dst_e + 'f', hdr, 0x28, f['prefetch_ms'])
    if f['kind'] == 'stream':
        off = struct.unpack_from(src_e + 'I', c, 8)[0] or 0x28
        path = bytes(c[off:]).split(b'\0')[0] + b'\0'
        out = bytes(hdr[:off]) + path
        return out + b'\0' * ((-len(out)) % 16)
    if dst_e == '>' and src_e == '<':
        struct.pack_into('>I', hdr, 4, 0)
    return bytes(hdr) + bytes(c[WAVE_HEADER:])


def split_blocks(sps):
    """[(block id, bytes, samples)] of an SPS stream (samples 0 for non-data blocks)."""
    out = []
    pos = 0
    while pos + 4 <= len(sps):
        bid = sps[pos]
        size = struct.unpack_from('>I', sps, pos)[0] & 0xFFFFFF
        if size < 4:
            break
        blk = bytes(sps[pos:pos + size])
        out.append((bid, blk, struct.unpack_from('>I', blk, 4)[0] if bid == 0x44 else 0))
        pos += size
        if bid == 0x45:
            break
    return out


def prefetch_part(sps, ms, rate):
    """Header block + the data blocks covering the first `ms` milliseconds (no end block), as the game stores
    the start of a streamed sound."""
    want = ms * rate / 1000.0
    out = bytearray()
    have = 0
    for bid, blk, n in split_blocks(sps):
        if bid == 0x48:
            out += blk
        elif bid == 0x44 and have < want:
            out += blk
            have += n
    return bytes(out)


def mix_channels(audio, n):
    """Mix audio to n channels: mono -> copies, many -> mono average, else channels repeated / pairs averaged."""
    src = audio.shape[1]
    if src == n:
        return audio
    a = audio.astype(np.float64)
    if n == 1:
        out = a.mean(1, keepdims=True)
    elif src == 1:
        out = np.repeat(a, n, 1)
    elif n < src:
        out = np.stack([a[:, c::n].mean(1) for c in range(n)], 1)
    else:
        out = np.stack([a[:, c % src] for c in range(n)], 1)
    return np.clip(np.rint(out), -32768, 32767).astype(np.int16)


def prepare_audio(audio, rate, channels=None, target_rate=None):
    """Mix / resample for encoding: channels None = keep; rate: target_rate (made an MPEG rate) or the file's."""
    if channels:
        audio = mix_channels(audio, channels)
    dst = mpeg_rate(target_rate or rate)
    return resample(audio, rate, dst), dst
