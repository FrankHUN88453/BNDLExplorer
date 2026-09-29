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
"""
import os
import struct
from dataclasses import dataclass

import numpy as np

T_RENDERABLE, T_MODEL, T_MATERIAL = 0x05, 0x51, 0x02
GLOBAL_BUNDLES = ('SHADERS.BNDL', 'SHADERS0.BNDL', 'SHADERS1.BNDL', 'GLOBALMATERIALDICTIONARY.BNDL',
                  'GLOBALTEXTUREDICTIONARY.BNDL', os.path.join('VEHICLES', 'VEHICLETEX.BNDL'), 'GLOBALEFFECTS.BNDL')
PS3_TYPES = {1: ('s16n', 2), 2: ('f32', 4), 3: ('f16', 2), 4: ('u8n', 1), 5: ('s16', 2), 6: ('cmp', 4), 7: ('u8', 1)}
DXGI = {2: ('f32', 4, 4), 6: ('f32', 3, 4), 16: ('f32', 2, 4), 41: ('f32', 1, 4), 10: ('f16', 4, 2),
        34: ('f16', 2, 2), 13: ('s16n', 4, 2), 11: ('u16n', 4, 2), 37: ('s16n', 2, 2), 35: ('u16n', 2, 2),
        28: ('u8n', 4, 1), 30: ('u8', 4, 1), 31: ('s8n', 4, 1), 14: ('s16', 4, 2), 12: ('u16', 4, 2)}
DIFFUSE_SLOTS = (0x0E88,)
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

    def normals(self):
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
    """Finds resources in the open bundles first, then in the global bundles of the game folder."""

    def __init__(self):
        self.globals = {}          # game root -> [Bundle]

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

    def find(self, rid, bundles, root):
        for b in list(bundles) + self.global_bundles(root):
            r = _index(b).get(rid)
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
              0x84E0: 'LightmapLights', 0x192D: 'AO', 0x5C7F: 'SpecAndAO'}


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
        for k in range(ntex):
            if slots[k] in DIFFUSE_SLOTS and imps.get(tip + 4 * k):
                tex = imps[tip + 4 * k]
                break
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
        out.append(MeshData(pos.astype(np.float32), uv, tris, mat_id, tex, sname, tint))
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
        return decode_renderable(rb, rr, lib, [b] + list(bundles), root), len(rids)
    return decode_renderable(b, res, lib, [b] + list(bundles), root), 1
