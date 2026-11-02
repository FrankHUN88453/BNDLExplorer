"""Reader for the HSMData (0x501) resource used by MW2012."""
import json
import struct


PARAM_TYPES = {
    0: 'String',
    1: 'Int',
    2: 'Float',
    3: 'ID',
    4: 'Bool',
    5: 'Asset reference',
    6: 'Structure',
    7: 'Invalid',
}
T_HSM = 0x501


class HSMError(ValueError):
    pass


class _Reader:
    def __init__(self, data):
        self.data = memoryview(data)

    def _check(self, offset, size, what):
        if offset < 0 or size < 0 or offset > len(self.data) or size > len(self.data) - offset:
            raise HSMError(f'{what} at {offset:#x} ({size:#x} bytes) is outside the resource')

    def unpack(self, fmt, offset, what):
        size = struct.calcsize(fmt)
        self._check(offset, size, what)
        return struct.unpack_from(fmt, self.data, offset)

    def ptr(self, offset, what):
        return self.unpack('<I', offset, what)[0]

    def records(self, offset, count, size, what):
        if count < 0:
            raise HSMError(f'{what} has a negative count')
        if count > 1_000_000:
            raise HSMError(f'{what} count is unreasonably large ({count})')
        if not count:
            return []
        if count and not offset:
            raise HSMError(f'{what} has entries but a null pointer')
        self._check(offset, count * size, what)
        return [offset + size * i for i in range(count)]

    def string(self, offset, what):
        if not offset:
            return ''
        self._check(offset, 1, what)
        end = self.data[offset:].tobytes().find(b'\0')
        if end < 0:
            raise HSMError(f'{what} at {offset:#x} is not null-terminated')
        return self.data[offset:offset + end].tobytes().decode('utf-8', errors='replace')


def _hash(reader, offset, what):
    reader._check(offset, 8, what)
    return reader.data[offset:offset + 8].tobytes().hex().upper()


def _parameters(reader, offset, count, depth=0, ancestors=frozenset()):
    if depth > 32:
        raise HSMError('nested parameter structure exceeds 32 levels')
    if offset in ancestors and count:
        raise HSMError(f'cyclic parameter structure at {offset:#x}')
    rows = []
    for pos in reader.records(offset, count, 0x10, 'parameters'):
        parameter_id = _hash(reader, pos, 'parameter id')
        value_ptr, param_type, value_count = reader.unpack('<Ihh', pos + 8, 'parameter header')
        label = PARAM_TYPES.get(param_type)
        if label is None:
            raise HSMError(f'parameter {parameter_id} has unknown type {param_type}')
        width = 8 if param_type == 6 else 4
        values = []
        for value_pos in reader.records(value_ptr, value_count, width, 'parameter values'):
            if param_type in (0, 1, 2, 4, 5):
                value_ptr = reader.ptr(value_pos, 'parameter value pointer')
                if not value_ptr:
                    value = None
                elif param_type in (0, 4, 5):
                    value = reader.string(value_ptr, label)
                elif param_type == 1:
                    value = reader.unpack('<I', value_ptr, 'integer value')[0]
                else:
                    value = reader.unpack('<f', value_ptr, 'float value')[0]
            elif param_type == 3:
                value = reader.unpack('<I', value_pos, 'ID value')[0]
            elif param_type == 6:
                child_ptr, child_count = reader.unpack('<Ih', value_pos, 'structure header')
                value = _parameters(reader, child_ptr, child_count, depth + 1, ancestors | {offset})
            else:
                value = reader.unpack('<I', value_pos, 'invalid parameter value')[0]
            values.append(value)
        rows.append({'id': parameter_id, 'type': label, 'values': values})
    return rows


def parse(data):
    """Parse an HSMData resource's first chunk into states, data and behaviour interfaces."""
    reader = _Reader(data)
    if len(reader.data) < 0x24:
        raise HSMError('resource is too short for an HSMData header')
    version, top_ptr, top_count, state_ptr, state_count, data_ptr, data_count, behaviour_ptr, behaviour_count = (
        reader.unpack('<9I', 0, 'HSMData header')
    )

    top_states = []
    for pos in reader.records(top_ptr, top_count, 0x0C, 'top-level states'):
        top_states.append({
            'definition_id': reader.unpack('<i', pos, 'top-level definition id')[0],
            'behaviour_id': reader.unpack('<i', pos + 4, 'top-level behaviour id')[0],
            'instance_id': reader.unpack('<i', pos + 8, 'top-level instance id')[0],
        })

    states = []
    for pos in reader.records(state_ptr, state_count, 0x20, 'state definitions'):
        behaviour_id, definition_id = reader.unpack('<2i', pos, 'state ids')
        entry_ptr, exit_ptr, parameter_ptr, data_definition_ptr, name_ptr = reader.unpack(
            '<5I', pos + 8, 'state pointers'
        )
        parameter_count, fallback, entry_count, child_count = reader.unpack(
            '<4B', pos + 0x1C, 'state counts'
        )
        entries = []
        for entry in reader.records(entry_ptr, entry_count, 0x10, 'state entry points'):
            entry_id = _hash(reader, entry, 'entry point id')
            action, spawn_count = reader.unpack('<2B', entry + 8, 'entry point header')
            spawn_ptr = reader.ptr(entry + 0x0C, 'entry point spawn pointer')
            spawns = []
            for spawn in reader.records(spawn_ptr, spawn_count, 0x10, 'state spawn data'):
                spawns.append({
                    'entry_id': _hash(reader, spawn, 'spawn entry point id'),
                    'state_id': reader.unpack('<i', spawn + 8, 'spawn state id')[0],
                    'name': reader.string(reader.ptr(spawn + 0x0C, 'spawn state name'), 'spawn state name'),
                })
            entries.append({'id': entry_id, 'action': action, 'spawns': spawns})

        exits = []
        for child in reader.records(exit_ptr, child_count, 0x10, 'child exit data'):
            child_id = reader.unpack('<i', child, 'child state id')[0]
            exit_points_ptr = reader.ptr(child + 4, 'child exit point pointer')
            exit_point_count = reader.unpack('<i', child + 8, 'child exit point count')[0]
            exit_name = reader.string(reader.ptr(child + 0x0C, 'child state name'), 'child state name')
            points = []
            for point in reader.records(exit_points_ptr, exit_point_count, 0x18, 'child exit points'):
                parent_id = _hash(reader, point, 'parent exit point id')
                target_id = _hash(reader, point + 8, 'child exit point id')
                action, spawn_count = reader.unpack('<2B', point + 0x10, 'child exit point header')
                spawn_ptr = reader.ptr(point + 0x14, 'child exit spawn pointer')
                spawns = []
                for spawn in reader.records(spawn_ptr, spawn_count, 0x10, 'child exit spawn data'):
                    spawns.append({
                        'entry_id': _hash(reader, spawn, 'spawn entry point id'),
                        'state_id': reader.unpack('<i', spawn + 8, 'spawn state id')[0],
                        'name': reader.string(reader.ptr(spawn + 0x0C, 'spawn state name'), 'spawn state name'),
                    })
                points.append({'parent_id': parent_id, 'child_id': target_id, 'action': action,
                               'spawns': spawns})
            exits.append({'state_id': child_id, 'name': exit_name, 'points': points})

        data_definition = None
        if data_definition_ptr:
            dd_id = reader.unpack('<i', data_definition_ptr, 'data definition id')[0]
            path = reader.string(reader.ptr(data_definition_ptr + 4, 'data path pointer'), 'data path')
            json_ptr = reader.ptr(data_definition_ptr + 8, 'data JSON pointer')
            data_name = reader.string(reader.ptr(data_definition_ptr + 0x10, 'data name pointer'), 'data name')
            raw_json = reader.string(json_ptr, 'data JSON')
            try:
                value = json.dumps(json.loads(raw_json), indent=2, ensure_ascii=False)
            except (ValueError, TypeError):
                value = raw_json
            data_definition = {'id': dd_id, 'path': path, 'name': data_name, 'data': value}

        states.append({
            'id': definition_id, 'behaviour_id': behaviour_id,
            'name': reader.string(name_ptr, 'state name'), 'fallback': fallback,
            'entries': entries, 'exits': exits,
            'parameters': _parameters(reader, parameter_ptr, parameter_count),
            'data_definition': data_definition,
        })

    data_definitions = []
    for pos in reader.records(data_ptr, data_count, 0x14, 'data definitions'):
        definition_id = reader.unpack('<i', pos, 'data definition id')[0]
        path = reader.string(reader.ptr(pos + 4, 'data path pointer'), 'data path')
        raw_json = reader.string(reader.ptr(pos + 8, 'data JSON pointer'), 'data JSON')
        name = reader.string(reader.ptr(pos + 0x10, 'data name pointer'), 'data name')
        try:
            value = json.dumps(json.loads(raw_json), indent=2, ensure_ascii=False)
        except (ValueError, TypeError):
            value = raw_json
        data_definitions.append({'id': definition_id, 'path': path, 'name': name, 'data': value})

    behaviours = []
    for pos in reader.records(behaviour_ptr, behaviour_count, 0x20, 'behaviour interfaces'):
        identifier = _hash(reader, pos, 'behaviour interface id')
        name = reader.string(reader.ptr(pos + 8, 'behaviour name pointer'), 'behaviour name')
        entry_ptr, exit_ptr, parameter_ptr = reader.unpack('<3I', pos + 0x0C, 'behaviour pointers')
        parameter_count, entry_count, exit_count = reader.unpack('<ihh', pos + 0x18, 'behaviour counts')
        entries = [_hash(reader, p, 'behaviour entry point id')
                   for p in reader.records(entry_ptr, entry_count, 8, 'behaviour entry points')]
        exits = [_hash(reader, p, 'behaviour exit point id')
                 for p in reader.records(exit_ptr, exit_count, 8, 'behaviour exit points')]
        behaviours.append({
            'id': identifier, 'name': name, 'entries': entries, 'exits': exits,
            'parameters': _parameters(reader, parameter_ptr, parameter_count),
        })

    return {'version': version, 'top_states': top_states, 'states': states,
            'data_definitions': data_definitions, 'behaviours': behaviours}


def format_text(hsm):
    """Format parsed HSM data as a readable report for export."""
    lines = [f'HSMData version {hsm["version"]}', '']
    if hsm['top_states']:
        lines.extend(['Top-level states:'])
        lines.extend(f'  definition {s["definition_id"]}, behaviour {s["behaviour_id"]}, '
                     f'instance {s["instance_id"]}' for s in hsm['top_states'])
        lines.append('')
    lines.append('State definitions:')
    for state in hsm['states']:
        lines.append(f'  {state["name"] or "(unnamed)"} [{state["id"]}] behaviour {state["behaviour_id"]}')
        for entry in state['entries']:
            lines.append(f'    entry {entry["id"]} (action {entry["action"]})')
            lines.extend(f'      -> {spawn["name"]} [{spawn["state_id"]}]' for spawn in entry['spawns'])
        for child in state['exits']:
            lines.append(f'    child exit: {child["name"]} [{child["state_id"]}]')
            for point in child['points']:
                lines.append(f'      {point["parent_id"]} -> {point["child_id"]} (action {point["action"]})')
                lines.extend(f'        -> {spawn["name"]} [{spawn["state_id"]}]' for spawn in point['spawns'])
        for param in state['parameters']:
            lines.append(f'    parameter {param["id"]} ({param["type"]}): {param["values"]}')
        if state['data_definition']:
            dd = state['data_definition']
            lines.append(f'    data: {dd["name"]} ({dd["path"]})')
            lines.extend('      ' + row for row in dd['data'].splitlines())
    lines.extend(['', 'Data definitions:'])
    for definition in hsm['data_definitions']:
        lines.append(f'  {definition["name"]} [{definition["id"]}]: {definition["path"]}')
        lines.extend('    ' + row for row in definition['data'].splitlines())
    lines.extend(['', 'Behaviour interfaces:'])
    for behaviour in hsm['behaviours']:
        lines.append(f'  {behaviour["name"]} [{behaviour["id"]}]')
        lines.extend(f'    entry point: {entry}' for entry in behaviour['entries'])
        lines.extend(f'    exit point: {exit_id}' for exit_id in behaviour['exits'])
        for param in behaviour['parameters']:
            lines.append(f'    parameter {param["id"]} ({param["type"]}): {param["values"]}')
    return '\n'.join(lines) + '\n'
