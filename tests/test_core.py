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
    if PS3:
        test_unchanged_save(os.path.join(PS3, 'GLOBALEFFECTS.BNDL'))
        test_edits(os.path.join(PS3, 'GLOBALEFFECTS.BNDL'))
        test_convert(os.path.join(PS3, 'HAWAII', 'ENVIRONMENT.BNDL'))
        test_convert(os.path.join(PS3, 'POSTFX.BNDL'))
        test_strings(os.path.join(PS3, 'UI', 'LANGUAGE', '0001.BNDL'))
        test_sounds(os.path.join(PS3, 'UI', 'SCREENS2', 'BLACKLISTHUD.BNDL'))
    if not (PC or PS3):
        print('set BNDLX_PC and / or BNDLX_PS3')
        return 2
    shutil.rmtree(TMP, ignore_errors=True)
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
