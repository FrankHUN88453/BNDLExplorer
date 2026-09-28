"""Import / export operations used by the explorer (no GUI code here)."""
import os
import re
import struct

import numpy as np

from . import colourcube, convert, raster, resfile, textfile
from .localised import StringTable
from .restypes import T_CUBE, T_STRINGS, T_TEXT, T_TEXTURE, name as type_name

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
        if r.type in (T_TEXTURE, T_TEXT, T_STRINGS, T_CUBE):
            try:
                export_native(b, r, sub, texture_format)
            except Exception:
                pass
    return written


def import_folder(b, folder, types, progress=None):
    """Add / replace resources from a folder: .bres files, and files named <id>.dds/.png/... (textures),
    <id>.txt (text files), <id>.csv (strings), <id>.png (colour cubes). Returns (changes, errors);
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
    except Exception:
        return ''
    return ''
