"""Game folders of a platform, for bundles opened from outside the game (e.g. a PS3 car bundle in Downloads): the
shaders, global materials / textures and stream files are then taken from a pinned game folder of the same
platform."""
import os
import struct

MARKERS = ('GLOBALEFFECTS.BNDL', 'NFS13.exe', 'SHADERS.BNDL')
FALLBACK = {}          # platform ('PC' / 'PS3') -> game folder


def game_root(path):
    cur = os.path.dirname(os.path.abspath(path or '.'))
    for _ in range(8):
        if any(os.path.exists(os.path.join(cur, n)) for n in MARKERS):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def platform_of(root):
    """'PC' / 'PS3' of a game folder (from the header of one of its global bundles)."""
    for n in ('SHADERS.BNDL', 'GLOBALEFFECTS.BNDL'):
        p = os.path.join(root, n)
        try:
            with open(p, 'rb') as f:
                h = f.read(8)
        except OSError:
            continue
        if h[:4] == b'bnd2':
            plat = struct.unpack('>H', h[6:8])[0] if h[4] == 0 else struct.unpack('<H', h[6:8])[0]
            return {1: 'PC', 2: 'PS3'}.get(plat)
    return None


def set_fallbacks(folders):
    """Remember the first game folder of each platform among `folders` (the pinned folders)."""
    FALLBACK.clear()
    for f in folders:
        r = f if any(os.path.exists(os.path.join(f, n)) for n in MARKERS) else game_root(os.path.join(f, 'x'))
        if r:
            plat = platform_of(r)
            if plat and plat not in FALLBACK:
                FALLBACK[plat] = r


def root_for(path, platform=None):
    """The game folder of a bundle; for a bundle outside any game folder, the pinned one of its platform."""
    r = game_root(path)
    if r is None and platform:
        r = FALLBACK.get(platform)
    return r
