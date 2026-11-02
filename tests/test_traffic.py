import struct
import unittest

from bndlx import traffic


def sample_resource():
    hull_table = 0x48
    hull = 0x4C
    sections = 0x8C
    rungs = 0xBC
    data = bytearray(rungs + 0x40)
    struct.pack_into('<BBHI', data, 0, 3, 0, 1, len(data))
    struct.pack_into('<i', data, 0x0C, hull_table)
    struct.pack_into('<i', data, hull_table, hull)
    data[hull] = 1
    struct.pack_into('<H', data, hull + 6, 2)
    struct.pack_into('<i', data, hull + 0x10, sections)
    struct.pack_into('<i', data, hull + 0x14, rungs)
    struct.pack_into('<IB', data, sections, 0, 2)
    struct.pack_into('<8f', data, rungs, 10, 20, 30, 0, 15, 20, 35, 0)
    struct.pack_into('<8f', data, rungs + 0x20, 20, 20, 40, 0, 25, 20, 45, 0)
    return data


class TrafficTests(unittest.TestCase):
    def test_reads_lane_hull_and_projects_xz_bounds(self):
        result = traffic.parse(sample_resource())
        self.assertEqual(result['version'], 3)
        self.assertEqual(len(result['hulls']), 1)
        self.assertEqual(len(result['hulls'][0]['lines']), 1)
        self.assertEqual(result['bounds'], (10.0, 30.0, 20.0, 40.0))

    def test_rejects_truncated_header(self):
        with self.assertRaisesRegex(traffic.TrafficError, 'too short'):
            traffic.parse(bytes(12))

    def test_rejects_invalid_hull_pointer(self):
        data = sample_resource()
        struct.pack_into('<i', data, 0x48, len(data) + 4)
        with self.assertRaisesRegex(traffic.TrafficError, 'outside'):
            traffic.parse(data)


if __name__ == '__main__':
    unittest.main()
