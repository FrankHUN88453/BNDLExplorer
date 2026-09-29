"""Import / export operations used by the explorer (no GUI code here)."""
import os
import re
import struct

import numpy as np

from . import colourcube, convert, eal3, raster, resfile, textfile, vehiclelist
from .localised import StringTable
from .restypes import T_CUBE, T_STRINGS, T_TEXT, T_TEXTURE, name as type_name
from .vehiclelist import T_VEHICLELIST

T_WAVE = 0x81

IMAGE_EXT = ('.png', '.tga', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp')
ID_RE = re.compile(r'^(?:0x)?([0-9A-Fa-f]{8,16})(?![0-9A-Fa-f])')
TOOL = 'BNDL Explorer'


class OpError(ValueError):
    pass


def id_text(rid):
    return f'{rid:016X}' if rid >> 32 else f'{rid:08X}'


def id_from_filename(path):
    m = ID_RE.match(os.path.basename(path))
    return int(m.group(1), 16) if m else None


def load_image(path):
    from PIL import Image
    with Image.open(path) as im:
        return np.array(im.convert('RGBA'))


def save_png(img, path):
    from PIL import Image
    Image.fromarray(np.ascontiguousarray(img, np.uint8), 'RGBA').save(path)


def write_file(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------------------------------------------
# textures
# ---------------------------------------------------------------------------------------------------------------
def replace_texture(b, res, path=None, data=None, fmt=None, mips=None, srgb=None, image=None):
    """Replace a texture from an image file / DDS file / RGBA array.
    fmt: target format (None = keep the current one when it can be encoded); mips: 'keep', 'full', 'none'
    (None = full chain if the texture had mip maps); srgb: None = keep."""
    plat = b.platform
    inf = raster.info(res, plat)
    old = res.data(0)
    srgb = inf.srgb if srgb is None else srgb
    template = old if plat == 'PC' else None
    if image is None and data is None:
        with open(path, 'rb') as f:
            data = f.read()
    if image is None and data[:4] == b'DDS ':
        hdr, pix = raster.from_dds(data, plat, template=template, srgb=srgb)
        if fmt and raster.info(_Tmp(hdr, pix, plat), plat).fmt != raster.target_format(fmt, plat):
            img = [raster.decode(_Tmp(hdr, pix, plat), plat, 0, f) for f in range(raster.info(_Tmp(hdr, pix, plat), plat).faces)]
            hdr, pix = raster.make(plat, img, fmt, srgb, None, template)
    else:
        img = image if image is not None else load_image(path)
        if inf.faces != 1:
            raise OpError('cube maps can only be replaced with a DDS cube map')
        target = fmt or inf.fmt
        if target not in (raster.BC1, raster.BC2, raster.BC3, raster.RGBA8, raster.BGRA8, raster.ARGB8,
                          raster.R8, raster.L8):
            target = raster.RGBA8 if plat == 'PC' else raster.ARGB8
        h, w = img.shape[:2]
        if mips == 'none':
            n = 1
        elif mips == 'keep':
            n = inf.mips if inf.mips > 1 else 1
        else:
            n = raster.full_mip_count(w, h) if (mips == 'full' or inf.mips > 1) else 1
        hdr, pix = raster.make(plat, [img], target, srgb, n, template)
    res.set_data(0, hdr)
    res.set_data(b.gfx_chunk, pix)
    return raster.info(res, plat)


class _Tmp:
    def __init__(self, hdr, pix, plat):
        self.c = [hdr, pix if plat == 'PC' else b'', pix if plat == 'PS3' else b'', b'']

    def data(self, k):
        return self.c[k]


def export_texture(b, res, path, mip=0, face=0):
    if path.lower().endswith('.dds'):
        write_file(path, raster.to_dds(res, b.platform))
    else:
        save_png(raster.decode(res, b.platform, mip, face), path)


# ---------------------------------------------------------------------------------------------------------------
# colour cubes
# ---------------------------------------------------------------------------------------------------------------
def cube_lut(b, res):
    c = res.data(0)
    return colourcube.pc_to_lut(c) if b.platform == 'PC' else colourcube.ps3_to_lut(c)


def cube_strip(lut):
    """16 slices side by side: 256 x 16, x = b * 16 + r, y = g."""
    img = np.zeros((16, 256, 4), np.uint8)
    for bb in range(16):
        img[:, bb * 16:(bb + 1) * 16, :3] = np.clip(np.rint(lut[:, :, bb].transpose(1, 0, 2)), 0, 255)
    img[..., 3] = 255
    return img


def strip_to_lut(img):
    if img.shape[:2] != (16, 256):
        raise OpError('a colour cube image must be 256 x 16 (16 slices of 16 x 16, blue = slice)')
    lut = np.zeros((16, 16, 16, 3), np.float32)
    for bb in range(16):
        lut[:, :, bb] = img[:, bb * 16:(bb + 1) * 16, :3].transpose(1, 0, 2)
    return lut


def replace_cube(b, res, path):
    lut = strip_to_lut(load_image(path))
    c = res.data(0)
    res.set_data(0, (colourcube.lut_to_pc if b.platform == 'PC' else colourcube.lut_to_ps3)(lut, c[:16]))


# ---------------------------------------------------------------------------------------------------------------
# sounds
# ---------------------------------------------------------------------------------------------------------------
_SPS_INDEX = {}


def game_root(bundle_path):
    cur = os.path.dirname(os.path.abspath(bundle_path or '.'))
    for _ in range(8):
        if any(os.path.exists(os.path.join(cur, n)) for n in ('GLOBALEFFECTS.BNDL', 'NFS13.exe', 'EBOOT.BIN')):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


_STRINGS = {}


def game_strings(bundle_path, language='0001'):
    """{string id: text} of the game's UI/LANGUAGE/<language>.BNDL (0001 = English), for showing names."""
    root = game_root(bundle_path)
    path = os.path.join(root, 'UI', 'LANGUAGE', language + '.BNDL') if root else None
    if path not in _STRINGS:
        out = {}
        if path and os.path.isfile(path):
            try:
                from .bundle import Bundle
                lb = Bundle.open(path)
                for r in lb.resources:
                    if r.type == T_STRINGS:
                        out.update(StringTable.read(r, lb.e).entries)
            except Exception:
                pass
        _STRINGS[path] = out
    return _STRINGS[path]


def vehicles_to_csv(b, res):
    return vehiclelist.read(res, b.e).to_csv(game_strings(b.path))


def vehicles_from_csv(b, res, text):
    v = vehiclelist.read(res, b.e)
    n = v.update_from_csv(text)
    res.set_data(0, v.build())
    return n


def find_stream_file(bundle_path, rel=None, name=None):
    """External .SPS file of a sound: `rel` = path relative to the game folder (stream references), or
    `name` = file name looked up anywhere in the game folder (prefetched streams: <GameChanger id>.SPS)."""
    if rel:
        rel = rel.replace('\\', os.sep).replace('/', os.sep)
        cur = os.path.dirname(os.path.abspath(bundle_path or '.'))
        for _ in range(8):
            p = os.path.join(cur, rel)
            if os.path.isfile(p):
                return p
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        return None
    root = game_root(bundle_path)
    if root is None or not name:
        return None
    idx = _SPS_INDEX.get(root)
    if idx is None:
        idx = {}
        for dp, _, fn in os.walk(root):
            for f in fn:
                if f.upper().endswith('.SPS'):
                    idx.setdefault(f.upper(), os.path.join(dp, f))
        _SPS_INDEX[root] = idx
    return idx.get(name.upper())


def stream_file_of(b, res, bundle_path):
    f = eal3.wave_fields(res.data(0), b.e)
    if f['kind'] == 'stream':
        return f, find_stream_file(bundle_path, rel=f['stream_ref'])
    if f['kind'] == 'prefetch':
        return f, find_stream_file(bundle_path, name=f'{res.id & 0xFFFFFFFF}.SPS')
    return f, None


def wave_audio(b, res, bundle_path=None):
    """(int16 audio, rate, SNR header, stream file or None) of a Wave resource."""
    f, p = stream_file_of(b, res, bundle_path)
    if f['kind'] == 'stream' and p is None:
        raise eal3.AudioError(f'this sound plays the stream file {f["stream_ref"]}, which was not found next to the bundle')
    if p is not None:
        with open(p, 'rb') as fh:
            ext = fh.read()
        if f['kind'] == 'prefetch':          # the file continues the start stored in the resource
            ext = eal3.wave_stream(res.data(0), b.e) + ext
        audio, rate, head = eal3.decode_sps(ext)
        return audio, rate, head, p
    audio, rate, head = eal3.decode_sps(eal3.wave_stream(res.data(0), b.e))
    if f['kind'] == 'prefetch':
        head = dict(head, prefetch_only=True)
    return audio, rate, head, None


def replace_wave(b, res, path=None, data=None, rate=None, channels=None, quality=0.2, bundle_path=None):
    """Encode an audio file into a Wave resource (EALayer3). rate / channels None = as the old sound.
    Streamed sounds get their .SPS file rewritten too (the original is kept as .orig). Returns a description."""
    audio, src_rate = eal3.read_audio(path, data)
    old = res.data(0)
    f, stream_file = stream_file_of(b, res, bundle_path)
    try:
        _, old_rate, old_head, _ = wave_audio(b, res, bundle_path)
    except eal3.AudioError:
        old_rate, old_head = None, None
    if f['kind'] != 'memory' and stream_file is None:
        raise eal3.AudioError('the stream file of this sound was not found in the game folder')
    target_rate = rate or old_rate or src_rate
    target_ch = channels or (old_head['channels'] if old_head else None) or f['channels'] or None
    audio, dst_rate = eal3.prepare_audio(audio, src_rate, target_ch, target_rate)
    loop = bool(old_head and old_head['loop'])
    sps, head = eal3.encode_sps(audio, dst_rate, loop=loop, loop_start=0, quality=quality)
    where = ''
    de = '<' if b.e == '>' else b.e
    part = None
    if f['kind'] == 'prefetch':
        part = eal3.prefetch_part(sps, f.get('prefetch_ms', 1000.0), head['rate'])
    if stream_file is not None:
        if not os.path.exists(stream_file + '.orig'):
            import shutil
            shutil.copy2(stream_file, stream_file + '.orig')
        write_file(stream_file, sps[len(part):] if part is not None else sps)
        where = f' (stream file {os.path.basename(stream_file)} rewritten, original kept as .orig)'
    if f['kind'] == 'stream':
        hdr = bytearray(old)
        struct.pack_into(de + 'f', hdr, 0x14, head['samples'] * 1000.0 / head['rate'])
        hdr[0x24] = head['channels']
        res.set_data(0, bytes(hdr))
    elif f['kind'] == 'prefetch':
        res.set_data(0, eal3.build_wave(old, part, head, b.e))
    else:
        res.set_data(0, eal3.build_wave(old, sps, head, b.e))
    return f'{head["channels"]} channel(s), {head["rate"]} Hz, {head["samples"] / head["rate"]:.2f} s{where}'


# ---------------------------------------------------------------------------------------------------------------
# text / strings
# ---------------------------------------------------------------------------------------------------------------
def replace_text(b, res, raw):
    res.set_data(0, textfile.build(raw, b.e))


def strings_to_csv(b, res):
    return StringTable.read(res, b.e).to_csv()


def strings_from_csv(b, res, text, add_new=False):
    t = StringTable.read(res, b.e)
    changed, added, unknown = t.update_from_csv(text, add_new)
    if changed or added:
        res.set_data(0, t.build(b.e))
    return changed, added, unknown


# ---------------------------------------------------------------------------------------------------------------
# whole resources
# ---------------------------------------------------------------------------------------------------------------
def export_native(b, res, folder, texture_format='.dds'):
    """Export in the resource's own exchange format when it has one (texture, text, strings, cube), else .bres.
    Returns the path written."""
    base = os.path.join(folder, id_text(res.id))
    t = res.type
    if t == T_TEXTURE:
        path = base + texture_format
        export_texture(b, res, path)
    elif t == T_TEXT:
        path = base + '.txt'
        write_file(path, textfile.read(res, b.e))
    elif t == T_STRINGS:
        path = base + '.csv'
        write_file(path, strings_to_csv(b, res).encode('utf-8-sig'))
    elif t == T_CUBE:
        path = base + '.png'
        save_png(cube_strip(cube_lut(b, res)), path)
    elif t == T_VEHICLELIST:
        path = base + '.csv'
        write_file(path, vehicles_to_csv(b, res).encode('utf-8-sig'))
    elif t == T_WAVE and eal3.wave_fields(res.data(0), b.e)['kind'] == 'memory':
        path = base + '.wav'
        audio, rate, _, _ = wave_audio(b, res)
        write_file(path, eal3.wav_bytes(audio, rate))
    else:
        path = base + '.bres'
        write_file(path, resfile.dump(res, b.platform, TOOL))
    return path


def export_bres(b, res, path):
    write_file(path, resfile.dump(res, b.platform, TOOL))


def adopt(b, res, platform, types):
    """Resource from another bundle / file ready to be added to bundle b (converted when the platform differs)."""
    if platform != b.platform:
        c = convert.convert_resource(res, platform, b.platform, types)
        c.us_bits, c.cs_bits = b.default_bits()
        return c
    c = res.copy()
    c.modified = True
    return c


def import_bres(b, data, types):
    res, plat = resfile.load(data)
    return adopt(b, res, plat, types)


def extract_all(b, folder, progress=None, texture_format='.dds'):
    """Every resource as .bres (in a folder per type), plus textures / texts / strings / cubes in their own
    formats next to them."""
    n = len(b.resources)
    written = 0
    for i, r in enumerate(b.resources):
        if progress:
            progress(i, n)
        if r.missing:
            continue
        sub = os.path.join(folder, type_name(r.type))
        os.makedirs(sub, exist_ok=True)
        export_bres(b, r, os.path.join(sub, id_text(r.id) + '.bres'))
        written += 1
        if r.type in (T_TEXTURE, T_TEXT, T_STRINGS, T_CUBE, T_WAVE, T_VEHICLELIST):
            try:
                export_native(b, r, sub, texture_format)
            except Exception:
                pass
    return written


def import_folder(b, folder, types, progress=None):
    """Add / replace resources from a folder: .bres files, and files named <id>.dds/.png/... (textures),
    <id>.txt (text files), <id>.csv (strings, vehicle list), <id>.png (colour cubes). Returns (changes, errors);
    changes = [(id, 'added'|'replaced', path)]."""
    files = []
    for dp, _, fn in os.walk(folder):
        files += [os.path.join(dp, f) for f in fn]
    changes, errors = [], []
    by_id = b.index()
    bres = [f for f in files if f.lower().endswith('.bres')]
    other = [f for f in files if not f.lower().endswith('.bres')]
    n = len(files)
    for i, path in enumerate(bres + other):
        if progress:
            progress(i, n)
        try:
            if path.lower().endswith('.bres'):
                with open(path, 'rb') as f:
                    r = import_bres(b, f.read(), types)
                existed = r.id in by_id
                b.add(r)
                by_id[r.id] = r
                changes.append((r.id, 'replaced' if existed else 'added', path))
                continue
            rid = id_from_filename(path)
            if rid is None or rid not in by_id:
                continue
            r = by_id[rid]
            ext = os.path.splitext(path)[1].lower()
            if r.type == T_TEXTURE and (ext == '.dds' or ext in IMAGE_EXT):
                replace_texture(b, r, path)
            elif r.type == T_CUBE and ext in IMAGE_EXT:
                replace_cube(b, r, path)
            elif r.type == T_TEXT and ext in ('.txt', '.json', '.xml'):
                with open(path, 'rb') as f:
                    replace_text(b, r, f.read())
            elif r.type == T_STRINGS and ext == '.csv':
                with open(path, encoding='utf-8-sig') as f:
                    strings_from_csv(b, r, f.read())
            elif r.type == T_WAVE and ext in eal3.AUDIO_EXT:
                replace_wave(b, r, path)
            elif r.type == T_VEHICLELIST and ext == '.csv':
                with open(path, encoding='utf-8-sig') as f:
                    vehicles_from_csv(b, r, f.read())
            else:
                continue
            changes.append((rid, 'replaced', path))
        except Exception as e:
            errors.append((path, str(e)))
    return changes, errors


def text_summary(b, res, types):
    """Short description for the resource list."""
    t = res.type
    try:
        if res.missing:
            return '(no data)'
        if t == T_TEXTURE:
            inf = raster.info(res, b.platform)
            return f'{inf.w}x{inf.h} {inf.fmt}' + (' cube' if inf.faces == 6 else '') + f' {inf.mips} mip'
        if t == 0x15:
            imps = res.imports()
            if imps and imps[0].offset == 0:
                return types.name(imps[0].id)
        if t == 0x14:
            tt = types.get(res.id)
            if tt is not None:
                return tt.name
        if t == T_TEXT:
            s = textfile.decode(textfile.read(res, b.e)[:80]).replace('\n', ' ').replace('\t', ' ')
            return ' '.join(s.split())[:60]
        if t == T_STRINGS:
            n = struct.unpack_from(b.e + 'I', res.data(0), 4)[0]
            return f'{n} strings'
        if t == T_VEHICLELIST:
            v = vehiclelist.read(res, b.e)
            return f'{len(v.rows)} vehicles, {len(v.makers)} manufacturers'
        if t == T_WAVE:
            f = eal3.wave_fields(res.data(0), b.e)
            s = f'{f["duration"] / 1000:.2f} s, {f["channels"]} ch'
            if f['kind'] == 'stream':
                s += f', stream {os.path.basename(f["stream_ref"])}'
            elif f['kind'] == 'prefetch':
                s += f', streamed ({res.id & 0xFFFFFFFF}.SPS)'
            return s
    except Exception:
        return ''
    return ''


# ---------------------------------------------------------------------------------------------------------------
# stand-alone sound streams (.SPS)
# ---------------------------------------------------------------------------------------------------------------
_SONGS = {}


def song_titles(path):
    """{SPS file name (upper case): 'Artist - Title'} from the Song objects of UI\\SONGS\\SONGS.BNDL."""
    root = game_root(path)
    if root in _SONGS:
        return _SONGS[root]
    out = {}
    p = os.path.join(root, 'UI', 'SONGS', 'SONGS.BNDL') if root else None
    if p and os.path.isfile(p):
        try:
            from . import genesys
            from .bundle import Bundle
            b = Bundle.open(p)
            types = genesys.TypeDB()
            g = os.path.join(root, 'GLOBALCONFIG.BNDL')
            if os.path.isfile(g):
                types.add_bundle(Bundle.open(g))
            types.add_bundle(b)
            strings = game_strings(p)
            rd = genesys.Reader(types, b.e)
            waves = {r.id: r for r in b.resources if r.type == T_WAVE}
            for r in b.resources:
                imps = r.imports() if r.type == 0x15 else []
                if not imps or types.name(imps[0].id) != 'Song':
                    continue
                wave = next((waves[i.id] for i in imps if i.id in waves), None)
                if wave is None:
                    continue
                try:
                    fields = rd.read_resource(r).fields
                except Exception:
                    continue
                texts = [strings[v] for v in fields.values()
                         if isinstance(v, int) and not isinstance(v, bool) and v in strings]
                f = eal3.wave_fields(wave.data(0), b.e)
                target = f['stream_ref'] if f['kind'] == 'stream' else f'{wave.id & 0xFFFFFFFF}.SPS'
                if texts and target:
                    out.setdefault(os.path.basename(target.replace('\\', '/')).upper(), ' - '.join(texts[:2]))
        except Exception:
            out = {}
    _SONGS[root] = out
    return out


def safe_filename(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .') or 'sound'


def export_sps_folder(src, dst, locator=None, progress=None):
    """Every .SPS file under src as a WAV under dst (same sub folders; songs named 'Artist - Title').
    Returns (written, [(file, error)])."""
    from .spsfile import SpsBundle
    files = []
    for dp, _, fn in os.walk(src):
        files += [os.path.join(dp, f) for f in fn if f.lower().endswith('.sps')]
    files.sort()
    written, errors = 0, []
    for i, p in enumerate(files):
        if progress:
            progress(i, len(files))
        try:
            b = SpsBundle.open(p, locator)
            audio, rate, _, _ = wave_audio(b, b.resources[0], p)
            title = song_titles(p).get(os.path.basename(p).upper())
            stem = os.path.splitext(os.path.basename(p))[0]
            name = safe_filename(f'{title} ({stem})' if title else stem) + '.wav'
            out = os.path.join(dst, os.path.relpath(os.path.dirname(p), src), name)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            write_file(out, eal3.wav_bytes(audio, rate))
            written += 1
        except Exception as ex:
            errors.append((p, str(ex)))
    return written, errors
