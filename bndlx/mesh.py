"""Geometry of Renderable (0x05) and Model (0x51) resources, PC and PS3, for the 3D preview and glTF export.

Renderable chunk 0: sphere (4 f32), ..., 0x12 u16 mesh count, 0x14 u32 offset of the mesh pointer table; each
mesh record is 24 u32 words:
  PC:  w[4] D3D11 topology (4 list, 5 strip), w[7] index count, w[15] index buffer offset, w[21] vertex
       buffer offset, w[22] vertex buffer size; material import at record + 0x20
  PS3: w[6] >> 24 GCM primitive (5 triangles, 6 strip), w[5] index count, w[12] index offset, w[20] vertex
       offset, w[22] vertex size; material import at record + 0x1C
  index / vertex buffers live in the graphics chunk (PC 1, PS3 2); indices u16, 0xFFFF restarts a strip.
The vertex layout comes from the material's shader (import at 0x8) -> its first technique's
VertexDescriptor (import at 0x9C). Shaders, and many materials and textures, live in the game's global
bundles (SHADERS*.BNDL, GLOBALMATERIALDICTIONARY.BNDL, ...), which are loaded from the game folder.
Model chunk 0: u32 offset of the renderable table (imports, one per LOD), renderable count at byte 0x14.
InstanceList (0x50, the static world of a TRK_UNIT bundle): u32 instances offset (0x10), u32 capacity,
u32 count, u32 version; version 3 (retail): 0x60-byte instances: model import at +0, f32 at +4, u32 instance id at
+8, a 4x4 row-vector matrix at +0x20 (version 2, PS3 prototype: 0x50 bytes, matrix at +0x10) (translation in the 4th row; the 4th column is not used). Shared world models are
in HAWAII\\GLOBALRESOURCES.BNDL and HAWAII\\DISTRICT_*.BNDL.
Prop / Dynamic / Compound instance lists (0x218 / 0x204 / 0x216): u32 version, u32 instances offset (0x10),
u32 count, u32 0; 0x60-byte instances: 4x4 matrix at +0, object import at +0x40 (PropObject / WorldObject /
CompoundObject), u32 id at +0x48. The object's model is its import at 0x4 (0x8 in the PS3 prototype's
WorldObject); the record size is 0x60 or 0x50 (taken from the import spacing).
PolygonSoupList (0x60, collision of a TRK_UNIT bundle), PC: f32x3 min, pad, f32x3 max, pad, u32 soup table
offset, u32 bounding box offset, i32 soup count, u32 data size; soup header (0x10): u32 polygon offset, u32
vertex offset, u16, i8 x3 vertex offset in 500 m steps, u8 quad count, u8 polygon count, u8 vertex count;
vertices u16 x3 in 1000/65536 m units; polygons 12 bytes: u32 collision tag, u8 x4 vertex indices (0xFF = a
triangle; quads first, split 0-1-2 / 1-3-2), u8 x4 edge data.
"""
import collections
import glob
import os
import struct
from dataclasses import dataclass

import numpy as np

T_RENDERABLE, T_MODEL, T_MATERIAL, T_INSTANCELIST, T_POLYSOUP = 0x05, 0x51, 0x02, 0x50, 0x60
GLOBAL_BUNDLES = ('SHADERS.BNDL', 'SHADERS0.BNDL', 'SHADERS1.BNDL', 'GLOBALMATERIALDICTIONARY.BNDL',
                  'GLOBALTEXTUREDICTIONARY.BNDL', os.path.join('VEHICLES', 'VEHICLETEX.BNDL'), 'GLOBALEFFECTS.BNDL')
PS3_TYPES = {1: ('s16n', 2), 2: ('f32', 4), 3: ('f16', 2), 4: ('u8n', 1), 5: ('s16', 2), 6: ('cmp', 4), 7: ('u8', 1)}
DXGI = {2: ('f32', 4, 4), 6: ('f32', 3, 4), 16: ('f32', 2, 4), 41: ('f32', 1, 4), 10: ('f16', 4, 2),
        34: ('f16', 2, 2), 13: ('s16n', 4, 2), 11: ('u16n', 4, 2), 37: ('s16n', 2, 2), 35: ('u16n', 2, 2),
        28: ('u8n', 4, 1), 30: ('u8', 4, 1), 31: ('s8n', 4, 1), 14: ('s16', 4, 2), 12: ('u16', 4, 2)}
# colour texture slots in order of preference: Diffuse, road surface colour (DriveableSurface), first blend layer
# colour (PlotPBR / TerrainPBR), cat's eyes
DIFFUSE_SLOTS = (0x0E88, 0x4C95, 0x7703, 0x8F77)
ALPHA_TEST_WORDS = ('1Bit', 'Translucent', 'Tree', 'Foliage', 'Cutout')     # shaders that cut out by alpha
S16N_SCALE = 10.0 / 32767.0          # vehicle positions: s16n x 10 m


class MeshError(ValueError):
    pass


@dataclass
class MeshData:
    pos: np.ndarray            # (N, 3) float32
    uv: np.ndarray             # (N, 2) float32 or None
    tris: np.ndarray           # (M, 3) uint32
    material: int
    texture: int               # diffuse texture id or None
    shader: str = ''
    tint: tuple = (0.72, 0.72, 0.74)
    nrm: np.ndarray = None     # (N, 3) float32, computed on first use
    alpha_test: bool = False   # leaves, fences, decals: texels with alpha < 0.5 are holes
    wire: bool = False         # always drawn as wireframe (collision over the world)

    def normals(self):
        if self.nrm is None:
            self.nrm = self._normals()
        return self.nrm

    def _normals(self):
        p = self.pos.astype(np.float64)
        t = self.tris.astype(np.int64)
        fn = np.cross(p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 0]])
        n = np.zeros_like(p)
        for k in range(3):
            np.add.at(n, t[:, k], fn)
        ln = np.linalg.norm(n, axis=1, keepdims=True)
        return (n / np.maximum(ln, 1e-12)).astype(np.float32)


# ---------------------------------------------------------------------------------------------------------------
# resource lookup
# ---------------------------------------------------------------------------------------------------------------
def _index(b):
    key = (id(b.resources), len(b.resources))
    if getattr(b, '_mesh_idx_key', None) != key:
        b._mesh_idx = {}
        for r in b.resources:
            b._mesh_idx.setdefault(r.id, r)
        b._mesh_idx_key = key
    return b._mesh_idx


class Library:
    """Finds resources in the open bundles first, then in the global bundles of the game folder, then (when a
    locator is set, e.g. the Find names index) in the bundle that holds them, and for world geometry in
    GLOBALRESOURCES and the DISTRICT bundles."""
    EXTRA_LIMIT = 16

    def __init__(self, locator=None):
        self.globals = {}          # game root -> [Bundle]
        self.extra = collections.OrderedDict()     # path -> Bundle or None, opened on demand
        self.locator = locator     # rid -> [bundle paths]

    def open_extra(self, path):
        if path in self.extra:
            self.extra.move_to_end(path)
            return self.extra[path]
        from .bundle import Bundle
        try:
            b = Bundle.open(path)
        except Exception:
            b = None
        self.extra[path] = b
        while len(self.extra) > self.EXTRA_LIMIT:
            self.extra.popitem(last=False)
        return b

    @staticmethod
    def world_paths(root, folder=None):
        """GLOBALRESOURCES and DISTRICT_* bundles of a world folder (the unit's own folder, e.g. SEACREST on the
        PS3 prototype; HAWAII by default)."""
        out = []
        for f in ([folder] if folder else []) + [os.path.join(root, 'HAWAII')]:
            out += [os.path.join(f, 'GLOBALRESOURCES.BNDL')] + sorted(glob.glob(os.path.join(f, 'DISTRICT_*.BNDL')))
        out.append(os.path.join(root, 'GLOBALRESOURCES.BNDL'))
        seen, res = set(), []
        for p in out:
            k = os.path.normcase(p)
            if k not in seen and os.path.isfile(p):
                seen.add(k)
                res.append(p)
        return res

    def global_bundles(self, root):
        if root is None:
            return []
        if root not in self.globals:
            from .bundle import Bundle
            out = []
            for name in GLOBAL_BUNDLES:
                p = os.path.join(root, name)
                if os.path.isfile(p):
                    try:
                        out.append(Bundle.open(p))
                    except Exception:
                        pass
            self.globals[root] = out
        return self.globals[root]

    def constant_names(self, root):
        """{~crc32(name): name} for every identifier in the game's shaders (material constant names)."""
        import re
        import zlib
        key = ('consts', root)
        if key not in self.globals:
            names = {}
            for b in self.global_bundles(root):
                if not os.path.basename(b.path or '').upper().startswith('SHADERS'):
                    continue
                for r in b.resources:
                    if r.type not in (0x08, 0x53):
                        continue
                    for k in range(2):
                        for m in re.finditer(rb'[A-Za-z_][A-Za-z0-9_]{2,63}', r.data(k)):
                            n = m.group(0).decode('latin1')
                            names[(~zlib.crc32(m.group(0))) & 0xFFFFFFFF] = n
            self.globals[key] = names
        return self.globals[key]

    def find(self, rid, bundles, root, deep=False):
        """(bundle, resource) or (None, None). deep: also search the world bundles (slow the first time); a folder
        name searches that world folder first."""
        if not rid:
            return None, None
        for b in list(bundles) + self.global_bundles(root) + [x for x in self.extra.values() if x is not None]:
            r = _index(b).get(rid)
            if r is not None and not r.missing:
                return b, r
        if root is None:
            return None, None
        paths = []
        if self.locator is not None:
            nroot = os.path.normcase(os.path.abspath(root))
            paths = [p for p in (self.locator(rid) or ()) if os.path.normcase(os.path.abspath(p)).startswith(nroot)]
        if deep:
            paths += [p for p in self.world_paths(root, deep if isinstance(deep, str) else None) if p not in paths]
        for p in paths:
            b = self.open_extra(p)
            r = _index(b).get(rid) if b is not None else None
            if r is not None and not r.missing:
                return b, r
        return None, None


def material_info(b, mat):
    """{'shader': id, 'textures': [(slot hash, texture id, sampler id, import offset)],
    'constants': [(hash, (4 floats), value offset)]} of a Material (same layout on PC and PS3)."""
    c = mat.data(0)
    e = b.e
    imps = {i.offset: i.id for i in mat.imports()}
    ntex, nconst = c[0x20], c[0x1C]
    tp, sp, tip = struct.unpack_from(e + '3I', c, 0x24)
    slots = struct.unpack_from(e + f'{ntex}H', c, tp) if ntex else ()
    texs = [(slots[k], imps.get(tip + 4 * k), imps.get(sp + 4 * k), tip + 4 * k) for k in range(ntex)]
    consts = []
    if nconst:
        ip, _, hp, vp = struct.unpack_from(e + '4I', c, 0x0C)
        hs = struct.unpack_from(e + f'{nconst}I', c, hp)
        for k, h in enumerate(hs):
            off = vp + 16 * c[ip + k]
            consts.append((h, struct.unpack_from(e + '4f', c, off), off))
    return {'shader': imps.get(0x8), 'textures': texs, 'constants': consts}


SLOT_NAMES = {0x0E88: 'Diffuse', 0x0D9C: 'Normal', 0x31F2: 'Specular', 0x2837: 'Effects', 0x27D6: 'Crumple',
              0x84E0: 'LightmapLights', 0x192D: 'AO', 0x5C7F: 'SpecAndAO',
              # worked out from the textures (road / terrain blend shaders)
              0x4C95: 'Road colour', 0xFEC8: 'Road markings', 0x0894: 'Blend mask', 0x7703: 'Layer 1 colour',
              0xB210: 'Layer 2 colour', 0xED04: 'Layer 3 colour', 0x17D2: 'Layer normal', 0x3221: 'Layer normal',
              0x8D3B: 'Layer normal', 0x5230: 'Reflection image'}


def game_root(path):
    cur = os.path.dirname(os.path.abspath(path or '.'))
    for _ in range(8):
        if any(os.path.exists(os.path.join(cur, n)) for n in ('GLOBALEFFECTS.BNDL', 'NFS13.exe', 'SHADERS.BNDL')):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


# ---------------------------------------------------------------------------------------------------------------
# vertex layouts
# ---------------------------------------------------------------------------------------------------------------
def vertex_layout(b, vd):
    """[(semantic, kind, count, offset, stride)] with semantic 'pos' / 'uvN' / other."""
    c = vd.data(0)
    els = []
    if b.platform == 'PS3':
        o = 0x10
        uv = 0
        while o + 8 <= len(c) and c[o] != 0xFF:
            t, cnt, off, stride, attr, stream = struct.unpack_from('>BBHHBB', c, o)
            kind = PS3_TYPES.get(t, ('?', 0))[0]
            sem = 'pos' if attr == 0 else (f'uv{attr - 8}' if 8 <= attr <= 13 else f'a{attr}')
            if sem.startswith('uv'):
                uv += 1
            els.append((sem, kind, cnt, off, stride))
            o += 8
    else:
        n = struct.unpack_from('<I', c, 0x0C)[0]
        for k in range(n):
            usage, idx, fmt, off, slot, stride = struct.unpack_from('<BBxxIIII', c, 0x10 + 20 * k)
            kind, cnt, _ = DXGI.get(fmt, ('?', 0, 0))
            sem = 'pos' if usage == 1 else (f'uv{idx}' if 5 <= usage <= 12 else f'u{usage}')
            els.append((sem, kind, cnt, off, stride))
    return els


def _read(raw, e, kind, cnt, off):
    if kind == 'f32':
        return np.ascontiguousarray(raw[:, off:off + 4 * cnt]).view(e + 'f4').astype(np.float32)
    if kind == 'f16':
        return np.ascontiguousarray(raw[:, off:off + 2 * cnt]).view(e + 'f2').astype(np.float32)
    if kind == 's16n':
        return np.ascontiguousarray(raw[:, off:off + 2 * cnt]).view(e + 'i2').astype(np.float32) / 32767.0
    if kind in ('u16n',):
        return np.ascontiguousarray(raw[:, off:off + 2 * cnt]).view(e + 'u2').astype(np.float32) / 65535.0
    if kind == 's16':
        return np.ascontiguousarray(raw[:, off:off + 2 * cnt]).view(e + 'i2').astype(np.float32)
    return None


def strips_to_tris(ib):
    """u16 triangle strips with 0xFFFF restarts -> (M, 3) triangles (degenerates dropped)."""
    ib = np.asarray(ib, np.int64)
    cuts = np.nonzero(ib == 0xFFFF)[0]
    starts = np.concatenate([[0], cuts + 1])
    ends = np.concatenate([cuts, [len(ib)]])
    out = []
    for s, e in zip(starts, ends):
        seg = ib[s:e]
        if len(seg) < 3:
            continue
        a, b, c = seg[:-2], seg[1:-1], seg[2:]
        odd = (np.arange(len(a)) % 2) == 1
        t = np.stack([np.where(odd, b, a), np.where(odd, a, b), c], 1)
        good = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
        out.append(t[good])
    return np.concatenate(out).astype(np.uint32) if out else np.zeros((0, 3), np.uint32)


DIFFUSE_COLOUR = 0x067923B3          # ~crc32('PbrMaterialDiffuseColour')


def material_look(b, mat, shader_name=''):
    """(diffuse texture id or None, RGB tint for untextured display) of a material."""
    tex, tint = None, None
    try:
        c = mat.data(0)
        e = b.e
        ntex, nconst = c[0x20], c[0x1C]
        tp, sp, tip = struct.unpack_from(e + '3I', c, 0x24)
        imps = {i.offset: i.id for i in mat.imports()}
        slots = struct.unpack_from(e + f'{ntex}H', c, tp)
        have = {slots[k]: imps.get(tip + 4 * k) for k in range(ntex) if imps.get(tip + 4 * k)}
        tex = next((have[sl] for sl in DIFFUSE_SLOTS if sl in have), None)
        if nconst:
            ip, _, hp, vp = struct.unpack_from(e + '4I', c, 0x0C)
            hs = struct.unpack_from(e + f'{nconst}I', c, hp)
            for k, h in enumerate(hs):
                if h == DIFFUSE_COLOUR:
                    lin = struct.unpack_from(e + '3f', c, vp + 16 * c[ip + k])
                    tint = tuple(float(min(1.0, max(0.0, x)) ** (1 / 2.2)) for x in lin)
    except Exception:
        pass
    if tint is None:
        low = shader_name.lower()
        tint = (0.16, 0.19, 0.22) if 'glass' in low or 'refraction' in low else             (0.62, 0.64, 0.68) if 'paint' in low else (0.72, 0.72, 0.74)
    return tex, tint


# ---------------------------------------------------------------------------------------------------------------
# decoding
# ---------------------------------------------------------------------------------------------------------------
def decode_renderable(b, res, lib, bundles, root):
    """-> [MeshData] (meshes whose material / shader cannot be found are skipped; raises if none decode)."""
    e = b.e
    c = res.data(0)
    gfx = res.data(1 if b.platform == 'PC' else 2)
    n = struct.unpack_from(e + 'H', c, 0x12)[0]
    table = struct.unpack_from(e + 'I', c, 0x14)[0]
    imps = {i.offset: i.id for i in res.imports()}
    out = []
    problems = []
    for k in range(n):
        rec = struct.unpack_from(e + 'I', c, table + 4 * k)[0]
        w = struct.unpack_from(e + '24I', c, rec)
        if b.platform == 'PC':
            topo, icount, ib_off, vb_off = w[4], w[7], w[15], w[21]
            strip = topo == 5
            mat_id = imps.get(rec + 0x20)
        else:
            prim, icount, ib_off, vb_off = w[6] >> 24, w[5], w[12], w[20]
            strip = prim == 6
            mat_id = imps.get(rec + 0x1C)
        mb, mat = lib.find(mat_id, bundles, root) if mat_id else (None, None)
        if mat is None:
            problems.append(f'material {mat_id or 0:#x} not found')
            continue
        mimps = {i.offset: i.id for i in mat.imports()}
        sb, sh = lib.find(mimps.get(0x8), bundles, root)
        if sh is None:
            problems.append('shader not found')
            continue
        simps = {i.offset: i.id for i in sh.imports()}
        vb_, vd = lib.find(simps.get(0x9C), bundles, root)
        if vd is None:
            problems.append('vertex layout not found')
            continue
        sc = sh.data(0)
        np_ = struct.unpack_from(sb.e + 'I', sc, 8)[0]
        sname = bytes(sc[np_:np_ + 128]).split(b'\0')[0].decode('latin1', 'replace')
        layout = vertex_layout(vb_, vd)
        pos_el = next((x for x in layout if x[0] == 'pos'), None)
        if pos_el is None or not layout:
            problems.append('no positions')
            continue
        stride = layout[0][4]
        ib = np.frombuffer(gfx[ib_off:ib_off + 2 * icount], e + 'u2')
        valid = ib[ib != 0xFFFF]
        if not len(valid):
            continue
        nv = int(valid.max()) + 1
        if vb_off + nv * stride > len(gfx):
            problems.append('vertex buffer out of range')
            continue
        raw = np.frombuffer(gfx[vb_off:vb_off + nv * stride], np.uint8).reshape(nv, stride)
        pos = _read(raw, e, pos_el[1], pos_el[2], pos_el[3])
        if pos is None:
            problems.append(f'position format {pos_el[1]}')
            continue
        pos = pos[:, :3] if pos.shape[1] >= 3 else np.concatenate([pos, np.zeros((nv, 3 - pos.shape[1]), np.float32)], 1)
        if pos_el[1] == 's16n':
            pos = pos * (32767.0 * S16N_SCALE)
        uv_els = sorted((x for x in layout if x[0].startswith('uv')), key=lambda x: x[0])
        uv = None
        if uv_els:
            u = uv_els[0]
            uv = _read(raw, e, u[1], u[2], u[3])
            if uv is not None:
                uv = uv[:, :2]
        tris = strips_to_tris(ib) if strip else ib[:len(ib) // 3 * 3].astype(np.uint32).reshape(-1, 3)
        tex, tint = material_look(mb, mat, sname)
        out.append(MeshData(pos.astype(np.float32), uv, tris, mat_id, tex, sname, tint,
                            alpha_test=any(w in sname for w in ALPHA_TEST_WORDS)))
    if not out and problems:
        raise MeshError('; '.join(sorted(set(problems))) + '. Open the bundles that hold them, or the game folder.')
    return out


def model_renderables(b, res):
    """Renderable ids of a model, LOD 0 first."""
    c = res.data(0)
    table = struct.unpack_from(b.e + 'I', c, 0)[0]
    count = c[0x14]
    imps = {i.offset: i.id for i in res.imports()}
    return [imps.get(table + 4 * k) for k in range(count)]


def decode_resource(b, res, lib, bundles, path, lod=0):
    """Meshes of a Renderable, or of LOD `lod` of a Model."""
    root = game_root(path)
    if res.type == T_MODEL:
        rids = [x for x in model_renderables(b, res) if x]
        if not rids:
            raise MeshError('this model has no renderables')
        rid = rids[min(lod, len(rids) - 1)]
        rb, rr = lib.find(rid, [b] + list(bundles), root)
        if rr is None:
            raise MeshError(f'renderable {rid:#x} not found')
        return decode_renderable(rb, rr, lib, [rb, b] + list(bundles), root), len(rids)
    if res.type == T_INSTANCELIST:
        return decode_instances(b, res, lib, bundles, path, lod)[0], 1
    if res.type == T_POLYSOUP:
        return decode_polysoup(b, res)[0], 1
    if res.type == T_VGS:
        return decode_vgs(b, res, lib, bundles, path, lod)[0], 1
    return decode_renderable(b, res, lib, [b] + list(bundles), root), 1


def _stride(res, default):
    """Instance record size from the spacing of the per-instance imports (0x60 retail, 0x50 in some lists)."""
    offs = sorted(i.offset for i in res.imports())
    for a, b_ in zip(offs, offs[1:]):
        if b_ - a in (0x50, 0x60):
            return b_ - a
    return default


def instance_list(b, res):
    """[(model id, 4x4 row-vector matrix as float64)] of an InstanceList."""
    e = b.e
    c = res.data(0)
    if len(c) < 16:
        return []
    off, cap, n, ver = struct.unpack_from(e + '4I', c, 0)
    stride = _stride(res, 0x60 if ver >= 3 else 0x50)      # version 2 (PS3 prototype): 0x50, no padding
    mo = stride - 0x40
    imps = {i.offset: i.id for i in res.imports()}
    out = []
    for i in range(n):
        o = off + stride * i
        if o + stride > len(c):
            break
        m = np.array(struct.unpack_from(e + '16f', c, o + mo), np.float64).reshape(4, 4)
        out.append((imps.get(o), m))
    return out


OBJECT_LISTS = {0x218: 'props', 0x204: 'dynamic', 0x216: 'compound'}


def object_instances(b, res, lib, look, root, res_path=None):
    """[(model id, 4x4 matrix)] of a Prop / Dynamic / Compound instance list."""
    e = b.e
    c = res.data(0)
    if len(c) < 16:
        return []
    ver, off, n, _ = struct.unpack_from(e + '4I', c, 0)
    imps = {i.offset: i.id for i in res.imports()}
    stride = _stride(res, 0x60)
    models = {}
    out = []
    for i in range(n):
        o = off + stride * i
        if o + stride > len(c):
            break
        m = np.array(struct.unpack_from(e + '16f', c, o), np.float64).reshape(4, 4)
        oid = imps.get(o + 0x40)
        if oid not in models:
            deep = os.path.dirname(os.path.abspath(res_path)) if res_path else True
            ob, obj = lib.find(oid, look, root, deep=deep)
            models[oid] = None
            if obj is not None:
                imps4 = {x.offset: x.id for x in obj.imports()}
                models[oid] = imps4.get(0x4)                   # retail: the model is the import at 0x4
                for io in sorted(imps4)[:3]:                  # PS3 prototype WorldObject: at 0x8
                    mb, mr = lib.find(imps4[io], [ob] + list(look), root, deep=deep)
                    if mr is not None and mr.type == T_MODEL:
                        models[oid] = imps4[io]
                        break
        out.append((models[oid], m))
    return out


def unit_instances(b, res, lib, look, root, objects=True):
    """[(model id, matrix, kind)]: the InstanceList, and (objects=True) the prop / dynamic / compound instances
    of the same bundle."""
    out = [(mid, m, 'world') for mid, m in instance_list(b, res)]
    if objects:
        for r in b.resources:
            kind = OBJECT_LISTS.get(r.type)
            if kind:
                out += [(mid, m, kind) for mid, m in object_instances(b, r, lib, look, root, b.path)]
    return out


def decode_instances(b, res, lib, bundles, path, lod=0, progress=None, objects=True):
    """World-space meshes of every instance of an InstanceList (and of the bundle's props, dynamic and compound
    objects): (meshes, {'instances', 'shown', 'models', 'missing': [model ids], 'kinds': {kind: count}}).
    Each model is decoded once."""
    root = game_root(path)
    look = [b] + list(bundles)
    insts = unit_instances(b, res, lib, look, root, objects)
    if not insts:
        raise MeshError('this instance list is empty (the PS3 prototype world is in SEACREST, not HAWAII)')
    kinds = collections.Counter(k for _, _, k in insts)
    cache = {}
    out = []
    shown = 0
    missing = []
    for i, (mid, m, kind) in enumerate(insts):
        if progress:
            progress(i, len(insts))
        if mid not in cache:
            cache[mid] = None
            mb, mr = lib.find(mid, look, root, deep=os.path.dirname(os.path.abspath(path)))
            if mr is not None and mr.type in (T_MODEL, T_RENDERABLE):
                try:
                    cache[mid] = decode_resource(mb, mr, lib, [mb] + look, path, lod)[0]
                except (MeshError, struct.error, ValueError, IndexError):
                    cache[mid] = None
            if cache[mid] is None:
                missing.append(mid)
        ms = cache[mid]
        if not ms:
            continue
        a, t = m[:3, :3], m[3, :3]
        try:
            na = np.linalg.inv(a).T
        except np.linalg.LinAlgError:
            continue
        shown += 1
        for md in ms:
            pos = (md.pos.astype(np.float64) @ a + t).astype(np.float32)
            nrm = md.normals().astype(np.float64) @ na
            nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
            out.append(MeshData(pos, md.uv, md.tris, md.material, md.texture, md.shader, md.tint,
                                nrm.astype(np.float32), md.alpha_test))
    if not out:
        raise MeshError('none of the instanced models could be found')
    return out, {'instances': len(insts), 'shown': shown, 'models': len(cache), 'missing': missing,
                 'kinds': dict(kinds)}


SOUP_UNIT = 1000.0 / 65536.0


def tag_colour(tag):
    """A stable, fairly bright colour for a collision tag."""
    import colorsys
    h = ((tag * 2654435761) & 0xFFFFFFFF) / 2 ** 32
    return colorsys.hsv_to_rgb(h, 0.55, 0.9)


def decode_polysoup(b, res):
    """Collision of a PolygonSoupList: ([MeshData] one per collision tag, {'soups', 'polygons', 'tags':
    {tag: triangle count}})."""
    e = b.e
    d = bytes(res.data(0))
    if len(d) < 0x30:
        raise MeshError('this collision list is empty')
    po, bo, n, size = struct.unpack_from(e + 'IIiI', d, 0x20)
    if n <= 0:
        raise MeshError('this collision list is empty (the PS3 prototype world is in SEACREST, not HAWAII)')
    offs = struct.unpack_from(e + f'{n}I', d, po)
    poly_t = np.dtype([('tag', e + 'u4'), ('idx', 'u1', 4), ('edge', 'u1', 4)])
    verts, tris, tags = [], [], []
    base = 0
    npolys = 0
    for s in offs:
        pp, vp = struct.unpack_from(e + 'II', d, s)
        ox, oy, oz = struct.unpack_from('3b', d, s + 10)
        nq, npl, nv = d[s + 13], d[s + 14], d[s + 15]
        if not nv or not npl:
            continue
        v = np.frombuffer(d, e + 'u2', 3 * nv, vp).reshape(-1, 3).astype(np.float32) * np.float32(SOUP_UNIT)
        v += np.array([ox, oy, oz], np.float32) * 500.0
        p = np.frombuffer(d, poly_t, npl, pp)
        idx = p['idx'].astype(np.int64)
        q, t = idx[:nq], idx[nq:]
        tri = np.concatenate([q[:, [0, 1, 2]], q[:, [1, 3, 2]], t[:, :3]])
        tag = np.concatenate([p['tag'][:nq], p['tag'][:nq], p['tag'][nq:]])
        ok = (tri < nv).all(1)
        verts.append(v)
        tris.append(tri[ok] + base)
        tags.append(tag[ok])
        base += nv
        npolys += npl
    if not verts:
        raise MeshError('this collision list has no polygons')
    pos = np.concatenate(verts)
    tri = np.concatenate(tris)
    tag = np.concatenate(tags)
    out = []
    counts = {}
    for tg in np.unique(tag):
        sel = tri[tag == tg]
        used, inv = np.unique(sel, return_inverse=True)
        counts[int(tg)] = len(sel)
        out.append(MeshData(pos[used], None, inv.reshape(-1, 3).astype(np.uint32), int(tg), None,
                            f'collision {int(tg):#x}', tag_colour(int(tg))))
    return out, {'soups': n, 'polygons': npolys, 'tags': counts}


T_VGS = 0x106


def _quat_matrix(q):
    x, y, z, w = q
    n = (x * x + y * y + z * z + w * w) ** 0.5 or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w)],
                     [2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w)],
                     [2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)]])


def vgs_layout(b, res):
    """VehicleGraphicsSpec (0x106): (body model id, [wheel dict: name, pos, quat, scale, parts (model ids)]).
    PC: wheels offset at 0x0C, count at byte 0x13, 0x90-byte wheel records (position f32x4, rotation quaternion,
    scale, 18 texture imports, u32 part table offset at +0x78, u16 part count at +0x80, name at +0x82).
    PS3 prototype: wheels offset at 0x10, count at byte 0x17, 0x50-byte records (u32 part table offset, u32 7,
    u32 part count, name at +0x0C, position at +0x20, rotation +0x30, scale +0x40). The body model is the
    import at 0x30; a part table holds one Model import per part (tyre, disc, rim, caliper)."""
    e = b.e
    c = res.data(0)
    imps = {i.offset: i.id for i in res.imports()}
    wheels = []
    if b.platform == 'PC':
        wo, n, stride = struct.unpack_from(e + 'I', c, 0x0C)[0], c[0x13], 0x90
    else:
        wo, n, stride = struct.unpack_from(e + 'I', c, 0x10)[0], c[0x17], 0x50
    for i in range(n):
        o = wo + stride * i
        if o + stride > len(c):
            break
        if b.platform == 'PC':
            pos, quat, scale = (struct.unpack_from(e + '3f', c, o), struct.unpack_from(e + '4f', c, o + 0x10),
                                struct.unpack_from(e + '3f', c, o + 0x20))
            tbl = struct.unpack_from(e + 'I', c, o + 0x78)[0]
            cnt = struct.unpack_from(e + 'H', c, o + 0x80)[0]
            name = bytes(c[o + 0x82:o + 0x90])
        else:
            tbl, _, cnt = struct.unpack_from(e + '3I', c, o)
            name = bytes(c[o + 0x0C:o + 0x20])
            pos, quat, scale = (struct.unpack_from(e + '3f', c, o + 0x20), struct.unpack_from(e + '4f', c, o + 0x30),
                                struct.unpack_from(e + '3f', c, o + 0x40))
        if cnt > 8:
            break
        wheels.append({'name': name.split(b'\0')[0].decode('latin1', 'replace'), 'pos': pos, 'quat': quat,
                       'scale': scale, 'parts': [imps.get(tbl + 4 * k) for k in range(cnt)]})
    return imps.get(0x30), wheels


def decode_vgs(b, res, lib, bundles, path, lod=0):
    """The assembled car: body model + every wheel part at its wheel's place. Right-hand wheels (negative x) use
    the same models mirrored, as the game does. Returns (meshes, {'wheels', 'parts', 'missing'})."""
    root = game_root(path)
    look = [b] + list(bundles)
    body, wheels = vgs_layout(b, res)
    out = []
    missing = []
    cache = {}

    def model(mid):
        if mid not in cache:
            cache[mid] = None
            mb, mr = lib.find(mid, look, root, deep=True)
            if mr is not None and mr.type in (T_MODEL, T_RENDERABLE):
                try:
                    cache[mid] = decode_resource(mb, mr, lib, [mb] + look, path, lod)[0]
                except (MeshError, struct.error, ValueError, IndexError):
                    cache[mid] = None
            if cache[mid] is None:
                missing.append(mid)
        return cache[mid]

    if body:
        out += model(body) or []
    parts = 0
    for w in wheels:
        a = _quat_matrix(w['quat']) * np.array(w['scale'], np.float64)[:, None]
        if w['pos'][0] < 0:
            a = np.diag([-1.0, 1.0, 1.0]) @ a          # right-hand side: mirrored left wheel
        t = np.array(w['pos'], np.float64)
        na = np.linalg.inv(a).T
        for mid in w['parts']:
            for md in model(mid) or []:
                pos = (md.pos.astype(np.float64) @ a + t).astype(np.float32)
                nrm = md.normals().astype(np.float64) @ na
                nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
                tris = md.tris[:, ::-1].copy() if np.linalg.det(a) < 0 else md.tris
                out.append(MeshData(pos, md.uv, tris, md.material, md.texture, md.shader, md.tint,
                                    nrm.astype(np.float32), md.alpha_test))
            parts += 1
    if not out:
        raise MeshError('neither the body nor the wheels of this car could be found')
    return out, {'wheels': len(wheels), 'parts': parts, 'missing': missing}
