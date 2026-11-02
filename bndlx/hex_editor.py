"""Lazy, file-backed hex editing and analysis."""
import hashlib
import mmap
import os
import tempfile
import zlib
import binascii
from bisect import bisect_right

import numpy as np


class HexDocument:
    """A piece-table editor that maps the source file and stores only inserted bytes."""

    def __init__(self, path, max_history=100):
        self.path = os.path.abspath(path)
        self._file = open(self.path, 'rb')
        self._length = os.fstat(self._file.fileno()).st_size
        self._mapping = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ) if self._length else None
        self._sources = [self._mapping]
        self._segments = ((0, 0, self._length),) if self._length else ()
        self._segment_offsets = (0,)
        self._undo = []
        self._redo = []
        self._max_history = max_history
        self.revision = 0
        self.cursor = 0
        self.selection = (0, 0)

    @property
    def size(self):
        return self._length

    @property
    def modified(self):
        return bool(self._undo)

    def close(self):
        if self._mapping is not None:
            self._mapping.close()
        self._file.close()
        self._mapping = None
        self._file = None

    @staticmethod
    def _join(parts):
        joined = []
        for source, start, length in parts:
            if not length:
                continue
            if joined and joined[-1][0] == source and joined[-1][1] + joined[-1][2] == start:
                previous = joined[-1]
                joined[-1] = (source, previous[1], previous[2] + length)
            else:
                joined.append((source, start, length))
        return tuple(joined)

    def _reindex(self):
        offsets = []
        position = 0
        for _, _, length in self._segments:
            offsets.append(position)
            position += length
        self._segment_offsets = tuple(offsets)

    def _compact_sources(self):
        states = (self._segments, *self._undo, *self._redo)
        used = {0}
        for state in states:
            used.update(source for source, _, _ in state)
        indices = {source: index for index, source in enumerate(sorted(used))}
        self._sources = [self._sources[source] for source in sorted(used)]

        def remap(state):
            return tuple((indices[source], start, length) for source, start, length in state)

        self._segments = remap(self._segments)
        self._undo = [remap(state) for state in self._undo]
        self._redo = [remap(state) for state in self._redo]

    def _segments_in_range(self, start, end):
        if start == end:
            return ()
        result = []
        index = max(0, bisect_right(self._segment_offsets, start) - 1)
        position = self._segment_offsets[index]
        for segment_index in range(index, len(self._segments)):
            source, source_start, length = self._segments[segment_index]
            next_position = position + length
            overlap_start = max(start, position)
            overlap_end = min(end, next_position)
            if overlap_start < overlap_end:
                result.append((source, source_start + overlap_start - position, overlap_end - overlap_start))
            position = next_position
            if position >= end:
                break
        return tuple(result)

    def read(self, offset, length):
        if offset < 0 or length < 0 or offset > self._length:
            raise ValueError('read range is outside the file')
        end = min(self._length, offset + length)
        chunks = []
        for source, start, count in self._segments_in_range(offset, end):
            data = self._sources[source]
            chunks.append(data[start:start + count])
        return b''.join(chunks)

    def snapshot(self):
        snapshot = object.__new__(HexDocument)
        snapshot.path = self.path
        snapshot._file = None
        snapshot._length = self._length
        snapshot._mapping = self._mapping
        snapshot._sources = list(self._sources)
        snapshot._segments = self._segments
        snapshot._segment_offsets = self._segment_offsets
        snapshot._undo = []
        snapshot._redo = []
        snapshot._max_history = self._max_history
        snapshot.revision = self.revision
        snapshot.cursor = self.cursor
        snapshot.selection = self.selection
        return snapshot

    def replace(self, offset, delete_length, inserted):
        inserted = bytes(inserted)
        if offset < 0 or delete_length < 0 or offset + delete_length > self._length:
            raise ValueError('edit range is outside the file')
        if not delete_length and not inserted:
            return
        self._undo.append(self._segments)
        del self._undo[:-self._max_history]
        self._redo.clear()
        parts = list(self._segments_in_range(0, offset))
        if inserted:
            source = len(self._sources)
            self._sources.append(inserted)
            parts.append((source, 0, len(inserted)))
        parts.extend(self._segments_in_range(offset + delete_length, self._length))
        self._segments = self._join(parts)
        self._length += len(inserted) - delete_length
        self._reindex()
        self._compact_sources()
        self.revision += 1
        self.cursor = min(offset + len(inserted), self._length)
        self.selection = (self.cursor, self.cursor)

    def undo(self):
        if not self._undo:
            return False
        self._redo.append(self._segments)
        self._segments = self._undo.pop()
        self._length = sum(length for _, _, length in self._segments)
        self._reindex()
        self._compact_sources()
        self.revision += 1
        self.cursor = min(self.cursor, self._length)
        self.selection = (self.cursor, self.cursor)
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append(self._segments)
        self._segments = self._redo.pop()
        self._length = sum(length for _, _, length in self._segments)
        self._reindex()
        self._compact_sources()
        self.revision += 1
        self.cursor = min(self.cursor, self._length)
        self.selection = (self.cursor, self.cursor)
        return True

    def find(self, pattern, start=None, reverse=False):
        pattern = bytes(pattern)
        if not pattern:
            raise ValueError('search value cannot be empty')
        if reverse:
            position = min(self._length - len(pattern),
                           self._length - 1 if start is None else start)
            while position >= 0:
                chunk_start = max(0, position - 1024 * 1024)
                chunk = self.read(chunk_start, position - chunk_start + len(pattern))
                found = chunk.rfind(pattern, 0, position - chunk_start + len(pattern))
                if found >= 0:
                    return chunk_start + found
                position = chunk_start - 1
            return -1
        position = max(0, start or 0)
        overlap = len(pattern) - 1
        while position < self._length:
            count = min(1024 * 1024 + overlap, self._length - position)
            chunk = self.read(position, count)
            found = chunk.find(pattern)
            if found >= 0:
                return position + found
            if count <= overlap:
                break
            position += 1024 * 1024
        return -1

    def iter_chunks(self, chunk_size=1024 * 1024):
        for offset in range(0, self._length, chunk_size):
            yield self.read(offset, min(chunk_size, self._length - offset))

    def save(self, path=None, progress=None):
        target = os.path.abspath(path or self.path)
        directory = os.path.dirname(target) or os.curdir
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(prefix='.bndlx-', suffix='.tmp', dir=directory,
                                             delete=False) as output:
                temp_path = output.name
                completed = 0
                for chunk in self.iter_chunks():
                    output.write(chunk)
                    completed += len(chunk)
                    if progress:
                        progress(completed, self._length)
                output.flush()
                os.fsync(output.fileno())
                if progress and not self._length:
                    progress(1, 1)
            if os.path.exists(target):
                os.chmod(temp_path, os.stat(target).st_mode)
            self.close()
            os.replace(temp_path, target)
            temp_path = None
            self.path = target
            self._reopen()
            return target
        except Exception:
            if self._file is None:
                self._reopen()
            raise
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    def _reopen(self):
        self._file = open(self.path, 'rb')
        self._length = os.fstat(self._file.fileno()).st_size
        self._mapping = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ) if self._length else None
        self._sources = [self._mapping]
        self._segments = ((0, 0, self._length),) if self._length else ()
        self._segment_offsets = (0,)
        self._undo.clear()
        self._redo.clear()
        self.revision = 0
        self.cursor = min(self.cursor, self._length)
        self.selection = (self.cursor, self.cursor)

    def analyze(self, progress=None, custom_crc=(0x1021, 0xffff, 0)):
        hashes = {
            'MD5': hashlib.md5(),
            'SHA-1': hashlib.sha1(),
            'SHA-512': hashlib.sha512(),
        }
        crc = 0
        checksum = 0
        polynomial, initial, xor_out = custom_crc
        custom_value = initial
        custom_table = None
        if polynomial != 0x1021:
            custom_table = []
            for value in range(256):
                remainder = value << 8
                for _ in range(8):
                    remainder = (((remainder << 1) ^ polynomial) if remainder & 0x8000
                                 else remainder << 1) & 0xffff
                custom_table.append(remainder)
        histogram = np.zeros(256, dtype=np.int64)
        completed = 0
        for chunk in self.iter_chunks():
            values = np.frombuffer(chunk, dtype=np.uint8)
            histogram += np.bincount(values, minlength=256)
            checksum += int(values.sum())
            crc = zlib.crc32(chunk, crc)
            if custom_table is None:
                custom_value = binascii.crc_hqx(chunk, custom_value)
            else:
                for value in chunk:
                    index = ((custom_value >> 8) ^ value) & 0xff
                    custom_value = ((custom_value << 8) ^ custom_table[index]) & 0xffff
            for digest in hashes.values():
                digest.update(chunk)
            completed += len(chunk)
            if progress:
                progress(completed, self._length)
        if progress and not self._length:
            progress(1, 1)
        result = {name: digest.hexdigest() for name, digest in hashes.items()}
        result['Checksum-8'] = f'{checksum & 0xff:02X}'
        result['Checksum-16'] = f'{checksum & 0xffff:04X}'
        result['CRC32'] = f'{crc & 0xffffffff:08X}'
        result['Custom CRC-16'] = f'{(custom_value ^ xor_out) & 0xffff:04X}'
        result['Histogram'] = histogram.tolist()
        return result

    def compare(self, other, progress=None):
        first_difference = None
        differing_bytes = 0
        common = min(self.size, other.size)
        chunk_size = 1024 * 1024
        for offset in range(0, common, chunk_size):
            left = self.read(offset, min(chunk_size, common - offset))
            right = other.read(offset, min(chunk_size, common - offset))
            if left != right:
                for index, (a, b) in enumerate(zip(left, right)):
                    if a != b:
                        if first_difference is None:
                            first_difference = offset + index
                        differing_bytes += 1
            if progress:
                progress(min(offset + len(left), common), common)
        if progress and not common:
            progress(1, 1)
        return {
            'first_difference': first_difference,
            'differing_bytes': differing_bytes,
            'left_size': self.size,
            'right_size': other.size,
        }
