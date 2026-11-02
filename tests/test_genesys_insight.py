"""Generated Genesys pseudocode and heuristic x86 field-reference disassembly."""
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bndlx import genesys_insight  # noqa: E402
from bndlx.genesys import Field, GType, Node, TypeDB  # noqa: E402


class Labels:
    def __init__(self):
        self.names = {0x4E616D65: 'Name', 0x506F7365: 'Pose'}

    def name(self, field_hash):
        return self.names.get(field_hash)


def check(condition, message):
    print(('ok    ' if condition else 'FAIL  ') + message)
    if not condition:
        raise AssertionError(message)


def make_pe(path, code):
    pe_offset = 0x80
    optional_size = 224
    section_offset = pe_offset + 24 + optional_size
    raw_offset = 0x200
    image = bytearray(raw_offset + len(code))
    image[:2] = b'MZ'
    struct.pack_into('<I', image, 0x3C, pe_offset)
    image[pe_offset:pe_offset + 4] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', image, pe_offset + 4, 0x14C, 1, 0, 0, 0, optional_size, 0)
    optional = pe_offset + 24
    struct.pack_into('<H', image, optional, 0x10B)
    struct.pack_into('<I', image, optional + 16, 0x1000)
    struct.pack_into('<I', image, optional + 28, 0x400000)
    image[section_offset:section_offset + 8] = b'.text\0\0\0'
    struct.pack_into('<IIII', image, section_offset + 8, len(code), 0x1000, len(code), raw_offset)
    struct.pack_into('<I', image, section_offset + 36, 0x60000020)
    image[raw_offset:raw_offset + len(code)] = code
    with open(path, 'wb') as f:
        f.write(image)


def main():
    types = TypeDB()
    base = GType(
        0x1233, 7, 'Demo.Base', 0x10, 2, (0xABCE, 1),
        fields=[Field(0, 0, 0, 0x42617365, 4, 0x8, 1, 2, 0, 0)]
    )
    root = GType(
        0x1234, 7, 'Demo.Object', 0x30, 2, (0xABCD, 1),
        fields=[
            Field(0x10, 0, 0, 0x4E616D65, 4, 0x10, 1, 2, 0, 0),
            Field(0x20, 0, 0, 0x506F7365, 4, 0x20, 1, 2, 0, 1),
        ]
    )
    root.base = base.id
    types.types[base.id] = base
    types.types[root.id] = root
    labels = Labels()
    source = genesys_insight.schema_source(types, root, labels)
    check('struct Demo_Object : Demo_Base' in source and 'Name' in source and '+0x10' in source,
          'schema pseudocode includes field names and offsets')

    node = Node(root.id, 0)
    node.fields = {0x4E616D65: 7, 0x506F7365: [1.0, 2.0]}
    object_code = genesys_insight.object_source(types, node, labels, 0x55)
    check('object_0x55' in object_code and 'Name = 7' in object_code,
          'object pseudocode includes serialized values')

    code = bytes.fromhex('55 8B EC 8B 45 10 8B 49 08 8B 41 10 8B 51 20 C3')
    with tempfile.TemporaryDirectory(prefix='bndlx_genesys_insight_') as folder:
        exe = os.path.join(folder, 'NFS13.exe')
        make_pe(exe, code)
        candidates = genesys_insight.assembly_candidates(exe, root, labels, types=types)
    matched = {offset for c in candidates for offset, _ in c['matches']}
    check({0x8, 0x10, 0x20} <= matched,
          'PE scanner finds x86 field-offset references from derived and base types')
    report = genesys_insight.candidate_report(candidates)
    check('confidence=low' in report and 'not proven' in report,
          'assembly report labels matches as heuristic')

    with tempfile.TemporaryDirectory(prefix='bndlx_genesys_stack_') as folder:
        stack_exe = os.path.join(folder, 'stack.exe')
        make_pe(stack_exe, bytes.fromhex('55 8B EC 8B 45 10 C3'))
        stack_candidates = genesys_insight.assembly_candidates(stack_exe, root, labels)
    check(not stack_candidates, 'stack-frame offsets are excluded from object field matches')

    print('0 failure(s)')


if __name__ == '__main__':
    main()
