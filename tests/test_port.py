"""Tests of the Genesys layout fix and of porting a bundle into a game bundle (bndlx/port.py).

Runs without game files (synthetic bundles). With a real PC bundle that holds Genesys data it also round-trips it
through a PS3 stand-in:

  set BNDLX_SAMPLE=...\\ENVIRONMENT.BNDL
  python tests\\test_port.py
"""
import os
import struct
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('APPDATA', tempfile.mkdtemp(prefix='bndlx_appdata_'))   # scan cache goes here, not to the user
from bndlx import colourcube, convert, genesys, port, raster, textfile  # noqa: E402
from bndlx.bundle import Bundle  # noqa: E402
from bndlx.restypes import T_CUBE, T_TEXT, T_TEXTURE  # noqa: E402

TMP = tempfile.mkdtemp(prefix='bndlx_port_')
failures = []


def check(cond, what):
    print(('ok    ' if cond else 'FAIL  ') + what)
    if not cond:
        failures.append(what)


# ---------------------------------------------------------------------------------------------------------------
# Genesys layout
# ---------------------------------------------------------------------------------------------------------------
def test_layout():
    # a declared uint16 sitting on an element of a pointer array: the declared field wins, whatever the order
    for first_weak in (True, False):
        L = genesys.Layout(16)
        if first_weak:
            L.scalar(8, 4, weak=True)
            L.scalar(8, 2)
        else:
            L.scalar(8, 2)
            L.scalar(8, 4, weak=True)
        check(L.conflicts == 0 and L.scalars.get(8) == 2, f'declared field beats a weak array element (weak first: {first_weak})')
    L = genesys.Layout(16)
    L.scalar(8, 4)
    L.scalar(8, 2)
    check(L.conflicts == 1, 'two declared fields that overlap are still a conflict')
    L = genesys.Layout(16)
    L.scalar(0, 4, weak=True)
    L.scalar(4, 4)
    check(L.conflicts == 0 and L.scalars == {0: 4, 4: 4}, 'weak elements that overlap nothing are kept')


def words(values, e):
    return b''.join(struct.pack(e + 'f', v) for v in values)


def test_float_guess():
    for e in '<>':
        data = words([0.5, 1.0, 0.25, 0.75, 0.3, 0.125], e)
        L = genesys.Layout(len(data))
        check(L.float_words(data, e) == [0, 4, 8, 12, 16, 20], f'a run of sane floats is recognised ({e})')
        L = genesys.Layout(len(data))
        ints = b''.join(struct.pack(e + 'I', v) for v in (1, 2, 3, 4, 5, 6))
        check(L.float_words(ints, e) is None, f'small integers are not taken for floats ({e})')
        rng = np.random.default_rng(7)
        h = b''.join(struct.pack(e + 'I', int(v)) for v in rng.integers(0, 2 ** 32, 12))
        check(genesys.Layout(len(h)).float_words(h, e) is None, f'random words (hashes) are not taken for floats ({e})')
        short = words([0.5, 1.0, 0.25], e)
        check(genesys.Layout(len(short)).float_words(short, e) is None, f'a run shorter than 4 words is refused ({e})')


# ---------------------------------------------------------------------------------------------------------------
# porting (synthetic bundles)
# ---------------------------------------------------------------------------------------------------------------
def make_cube(seed):
    rng = np.random.default_rng(seed)
    texels = rng.integers(0, 256, colourcube.TEXELS * 3, dtype=np.uint8).tobytes()
    return struct.pack('<II8x', 16, 16) + texels + b'\0' * (12304 - 16 - len(texels))


def make_pc(seed):
    """A small PC bundle: text, colour cube, 2D texture, a material-like resource, plus one resource of its own."""
    b = Bundle('PC')
    b.flags = 1
    img = np.random.default_rng(seed).integers(0, 256, (16, 16, 4), dtype=np.uint8)
    hdr, pix = raster.make('PC', [img], raster.RGBA8, False, mips=1)
    b.resources.append(b.new_resource(0x10, T_TEXT, [textfile.build(f'text {seed}'.encode(), '<'), b'', b'', b'']))
    b.resources.append(b.new_resource(0x20, T_CUBE, [make_cube(seed), b'', b'', b'']))
    b.resources.append(b.new_resource(0x30, T_TEXTURE, [hdr, pix, b'', b'']))
    b.resources.append(b.new_resource(0x40, 0x002, [bytes([seed]) * 64, b'', b'', b'']))      # Material: not convertible
    return b


def test_port_synthetic():
    game = make_pc(1)
    game.resources.append(game.new_resource(0x50, T_TEXT, [textfile.build(b'only in the game', '<'), b'', b'', b'']))
    gpath = os.path.join(TMP, 'game', 'ENV.BNDL')
    os.makedirs(os.path.dirname(gpath))
    game.save(gpath)
    dst = Bundle.open(gpath)

    pc_src = make_pc(2)
    pc_src.resources.append(pc_src.new_resource(0x60, T_TEXT, [textfile.build(b'only in the source', '<'), b'', b'', b'']))
    ps3, rep = convert.convert_bundle(pc_src, 'PS3')
    spath = os.path.join(TMP, 'ps3', 'ENV.BNDL')
    os.makedirs(os.path.dirname(spath))
    ps3.save(spath)
    src = Bundle.open(spath)
    check(src.platform == 'PS3' and len(src.resources) == 4, 'stand-in PS3 source built (material left out by Convert)')

    gs = port.GameScan.scan(os.path.dirname(gpath))
    check(gs.bundles == 1 and set(gs.ids) == {0x10, 0x20, 0x30, 0x40, 0x50}, 'game scan indexes every resource id')
    gs2 = port.GameScan.scan(os.path.dirname(gpath))
    check(gs2.cached and gs2.ids == gs.ids, 'second scan comes from the cache')

    res = port.port_bundle(src, dst, genesys.TypeDB(), gs)
    out = res.bundle
    check([(r.id, r.type) for r in out.resources] == [(r.id, r.type) for r in dst.resources],
          'table identical to the game\'s: same ids, types, order and count')
    check(0x60 not in {r.id for r in out.resources}, 'source-only resource is not added')
    by = {r.id: r for r in out.resources}
    s_by = {r.id: r for r in pc_src.resources}
    g_by = {r.id: r for r in dst.resources}
    for rid, nm in ((0x10, 'TextFile'), (0x20, 'ColourCube'), (0x30, 'Texture')):
        check(by[rid].chunks() == s_by[rid].chunks(), f'{nm} comes from the source (converted back to PC it equals it)')
    check(by[0x40].chunks() == g_by[0x40].chunks(), 'Material (platform-specific) is the game\'s own')
    check(by[0x50].chunks() == g_by[0x50].chunks(), 'resource only the game has is the game\'s own')
    check(sorted(d.id for d in res.replaced) == [0x10, 0x20, 0x30] and [d.id for d in res.source_only] == [0x60],
          'decisions list what was replaced and what is source-only')
    opath = os.path.join(TMP, 'OUT.BNDL')
    out.save(opath)
    again = Bundle.open(opath)
    again.save(opath + '.2')
    with open(opath, 'rb') as f1, open(opath + '.2', 'rb') as f2:
        check(f1.read() == f2.read(), 'the ported file re-saves byte-identical')
    check(bool(res.text()) and 'nothing was added' in res.text(), 'report text is produced')

    try:
        port.port_bundle(dst, dst, genesys.TypeDB(), gs)
        check(False, 'same-platform port is refused')
    except port.PortError:
        check(True, 'same-platform port is refused')

    # a cube map must not replace a 2D texture
    img = np.random.default_rng(3).integers(0, 256, (16, 16, 4), dtype=np.uint8)
    hdr, pix = raster.make('PC', [img] * 6, raster.RGBA8, False, mips=1)
    cube_src = make_pc(2)
    cube_src.resources[2] = cube_src.new_resource(0x30, T_TEXTURE, [hdr, pix, b'', b''])
    ps3c, _ = convert.convert_bundle(cube_src, 'PS3')
    ps3c.save(os.path.join(TMP, 'ps3', 'CUBE.BNDL'))
    res2 = port.port_bundle(Bundle.open(os.path.join(TMP, 'ps3', 'CUBE.BNDL')), dst, genesys.TypeDB(), gs)
    d30 = next(d for d in res2.decisions if d.id == 0x30)
    check(d30.action == 'keep' and 'cube' in d30.reason, 'a cube map does not replace a 2D texture')


# ---------------------------------------------------------------------------------------------------------------
# optional: a real PC bundle with Genesys data, through a PS3 stand-in
# ---------------------------------------------------------------------------------------------------------------
def test_sample(path):
    pc = Bundle.open(path)
    pdir = os.path.join(TMP, 'real_pc')
    sdir = os.path.join(TMP, 'real_ps3')
    os.makedirs(pdir)
    os.makedirs(sdir)
    pc.save(os.path.join(pdir, os.path.basename(path)))
    dst = Bundle.open(os.path.join(pdir, os.path.basename(path)))
    types = genesys.TypeDB()
    types.add_bundle(dst)
    ps3b, rep = convert.convert_bundle(dst, 'PS3', types, lenient=True)
    sp = os.path.join(sdir, os.path.basename(path))
    ps3b.save(sp)
    src = Bundle.open(sp)
    st = genesys.TypeDB()
    st.add_bundle(src)
    gs = port.GameScan.scan(pdir)
    res = port.port_bundle(src, dst, st, gs, lenient=True)
    out = res.bundle
    check([(r.id, r.type) for r in out.resources] == [(r.id, r.type) for r in dst.resources],
          'real bundle: table identical to the game\'s')
    check(all(a.chunks() == b.chunks() for a, b in zip(out.resources, dst.resources)),
          'real bundle: PC -> PS3 -> ported back gives the original data for every resource')
    kinds = {d.type for d in res.kept}
    check(genesys.T_TYPE in kinds, 'real bundle: Genesys types are kept as the game has them')
    if any(r.type == genesys.T_OBJECT for r in dst.resources):
        s = genesys.TypeDB()
        s.add_bundle(src)
        sky = next((t for t in s.types.values() if t.name.endswith('.Sky')), None)
        if sky is not None:
            sky.size += 16
            r2 = port.port_bundle(src, dst, s, gs, lenient=True)
            check(len(r2.replaced) < len(res.replaced), 'real bundle: objects whose schema differs from the game\'s are kept')


if __name__ == '__main__':
    test_layout()
    test_float_guess()
    test_port_synthetic()
    sample = os.environ.get('BNDLX_SAMPLE')
    if sample and os.path.isfile(sample):
        test_sample(sample)
    else:
        print('(set BNDLX_SAMPLE to a PC bundle with Genesys data to run the real-file test)')
    print()
    print(f'{len(failures)} failure(s)' if failures else 'all tests passed')
    sys.exit(1 if failures else 0)
