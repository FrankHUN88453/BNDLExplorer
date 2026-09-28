"""Library tests on real game files (not included). Set the game folders first:

  set BNDLX_PC=...\\Need for Speed(TM) Most Wanted
  set BNDLX_PS3=...\\NPXX00207\\USRDIR\\HAWAII_MAIN
  python tests\\test_core.py
"""
import os
import shutil
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bndlx import convert, genesys, ops, raster, resfile  # noqa: E402
from bndlx.bundle import Bundle  # noqa: E402
from bndlx.localised import StringTable  # noqa: E402

PC = os.environ.get('BNDLX_PC')
PS3 = os.environ.get('BNDLX_PS3')
TMP = tempfile.mkdtemp(prefix='bndlx_test_')
failures = []


def check(cond, what):
    print(('ok    ' if cond else 'FAIL  ') + what)
    if not cond:
        failures.append(what)


def types_of(*bundles):
    t = genesys.TypeDB()
    for b in bundles:
        t.add_bundle(b)
    return t


def test_unchanged_save(path):
    b = Bundle.open(path)
    out = os.path.join(TMP, 'same.BNDL')
    b.save(out)
    with open(path, 'rb') as f1, open(out, 'rb') as f2:
        check(f1.read() == f2.read(), f'unchanged save is byte-identical: {os.path.basename(path)}')


def test_edits(path):
    src = os.path.join(TMP, 'edit.BNDL')
    shutil.copy2(path, src)
    b = Bundle.open(src)
    before = {r.id: r.chunks() for r in b.resources}
    tex = [r for r in b.resources if r.type == 1 and raster.info(r, b.platform).faces == 1]
    t0 = tex[0]
    img = np.zeros((64, 128, 4), np.uint8)
    img[..., 0] = 255
    img[..., 3] = 255
    png = os.path.join(TMP, 'red.png')
    ops.save_png(img, png)
    inf = ops.replace_texture(b, t0, png)
    check(inf.w == 128 and inf.h == 64, f'texture replaced from PNG ({inf.describe()})')
    dup = t0.copy()
    dup.id = 0x7F00000000000001
    b.add(dup)
    gone = tex[1].id
    b.remove(gone)
    bres = os.path.join(TMP, 'x.bres')
    ops.export_bres(b, tex[2], bres)
    b.save(src)
    nb = Bundle.open(src)
    got = nb.find(t0.id)
    dec = raster.decode(got, nb.platform)
    check(dec.shape[:2] == (64, 128) and abs(int(dec[..., 0].mean()) - 255) <= 2, 'replaced texture reads back red')
    check(nb.find(0x7F00000000000001) is not None, 'added resource is there')
    check(nb.find(gone) is None, 'deleted resource is gone')
    same = all(r.chunks() == before[r.id] for r in nb.resources if r.id in before and r.id != t0.id)
    check(same, 'every other resource is unchanged')
    with open(bres, 'rb') as f:
        r, plat = resfile.load(f.read())
    check(plat == nb.platform and r.chunks() == nb.find(tex[2].id).chunks(), '.bres export / load round trip')


def test_change_id(path):
    b = Bundle.open(path)
    obj = next(r for r in b.resources if r.import_count and any(b.find(i.id) for i in r.imports()))
    target = next(i.id for i in obj.imports() if b.find(i.id))
    n = b.change_id(target, 0x7F000000000000AA)
    check(n >= 1 and any(i.id == 0x7F000000000000AA for i in b.find(obj.id).imports()),
          f'change id updates imports ({n})')
    nb = Bundle.from_bytes(b.to_bytes())
    check(nb.find(0x7F000000000000AA) is not None and nb.find(target) is None, 'changed id saved')


def test_convert(path):
    b = Bundle.open(path)
    other = 'PC' if b.platform == 'PS3' else 'PS3'
    out, rep = convert.convert_bundle(b, other, types_of(b))
    nb = Bundle.from_bytes(out.to_bytes())
    back, rep2 = convert.convert_bundle(nb, b.platform, types_of(nb))
    idx = b.index()
    same = sum(1 for r in back.resources if r.chunks() == idx[r.id].chunks())
    check(rep['converted'] > 0 and same >= 0.9 * len(back.resources),
          f'{os.path.basename(path)} {b.platform} -> {other} -> back: {same}/{len(back.resources)} identical, '
          f'{len(rep["failed"])} failed')


def test_strings(path):
    b = Bundle.open(path)
    r = next(r for r in b.resources if r.type == 0x201)
    t = StringTable.read(r, b.e)
    check(t.build(b.e) == r.data(0), f'string table rebuild identical ({len(t.entries)} strings)')
    csv = t.to_csv().replace(f'{t.entries[0][0]:08X},', f'{t.entries[0][0]:08X},HELLO ', 1)
    ch, add, unk = ops.strings_from_csv(b, r, csv)
    t2 = StringTable.read(r, b.e)
    check(ch == 1 and t2.entries[0][1].startswith('HELLO '), 'CSV import changes one string')


def main():
    if PC:
        g = os.path.join(PC, 'GLOBALEFFECTS.BNDL')
        test_unchanged_save(g)
        test_edits(g)
        test_change_id(os.path.join(PC, 'UI', 'SCREENS2', '371621.BNDL'))
        test_convert(os.path.join(PC, 'HAWAII', 'ENVIRONMENT.BNDL'))
        test_convert(os.path.join(PC, 'UI', 'SCREENS2', '371621.BNDL'))
        test_strings(os.path.join(PC, 'UI', 'LANGUAGE', '0001.BNDL'))
    if PS3:
        test_unchanged_save(os.path.join(PS3, 'GLOBALEFFECTS.BNDL'))
        test_edits(os.path.join(PS3, 'GLOBALEFFECTS.BNDL'))
        test_convert(os.path.join(PS3, 'HAWAII', 'ENVIRONMENT.BNDL'))
        test_convert(os.path.join(PS3, 'POSTFX.BNDL'))
        test_strings(os.path.join(PS3, 'UI', 'LANGUAGE', '0001.BNDL'))
    if not (PC or PS3):
        print('set BNDLX_PC and / or BNDLX_PS3')
        return 2
    shutil.rmtree(TMP, ignore_errors=True)
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
