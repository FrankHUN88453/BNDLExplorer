"""ZoneList (resource type 0x90, HAWAII\\PVS.BNDL): the map of track units.

Header (platform byte order): u32 points offset, u32 zones offset (0x20), u32 / u32 two more tables, u32 zone
count, u32 total neighbour count, u32 3, u32 0. Zones are 0x50 bytes: u32 points offset, f32x4 min at 0x10 and
max at 0x20 (x, -FLT_MAX / FLT_MAX, z), u32 neighbours offset at 0x30, u32 track unit number (TRK_UNIT<n>) at
0x34, u32 district id (DISTRICT_<id>) at 0x38, then u16, u8 point count, u8 neighbour count at 0x40.
Points are f32x4 (x, z, 0, 0); a neighbour is u32 zone offset + u32 flags (bit 1: shares a border).
The PS3 prototype (SEACREST\\PVS.BNDL) uses 0x18-byte zones: u32 points offset, u32 neighbours offset, u32 track
unit number, u32 district id, u16 ?, u16 point count, u16 neighbour count, u16 0 (no bounding box)."""
import os
import re
import struct
from dataclasses import dataclass, field

T_ZONELIST = 0x90
ZONE_SIZE = 0x50


@dataclass
class Zone:
    index: int
    unit: int                  # TRK_UNIT<unit>.BNDL
    district: int              # DISTRICT_<district>.BNDL
    points: list               # [(x, z)]
    box: tuple                 # (min x, min z, max x, max z)
    neighbours: list = field(default_factory=list)     # [(zone index, flags)]


def read(res, e):
    d = bytes(res.data(0))
    if len(d) < 0x20:
        return []
    pts_off, zo, _, _, n = struct.unpack_from(e + '5I', d, 0)
    old = struct.unpack_from(e + 'I', d, zo + 4)[0] != 0       # prototype layout: neighbours offset at +4
    size = 0x18 if old else ZONE_SIZE
    zones = []
    for i in range(n):
        o = zo + size * i
        if old:
            pp, nb, unit, district = struct.unpack_from(e + '4I', d, o)
            npts, nnb = struct.unpack_from(e + '2H', d, o + 0x12)
        else:
            pp = struct.unpack_from(e + 'I', d, o)[0]
            nb, unit, district = struct.unpack_from(e + '3I', d, o + 0x30)
            npts, nnb = d[o + 0x42], d[o + 0x43]
        pts = [struct.unpack_from(e + '2f', d, pp + 16 * k) for k in range(npts)]
        nbs = []
        for k in range(nnb):
            zoff, flags = struct.unpack_from(e + '2I', d, nb + 8 * k)
            nbs.append(((zoff - zo) // size, flags))
        xs = [p[0] for p in pts] or [0.0]
        ys = [p[1] for p in pts] or [0.0]
        zones.append(Zone(i, unit, district, pts, (min(xs), min(ys), max(xs), max(ys)), nbs))
    return zones


def unit_file(unit):
    return f'TRK_UNIT{unit}.BNDL'


_CACHE = {}


def zones_in(folder):
    """Zones of <folder>\\PVS.BNDL (cached; [] when there is none)."""
    p = os.path.join(folder, 'PVS.BNDL')
    if p not in _CACHE:
        out = []
        if os.path.isfile(p):
            try:
                from .bundle import Bundle
                b = Bundle.open(p)
                r = next((x for x in b.resources if x.type == T_ZONELIST), None)
                out = read(r, b.e) if r is not None else []
            except Exception:
                out = []
        _CACHE[p] = out
    return _CACHE[p]


def unit_number(path):
    m = re.match(r'TRK_UNIT(\d+)\.BNDL$', os.path.basename(path or ''), re.I)
    return int(m.group(1)) if m else None


def neighbour_paths(path, border_only=True):
    """TRK_UNIT bundle paths next to the unit bundle `path` (sharing a border, or also only visible from it)."""
    n = unit_number(path)
    if n is None:
        return []
    folder = os.path.dirname(os.path.abspath(path))
    zs = zones_in(folder)
    z = next((z for z in zs if z.unit == n), None)
    if z is None:
        return []
    out = []
    for j, flags in z.neighbours:
        if 0 <= j < len(zs) and (flags & 2 or not border_only):
            p = os.path.join(folder, unit_file(zs[j].unit))
            if os.path.isfile(p):
                out.append(p)
    return out
