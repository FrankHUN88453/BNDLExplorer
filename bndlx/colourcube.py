"""Colour cubes (resource type 0x52): the 16x16x16 colour-grading LUTs of the environment and post-fx.

Resource layout (chunk 0, 12304 bytes): u32 width, u32 height (16, 16; platform byte order), 8 bytes zero,
then 16^3 texels of 3 bytes.
  PC  : linear, index = b*256 + g*16 + r, texel bytes B, G, R
  PS3 : 3D Morton order (bit 3k = r, 3k+1 = g, 3k+2 = b), texel bytes R, G, B
Verified on the eight POSTFX cubes that exist on both platforms: converted PS3 data is byte-identical.
"""
import struct

import numpy as np

SIZE = 16
TEXELS = SIZE ** 3


def _morton3(x, y, z):
    i = 0
    for bit in range(4):
        i |= ((x >> bit) & 1) << (3 * bit) | ((y >> bit) & 1) << (3 * bit + 1) | ((z >> bit) & 1) << (3 * bit + 2)
    return i


_r, _g, _b = np.meshgrid(np.arange(SIZE), np.arange(SIZE), np.arange(SIZE), indexing='ij')
PS3_INDEX = np.vectorize(_morton3)(_r, _g, _b)           # [r, g, b] -> PS3 texel index
PC_INDEX = _b * 256 + _g * 16 + _r                         # [r, g, b] -> PC texel index


def ps3_to_lut(chunk):
    """PS3 chunk 0 -> float LUT [r, g, b, 3] (RGB, 0..255)."""
    rgb = np.frombuffer(chunk[16:16 + TEXELS * 3], np.uint8).reshape(-1, 3)
    return rgb[PS3_INDEX].astype(np.float32)


def pc_to_lut(chunk):
    bgr = np.frombuffer(chunk[16:16 + TEXELS * 3], np.uint8).reshape(-1, 3)
    return bgr[PC_INDEX][..., ::-1].astype(np.float32)


def lut_to_pc(lut, like=None):
    """Float LUT [r, g, b, 3] -> PC chunk 0 (header taken from `like` if given)."""
    out = np.zeros((TEXELS, 3), np.uint8)
    out[PC_INDEX.ravel()] = np.clip(np.rint(lut.reshape(-1, 3)[:, ::-1]), 0, 255).astype(np.uint8)
    header = like[:16] if like is not None else struct.pack('<II8x', SIZE, SIZE)
    return header + out.tobytes()


def apply(lut, image):
    """Grade an RGB uint8 image (H, W, 3) with a LUT (trilinear, like the GPU)."""
    x = image.astype(np.float32) / 255.0 * (SIZE - 1)
    i0 = np.floor(x).astype(int).clip(0, SIZE - 2)
    f = x - i0
    out = np.zeros_like(x)
    for dr in (0, 1):
        for dg in (0, 1):
            for db in (0, 1):
                w = ((f[..., 0] if dr else 1 - f[..., 0]) * (f[..., 1] if dg else 1 - f[..., 1])
                     * (f[..., 2] if db else 1 - f[..., 2]))
                out += w[..., None] * lut[i0[..., 0] + dr, i0[..., 1] + dg, i0[..., 2] + db]
    return np.clip(out, 0, 255).astype(np.uint8)


def identity():
    return np.stack([_r, _g, _b], -1).astype(np.float32) * 17.0


def lut_to_ps3(lut, like=None):
    """Float LUT [r, g, b, 3] -> PS3 chunk 0 (header taken from `like` if given)."""
    out = np.zeros((TEXELS, 3), np.uint8)
    out[PS3_INDEX.ravel()] = np.clip(np.rint(lut.reshape(-1, 3)), 0, 255).astype(np.uint8)
    header = like[:16] if like is not None else struct.pack('>II8x', SIZE, SIZE)
    return header + out.tobytes()
