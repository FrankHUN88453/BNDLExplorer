import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

from bndlx import gnames, ts_names  # noqa: E402
from bndlx.labels import FIELD_NAME_ALIASES, FIELD_NAMES, Labels  # noqa: E402
from import_ts_names import decode_field_hash  # noqa: E402


class TypeScriptNameMapTests(unittest.TestCase):
    def test_source_hash_bytes_are_little_endian(self):
        self.assertEqual(decode_field_hash('AD_51_CA_80'), 0x80CA51AD)
        self.assertEqual(decode_field_hash('-64_8A_B3_D4'), 0xD4B38A64)
        with self.assertRaisesRegex(ValueError, 'four hash bytes'):
            decode_field_hash('AD_51_CA')

    def test_bundled_field_names_are_hash_verified(self):
        self.assertGreater(len(FIELD_NAMES), 2500)
        self.assertTrue(all(gnames.name_hash(name) == value for value, name in FIELD_NAMES.items()))
        self.assertTrue(all(gnames.name_hash(name) == value
                            for value, names in FIELD_NAME_ALIASES.items() for name in names))
        self.assertTrue(FIELD_NAME_ALIASES)

    def test_field_name_precedence_and_collision_source(self):
        value = gnames.name_hash('Absolute')
        labels = Labels.__new__(Labels)
        labels.user = {}
        labels.found = {}
        labels.user[value] = 'UserLabel'
        labels.found[value] = 'ScannedLabel'
        self.assertEqual(labels.name(value), 'UserLabel')
        labels.user.clear()
        self.assertEqual(labels.name(value), 'ScannedLabel')
        labels.found.clear()
        self.assertEqual(labels.name(value), 'Absolute')
        self.assertIn('TypeScript hash maps', labels.source(value))
        if value in FIELD_NAME_ALIASES:
            self.assertIn('collision candidates', labels.source(value))

    def test_event_and_car_id_tables_are_available(self):
        self.assertGreater(len(ts_names.EVENT_NAMES), 800)
        self.assertGreater(len(ts_names.CAR_NAMES), 200)
        self.assertEqual(ts_names.EVENT_NAMES[0xBBC60800], 'Rolling Stock')
        self.assertTrue(any(name.lower() == 'alfa romeo 4c' for name in ts_names.CAR_NAMES.values()))


if __name__ == '__main__':
    unittest.main()
