import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bndlx.bundle import Bundle, Resource
from bndlx.compare import bundle_differences, same_bundle_name
from bndlx.genesys import Node, TypeDB
from bndlx.restypes import T_GOBJECT, T_GTYPE


class EmptyLabels:
    def name(self, field_hash):
        return None


class CompareTests(unittest.TestCase):
    def bundle_with(self, resource):
        bundle = Bundle()
        bundle.resources.append(resource)
        return bundle

    def resource(self, rid, data, type_id=1):
        resource = Resource(rid, type_id)
        resource._e = '<'
        resource.set_data(0, data)
        return resource

    def test_same_bundle_name_ignores_directory_and_case(self):
        self.assertTrue(same_bundle_name(r'C:\one\WORLD.BNDL', r'D:\two\world.bndl'))
        self.assertFalse(same_bundle_name(r'C:\one\WORLD.BNDL', r'D:\two\world.bundle'))

    def test_reports_added_removed_and_chunk_differences(self):
        left = self.bundle_with(self.resource(1, b'old'))
        left.resources.append(self.resource(2, b'removed'))
        right = self.bundle_with(self.resource(1, b'new'))
        right.resources.append(self.resource(3, b'added'))

        rows = bundle_differences(left, right, {}, EmptyLabels())

        self.assertIn((1, 'Chunk 0 data', '3 bytes (contents differ)', '3 bytes (contents differ)'), rows)
        self.assertIn((2, 'Resource', 'removed (0x1)', '<absent>'), rows)
        self.assertIn((3, 'Resource', '<absent>', 'added (0x1)'), rows)

    def test_field_values_are_flattened_with_names(self):
        from bndlx.compare import _field_values

        class Field:
            name_hash = 0x4142

        class Schema:
            fields = [Field()]

        class Types:
            def get(self, type_id):
                return Schema()

        node = Node(7, 0)
        node.fields[0x4142] = 42
        self.assertEqual(_field_values(node, Types(), EmptyLabels()), {'AB': '42'})

    def test_reports_decoded_genesys_field_differences(self):
        def object_bundle(value):
            bundle = Bundle()
            schema = Resource(0x100, T_GTYPE)
            schema._e = '<'
            schema_data = bytearray(0x40)
            struct.pack_into('<BBH', schema_data, 0, 2, 7, 1)
            struct.pack_into('<I', schema_data, 0x0C, 0x24)
            struct.pack_into('<II', schema_data, 0x18, 16, 4)
            struct.pack_into('<6I', schema_data, 0x24, 0, 0, 0, 0x4142, 4, 12)
            struct.pack_into('<HBB', schema_data, 0x3C, 1, 4, 0)
            schema.set_body(schema_data, [(77, 0x24)])

            obj = Resource(0x200, T_GOBJECT)
            obj._e = '<'
            body = struct.pack('<IIIi', 0, 0, 0, value)
            obj.set_body(body, [(schema.id, 0)])
            bundle.resources = [schema, obj]
            return bundle

        rows = bundle_differences(object_bundle(10), object_bundle(20), TypeDB(), EmptyLabels())

        self.assertIn((0x200, 'AB', '10', '20'), rows)


if __name__ == '__main__':
    unittest.main()
