"""Evidence-labeled, source-like views of Genesys schemas and possible executable references."""
import collections
import re
import struct

from capstone import (
    Cs, CsError, CS_ARCH_X86, CS_MODE_32, CS_GRP_CALL, CS_GRP_JUMP, CS_GRP_RET, CS_OP_IMM, CS_OP_MEM
)
from capstone.x86 import X86_INS_JMP, X86_REG_EBP, X86_REG_ESP, X86_REG_INVALID

from .genesys import Node, Ref


_IDENT = re.compile(r'[^A-Za-z0-9_]')
_PRIMITIVES = {
    0: 'int', 1: 'uint', 2: 'float', 3: 'bool', 4: 'char',
}
_SIZES = {1: 8, 2: 16, 4: 32, 8: 64}


def identifier(name, fallback):
    name = _IDENT.sub('_', name or '').strip('_')
    if not name:
        name = fallback
    if name[0].isdigit():
        name = '_' + name
    return name


def type_name(types, tid):
    t = types.get(tid)
    if t is None:
        return f'unknown_type_{tid:X}'
    if t.kind == 5:
        return identifier(types.name(tid), f'enum_{tid:X}')
    if t.kind == 6:
        return identifier(types.name(tid), f'vector_{tid:X}')
    if t.kind == 7:
        return identifier(types.name(tid), f'struct_{tid:X}')
    return _PRIMITIVES.get(t.kind, f'opaque_{t.size}_bytes')


def _field_name(field, labels):
    return identifier(
        labels.name(field.name_hash) or f'field_{field.name_hash:08X}',
        f'field_{field.index}'
    )


def _field_type(types, field):
    child = types.get(field.type_id) if field.type_id else None
    if child is None:
        return f'bytes[{field.size}]'
    if child.id == 0x40404301:
        return 'char*'
    if child.kind == 5:
        return type_name(types, child.id)
    if child.kind == 6:
        return type_name(types, child.id)
    if child.kind == 7:
        return type_name(types, child.id)
    bits = _SIZES.get(child.size, child.size * 8)
    if child.kind == 2:
        return 'float' if bits == 32 else 'double' if bits == 64 else f'float{bits}'
    if child.kind == 3:
        return 'bool'
    if child.kind == 0:
        return f'int{bits}_t'
    if child.kind in (1, 4):
        return f'uint{bits}_t'
    if child.kind == 8:
        return 'ResourceHandle'
    return f'bytes[{field.size}]'


def schema_source(types, t, labels):
    """Create readable schema pseudocode. This is generated from metadata, not decompiled code."""
    lines = [
        '// INFERRED SCHEMA PSEUDOCODE — generated from GenesysType metadata.',
        '// This is not recovered game source and does not describe executable behavior.',
        f'// Type: {types.name(t.id)}  id=0x{t.id:X}  kind={t.kind}  size={t.size}  alignment={1 << t.align}',
        f'// Schema: {t.schema[0]:08X} / 0x{t.schema[1]:X}',
        '',
    ]
    if t.kind == 5:
        lines.append(f'enum {type_name(types, t.id)} {{')
        for f in t.fields:
            name = _field_name(f, labels)
            lines.append(f'    {name} = {f.offset}, // value hash 0x{f.name_hash:08X}')
        lines.append('};')
        return '\n'.join(lines)
    if t.kind != 7:
        lines.extend([
            f'// No struct fields are stored for this {type_name(types, t.id)} schema.',
            f'type {type_name(types, t.id)} = {genesys_kind_name(t.kind)};',
        ])
        return '\n'.join(lines)

    base = f' : {type_name(types, t.base)}' if t.base else ''
    lines.append(f'struct {type_name(types, t.id)}{base} {{')
    if not t.fields:
        lines.append('    // No fields in this schema.')
    for f in t.fields:
        name = _field_name(f, labels)
        value_type = _field_type(types, f)
        if f.flags & 1:
            value_type += '*'
        if f.flags & 8:
            value_type += '[]'
        elif f.flags & 2 and f.count > 1:
            value_type += f'[{f.count}]'
        lines.append(
            f'    {value_type} {name}; // +0x{f.offset:X}, size={f.size}, '
            f'count={f.count}, flags={f.flags:#04x}, name_hash=0x{f.name_hash:08X}'
        )
    lines.append('};')
    return '\n'.join(lines)


def genesys_kind_name(kind):
    return {0: 'signed integer', 1: 'unsigned integer', 2: 'floating point',
            3: 'boolean', 4: 'character', 6: 'vector', 8: 'resource handle'}.get(
                kind, f'opaque kind {kind}'
            )


def _literal(value):
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, Ref):
        return f'resource_ref(0x{value.id:X})'
    if isinstance(value, bytes):
        return f'bytes.fromhex({value.hex()!r})'
    if isinstance(value, list):
        return '[' + ', '.join(_literal(v) for v in value[:32]) + (
            ', ...' if len(value) > 32 else ''
        ) + ']'
    if isinstance(value, Node):
        return f'{type_name_placeholder(value.type)}(...)'
    if value is None:
        return 'null'
    if isinstance(value, float):
        return repr(value)
    return repr(value)


def type_name_placeholder(type_id):
    return f'GenesysType_0x{type_id:X}'


def _node_lines(types, labels, node, indent, ancestors, depth):
    if depth >= 24:
        return [indent + '// Maximum inferred nesting depth reached.']
    if node.type in ancestors:
        return [indent + '// Recursive type reference omitted.']
    schema = types.get(node.type)
    if schema is None:
        return [indent + f'// Missing type 0x{node.type:X}.']
    lines = [indent + f'{type_name(types, node.type)} {{']
    for f in schema.fields:
        value = node.fields.get(f.name_hash)
        name = _field_name(f, labels)
        if isinstance(value, Node):
            lines.append(indent + f'    {name} =')
            lines.extend(_node_lines(
                types, labels, value, indent + '        ', ancestors | {node.type}, depth + 1
            ))
        elif isinstance(value, list) and any(isinstance(v, Node) for v in value):
            lines.append(indent + f'    {name} = [')
            for item in value[:32]:
                if isinstance(item, Node):
                    lines.extend(_node_lines(
                        types, labels, item, indent + '        ', ancestors | {node.type}, depth + 1
                    ))
                else:
                    lines.append(indent + f'        {_literal(item)},')
            if len(value) > 32:
                lines.append(indent + '        // Additional array entries omitted.')
            lines.append(indent + '    ],')
        else:
            lines.append(indent + f'    {name} = {_literal(value)},')
    lines.append(indent + '}')
    return lines


def object_source(types, node, labels, resource_id):
    """Create a source-like type declaration plus literal values from one GenesysObject."""
    t = types.get(node.type)
    if t is None:
        return '// Cannot generate source-like output: the Genesys type is not loaded.'
    source = schema_source(types, t, labels)
    lines = [
        '',
        '// INFERRED INSTANCE PSEUDOCODE — values read from this serialized object.',
        '// Editing this generated text does not edit the bundle resource.',
        f'// Resource id: 0x{resource_id:X}',
        f'const {type_name(types, node.type)} object_0x{resource_id:X} =',
    ]
    lines.extend(_node_lines(types, labels, node, '', set(), 0))
    lines.append(';')
    return source + '\n' + '\n'.join(lines)


def _pe_code_sections(data):
    if len(data) < 0x40 or data[:2] != b'MZ':
        raise ValueError('File is not a valid PE executable (missing MZ header).')
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    if pe + 24 > len(data) or data[pe:pe + 4] != b'PE\0\0':
        raise ValueError('File is not a valid PE executable (missing PE header).')
    machine, section_count, _, _, _, optional_size, _ = struct.unpack_from('<HHIIIHH', data, pe + 4)
    if machine != 0x14C:
        raise ValueError(f'Unsupported PC executable machine type: 0x{machine:X}.')
    optional = pe + 24
    magic = struct.unpack_from('<H', data, optional)[0]
    if magic != 0x10B:
        raise ValueError(f'Expected a 32-bit PC executable, found optional-header type 0x{magic:X}.')
    image_base = struct.unpack_from('<I', data, optional + 28)[0]
    entry_rva = struct.unpack_from('<I', data, optional + 16)[0]
    section_table = optional + optional_size
    sections = []
    for index in range(section_count):
        off = section_table + index * 40
        if off + 40 > len(data):
            raise ValueError('PE section table is truncated.')
        name = data[off:off + 8].split(b'\0')[0].decode('ascii', 'replace')
        virtual_size, rva, raw_size, raw_off = struct.unpack_from('<IIII', data, off + 8)
        flags = struct.unpack_from('<I', data, off + 36)[0]
        if flags & 0x20000000 and raw_size:
            end = min(raw_off + raw_size, len(data))
            if raw_off < end:
                sections.append((name, image_base + rva, data[raw_off:end]))
    if not sections:
        raise ValueError('No executable code sections were found in the PC executable.')
    return image_base + entry_rva, sections


def _field_label_map(t, labels, types=None, seen=None):
    seen = set() if seen is None else seen
    if t.id in seen:
        return {}
    seen.add(t.id)
    out = collections.defaultdict(set)
    if t.base and types is not None:
        base = types.get(t.base)
        if base is not None:
            for offset, names in _field_label_map(base, labels, types, seen).items():
                out[offset].update(names)
    for f in t.fields:
        if 0 < f.offset < t.size:
            out[f.offset].add(_field_name(f, labels))
    return {offset: sorted(names) for offset, names in out.items()}


def _reachable_code_candidates(entrypoint, sections, field_map, progress=None, limit_per_offset=6):
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    ranges = sorted((start, start + len(code), name, code) for name, start, code in sections)
    queue = collections.deque([entrypoint])
    visited = set()
    per_offset = collections.Counter()
    output = []
    scanned_bytes = 0
    total_bytes = sum(len(code) for _, _, code in sections)
    while queue:
        block_start = queue.popleft()
        section = next((row for row in ranges if row[0] <= block_start < row[1]), None)
        if section is None or block_start in visited:
            continue
        section_start, section_end, section_name, code = section
        block = []
        address = block_start
        while section_start <= address < section_end and address not in visited and len(block) < 256:
            offset = address - section_start
            instructions = md.disasm(code[offset:min(offset + 32, len(code))], address, count=1)
            ins = next(instructions, None)
            if ins is None:
                break
            visited.add(ins.address)
            block.append(ins)
            scanned_bytes += ins.size
            if ins.group(CS_GRP_CALL):
                if ins.operands and ins.operands[0].type == CS_OP_IMM:
                    queue.append(ins.operands[0].imm)
                address += ins.size
                continue
            if ins.group(CS_GRP_JUMP):
                if ins.operands and ins.operands[0].type == CS_OP_IMM:
                    queue.append(ins.operands[0].imm)
                if ins.id == X86_INS_JMP:
                    break
                address += ins.size
                continue
            if ins.group(CS_GRP_RET):
                break
            address += ins.size
        else:
            if block and len(block) >= 256:
                queue.append(block[-1].address + block[-1].size)

        for index, ins in enumerate(block):
            matches = set()
            for operand in ins.operands:
                if (operand.type == CS_OP_MEM
                        and operand.mem.base not in (X86_REG_INVALID, X86_REG_EBP, X86_REG_ESP)):
                    for name in field_map.get(operand.mem.disp, ()):
                        matches.add((operand.mem.disp, name))
            if not matches:
                continue
            allowed = {
                pair for pair in matches
                if per_offset[pair[0]] < limit_per_offset
            }
            if not allowed:
                continue
            for offset, _ in allowed:
                per_offset[offset] += 1
            snippet = block[max(0, index - 3):index + 4]
            output.append({
                'section': section_name,
                'address': ins.address,
                'matches': allowed,
                'lines': [
                    f'{item.address:08X}  {item.mnemonic:<8} {item.op_str}'.rstrip()
                    for item in snippet
                ],
            })
        if progress:
            progress(scanned_bytes, total_bytes)

    output.sort(key=lambda x: (-len(x['matches']), x['address']))
    unique = []
    seen = set()
    for item in output:
        key = (item['address'], tuple(sorted(item['matches'])))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def assembly_candidates(exe_path, t, labels, progress=None, types=None):
    """Find reachable PC x86 instructions accessing schema offsets; these are heuristic, not confirmed callers."""
    with open(exe_path, 'rb') as f:
        data = f.read()
    entrypoint, sections = _pe_code_sections(data)
    field_map = _field_label_map(t, labels, types)
    if not field_map:
        return []
    candidates = _reachable_code_candidates(entrypoint, sections, field_map, progress)
    return [{
        'section': item['section'],
        'address': item['address'],
        'matches': sorted(item['matches']),
        'lines': item['lines'],
        'confidence': 'low',
    } for item in candidates[:24]]


def candidate_report(candidates):
    """Format candidate disassembly without presenting heuristics as proven source."""
    lines = [
        '// HEURISTIC ASSEMBLY CANDIDATES - not proven to belong to this type.',
        '// Candidates were found by matching field offsets; common offsets create false positives.',
    ]
    if not candidates:
        lines.append(
            '// No matching field-offset references were found in statically reachable code. '
            'Packed or indirectly dispatched code may not be visible.'
        )
        return '\n'.join(lines)
    for index, item in enumerate(candidates, 1):
        fields = ', '.join(f'{name} @ +0x{offset:X}' for offset, name in item['matches'])
        lines.append(
            f'\n// Candidate {index}: {item["section"]} + 0x{item["address"]:08X}; '
            f'confidence={item["confidence"]}; matched {fields}'
        )
        lines.extend(item['lines'])
    return '\n'.join(lines)
