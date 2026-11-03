"""The game's UI fonts (resource type 0x30, UI/FONTS/<font id>.BNDL; the Font resource's id is crc32(str(font id)),
which is how a TextStyle's Font_Asset finds it): read them, lay text out like the game, build new ones from TTF.

Font (little-endian on PC):
  0x00 u32 version (10), u32 data size (= end of the glyph table)
  0x08 f32 sx, f32 sy        em per atlas unit: x metrics (u, w, x offset, advance) * sx and y metrics (v, h, y
                             offset) * sy are in em; a TextStyle's Font_Size is the em in pixels
  0x10 u32 0, f32 ascent (em, line top to baseline), f32 (~0.78)
  0x1C u32 glyph slots n, u32 glyph table offset, u32 code table offset
  0x28 u16[129] hash buckets: the slots of codepoint c are bucket[c & 0x7F] .. bucket[(c & 0x7F) + 1] - 1
       (slots sorted by (c & 0x7F, c)); then u32 0, u32 1, u32 0x250 (where the atlas handle is), name and
       style strings (char[] at 0x14C and 0x1CC, e.g. '1206237', 'Regular')
  0x250 handle: the atlas texture (glyphs in its alpha; letters white, button icons in colour)
  code table: u16 codepoint per slot; glyph table: n x {f32 u, v, w, h (atlas 0..1), f32 x offset, y offset (from
       the line top), advance, u16 scale x 1000 (icons are drawn bigger), u16 kind (2 glyph, 3 icon, 4 empty)}
"""
import struct
import zlib

import numpy as np

T_FONT = 0x30
NAME_AT = 0x14C
STYLE_AT = 0x1CC
HANDLE_AT = 0x250


class FontError(ValueError):
    pass


class Glyph:
    __slots__ = ('code', 'u', 'v', 'w', 'h', 'xo', 'yo', 'adv', 'scale', 'kind')


def drawn(g):
    """A glyph with a picture (empty slots have u = v = -1; glyphs at the atlas edge may start a pixel before 0)."""
    return g is not None and g.u > -0.5 and g.v > -0.5 and g.w > 0 and g.h > 0


class Font:
    def __init__(self, data, e='<'):
        d = bytes(data)
        if len(d) < 0x260:
            raise FontError('too short for a font')
        self.data = d
        self.e = e
        (self.version, self.size, self.sx, self.sy, _z, self.ascent, self.f18, n, go, co) = struct.unpack_from(
            e + '2I2fI2f3I', d, 0)
        if self.version != 10 or go + 32 * n > len(d) or co + 2 * n > len(d):
            raise FontError(f'font version {self.version} / tables out of range')
        self.buckets = struct.unpack_from(e + '129H', d, 0x28)
        codes = struct.unpack_from(e + f'{n}H', d, co)
        self.slots = []
        self.glyphs = {}
        for i in range(n):
            g = Glyph()
            g.code = codes[i]
            g.u, g.v, g.w, g.h, g.xo, g.yo, g.adv = struct.unpack_from(e + '7f', d, go + 32 * i)
            g.scale, g.kind = struct.unpack_from(e + '2H', d, go + 32 * i + 28)
            self.slots.append(g)
            old = self.glyphs.get(g.code)
            if old is None or (not drawn(old) and drawn(g)):  # a drawn glyph over an empty slot of the same code
                self.glyphs[g.code] = g
        self.name = self._text(NAME_AT)
        self.style = self._text(STYLE_AT)

    def _text(self, off):
        raw = self.data[off:off + 64]
        return raw.split(b'\0', 1)[0].decode('latin1', 'replace')

    def glyph(self, ch):
        return self.glyphs.get(ord(ch)) if isinstance(ch, str) else self.glyphs.get(ch)

    def layout(self, text, size, spacing=0.0, line_spacing=0.0):
        """Glyph quads of the text at `size` px per em: ([(x, y, w, h, u0, v0, u1, v1, kind)], width, height),
        x / y from the top-left of the text (line top). Unknown characters are left out."""
        quads = []
        x = y = 0.0
        width = 0.0
        line_h = size * (self.ascent + 0.25)
        for ch in text:
            if ch == '\n':
                width = max(width, x)
                x = 0.0
                y += line_h + line_spacing
                continue
            g = self.glyph(ch)
            if g is None:
                g = self.glyph('?')
                if g is None:
                    continue
            k = (g.scale or 1000) / 1000.0 if g.kind == 3 else 1.0
            if drawn(g):
                gx = x + g.xo * self.sx * size
                gy = y + g.yo * self.sy * size
                quads.append((gx, gy, g.w * self.sx * size * k, g.h * self.sy * size * k,
                              g.u, g.v, g.u + g.w, g.v + g.h, g.kind))
            x += g.adv * self.sx * size * k + spacing
        width = max(width, x - spacing if text else 0.0)
        return quads, width, y + line_h

    def height(self, size):
        return size * (self.ascent + 0.25)


def font_resource_id(font_id):
    """The Font resource of font id N is crc32(str(N)) (the bundle is UI/FONTS/N.BNDL)."""
    return zlib.crc32(str(font_id).encode('ascii'))


def from_bundle(b):
    """(Font, atlas texture resource) of a font bundle."""
    fr = next((r for r in b.resources if r.type == T_FONT), None)
    if fr is None:
        raise FontError('no font in this bundle')
    font = Font(fr.data(0), b.e)
    tid = next((i.id for i in fr.imports() if (i.offset & 0x7FFFFFFF) == HANDLE_AT), None)
    tex = b.find(tid) if tid is not None else None
    return font, fr, tex


# -- building ---------------------------------------------------------------------------------------------------
def _pack(sizes, width, pad=2):
    """Shelf packing: [(x, y)] per (w, h) and the height used, or None if it does not fit."""
    order = sorted(range(len(sizes)), key=lambda i: -sizes[i][1])
    pos = [None] * len(sizes)
    x = y = shelf = 0
    for i in order:
        w, h = sizes[i]
        if w + pad > width:
            return None, 0
        if x + w + pad > width:
            x = 0
            y += shelf + pad
            shelf = 0
        pos[i] = (x + pad, y + pad)
        x += w + pad
        shelf = max(shelf, h)
    return pos, y + shelf + 2 * pad


EXTRA = ''.join(chr(c) for c in range(0xA0, 0x180))     # Latin-1 Supplement + Latin Extended-A (ő ű ...)


def build(base, atlas, ttf_path, name, em_px=40, size=1024, style='Regular', keep_base=False, extra=EXTRA):
    """A new font from a TrueType / OpenType file, with the base font's characters plus `extra` ones the TTF has.
    keep_base: the base font's letters stay (only the characters it lacks are drawn from the TTF, e.g. ő ű);
    otherwise the letters are drawn from the TTF. Button icons, and characters the TTF lacks, are copied from the
    base, scaled to the new em. base: Font; atlas: the base atlas (H, W, 4) uint8.
    -> (font data bytes, RGBA atlas, characters drawn from the TTF)."""
    from PIL import Image, ImageDraw, ImageFont
    if keep_base:
        em_px = max(8, int(round(atlas.shape[1] / base.sx)))         # the base's own pixels per em
    tt = ImageFont.truetype(ttf_path, em_px)
    asc_px, desc_px = tt.getmetrics()
    em_old_x = 1.0 / base.sx * atlas.shape[1] if base.sx else em_px      # base atlas px per em (x)
    em_old_y = 1.0 / base.sy * atlas.shape[0] if base.sy else em_px
    missing = _missing_probe(tt)
    items = []                   # (slot glyph, image (h, w, 4) or None, metrics in new px)
    n_drawn = 0
    slots = list(base.slots)
    have = {g.code for g in slots if g.kind in (2, 3) and drawn(g)}
    for ch in extra or '':
        c = ord(ch)
        if c not in have and c <= 0xFFFF and _has_glyph(tt, ch, missing):
            slots = [g for g in slots if g.code != c]               # an empty slot of it is replaced
            g = Glyph()
            g.code, g.u, g.v, g.w, g.h, g.xo, g.yo, g.adv, g.scale, g.kind = c, -1, -1, 0, 0, 0, 0, 0, 1000, 2
            g.adv = -1.0                                               # marks a character to draw
            slots.append(g)
            have.add(c)
    for g in slots:
        ch = chr(g.code)
        img = None
        new = g.adv == -1.0
        if new or (g.kind == 2 and not keep_base and not 0xE000 <= g.code <= 0xF8FF and _has_glyph(tt, ch, missing)):
            box = tt.getbbox(ch, anchor='la')
            w, h = max(1, box[2] - box[0]), max(1, box[3] - box[1])
            im = Image.new('L', (w + 2, h + 2), 0)
            ImageDraw.Draw(im).text((1 - box[0], 1 - box[1]), ch, font=tt, fill=255, anchor='la')
            a = np.asarray(im)
            img = np.dstack([np.full_like(a, 255)] * 3 + [a])
            m = (box[0] - 1, box[1] - 1, tt.getlength(ch))
            n_drawn += 1
        elif drawn(g):                                                  # copy from the base atlas, rescaled
            H, W = atlas.shape[:2]
            x0, y0 = max(0, int(round(g.u * W))), max(0, int(round(g.v * H)))
            x1, y1 = int(round((g.u + g.w) * W)), int(round((g.v + g.h) * H))
            crop = atlas[y0:max(y1, y0 + 1), x0:max(x1, x0 + 1)]
            kx, ky = em_px / em_old_x, em_px / em_old_y
            nw, nh = max(1, int(round(crop.shape[1] * kx))), max(1, int(round(crop.shape[0] * ky)))
            img = np.asarray(Image.fromarray(crop).resize((nw, nh), Image.LANCZOS))
            m = (g.xo * W * kx, g.yo * H * ky, g.adv * W * kx)
        elif ch.isspace() and not keep_base and _has_glyph(tt, ch, missing):
            m = (0.0, 0.0, tt.getlength(ch))
        else:
            m = (0.0, 0.0, g.adv * base.sx * em_px)
        items.append((g, img, m))
    sizes = [(it[1].shape[1], it[1].shape[0]) if it[1] is not None else (0, 0) for it in items]
    for width in (size, size * 2):
        imgs = [s for s in sizes if s[0]]
        pos, used = _pack([s for s in imgs], width)
        if pos is not None and used <= width:
            break
    else:
        raise FontError('the glyphs do not fit a 2048 x 2048 atlas: use a smaller em size')
    AW = width
    AH = 1 << max(4, int(np.ceil(np.log2(max(used, 16)))))
    out = np.zeros((AH, AW, 4), np.uint8)
    out[..., :3] = 255
    k = 0
    recs = []
    for g, img, m in items:
        if img is None:
            recs.append((g.code, -1.0, -1.0, 8.0 / AW if g.kind == 4 else 0.0, 0.0, 0.0, 0.0, m[2] / AW, 0, 4))
            continue
        x, y = pos[k]
        k += 1
        h, w = img.shape[:2]
        out[y:y + h, x:x + w] = img
        recs.append((g.code, x / AW, y / AH, w / AW, h / AH, m[0] / AW, m[1] / AH, m[2] / AW,
                     g.scale if g.kind == 3 else 1000, g.kind if g.kind in (2, 3) else 2))
    ascent = base.ascent if keep_base else asc_px / em_px
    return pack_font(base, recs, AW / em_px, AH / em_px, ascent, name, style), out, n_drawn


def _missing_probe(tt):
    """What the TTF draws for a character it does not have (its .notdef box)."""
    try:
        m = tt.getmask('􏿽')
        return bytes(m), m.size, tt.getlength('􏿽')
    except Exception:
        return None


def _has_glyph(tt, ch, missing):
    try:
        m = tt.getmask(ch)
        if missing is not None and (bytes(m), m.size, tt.getlength(ch)) == missing:
            return False
        return m.size[0] > 0 or ch.isspace()
    except Exception:
        return False


def make_bundle(base_bundle, font_id, data, atlas):
    """A font bundle (bytes) for font id `font_id` from a base font bundle: the Font resource crc32(str(id)) with
    `data`, its atlas (a new id) encoded like the base atlas."""
    from . import ops
    b = base_bundle
    font, fr, tex = from_bundle(b)
    if tex is None:
        raise FontError('the base font has no atlas texture')
    new_fid = font_resource_id(font_id)
    new_tid = zlib.crc32(f'{font_id}_fontatlas'.encode('ascii'))
    imps = bytes(fr.data(0)[fr.import_offset:fr.import_offset + 16 * fr.import_count])
    if tex.id != new_tid:
        b.change_id(tex.id, new_tid, update_imports=True)
    if fr.id != new_fid:
        b.change_id(fr.id, new_fid, update_imports=True)
    fr = b.find(new_fid)
    tex = b.find(new_tid)
    table = bytearray(imps)
    for k in range(len(table) // 16):                 # the atlas handle points at the new atlas id
        off = struct.unpack_from(b.e + 'I', table, 16 * k + 8)[0]
        if (off & 0x7FFFFFFF) == HANDLE_AT:
            struct.pack_into(b.e + 'Q', table, 16 * k, new_tid)
    fr.set_data(0, bytes(data) + bytes(table))
    fr.import_offset, fr.import_count = len(data), len(table) // 16
    ops.replace_texture(b, tex, image=atlas)
    return b.to_bytes()


def pack_font(base, recs, sx, sy, ascent, name, style):
    """Font data from glyph records [(code, u, v, w, h, xo, yo, adv, scale, kind)] (any order)."""
    e = base.e
    recs = sorted(recs, key=lambda r: (r[0] & 0x7F, r[0]))
    n = len(recs)
    buckets = [0] * 129
    for r in recs:
        buckets[(r[0] & 0x7F) + 1] += 1
    for i in range(1, 129):
        buckets[i] += buckets[i - 1]
    head = bytearray(base.data[:0x260])           # keeps the fields between the buckets and the handle
    co = 0x260
    go = ((co + 2 * n + 31) // 32) * 32            # the glyph table is 32-aligned
    end = go + 32 * n
    struct.pack_into(e + '2I2fI2f3I', head, 0, 10, end, sx, sy, 0, ascent, base.f18, n, go, co)
    struct.pack_into(e + '129H', head, 0x28, *buckets)
    for off, text in ((NAME_AT, name), (STYLE_AT, style)):
        raw = text.encode('latin1', 'replace')[:63]
        head[off:off + 64] = raw + b'\0' * (64 - len(raw))
    head[HANDLE_AT:HANDLE_AT + 16] = b'\0' * 16
    body = bytearray(end - 0x260)
    struct.pack_into(e + f'{n}H', body, 0, *[r[0] for r in recs])
    for i, r in enumerate(recs):
        struct.pack_into(e + '7f2H', body, go - 0x260 + 32 * i, *r[1:8], int(r[8]), int(r[9]))
    data = bytes(head) + bytes(body)
    return data + b'\0' * ((-len(data)) % 16)
