"""Reader and 2D projection data for the MW2012 TrafficData resource (0x701)."""
import math
import struct


T_TRAFFIC = 0x701


class TrafficError(ValueError):
    pass


class _Reader:
    def __init__(self, data):
        self.data = memoryview(data)

    def unpack(self, fmt, offset, label):
        size = struct.calcsize(fmt)
        if offset < 0 or offset > len(self.data) or size > len(self.data) - offset:
            raise TrafficError(f'{label} at {offset:#x} is outside the resource')
        return struct.unpack_from(fmt, self.data, offset)

    def pointer(self, offset, label):
        return self.unpack('<i', offset, label)[0]

    def count(self, value, label):
        if value > 1_000_000:
            raise TrafficError(f'{label} count is unreasonably large ({value})')
        return value

    def array(self, offset, count, stride, label):
        count = self.count(count, label)
        if count and not offset:
            raise TrafficError(f'{label} has entries but a null pointer')
        if offset < 0 or offset > len(self.data) or count * stride > len(self.data) - offset:
            raise TrafficError(f'{label} at {offset:#x} runs outside the resource')
        return [offset + i * stride for i in range(count)]


def parse(data):
    """Read lane hulls, section boundaries, and rung coordinates from a resource chunk."""
    reader = _Reader(data)
    if len(reader.data) < 0x48:
        raise TrafficError('resource is too short for a TrafficData header')
    version, _, hull_count, size = reader.unpack('<BBHI', 0, 'TrafficData header')
    if size and size > len(reader.data):
        raise TrafficError(f'TrafficData declares {size:#x} bytes but only {len(reader.data):#x} are present')
    hull_table = reader.pointer(0x0C, 'hull pointer table')
    hull_ptrs = reader.array(hull_table, hull_count, 4, 'hull pointer table')
    hulls = []
    for hull_index, ptr_pos in enumerate(hull_ptrs):
        hull_offset = reader.pointer(ptr_pos, f'hull {hull_index} pointer')
        if not hull_offset:
            hulls.append({'sections': [], 'lines': [], 'points': 0})
            continue
        reader.unpack('<14BHHHH', hull_offset, f'hull {hull_index} header')
        section_count, _, _, _, _, _, rung_count = reader.unpack(
            '<6B H', hull_offset, f'hull {hull_index} counts'
        )
        section_ptr = reader.pointer(hull_offset + 0x10, f'hull {hull_index} sections pointer')
        rung_ptr = reader.pointer(hull_offset + 0x14, f'hull {hull_index} rungs pointer')
        section_offsets = reader.array(section_ptr, section_count, 0x30, 'hull sections')
        rung_offsets = reader.array(rung_ptr, rung_count, 0x20, 'lane rungs')
        rungs = []
        for rung in rung_offsets:
            points = []
            for side in range(2):
                points.append(reader.unpack('<3f', rung + side * 0x10, 'lane rung point'))
            rungs.append(points)

        sections = []
        lines = []
        rung_cursor = 0
        for section_offset in section_offsets:
            rung_start, section_rungs = reader.unpack('<IB', section_offset, 'traffic section')
            section_rungs = reader.count(section_rungs, 'section rungs')
            start = rung_start if rung_start < rung_count else rung_cursor
            end = min(start + section_rungs, rung_count)
            points = [rungs[i][0] for i in range(start, end)]
            sections.append({'rung_offset': start, 'rung_count': section_rungs, 'points': points})
            if len(points) > 1:
                lines.append(points)
            rung_cursor = end
        hulls.append({'sections': sections, 'lines': lines, 'points': sum(len(x) for x in lines)})

    all_points = [point for hull in hulls for line in hull['lines'] for point in line]
    if all_points:
        xs = [p[0] for p in all_points]
        zs = [p[2] for p in all_points]
        bounds = (min(xs), min(zs), max(xs), max(zs))
        if not all(math.isfinite(v) for v in bounds):
            raise TrafficError('TrafficData contains non-finite lane coordinates')
    else:
        bounds = (0.0, 0.0, 1.0, 1.0)
    return {'version': version, 'size': size, 'hulls': hulls, 'bounds': bounds}
