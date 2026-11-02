"""Editing and saving embedded GenesysObject text fields."""
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bndlx.bundle import Bundle  # noqa: E402
from bndlx.genesys import Field, GType, Reader, STRINGBASE  # noqa: E402
from bndlx.restypes import T_GOBJECT  # noqa: E402


ROOT_TYPE = 0x1001
TEXT_FIELD = 0x44617461


def check(condition, message):
    print(('ok    ' if condition else 'FAIL  ') + message)
    if not condition:
        raise AssertionError(message)


def make_object(path):
    raw = bytearray(48)
    struct.pack_into('<III', raw, 0, 0, 0, 0)
    initial = b'old text\0'
    struct.pack_into('<II', raw, 12, 8, len(initial))
    raw[20:20 + len(initial)] = initial
    struct.pack_into('<QI', raw, 32, ROOT_TYPE, 0)
    bundle = Bundle()
    resource = bundle.new_resource(
        0x1234, T_GOBJECT, [raw, b'', b'', b''], import_offset=32, import_count=1
    )
    bundle.add(resource)
    bundle.save(path)


def reader_for(app, doc, resource):
    app.types.types = {
        ROOT_TYPE: GType(
            ROOT_TYPE, 7, 'TestObject', 8, 2, (0, 0),
            fields=[Field(STRINGBASE, 0, 0, TEXT_FIELD, 8, 12, 1, 2, 0)]
        ),
        STRINGBASE: GType(STRINGBASE, 6, 'StringBase', 8, 2, (0, 0)),
    }
    return Reader(app.types, doc.b.e).read_resource(resource)


def main():
    with tempfile.TemporaryDirectory(prefix='bndlx_genesys_text_') as folder:
        os.environ['APPDATA'] = os.path.join(folder, 'appdata')
        os.makedirs(os.environ['APPDATA'])
        from bndlx.app import App

        bundle_path = os.path.join(folder, 'object.BNDL')
        make_object(bundle_path)
        app = App([bundle_path])
        doc = app.docs[0]
        resource = doc.b.find(0x1234)
        node = reader_for(app, doc, resource)
        check(node.fields[TEXT_FIELD] == 'old text', 'read embedded Genesys text')

        check(app.write_string(doc, resource, 12, 'a longer edited text'),
              'apply a longer embedded string')
        resource = doc.b.find(0x1234)
        node = reader_for(app, doc, resource)
        check(node.fields[TEXT_FIELD] == 'a longer edited text',
              'reader follows relocated string data')

        doc.last_edit = (None, 0.0)
        check(app.write_string(doc, resource, 12, 'short'),
              'apply a shorter embedded string')
        resource = doc.b.find(0x1234)
        check(reader_for(app, doc, resource).fields[TEXT_FIELD] == 'short',
              'read shorter embedded string')

        doc.do_undo()
        resource = doc.b.find(0x1234)
        check(reader_for(app, doc, resource).fields[TEXT_FIELD] == 'a longer edited text',
              'undo restores prior Genesys string')

        saved_path = os.path.join(folder, 'saved.BNDL')
        doc.b.save(saved_path)
        saved = Bundle.open(saved_path).find(0x1234)
        check(reader_for(app, doc, saved).fields[TEXT_FIELD] == 'a longer edited text',
              'bundle save persists edited Genesys string')

    print('0 failure(s)')


if __name__ == '__main__':
    main()
