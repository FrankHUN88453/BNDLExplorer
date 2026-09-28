"""Textures (RwRaster, resource type 0x01) of the PC game and the PS3 prototype.

PC   chunk 0 = 48-byte header, chunk 1 = pixels: for each face, every mip level (largest first), packed; the
     total is padded to 128 bytes.
       0x00 u32 0, u32 1, u32 7 (2D) / 9 (cube), u32 0, 12 bytes 0
       0x1C u32 DXGI format, u32 flags (0x10 sRGB, 0x20 set on mip-mapped textures)
       0x24 u16 width, height, depth (1; cube 0), array size (1; cube 6), u8 unknown (0-3), u8 mip count, u16 0
PS3  chunk 0 = 48-byte header, chunk 2 = pixels: for each face every mip level; cube faces start on 128 bytes.
       0x00 CellGcmTexture {u8 format, u8 mips, u8 dimension (2), u8 cubemap, u32 remap (0xAAE4), u16 width,
            u16 height, u16 depth, u8 location, u8 0, u32 pitch, u32 offset}
       0x18 u32 0, u16 cube, u16 2, u32 0x20 (sRGB) or 0, u8 format, u8 (8 = A8R8G8B8, 11 = RGBA16F, else 0),
       0x26 u16 0x7255, 8 bytes 0
     GCM format = base | 0x20 (linear, else Morton-swizzled) | 0x40 (unnormalised). DXT blocks are the same as
     on PC; A8R8G8B8 is stored A, R, G, B and swizzled; RGBA16F is big-endian.
"""
import struct
from dataclasses import dataclass

import numpy as np

from . import bcenc

# canonical format names
BC1, BC2, BC3, BC4, BC5, RGBA8, BGRA8, R8, RGBA16F, L8, ARGB8 = (
    'BC1', 'BC2', 'BC3', 'BC4', 'BC5', 'RGBA8', 'BGRA8', 'R8', 'RGBA16F', 'L8', 'ARGB8')
DXGI = {71: BC1, 72: BC1, 74: BC2, 75: BC2, 77: BC3, 78: BC3, 80: BC4, 83: BC5, 28: RGBA8, 29: RGBA8,
        87: BGRA8, 91: BGRA8, 61: R8, 10: RGBA16F}
DXGI_OF = {BC1: 71, BC2: 74, BC3: 77, BC4: 80, BC5: 83, RGBA8: 28, BGRA8: 87, R8: 61, RGBA16F: 10}
GCM = {0x86: BC1, 0x87: BC2, 0x88: BC3, 0x85: ARGB8, 0x81: L8, 0x9A: RGBA16F}
GCM_OF = {BC1: 0x86, BC2: 0x87, BC3: 0x88, ARGB8: 0x85, RGBA16F: 0x9A}
GCM_CODE = {0x85: 8, 0x9A: 11}
BLOCK = {BC1: 8, BC2: 16, BC3: 16, BC4: 8, BC5: 16}
BPP = {RGBA8: 4, BGRA8: 4, ARGB8: 4, R8: 1, L8: 1, RGBA16F: 8}
FORMAT_HELP = {
    BC1: 'BC1 / DXT1 (colour, 1-bit alpha)', BC2: 'BC2 / DXT3 (colour + sharp alpha)',
    BC3: 'BC3 / DXT5 (colour + smooth alpha)', RGBA8: 'R8G8B8A8 (uncompressed)', BGRA8: 'B8G8R8A8 (uncompressed)',
    R8: 'R8 (one channel)', RGBA16F: 'RGBA 16-bit float (HDR)', ARGB8: 'A8R8G8B8 (uncompressed)',
    L8: 'L8 (one channel)', BC4: 'BC4 (one channel)', BC5: 'BC5 (two channels)'}
PC_FLAG_SRGB, PC_FLAG_MIPS = 0x10, 0x20


class RasterError(ValueError):
    pass


@dataclass
class Info:
    platform: str
    fmt: str
    code: int          # DXGI format (PC) or GCM format byte (PS3)
    w: int
    h: int
    mips: int
    faces: int
    srgb: bool
    flags: int = 0     # PC flags
    swizzled: bool = False
    pitch: int = 0
    unknown: int = 0   # PC byte 0x2C

    def describe(self):
        s = f'{self.w} x {self.h}, {FORMAT_HELP.get(self.fmt, self.fmt)}, {self.mips} mip level(s)'
        if self.faces == 6:
            s += ', cube map'
        s += ', sRGB' if self.srgb else ', linear'
        if self.platform == 'PS3' and self.fmt in BPP:
            s += ', swizzled' if self.swizzled else ', linear layout'
        return s


def info(res, platform):
    h = res.data(0)
    if len(h) < 48:
        raise RasterError('raster header is too short')
    if platform == 'PC':
        code, flags = struct.unpack_from('<II', h, 0x1C)
        w, hh, depth, arr = struct.unpack_from('<4H', h, 0x24)
        faces = 6 if (struct.unpack_from('<I', h, 8)[0] == 9 or arr == 6) else 1
        return Info('PC', DXGI.get(code, f'DXGI {code}'), code, w, hh, max(1, h[0x2D]), faces,
                    bool(flags & PC_FLAG_SRGB), flags, unknown=h[0x2C])
    raw, mips, cube = h[0], h[1], h[3]
    w, hh, depth = struct.unpack_from('>3H', h, 8)
    pitch = struct.unpack_from('>I', h, 0x10)[0]
    base = raw & ~0x60
    srgb = bool(struct.unpack_from('>I', h, 0x20)[0] & 0x20)
    return Info('PS3', GCM.get(base, f'GCM {raw:#x}'), raw, w, hh, max(1, mips), 6 if cube else 1, srgb,
                swizzled=not (raw & 0x20), pitch=pitch)


# ---------------------------------------------------------------------------------------------------------------
# level layout
# ---------------------------------------------------------------------------------------------------------------
def level_size(fmt, w, h):
    if fmt in BLOCK:
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * BLOCK[fmt]
    return w * h * BPP[fmt]


def mip_dims(w, h, mips):
    out = []
    for _ in range(mips):
        out.append((w, h))
        w, h = max(1, w // 2), max(1, h // 2)
    return out


def full_mip_count(w, h):
    return max(w, h).bit_length()


def levels_of(inf, data):
    """[(face, mip, w, h, bytes)] for the raster's pixel data."""
    if inf.fmt not in BLOCK and inf.fmt not in BPP:
        raise RasterError(f'format {inf.fmt} is not supported')
    out = []
    pos = 0
    for face in range(inf.faces):
        if inf.platform == 'PS3' and face:
            pos = (pos + 127) // 128 * 128
        for mip, (w, h) in enumerate(mip_dims(inf.w, inf.h, inf.mips)):
            n = level_size(inf.fmt, w, h)
            if inf.platform == 'PS3' and not inf.swizzled and inf.fmt in BPP and mip == 0 and inf.pitch:
                n = inf.pitch * h
            if pos + n > len(data):
                if face == 0 and mip == 0:
                    raise RasterError('pixel data is shorter than the header says')
                return out
            out.append((face, mip, w, h, data[pos:pos + n]))
            pos += n
    return out


# ---------------------------------------------------------------------------------------------------------------
# decoding
# ---------------------------------------------------------------------------------------------------------------
def morton(w, h):
    """Linear index -> (x, y) of the PS3 swizzle (x / y bits interleaved while both have bits left)."""
    lw, lh = max(w, 1).bit_length() - 1, max(h, 1).bit_length() - 1
    idx = np.arange(w * h, dtype=np.int64)
    x = np.zeros_like(idx)
    y = np.zeros_like(idx)
    bit = sx = sy = 0
    while sx < lw or sy < lh:
        if sx < lw:
            x |= ((idx >> bit) & 1) << sx
            bit += 1
            sx += 1
        if sy < lh:
            y |= ((idx >> bit) & 1) << sy
            bit += 1
            sy += 1
    return x, y


def is_pow2(v):
    return v > 0 and v & (v - 1) == 0


def unswizzle(buf, w, h, bpp):
    src = np.frombuffer(buf[:w * h * bpp], np.uint8).reshape(-1, bpp)
    x, y = morton(w, h)
    out = np.zeros((h, w, bpp), np.uint8)
    out[y, x] = src
    return out


def swizzle(img):
    h, w, bpp = img.shape
    x, y = morton(w, h)
    return np.ascontiguousarray(img[y, x]).tobytes()


def _565(c):
    r = ((c >> 11) & 31) * 255 // 31
    g = ((c >> 5) & 63) * 255 // 63
    b = (c & 31) * 255 // 31
    return np.stack([r, g, b], -1).astype(np.int32)


def _alpha_block(blk):
    a0 = blk[:, 0].astype(np.int32)
    a1 = blk[:, 1].astype(np.int32)
    bits = np.zeros(len(blk), np.int64)
    for i in range(6):
        bits |= blk[:, 2 + i].astype(np.int64) << (8 * i)
    sel = (bits[:, None] >> (3 * np.arange(16))) & 7
    gt = a0 > a1
    pal = np.zeros((len(blk), 8), np.int32)
    pal[:, 0], pal[:, 1] = a0, a1
    for k in range(2, 8):
        eight = ((8 - k) * a0 + (k - 1) * a1) // 7
        six = ((6 - k) * a0 + (k - 1) * a1) // 5 if k < 6 else (0 if k == 6 else 255)
        pal[:, k] = np.where(gt, eight, six)
    return np.take_along_axis(pal, sel, 1)


def decode_bc(data, w, h, fmt):
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    bs = BLOCK[fmt]
    blk = np.frombuffer(data[:bw * bh * bs], np.uint8).reshape(bh * bw, bs)
    if fmt in (BC4, BC5):
        r = _alpha_block(blk[:, :8])
        g = _alpha_block(blk[:, 8:]) if fmt == BC5 else r
        b = np.zeros_like(r) if fmt == BC5 else r
        px = np.stack([r, g, b, np.full_like(r, 255)], -1)
    else:
        cb = blk[:, bs - 8:]
        c0 = cb[:, 0].astype(np.int32) | (cb[:, 1].astype(np.int32) << 8)
        c1 = cb[:, 2].astype(np.int32) | (cb[:, 3].astype(np.int32) << 8)
        bits = (cb[:, 4].astype(np.int64) | (cb[:, 5].astype(np.int64) << 8) | (cb[:, 6].astype(np.int64) << 16)
                | (cb[:, 7].astype(np.int64) << 24))
        p0, p1 = _565(c0), _565(c1)
        four = (c0 > c1) | (fmt != BC1)
        p2 = np.where(four[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2)
        p3 = np.where(four[:, None], (p0 + 2 * p1) // 3, 0)
        pal = np.stack([p0, p1, p2, p3], 1)
        sel = (bits[:, None] >> (2 * np.arange(16))) & 3
        rgb = np.take_along_axis(pal, sel[:, :, None].repeat(3, 2), 1)
        if fmt == BC1:
            alpha = np.where((~four)[:, None] & (sel == 3), 0, 255)
        elif fmt == BC2:
            a = blk[:, :8]
            alpha = np.stack([a & 15, a >> 4], -1).reshape(-1, 16).astype(np.int32) * 17
        else:
            alpha = _alpha_block(blk[:, :8])
        px = np.concatenate([rgb, alpha[:, :, None]], -1)
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return np.ascontiguousarray(img[:h, :w]).astype(np.uint8)


def half_to_display(v):
    """RGBA16F (float) -> RGBA8 for display (Reinhard tone map, sRGB)."""
    rgb = np.maximum(v[..., :3], 0)
    rgb = rgb / (1.0 + rgb)
    rgb = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(np.clip(rgb, 0, 1), 1 / 2.4) - 0.055)
    a = np.clip(v[..., 3:4], 0, 1)
    return np.clip(np.concatenate([rgb, a], -1) * 255 + 0.5, 0, 255).astype(np.uint8)


def decode_level(inf, w, h, data):
    """One level -> RGBA uint8 (h, w, 4)."""
    f = inf.fmt
    if f in BLOCK:
        return decode_bc(data, w, h, f)
    ps3 = inf.platform == 'PS3'
    bpp = BPP[f]
    if ps3 and inf.swizzled and is_pow2(w) and is_pow2(h):
        px = unswizzle(data, w, h, bpp)
    else:
        pitch = inf.pitch if (ps3 and inf.pitch and not inf.swizzled and inf.pitch >= w * bpp) else w * bpp
        rows = np.frombuffer(data[:pitch * h], np.uint8).reshape(h, pitch)
        px = rows[:, :w * bpp].reshape(h, w, bpp)
    if f == RGBA8:
        return px.copy()
    if f == BGRA8:
        return px[:, :, [2, 1, 0, 3]].copy()
    if f == ARGB8:
        return px[:, :, [1, 2, 3, 0]].copy()
    if f in (R8, L8):
        l8 = px[:, :, 0]
        return np.stack([l8, l8, l8, np.full_like(l8, 255)], -1)
    if f == RGBA16F:
        v = np.frombuffer(px.tobytes(), '>f2' if ps3 else '<f2').reshape(h, w, 4).astype(np.float32)
        return half_to_display(v)
    raise RasterError(f'format {f} is not supported')


def pixel_chunk(res, platform):
    return res.data(1 if platform == 'PC' else 2)


def decode(res, platform, mip=0, face=0):
    inf = info(res, platform)
    for fc, m, w, h, data in levels_of(inf, pixel_chunk(res, platform)):
        if fc == face and m == mip:
            return decode_level(inf, w, h, data)
    raise RasterError(f'no mip {mip} / face {face}')


# ---------------------------------------------------------------------------------------------------------------
# encoding
# ---------------------------------------------------------------------------------------------------------------
def srgb_to_linear(x):
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(x):
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(np.clip(x, 0, 1), 1 / 2.4) - 0.055)


def mip_chain(img, count=None, srgb=False):
    """RGBA uint8 -> list of levels (box filter; colour averaged in linear light for sRGB textures)."""
    h, w = img.shape[:2]
    count = count or full_mip_count(w, h)
    levels = [np.ascontiguousarray(img, np.uint8)]
    cur = img.astype(np.float32) / 255.0
    if srgb:
        cur[..., :3] = srgb_to_linear(cur[..., :3])
    for _ in range(count - 1):
        h, w = cur.shape[:2]
        h2, w2 = max(1, h // 2), max(1, w // 2)
        c = cur[:h2 * (2 if h > 1 else 1), :w2 * (2 if w > 1 else 1)]
        cur = c.reshape(h2, c.shape[0] // h2, w2, c.shape[1] // w2, 4).mean((1, 3))
        out = cur.copy()
        if srgb:
            out[..., :3] = linear_to_srgb(out[..., :3])
        levels.append(np.clip(np.rint(out * 255), 0, 255).astype(np.uint8))
    return levels


def encode_level(img, fmt, platform):
    """RGBA uint8 level -> bytes in `fmt` (PS3: uncompressed levels swizzled when the size allows)."""
    if fmt in (BC1, BC2, BC3):
        return bcenc.encode(img, {BC1: 1, BC2: 2, BC3: 3}[fmt])
    if fmt == RGBA8:
        px = img
    elif fmt == BGRA8:
        px = img[:, :, [2, 1, 0, 3]]
    elif fmt == ARGB8:
        px = img[:, :, [3, 0, 1, 2]]
    elif fmt in (R8, L8):
        px = img[:, :, :1]
    else:
        raise RasterError(f'cannot encode {fmt}')
    px = np.ascontiguousarray(px, np.uint8)
    h, w = px.shape[:2]
    if platform == 'PS3' and is_pow2(w) and is_pow2(h):
        return swizzle(px)
    return px.tobytes()


def pad(data, a=128):
    return data + b'\0' * ((-len(data)) % a)


def pc_header(fmt, w, h, mips, faces=1, srgb=False, flags=None, unknown=None):
    if flags is None:
        flags = (PC_FLAG_SRGB if srgb else 0) | (PC_FLAG_MIPS if mips > 1 else 0)
    else:
        flags = (flags & ~PC_FLAG_SRGB) | (PC_FLAG_SRGB if srgb else 0)
    if unknown is None:
        unknown = 1 if mips > 1 else 0
    cube = faces == 6
    hdr = struct.pack('<IIII12x', 0, 1, 9 if cube else 7, 0)
    hdr += struct.pack('<IIHHHHBBH', DXGI_OF[fmt], flags, w, h, 0 if cube else 1, 6 if cube else 1, unknown, mips, 0)
    return hdr


def ps3_header(fmt, w, h, mips, faces=1, srgb=False, swizzled=True):
    raw = GCM_OF[fmt] | (0 if swizzled or fmt in BLOCK else 0x20)
    if fmt in BLOCK:
        pitch = w * (2 if fmt == BC1 else 4)             # as the prototype stores it (tiny mips too)
    else:
        pitch = w * BPP[fmt]
    cube = faces == 6
    hdr = struct.pack('>BBBBIHHHBBII', raw, mips, 2, 1 if cube else 0, 0x0000AAE4, w, h, 1, 0, 0, pitch, 0)
    hdr += struct.pack('>IHHIBBH8x', 0, 1 if cube else 0, 2, 0x20 if srgb else 0, GCM_OF[fmt],
                       GCM_CODE.get(GCM_OF[fmt], 0), 0x7255)
    assert len(hdr) == 48
    return hdr


def pack_levels(levels, platform, faces):
    """levels: [[bytes per mip] per face] -> pixel chunk."""
    out = b''
    for f, mips in enumerate(levels):
        if platform == 'PS3' and f:
            out = pad(out)
        out += b''.join(mips)
    return pad(out)


def storable(fmt, platform):
    return fmt in (DXGI_OF if platform == 'PC' else GCM_OF)


def target_format(fmt, platform):
    """Closest format the platform stores (PC R8 -> PS3 ARGB8, PS3 ARGB8 -> PC RGBA8, ...)."""
    if storable(fmt, platform):
        return fmt
    if fmt == L8 and platform == 'PC':
        return R8
    if fmt in (RGBA8, BGRA8, ARGB8, R8, L8):
        return RGBA8 if platform == 'PC' else ARGB8
    if fmt in (BC4, BC5):
        return BC3 if platform == 'PS3' else fmt
    raise RasterError(f'{fmt} cannot be stored on {platform}')


def make(platform, images, fmt, srgb, mips=None, template=None):
    """RGBA images (one per face, uint8 HxWx4) -> (chunk 0, pixel chunk) for `platform`.
    mips: level count (None = full chain; 1 = no mips). template: old header whose flags are kept (PC)."""
    fmt = target_format(fmt, platform)
    h, w = images[0].shape[:2]
    mips = mips or full_mip_count(w, h)
    mips = min(mips, full_mip_count(w, h))
    faces = []
    for img in images:
        faces.append([encode_level(l, fmt, platform) for l in mip_chain(img, mips, srgb)])
    return _finish(platform, fmt, w, h, mips, len(images), srgb, faces, template)


def _finish(platform, fmt, w, h, mips, nfaces, srgb, faces, template):
    if platform == 'PC':
        flags = unknown = None
        if template is not None and len(template) >= 48:
            flags = struct.unpack_from('<I', template, 0x20)[0]
            flags = (flags & ~PC_FLAG_MIPS) | (PC_FLAG_MIPS if mips > 1 else 0)
            unknown = template[0x2C]
        hdr = pc_header(fmt, w, h, mips, nfaces, srgb, flags, unknown)
    else:
        hdr = ps3_header(fmt, w, h, mips, nfaces, srgb, swizzled=is_pow2(w) and is_pow2(h))
    return hdr, pack_levels(faces, platform, nfaces)


# ---------------------------------------------------------------------------------------------------------------
# PS3 <-> PC
# ---------------------------------------------------------------------------------------------------------------
def convert(res, src, dst):
    """(chunk 0, pixels) of a raster converted between platforms. Block-compressed data is copied as it is;
    uncompressed data is (un)swizzled and reordered; RGBA16F is byte swapped."""
    inf = info(res, src)
    levels = levels_of(inf, pixel_chunk(res, src))
    fmt = target_format(inf.fmt, dst)
    faces = [[] for _ in range(inf.faces)]
    mips = min(inf.mips, max(m for _, m, *_ in levels) + 1)
    for face, mip, w, h, data in levels:
        if mip >= mips:
            continue
        if inf.fmt in BLOCK and fmt == inf.fmt:
            out = bytes(data[:level_size(fmt, w, h)])
        elif inf.fmt == RGBA16F:
            px = _raw_level(inf, w, h, data, 8)
            v = np.frombuffer(px.tobytes(), '>f2' if src == 'PS3' else '<f2').astype('<f2' if dst == 'PC' else '>f2')
            px = v.view(np.uint8).reshape(h, w, 8)
            out = swizzle(px) if dst == 'PS3' and is_pow2(w) and is_pow2(h) else px.tobytes()
        else:
            out = encode_level(decode_level(inf, w, h, data), fmt, dst)
        faces[face].append(out)
    template = res.data(0) if src == dst else None
    return _finish(dst, fmt, inf.w, inf.h, mips, inf.faces, inf.srgb, faces, template)


def _raw_level(inf, w, h, data, bpp):
    if inf.platform == 'PS3' and inf.swizzled and is_pow2(w) and is_pow2(h):
        return unswizzle(data, w, h, bpp)
    return np.frombuffer(data[:w * h * bpp], np.uint8).reshape(h, w, bpp)


# ---------------------------------------------------------------------------------------------------------------
# DDS
# ---------------------------------------------------------------------------------------------------------------
DDSD_CAPS, DDSD_HEIGHT, DDSD_WIDTH, DDSD_PITCH, DDSD_PIXELFORMAT, DDSD_MIPMAPCOUNT, DDSD_LINEARSIZE = (
    0x1, 0x2, 0x4, 0x8, 0x1000, 0x20000, 0x80000)
DDPF_ALPHAPIXELS, DDPF_FOURCC, DDPF_RGB, DDPF_LUMINANCE = 0x1, 0x4, 0x40, 0x20000
DDSCAPS_COMPLEX, DDSCAPS_TEXTURE, DDSCAPS_MIPMAP = 0x8, 0x1000, 0x400000
DDSCAPS2_CUBEMAP_ALL = 0x200 | 0xFC00
FOURCC = {BC1: b'DXT1', BC2: b'DXT3', BC3: b'DXT5', BC4: b'ATI1', BC5: b'ATI2'}


def to_dds(res, platform):
    """The whole raster (every face and mip) as a .dds file in its own format (PS3 data converted to PC
    layout first: DDS stores rows, not swizzled data)."""
    if platform == 'PS3':
        hdr, pix = convert(res, 'PS3', 'PC')

        class _R:
            def data(self, k):
                return (hdr, pix)[k] if k < 2 else b''
        res = _R()
    inf = info(res, 'PC')
    levels = levels_of(inf, res.data(1))
    body = b''.join(bytes(d[:level_size(inf.fmt, w, h)]) for _, _, w, h, d in levels)
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_MIPMAPCOUNT
    ext = b''
    if inf.fmt in FOURCC:
        flags |= DDSD_LINEARSIZE
        pitch = level_size(inf.fmt, inf.w, inf.h)
        pf = struct.pack('<II4sI4I', 32, DDPF_FOURCC, FOURCC[inf.fmt], 0, 0, 0, 0, 0)
    elif inf.fmt == RGBA8:
        flags |= DDSD_PITCH
        pitch = inf.w * 4
        pf = struct.pack('<II4sI4I', 32, DDPF_RGB | DDPF_ALPHAPIXELS, b'\0' * 4, 32,
                         0xFF, 0xFF00, 0xFF0000, 0xFF000000)
    elif inf.fmt == BGRA8:
        flags |= DDSD_PITCH
        pitch = inf.w * 4
        pf = struct.pack('<II4sI4I', 32, DDPF_RGB | DDPF_ALPHAPIXELS, b'\0' * 4, 32,
                         0xFF0000, 0xFF00, 0xFF, 0xFF000000)
    else:                                   # R8, RGBA16F: DX10 header
        flags |= DDSD_PITCH
        pitch = inf.w * BPP[inf.fmt]
        pf = struct.pack('<II4sI4I', 32, DDPF_FOURCC, b'DX10', 0, 0, 0, 0, 0)
        ext = struct.pack('<5I', DXGI_OF[inf.fmt], 3, 4 if inf.faces == 6 else 0, 1, 0)
    caps = DDSCAPS_TEXTURE | (DDSCAPS_MIPMAP | DDSCAPS_COMPLEX if inf.mips > 1 else 0)
    caps2 = 0
    if inf.faces == 6:
        caps |= DDSCAPS_COMPLEX
        caps2 = DDSCAPS2_CUBEMAP_ALL
    head = struct.pack('<7I', 124, flags, inf.h, inf.w, pitch, 0, inf.mips) + b'\0' * 44 + pf
    head += struct.pack('<4I4x', caps, caps2, 0, 0)
    assert len(head) == 124
    return b'DDS ' + head + ext + body


def read_dds(data):
    """-> (fmt, w, h, mips, faces, [[level bytes] per face], srgb or None). Uncompressed DDS files in other
    channel orders are converted to RGBA8."""
    if data[:4] != b'DDS ':
        raise RasterError('not a DDS file')
    size, flags, h, w, pitch, depth, mips = struct.unpack_from('<7I', data, 4)
    pf_flags, fourcc, bits, rm, gm, bm, am = struct.unpack_from('<I4s5I', data, 4 + 76)
    caps, caps2 = struct.unpack_from('<II', data, 4 + 104)
    mips = max(1, mips if flags & DDSD_MIPMAPCOUNT else 1)
    faces = 6 if caps2 & 0x200 else 1
    pos = 128
    srgb = None
    convert_px = None
    if pf_flags & DDPF_FOURCC and fourcc == b'DX10':
        dxgi, dim, misc, arr, _ = struct.unpack_from('<5I', data, 128)
        pos += 20
        if dxgi not in DXGI:
            raise RasterError(f'DDS format DXGI {dxgi} is not supported (use BC1, BC2, BC3, RGBA8 or BGRA8)')
        fmt = DXGI[dxgi]
        srgb = dxgi in (72, 75, 78, 29, 91)
        if misc & 4:
            faces = 6
    elif pf_flags & DDPF_FOURCC:
        inv = {v: k for k, v in FOURCC.items()}
        inv[b'BC4U'], inv[b'BC5U'] = BC4, BC5
        if fourcc in inv:
            fmt = inv[fourcc]
        elif struct.unpack('<I', fourcc)[0] == 113:
            fmt = RGBA16F
        else:
            raise RasterError(f'DDS FourCC {fourcc!r} is not supported (use DXT1, DXT3, DXT5 or uncompressed)')
    elif pf_flags & (DDPF_RGB | DDPF_LUMINANCE):
        if bits == 32 and (rm, gm, bm) == (0xFF, 0xFF00, 0xFF0000):
            fmt = RGBA8
            if not (pf_flags & DDPF_ALPHAPIXELS) or not am:
                convert_px = ('rgbx',)
        elif bits == 32 and (rm, gm, bm) == (0xFF0000, 0xFF00, 0xFF):
            fmt = BGRA8
            if not (pf_flags & DDPF_ALPHAPIXELS) or not am:
                convert_px = ('bgrx',)
        elif bits == 24:
            fmt = RGBA8
            convert_px = ('rgb24', (rm, gm, bm))
        elif bits == 8:
            fmt = R8
        else:
            raise RasterError(f'DDS {bits}-bit RGB layout is not supported')
    else:
        raise RasterError('DDS pixel format is not supported')
    out = []
    for _ in range(faces):
        lv = []
        for (lw, lh) in mip_dims(w, h, mips):
            bpp = 3 if convert_px and convert_px[0] == 'rgb24' else None
            n = lw * lh * bpp if bpp else level_size(fmt, lw, lh)
            if pos + n > len(data):
                raise RasterError('DDS file is truncated')
            chunk = data[pos:pos + n]
            pos += n
            if convert_px:
                if convert_px[0] == 'rgb24':
                    px = np.frombuffer(chunk, np.uint8).reshape(lh, lw, 3)
                    if convert_px[1][0] == 0xFF0000:
                        px = px[:, :, ::-1]
                    px = np.concatenate([px, np.full((lh, lw, 1), 255, np.uint8)], -1)
                else:
                    px = np.frombuffer(chunk, np.uint8).reshape(lh, lw, 4).copy()
                    px[:, :, 3] = 255
                chunk = px.tobytes()
            lv.append(chunk)
        out.append(lv)
    return fmt, w, h, mips, faces, out, srgb


def from_dds(data, platform, template=None, srgb=None):
    """DDS file -> (chunk 0, pixel chunk). Block-compressed data is stored without re-encoding (PC and PS3).
    sRGB: from a DX10 DDS header, else `srgb`, else the template header's flag, else on."""
    fmt, w, h, mips, faces, levels, dds_srgb = read_dds(data)
    if dds_srgb is not None:
        srgb = dds_srgb
    elif srgb is None:
        srgb = info_srgb(template, platform) if template is not None else True
    if storable(fmt, platform):
        packed = []
        for lv in levels:
            if fmt in BLOCK:
                packed.append(list(lv))
            else:
                inf = Info('PC', fmt, 0, w, h, mips, 1, srgb)
                packed.append([encode_level(decode_level(inf, lw, lh, d), fmt, platform)
                               for d, (lw, lh) in zip(lv, mip_dims(w, h, mips))])
        return _finish(platform, fmt, w, h, mips, faces, srgb, packed, template)
    inf = Info('PC', fmt, 0, w, h, mips, 1, srgb)
    images = [decode_level(inf, w, h, lv[0]) for lv in levels]
    return make(platform, images, fmt, srgb, mips, template)


def info_srgb(template, platform):
    if platform == 'PC':
        return bool(struct.unpack_from('<I', template, 0x20)[0] & PC_FLAG_SRGB)
    return bool(struct.unpack_from('>I', template, 0x20)[0] & 0x20)
