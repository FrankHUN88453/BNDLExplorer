"""Genesys data: schemas (GenesysType, resource type 0x14) and objects (GenesysObject, 0x15).

GenesysType (chunk 0):
  0x00 u8 version (2), u8 kind, u16 field count
  0x04 u32 base type (patched by an import), u32 0, u32 fields offset
  0x10 u32 schema hash, u32 schema id, u32 size, u32 log2 alignment
  0x20 u32 offset of {u32 name offset, u32 0}; the name (ASCII) follows
  fields: 0x1C bytes each {u32 type (import), u32 type hash, u32 count-field pointer, u32 name hash, u32 size,
          u32 offset, u16 count, u8 alignment, u8 flags}; enum values use the same layout (offset = value)
Kinds: 0 int, 1 uint, 2 float, 3 bool, 4 char, 5 enum, 6 vector, 7 struct, 8 resource handle.

GenesysObject: {u32 type (import), u32 0, u32 schema hash} + the type's fields at their offsets (base included).
Field flags: 0 inline, 2 inline array (count), 1 pointer to one struct, 8|1 pointer to an array (count in the
field the descriptor points at), 16|8|1 pointer to an array of pointers to objects (each with its header).
StringBase = {u32 relative offset of the characters, u32 length incl. NUL}; Handle = 8 bytes set by an import.

Names are hashed; names of 4 characters or fewer are stored as ASCII (big-endian packed).
"""
import re
import struct
from dataclasses import dataclass, field

import numpy as np

KIND = {0: 'int', 1: 'uint', 2: 'float', 3: 'bool', 4: 'char', 5: 'enum', 6: 'vector', 7: 'struct', 8: 'handle'}
STRINGBASE = 0x40404301
GENESYS_OBJECT = 0x2EDB25E7
HANDLE_KIND = 8
T_TYPE, T_OBJECT = 0x14, 0x15


@dataclass
class Field:
    type_id: int
    type_hash: int
    count_ptr: int
    name_hash: int
    size: int
    offset: int
    count: int
    align: int
    flags: int
    index: int = 0


@dataclass
class GType:
    id: int
    kind: int
    name: str
    size: int
    align: int
    schema: tuple
    fields_off: int = 0
    base: int = 0
    fields: list = field(default_factory=list)

    def count_field(self, f):
        """The field holding the element count of array field f."""
        i = (f.count_ptr - self.fields_off) // 0x1C
        return self.fields[i] if 0 <= i < len(self.fields) else None


def hash_name(h):
    """Short names are stored as ASCII instead of a hash."""
    b = h.to_bytes(4, 'big').lstrip(b'\0')
    if len(b) >= 2 and re.fullmatch(rb'[A-Za-z][A-Za-z0-9_]*', b):
        return b.decode('ascii')
    return None


def parse_type(res, e):
    c = res.data(0)
    imports = {i.offset: i.id for i in res.imports()}
    kind = c[1]
    n = struct.unpack_from(e + 'H', c, 2)[0]
    fields_off = struct.unpack_from(e + 'I', c, 0x0C)[0]
    schema = struct.unpack_from(e + 'II', c, 0x10)
    size, al = struct.unpack_from(e + 'II', c, 0x18)
    npp = struct.unpack_from(e + 'I', c, 0x20)[0]
    name = ''
    if 0x24 <= npp and npp + 4 <= len(c):
        np_ = struct.unpack_from(e + 'I', c, npp)[0]
        if np_ < len(c):
            name = c[np_:].split(b'\0')[0].decode('latin1')
    t = GType(res.id, kind, name, size, al & 0xFF, schema, fields_off, imports.get(4, 0))
    if kind in (5, 6, 7):
        for i in range(n):
            o = fields_off + i * 0x1C
            if o + 0x1C > len(c):
                break
            _, th, cp, nh, sz, off = struct.unpack_from(e + '6I', c, o)
            cnt, a, fl = struct.unpack_from(e + 'HBB', c, o + 0x18)
            t.fields.append(Field(imports.get(o, 0), th, cp, nh, sz, off, cnt, a, fl, i))
    return t


class TypeDB:
    """GenesysTypes of every open bundle (objects often use types stored in other bundles)."""

    def __init__(self):
        self.types = {}

    def add_bundle(self, b):
        for r in b.resources:
            if r.type == T_TYPE and r.id not in self.types and not r.missing:
                try:
                    self.types[r.id] = parse_type(r, b.e)
                except (struct.error, IndexError, UnicodeDecodeError):
                    pass

    def get(self, tid):
        return self.types.get(tid)

    def __contains__(self, tid):
        return tid in self.types

    def __getitem__(self, tid):
        return self.types[tid]

    def name(self, tid):
        t = self.types.get(tid)
        if t is None:
            return f'{tid:#x}'
        return (t.name or f'{tid:#x}').replace('Genesys.Gen.', '')


# ---------------------------------------------------------------------------------------------------------------
# object reading
# ---------------------------------------------------------------------------------------------------------------
class Ref:
    """Reference to another resource (through an import)."""
    __slots__ = ('id',)

    def __init__(self, rid):
        self.id = rid

    def __repr__(self):
        return f'Ref({self.id:#x})'


class Node:
    """A struct instance: fields {name_hash: value}; locs {name_hash: (absolute offset, Field)}."""
    __slots__ = ('type', 'fields', 'offset', 'locs')

    def __init__(self, type_id, offset):
        self.type = type_id
        self.fields = {}
        self.offset = offset
        self.locs = {}


class MissingType(Exception):
    pass


class Reader:
    def __init__(self, types, e):
        self.T = types
        self.e = e

    def read_resource(self, res):
        self.data = res.data(0)
        self.imports = {i.offset: i.id for i in res.imports()}
        return self.read_object(0)

    def u32(self, o):
        return struct.unpack_from(self.e + 'I', self.data, o)[0]

    def count_of(self, t, f, base):
        cf = t.count_field(f)
        if cf is None:
            return 0
        return self.prim(base + cf.offset, cf.size, self.T.get(cf.type_id))

    def prim(self, o, size, ft):
        e = self.e
        kind = ft.kind if ft else None
        d = self.data
        if kind == 2 and size == 4:
            return struct.unpack_from(e + 'f', d, o)[0]
        if kind == 2 and size == 8:
            return struct.unpack_from(e + 'd', d, o)[0]
        if size == 1:
            return struct.unpack_from('b' if kind == 0 else 'B', d, o)[0]
        if size == 2:
            return struct.unpack_from(e + ('h' if kind == 0 else 'H'), d, o)[0]
        if size == 4:
            return struct.unpack_from(e + ('i' if kind == 0 else 'I'), d, o)[0]
        if size == 8:
            return struct.unpack_from(e + ('q' if kind == 0 else 'Q'), d, o)[0]
        return bytes(d[o:o + size])

    def read_object(self, o):
        tid = self.imports.get(o)
        if tid is None:
            raise ValueError(f'no type import at {o:#x}')
        return self.read_struct(tid, o)

    def read_struct(self, tid, o):
        t = self.T.get(tid)
        if t is None:
            raise MissingType(tid)
        node = Node(tid, o)
        for f in t.fields:
            node.locs[f.name_hash] = (o + f.offset, f)
            node.fields[f.name_hash] = self.read_field(t, f, o)
        return node

    def read_field(self, t, f, base):
        o = base + f.offset
        ft = self.T.get(f.type_id)
        if f.flags & 1:
            ptr = self.u32(o)
            imp = self.imports.get(o)
            if f.flags & 8:
                n = self.count_of(t, f, base)
                if imp is not None:
                    return Ref(imp)
                if ptr == 0 and n == 0:
                    return None
                if f.flags & 16:
                    out = []
                    for i in range(n):
                        po = ptr + i * 4
                        if po in self.imports:
                            out.append(Ref(self.imports[po]))
                        else:
                            out.append(self.read_object(self.u32(po)))
                    return out
                stride = ft.size if ft else f.size
                return [self.read_elem(f, ft, ptr + i * stride, single=True) for i in range(n)]
            if imp is not None:
                return Ref(imp)
            if ptr == 0:
                return None
            return self.read_elem(f, ft, ptr, single=True)
        return self.read_elem(f, ft, o)

    def read_elem(self, f, ft, o, single=False):
        if ft is None:
            return self.prim(o, f.size, None)
        many = (not single) and f.flags & 2 and f.count > 1
        if ft.id == STRINGBASE:
            rel, ln = struct.unpack_from(self.e + 'II', self.data, o)
            return bytes(self.data[o + rel:o + rel + max(ln - 1, 0)]).decode('latin1')
        if ft.kind == HANDLE_KIND:
            imp = self.imports.get(o)
            return Ref(imp) if imp is not None else None
        if ft.kind == 6:
            vals = [self.vector(ft, o + i * ft.size) for i in range(f.count if many else 1)]
            return vals if many else vals[0]
        if ft.kind == 7:
            if many:
                return [self.read_struct(self.imports.get(o + i * ft.size, ft.id), o + i * ft.size)
                        for i in range(f.count)]
            if o in self.imports and (self.imports[o] == ft.id or ft.base):
                return self.read_struct(self.imports[o], o)
            return self.read_struct(ft.id, o)
        if many:
            return [self.prim(o + i * ft.size, ft.size, ft) for i in range(f.count)]
        return self.prim(o, ft.size, ft)

    def vector(self, ft, o):
        sub = ft.fields[0]
        n = sub.count
        st = self.T.get(sub.type_id)
        if st is not None and st.size == 1:
            return list(self.data[o:o + n])
        return list(struct.unpack_from(self.e + f'{n}f', self.data, o))


def leaf_kind(types, f):
    """How a field can be edited in place: 'float', 'vector', 'int', 'uint', 'bool', 'enum', 'string' or None."""
    ft = types.get(f.type_id)
    if f.flags & 1:
        return None
    if ft is None:
        return 'uint' if f.size in (1, 2, 4) and not (f.flags & 2 and f.count > 1) else None
    if ft.id == STRINGBASE:
        return 'string'
    if ft.kind in (7, 8):
        return None
    if f.flags & 2 and f.count > 1:
        return 'vector' if ft.kind == 2 and ft.size == 4 else None
    if ft.kind == 2 and ft.size == 4:
        return 'float'
    if ft.kind == 6:
        st = types.get(ft.fields[0].type_id) if ft.fields else None
        return 'vector' if (st is None or st.size == 4) else 'bytes'
    if ft.kind == 3:
        return 'bool'
    if ft.kind == 5:
        return 'enum'
    if ft.kind in (0, 1, 4) and ft.size in (1, 2, 4, 8):
        return 'int' if ft.kind == 0 else 'uint'
    return None


# ---------------------------------------------------------------------------------------------------------------
# byte-order conversion
# ---------------------------------------------------------------------------------------------------------------
class Layout:
    """Every scalar of a resource: {offset: size}. Bytes not covered (except zeros) are 'unknown'."""

    def __init__(self, n):
        self.scalars = {}
        self.covered = bytearray(n)       # 0 unknown, 1 bytes kept as they are, 2 part of a scalar
        self.conflicts = 0                # overlapping scalars: the layout is not understood

    def scalar(self, o, size):
        if self.scalars.get(o) == size:
            return
        end = min(o + size, len(self.covered))
        if 2 in self.covered[o:end]:
            self.conflicts += 1
            return
        if size in (2, 4, 8) and o + size <= len(self.covered):
            self.scalars[o] = size
            self.covered[o:end] = b'\2' * (end - o)
        else:
            self.cover(o, size)

    def cover(self, o, size):
        for i in range(o, min(o + size, len(self.covered))):
            if not self.covered[i]:
                self.covered[i] = 1

    def unknown(self, data):
        """Non-zero bytes outside every known field (+1000 per overlap)."""
        cov = np.frombuffer(bytes(self.covered), np.uint8)
        dat = np.frombuffer(bytes(data[:len(cov)]), np.uint8)
        return int(np.count_nonzero((cov == 0) & (dat != 0))) + 1000 * self.conflicts

    def apply(self, data):
        out = bytearray(data)
        for o, size in self.scalars.items():
            out[o:o + size] = out[o:o + size][::-1]
        return bytes(out)


def import_table(layout, res):
    for i in range(res.import_count):
        o = res.import_offset + 16 * i
        layout.scalar(o, 8)
        layout.scalar(o + 8, 4)
        layout.cover(o + 12, 4)


def type_layout(res, e):
    c = res.data(0)
    L = Layout(len(c))
    L.cover(0, 2)
    L.scalar(2, 2)
    for o in range(4, 0x24, 4):
        L.scalar(o, 4)
    n = struct.unpack_from(e + 'H', c, 2)[0]
    fields_off = struct.unpack_from(e + 'I', c, 0x0C)[0]
    npp = struct.unpack_from(e + 'I', c, 0x20)[0]
    if 0x24 <= npp and npp + 8 <= len(c):
        L.scalar(npp, 4)
        L.scalar(npp + 4, 4)
        np_ = struct.unpack_from(e + 'I', c, npp)[0]
        if np_ < len(c):
            L.cover(np_, len(c[np_:].split(b'\0')[0]) + 1)
    if c[1] in (5, 6, 7):
        for i in range(n):
            o = fields_off + i * 0x1C
            for k in range(6):
                L.scalar(o + 4 * k, 4)
            L.scalar(o + 0x18, 2)
            L.cover(o + 0x1A, 2)
    import_table(L, res)
    return L


class ObjectLayout:
    def __init__(self, types, e):
        self.T = types
        self.e = e

    def build(self, res):
        self.data = res.data(0)
        self.imports = {i.offset: i.id for i in res.imports()}
        self.L = Layout(len(self.data))
        self.seen = set()
        import_table(self.L, res)
        self.object(0)
        return self.L

    def u32(self, o):
        return struct.unpack_from(self.e + 'I', self.data, o)[0]

    def object(self, o):
        tid = self.imports.get(o)
        if tid is None:
            raise ValueError(f'no type import at {o:#x}')
        self.struct(tid, o)

    def struct(self, tid, o):
        if (tid, o) in self.seen:
            return
        self.seen.add((tid, o))
        t = self.T.get(tid)
        if t is None:
            raise MissingType(tid)
        if t.base or tid == GENESYS_OBJECT:
            for k in range(3):
                self.L.scalar(o + 4 * k, 4)
        for f in t.fields:
            self.field(t, f, o)

    def field(self, t, f, base):
        o = base + f.offset
        ft = self.T.get(f.type_id)
        L = self.L
        if f.flags & 1:
            L.scalar(o, 4)
            if o in self.imports:
                return
            ptr = self.u32(o)
            if f.flags & 8:
                cf = t.count_field(f)
                n = 0
                if cf is not None:
                    cft = self.T.get(cf.type_id)
                    n = Reader.prim(self, base + cf.offset, cf.size, cft)
                if not n:
                    return
                if f.flags & 16:
                    for i in range(n):
                        po = ptr + 4 * i
                        L.scalar(po, 4)
                        if po not in self.imports:
                            self.object(self.u32(po))
                    return
                stride = ft.size if ft else f.size
                for i in range(n):
                    self.elem(f, ft, ptr + i * stride, single=True)
                return
            if ptr:
                self.elem(f, ft, ptr, single=True)
            return
        self.elem(f, ft, o)

    def elem(self, f, ft, o, single=False):
        L = self.L
        count = f.count if (not single and f.flags & 2 and f.count > 1) else 1
        if ft is None:
            L.scalar(o, f.size)
            return
        for i in range(count):
            eo = o + i * ft.size
            if ft.id == STRINGBASE:
                L.scalar(eo, 4)
                L.scalar(eo + 4, 4)
                rel, ln = struct.unpack_from(self.e + 'II', self.data, eo)
                L.cover(eo + rel, ln)
            elif ft.kind == HANDLE_KIND:
                L.scalar(eo, ft.size or 8)
            elif ft.kind == 6:
                sub = ft.fields[0]
                st = self.T.get(sub.type_id)
                es = st.size if st is not None else 4
                for j in range(sub.count):
                    L.scalar(eo + j * es, es)
            elif ft.kind == 7:
                tid = self.imports.get(eo)
                if tid is not None and (tid == ft.id or ft.base):
                    self.struct(tid, eo)
                else:
                    self.struct(ft.id, eo)
            else:
                L.scalar(eo, ft.size)


def swap_type(res, e):
    """Chunk 0 of a GenesysType in the other byte order, plus the number of unexplained non-zero bytes."""
    L = type_layout(res, e)
    c = res.data(0)
    return L.apply(c), L.unknown(c)


def swap_object(res, types, e):
    L = ObjectLayout(types, e).build(res)
    c = res.data(0)
    return L.apply(c), L.unknown(c)
