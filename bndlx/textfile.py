"""TextFile (resource type 0x70): u32 length + text (JSON widget definitions, XML manifests), NUL, padded to 16."""
import struct


def read(res, e):
    c = res.data(0)
    n = struct.unpack_from(e + 'I', c, 0)[0]
    return bytes(c[4:4 + n])


def build(text, e):
    body = struct.pack(e + 'I', len(text)) + bytes(text) + b'\0'
    return body + b'\0' * ((-len(body)) % 16)


def swap(res, src_e, dst_e):
    return build(read(res, src_e), dst_e)


def decode(raw):
    for enc in ('utf-8', 'latin1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode('latin1', 'replace')
