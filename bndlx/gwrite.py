"""GenesysObject writer: a Node tree (genesys.Reader) back to chunk 0, in the retail serialisation order.

  * a struct is written in place: Genesys.Object header {type import, 0, schema hash} + its inline fields
  * after it, field order: strings (chars + NUL), the complete payload of inline sub-structs and of single
    pointed-to structs; then this struct's arrays: contiguous elements (then each element's payload), pointer
    arrays (u32 array, then each object with its payload)
  * a null array is pointer 0, an allocated but empty one points at the current (aligned) write position
  * references to other resources are 0 + an import {id, offset | 0x80000000} (every retail object sets bit 31)
  * the body is padded to 16, the import table follows and ends the chunk

Retail objects of most types come back byte for byte (tests/test_core.py measures it); some do not (e.g.
EnvironmentKeyframe keeps its payloads in another order), so callers re-write the unchanged object first and
only use the writer when that gives the same bytes (`roundtrips`). Ported from NFSMW2012_ProtoHUD tools/gwrite.py
(PC only there) to both byte orders."""
import struct

from .genesys import GENESYS_OBJECT, HANDLE_KIND, STRINGBASE, Node, Reader, Ref, fixed_pointers


class WriteError(ValueError):
    pass


class Writer:
    def __init__(self, types, e, obj_align=4, sort_arrays=True):
        self.T = types.view(e) if hasattr(types, 'view') else types
        self.e = e
        self.obj_align = obj_align
        self.sort_arrays = sort_arrays
        self.buf = bytearray()
        self.imports = []

    # -- low level --------------------------------------------------------------------------------------------
    def alloc(self, size, align):
        self.buf += b'\0' * ((-len(self.buf)) % align)
        off = len(self.buf)
        self.buf += b'\0' * size
        return off

    def put(self, off, fmt, *vals):
        struct.pack_into(self.e + fmt, self.buf, off, *vals)

    def align_of(self, t):
        return max(4, 1 << t.align) if t else 4

    def imp(self, rid, off):
        self.imports.append((rid, off))

    def type_of(self, tid):
        t = self.T.get(tid)
        if t is None:
            raise WriteError(f'Genesys type {tid:#x} is not open')
        return t

    # -- entry ------------------------------------------------------------------------------------------------
    def write(self, node):
        """-> (chunk 0 bytes, import offset, import count)."""
        self.buf = bytearray()
        self.imports = []
        t = self.type_of(node.type)
        off = self.alloc(t.size, self.align_of(t))
        self.fill_struct(node, off)
        self.payload(node, off)
        self.buf += b'\0' * ((-len(self.buf)) % 16)
        imp_off = len(self.buf)
        for rid, o in sorted(self.imports, key=lambda x: x[1]):
            self.buf += struct.pack(self.e + 'QII', rid, o | 0x80000000, 0)
        return bytes(self.buf), imp_off, len(self.imports)

    # -- struct -----------------------------------------------------------------------------------------------
    def fill_struct(self, node, off):
        t = self.type_of(node.type)
        raw = getattr(node, 'raw', None)
        if raw is not None and len(raw) == t.size:
            # start from the bytes read: data between / under fields the schema does not describe stays; what is
            # written again (pointers, strings, references) is cleared first
            self.buf[off:off + t.size] = raw
            self.clear_refs(t, off)
        if t.kind == 7 and (t.base or node.type == GENESYS_OBJECT):
            self.imp(node.type, off)
            self.put(off + 8, 'I', t.schema[0])
        counts = {}
        for f in t.fields:                     # the count of each array is its length
            if f.flags & 8 and f.flags & 1:
                cf = t.count_field(f)
                v = node.fields.get(f.name_hash)
                if cf is not None and not isinstance(v, Ref):
                    counts[cf.name_hash] = len(v) if isinstance(v, list) else 0
        for f in t.fields:
            v = counts.get(f.name_hash, node.fields.get(f.name_hash))
            self.fill_field(f, off + f.offset, v)

    def clear_refs(self, t, off):
        if t.base or t.id == GENESYS_OBJECT:
            self.buf[off:off + 4] = b'\0' * 4          # the type import
        for f in t.fields:
            o = off + f.offset
            ft = self.T.get(f.type_id)
            if f.flags & 1 or fixed_pointers(f):
                n = f.count if fixed_pointers(f) else 1
                self.buf[o:o + 4 * n] = b'\0' * (4 * n)
            elif ft is not None and (ft.id == STRINGBASE or ft.kind == HANDLE_KIND):
                n = f.count if f.flags & 2 and f.count > 1 else 1
                self.buf[o:o + ft.size * n] = b'\0' * (ft.size * n)

    def fill_field(self, f, o, v):
        ft = self.T.get(f.type_id)
        if f.flags & 1:
            if isinstance(v, Ref):
                self.imp(v.id, o)
            return                             # pointers are set when the payload is written
        if fixed_pointers(f):
            for i, x in enumerate(v or []):
                if isinstance(x, Ref):
                    self.imp(x.id, o + 4 * i)
            return
        self.fill_value(f, ft, o, v)

    def fill_value(self, f, ft, o, v):
        if ft is None:
            self.prim(o, f.size, None, v)
            return
        if ft.id == STRINGBASE:
            return                             # set with the payload
        if ft.kind == HANDLE_KIND:
            if isinstance(v, Ref):
                self.imp(v.id, o)
            return
        many = f.flags & 2 and f.count > 1
        if ft.kind == 6:
            sub = ft.fields[0]
            st = self.T.get(sub.type_id)
            for i, x in enumerate(v if many else [v]):
                eo = o + i * ft.size
                vals = list(x if x is not None else [0] * sub.count)
                if st is not None and st.size == 1:
                    self.buf[eo:eo + sub.count] = bytes(int(y) & 0xFF for y in vals)
                else:
                    self.put(eo, f'{sub.count}f', *[float(y) for y in vals])
            return
        if ft.kind == 7:
            if isinstance(v, list):
                for i, x in enumerate(v):
                    self.fill_struct(x, o + i * ft.size)
            elif isinstance(v, Node):
                self.fill_struct(v, o)
            return
        if many:
            for i, x in enumerate(v or [0] * f.count):
                self.prim(o + i * ft.size, ft.size, ft, x)
            return
        self.prim(o, ft.size, ft, v)

    def prim(self, o, size, ft, v):
        if v is None:
            v = 0
        kind = ft.kind if ft else None
        if isinstance(v, (bytes, bytearray)):
            self.buf[o:o + size] = bytes(v[:size]).ljust(size, b'\0')
        elif kind == 2 and size == 4:
            self.put(o, 'f', float(v))
        elif kind == 2 and size == 8:
            self.put(o, 'd', float(v))
        elif size == 1:
            self.put(o, 'b' if kind == 0 else 'B', int(v))
        elif size == 2:
            self.put(o, 'h' if kind == 0 else 'H', int(v))
        elif size == 4:
            self.put(o, 'i' if kind == 0 else 'I', int(v))
        elif size == 8:
            self.put(o, 'q' if kind == 0 else 'Q', int(v))

    # -- payload ----------------------------------------------------------------------------------------------
    def payload(self, node, off):
        t = self.type_of(node.type)
        for f in t.fields:
            v = node.fields.get(f.name_hash)
            o = off + f.offset
            ft = self.T.get(f.type_id)
            if f.flags & 1:
                if not f.flags & 8:
                    self.pointer_payload(f, ft, o, v)
                continue
            if fixed_pointers(f):
                for i, x in enumerate(v or []):
                    if isinstance(x, Node):
                        xt = self.type_of(x.type)
                        p = self.alloc(xt.size, max(self.obj_align, self.align_of(xt)))
                        self.put(o + 4 * i, 'I', p)
                        self.fill_struct(x, p)
                        self.payload(x, p)
                continue
            if ft is None:
                continue
            many = f.flags & 2 and f.count > 1
            if ft.id == STRINGBASE:
                for i, s in enumerate(v if many else [v]):
                    self.string(o + i * ft.size, s)
            elif ft.kind == 7 and isinstance(v, Node):
                self.payload(v, o)
            elif ft.kind == 7 and isinstance(v, list):
                for i, x in enumerate(v):
                    self.payload(x, o + i * ft.size)
        arrays = [f for f in t.fields if f.flags & 1 and f.flags & 8]
        if self.sort_arrays:
            arrays.sort(key=lambda f: -self.elem_align(f))      # larger element alignment first
        for f in arrays:
            self.pointer_payload(f, self.T.get(f.type_id), off + f.offset, node.fields.get(f.name_hash))

    def elem_align(self, f):
        if f.flags & 16:
            return 4
        ft = self.T.get(f.type_id)
        return 1 << ft.align if ft else 4

    def string(self, o, s):
        raw = (s or '').encode('latin1') + b'\0'
        p = self.alloc(len(raw), 1)
        self.buf[p:p + len(raw)] = raw
        self.put(o, 'II', p - o, len(raw))

    def pointer_payload(self, f, ft, o, v):
        if isinstance(v, Ref) or v is None:
            return
        if f.flags & 8:
            if not v:
                pad = (-len(self.buf)) % (self.align_of(ft) if ft and not f.flags & 16 else 4)
                self.put(o, 'I', len(self.buf) + pad)
                return
            if f.flags & 16:
                arr = self.alloc(4 * len(v), 4)
                self.put(o, 'I', arr)
                for i, x in enumerate(v):
                    if isinstance(x, Ref):
                        self.imp(x.id, arr + 4 * i)
                        continue
                    xt = self.type_of(x.type)
                    p = self.alloc(xt.size, max(self.obj_align, self.align_of(xt)))
                    self.put(arr + 4 * i, 'I', p)
                    self.fill_struct(x, p)
                    self.payload(x, p)
                return
            stride = ft.size if ft else f.size
            arr = self.alloc(stride * len(v), self.align_of(ft) if ft else 4)
            self.put(o, 'I', arr)
            for i, x in enumerate(v):
                self.fill_value(f, ft, arr + i * stride, x)
            for i, x in enumerate(v):
                eo = arr + i * stride
                if ft is not None and ft.id == STRINGBASE:
                    self.string(eo, x)
                elif isinstance(x, Node):
                    self.payload(x, eo)
            return
        if isinstance(v, Node):
            xt = self.type_of(v.type)
            p = self.alloc(xt.size, self.align_of(xt))
            self.put(o, 'I', p)
            self.fill_struct(v, p)
            self.payload(v, p)


def write(types, e, node, **kw):
    return Writer(types, e, **kw).write(node)


def roundtrips(types, res, e):
    """(node, True) when the resource re-written unchanged gives its exact bytes (the writer can be used on it)."""
    node = Reader(types, e).read_resource(res)
    try:
        data, off, cnt = write(types, e, node)
    except (WriteError, struct.error, TypeError, ValueError, AttributeError):
        return node, False
    return node, (data == bytes(res.data(0)) and off == res.import_offset and cnt == res.import_count)


def apply(res, types, e, node):
    """Put a (changed) node tree into the resource."""
    data, off, cnt = write(types, e, node)
    res.set_data(0, data)
    res.import_offset, res.import_count = off, cnt
