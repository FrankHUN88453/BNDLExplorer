"""Raw resource files (.dat) as Bundle Manager and similar tools export them: chunk 0 of one resource exactly as the
game loads it (the import table at its end), named after the resource id as its bytes, e.g. 06_57_07_00.dat for
0x00075706 (little-endian). The type is not in the file: it is guessed (Genesys type / object, or the type of the
resource with that id in the bundle) and can be chosen.

Import table: 16-byte entries {u64 id, u32 offset (bit 31 set in the game's files), u32 0} in the platform's byte
order at the end of chunk 0. Files byte-swapped by hand from PS3 data often have the two halves of each 64-bit id
wrongly swapped (0x00000001_xxxxxxxx instead of 0x01000000_xxxxxxxx): that is repaired, a table that is not where
the game keeps it (the file size not a multiple of 16) is reported."""
import os
import re
import struct

from .bundle import Resource
from .restypes import name as type_name

T_TEXTURE, T_GTYPE, T_GOBJECT = 0x01, 0x14, 0x15
NAME_BYTES = re.compile(r'^([0-9a-fA-F]{2})(?:[_ -]([0-9a-fA-F]{2})){3,7}$')
CHOICES = (0x15, 0x14, 0x01, 0x02, 0x05, 0x51, 0x70, 0x81, 0x105)
NO_IMPORTS = (0x01, 0x52, 0x70, 0x81, 0x201)      # types that never import (sound / texture data can look like a table)


class DatError(ValueError):
    pass


def id_from_name(path, b=None):
    """The resource id of a .dat file name (XX_XX_XX_XX bytes, little-endian, or a hex id); the full 64-bit id of
    a resource of `b` with those low 32 bits, else 0x01000000_xxxxxxxx for a GameChanger number."""
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r'_(texture|body|header|chunk\d)$', '', stem, flags=re.I)
    m = NAME_BYTES.match(stem)
    if m:
        parts = re.split(r'[_ -]', stem)
        rid = int.from_bytes(bytes(int(x, 16) for x in parts), 'little')
    else:
        h = stem[2:] if stem.lower().startswith('0x') else stem
        if not re.fullmatch(r'[0-9a-fA-F]{1,16}', h):
            return None
        rid = int(h, 16)
    if rid >> 32:
        return rid
    if b is not None:
        same = [r.id for r in b.resources if r.id & 0xFFFFFFFF == rid]
        if same:
            return same[0]
    return (0x01000000 << 32) | rid if rid < 0x01000000 else rid


def guess_type(data, rid, b=None):
    if b is not None:
        r = b.find(rid)
        if r is not None:
            return r.type
    if len(data) >= 0x24 and data[0] == 2 and data[1] <= 8:
        return T_GTYPE
    if len(data) >= 12 and data[4:8] == b'\0\0\0\0':
        return T_GOBJECT
    return None


def import_table(data, e):
    """(import offset, count, repaired, problem) of the table at the end of chunk 0."""
    n = len(data)
    if n % 16:
        return n, 0, 0, (f'its size ({n} bytes) is not a multiple of 16, so an import table cannot end it where the '
                         'game expects: the file looks damaged (often a PS3 resource byte-swapped by hand)')
    # in every retail resource the offsets go up and the body holds 0 where an import is patched in: that tells
    # the table from body data that happens to look like an entry (28-42 % of resources without it)
    count = repaired = 0
    nxt = top = None
    while 16 * (count + 1) <= n - 16:
        o = n - 16 * (count + 1)
        rid, off, pad = struct.unpack_from(e + 'QII', data, o)
        off &= 0x7FFFFFFF
        if not rid or pad or off % 4 or off + 4 > o or (nxt is not None and off > nxt):
            break
        if (top if top is not None else off) + 4 > o or struct.unpack_from(e + 'I', data, off)[0]:
            break                                        # every import must point into the body before the table
        nxt = off
        top = off if top is None else top
        count += 1
        if off == 0:                                     # nothing comes before offset 0 (a Genesys object's type)
            break
    if count:
        start = n - 16 * count
        for i in range(count):
            rid, = struct.unpack_from(e + 'Q', data, start + 16 * i)
            if rid >> 32 == 1:
                repaired += 1
    return n - 16 * count, count, repaired, None


def load(path, b, rid=None, type_id=None):
    """-> (Resource for bundle b, [notes])."""
    with open(path, 'rb') as f:
        data = bytearray(f.read())
    notes = []
    rid = rid if rid is not None else id_from_name(path, b)
    if rid is None:
        raise DatError(f'{os.path.basename(path)}: the file name is not a resource id (XX_XX_XX_XX.dat)')
    type_id = type_id if type_id is not None else guess_type(bytes(data), rid, b)
    if type_id is None:
        raise DatError(f'{os.path.basename(path)}: choose its resource type')
    e = b.e
    if type_id == T_GTYPE and len(data) >= 0x18:
        own = struct.unpack_from(e + 'I', data, 0x14)[0]   # a Genesys.Gen type keeps its GameChanger id
        if 0 < own < 0x01000000 and own != rid & 0xFFFFFFFF:
            notes.append(f'the file name says {rid & 0xFFFFFFFF:#x} but the type\'s own id is {own:#x}: using '
                         f'{own:#x}')
            rid = (0x01000000 << 32) | own
    if type_id in NO_IMPORTS:
        off, cnt, repaired, problem = len(data), 0, 0, None
    else:
        off, cnt, repaired, problem = import_table(bytes(data), e)
    if problem:
        notes.append(problem)
    if repaired:
        for i in range(cnt):
            o = off + 16 * i
            rid_i, = struct.unpack_from(e + 'Q', data, o)
            if rid_i >> 32 == 1:
                struct.pack_into(e + 'Q', data, o, (0x01000000 << 32) | (rid_i & 0xFFFFFFFF))
        notes.append(f'{repaired} import id(s) had their two halves swapped (00000001_ instead of 01000000_): '
                     'repaired')
    r = Resource(rid, type_id, 0)
    r._e = e
    r._data = [bytes(data), b'', b'', b'']
    r._stored = [None] * 4
    r.import_offset, r.import_count = off, cnt
    r.us_bits, r.cs_bits = b.default_bits()
    r.name, r.debug_type = '', ''
    r.modified = True
    base = os.path.splitext(path)[0]
    for k, suf in ((1, '_texture'), (1, '_chunk1'), (2, '_chunk2'), (3, '_chunk3')):
        p = base + suf + '.dat'
        if os.path.isfile(p):
            with open(p, 'rb') as f:
                r._data[k] = f.read()
            notes.append(f'chunk {k} from {os.path.basename(p)}')
    return r, notes


def check(b, types, res):
    """Problems of an imported Genesys type / object read with the bundle's schemas ([] when it reads)."""
    from . import genesys
    try:
        if res.type == T_GTYPE:
            t = genesys.parse_type(res, b.e)
            if not t.name and t.kind in (6, 7):
                return ['the Genesys type has no readable name: it is probably not PC data']
        elif res.type == T_GOBJECT:
            genesys.Reader(types, b.e).read_resource(res)
            imp = {i.offset: i.id for i in res.imports()}
            t = types.get(imp.get(0))
            have = struct.unpack_from(b.e + 'I', res.data(0), 8)[0]
            if t is not None and t.schema[0] != have:
                return [f'its schema hash {have:08x} is not that of its type {t.name or hex(t.id)} '
                        f'({t.schema[0]:08x}): the data does not match the type (wrong conversion?)']
    except genesys.MissingType as m:
        return [f'its Genesys type {m.args[0]:#x} is not open: import the type too']
    except Exception as ex:
        return [f'it does not read as a {type_name(res.type)} of this platform: {ex}']
    return []
