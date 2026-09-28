"""PS3 <-> PC conversion of resources and whole bundles.

Converted types (the others hold platform-specific GPU data: shaders, vertex layouts, meshes, ...):
  Texture         header rewritten; DXT / BC blocks copied; uncompressed data (un)swizzled; RGBA16F byte swapped
  GenesysType     byte order of every header / field descriptor / import (layout checked: no unknown bytes)
  GenesysObject   byte order of every field, walked with the schema (needs the object's types to be loaded)
  ColourCube      PC linear B, G, R <-> PS3 Morton R, G, B
  TextFile        length prefix
  LocalisedText   table + UTF-16 byte order
Note: the PS3 prototype and the retail game do not share every schema; converted Genesys data only works in
the other build if its types are the same there.
"""
import struct

from . import colourcube, genesys, raster, textfile
from .bundle import Bundle, F_MAIN_OPT, Resource
from .localised import StringTable
from .restypes import T_CUBE, T_GOBJECT, T_GTYPE, T_STRINGS, T_TEXT, T_TEXTURE, name

CONVERTIBLE = {T_TEXTURE, T_GTYPE, T_GOBJECT, T_CUBE, T_TEXT, T_STRINGS}
E = {'PC': '<', 'PS3': '>'}


class ConvertError(ValueError):
    pass


def _gfx(platform):
    return 1 if platform == 'PC' else 2


def convert_resource(res, src, dst, types=None, strict=True):
    """A new Resource with `res` converted from platform `src` to `dst` (same id / type / stream)."""
    if src == dst:
        return res.copy()
    es, ed = E[src], E[dst]
    t = res.type
    imp_off, imp_cnt = res.import_offset, res.import_count
    chunks = [b'', b'', b'', b'']
    if t == T_TEXTURE:
        hdr, pix = raster.convert(res, src, dst)
        chunks[0], chunks[_gfx(dst)] = hdr, pix
    elif t == T_GTYPE:
        out, unknown = genesys.swap_type(res, es)
        if unknown and strict:
            raise ConvertError(f'{unknown} bytes of this type are not understood')
        chunks[0] = out
    elif t == T_GOBJECT:
        if types is None:
            raise ConvertError('no Genesys types loaded')
        try:
            out, unknown = genesys.swap_object(res, types, es)
        except genesys.MissingType as m:
            raise ConvertError(f'Genesys type {m.args[0]:#x} is not in any open bundle') from None
        if unknown and strict:
            raise ConvertError(f'{unknown} bytes of this object are not understood')
        chunks[0] = out
    elif t == T_CUBE:
        c = res.data(0)
        lut = colourcube.ps3_to_lut(c) if src == 'PS3' else colourcube.pc_to_lut(c)
        w, h = struct.unpack_from(es + 'II', c, 0)
        head = struct.pack(ed + 'II', w, h) + bytes(c[8:16])
        chunks[0] = (colourcube.lut_to_pc if dst == 'PC' else colourcube.lut_to_ps3)(lut, head)
        imp_off = imp_cnt = 0
    elif t == T_TEXT:
        chunks[0] = textfile.swap(res, es, ed)
        imp_off = imp_cnt = 0
    elif t == T_STRINGS:
        chunks[0] = StringTable.read(res, es).build(ed)
        imp_off = imp_cnt = 0
    else:
        raise ConvertError(f'{name(t)} holds {src}-specific data and cannot be converted')
    if t in (T_TEXTURE,) and res.import_count:
        raise ConvertError('textures with imports are not supported')
    r = Resource(res.id, t, res.stream, res.flags)
    r._e = ed
    r._data = chunks
    r._stored = [None] * 4
    r.import_offset, r.import_count = imp_off, imp_cnt
    r.name, r.debug_type = res.name, res.debug_type
    r.modified = True
    return r


def convert_bundle(src, dst_platform, types=None, strict=True, progress=None):
    """New Bundle for `dst_platform` with every convertible resource. Returns (bundle, report)."""
    out = Bundle(dst_platform)
    out.version = src.version
    out.flags = (src.flags & ~F_MAIN_OPT) if dst_platform == 'PC' else (src.flags | F_MAIN_OPT)
    out.root_id = src.root_id
    out.entries_off = 0x70
    out.header = src.header[:0x40].ljust(0x40, b'\0')
    out.import_bit31 = src.import_bit31
    if src.debug is not None:
        out.debug = b'<'
        out._xml_style = getattr(src, '_xml_style', {'short_ids': False, 'stream': False})
    report = {'converted': 0, 'skipped': {}, 'failed': []}
    n = len(src.resources)
    for i, r in enumerate(src.resources):
        if progress and i % 50 == 0:
            progress(i, n)
        if r.missing:
            report['failed'].append((r.id, r.type, 'no data in the source file'))
            continue
        if r.type not in CONVERTIBLE:
            report['skipped'][r.type] = report['skipped'].get(r.type, 0) + 1
            continue
        try:
            c = convert_resource(r, src.platform, dst_platform, types, strict)
        except Exception as ex:
            report['failed'].append((r.id, r.type, str(ex)))
            continue
        c.us_bits, c.cs_bits = out.default_bits()
        out.resources.append(c)
        report['converted'] += 1
    return out, report


def report_text(report):
    lines = [f'{report["converted"]} resource(s) converted.']
    if report['skipped']:
        s = ', '.join(f'{name(t)} x{n}' for t, n in sorted(report['skipped'].items()))
        lines.append(f'Left out (platform-specific data): {s}.')
    if report['failed']:
        lines.append(f'{len(report["failed"])} failed:')
        for rid, t, msg in report['failed'][:12]:
            lines.append(f'  {rid:#x} {name(t)}: {msg}')
        if len(report['failed']) > 12:
            lines.append(f'  ... and {len(report["failed"]) - 12} more')
    return '\n'.join(lines)
