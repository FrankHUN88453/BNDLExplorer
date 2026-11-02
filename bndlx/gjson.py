"""Genesys objects as JSON (export, and import onto the object), and array edits (add / remove elements).

Export: {"$type": type name, "$id": resource id, fields...}; a field is keyed by its name (or 0x<hash> when the
name is not known); structs are objects with "$type", arrays are lists, references to other resources are
{"$ref": "0x..."}, vectors are lists of numbers, enums are numbers. Data the schema does not describe is not in
the JSON: import applies the JSON onto the object as it is (fields present in the JSON are set, others stay; an
array longer than now gets elements copied from its last one, then set), so that data stays.
"""
import copy
import json

from .genesys import GENESYS_OBJECT, HANDLE_KIND, STRINGBASE, Node, Ref


class JsonError(ValueError):
    pass


def field_key(labels, f):
    n = labels.name(f.name_hash) if labels is not None else None
    return n.replace(' (?)', '') if n else f'0x{f.name_hash:08x}'


def to_json(types, labels, node, rid=None):
    def value(v):
        if isinstance(v, Node):
            return struct_(v)
        if isinstance(v, Ref):
            return {'$ref': f'{v.id:#018x}'}
        if isinstance(v, list):
            return [value(x) for x in v]
        if isinstance(v, (bytes, bytearray)):
            return {'$bytes': bytes(v).hex()}
        if isinstance(v, float):
            return short_float(v)
        return v

    def struct_(n):
        t = types.get(n.type)
        out = {'$type': types.name(n.type)}
        if t is None:
            return out
        for f in t.fields:
            out[field_key(labels, f)] = value(n.fields.get(f.name_hash))
        return out

    out = struct_(node)
    if rid is not None:
        out = {'$id': f'{rid:#018x}', **out}
    return out


def short_float(v):
    """The shortest decimal that is the same float32 again (readable, and exact on import)."""
    import struct
    want = struct.pack('<f', v)
    for p in range(6, 10):
        x = float(f'{v:.{p}g}')
        if struct.pack('<f', x) == want:
            return x
    return v


def dumps(types, labels, node, rid=None):
    return json.dumps(to_json(types, labels, node, rid), indent=1, ensure_ascii=False)


def clone(v):
    """A deep copy of a value of the node tree (new elements start as copies of an existing one)."""
    if isinstance(v, Node):
        n = Node(v.type, -1)
        n.fields = {k: clone(x) for k, x in v.fields.items()}
        n.raw = v.raw
        return n
    if isinstance(v, Ref):
        return Ref(v.id)
    if isinstance(v, list):
        return [clone(x) for x in v]
    return copy.copy(v)


def default_of(types, f):
    ft = types.get(f.type_id)
    if ft is None:
        return 0
    if ft.id == STRINGBASE:
        return ''
    if ft.kind == HANDLE_KIND:
        return None
    if ft.kind == 6:
        return [0.0] * ft.fields[0].count
    if ft.kind == 7:
        n = Node(ft.id, -1)
        for sf in ft.fields:
            n.fields[sf.name_hash] = default_of(types, sf)
        return n
    if ft.kind == 2:
        return 0.0
    return 0


def resizable(f):
    """Arrays that keep their length in a count field (pointer + count): elements can be added / removed."""
    return bool(f.flags & 1 and f.flags & 8)


def add_element(types, f, arr):
    """A new element at the end: a copy of the last one, or the type's defaults for an empty array."""
    if arr:
        arr.append(clone(arr[-1]))
    elif f.flags & 16:
        ft = types.get(f.type_id)
        arr.append(default_of(types, f) if ft is not None and ft.kind == 7 and (ft.base or ft.id == GENESYS_OBJECT)
                   else None)
        if arr[-1] is None:
            arr.pop()
            raise JsonError('an empty list of objects: the kind of object to add is not known (copy one in first)')
    else:
        arr.append(default_of(types, f))
    return arr


def apply_json(types, labels, node, js):
    """Set node's fields from the JSON (as to_json writes it)."""
    t = types.get(node.type)
    if t is None:
        raise JsonError(f'type {node.type:#x} is not open')
    keys = {field_key(labels, f): f for f in t.fields}
    keys.update({f'0x{f.name_hash:08x}': f for f in t.fields})
    for k, v in js.items():
        if k.startswith('$'):
            continue
        f = keys.get(k)
        if f is None:
            raise JsonError(f'{types.name(node.type)} has no field "{k}"')
        node.fields[f.name_hash] = merge(types, labels, f, node.fields.get(f.name_hash), v)


def merge(types, labels, f, cur, v):
    if isinstance(v, dict) and '$ref' in v:
        return Ref(int(v['$ref'], 16))
    if isinstance(v, dict) and '$bytes' in v:
        return bytes.fromhex(v['$bytes'])
    if v is None:
        return None
    if isinstance(v, dict):
        base = cur if isinstance(cur, Node) else default_of(types, f)
        if not isinstance(base, Node):
            raise JsonError(f'field {field_key(labels, f)} is not a struct')
        node = clone(base)
        apply_json(types, labels, node, v)
        return node
    if isinstance(v, list):
        ft = types.get(f.type_id)
        if ft is not None and ft.kind == 6 and not (f.flags & 1) and not (f.flags & 2 and f.count > 1) \
                and all(isinstance(x, (int, float)) for x in v):
            return [float(x) for x in v]
        cur = list(cur) if isinstance(cur, list) else []
        if len(v) != len(cur) and not (resizable(f) or f.flags & 2 and f.count > 1 and len(v) == f.count):
            raise JsonError(f'the array {field_key(labels, f)} has a fixed length of {len(cur)}')
        out = []
        for i, x in enumerate(v):
            if i < len(cur):
                base = cur[i]
            else:
                base = clone(cur[-1]) if cur else default_of(types, f)
            out.append(merge(types, labels, f, base, x) if isinstance(x, (dict, list)) else
                       scalar(types, f, x))
        return out
    return scalar(types, f, v)


def scalar(types, f, v):
    ft = types.get(f.type_id)
    if isinstance(v, str):
        if ft is not None and ft.id == STRINGBASE:
            return v
        raise JsonError(f'expected a number, got "{v}"')
    if ft is not None and ft.kind == 2:
        return float(v)
    if isinstance(v, bool):
        return int(v)
    return int(v) if isinstance(v, (int, float)) and float(v).is_integer() else v
