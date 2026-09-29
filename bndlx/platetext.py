"""License plate texts of the car packs (PC NFS13.exe).

The number plate of a car is 8 letter quads whose UVs point at the cells of a font atlas (VEHICLETEX texture
0x0100000000138805: 9 x 4 cells A-Z, '-', 1-9); the letters are chosen at run time from an 8-character text. The
texts per content pack are a table of five 12-byte slots (8 characters, NUL padded) that the compiler copied into
.rdata about fifty times: NEED4SPD (base game), ULTIMATE, VELOCITY, MOVILGND (Movie Legends), NFS HERO (NFS
Heroes). Changing every copy changes the plates; NFS13.exe is kept once as NFS13.exe.orig."""
import os
import shutil
import struct

import numpy as np

PACKS = [('Base game', b'NEED4SPD'), ('Ultimate Speed pack', b'ULTIMATE'), ('Terminal Velocity pack', b'VELOCITY'),
         ('Movie Legends pack', b'MOVILGND'), ('NFS Heroes pack', b'NFS HERO')]
SLOT = 12
LETTERS = 8
ATLAS_ID = 0x0100000000138805
GLYPHS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ-123456789'      # atlas cells, row by row (9 per row)


class PlateError(Exception):
    pass


def exe_path(root):
    return os.path.join(root, 'NFS13.exe')


def _rdata(d):
    pe = struct.unpack_from('<I', d, 0x3C)[0]
    nsec = struct.unpack_from('<H', d, pe + 6)[0]
    opt = struct.unpack_from('<H', d, pe + 20)[0]
    for i in range(nsec):
        o = pe + 24 + opt + 40 * i
        if d[o:o + 8].rstrip(b'\0') == b'.rdata':
            vsize, va, rsize, raw = struct.unpack_from('<4I', d, o + 8)
            return raw, raw + rsize
    raise PlateError('no .rdata section in the exe')


def _slots(d, text):
    """Offsets of the 12-byte slots holding `text` in .rdata."""
    lo, hi = _rdata(d)
    key = text + b'\0' * (SLOT - len(text))
    out = []
    i = d.find(key, lo, hi)
    while i >= 0:
        out.append(i)
        i = d.find(key, i + 1, hi)
    return out


def clean(text):
    """What the plate can show: upper case, A-Z 1-9 '-' and space ('0' becomes 'O'), 8 characters, centred."""
    t = ''.join('O' if c == '0' else c for c in text.upper())
    bad = sorted({c for c in t if c != ' ' and c not in GLYPHS})
    if bad:
        raise PlateError('the plate font has no ' + ' '.join(repr(c) for c in bad) + ' (only A-Z, 1-9, - and space)')
    t = t.strip()
    if len(t) > LETTERS:
        raise PlateError(f'"{t}" is longer than {LETTERS} characters')
    pad = LETTERS - len(t)
    return ' ' * (pad // 2) + t + ' ' * (pad - pad // 2)


def locate(root):
    """[(pack name, original text, [offsets])] found in the original exe (NFS13.exe.orig when it exists)."""
    src = exe_path(root) + '.orig' if os.path.isfile(exe_path(root) + '.orig') else exe_path(root)
    with open(src, 'rb') as f:
        d = f.read()
    return [(name, text, _slots(d, text)) for name, text in PACKS]


def current(root):
    """The texts in NFS13.exe now: [(pack name, original text, current text, copies)]."""
    with open(exe_path(root), 'rb') as f:
        d = f.read()
    out = []
    for name, text, offs in locate(root):
        cur = d[offs[0]:offs[0] + LETTERS].decode('latin1') if offs else ''
        out.append((name, text.decode(), cur, len(offs)))
    return out


def write(root, texts):
    """texts: {original text (str): new text}. Patches every copy; NFS13.exe is kept once as .orig.
    Returns the number of slots written."""
    p = exe_path(root)
    new = {orig.encode(): clean(t).encode('latin1') for orig, t in texts.items()}
    places = locate(root)
    if not any(offs for _, _, offs in places):
        raise PlateError('the plate text table was not found in NFS13.exe (another version of the game?)')
    with open(p, 'rb') as f:
        d = bytearray(f.read())
    n = 0
    for name, text, offs in places:
        if text not in new:
            continue
        slot = new[text] + b'\0' * (SLOT - LETTERS)
        for o in offs:
            if d[o:o + SLOT] != slot:
                d[o:o + SLOT] = slot
                n += 1
    if n:
        if not os.path.exists(p + '.orig'):
            shutil.copy2(p, p + '.orig')
        with open(p + '.tmp', 'wb') as f:
            f.write(d)
        os.replace(p + '.tmp', p)
    return n


def restore(root):
    """Put NFS13.exe.orig back (every plate text as the game shipped it)."""
    p = exe_path(root)
    if not os.path.isfile(p + '.orig'):
        raise PlateError('there is no NFS13.exe.orig to restore')
    shutil.copy2(p + '.orig', p)


def preview(atlas, text):
    """RGBA image of a plate text drawn with the game's plate font atlas (RGBA array, 9 x 4 cells)."""
    h, w = atlas.shape[:2]
    cw, ch = w / 9.0, h / 4.0
    cells = []
    bg = atlas[int(ch * 0.02):int(ch * 0.06), int(cw * 0.02):int(cw * 0.06)].reshape(-1, 4).mean(0).astype(np.uint8)
    for c in text:
        if c in GLYPHS:
            i = GLYPHS.index(c)
            x0, y0 = int(round((i % 9) * cw)), int(round((i // 9) * ch))
            cells.append(atlas[y0:y0 + int(ch), x0:x0 + int(cw)])
        else:
            cells.append(np.broadcast_to(bg, (int(ch), int(cw), 4)))
    return np.ascontiguousarray(np.concatenate(cells, 1))
