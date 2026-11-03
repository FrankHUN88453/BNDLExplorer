"""Mod packages on temporary game folders (copies of a few game files; the game itself is never touched): a
package holds only the changed resources of a bundle and whole other files; two mods in a load order merge per
resource, the later one wins and the conflict is reported; reordering, disabling, order export / import, files
changed outside, new files, restore everything, unsafe paths and other platforms. Needs BNDLX_PC."""
import json
import os
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from bndlx import modpack as M, triggers as TR  # noqa: E402
from bndlx.bundle import Bundle  # noqa: E402

PC = os.environ['BNDLX_PC']
TMP = tempfile.mkdtemp(prefix='bndlx_mods_')
FILES = ['HAWAII/TRIGGERS.BNDL', 'UI/SCREENS2/371621.BNDL', 'VERSION.TXT']
failures = []


def check(cond, what):
    print(('ok    ' if cond else 'FAIL  ') + what)
    if not cond:
        failures.append(what)


def game(name):
    """A small game folder: the test files plus the header of GLOBALEFFECTS.BNDL (the platform marker)."""
    root = os.path.join(TMP, name)
    for f in FILES:
        dst = os.path.join(root, *f.split('/'))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(PC, *f.split('/')), dst)
    with open(os.path.join(PC, 'GLOBALEFFECTS.BNDL'), 'rb') as f:
        head = f.read(64)
    with open(os.path.join(root, 'GLOBALEFFECTS.BNDL'), 'wb') as f:
        f.write(head)
    return root


def path(root, rel):
    return os.path.join(root, *rel.split('/'))


def save_edit(root, rel, data=None, bundle=None):
    """Save like BNDL Explorer: the original kept once as .orig."""
    p = path(root, rel)
    if not os.path.exists(p + '.orig'):
        shutil.copyfile(p, p + '.orig')
    if bundle is not None:
        bundle.save(p)
    else:
        with open(p, 'wb') as f:
            f.write(data)


def move_trigger(root, dx):
    b = Bundle.open(path(root, 'HAWAII/TRIGGERS.BNDL'))
    r = next(x for x in b.resources if x.type == TR.T_TRIGGERS)
    td = TR.TriggerData.read(r.data(0), b.e)
    td.triggers[0].pos[0] += dx
    r.set_data(0, td.to_bytes())
    save_edit(root, 'HAWAII/TRIGGERS.BNDL', bundle=b)
    return td.triggers[0].pos[0]


def edit_screen(root):
    b = Bundle.open(path(root, 'UI/SCREENS2/371621.BNDL'))
    r = next(x for x in b.resources if x.type == 0x15 and len(x.data(0)) > 64)
    d = bytearray(r.data(0))
    d[40] ^= 0x5A
    r.set_data(0, bytes(d))
    save_edit(root, 'UI/SCREENS2/371621.BNDL', bundle=b)
    return r.id


def trigger_x(root):
    b = Bundle.open(path(root, 'HAWAII/TRIGGERS.BNDL'))
    r = next(x for x in b.resources if x.type == TR.T_TRIGGERS)
    return TR.TriggerData.read(r.data(0), b.e).triggers[0].pos[0]


def main():
    pristine = {f: M.md5_file(os.path.join(PC, *f.split('/'))) for f in FILES}
    # -- two mods made on their own game folders --------------------------------------------------------------
    a = game('author1')
    x1 = move_trigger(a, 100.0)
    save_edit(a, 'VERSION.TXT', b'modded version\r\n')
    ch = M.scan(a)
    kinds = {c.path: (c.kind, c.resources) for c in ch}
    check(kinds == {'HAWAII/TRIGGERS.BNDL': ('bundle', 1), 'VERSION.TXT': ('file', 0)},
          f'scan finds the changed bundle (1 resource) and file: {kinds}')
    z1 = os.path.join(TMP, 'mod1.zip')
    man = M.make_package(a, z1, ch, {'name': 'Mod one', 'author': 'tester'})
    with zipfile.ZipFile(z1) as z:
        names = z.namelist()
    check(len([n for n in names if n.endswith('.bres')]) == 1 and 'files/VERSION.TXT' in names
          and man['platform'] == 'PC', f'the package holds 1 resource and the file ({len(names)} entries)')

    b2 = game('author2')
    x2 = move_trigger(b2, -50.0)
    rid = edit_screen(b2)
    z2 = os.path.join(TMP, 'mod2.zip')
    man2 = M.make_package(b2, z2, M.scan(b2), {'name': 'Mod two'})
    scr = next(f for f in man2['files'] if f['path'].endswith('371621.BNDL'))
    nres = len(Bundle.open(path(b2, 'UI/SCREENS2/371621.BNDL')).resources)
    check(len(scr['resources']) == 1 and not scr['removed'],
          f'only the one edited resource of the screen bundle is packed (of {nres}; ids per stream)')
    # a new file
    with open(path(b2, 'UI/NEWFILE.TXT'), 'wb') as f:
        f.write(b'brand new')
    z3 = os.path.join(TMP, 'mod3.zip')
    M.make_package(b2, z3, ['UI/NEWFILE.TXT'], {'name': 'New file'})

    # -- installed in order on another copy -------------------------------------------------------------------
    g = game('player')
    prof = M.Profile.load(g)
    for z in (z1, z2, z3):
        prof.add(z)
    prof.save()
    rep = M.apply(prof)
    ok = (abs(trigger_x(g) - x2) < 1e-3 and M.md5_file(path(g, 'VERSION.TXT')) == M.md5_file(path(a, 'VERSION.TXT'))
          and M.md5_file(path(g, 'UI/SCREENS2/371621.BNDL')) == M.md5_file(path(b2, 'UI/SCREENS2/371621.BNDL')))
    check(ok and not rep.errors, f'mods 1, 2, 3 applied: the later trigger edit wins, both other files changed '
                                 f'({len(rep.written)} written, errors {rep.errors})')
    check(os.path.isfile(path(g, 'UI/NEWFILE.TXT')) and 'UI/NEWFILE.TXT' in prof.created, 'the new file is created')
    con = [(c.path, c.rid) for c in rep.conflicts]
    check(len(con) == 1 and con[0][0] == 'HAWAII/TRIGGERS.BNDL',
          f'one conflict reported (the trigger resource, mods {rep.conflicts[0].mods if rep.conflicts else []})')
    check(all(os.path.isfile(path(g, f) + '.orig') for f in FILES), 'the originals are kept as .orig')

    prof.mods = [prof.mods[1], prof.mods[0], prof.mods[2]]   # mod 2 first: mod 1 wins the trigger
    M.apply(prof)
    check(abs(trigger_x(g) - x1) < 1e-3 and M.md5_file(path(g, 'UI/SCREENS2/371621.BNDL'))
          == M.md5_file(path(b2, 'UI/SCREENS2/371621.BNDL')), 'reordered: mod 1 wins the trigger, mod 2 keeps the screen')
    alone = M.Profile.load(game('alone'))
    alone.add(z1)
    M.apply(alone)
    check(M.md5_file(path(alone.root, 'HAWAII/TRIGGERS.BNDL')) == man['files'][0]['result_md5'],
          'a single mod rebuilds the author\'s bundle byte for byte')

    prof.mods[1].enabled = False                              # mod 1 off: its file goes back
    prof.mods[2].enabled = False                              # the new file goes away
    rep = M.apply(prof)
    check(M.md5_file(path(g, 'VERSION.TXT')) == pristine['VERSION.TXT'] and abs(trigger_x(g) - x2) < 1e-3
          and not os.path.exists(path(g, 'UI/NEWFILE.TXT')),
          f'disabled mods are undone ({len(rep.restored)} restored, {len(rep.removed)} removed)')

    # -- order files --------------------------------------------------------------------------------------------
    order = os.path.join(TMP, 'order.json')
    prof.export_order(order)
    other = M.Profile.load(game('friend'))
    missing = other.import_order(order, [TMP])
    check(not missing and [os.path.basename(m.path) for m in other.mods] == ['mod2.zip', 'mod1.zip', 'mod3.zip']
          and [m.enabled for m in other.mods] == [True, False, False], 'the order file brings the same order and switches')

    # -- changed outside ----------------------------------------------------------------------------------------
    with open(path(g, 'UI/SCREENS2/371621.BNDL'), 'ab') as f:
        f.write(b'\0' * 16)
    edited = M.md5_file(path(g, 'UI/SCREENS2/371621.BNDL'))
    rep = M.apply(prof)
    check(rep.outside == ['UI/SCREENS2/371621.BNDL'] and M.md5_file(path(g, 'UI/SCREENS2/371621.BNDL')) == edited,
          'a file changed outside the launcher is reported and left alone')
    M.apply(prof, force=True)
    check(M.md5_file(path(g, 'UI/SCREENS2/371621.BNDL')) == M.md5_file(path(b2, 'UI/SCREENS2/371621.BNDL')),
          'Apply anyway overwrites it')
    fresh = M.Profile.load(game('own_edit'))                  # an own edit saved in BNDL Explorer, not packaged
    move_trigger(fresh.root, 7.0)
    fresh.add(z2)
    rep = M.apply(fresh)
    check('HAWAII/TRIGGERS.BNDL' in rep.outside, 'an own unpackaged edit (differs from .orig) is not overwritten')

    # -- everything back ----------------------------------------------------------------------------------------
    M.restore_all(prof)
    check(all(M.md5_file(path(g, f)) == pristine[f] for f in FILES) and [m.enabled for m in prof.mods] == [True, False, False],
          'Restore the original game: every file is the original again, the order is kept')

    # -- unsafe / foreign packages ------------------------------------------------------------------------------
    bad = os.path.join(TMP, 'bad.zip')
    with zipfile.ZipFile(bad, 'w') as z:
        z.writestr('manifest.json', json.dumps({'format': M.FORMAT, 'version': 1, 'name': 'bad', 'platform': 'PC',
                                                'files': [{'path': '../../evil.txt', 'kind': 'file'}]}))
        z.writestr('files/../../evil.txt', b'x')
    try:
        M.Package(bad)
        refused = False
    except M.ModError:
        refused = True
    check(refused, 'a package with a path outside the game folder is refused')
    ps3 = os.path.join(TMP, 'ps3.zip')
    with zipfile.ZipFile(z1) as zi, zipfile.ZipFile(ps3, 'w') as zo:
        for n in zi.namelist():
            data = zi.read(n)
            if n == 'manifest.json':
                js = json.loads(data)
                js['platform'] = 'PS3'
                data = json.dumps(js).encode()
            zo.writestr(n, data)
    p4 = M.Profile.load(game('ps3test'))
    p4.add(ps3)
    rep = M.apply(p4)
    check(rep.skipped and not rep.written, f'a PS3 package is left out on a PC game ({rep.skipped})')
    shutil.rmtree(TMP, ignore_errors=True)
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
