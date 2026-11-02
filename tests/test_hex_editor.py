import os
import random
import tempfile
import unittest

from bndlx.hex_editor import HexDocument
from bndlx.new_ui import Dashboard


class HexDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp_dir.name, 'sample.bin')
        with open(self.path, 'wb') as source:
            source.write(bytes(range(64)))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_insert_overwrite_and_undo_redo(self):
        document = HexDocument(self.path)
        try:
            document.replace(4, 0, b'xyz')
            self.assertEqual(document.read(0, 10), b'\x00\x01\x02\x03xyz\x04\x05\x06')
            document.replace(1, 2, b'!')
            self.assertEqual(document.read(0, 8), b'\x00!\x03xyz\x04\x05')
            self.assertTrue(document.undo())
            self.assertEqual(document.read(0, 10), b'\x00\x01\x02\x03xyz\x04\x05\x06')
            self.assertTrue(document.undo())
            self.assertFalse(document.modified)
            self.assertEqual(document.read(0, 8), bytes(range(8)))
            self.assertTrue(document.redo())
            self.assertTrue(document.modified)
        finally:
            document.close()

    def test_search_crosses_chunk_boundaries(self):
        with open(self.path, 'wb') as source:
            source.write(b'a' * (1024 * 1024 - 1) + b'needle' + b'z')
        document = HexDocument(self.path)
        try:
            self.assertEqual(document.find(b'needle'), 1024 * 1024 - 1)
            self.assertEqual(document.find(b'needle', reverse=True), 1024 * 1024 - 1)
            self.assertEqual(document.find(b'missing'), -1)
        finally:
            document.close()

    def test_save_applies_staged_edits_and_save_as(self):
        document = HexDocument(self.path)
        target = os.path.join(self.temp_dir.name, 'copy.bin')
        document.replace(2, 2, b'ABCD')
        try:
            document.save(target)
            self.assertEqual(document.read(0, 8), b'\x00\x01ABCD\x04\x05')
            self.assertFalse(document.modified)
            with open(self.path, 'rb') as original:
                self.assertEqual(original.read(8), bytes(range(8)))
            with open(target, 'rb') as saved:
                self.assertEqual(saved.read(8), b'\x00\x01ABCD\x04\x05')
        finally:
            document.close()

    def test_save_replaces_original_after_closing_mapping(self):
        document = HexDocument(self.path)
        document.replace(0, 1, b'!')
        try:
            document.save()
            with open(self.path, 'rb') as saved:
                self.assertEqual(saved.read(4), b'!\x01\x02\x03')
            self.assertFalse(document.modified)
            self.assertEqual(document.read(0, 4), b'!\x01\x02\x03')
        finally:
            document.close()

    def test_snapshot_and_random_edits(self):
        document = HexDocument(self.path)
        expected = bytearray(range(64))
        snapshot = document.snapshot()
        randomizer = random.Random(7)
        try:
            edit_count = 0
            for _ in range(40):
                offset = randomizer.randrange(len(expected) + 1)
                delete_length = randomizer.randrange(min(4, len(expected) - offset) + 1)
                inserted = randomizer.randbytes(randomizer.randrange(4))
                revision = document.revision
                document.replace(offset, delete_length, inserted)
                edit_count += document.revision != revision
                expected[offset:offset + delete_length] = inserted
                self.assertEqual(document.read(0, document.size), bytes(expected))
            self.assertEqual(snapshot.read(0, snapshot.size), bytes(range(64)))
            for _ in range(edit_count):
                self.assertTrue(document.undo())
            self.assertEqual(document.read(0, document.size), bytes(range(64)))
            self.assertFalse(document.modified)
        finally:
            document.close()

    def test_analyze_and_compare(self):
        document = HexDocument(self.path)
        other_path = os.path.join(self.temp_dir.name, 'other.bin')
        with open(other_path, 'wb') as other_file:
            other_file.write(bytes([0, 1, 2, 255]))
        other = HexDocument(other_path)
        try:
            result = document.analyze()
            self.assertEqual(result['CRC32'], '100ECE8C')
            self.assertEqual(result['Checksum-8'], 'E0')
            self.assertEqual(result['Checksum-16'], '07E0')
            comparison = document.compare(other)
            self.assertEqual(comparison['first_difference'], 3)
            self.assertEqual(comparison['differing_bytes'], 1)
            self.assertEqual(comparison['left_size'], 64)
            self.assertEqual(comparison['right_size'], 4)
        finally:
            document.close()
            other.close()

    def test_custom_crc16_parameters(self):
        with open(self.path, 'wb') as source:
            source.write(b'123456789')
        document = HexDocument(self.path)
        try:
            result = document.analyze(custom_crc=(0x1021, 0xffff, 0))
            self.assertEqual(result['Custom CRC-16'], '29B1')
        finally:
            document.close()

    def test_dashboard_byte_cell_commits_direct_edit(self):
        document = HexDocument(self.path)
        editor = object.__new__(Dashboard)
        editor.hex_document = document
        editor.hex_status = ''
        editor.hex_selection_anchor = 0
        editor.hex_offset_input = '0'
        editor.hex_offset_base = 'Hexadecimal'
        try:
            self.assertTrue(editor._commit_hex_cell(7, 'A5'))
            self.assertEqual(document.read(7, 1), b'\xA5')
            self.assertEqual(document.cursor, 7)
            self.assertEqual(document.selection, (7, 8))
            self.assertTrue(document.modified)
        finally:
            document.close()

    def test_dashboard_ascii_edit_updates_bytes_and_preserves_unchanged_nonprintable_bytes(self):
        with open(self.path, 'wb') as source:
            source.write(b'a\x00\xffd')
        document = HexDocument(self.path)
        editor = object.__new__(Dashboard)
        editor.hex_document = document
        editor.hex_status = ''
        editor.hex_selection_anchor = 0
        editor.hex_offset_input = '0'
        editor.hex_offset_base = 'Hexadecimal'
        editor.hex_ascii_buffers = {}
        editor.hex_cell_buffers = {}
        editor.hex_text_encoding = 'Windows (ANSI)'
        try:
            self.assertTrue(editor._commit_ascii_row(0, 'a..d', 'ab...d'))
            self.assertEqual(document.read(0, 6), b'ab.\x00\xffd')
            self.assertEqual(document.selection, (0, 6))
            self.assertTrue(document.undo())
            self.assertEqual(document.read(0, 4), b'a\x00\xffd')
        finally:
            document.close()

    def test_dashboard_ascii_edit_rejects_unrepresentable_characters_without_mutating_bytes(self):
        document = HexDocument(self.path)
        editor = object.__new__(Dashboard)
        editor.hex_document = document
        editor.hex_status = ''
        editor.hex_offset_base = 'Hexadecimal'
        editor.hex_ascii_buffers = {}
        editor.hex_cell_buffers = {}
        editor.hex_text_encoding = 'Windows (ANSI)'
        try:
            self.assertFalse(editor._commit_ascii_row(0, 'abcd', 'ab\u03bbd'))
            self.assertIn('Windows (ANSI)', editor.hex_status)
            self.assertEqual(document.read(0, 4), bytes(range(4)))
        finally:
            document.close()

    def test_hex_display_settings_format_offsets_and_decode_encodings(self):
        self.assertEqual(Dashboard._format_hex_offset(31, 'Hexadecimal'), '0000001F')
        self.assertEqual(Dashboard._format_hex_offset(31, 'Decimal'), '00000031')
        self.assertEqual(Dashboard._format_hex_offset(31, 'Octal'), '00000037')
        self.assertEqual(Dashboard._parse_hex_offset('37', 'Octal'), 31)
        self.assertEqual(Dashboard._decode_hex_row(b'\x80', 'Windows (ANSI)'), '\u20ac')
        self.assertEqual(Dashboard._decode_hex_row(b'\x80', 'DOS/IBM-ASCII (OEM)'), '\u00c7')
        self.assertEqual(Dashboard._decode_hex_row(b'\x80', 'Macintosh'), '\u00c4')
        self.assertEqual(Dashboard._decode_hex_row(b'\xc1', 'EBCDIC'), 'A')
        editor = object.__new__(Dashboard)
        editor.hex_search_mode = 'TEXT'
        editor.hex_text_encoding = 'EBCDIC'
        self.assertEqual(editor._hex_pattern('A'), b'\xc1')

    def test_fit_to_width_uses_visible_byte_columns(self):
        editor = object.__new__(Dashboard)
        editor.hex_fit_width = True
        editor.hex_bytes_per_row = 16
        editor.hex_group_size = 1
        editor.hex_visible_columns = 'Hex'
        self.assertEqual(editor._effective_hex_bytes_per_row(1000), 22)
        editor.hex_visible_columns = 'Hex and text'
        self.assertEqual(editor._effective_hex_bytes_per_row(1000), 14)
        editor.hex_visible_columns = 'Text'
        self.assertEqual(editor._effective_hex_bytes_per_row(1000), 64)

    def test_find_all_collects_overlapping_matches_and_opens_results(self):
        with open(self.path, 'wb') as source:
            source.write(b'\x00\x00\x00')
        document = HexDocument(self.path)

        class InlineJobApp:
            job = None

            def run_job(self, label, work, completed):
                completed(work(lambda progress: None))

        editor = object.__new__(Dashboard)
        editor.hex_document = document
        editor.app = InlineJobApp()
        editor.hex_search_mode = 'HEX'
        editor.hex_search_input = '00 00'
        editor.hex_search_results = []
        editor.hex_search_pattern = b''
        editor.hex_results_visible = False
        editor.hex_status = ''
        try:
            editor._hex_find_all()
            self.assertEqual(editor.hex_search_results, [0, 1])
            self.assertEqual(editor.hex_search_pattern, b'\x00\x00')
            self.assertTrue(editor.hex_results_visible)
        finally:
            document.close()


if __name__ == '__main__':
    unittest.main()
