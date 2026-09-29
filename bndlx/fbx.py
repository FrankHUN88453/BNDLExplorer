"""Binary FBX (7.4) writing and reading for model export / import.

Writing: every MeshData becomes a Model + Geometry (positions, per-corner normals, UVs, triangles) with a Phong
material (diffuse colour, diffuse texture file), in metres, Y up. Opens in Blender, 3ds Max, Maya, Unity.
Reading: binary FBX 6.1 - 7.7 (and ASCII FBX) meshes: world-space triangles with per-corner UVs and normals,
the model names and the material names, enough to bring edited geometry back."""
import io
import os
import struct
import zlib

import numpy as np

VERSION = 7400
MAGIC = b'Kaydara FBX Binary  \x00\x1a\x00'
# the FileId / CreationTime / footer id triple that FBX readers accept together (as Blender writes them)
FILE_ID = b'\x28\xb3\x2a\xeb\xb6\x24\xcc\xc2\xbf\xc8\xb0\x2a\xa9\x2b\xfc\xf1'
TIME_ID = b'1970-01-01 10:00:00:000'
FOOT_ID = b'\xfa\xbc\xab\x09\xd0\xc8\xd4\x66\xb1\x76\xfb\x83\x1c\xf7\x26\x7e'
FOOT_MAGIC = b'\xf8\x5a\x8c\x6a\xde\xf5\xd9\x7e\xec\xe9\x0c\xe3\x75\x8f\x29\x0b'
ID_LAYER = 'bndlx_id'      # UV layer holding each vertex's index in the game mesh (u + 256 v), for import


class FbxError(Exception):
    pass


# ---------------------------------------------------------------------------------------------------------------
# node tree
# ---------------------------------------------------------------------------------------------------------------
class Node:
    __slots__ = ('name', 'props', 'children')

    def __init__(self, name, props=(), children=None):
        self.name = name
        self.props = list(props)
        self.children = children or []

    def add(self, name, *props):
        n = Node(name, props)
        self.children.append(n)
        return n

    def find(self, name):
        return next((c for c in self.children if c.name == name), None)

    def findall(self, name):
        return [c for c in self.children if c.name == name]

    def value(self, name, default=None):
        c = self.find(name)
        return c.props[0] if c is not None and c.props else default


class Raw(bytes):
    """A property written as raw bytes ('R')."""


class Long(int):
    """A property always written as a 64-bit integer ('L'): object ids."""


def _prop(p):
    if isinstance(p, Raw):
        return b'R' + struct.pack('<I', len(p)) + bytes(p)
    if isinstance(p, Long):
        return b'L' + struct.pack('<q', p)
    if isinstance(p, bool):
        return b'C' + (b'\x01' if p else b'\x00')
    if isinstance(p, int):
        return (b'I' + struct.pack('<i', p)) if -2 ** 31 <= p < 2 ** 31 else (b'L' + struct.pack('<q', p))
    if isinstance(p, float):
        return b'D' + struct.pack('<d', p)
    if isinstance(p, (bytes, str)):
        s = p.encode('utf-8') if isinstance(p, str) else p
        return b'S' + struct.pack('<I', len(s)) + s
    if isinstance(p, np.ndarray):
        code = {np.dtype('<f8'): b'd', np.dtype('<f4'): b'f', np.dtype('<i4'): b'i', np.dtype('<i8'): b'l',
                np.dtype('bool'): b'b'}.get(p.dtype)
        if code is None:
            raise FbxError(f'array type {p.dtype}')
        data = np.ascontiguousarray(p).tobytes()
        comp = zlib.compress(data, 6)
        if len(comp) < len(data):
            return code + struct.pack('<III', len(p), 1, len(comp)) + comp
        return code + struct.pack('<III', len(p), 0, len(data)) + data
    raise FbxError(f'property {type(p)}')


def _write_node(out, node):
    props = b''.join(_prop(p) for p in node.props)
    name = node.name.encode('ascii')
    start = out.tell()
    out.write(b'\0' * 12)
    out.write(bytes([len(name)]) + name + props)
    if node.children:
        for c in node.children:
            _write_node(out, c)
        out.write(b'\0' * 13)
    end = out.tell()
    out.seek(start)
    out.write(struct.pack('<III', end, len(node.props), len(props)))
    out.seek(end)


def write_binary(nodes):
    out = io.BytesIO()
    out.write(MAGIC + struct.pack('<I', VERSION))
    for n in nodes:
        _write_node(out, n)
    out.write(b'\0' * 13)
    out.write(FOOT_ID)
    pad = ((out.tell() + 15) & ~15) - out.tell()
    out.write(b'\0' * (pad or 16))
    out.write(struct.pack('<I', VERSION) + b'\0' * 120 + FOOT_MAGIC)
    return out.getvalue()


def _read_prop(d, o):
    t = chr(d[o])
    o += 1
    if t in 'YCIFDL':
        fmt = {'Y': '<h', 'C': '<?', 'I': '<i', 'F': '<f', 'D': '<d', 'L': '<q'}[t]
        return struct.unpack_from(fmt, d, o)[0], o + struct.calcsize(fmt)
    if t in 'fdlib':
        n, enc, clen = struct.unpack_from('<III', d, o)
        o += 12
        raw = d[o:o + clen]
        o += clen
        if enc == 1:
            raw = zlib.decompress(raw)
        dt = {'f': '<f4', 'd': '<f8', 'l': '<i8', 'i': '<i4', 'b': 'bool'}[t]
        return np.frombuffer(raw, dt, n).copy(), o
    if t in 'SR':
        n = struct.unpack_from('<I', d, o)[0]
        s = bytes(d[o + 4:o + 4 + n])
        return (s if t == 'R' else s.decode('utf-8', 'replace')), o + 4 + n
    raise FbxError(f'unknown FBX property type {t!r} at {o - 1:#x}')


def _read_node(d, o, wide):
    if wide:
        end, nprops, plen = struct.unpack_from('<QQQ', d, o)
        o += 24
    else:
        end, nprops, plen = struct.unpack_from('<III', d, o)
        o += 12
    if end == 0:
        return None, o
    nl = d[o]
    name = bytes(d[o + 1:o + 1 + nl]).decode('ascii', 'replace')
    o += 1 + nl
    props = []
    for _ in range(nprops):
        v, o = _read_prop(d, o)
        props.append(v)
    node = Node(name, props)
    sentinel = 25 if wide else 13
    while o < end - sentinel + 1 and o < end:
        c, o = _read_node(d, o, wide)
        if c is None:
            break
        node.children.append(c)
    return node, end


def read_binary(d):
    if not d.startswith(MAGIC[:20]):
        raise FbxError('not a binary FBX file')
    version = struct.unpack_from('<I', d, 23)[0]
    wide = version >= 7500
    o = 27
    nodes = []
    while o < len(d):
        n, o = _read_node(d, o, wide)
        if n is None:
            break
        nodes.append(n)
    return version, nodes


# ---------------------------------------------------------------------------------------------------------------
# ASCII FBX (read only)
# ---------------------------------------------------------------------------------------------------------------
def _ascii_tokens(text):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in ' \t\r\n,':
            i += 1
        elif c == ';':
            j = text.find('\n', i)
            i = n if j < 0 else j
        elif c == '"':
            j = text.find('"', i + 1)
            yield ('str', text[i + 1:j])
            i = j + 1
        elif c in '{}':
            yield (c, c)
            i += 1
        elif c == '*':
            j = i + 1
            while j < n and text[j].isdigit():
                j += 1
            yield ('count', text[i + 1:j])
            i = j
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n,{}";':
                j += 1
            word = text[i:j]
            if word.endswith(':'):
                yield ('key', word[:-1])
            else:
                yield ('word', word)
            i = j


def read_ascii(text):
    root = Node('root')
    stack = [root]
    cur = None
    in_array = []
    for kind, v in _ascii_tokens(text):
        if kind == 'key':
            if in_array and in_array[-1] is not None and v == 'a':
                continue
            cur = stack[-1].add(v)
        elif kind == 'count':
            in_array.append(cur)
        elif kind == '{':
            if in_array and in_array[-1] is cur:
                continue
            stack.append(cur)
        elif kind == '}':
            if in_array and in_array[-1] is cur:
                arr = cur.props
                vals = [float(x) for x in arr]
                cur.props = [np.array(vals)]
                in_array.pop()
                continue
            stack.pop()
            cur = stack[-1] if stack else None
        elif cur is not None:
            if kind == 'str':
                cur.props.append(v)
            else:
                try:
                    cur.props.append(int(v))
                except ValueError:
                    try:
                        cur.props.append(float(v))
                    except ValueError:
                        cur.props.append(v)
    return root.children


def read(data):
    """(version, top-level nodes) of a binary or ASCII FBX file."""
    if data.startswith(MAGIC[:20]):
        return read_binary(data)
    text = data.decode('utf-8', 'replace')
    if 'FBXHeaderExtension' not in text[:4096] and 'Objects' not in text:
        raise FbxError('not an FBX file')
    return 7000, read_ascii(text)


# ---------------------------------------------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------------------------------------------
def _p70(parent, rows):
    p = parent.add('Properties70')
    for row in rows:
        p.add('P', *row)
    return p


def _fbx_name(name, cls):
    return name.encode('utf-8') + b'\x00\x01' + cls.encode('ascii')


def _split_duplicates(tris, nverts):
    """Two-sided geometry uses the same three vertices twice; FBX importers drop such repeated faces, so every
    repeat gets its own copies of the vertices. Returns (triangles, source vertex of every vertex)."""
    key = np.sort(tris, 1)
    _, first = np.unique(key, axis=0, return_index=True)
    rep = np.ones(len(tris), bool)
    rep[first] = False
    vmap = np.arange(nverts)
    if not rep.any():
        return tris, vmap
    tris = tris.copy()
    extra = tris[rep].reshape(-1)
    tris[rep] = (nverts + np.arange(len(extra))).reshape(-1, 3)
    return tris, np.concatenate([vmap, extra])


def write_fbx(meshes, texture_files, name='model', mesh_names=None):
    """meshes: [MeshData]; texture_files: {texture id: relative file name of the PNG next to the .fbx}.
    Returns the .fbx bytes (binary FBX 7.4, metres, Y up)."""
    ids = (Long(x) for x in range(1000000, 10 ** 9, 7))
    head = Node('FBXHeaderExtension')
    head.add('FBXHeaderVersion', 1003)
    head.add('FBXVersion', VERSION)
    head.add('EncryptionType', 0)
    ts = head.add('CreationTimeStamp')
    for k, v in (('Version', 1000), ('Year', 1970), ('Month', 1), ('Day', 1), ('Hour', 10), ('Minute', 0),
                 ('Second', 0), ('Millisecond', 0)):
        ts.add(k, v)
    head.add('Creator', 'BNDL Explorer')
    nodes = [head, Node('FileId', [Raw(FILE_ID)]), Node('CreationTime', [TIME_ID.decode()]),
             Node('Creator', ['BNDL Explorer'])]
    gs = Node('GlobalSettings')
    gs.add('Version', 1000)
    _p70(gs, [('UpAxis', 'int', 'Integer', '', 1), ('UpAxisSign', 'int', 'Integer', '', 1),
              ('FrontAxis', 'int', 'Integer', '', 2), ('FrontAxisSign', 'int', 'Integer', '', 1),
              ('CoordAxis', 'int', 'Integer', '', 0), ('CoordAxisSign', 'int', 'Integer', '', 1),
              ('OriginalUpAxis', 'int', 'Integer', '', 1), ('OriginalUpAxisSign', 'int', 'Integer', '', 1),
              ('UnitScaleFactor', 'double', 'Number', '', 100.0),
              ('OriginalUnitScaleFactor', 'double', 'Number', '', 100.0)])
    nodes.append(gs)
    docs = Node('Documents')
    docs.add('Count', 1)
    doc = docs.add('Document', next(ids), '', 'Scene')
    _p70(doc, [('SourceObject', 'object', '', '')])
    doc.add('RootNode', Long(0))
    nodes += [docs, Node('References')]
    objects = Node('Objects')
    conns = Node('Connections')
    counts = {'Model': 0, 'Geometry': 0, 'Material': 0, 'Texture': 0, 'Video': 0}
    mat_ids = {}
    tex_ids = {}
    for i, m in enumerate(meshes):
        mname = (mesh_names[i] if mesh_names else f'{name}_{i}')
        tris, vmap = _split_duplicates(np.asarray(m.tris, np.int64), len(m.pos))
        pos = np.asarray(m.pos, np.float64)[vmap]
        poly = tris.copy()
        poly[:, 2] = -poly[:, 2] - 1                 # the last index of a polygon is stored as ~index
        geo_id, model_id = next(ids), next(ids)
        geo = objects.add('Geometry', geo_id, _fbx_name(mname, 'Geometry'), 'Mesh')
        geo.add('Vertices', pos.ravel())
        geo.add('PolygonVertexIndex', poly.ravel().astype('<i4'))
        geo.add('GeometryVersion', 124)
        nrm = m.normals()[vmap[tris.ravel()]].astype(np.float64)
        le = geo.add('LayerElementNormal', 0)
        le.add('Version', 101)
        le.add('Name', '')
        le.add('MappingInformationType', 'ByPolygonVertex')
        le.add('ReferenceInformationType', 'Direct')
        le.add('Normals', nrm.ravel())
        uv_sets = [np.asarray(u)[vmap] for u in [m.uv] + list(m.uvs or [])] if m.uv is not None else []
        names = ['UVMap'] + [f'UV{li}' for li in range(1, len(uv_sets))]
        if getattr(m, 'src', None) is not None:
            vid = vmap
            uv_sets = uv_sets + [np.stack([vid % 256 + 0.5, vid // 256 + 0.5], 1)]
            names.append(ID_LAYER)
        for li, u in enumerate(uv_sets):
            uv = np.asarray(u, np.float64)[:, :2].copy()
            uv[:, 1] = 1.0 - uv[:, 1]                # FBX / Blender UVs start at the bottom
            lu = geo.add('LayerElementUV', li)
            lu.add('Version', 101)
            lu.add('Name', names[li])
            lu.add('MappingInformationType', 'ByPolygonVertex')
            lu.add('ReferenceInformationType', 'IndexToDirect')
            lu.add('UV', uv.ravel())
            lu.add('UVIndex', tris.ravel().astype('<i4'))
        lm = geo.add('LayerElementMaterial', 0)
        lm.add('Version', 101)
        lm.add('Name', '')
        lm.add('MappingInformationType', 'AllSame')
        lm.add('ReferenceInformationType', 'IndexToDirect')
        lm.add('Materials', np.zeros(1, '<i4'))
        for li in range(max(1, len(uv_sets))):
            layer = geo.add('Layer', li)
            layer.add('Version', 100)
            kinds = ('LayerElementNormal', 'LayerElementMaterial') if li == 0 else ()
            for t in kinds + (('LayerElementUV',) if li < len(uv_sets) else ()):
                e = layer.add('LayerElement')
                e.add('Type', t)
                e.add('TypedIndex', li if t == 'LayerElementUV' else 0)
        model = objects.add('Model', model_id, _fbx_name(mname, 'Model'), 'Mesh')
        model.add('Version', 232)
        _p70(model, [('DefaultAttributeIndex', 'int', 'Integer', '', 0)])
        model.add('Shading', True)
        model.add('Culling', 'CullingOff')
        counts['Model'] += 1
        counts['Geometry'] += 1
        conns.add('C', 'OO', model_id, Long(0))
        conns.add('C', 'OO', geo_id, model_id)
        mkey = (m.material, m.texture)
        if mkey not in mat_ids:
            mid = mat_ids[mkey] = next(ids)
            mat_name = f'material_{m.material:x}' if m.material else f'material_{len(mat_ids)}'
            mat = objects.add('Material', mid, _fbx_name(mat_name, 'Material'), '')
            mat.add('Version', 102)
            mat.add('ShadingModel', 'phong')
            mat.add('MultiLayer', 0)
            col = tuple(float(x) ** 2.2 for x in m.tint)        # linear, as glTF export
            _p70(mat, [('DiffuseColor', 'Color', '', 'A', *col), ('Diffuse', 'Vector3D', 'Vector', '', *col),
                       ('DiffuseFactor', 'Number', '', 'A', 1.0)])
            counts['Material'] += 1
            tfile = texture_files.get(m.texture) if m.texture else None
            if tfile:
                if tfile not in tex_ids:
                    tid, vid = next(ids), next(ids)
                    tex_ids[tfile] = tid
                    tname = os.path.splitext(os.path.basename(tfile))[0]
                    tx = objects.add('Texture', tid, _fbx_name(tname, 'Texture'), '')
                    tx.add('Type', 'TextureVideoClip')
                    tx.add('Version', 202)
                    tx.add('TextureName', _fbx_name(tname, 'Texture'))
                    tx.add('Media', _fbx_name(tname, 'Video'))
                    tx.add('FileName', tfile)
                    tx.add('RelativeFilename', tfile)
                    vd = objects.add('Video', vid, _fbx_name(tname, 'Video'), 'Clip')
                    vd.add('Type', 'Clip')
                    vd.add('FileName', tfile)
                    vd.add('RelativeFilename', tfile)
                    conns.add('C', 'OO', vid, tid)
                    counts['Texture'] += 1
                    counts['Video'] += 1
                conns.add('C', 'OP', tex_ids[tfile], mid, 'DiffuseColor')
        conns.add('C', 'OO', mat_ids[mkey], model_id)
    defs = Node('Definitions')
    defs.add('Version', 100)
    defs.add('Count', sum(counts.values()) + 1)
    ot = defs.add('ObjectType', 'GlobalSettings')
    ot.add('Count', 1)
    for k, v in counts.items():
        if v:
            ot = defs.add('ObjectType', k)
            ot.add('Count', v)
    nodes += [defs, objects, conns]
    return write_binary(nodes)


# ---------------------------------------------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------------------------------------------
class FbxMesh:
    """Triangles of one FBX model in metres, Y up, moved by the model's own translation / rotation / scaling
    (parents are not applied). pos, uv, normal and every entry of uvs are per polygon corner; tris index them."""

    def __init__(self, name, pos, tris, uv, normal, material, uvs=None, ids=None):
        self.name, self.pos, self.tris, self.uv, self.normal, self.material = name, pos, tris, uv, normal, material
        self.uvs = uvs or []
        self.ids = ids              # per corner: the vertex index in the game mesh it was exported from, or -1


def _layer(geo, name, key, index_key, npoly_verts, polygon_of_corner, vert_of_corner, nverts, le=None):
    le = le if le is not None else geo.find(name)
    if le is None:
        return None
    data = le.value(key)
    if data is None:
        return None
    width = 2 if key == 'UV' else 3
    data = np.asarray(data, np.float64).reshape(-1, width)
    mapping = le.value('MappingInformationType', 'ByPolygonVertex')
    ref = le.value('ReferenceInformationType', 'Direct')
    if ref in ('IndexToDirect', 'Index'):
        idx = np.asarray(le.value(index_key), np.int64)
    else:
        idx = None
    if mapping in ('ByPolygonVertex', 'ByPolygonVertice'):
        sel = idx if idx is not None else np.arange(npoly_verts)
    elif mapping in ('ByVertex', 'ByVertice'):
        sel = (idx[vert_of_corner] if idx is not None else vert_of_corner)
    elif mapping == 'ByPolygon':
        sel = (idx[polygon_of_corner] if idx is not None else polygon_of_corner)
    elif mapping == 'AllSame':
        sel = np.zeros(npoly_verts, np.int64)
    else:
        return None
    return data[np.clip(sel, 0, len(data) - 1)]


def _euler_matrix(deg):
    rx, ry, rz = np.radians(deg)
    cx, sx, cy, sy, cz, sz = np.cos(rx), np.sin(rx), np.cos(ry), np.sin(ry), np.cos(rz), np.sin(rz)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return mz @ my @ mx                                   # FBX default order XYZ (column vectors)


def load_meshes(data):
    """Meshes of an FBX file: [FbxMesh] in metres with the file's axes turned into Y up. Polygons are fanned
    into triangles."""
    version, nodes = read(data)
    top = {n.name: n for n in nodes}
    objs = top.get('Objects')
    if objs is None:
        raise FbxError('the FBX file has no objects')
    unit = 1.0
    up, front, coord = (1, 1), (2, 1), (0, 1)
    gs = top.get('GlobalSettings')
    if gs is not None and gs.find('Properties70') is not None:
        for p in gs.find('Properties70').findall('P'):
            if p.props and p.props[0] == 'UnitScaleFactor':
                unit = float(p.props[-1]) / 100.0
            elif p.props and p.props[0] == 'UpAxis':
                up = (int(p.props[-1]), up[1])
            elif p.props and p.props[0] == 'UpAxisSign':
                up = (up[0], int(p.props[-1]))
            elif p.props and p.props[0] == 'FrontAxis':
                front = (int(p.props[-1]), front[1])
            elif p.props and p.props[0] == 'FrontAxisSign':
                front = (front[0], int(p.props[-1]))
            elif p.props and p.props[0] == 'CoordAxis':
                coord = (int(p.props[-1]), coord[1])
            elif p.props and p.props[0] == 'CoordAxisSign':
                coord = (coord[0], int(p.props[-1]))
    # file axes -> (x right, y up, z front)
    axes = np.zeros((3, 3))
    axes[0, coord[0]] = coord[1]
    axes[1, up[0]] = up[1]
    axes[2, front[0]] = front[1]

    def oid(n):
        return n.props[0] if n.props else None

    def oname(n):
        s = n.props[1] if len(n.props) > 1 else ''
        if isinstance(s, bytes):
            s = s.decode('utf-8', 'replace')
        return s.split('\x00\x01')[0].split('::')[-1]

    geos = {oid(n): n for n in objs.findall('Geometry')}
    models = {oid(n): n for n in objs.findall('Model')}
    mats = {oid(n): n for n in objs.findall('Material')}
    geo_model, model_mats = {}, {}
    conns = top.get('Connections')
    for c in (conns.findall('C') if conns is not None else []):
        if len(c.props) >= 3 and c.props[0] == 'OO':
            child, parent = c.props[1], c.props[2]
            if child in geos and parent in models:
                geo_model[child] = parent
            elif child in mats and parent in models:
                model_mats.setdefault(parent, []).append(child)
    out = []
    for gid, geo in geos.items():
        verts = geo.value('Vertices')
        pvi = geo.value('PolygonVertexIndex')
        if verts is None or pvi is None:
            continue
        pos = np.asarray(verts, np.float64).reshape(-1, 3)
        pvi = np.asarray(pvi, np.int64)
        ends = np.nonzero(pvi < 0)[0]
        vert_of_corner = np.where(pvi < 0, -pvi - 1, pvi)
        starts = np.concatenate([[0], ends[:-1] + 1])
        polygon_of_corner = np.repeat(np.arange(len(ends)), ends - starts + 1)
        n_corners = len(pvi)
        uv_layers = sorted(geo.findall('LayerElementUV'), key=lambda n: n.props[0] if n.props else 0)
        ids = None
        uvl = []
        for le in uv_layers:
            u = _layer(geo, 'LayerElementUV', 'UV', 'UVIndex', n_corners, polygon_of_corner, vert_of_corner,
                       len(pos), le)
            if u is None:
                continue
            u[:, 1] = 1.0 - u[:, 1]
            if str(le.value('Name', '')).split('\x00')[0] == ID_LAYER:
                cell = np.floor(u)
                exact = (np.abs(u - cell - 0.5) < 0.25).all(1) & (cell >= 0).all(1) & (cell[:, 0] < 256)
                ids = np.where(exact, cell[:, 0] + 256 * cell[:, 1], -1).astype(np.int64)
            else:
                uvl.append(u)
        uv = uvl[0] if uvl else None
        nrm = _layer(geo, 'LayerElementNormal', 'Normals', 'NormalsIndex', n_corners, polygon_of_corner,
                     vert_of_corner, len(pos))
        tri = []
        for s, e in zip(starts, ends):
            for k in range(s + 1, e):
                tri.append((s, k, k + 1))
        tri = np.array(tri, np.int64).reshape(-1, 3)
        mid = geo_model.get(gid)
        model = models.get(mid)
        name = oname(model) if model is not None else oname(geo)
        # the model's own transform
        m = np.eye(3)
        t = np.zeros(3)
        if model is not None and model.find('Properties70') is not None:
            tr, rot, sc = np.zeros(3), np.zeros(3), np.ones(3)
            for p in model.find('Properties70').findall('P'):
                if p.props and p.props[0] in ('Lcl Translation', 'Lcl Rotation', 'Lcl Scaling'):
                    v = np.array([float(x) for x in p.props[-3:]])
                    if p.props[0] == 'Lcl Translation':
                        tr = v
                    elif p.props[0] == 'Lcl Rotation':
                        rot = v
                    else:
                        sc = v
            m = _euler_matrix(rot) @ np.diag(sc)
            t = tr
        wpos = (pos @ m.T + t) * unit
        wpos = wpos @ axes.T
        wn = None
        if nrm is not None:
            wn = nrm @ np.linalg.inv(m) @ axes.T
            wn /= np.maximum(np.linalg.norm(wn, axis=1, keepdims=True), 1e-12)
        mat_names = [oname(mats[x]) for x in model_mats.get(mid, []) if x in mats]
        out.append(FbxMesh(name, wpos[vert_of_corner], tri, uv, wn, mat_names[0] if mat_names else '', uvl[1:],
                           ids))
    return out
