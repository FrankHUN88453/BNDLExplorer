"""Binary glTF 2.0 (.glb) export of decoded meshes: positions, normals, UVs, indices and the diffuse texture of
each mesh as an embedded PNG. Opens in Blender, Windows 3D Viewer and most tools."""
import io
import json
import struct

import numpy as np


def _png(img):
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(img, np.uint8), 'RGBA').save(buf, 'PNG')
    return buf.getvalue()


def write_glb(meshes, texture_images, name='model'):
    """meshes: [MeshData]; texture_images: {texture id: RGBA array}. Returns the .glb bytes."""
    bin_ = bytearray()
    views, accessors, prims_meshes, nodes = [], [], [], []
    materials, textures, images = [], [], []
    tex_index = {}

    def add_view(data, target=None):
        while len(bin_) % 4:
            bin_.append(0)
        off = len(bin_)
        bin_.extend(data)
        v = {'buffer': 0, 'byteOffset': off, 'byteLength': len(data)}
        if target:
            v['target'] = target
        views.append(v)
        return len(views) - 1

    def add_accessor(arr, ctype, typ, target=None, minmax=False):
        view = add_view(arr.tobytes(), target)
        a = {'bufferView': view, 'componentType': ctype, 'count': int(arr.shape[0]), 'type': typ}
        if minmax:
            a['min'] = [float(x) for x in arr.min(0)]
            a['max'] = [float(x) for x in arr.max(0)]
        accessors.append(a)
        return len(accessors) - 1

    for i, m in enumerate(meshes):
        attrs = {'POSITION': add_accessor(np.ascontiguousarray(m.pos, np.float32), 5126, 'VEC3', 34962, True),
                 'NORMAL': add_accessor(np.ascontiguousarray(m.normals(), np.float32), 5126, 'VEC3', 34962)}
        if m.uv is not None:
            attrs['TEXCOORD_0'] = add_accessor(np.ascontiguousarray(m.uv, np.float32), 5126, 'VEC2', 34962)
        ind = add_accessor(np.ascontiguousarray(m.tris.ravel(), np.uint32), 5125, 'SCALAR', 34963)
        mat = {'name': f'material_{m.material:x}' if m.material else f'material_{i}',
               'pbrMetallicRoughness': {'metallicFactor': 0.0, 'roughnessFactor': 0.8,
                                        'baseColorFactor': [(x ** 2.2) for x in m.tint] + [1.0]}, 'doubleSided': True}
        img = texture_images.get(m.texture) if m.texture else None
        if img is not None and m.uv is not None:
            if m.texture not in tex_index:
                images.append({'bufferView': add_view(_png(img)), 'mimeType': 'image/png', 'name': f'{m.texture:x}'})
                textures.append({'source': len(images) - 1})
                tex_index[m.texture] = len(textures) - 1
            mat['pbrMetallicRoughness']['baseColorTexture'] = {'index': tex_index[m.texture]}
            mat['pbrMetallicRoughness']['baseColorFactor'] = [1.0, 1.0, 1.0, 1.0]
        materials.append(mat)
        prims_meshes.append({'name': f'mesh_{i}', 'primitives': [{'attributes': attrs, 'indices': ind,
                                                                   'material': len(materials) - 1}]})
        nodes.append({'mesh': i, 'name': f'{name}_{i}'})
    doc = {'asset': {'version': '2.0', 'generator': 'BNDL Explorer'}, 'scene': 0,
           'scenes': [{'nodes': list(range(len(nodes))), 'name': name}], 'nodes': nodes, 'meshes': prims_meshes,
           'materials': materials, 'accessors': accessors, 'bufferViews': views,
           'buffers': [{'byteLength': len(bin_)}]}
    if images:
        doc['images'] = images
        doc['textures'] = textures
        doc['samplers'] = [{'magFilter': 9729, 'minFilter': 9987, 'wrapS': 10497, 'wrapT': 10497}]
        for t in textures:
            t['sampler'] = 0
    js = json.dumps(doc, separators=(',', ':')).encode('utf-8')
    js += b' ' * ((-len(js)) % 4)
    while len(bin_) % 4:
        bin_.append(0)
    out = struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(bin_))
    out += struct.pack('<II', len(js), 0x4E4F534A) + js
    out += struct.pack('<II', len(bin_), 0x004E4942) + bytes(bin_)
    return out
