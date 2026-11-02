import struct
import unittest

from bndlx import hsm


def sample_resource():
    name = b'TestState\0'
    name_offset = 0x24 + 0x20
    header = struct.pack('<9I', 3, 0, 0, 0x24, 1, 0, 0, 0, 0)
    state = struct.pack('<iiIIIII4B', 7, 11, 0, 0, 0, 0, name_offset, 0, 0, 0, 0)
    return header + state + name


def sample_with_parameter():
    header = struct.pack('<9I', 3, 0, 0, 0x24, 1, 0, 0, 0, 0)
    name_offset = 0x24 + 0x20 + 0x10 + 8
    state = struct.pack('<iiIIIII4B', 7, 11, 0, 0, 0x44, 0, name_offset, 1, 0, 0, 0)
    parameter = struct.pack('<8sIhh', b'PARAM001', 0x54, 1, 1)
    value_pointer = struct.pack('<I', 0x58)
    value = struct.pack('<I', 1234)
    return header + state + parameter + value_pointer + value + b'TestState\0'


class HSMTests(unittest.TestCase):
    def test_reads_state_from_resource_chunk(self):
        parsed = hsm.parse(sample_resource())
        self.assertEqual(parsed['version'], 3)
        self.assertEqual(parsed['states'][0]['name'], 'TestState')
        self.assertEqual(parsed['states'][0]['id'], 11)
        self.assertIn('TestState [11]', hsm.format_text(parsed))

    def test_reads_typed_parameter_values(self):
        parsed = hsm.parse(sample_with_parameter())
        parameter = parsed['states'][0]['parameters'][0]
        self.assertEqual(parameter['type'], 'Int')
        self.assertEqual(parameter['values'], [1234])

    def test_rejects_truncated_header(self):
        with self.assertRaisesRegex(hsm.HSMError, 'too short'):
            hsm.parse(b'\0' * 12)

    def test_rejects_null_pointer_with_records(self):
        data = struct.pack('<9I', 3, 0, 0, 0, 1, 0, 0, 0, 0) + bytes(0x20)
        with self.assertRaisesRegex(hsm.HSMError, 'null pointer'):
            hsm.parse(data)


if __name__ == '__main__':
    unittest.main()
