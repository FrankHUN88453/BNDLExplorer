"""Library tests on real game files (not included). Set the game folders first:

  set BNDLX_PC=...\\Need for Speed(TM) Most Wanted
  set BNDLX_PS3=...\\NPXX00207\\USRDIR\\HAWAII_MAIN
  python tests\\test_core.py
"""
import os
import shutil
import struct
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
    csv = t.to_csv().replace(f'0x{t.entries[0][0]:08X},', f'0x{t.entries[0][0]:08X},HELLO ', 1)
    ch, add, unk = ops.strings_from_csv(b, r, csv)
    t2 = StringTable.read(r, b.e)
    check(ch == 1 and t2.entries[0][1].startswith('HELLO '), 'CSV import changes one string')
    # what Excel saves with a Hungarian / German locale: ';' separators, a BOM, ids without leading zeros
    sid, old = t.entries[1]
    quoted = ('SZIA; ' + old).replace('"', '""')
    excel = chr(0xFEFF) + 'id;text' + chr(13) + chr(10) + f'{sid:X};"{quoted}"' + chr(13) + chr(10)
    ch, add, unk = ops.strings_from_csv(b, r, excel)
    t3 = dict(StringTable.read(r, b.e).entries)
    check(ch == 1 and t3[sid] == f'SZIA; {old}', 'semicolon CSV (Excel, decimal-comma locales) imports')


def test_sounds(path):
    """Decode every sound, replace one from a WAV, save, reload; PS3 <-> PC header conversion."""
    from bndlx import eal3
    b = Bundle.open(path)
    waves = [r for r in b.resources if r.type == 0x81 and eal3.wave_fields(r.data(0), b.e)['stream_ref'] is None]
    bad = 0
    for r in waves:
        audio, rate, head, _ = ops.wave_audio(b, r)
        if len(audio) != head['samples'] or audio.shape[1] != head['channels']:
            bad += 1
    check(waves and not bad, f'{len(waves)} sounds decode to their exact length ({os.path.basename(path)})')
    rate = 48000
    t = np.arange(rate) / rate
    tone = (0.4 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    wav = os.path.join(TMP, 'tone.wav')
    ops.write_file(wav, eal3.wav_bytes(np.stack([tone, tone], 1), rate))
    r = waves[0]
    old_ch = ops.wave_audio(b, r)[2]['channels']
    ops.replace_wave(b, r, wav)
    out = os.path.join(TMP, 'snd.BNDL')
    b.save(out)
    nb = Bundle.open(out)
    audio, rate2, head, _ = ops.wave_audio(nb, nb.find(r.id))
    ref = eal3.mix_channels(np.stack([tone, tone], 1), old_ch)
    ref = eal3.resample(ref, rate, rate2)
    n = min(len(ref), len(audio))
    snr = 10 * np.log10((ref[:n].astype(float) ** 2).sum() / max(((ref[:n].astype(float) - audio[:n]) ** 2).sum(), 1))
    check(len(audio) == len(ref) and snr > 25, f'a WAV imported into a sound decodes back ({snr:.1f} dB, {head["channels"]} ch)')
    other = 'PC' if b.platform == 'PS3' else 'PS3'
    conv = convert.convert_resource(nb.find(r.id), b.platform, other)
    back = convert.convert_resource(conv, other, b.platform)
    check(eal3.wave_stream(back.data(0), b.e) == eal3.wave_stream(nb.find(r.id).data(0), b.e), 'sound converts PS3 <-> PC')


def test_prefetch(pc_root):
    """A prefetched stream (start in the bundle, rest in <GameChanger id>.SPS): decode and import, on a copy."""
    from bndlx import eal3
    root = os.path.join(TMP, 'game')
    os.makedirs(os.path.join(root, 'EN_US', 'FEEDBACKGROUPS'))
    os.makedirs(os.path.join(root, 'UI', 'SEQUENCES', 'STREAMS'))
    open(os.path.join(root, 'GLOBALEFFECTS.BNDL'), 'wb').close()
    bp = os.path.join(root, 'EN_US', 'FEEDBACKGROUPS', '1156428.BNDL')
    shutil.copy2(os.path.join(pc_root, 'EN_US', 'FEEDBACKGROUPS', '1156428.BNDL'), bp)
    sp = os.path.join(root, 'UI', 'SEQUENCES', 'STREAMS', '1317608.SPS')
    shutil.copy2(os.path.join(pc_root, 'UI', 'SEQUENCES', 'STREAMS', '1317608.SPS'), sp)
    ops._SPS_INDEX.clear()
    b = Bundle.open(bp)
    r = b.find(0x0100000000141AE8)
    audio, rate, head, f = ops.wave_audio(b, r, bp)
    check(f == sp and len(audio) == head['samples'] and len(audio) > 10 * rate,
          f'prefetched stream decodes whole ({len(audio) / rate:.1f} s from bundle + {os.path.basename(sp)})')
    t = np.arange(3 * rate) / rate
    wav = os.path.join(TMP, 'long.wav')
    ops.write_file(wav, eal3.wav_bytes((np.sin(2 * np.pi * 330 * t) * 9000).astype(np.int16)[:, None], rate))
    ops.replace_wave(b, r, wav, bundle_path=bp)
    b.save(bp)
    nb = Bundle.open(bp)
    fields = eal3.wave_fields(nb.find(r.id).data(0), nb.e)
    audio2, rate2, head2, f2 = ops.wave_audio(nb, nb.find(r.id), bp)
    check(os.path.exists(sp + '.orig') and fields['kind'] == 'prefetch' and abs(len(audio2) - 3 * rate2) <= 1,
          f'importing into a prefetched stream rewrites the bundle start and the .SPS ({len(audio2) / rate2:.2f} s)')


def check_glb(data):
    """Minimal .glb validation: header, chunks, accessors / views inside the buffer."""
    import json
    import struct as st
    magic, ver, total = st.unpack_from('<III', data, 0)
    jl, jt = st.unpack_from('<II', data, 12)
    js = json.loads(data[20:20 + jl])
    bl, bt = st.unpack_from('<II', data, 20 + jl)
    ok = magic == 0x46546C67 and ver == 2 and total == len(data) and jt == 0x4E4F534A and bt == 0x004E4942
    for v in js['bufferViews']:
        ok &= v['byteOffset'] + v['byteLength'] <= bl
    size = {5126: 4, 5125: 4}
    comps = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3}
    for a in js['accessors']:
        v = js['bufferViews'][a['bufferView']]
        ok &= a['count'] * size[a['componentType']] * comps[a['type']] <= v['byteLength']
    return ok, js


def test_models(path, expect_textures=True):
    from bndlx import gltf, mesh
    b = Bundle.open(path)
    lib = mesh.Library()
    done = tris = bad = 0
    for r in b.resources:
        if r.type != 0x51:
            continue
        try:
            meshes, nlod = mesh.decode_resource(b, r, lib, [], path)
        except mesh.MeshError:
            continue
        done += 1
        for m in meshes:
            tris += len(m.tris)
            bad += int(m.tris.max() >= len(m.pos)) if len(m.tris) else 0
    check(done and not bad, f'{done} models decode ({tris} triangles, {os.path.basename(path)})')
    body = next(r for r in b.resources if r.type == 0x51)
    meshes, _ = mesh.decode_resource(b, body, lib, [], path)
    texs = {}
    root = mesh.game_root(path)
    for m in meshes:
        if m.texture:
            tb, tr = lib.find(m.texture, [b], root)
            texs[m.texture] = raster.decode(tr, tb.platform) if tr is not None else None
    ok, js = check_glb(gltf.write_glb(meshes, texs, 'test'))
    check(ok and len(js['meshes']) == len(meshes) and (not expect_textures or 'images' in js or not any(texs.values())),
          f'glTF export is well formed ({len(js["meshes"])} meshes, {len(js.get("images", []))} images)')


def test_materials(path):
    """Every material parses; its textures resolve; constant names come from the shaders; editing a constant
    changes exactly its 16 bytes."""
    from bndlx import mesh
    b = Bundle.open(path)
    lib = mesh.Library()
    root = mesh.game_root(path)
    names = lib.constant_names(root)
    mats = [r for r in b.resources if r.type == 0x02]
    texs = found = consts = named = 0
    for r in mats:
        info = mesh.material_info(b, r)
        for slot, tid, sid, off in info['textures']:
            texs += 1
            found += lib.find(tid, [b], root)[1] is not None if tid else 0
        for h, vals, off in info['constants']:
            consts += 1
            named += h in names
    check(mats and found == texs, f'{len(mats)} materials, {found}/{texs} textures found ({os.path.basename(path)})')
    check(consts and named * 10 >= consts * 9, f'{named}/{consts} material constants named')
    r = next(r for r in mats if mesh.material_info(b, r)['constants'])
    h, vals, off = mesh.material_info(b, r)['constants'][0]
    before = bytes(r.data(0))
    out = os.path.join(TMP, 'mat.bndl')
    c = bytearray(before)
    struct.pack_into(b.e + '4f', c, off, 0.25, 0.5, 0.75, 1.0)
    r.set_data(0, bytes(c))
    b.save(out)
    b2 = Bundle.open(out)
    r2 = b2.find(r.id)
    diff = [i for i, (x, y) in enumerate(zip(before, r2.data(0))) if x != y]
    got = mesh.material_info(b2, r2)['constants'][0][1]
    check(diff and min(diff) >= off and max(diff) < off + 16 and got == (0.25, 0.5, 0.75, 1.0),
          'material constant edit round-trips')


def test_sps(pc_root):
    """Stand-alone .SPS files: open (continuation files with their start from the bundle), save byte-identical,
    replace the sound and save, batch export as WAV with song titles."""
    from bndlx import eal3, spsfile
    for rel in (('UI', 'SONGS', '116502.SPS'), ('UI', 'SEQUENCES', 'STREAMS', '1009836.SPS'),
                ('EN_US', 'STREAMS', '2026277.SPS')):
        p = os.path.join(pc_root, *rel)
        b = spsfile.SpsBundle.open(p)
        audio, rate, head, _ = ops.wave_audio(b, b.resources[0], p)
        same = b.to_bytes() == open(p, 'rb').read()
        check(same and len(audio) == head['samples'],
              f'{rel[-1]} opens ({len(audio) / rate:.1f} s, {head["channels"]} ch'
              + (f', start from {os.path.basename(b.owner[0])}' if b.owner else '') + ') and saves unchanged')
    src = os.path.join(pc_root, 'SOUND', 'STREAMS', '1536091.SPS')
    p = os.path.join(TMP, 'stream.SPS')
    shutil.copy2(src, p)
    b = spsfile.SpsBundle.open(p)
    wav = os.path.join(TMP, 'tone.wav')
    t = np.arange(48000) / 48000
    ops.write_file(wav, eal3.wav_bytes((np.sin(2 * np.pi * 440 * t) * 9000).astype(np.int16)[:, None], 48000))
    ops.replace_wave(b, b.resources[0], path=wav, bundle_path=p)
    b.save()
    audio, rate, head = eal3.decode_sps(open(p, 'rb').read())
    check(abs(len(audio) / rate - 1.0) < 0.01, f'sound replaced in an .SPS file and saved ({len(audio) / rate:.2f} s)')
    audio2, rate2 = eal3.read_audio(p)
    check(len(audio2) == len(audio), 'an .SPS file can be used as audio input')
    out = os.path.join(TMP, 'wav_out')
    n, errors = ops.export_sps_folder(os.path.join(pc_root, 'EN_US', 'STREAMS'), out)
    check(n == len([f for f in os.listdir(os.path.join(pc_root, 'EN_US', 'STREAMS')) if f.lower().endswith('.sps')])
          and not errors, f'{n} continuation .SPS files exported as WAV')
    titles = ops.song_titles(os.path.join(pc_root, 'UI', 'SONGS', 'SONGS.BNDL'))
    check(len(titles) >= 40, f'{len(titles)} songs named from SONGS.BNDL (e.g. {next(iter(titles.values()), "")})')


def test_world(path):
    """Every instance of a track unit is drawn (shared models from GLOBALRESOURCES / DISTRICT_*), inside the
    unit's area, and exports as glTF."""
    from bndlx import gltf, mesh
    b = Bundle.open(path)
    r = next(x for x in b.resources if x.type == mesh.T_INSTANCELIST)
    lib = mesh.Library()
    meshes, st = mesh.decode_instances(b, r, lib, [], path)
    lo = np.min([m.pos.min(0) for m in meshes], 0)
    hi = np.max([m.pos.max(0) for m in meshes], 0)
    check(st['kinds'].get('props') and st['kinds'].get('compound') and st['kinds'].get('world') == 222,
          f"instances by list: {st['kinds']}")
    check(st['shown'] == st['instances'] and (hi - lo).max() < 2000,
          f"{st['shown']}/{st['instances']} instances drawn, {len(meshes)} meshes, "
          f"{(hi - lo).round().tolist()} m ({os.path.basename(path)})")
    tex = sum(1 for m in meshes if m.texture) / len(meshes)
    check(tex > 0.9 and any(m.alpha_test for m in meshes), f'{tex:.0%} of the world meshes have a colour texture')
    ok, js = check_glb(gltf.write_glb(meshes, {}, 'world'))
    check(ok and len(js['meshes']) == len(meshes), 'track unit exports as glTF')
    soup = next(x for x in b.resources if x.type == mesh.T_POLYSOUP)
    cm, cst = mesh.decode_polysoup(b, soup)
    d = soup.data(0)
    box_lo = np.array(struct.unpack_from('<3f', d, 0))
    box_hi = np.array(struct.unpack_from('<3f', d, 16))
    clo = np.min([m.pos.min(0) for m in cm], 0)
    chi = np.max([m.pos.max(0) for m in cm], 0)
    ntri = sum(len(m.tris) for m in cm)
    check(np.allclose(clo, box_lo, atol=0.02) and np.allclose(chi, box_hi, atol=0.02) and ntri >= cst['polygons'],
          f"collision: {cst['soups']} soups, {cst['polygons']} polygons, {len(cst['tags'])} tags, fills the list's box")


def test_zones(hawaii):
    """HAWAII\\PVS.BNDL: one zone per track unit, border neighbours share polygon points."""
    from bndlx import zonelist as ZL
    zones = ZL.zones_in(hawaii)
    exist = sum(os.path.isfile(os.path.join(hawaii, ZL.unit_file(z.unit))) for z in zones)
    check(zones and exist == len(zones), f'{len(zones)} zones, a TRK_UNIT file for each ({exist})')
    border = [(z, zones[j]) for z in zones for j, fl in z.neighbours if fl & 2]
    shared = sum(1 for a, b in border
                 if len({(round(x, 1), round(y, 1)) for x, y in a.points} & {(round(x, 1), round(y, 1)) for x, y in b.points}) >= 2)
    check(border and shared == len(border), f'{len(border)} border neighbours, all share an edge')
    nb = ZL.neighbour_paths(os.path.join(hawaii, 'TRK_UNIT1.BNDL'))
    check(nb and all(os.path.isfile(p) for p in nb), f'TRK_UNIT1 borders {[os.path.basename(p) for p in nb]}')


def test_car(path):
    """VehicleGraphicsSpec: the body and 4 wheels x 4 parts assemble into a car of plausible, symmetric size."""
    from bndlx import mesh
    b = Bundle.open(path)
    r = next(x for x in b.resources if x.type == mesh.T_VGS)
    meshes, st = mesh.decode_vgs(b, r, mesh.Library(), [], path)
    lo = np.min([m.pos.min(0) for m in meshes], 0)
    hi = np.max([m.pos.max(0) for m in meshes], 0)
    size = hi - lo
    check(st['wheels'] == 4 and st['parts'] == 16 and not st['missing'] and 3.5 < size[2] < 5.5
          and abs(lo[0] + hi[0]) < 0.05, f"car assembled: {st['wheels']} wheels, {st['parts']} parts, "
          f"{size.round(2).tolist()} m ({os.path.basename(path)})")


def test_proto_world(seacrest):
    """PS3 prototype world (SEACREST): old ZoneList layout, version 2 instance lists, WorldObject model at 0x8."""
    from bndlx import mesh, zonelist as ZL
    ZL._CACHE.clear()
    zones = ZL.zones_in(seacrest)
    exist = sum(os.path.isfile(os.path.join(seacrest, ZL.unit_file(z.unit))) for z in zones)
    check(len(zones) > 100 and exist == len(zones), f'prototype zone list: {len(zones)} zones, {exist} units')
    p = os.path.join(seacrest, 'TRK_UNIT100.BNDL')
    b = Bundle.open(p)
    r = next(x for x in b.resources if x.type == mesh.T_INSTANCELIST)
    meshes, st = mesh.decode_instances(b, r, mesh.Library(), [], p)
    lo = np.min([m.pos.min(0) for m in meshes], 0)
    hi = np.max([m.pos.max(0) for m in meshes], 0)
    check(st['shown'] >= 0.95 * st['instances'] and st['kinds'].get('dynamic') and (hi - lo).max() < 2000,
          f"prototype track unit: {st['shown']}/{st['instances']} instances {st['kinds']}, "
          f"{(hi - lo).round().tolist()} m")
    soup = next(x for x in b.resources if x.type == mesh.T_POLYSOUP)
    cm, cst = mesh.decode_polysoup(b, soup)
    check(cst['polygons'] > 1000, f"prototype collision: {cst['soups']} soups, {cst['polygons']} polygons")


def test_vehiclelist(path):
    """The vehicle list rebuilds byte-identical, survives a CSV round trip, and a CSV edit (a changed value, a
    duplicated car) saves and reads back."""
    from bndlx import vehiclelist as VL
    b = Bundle.open(path)
    r = next(x for x in b.resources if x.type == VL.T_VEHICLELIST)
    v = VL.read(r, b.e)
    check(v.build() == bytes(r.data(0)), f'vehicle list rebuilds identical ({len(v.rows)} vehicles, '
                                         f'{len(v.makers)} manufacturers, {b.platform})')
    txt = ops.vehicles_to_csv(b, r)
    names = ops.game_strings(path)
    first = names.get(v.value(v.rows[0], 'name'), '')
    check(first and first in txt, f'CSV names the cars ({first})')
    lines = txt.splitlines()
    cols = lines[0].split(',')
    row = lines[1].split(',')
    row[cols.index('top_speed_2')] = '250.5'
    lines[1] = ','.join(row)
    lines.insert(2, lines[1])                                  # a duplicated car
    ops.vehicles_from_csv(b, r, '\n'.join(lines) + '\n')
    out = os.path.join(TMP, 'vl.bndl')
    b.save(out)
    v2 = VL.read(Bundle.open(out).find(r.id), b.e)
    check(len(v2.rows) == len(v.rows) + 1 and v2.value(v2.rows[0], 'top_speed_2') == 250.5
          and v2.rows[1] == v2.rows[0] and v2.rows[2:] == v.rows[1:] and v2.makers == v.makers,
          'vehicle list CSV edit saves and reads back')


def main():
    if PC:
        g = os.path.join(PC, 'GLOBALEFFECTS.BNDL')
        test_unchanged_save(g)
        test_edits(g)
        test_change_id(os.path.join(PC, 'UI', 'SCREENS2', '371621.BNDL'))
        test_convert(os.path.join(PC, 'HAWAII', 'ENVIRONMENT.BNDL'))
        test_convert(os.path.join(PC, 'UI', 'SCREENS2', '371621.BNDL'))
        test_strings(os.path.join(PC, 'UI', 'LANGUAGE', '0001.BNDL'))
        test_sounds(os.path.join(PC, 'UI', 'SCREENS2', '371621.BNDL'))
        test_prefetch(PC)
        test_models(os.path.join(PC, 'VEHICLES', 'VEH_1085007_HI.BNDL'))
        test_materials(os.path.join(PC, 'VEHICLES', 'VEH_1085007_HI.BNDL'))
        test_vehiclelist(os.path.join(PC, 'VEHICLES', 'VEHICLELIST.BNDL'))
        test_car(os.path.join(PC, 'VEHICLES', 'VEH_1085007_HI.BNDL'))
        test_world(os.path.join(PC, 'HAWAII', 'TRK_UNIT1.BNDL'))
        test_sps(PC)
        test_zones(os.path.join(PC, 'HAWAII'))
    if PS3:
        test_unchanged_save(os.path.join(PS3, 'GLOBALEFFECTS.BNDL'))
        test_edits(os.path.join(PS3, 'GLOBALEFFECTS.BNDL'))
        test_convert(os.path.join(PS3, 'HAWAII', 'ENVIRONMENT.BNDL'))
        test_convert(os.path.join(PS3, 'POSTFX.BNDL'))
        test_strings(os.path.join(PS3, 'UI', 'LANGUAGE', '0001.BNDL'))
        test_sounds(os.path.join(PS3, 'UI', 'SCREENS2', 'BLACKLISTHUD.BNDL'))
        test_models(os.path.join(PS3, 'VEHICLES', 'VEH_122672_MS.BNDL'))
        test_materials(os.path.join(PS3, 'VEHICLES', 'VEH_122672_MS.BNDL'))
        test_vehiclelist(os.path.join(PS3, 'VEHICLES', 'VEHICLELIST.BNDL'))
        test_car(os.path.join(PS3, 'VEHICLES', 'VEH_122672_MS.BNDL'))
        test_proto_world(os.path.join(PS3, 'SEACREST'))
    if not (PC or PS3):
        print('set BNDLX_PC and / or BNDLX_PS3')
        return 2
    shutil.rmtree(TMP, ignore_errors=True)
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
