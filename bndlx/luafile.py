"""Plain-text LuaCode resource handling."""


def decode(data):
    """Decode source text, rejecting Lua bytecode and other binary payloads."""
    if any(byte == 0 or (byte < 32 and byte not in (9, 10, 12, 13)) for byte in data):
        raise ValueError('LuaCode is binary or not plain-text source.')
    if data.startswith(b'\xef\xbb\xbf'):
        return data.decode('utf-8-sig'), 'utf-8-sig'
    try:
        return data.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        return data.decode('latin1'), 'latin1'


def encode(source, encoding):
    """Encode edited Lua source using the resource's original text encoding."""
    return source.encode(encoding)
