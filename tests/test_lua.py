"""LuaCode source handling, export, and import checks."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bndlx import luafile, names, ops  # noqa: E402
from bndlx.bundle import Bundle  # noqa: E402
from bndlx.restypes import T_LUA_CODE  # noqa: E402


def check(condition, message):
    print(('ok    ' if condition else 'FAIL  ') + message)
    if not condition:
        raise AssertionError(message)


def main():
    source = b'local message = "hello"\nreturn message\n'
    check(luafile.decode(source) == (source.decode(), 'utf-8'), 'decode UTF-8 Lua source')
    check(names.plausible('scripts/example.lua', T_LUA_CODE)
          and not names.plausible('scripts/example.json', T_LUA_CODE),
          'recognize LuaCode resource names only as Lua scripts')

    bom_source = b'\xef\xbb\xbfreturn "hello"\n'
    text, encoding = luafile.decode(bom_source)
    check(luafile.encode(text, encoding) == bom_source, 'preserve UTF-8 BOM')

    latin_source = b'-- caf\xe9\nreturn true\n'
    text, encoding = luafile.decode(latin_source)
    check(luafile.encode(text, encoding) == latin_source, 'preserve Latin-1 Lua source')

    try:
        luafile.decode(b'\x1bLua\x53\x00\x19\x93')
        check(False, 'reject Lua bytecode as editable text')
    except ValueError:
        check(True, 'reject Lua bytecode as editable text')

    with tempfile.TemporaryDirectory(prefix='bndlx_lua_test_') as folder:
        bundle = Bundle()
        resource = bundle.new_resource(0x1234, T_LUA_CODE, [source, b'', b'', b''])
        bundle.add(resource)

        exported = ops.export_native(bundle, resource, folder)
        check(exported.lower().endswith('.lua'), 'native export uses .lua extension')
        with open(exported, 'rb') as f:
            check(f.read() == source, 'native export preserves source bytes')

        replacement = b'return "edited"\n'
        replacement_path = os.path.join(folder, '00001234.lua')
        with open(replacement_path, 'wb') as f:
            f.write(replacement)
        changes, errors = ops.import_folder(bundle, folder, None)
        check(not errors and any(row[0] == resource.id for row in changes),
              'import folder replaces LuaCode from .lua')
        check(resource.data(0) == replacement, 'imported source bytes are stored unchanged')
        saved_path = os.path.join(folder, 'saved.BNDL')
        bundle.save(saved_path)
        check(Bundle.open(saved_path).find(resource.id).data(0) == replacement,
              'save bundle persists edited Lua source')

        with open(replacement_path, 'wb') as f:
            f.write(b'\x1bLua\x53\x00')
        before = resource.data(0)
        _, errors = ops.import_folder(bundle, folder, None)
        check(errors and resource.data(0) == before, 'binary source replacement is rejected without mutation')

        binary = bundle.new_resource(0x5678, T_LUA_CODE, [b'\x1bLua\x53\x00', b'', b'', b''])
        bundle.add(binary)
        binary_export = ops.export_native(bundle, binary, folder)
        check(binary_export.lower().endswith('.bres') and os.path.isfile(binary_export),
              'native export preserves binary LuaCode as a resource file')

    print('0 failure(s)')


if __name__ == '__main__':
    main()
