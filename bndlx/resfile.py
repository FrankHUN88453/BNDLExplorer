"""Single-resource files (.bres): one resource with all of its data, for exporting, importing and sharing.

  8 bytes  b'BNDLRES1'
  u32      JSON header length (little-endian), then the JSON header (UTF-8):
           {"id", "type", "type_name", "platform", "stream", "flags", "import_offset", "import_count",
            "us_bits", "cs_bits", "sizes", "name", "debug_type", "tool"}
  the four memory chunks back to back (sizes from the header), exactly as the game loads them.
"""
import json
import struct

from .bundle import Resource
from .restypes import name as type_name

MAGIC = b'BNDLRES1'


def dump(res, platform, tool=''):
    chunks = res.chunks()
    head = {
        'id': f'{res.id:#018x}', 'type': res.type, 'type_name': type_name(res.type), 'platform': platform,
        'stream': res.stream, 'flags': res.flags, 'import_offset': res.import_offset,
        'import_count': res.import_count, 'us_bits': list(res.us_bits), 'cs_bits': list(res.cs_bits),
        'sizes': [len(c) for c in chunks], 'name': res.name, 'debug_type': res.debug_type, 'tool': tool,
    }
    js = json.dumps(head, indent=1).encode('utf-8')
    return MAGIC + struct.pack('<I', len(js)) + js + b''.join(chunks)


def is_resfile(data):
    return data[:8] == MAGIC


def load(data):
    """-> (Resource, platform)"""
    if not is_resfile(data):
        raise ValueError('not a BNDL Explorer resource file (.bres)')
    n = struct.unpack_from('<I', data, 8)[0]
    head = json.loads(data[12:12 + n].decode('utf-8'))
    pos = 12 + n
    chunks = []
    for size in head['sizes']:
        chunks.append(bytes(data[pos:pos + size]))
        pos += size
    if pos > len(data):
        raise ValueError('the resource file is truncated')
    r = Resource(int(head['id'], 16), int(head['type']), int(head.get('stream', 0)), int(head.get('flags', 0)))
    r._e = '<' if head['platform'] == 'PC' else '>'
    r._data = chunks
    r._stored = [None] * 4
    r.import_offset = int(head.get('import_offset', 0))
    r.import_count = int(head.get('import_count', 0))
    r.us_bits = list(head.get('us_bits', [0] * 4))
    r.cs_bits = list(head.get('cs_bits', [0] * 4))
    r.name = head.get('name', '')
    r.debug_type = head.get('debug_type', '')
    r.modified = True
    return r, head['platform']
