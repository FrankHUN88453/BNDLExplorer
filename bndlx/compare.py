"""Field-level comparison of resources in two bundles."""
import os
import struct
import zlib

from . import genesys
from .genesys import Node, Ref
from .restypes import T_GOBJECT


def same_bundle_name(path_a, path_b):
    """Whether two paths name the same .BNDL file, ignoring directory and case."""
    name_a = os.path.basename(path_a or '')
    name_b = os.path.basename(path_b or '')
    return (name_a.casefold().endswith('.bndl') and name_b.casefold().endswith('.bndl')
            and name_a.casefold() == name_b.casefold())


def _value_text(value):
    if isinstance(value, Ref):
        return f'Resource {value.id:016X}'
    if isinstance(value, bytes):
        return f'{len(value)} bytes'
    return repr(value)


def _field_values(node, types, labels, prefix=''):
    values = {}
    schema = types.get(node.type)
    if schema is None:
        return {prefix or f'type {node.type:#x}': f'<missing schema {node.type:#x}>'}
    for field in schema.fields:
        name = labels.name(field.name_hash) or genesys.hash_name(field.name_hash) or f'{field.name_hash:08x}'
        path = f'{prefix}.{name}' if prefix else name
        value = node.fields.get(field.name_hash)
        if isinstance(value, Node):
            values.update(_field_values(value, types, labels, path))
        elif isinstance(value, list) and any(isinstance(item, Node) for item in value):
            for index, item in enumerate(value):
                item_path = f'{path}[{index}]'
                if isinstance(item, Node):
                    values.update(_field_values(item, types, labels, item_path))
                else:
                    values[item_path] = _value_text(item)
        else:
            values[path] = _value_text(value)
    return values


def _read_object_fields(bundle, resource, types, labels):
    local_types = genesys.TypeDB()
    local_types.types = dict(types.types)
    for schema_resource in bundle.resources:
        if schema_resource.type == genesys.T_TYPE and not schema_resource.missing:
            try:
                local_types.types[schema_resource.id] = genesys.parse_type(schema_resource.copy(), bundle.e)
            except (ValueError, struct.error, IndexError, UnicodeDecodeError, zlib.error):
                continue
    node = genesys.Reader(local_types, bundle.e).read_resource(resource.copy())
    return _field_values(node, local_types, labels)


def _changed_chunks(resource_a, resource_b):
    try:
        data_a, data_b = resource_a.copy(), resource_b.copy()
        return [chunk for chunk in range(4) if data_a.data(chunk) != data_b.data(chunk)], None
    except (ValueError, struct.error, IndexError, KeyError, zlib.error) as exc:
        return None, exc


def _chunk_difference(resource_a, resource_b, chunk, prefix='Chunk'):
    return (prefix, f'{prefix} {chunk} data', f'{resource_a.size(chunk)} bytes (contents differ)',
            f'{resource_b.size(chunk)} bytes (contents differ)')


def bundle_differences(bundle_a, bundle_b, types, labels):
    """Return (resource id, field, value in A, value in B) rows for two bundles."""
    rows = []
    resources_a = {resource.id: resource for resource in bundle_a.resources}
    resources_b = {resource.id: resource for resource in bundle_b.resources}

    for rid in sorted(resources_a.keys() | resources_b.keys()):
        a = resources_a.get(rid)
        b = resources_b.get(rid)
        if a is None:
            rows.append((rid, 'Resource', '<absent>', f'added ({b.type:#x})'))
            continue
        if b is None:
            rows.append((rid, 'Resource', f'removed ({a.type:#x})', '<absent>'))
            continue

        try:
            data_a, data_b = a.copy(), b.copy()
            metadata_a = {
                'Type': a.type,
                'Stream': a.stream,
                'Flags': a.flags,
                'Chunk sizes': tuple(data_a.size(k) for k in range(4)),
                'Imports': tuple((imp.id, imp.raw) for imp in data_a.imports()),
            }
            metadata_b = {
                'Type': b.type,
                'Stream': b.stream,
                'Flags': b.flags,
                'Chunk sizes': tuple(data_b.size(k) for k in range(4)),
                'Imports': tuple((imp.id, imp.raw) for imp in data_b.imports()),
            }
        except (ValueError, struct.error, IndexError, KeyError, zlib.error) as exc:
            rows.append((rid, 'Comparison error', 'Could not read resource metadata', str(exc)))
            continue
        for field, value_a in metadata_a.items():
            value_b = metadata_b[field]
            if value_a != value_b:
                rows.append((rid, field, _value_text(value_a), _value_text(value_b)))

        if a.type == b.type == T_GOBJECT:
            try:
                fields_a = _read_object_fields(bundle_a, a, types, labels)
                fields_b = _read_object_fields(bundle_b, b, types, labels)
            except (ValueError, struct.error, IndexError, KeyError, zlib.error) as exc:
                chunks_differ, chunk_error = _changed_chunks(a, b)
                if chunk_error:
                    rows.append((rid, 'Comparison error', 'Could not read resource data', str(chunk_error)))
                    continue
                if chunks_differ:
                    rows.append((rid, 'Genesys fields', 'could not decode', str(exc)))
                    for chunk in chunks_differ:
                        rows.append((rid, *_chunk_difference(a, b, chunk)[1:]))
            else:
                field_change_count = 0
                field_names = fields_a.keys() | fields_b.keys()
                for field in sorted(field_names):
                    value_a = fields_a.get(field, '<absent>')
                    value_b = fields_b.get(field, '<absent>')
                    if value_a != value_b:
                        rows.append((rid, field, value_a, value_b))
                        field_change_count += 1
                if not field_change_count:
                    chunks_differ, chunk_error = _changed_chunks(a, b)
                    if chunk_error:
                        rows.append((rid, 'Comparison error', 'Could not read resource data', str(chunk_error)))
                    else:
                        for chunk in chunks_differ:
                            rows.append((rid, *_chunk_difference(a, b, chunk, 'Other chunk')[1:]))
        else:
            chunks_differ, chunk_error = _changed_chunks(a, b)
            if chunk_error:
                rows.append((rid, 'Comparison error', 'Could not read resource data', str(chunk_error)))
            else:
                for chunk in chunks_differ:
                    rows.append((rid, *_chunk_difference(a, b, chunk)[1:]))
    return rows
