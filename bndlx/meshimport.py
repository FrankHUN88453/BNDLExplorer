"""Model export names and FBX import: edited geometry written back into Renderable meshes.

Exported objects are named R<renderable id>_<mesh index> (further instances of the same mesh, e.g. the other
wheels, get ~<n>). On import every object with such a name replaces that mesh: positions and the first UV set
are encoded in the mesh's own vertex format, normals too when the format has them in a readable element, and
every other attribute (tangents, colours, damage weights, extra UV sets) is taken from the nearest original
vertex. Triangles are written as triangle strips like the game's own; the Renderable's buffers are laid out again."""
import re
import struct

import numpy as np

from . import mesh

NAME_RE = re.compile(r'R([0-9A-Fa-f]{16})_(\d+)(?:~(\d+))?')
MAX_VERTICES = 0xFFFF               # 16-bit indices, 0xFFFF is the strip restart


class MeshImportError(ValueError):
    pass


def export_names(meshes, fallback='mesh'):
    """Object names for exported meshes: R<id>_<k> for the first use of a renderable mesh, ~n for the others."""
    seen = {}
    out = []
    for i, m in enumerate(meshes):
        if m.src is None:
            out.append(f'{fallback}_{i}')
            continue
        n = seen.get(m.src, 0)
        seen[m.src] = n + 1
        base = f'R{m.src[0]:016X}_{m.src[1]}'
        out.append(base if n == 0 else f'{base}~{n}')
    return out


def parse_name(name):
    """(renderable id, mesh index, instance) of an exported object name, or None. Suffixes that 3D programs add
    (.001, _mesh) are ignored."""
    m = NAME_RE.search(name or '')
    if not m:
        return None
    return int(m.group(1), 16), int(m.group(2)), int(m.group(3) or 0)


# ---------------------------------------------------------------------------------------------------------------
# vertex encoding (the inverse of mesh._read)
# ---------------------------------------------------------------------------------------------------------------
def _encode(raw, e, kind, cnt, off, values):
    """Write values (n, <= cnt) into the element at `off` of the (n, stride) byte rows; components not given keep
    their bytes. Returns False for a format that cannot be written."""
    k = values.shape[1]
    if kind == 'f32':
        data = values.astype(e + 'f4')
        size = 4
    elif kind == 'f16':
        data = values.astype(e + 'f2')
        size = 2
    elif kind == 's16n':
        data = np.clip(np.round(values * 32767.0), -32767, 32767).astype(e + 'i2')
        size = 2
    elif kind == 'u16n':
        data = np.clip(np.round(values * 65535.0), 0, 65535).astype(e + 'u2')
        size = 2
    elif kind == 's16':
        data = np.clip(np.round(values), -32768, 32767).astype(e + 'i2')
        size = 2
    else:
        return False
    k = min(k, cnt)
    raw[:, off:off + size * k] = np.ascontiguousarray(data[:, :k]).view(np.uint8).reshape(len(raw), size * k)
    return True


def _read(raw, e, el):
    return mesh._read(raw, e, el[1], el[2], el[3])


def _unit(v):
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)


def _cmp_read(raw, e, off):
    """PS3 CELL_GCM_VERTEX_CMP: x 11 bits, y 11 bits, z 10 bits, signed normalized."""
    v = np.ascontiguousarray(raw[:, off:off + 4]).view(e + 'u4')[:, 0].astype(np.int64)
    x = ((v & 0x7FF) ^ 0x400) - 0x400
    y = (((v >> 11) & 0x7FF) ^ 0x400) - 0x400
    z = (((v >> 22) & 0x3FF) ^ 0x200) - 0x200
    return np.stack([x / 1023.0, y / 1023.0, z / 511.0], 1)


def _cmp_write(raw, e, off, v):
    x = np.clip(np.round(v[:, 0] * 1023.0), -1023, 1023).astype(np.int64) & 0x7FF
    y = np.clip(np.round(v[:, 1] * 1023.0), -1023, 1023).astype(np.int64) & 0x7FF
    z = np.clip(np.round(v[:, 2] * 511.0), -511, 511).astype(np.int64) & 0x3FF
    word = (x | (y << 11) | (z << 22)).astype(e + 'u4')
    raw[:, off:off + 4] = word.view(np.uint8).reshape(len(raw), 4)


def _q_normal(q):
    """Normal of a tangent-frame quaternion (x, y, z, w): its z axis, flipped where w < 0 (the handedness)."""
    x, y, z, w = q.T
    zax = np.stack([2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)], 1)
    return zax * np.where(w < 0, -1.0, 1.0)[:, None]


def _rotation(n0, n1):
    """Unit quaternions (x, y, z, w) turning each n0 onto n1 the shortest way."""
    d = np.sum(n0 * n1, 1)
    axis = np.cross(n0, n1)
    q = np.concatenate([axis, (1.0 + d)[:, None]], 1)
    back = d < -0.9999                               # opposite: half a turn about any perpendicular axis
    if back.any():
        perp = np.cross(n0[back], np.array([1.0, 0.0, 0.0]))
        weak = np.linalg.norm(perp, axis=1) < 1e-3
        perp[weak] = np.cross(n0[back][weak], np.array([0.0, 1.0, 0.0]))
        q[back] = np.concatenate([_unit(perp), np.zeros((back.sum(), 1))], 1)
    return _unit(q)


def _qmul(a, b):
    av, aw, bv, bw = a[:, :3], a[:, 3:], b[:, :3], b[:, 3:]
    return np.concatenate([aw * bv + bw * av + np.cross(av, bv), aw * bw - np.sum(av * bv, 1, keepdims=True)], 1)


def _qrotate(q, v):
    """Vectors v turned by the unit quaternions q."""
    qv, w = q[:, :3], q[:, 3:]
    t = 2 * np.cross(qv, v)
    return v + w * t + np.cross(qv, t)


def normal_frames(raw, e, layout, normals):
    """How the normal is stored in this vertex format, found from the data (its direction matches the mesh's own
    normals): ('vec', element, sign) for a plain vector, ('quat', element) for a tangent-frame quaternion (PC
    cars), ('cmp', element, [tangent elements]) for PS3 11:11:10 normals; None when no element matches."""
    best, score = None, 0.85
    for el in layout:
        sem, kind, cnt, off = el[0], el[1], el[2], el[3]
        if sem in ('pos', 'uv0'):
            continue
        cands = []
        if kind == 'cmp':
            cands.append(('cmp', _cmp_read(raw, e, off)))
        elif kind in ('f32', 'f16', 's16n') and cnt >= 3:
            v = _read(raw, e, el).astype(np.float64)
            if cnt == 4 and np.abs(np.linalg.norm(v, axis=1) - 1).mean() < 0.02:
                cands.append(('quat', _q_normal(_unit(v))))
            cands.append(('vec', v[:, :3]))
        for how, v in cands:
            ln = np.linalg.norm(v, axis=1)
            if np.abs(ln - 1).mean() > 0.1:
                continue
            dot = np.mean(np.sum(_unit(v) * normals, 1))
            if abs(dot) > score and (how != 'quat' or dot > 0):
                score = abs(dot)
                best = (how, el, 1.0 if dot > 0 else -1.0)
    if best is None:
        return None
    if best[0] == 'cmp':                              # the tangent / binormal next to it turn with the normal
        others = [x for x in layout if x[1] == 'cmp' and x is not best[1]]
        return ('cmp', best[1], others)
    return best


def frame_normals(raw, e, frame):
    """The unit normals stored in the rows of `raw` in the way `frame` (from normal_frames) says."""
    how, el = frame[0], frame[1]
    if how == 'cmp':
        return _unit(_cmp_read(raw, e, el[3]))
    v = _read(raw, e, el).astype(np.float64)
    if how == 'quat':
        return _unit(_q_normal(_unit(v)))
    return _unit(frame[2] * v[:, :3])


def write_normals(raw, e, frame, normals):
    """Store new normals in the rows of `raw` (copied from the nearest original vertices), turning tangent frames
    with them."""
    how, el = frame[0], frame[1]
    off = el[3]
    if how == 'vec':
        _encode(raw, e, el[1], el[2], off, frame[2] * normals)
    elif how == 'quat':
        q = _unit(_read(raw, e, el).astype(np.float64))
        hand = np.where(q[:, 3] < 0, -1.0, 1.0)
        q = _unit(_qmul(_rotation(_q_normal(q), normals), q))
        q[np.sign(q[:, 3]) != hand] *= -1               # keep the handedness in the sign of w
        small = np.abs(q[:, 3]) < 1.0 / 16384
        q[small, 3] = hand[small] / 16384
        _encode(raw, e, el[1], el[2], off, _unit(q))
    elif how == 'cmp':
        n0 = _unit(_cmp_read(raw, e, off))
        r = _rotation(n0, normals)
        _cmp_write(raw, e, off, normals)
        for t in frame[2]:
            _cmp_write(raw, e, t[3], _qrotate(r, _cmp_read(raw, e, t[3])))


def _nearest(old_pos, old_nrm, old_uv, pos, nrm, uv, old_extra=(), extra=(), ids=None):
    """Index of the closest original vertex for every new one (position first; normal and UV decide between
    vertices at the same place). Unmoved vertices are found through a hash of their position, the rest by
    distance."""
    old_pos = old_pos.astype(np.float64)

    def score(i, rows):
        d = ((pos[rows] - old_pos[i]) ** 2).sum(1)
        if nrm is not None and old_nrm is not None:
            d += 1e-4 * ((nrm[rows] - old_nrm[i]) ** 2).sum(1)
        if uv is not None and old_uv is not None:
            d += 1e-4 * ((uv[rows] - old_uv[i]) ** 2).sum(1)
        for a, b in zip(extra, old_extra):
            d += 1e-5 * ((a[rows] - b[i]) ** 2).sum(1)
        return d

    def key(p):
        g = np.round(p * 1e4).astype(np.int64)
        return (g[:, 0] * 73856093) ^ (g[:, 1] * 19349663) ^ (g[:, 2] * 83492791)

    if ids is not None:                             # the exported vertex, where it is still in place
        ok = (ids >= 0) & (ids < len(old_pos))
        rows = np.nonzero(ok)[0]
        i = ids[rows]
        good = ((pos[rows] - old_pos[i]) ** 2).sum(1) < 1e-8          # still in place (0.1 mm)
        if uv is not None and old_uv is not None:
            good &= ((uv[rows] - old_uv[i]) ** 2).sum(1) < 1e-6
        out = np.full(len(pos), -1, np.int64)
        out[rows[good]] = i[good]
    else:
        out = None
    oh = key(old_pos)
    order = np.argsort(oh, kind='stable')
    sh = oh[order]
    nh = key(pos)
    lo = np.searchsorted(sh, nh, 'left')
    cnt = np.searchsorted(sh, nh, 'right') - lo
    fixed = out if out is not None else np.full(len(pos), -1, np.int64)
    out = fixed.copy()
    best = np.where(fixed >= 0, 0.0, np.inf)
    for j in range(int(cnt.max()) if len(cnt) else 0):
        rows = np.nonzero((cnt > j) & (fixed < 0))[0]
        i = order[lo[rows] + j]
        d = score(i, rows)
        better = d < best[rows]
        out[rows[better]] = i[better]
        best[rows[better]] = d[better]
    far = np.nonzero((out < 0) | (best > 1e-6))[0]
    step = max(1, 2_000_000 // max(len(old_pos), 1))
    op = old_pos.astype(np.float32)
    for s in range(0, len(far), step):
        rows = far[s:s + step]
        d = ((pos[rows, None, :].astype(np.float32) - op[None, :, :]) ** 2).sum(2)
        if nrm is not None and old_nrm is not None:
            d += 1e-4 * ((nrm[rows, None, :] - old_nrm[None, :, :]) ** 2).sum(2)
        if uv is not None and old_uv is not None:
            d += 1e-4 * ((uv[rows, None, :] - old_uv[None, :, :]) ** 2).sum(2)
        out[rows] = d.argmin(1)
    return out


def _snap_normals(nrm, ids):
    """3D programs store normals with small per-corner differences; corners of the same game vertex whose normals
    are within 2.5 degrees of its first corner's get exactly that normal, so the vertex is not split."""
    nrm = nrm.copy()
    valid = np.nonzero(ids >= 0)[0]
    if not len(valid):
        return nrm
    _, first, inv = np.unique(ids[valid], return_index=True, return_inverse=True)
    ref = nrm[valid[first]][inv.reshape(-1)]
    close = np.sum(_unit(nrm[valid]) * _unit(ref), 1) > 0.999
    nrm[valid[close]] = ref[close]
    return nrm


def _weld(pos, uv, nrm, extra=(), ids=None):
    """Unique vertices of per-corner data: (pos, uv, nrm, [extra UV sets], ids, corner -> vertex). Corners that
    came from different game vertices stay apart."""
    cols = [np.round(pos * 1e5)] + ([ids[:, None].astype(np.float64)] if ids is not None else [])
    if uv is not None:
        cols.append(np.round(uv * 1e5))
    if nrm is not None:
        cols.append(np.round(nrm * 1e3))
    cols += [np.round(u * 1e5) for u in extra]
    _, first, inv = np.unique(np.concatenate(cols, 1), axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    return (pos[first], uv[first] if uv is not None else None, nrm[first] if nrm is not None else None,
            [u[first] for u in extra], ids[first] if ids is not None else None, inv)


def tris_to_strips(tris):
    """u16 triangle strips with 0xFFFF restarts (what every mesh of the game uses) drawing exactly `tris` with
    the same winding: greedy strips, each continued by the triangle on the edge the strip's winding needs."""
    tris = [tuple(int(v) for v in t) for t in tris if len({int(v) for v in t}) == 3]
    edges = {}
    for i, (a, b, c) in enumerate(tris):
        for e in ((a, b), (b, c), (c, a)):
            edges.setdefault(e, []).append(i)
    used = bytearray(len(tris))
    out = []
    for i, t in enumerate(tris):
        if used[i]:
            continue
        used[i] = 1
        strip = list(t)
        while True:
            x, y = strip[-2], strip[-1]
            want = (x, y) if (len(strip) - 2) % 2 == 0 else (y, x)     # winding of the next triangle
            nxt = next((j for j in edges.get(want, ()) if not used[j]), None)
            if nxt is None:
                break
            used[nxt] = 1
            strip.append(next(v for v in tris[nxt] if v != x and v != y))
        if out:
            out.append(0xFFFF)
        out += strip
    return np.array(out, np.uint16)


def _align(n, a):
    return (n + a - 1) // a * a


# ---------------------------------------------------------------------------------------------------------------
# rewriting a Renderable
# ---------------------------------------------------------------------------------------------------------------
def replace_meshes(b, res, edits, lib, bundles, root):
    """edits: {mesh index: (corner positions (T*3, 3), corner UVs or None, corner normals or None, [further corner
    UV sets], corner game vertex ids or None)} in the renderable's own space, three corners per triangle. Rewrites the Renderable's records and buffers.
    Returns [(mesh index, vertices, triangles, notes)]."""
    e = b.e
    pc = b.platform == 'PC'
    parts = {}
    olds = {m.src[1]: m for m in mesh.decode_renderable(b, res, lib, bundles, root, parts)}
    gk = 1 if pc else 2
    gfx = res.data(gk)
    c = bytearray(res.data(0))
    recs = mesh.mesh_records(b, res)
    new_buffers = {}
    report = []
    for k, (cpos, cuv, cnrm, cuvs, cids) in edits.items():
        if k not in parts:
            raise MeshImportError(f'mesh {k} of {res.id:#x} cannot be decoded (its material or shader was not found)')
        mr, layout, stride, raw_old = parts[k]
        old = olds[k]
        if not len(cpos):
            raise MeshImportError(f'mesh {k} of {res.id:#x}: the imported object has no triangles')
        old_extra = old.uvs or []
        cuvs = [np.asarray(u, np.float64) for u in cuvs[:len(old_extra)]]
        if cnrm is not None and cids is not None:
            cnrm = _snap_normals(np.asarray(cnrm, np.float64), cids)
        pos, uv, nrm, extra, ids, inv = _weld(np.asarray(cpos, np.float64),
                                              None if cuv is None else np.asarray(cuv, np.float64),
                                              None if cnrm is None else np.asarray(cnrm, np.float64), cuvs, cids)
        if len(pos) > MAX_VERTICES:
            raise MeshImportError(f'mesh {k} of {res.id:#x}: {len(pos)} vertices (at most {MAX_VERTICES} fit '
                               '16-bit indices); split it or reduce it')
        tris = inv.reshape(-1, 3).astype(np.uint32)
        if nrm is None:
            nrm = mesh.MeshData(pos.astype(np.float32), None, tris, 0, None).normals().astype(np.float64)
        notes = []
        near = _nearest(old.pos, old.normals(), old.uv, pos, nrm, uv, old_extra, extra, ids)
        raw = raw_old[near].copy()
        pos_el = next(x for x in layout if x[0] == 'pos')
        p = pos
        if pos_el[1] == 's16n':
            p = pos / (32767.0 * mesh.S16N_SCALE)
            if np.abs(p).max() > 1.0:
                notes.append(f'positions beyond {32767.0 * mesh.S16N_SCALE:.0f} m were clamped')
        if not _encode(raw, e, pos_el[1], pos_el[2], pos_el[3], p):
            raise MeshImportError(f'mesh {k}: positions in {pos_el[1]} cannot be written')
        uv_els = mesh.uv_sets(layout)
        for el, u in zip(uv_els, ([uv] if uv is not None and old.uv is not None else []) + extra):
            if not _encode(raw, e, el[1], el[2], el[3], u):
                notes.append(f'{el[0]} ({el[1]}) kept from the nearest vertices')
        frame = normal_frames(raw_old, e, layout, mesh.MeshData(old.pos, None, old.tris, 0, None).normals()
                              .astype(np.float64))
        if frame is not None:
            # only where the normal changed: the others keep their exact original frame
            nrm = _unit(nrm)
            moved = np.sum(frame_normals(raw, e, frame) * nrm, 1) < 0.999    # turned by more than 2.5 degrees
            if moved.any():
                sub = raw[moved]
                write_normals(sub, e, frame, nrm[moved])
                raw[moved] = sub
        else:
            notes.append('normals taken from the nearest original vertices')
        strips = tris_to_strips(tris)
        new_buffers[k] = (strips.astype(e + 'u2').tobytes(), raw.tobytes(), len(strips))
        report.append((k, len(pos), len(tris), notes))
    # lay the buffers out again in their original order
    ia, va = (32, 32) if pc else (16, 16)
    out = bytearray()
    for mr in sorted(recs, key=lambda r: r['ib_off']):
        k = mr['k']
        if k in new_buffers:
            ib, vb, icount = new_buffers[k]
        else:
            ib, vb, icount = (gfx[mr['ib_off']:mr['ib_off'] + 2 * mr['icount']],
                              gfx[mr['vb_off']:mr['vb_off'] + mr['vb_size']], mr['icount'])
        out += b'\0' * (_align(len(out), ia) - len(out))
        ib_off = len(out)
        ib_size = _align(len(ib), 16) if pc else len(ib)
        out += ib + b'\0' * (ib_size - len(ib))
        out += b'\0' * (_align(len(out), va) - len(out))
        vb_off = len(out)
        out += vb
        w = list(mr['w'])
        if pc:
            w[7], w[15], w[16], w[21], w[22] = icount, ib_off, ib_size, vb_off, len(vb)
        else:
            w[5], w[14], w[12], w[20], w[22] = icount, icount, ib_off, vb_off, len(vb)
        struct.pack_into(e + '24I', c, mr['rec'], *w)
    out += b'\0' * (_align(len(out), 128) - len(out))
    # bounding sphere of the whole renderable (header 0x00: centre, radius)
    allpos = []
    for k, m in olds.items():
        if k in new_buffers:
            allpos.append(np.asarray(edits[k][0], np.float64))
        else:
            allpos.append(m.pos.astype(np.float64))
    if allpos:
        p = np.concatenate(allpos)
        centre = (p.min(0) + p.max(0)) / 2
        radius = float(np.sqrt(((p - centre) ** 2).sum(1).max()))
        old_c = np.array(struct.unpack_from(e + '3f', c, 0))
        old_r = struct.unpack_from(e + 'f', c, 12)[0]
        # keep the original sphere when it still holds everything (it is tighter than the box's)
        if np.sqrt(((p - old_c) ** 2).sum(1).max()) > old_r * 1.0001:
            struct.pack_into(e + '4f', c, 0, *centre, radius)
    res.set_data(0, bytes(c))
    res.set_data(gk, bytes(out))
    return report


def import_fbx_meshes(b, targets, fbx_meshes, lib, bundles, root):
    """Write FBX objects back. targets: the meshes of the exported model as decoded (MeshData with src / xform).
    Returns (report lines, {renderable id: [(k, vertices, triangles, notes)]}, skipped object names)."""
    place = {}
    for m in targets:
        if m.src is not None and m.src not in place:
            place[m.src] = m.xform
    by_res = {}
    skipped = []
    for fm in fbx_meshes:
        key = parse_name(fm.name)
        if key is None or key[2] != 0 or (key[0], key[1]) not in place:
            skipped.append(fm.name)
            continue
        src = (key[0], key[1])
        x = place[src]
        corners = fm.tris.reshape(-1)                      # FbxMesh data is per polygon corner
        cpos = fm.pos[corners].astype(np.float64)
        cuv = fm.uv[corners] if fm.uv is not None else None
        cnrm = fm.normal[corners] if fm.normal is not None else None
        cuvs = [u[corners] for u in fm.uvs]
        cids = fm.ids[corners] if fm.ids is not None else None
        if x is not None:
            a, t = x[:3, :3], x[3, :3]
            cpos = (cpos - t) @ np.linalg.inv(a)
            if cnrm is not None:
                cnrm = cnrm @ a.T
                cnrm = cnrm / np.maximum(np.linalg.norm(cnrm, axis=1, keepdims=True), 1e-12)
            if np.linalg.det(a) < 0:                       # mirrored instance: the triangles were reversed
                order = np.arange(len(cpos)).reshape(-1, 3)[:, ::-1].reshape(-1)
                cpos = cpos[order]
                cuv = cuv[order] if cuv is not None else None
                cnrm = cnrm[order] if cnrm is not None else None
                cuvs = [u[order] for u in cuvs]
                cids = cids[order] if cids is not None else None
        by_res.setdefault(src[0], {})[src[1]] = (cpos, cuv, cnrm, cuvs, cids)
    done = {}
    for rid, edits in by_res.items():
        r = b.find(rid)
        if r is None or r.type != mesh.T_RENDERABLE:
            skipped += [f'R{rid:016X}_{k}' for k in edits]
            continue
        done[rid] = replace_meshes(b, r, edits, lib, bundles, root)
    return done, skipped
