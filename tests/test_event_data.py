import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bndlx import event_data  # noqa: E402


def event_blob(offsets):
    table_offset = 24
    blob = bytearray(max(256, table_offset + 4 * len(offsets), max(offsets, default=0) + 0x100))
    struct.pack_into('>I', blob, 8, event_data.ROOT_TYPE_VERSION)
    struct.pack_into('<iI', blob, 0, -4, 0xABC)
    struct.pack_into('<3I', blob, 12, 0x1234, table_offset, len(offsets))
    for index, offset in enumerate(offsets):
        struct.pack_into('<i', blob, table_offset + index * 4, offset)
    return blob


class EventDataTests(unittest.TestCase):
    def test_reads_typed_event_pointer_table(self):
        blob = event_blob([40, 64, 0])
        struct.pack_into('>I', blob, 48, 0x903BCC47)
        struct.pack_into('>I', blob, 72, 0x1773054A)
        struct.pack_into('<I', blob, 40 + 0x84, 0x8C6BC)
        catalog = event_data.parse(blob)
        self.assertEqual((catalog.handle_entry, catalog.handle_offset), (-4, 0xABC))
        self.assertEqual(catalog.type_version, event_data.ROOT_TYPE_VERSION)
        self.assertEqual(catalog.unknown_id, 0x1234)
        self.assertEqual(catalog.table_offset, 24)
        self.assertEqual(catalog.event_start_offset, 24)
        root_fields = {field[0]: field for field in catalog.fields}
        self.assertEqual(root_fields['unknownId'][3], 0x1234)
        self.assertTrue(root_fields['unknownId'][5])
        self.assertEqual(root_fields['allEvents'][2], 'Ptr[3]')
        self.assertEqual([record.kind for record in catalog.records],
                         ['Offline event', 'Online event', 'Unknown event type'])
        self.assertEqual(catalog.records[0].name_id, 0x8C6BC)
        self.assertEqual(event_data.event_name(catalog.records[0]), 'Rolling Stock')
        offline_fields = {field[0]: field for field in catalog.records[0].fields}
        self.assertTrue(offline_fields['gameChangerId'][5])
        self.assertEqual(offline_fields['gameChangerId'][4], '>I')
        self.assertTrue(offline_fields['trafficEnabled'][5])
        self.assertFalse(offline_fields['checkpoints'][5])
        self.assertIsNone(catalog.records[2].offset)

    def test_returns_none_for_other_resource_types(self):
        self.assertIsNone(event_data.parse(bytes(40)))

    def test_accepts_tables_larger_than_the_old_fixed_cap(self):
        count = 10001
        blob = bytearray(24 + count * 4)
        struct.pack_into('>I', blob, 8, event_data.ROOT_TYPE_VERSION)
        struct.pack_into('<3I', blob, 12, 0x1234, 24, count)
        catalog = event_data.parse(blob)
        self.assertEqual(len(catalog.records), count)

    def test_reads_pointer_arrays_as_editable_entries(self):
        blob = event_blob([40])
        blob.extend(bytes(40))
        struct.pack_into('>I', blob, 48, 0x903BCC47)
        struct.pack_into('<I', blob, 40 + 0x64, 320)
        struct.pack_into('<H', blob, 40 + 0xDC, 2)
        struct.pack_into('<2I', blob, 320, 0x111, 0x222)
        record = event_data.parse(blob).records[0]
        checkpoints = next(array for array in record.arrays if array.name == 'checkpoints')
        self.assertEqual((checkpoints.offset, checkpoints.count, checkpoints.fmt), (320, 2, '<I'))
        struct.pack_into(checkpoints.fmt, blob, checkpoints.offset + 4, 0x333)
        self.assertEqual(struct.unpack_from('<I', blob, checkpoints.offset + 4)[0], 0x333)

    def test_rejects_invalid_table_and_record_offsets(self):
        blob = event_blob([])
        struct.pack_into('<3I', blob, 12, 0, len(blob) + 4, 1)
        with self.assertRaisesRegex(event_data.EventDataError, 'Invalid event pointer table'):
            event_data.parse(blob)
        blob = event_blob([40])
        struct.pack_into('<i', blob, 24, 0x7FFFFFFF)
        with self.assertRaisesRegex(event_data.EventDataError, 'invalid object offset'):
            event_data.parse(blob)


if __name__ == '__main__':
    unittest.main()
