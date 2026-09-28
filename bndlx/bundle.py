"""Reader / writer for Criterion "Bundle 2" (bnd2 version 5) files of Need for Speed: Most Wanted (2012).

PC files are little-endian (platform 1), PS3 files big-endian (platform 2).

Header (0x70 bytes, then optional extra header bytes up to the entry table):
  0x00 'bnd2', u16 version (5), u16 platform
  0x08 u32 debug data offset (ResourceStringTable XML), u32 resource count, u32 entry table offset
  0x14 u32[4] start of each memory chunk's data
  0x24 u32 flags (1 compressed, 2 main memory optimised, 4 graphics memory optimised, 8 debug data, 0x20 multi-stream)
  0x28 u64 root resource id
  0x30 stream names ("GRAPHICS", "SOUND", ...) and padding
Entry (0x48 bytes): u64 id, u32[4] uncompressed size (top 4 bits: log2 alignment), u32[4] stored size, u32[4] offset
  in the chunk's data, u32 import offset, u32 type, u16 import count, u8 flags, u8 stream, u32 padding.
Imports: table at the end of chunk 0 {u64 id, u32 offset, u32 0}; the engine patches the imported resource's
  pointer in at `offset` (UI bundles set bit 31 of the offset).

Layout (checked over every retail PC and PS3 prototype bundle): compressed bundles store each chunk of each
resource as its own zlib stream, back to back in entry order; uncompressed bundles align every resource to the
alignment in its stored size's top bits and every chunk to its largest resource alignment. Saving keeps the
original layout rules, header bytes, debug data and trailing data, so a bundle saved without changes is
byte-identical to the file it came from.
"""
import os
import re
import struct
import zlib

PLATFORM_NAMES = {1: 'PC', 2: 'PS3'}
PLATFORM_IDS = {'PC': 1, 'PS3': 2}
F_COMPRESSED, F_MAIN_OPT, F_GFX_OPT, F_DEBUG, F_MULTISTREAM = 1, 2, 4, 8, 0x20
FLAG_NAMES = [(F_COMPRESSED, 'compressed'), (F_MAIN_OPT, 'main memory optimised'),
              (F_GFX_OPT, 'graphics memory optimised'), (F_DEBUG, 'debug data'), (0x10, '0x10'),
              (F_MULTISTREAM, 'multi-stream')]
SIZE_MASK = 0x0FFFFFFF
GFX_CHUNK = {'PC': 1, 'PS3': 2}      # memory chunk that holds texture pixels / vertex and index buffers


class BundleError(ValueError):
    pass


def align(v, a):
    return (v + a - 1) // a * a if a > 1 else v


def compress(data):
    return zlib.compress(data, 9)


class Import:
    __slots__ = ('id', 'offset', 'raw')

    def __init__(self, rid, raw_offset):
        self.id = rid
        self.raw = raw_offset
        self.offset = raw_offset & 0x7FFFFFFF

    def __repr__(self):
        return f'Import({self.id:#x} @ {self.offset:#x})'


class Resource:
    """One bundle entry. Chunk data is decompressed on first use; unchanged chunks are written back as stored."""

    def __init__(self, rid, type_id, stream=0, flags=0):
        self.id = rid
        self.type = type_id
        self.stream = stream
        self.flags = flags
        self.pad = 0
        self.us_bits = [0, 0, 0, 0]       # top 4 bits of the uncompressed sizes (log2 alignment on PC)
        self.cs_bits = [0, 0, 0, 0]       # top 4 bits of the stored sizes (uncompressed bundles: alignment)
        self.import_offset = 0
        self.import_count = 0
        self.missing = False             # data lies outside a truncated file
        self.name = ''                   # from the debug data, if any
        self.debug_type = ''
        self._stored = [b'', b'', b'', b'']   # bytes as stored in the file (compressed or not)
        self._data = [None, None, None, None]
        self._compressed = False
        self.modified = False

    # -- data ------------------------------------------------------------------------------------------------
    def data(self, k):
        d = self._data[k]
        if d is None:
            s = self._stored[k]
            d = zlib.decompress(s) if (self._compressed and s) else bytes(s)
            self._data[k] = d
        return d

    def chunks(self):
        return [self.data(k) for k in range(4)]

    def size(self, k):
        if self._data[k] is not None:
            return len(self._data[k])
        return self._usize[k] if hasattr(self, '_usize') else len(self.data(k))

    def set_data(self, k, data):
        data = bytes(data)
        if self._data[k] is None or data != self.data(k):
            self._data[k] = data
            self._stored[k] = None
            self.modified = True
            if hasattr(self, '_usize'):
                self._usize[k] = len(data)

    def stored(self, k, compressed):
        s = self._stored[k]
        if s is None or compressed != self._compressed:
            d = self.data(k)
            s = compress(d) if (compressed and d) else d
        return s

    # -- imports -----------------------------------------------------------------------------------------------
    def imports(self):
        if not self.import_count:
            return []
        c0 = self.data(0)
        e = self._e
        out = []
        for i in range(self.import_count):
            o = self.import_offset + 16 * i
            if o + 12 > len(c0):
                break
            rid, off = struct.unpack_from(e + 'QI', c0, o)
            out.append(Import(rid, off))
        return out

    def body(self):
        """Chunk 0 without the import table."""
        c0 = self.data(0)
        return c0[:self.import_offset] if self.import_count else c0

    def set_body(self, body, imports, e=None):
        """Chunk 0 = body + import table [(id, raw_offset)] (16-byte aligned, as the game writes it)."""
        e = e or self._e
        body = bytes(body)
        if imports:
            body += b'\0' * ((-len(body)) % 16)
            self.import_offset = len(body)
            body += b''.join(struct.pack(e + 'QII', rid, off, 0) for rid, off in imports)
        else:
            self.import_offset = 0
        self.import_count = len(imports)
        self.set_data(0, body)
        self.modified = True

    def set_import_id(self, index, new_id):
        c0 = bytearray(self.data(0))
        struct.pack_into(self._e + 'Q', c0, self.import_offset + 16 * index, new_id)
        self.set_data(0, c0)

    def copy(self):
        r = Resource(self.id, self.type, self.stream, self.flags)
        r.pad = self.pad
        r.us_bits, r.cs_bits = list(self.us_bits), list(self.cs_bits)
        r.import_offset, r.import_count = self.import_offset, self.import_count
        r.name, r.debug_type = self.name, self.debug_type
        r._data = list(self._data)
        r._stored = list(self._stored)
        r._compressed = self._compressed
        r._usize = list(getattr(self, '_usize', [0] * 4))
        r._e = self._e
        r.modified = True
        return r

    def __repr__(self):
        return f'Resource({self.id:#x}, type {self.type:#x})'


def _xml_escape(s):
    return s.replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')


def _xml_unescape(s):
    return s.replace('&quot;', '"').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')


class Bundle:
    def __init__(self, platform='PC'):
        self.path = None
        self.platform = platform
        self.e = '<' if platform == 'PC' else '>'
        self.version = 5
        self.flags = F_COMPRESSED
        self.root_id = 0
        self.entries_off = 0x70
        self.header = b'\0' * 0x40          # bytes 0x30 .. entry table
        self.resources = []
        self.debug = None                  # raw debug data (XML + NUL + padding) or None
        self.trailer = b''                 # bytes after the last chunk when there is no debug data
        self.dbg_value = None              # original debug offset if it pointed somewhere unusual
        self.chunk_align = [1, 1, 1, 1]
        self.chunk_gap = [None] * 4
        self.debug_align = 1
        self.import_bit31 = False
        self.original = None
        self._snapshot = None              # (id, type) list as loaded: debug data is kept verbatim while it matches
        self.modified = False
        self.truncated = False

    # -- reading ---------------------------------------------------------------------------------------------
    @classmethod
    def open(cls, path):
        with open(path, 'rb') as f:
            data = f.read()
        b = cls.from_bytes(data)
        b.path = path
        return b

    @classmethod
    def from_bytes(cls, d):
        if len(d) < 0x30 or d[:4] != b'bnd2':
            raise BundleError('not a bnd2 bundle')
        e = '>' if struct.unpack('>H', d[4:6])[0] < 0x100 else '<'
        version, platform = struct.unpack(e + 'HH', d[4:8])
        if version != 5:
            raise BundleError(f'bundle version {version} is not supported (NFS Most Wanted 2012 uses version 5)')
        if platform not in PLATFORM_NAMES:
            raise BundleError(f'unknown platform {platform}')
        b = cls(PLATFORM_NAMES[platform])
        b.e = e
        dbg, n, eo = struct.unpack(e + '3I', d[8:20])
        chunk_off = list(struct.unpack(e + '4I', d[0x14:0x24]))
        b.flags = struct.unpack(e + 'I', d[0x24:0x28])[0]
        b.root_id = struct.unpack(e + 'Q', d[0x28:0x30])[0]
        b.entries_off = eo
        b.header = d[0x30:eo]
        if eo + n * 0x48 > len(d):
            raise BundleError('truncated entry table')
        comp = bool(b.flags & F_COMPRESSED)
        ends = [0, 0, 0, 0]
        for i in range(n):
            o = eo + i * 0x48
            rid = struct.unpack_from(e + 'Q', d, o)[0]
            us = struct.unpack_from(e + '4I', d, o + 0x08)
            cs = struct.unpack_from(e + '4I', d, o + 0x18)
            of = struct.unpack_from(e + '4I', d, o + 0x28)
            imp_off, tid = struct.unpack_from(e + '2I', d, o + 0x38)
            icnt, fl, st = struct.unpack_from(e + 'HBB', d, o + 0x40)
            pad = struct.unpack_from(e + 'I', d, o + 0x44)[0]
            r = Resource(rid, tid, st, fl)
            r.pad = pad
            r._e = e
            r._compressed = comp
            r.us_bits = [x >> 28 for x in us]
            r.cs_bits = [x >> 28 for x in cs]
            r._usize = [x & SIZE_MASK for x in us]
            r.import_offset, r.import_count = imp_off, icnt
            for k in range(4):
                sz = cs[k] & SIZE_MASK
                if sz:
                    start = chunk_off[k] + of[k]
                    if start + sz > len(d):           # truncated file: open read-only, data marked missing
                        r.missing = True
                        b.truncated = True
                        r._usize[k] = 0
                        continue
                    r._stored[k] = d[start:start + sz]
                    ends[k] = max(ends[k], of[k] + sz)
            b.resources.append(r)
        # import offset convention
        for r in b.resources[:64]:
            if r.import_count:
                imps = r.imports()
                if imps:
                    b.import_bit31 = bool(imps[0].raw & 0x80000000)
                    break
        # layout rules of this file
        prev = eo + n * 0x48
        for k in range(4):
            start = chunk_off[k]
            a = 1
            if start != prev:
                a = next((x for x in (2 ** i for i in range(1, 13)) if align(prev, x) == start), None)
                if a is None:
                    b.chunk_gap[k] = d[prev:start] if start > prev else b''
                    a = 1
            b.chunk_align[k] = a
            prev = start + ends[k]
        end = prev
        b.dbg_value = None if dbg == end else dbg
        if dbg == end and dbg < len(d) and d[dbg:dbg + 1] == b'<':
            b.debug = d[dbg:]
            b._parse_debug()
            tail = len(d) - dbg
            xml_len = b.debug.find(b'\0') + 1
            if tail > xml_len and not b.debug[xml_len:].strip(b'\0'):
                b.debug_align = next((x for x in (2 ** i for i in range(4, 13)) if len(d) % x == 0 and tail - xml_len < x
                                      and align(dbg + xml_len, x) == len(d)), 1)
        else:
            b.trailer = d[end:] if end < len(d) else b''
        b.original = d
        b._snapshot = b.layout_key()
        return b

    def _parse_debug(self):
        xml = self.debug.split(b'\0')[0].decode('latin1')
        by_id = {}
        for m in re.finditer(r'<Resource id="([0-9a-fA-F]+)" type="([^"]*)" name="([^"]*)"', xml):
            by_id[int(m.group(1), 16)] = (_xml_unescape(m.group(2)), _xml_unescape(m.group(3)))
        for r in self.resources:
            if r.id in by_id:
                r.debug_type, r.name = by_id[r.id]
        self._xml_style = {
            'short_ids': bool(re.search(r'<Resource id="[0-9a-fA-F]{8}"', xml)),
            'stream': 'streamIndex=' in xml,
        }

    def layout_key(self):
        return tuple((r.id, r.type) for r in self.resources)

    # -- queries ---------------------------------------------------------------------------------------------
    @property
    def compressed(self):
        return bool(self.flags & F_COMPRESSED)

    @property
    def gfx_chunk(self):
        return GFX_CHUNK[self.platform]

    def find(self, rid):
        for r in self.resources:
            if r.id == rid:
                return r
        return None

    def index(self):
        return {r.id: r for r in self.resources}

    def stream_names(self):
        """Stream names stored in the header (0x30..0x70), in stream-index order."""
        h = self.header[4:0x40] if len(self.header) >= 0x40 else b''
        names = [h[i:i + 15].split(b'\0')[0].decode('latin1') for i in range(0, len(h), 15)]
        return [n for n in names if n]

    @property
    def is_modified(self):
        return self.modified or any(r.modified for r in self.resources)

    # -- editing ---------------------------------------------------------------------------------------------
    def new_resource(self, rid, type_id, chunks, import_offset=0, import_count=0, stream=0):
        r = Resource(rid, type_id, stream)
        r._e = self.e
        r._data = [bytes(c) for c in chunks]
        r._stored = [None] * 4
        r.import_offset, r.import_count = import_offset, import_count
        r.us_bits, r.cs_bits = self.default_bits()
        r.modified = True
        return r

    def default_bits(self):
        """Alignment nibbles for a new resource, as the game's own bundles of this platform use them."""
        if self.platform == 'PC':
            us = [4, 4, 0, 0]
            cs = [0, 0, 0, 0] if self.compressed else [4, 7, 7, 7]
        else:
            us = [0, 0, 0, 0] if self.compressed else [4, 0, 7, 0]
            cs = [0, 0, 0, 0] if self.compressed else [4, 7, 7, 7]
        return us, cs

    def add(self, res, replace=True):
        """Insert (or replace) a resource, keeping the entries sorted by (stream, id) when the file was sorted."""
        res._e = self.e
        old = self.find(res.id)
        if old is not None:
            if not replace:
                raise BundleError(f'{res.id:#x} already exists')
            if res.type != old.type or res.stream != old.stream:
                self.resources.remove(old)
            else:
                self.resources[self.resources.index(old)] = res
                self.modified = True
                return res
        keys = [(r.stream, r.id) for r in self.resources]
        if keys == sorted(keys):
            pos = 0
            while pos < len(self.resources) and (self.resources[pos].stream, self.resources[pos].id) < (res.stream, res.id):
                pos += 1
            self.resources.insert(pos, res)
        else:
            self.resources.append(res)
        self.modified = True
        return res

    def remove(self, rid):
        r = self.find(rid)
        if r is not None:
            self.resources.remove(r)
            self.modified = True
        return r

    def change_id(self, old_id, new_id, update_imports=True):
        """Give a resource a new id (and point this bundle's imports of the old id at it)."""
        if self.find(new_id) is not None:
            raise BundleError(f'{new_id:#x} already exists in this bundle')
        r = self.find(old_id)
        if r is None:
            raise BundleError(f'{old_id:#x} not found')
        self.resources.remove(r)
        r.id = new_id
        r.modified = True
        self.add(r)
        n = 0
        if update_imports:
            for o in self.resources:
                for i, imp in enumerate(o.imports()):
                    if imp.id == old_id:
                        o.set_import_id(i, new_id)
                        n += 1
        if self.root_id == old_id:
            self.root_id = new_id
        self.modified = True
        return n

    # -- writing ---------------------------------------------------------------------------------------------
    def _debug_bytes(self, start):
        if self.debug is None:
            return None
        if self.layout_key() == self._snapshot:
            return self.debug
        style = getattr(self, '_xml_style', {'short_ids': False, 'stream': False})
        lines = ['<ResourceStringTable>']
        for r in self.resources:
            rid = f'{r.id:08x}' if (style['short_ids'] and r.id < 1 << 32) else f'{r.id:016x}'
            extra = f' streamIndex="{r.stream}"' if style['stream'] else ''
            lines.append(f'\t<Resource id="{rid}" type="{_xml_escape(r.debug_type or "")}" '
                         f'name="{_xml_escape(r.name or "")}"{extra}/>')
        lines.append('</ResourceStringTable>')
        out = ('\n'.join(lines) + '\n').encode('latin1', 'replace') + b'\0'
        if self.debug_align > 1:
            out += b'\0' * (align(start + len(out), self.debug_align) - start - len(out))
        return out

    def to_bytes(self):
        e = self.e
        comp = self.compressed
        res = self.resources
        n = len(res)
        stored = [[r.stored(k, comp) for k in range(4)] for r in res]
        for r in res:
            r._e = e
        chunk_off = [0] * 4
        offsets = [[0] * 4 for _ in res]
        parts = []
        cur = self.entries_off + 0x48 * n
        for k in range(4):
            a = self.chunk_align[k]
            if not comp:
                for i, r in enumerate(res):
                    if stored[i][k]:
                        a = max(a, 1 << r.cs_bits[k])
            gap = self.chunk_gap[k]
            if gap is not None:
                start = cur + len(gap)
                parts.append(gap)
            else:
                start = align(cur, a)
                parts.append(b'\0' * (start - cur))
            chunk_off[k] = start
            pos = 0
            for i, r in enumerate(res):
                blob = stored[i][k]
                if not blob:
                    continue
                if not comp:
                    p2 = align(pos, 1 << r.cs_bits[k])
                    parts.append(b'\0' * (p2 - pos))
                    pos = p2
                offsets[i][k] = pos
                parts.append(blob)
                pos += len(blob)
            cur = start + pos
        dbg = cur
        tail = self._debug_bytes(cur)
        if tail is None:
            tail = self.trailer
            if self.dbg_value is not None and not self.is_modified:
                dbg = self.dbg_value
        head = b'bnd2' + struct.pack(e + 'HHIII', self.version, PLATFORM_IDS[self.platform], dbg, n, self.entries_off)
        head += struct.pack(e + '4I', *chunk_off) + struct.pack(e + 'IQ', self.flags, self.root_id)
        head += self.header
        assert len(head) == self.entries_off
        ents = []
        for i, r in enumerate(res):
            us, cs = [], []
            for k in range(4):
                size = len(r.data(k)) if r._data[k] is not None or r._stored[k] is None else r._usize[k]
                sz = len(stored[i][k])
                us.append((r.us_bits[k] << 28) | size if (size or not comp) else 0)
                cs.append(((r.cs_bits[k] << 28) | sz) if not comp else sz)
            ents.append(struct.pack(e + 'Q4I4I4IIIHBBI', r.id, *us, *cs, *offsets[i], r.import_offset, r.type,
                                    r.import_count, r.flags, r.stream, r.pad))
        return b''.join([head] + ents + parts + [tail])

    def save(self, path=None, verify=True):
        """Write the bundle (to a temporary file first; read back and compared before it replaces anything)."""
        path = path or self.path
        if self.truncated:
            raise BundleError('the file is truncated (some resources have no data); it can be viewed but not saved')
        data = self.to_bytes()
        tmp = path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(data)
        try:
            if verify:
                nb = Bundle.from_bytes(data)
                if len(nb.resources) != len(self.resources):
                    raise BundleError('resource count differs after writing')
                for a, b in zip(self.resources, nb.resources):
                    if a.id != b.id or a.type != b.type or a.chunks() != b.chunks():
                        raise BundleError(f'{a.id:#x} differs after writing')
        except Exception:
            os.remove(tmp)
            raise
        os.replace(tmp, path)
        # the saved file is the new baseline
        fresh = Bundle.from_bytes(data)
        fresh.path = path
        self.__dict__.update(fresh.__dict__)
        return path
