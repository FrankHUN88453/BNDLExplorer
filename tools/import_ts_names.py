"""Import the non-rebuilder name maps from a MostWanted TypeScript checkout.

Usage: python tools/import_ts_names.py path\\to\\MostWanted
Writes bndlx/ts_names.py; signed-byte prefixes in foundHashes.txt are notation
artifacts and are removed before decoding the little-endian hash.
"""
import collections
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bndlx.gnames import name_hash


def parse_field_hashes(root):
    names = collections.defaultdict(set)
    preferred = {}
    sources = ('type-extractor-tool/foundHashes.txt', 'ParserStuff-LE/foundHashes.txt')
    invalid = []
    for relative in sources:
        path = os.path.join(root, relative)
        with open(path, encoding='utf-8-sig') as f:
            for line_number, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    raw, name = line.split(',', 1)
                    value = decode_field_hash(raw)
                    name = name.strip()
                    if not name or name_hash(name) != value:
                        raise ValueError('name does not match its Genesys hash')
                except (ValueError, TypeError) as exc:
                    invalid.append(f'{relative}:{line_number}: {exc}')
                    continue
                names[value].add(name)
                preferred.setdefault(value, name)
    return names, preferred, invalid


def decode_field_hash(text):
    """Decode the source's underscore-separated bytes as a little-endian u32."""
    value = bytes.fromhex(text.replace('-', '').replace('_', ' '))
    if len(value) != 4:
        raise ValueError('expected four hash bytes')
    return int.from_bytes(value, 'little')


def parse_object(text, variable):
    match = re.search(
        rf'export\s+const\s+{re.escape(variable)}\s*=\s*\{{(.*?)^\}};?',
        text, re.MULTILINE | re.DOTALL)
    if not match:
        raise ValueError(f'Could not find TypeScript map {variable}')
    values = {}
    for row in re.finditer(r'^\s*(0x[0-9a-fA-F]+)\s*:\s*("(?:\\.|[^"\\])*")\s*,?\s*$',
                           match.group(1), re.MULTILINE):
        values[int(row.group(1), 16)] = json.loads(row.group(2))
    if not values:
        raise ValueError(f'The TypeScript map {variable} contains no numeric entries')
    return values


def merge_maps(text, variables):
    merged = {}
    for variable in variables:
        for key, value in parse_object(text, variable).items():
            merged.setdefault(key, value)
    return merged


def dump_dict(name, values):
    rows = [f'{name} = {{']
    rows.extend(f'    0x{key:08X}: {values[key]!r},' for key in sorted(values))
    rows.append('}')
    return '\n'.join(rows)


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    source = os.path.abspath(sys.argv[1])
    fields, preferred, invalid = parse_field_hashes(source)
    # Prefer the type extractor's contextual choice where the 32-bit hash collides;
    # retain every verified candidate in FIELD_NAME_ALIASES.
    field_names = {}
    for value, candidates in fields.items():
        field_names[value] = preferred[value]
    aliases = {key: tuple(sorted(values, key=lambda name: (name != field_names[key], name.casefold(), name)))
               for key, values in fields.items() if len(values) > 1}

    helper = os.path.join(source, 'helpers')
    with open(os.path.join(helper, 'event_names.ts'), encoding='utf-8-sig') as f:
        events = merge_maps(f.read(), ('evtname_BE', 'evtname_LE'))
    with open(os.path.join(helper, 'car_names.ts'), encoding='utf-8-sig') as f:
        cars = merge_maps(f.read(), ('carname_LE', 'carname_BE'))

    out = [
        '"""Verified Genesys hash labels and event / car ids imported from the MostWanted TypeScript tools."""',
        '',
        '# Names sharing a 32-bit field hash are retained in FIELD_NAME_ALIASES.',
        dump_dict('FIELD_NAMES', field_names),
        '',
        'FIELD_NAME_ALIASES = {',
    ]
    out.extend(f'    0x{key:08X}: {values!r},' for key, values in sorted(aliases.items()))
    out += ['}', '', dump_dict('EVENT_NAMES', events), '', dump_dict('CAR_NAMES', cars), '']
    output = os.path.join(ROOT, 'bndlx', 'ts_names.py')
    with open(output, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(out))
    print(f'{len(field_names)} verified field hashes ({len(aliases)} collisions), '
          f'{len(events)} event ids, {len(cars)} car ids; skipped {len(invalid)} unverified rows -> {output}')
    for row in invalid[:5]:
        print(f'warning: {row}')

if __name__ == '__main__':
    main()
