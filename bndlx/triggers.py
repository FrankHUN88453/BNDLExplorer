"""TriggerData (resource type 0x202, HAWAII\\TRIGGERS.BNDL): the trigger shapes of the world.

Version 8, platform byte order. Header:
  0x00 u32 version (8)
  0x04 u32 boxes offset,    0x08 u32 box count
  0x0C u32 spheres offset,  0x10 u32 sphere count
  0x14 u32 locators offset, 0x18 u32 locator count
  0x1C u32 grid offset,     0x20 u32 cells in x, 0x24 u32 cells in z
  0x28 f32 grid origin x,   0x2C f32 grid origin z, 0x30 f32 cell size x, 0x34 f32 cell size z (250 m)
Every trigger starts with f32 position x, y, z, u32 owner id (the world placement / gameplay object it belongs
to), u8 trigger type, u8 shape (0 box, 1 sphere, 2 locator), u8 flags, u8 0; then
  box (48 bytes):     f32 rotation quaternion x, y, z, w, f32 half size x, y, z
  sphere (24 bytes):  f32 radius
  locator (32 bytes): f32 direction x, y, z
Trigger indices run over boxes, then spheres, then locators. The grid (z rows of x cells) holds per cell
u32 list offset, u16 count, u16 0; the lists are u16 trigger indices, in index order, of every trigger whose
x / z extent touches the cell (boxes: the rotated box, spheres: centre +- radius, locators: the point); they
are stored column by column (x outer, z inner), each list 4-aligned on PC. The PS3 prototype leaves the locators
out of the grid and aligns lists to 16. The game finds triggers through the grid, so it is rebuilt on save."""
import math
import struct
from dataclasses import dataclass, field

T_TRIGGERS = 0x202
VERSION = 8
BOX, SPHERE, LOCATOR = 0, 1, 2
SHAPES = {BOX: 'Box', SPHERE: 'Sphere', LOCATOR: 'Locator'}
SIZES = {BOX: 48, SPHERE: 24, LOCATOR: 32}

# Trigger types (u8) of the retail PC game, named by the gameplay data that refers to their owners (GAMELOGIC,
# GLOBALCONFIG CollectablesConfig, the track units' prop / compound instances). Some types are used by more than
# one kind of object, and the game also looks some types up by number (collectables); '?' marks a guess.
TYPE_NAMES = {
    0: 'Checkpoint / event trigger',
    1: 'Car select (box)',
    3: 'Car select (box)',
    4: 'Placement / spawn point',
    5: 'Roadblock placement',
    7: 'Dynamic prop',
    12: 'Jack spot: parking',
    14: 'Car select (point)',
    15: 'Challenge trigger',
    17: 'Challenge display trigger',
    20: 'Event AI hint',
    22: 'Event route marker',
    23: 'Challenge trigger',
    25: 'Gameplay trigger',
    31: 'Event asset',
    35: 'Drift marker',
    36: 'Drift safety zone',
    48: 'Collectable (?)',
    50: 'Compound object (billboard / gate ?)',
    52: 'Collectable (?)',
    55: 'Challenge trigger',
    58: 'Safehouse',
    60: 'Jack spot (car swap)',
    61: 'Vehicle placement (jack spot car, spawn)',
    63: 'Safehouse discovery',
}
TYPE_NOTES = {
    0: 'OfflineEvent.Checkpoints, OnlineRoute.Checkpoints, Challenge.Location.Triggers, GameplayTrigger.Input',
    1: 'CarSelectData.CopPlacements / RacerPlacements',
    3: 'CarSelectData.CopPlacements / RacerPlacements',
    4: 'EventArena spawn points, OfflineEvent.SpawnPosition, AIPlayerInformation.Placement, '
       'Safehouse.ExitSpawnLocation, online challenges',
    5: 'RoadblockInstance.Placement',
    7: 'prop instances (PropInstanceList) of the track units',
    12: 'CarSwapSpot.ParkingPlacement',
    14: 'CarSelectData placements',
    15: 'Challenge.Location / OnlineChallenge',
    17: 'Challenge.Location.DisplayTrigger',
    20: 'OfflineEvent.AIHints',
    22: 'OfflineEvent.AlternateRoutes.RouteMarkers',
    25: 'GameplayTrigger.Input.Trigger',
    31: 'OfflineEvent.AdditionalAssets',
    35: 'Gameplay.DriftMarker (start / end)',
    36: 'Gameplay.DriftMarker.SafetyTrigger',
    48: 'CollectablesConfig lists',
    50: 'compound instances (CompoundInstanceList) of the track units, CollectablesConfig, OfflineEvent assets',
    52: 'CollectablesConfig lists',
    58: 'Safehouse.Placement',
    60: 'CarSwapSpot.Placement (the zone that offers the car)',
    61: 'CarSwapSpot.VehiclePlacement, EventArena team spawn points',
    63: 'Safehouse.DiscoveryTrigger',
}


class TriggerError(ValueError):
    pass


@dataclass
class Trigger:
    shape: int
    pos: list
    owner: int = 0
    kind: int = 0
    flags: int = 0
    pad: int = 0
    rot: list = field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])     # box: quaternion x, y, z, w
    half: list = field(default_factory=lambda: [2.0, 2.0, 2.0])         # box: half size
    radius: float = 5.0                                                  # sphere
    dir: list = field(default_factory=lambda: [0.0, 0.0, 1.0])          # locator: direction

    def to_f32(self):
        """Every number as the file stores it (float32), so the grid is built from the values the game reads."""
        self.pos, self.rot, self.half, self.dir = f32(self.pos), f32(self.rot), f32(self.half), f32(self.dir)
        self.radius = f32([self.radius])[0]

    def copy(self):
        return Trigger(self.shape, list(self.pos), self.owner, self.kind, self.flags, self.pad, list(self.rot),
                       list(self.half), self.radius, list(self.dir))

    # x / z footprint
    def corners(self):
        """Box: the 4 corners of its outline seen from above [(x, z)] (at the box's middle height)."""
        pts = [rotate(self.rot, (sx * self.half[0], 0.0, sz * self.half[2]))
               for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        return [(self.pos[0] + p[0], self.pos[2] + p[2]) for p in pts]

    def extent(self):
        """(min x, min z, max x, max z) as the grid uses it."""
        if self.shape == BOX:
            xs, zs = [], []
            for sx in (-1, 1):
                for sy in (-1, 1):
                    for sz in (-1, 1):
                        v = rotate(self.rot, (sx * self.half[0], sy * self.half[1], sz * self.half[2]))
                        xs.append(self.pos[0] + v[0])
                        zs.append(self.pos[2] + v[2])
            return min(xs), min(zs), max(xs), max(zs)
        if self.shape == SPHERE:
            r = self.radius
            return self.pos[0] - r, self.pos[2] - r, self.pos[0] + r, self.pos[2] + r
        return self.pos[0], self.pos[2], self.pos[0], self.pos[2]

    def yaw(self):
        """Heading in degrees around the up axis (box: of its rotation, locator: of its direction)."""
        if self.shape == LOCATOR:
            return math.degrees(math.atan2(self.dir[0], self.dir[2]))
        f = rotate(self.rot, (0.0, 0.0, 1.0))
        return math.degrees(math.atan2(f[0], f[2]))

    def set_yaw(self, deg):
        a = math.radians(deg)
        if self.shape == LOCATOR:
            y = self.dir[1]
            k = math.sqrt(max(0.0, 1.0 - y * y))
            self.dir = [math.sin(a) * k, y, math.cos(a) * k]
        else:
            self.rot = [0.0, math.sin(a / 2), 0.0, math.cos(a / 2)]

    def contains(self, x, z, margin=0.0):
        """The top-down outline holds the point (x, z)."""
        if self.shape == BOX:
            inv = (-self.rot[0], -self.rot[1], -self.rot[2], self.rot[3])
            v = rotate(inv, (x - self.pos[0], 0.0, z - self.pos[2]))
            return abs(v[0]) <= self.half[0] + margin and abs(v[2]) <= self.half[2] + margin
        if self.shape == SPHERE:
            return math.hypot(x - self.pos[0], z - self.pos[2]) <= self.radius + margin
        return math.hypot(x - self.pos[0], z - self.pos[2]) <= margin


def f32(values):
    return list(struct.unpack(f'<{len(values)}f', struct.pack(f'<{len(values)}f', *values)))


def rotate(q, v):
    """v rotated by the unit quaternion q = (x, y, z, w)."""
    x, y, z, w = q
    vx, vy, vz = v
    cx, cy, cz = y * vz - z * vy, z * vx - x * vz, x * vy - y * vx
    dx, dy, dz = y * cz - z * cy, z * cx - x * cz, x * cy - y * cx
    return vx + 2 * (w * cx + dx), vy + 2 * (w * cy + dy), vz + 2 * (w * cz + dz)


class TriggerData:
    def __init__(self, e='<'):
        self.e = e
        self.version = VERSION
        self.triggers = []
        self.grid = (1, 1, 0.0, 0.0, 250.0, 250.0)         # cells x, cells z, origin x, origin z, cell x, cell z
        self.first = 0x38 if e == '<' else 0x40
        self.raw = None                                     # the bytes as read: written back while unchanged
        self.snapshot = None

    # -- reading ----------------------------------------------------------------------------------------------
    @classmethod
    def read(cls, data, e):
        d = bytes(data)
        if len(d) < 0x38:
            raise TriggerError('too short for a TriggerData header')
        t = cls(e)
        hdr = struct.unpack_from(e + '7I', d, 0)
        t.version = hdr[0]
        if t.version != VERSION:
            raise TriggerError(f'TriggerData version {t.version}, this reader knows version {VERSION}')
        offs = {BOX: (hdr[1], hdr[2]), SPHERE: (hdr[3], hdr[4]), LOCATOR: (hdr[5], hdr[6])}
        g, nx, nz = struct.unpack_from(e + '3I', d, 0x1C)
        ox, oz, cw, ch = struct.unpack_from(e + '4f', d, 0x28)
        t.grid = (nx, nz, ox, oz, cw, ch)
        t.first = hdr[1]
        for shape in (BOX, SPHERE, LOCATOR):
            off, n = offs[shape]
            if off + n * SIZES[shape] > len(d):
                raise TriggerError(f'{SHAPES[shape].lower()} array runs past the end')
            for i in range(n):
                o = off + SIZES[shape] * i
                x, y, z, owner = struct.unpack_from(e + '3fI', d, o)
                kind, sh, flags, pad = d[o + 16], d[o + 17], d[o + 18], d[o + 19]
                tr = Trigger(shape, [x, y, z], owner, kind, flags, pad)
                if sh != shape:
                    raise TriggerError(f'{SHAPES[shape]} {i} says it is shape {sh}')
                if shape == BOX:
                    tr.rot = list(struct.unpack_from(e + '4f', d, o + 20))
                    tr.half = list(struct.unpack_from(e + '3f', d, o + 36))
                elif shape == SPHERE:
                    tr.radius = struct.unpack_from(e + 'f', d, o + 20)[0]
                else:
                    tr.dir = list(struct.unpack_from(e + '3f', d, o + 20))
                t.triggers.append(tr)
        if g + 8 * nx * nz > len(d):
            raise TriggerError('the grid runs past the end')
        t.raw = d
        t.snapshot = t.state()
        return t

    def cells(self, data=None):
        """The grid lists as stored ([[trigger index]] by cell, z rows of x cells)."""
        d = bytes(data if data is not None else self.raw or self.to_bytes())
        e = self.e
        g, nx, nz = struct.unpack_from(e + '3I', d, 0x1C)
        out = []
        for i in range(nx * nz):
            p, n, _ = struct.unpack_from(e + 'I2H', d, g + 8 * i)
            out.append(list(struct.unpack_from(e + f'{n}H', d, p)))
        return out

    # -- editing ----------------------------------------------------------------------------------------------
    def state(self):
        return [(t.shape, tuple(t.pos), t.owner, t.kind, t.flags, t.pad, tuple(t.rot), tuple(t.half), t.radius,
                 tuple(t.dir)) for t in self.triggers]

    def changed(self):
        return self.raw is None or self.state() != self.snapshot

    def ordered(self):
        """The triggers in file order (boxes, spheres, locators; each group in list order)."""
        return [t for s in (BOX, SPHERE, LOCATOR) for t in self.triggers if t.shape == s]

    def counts(self):
        return {s: sum(1 for t in self.triggers if t.shape == s) for s in (BOX, SPHERE, LOCATOR)}

    def cell_of(self, x, z):
        nx, nz, ox, oz, cw, ch = self.grid
        a, b = int(math.floor((x - ox) / cw)), int(math.floor((z - oz) / ch))
        return (a, b) if 0 <= a < nx and 0 <= b < nz else None

    def build_cells(self, trigs):
        nx, nz, ox, oz, cw, ch = self.grid
        out = [[] for _ in range(nx * nz)]
        for i, t in enumerate(trigs):
            if t.shape == LOCATOR and self.e == '>':
                continue                                   # the prototype's grid has no locators
            x0, z0, x1, z1 = t.extent()
            a0, a1 = int(math.floor((x0 - ox) / cw)), int(math.floor((x1 - ox) / cw))
            b0, b1 = int(math.floor((z0 - oz) / ch)), int(math.floor((z1 - oz) / ch))
            for b in range(max(b0, 0), min(b1, nz - 1) + 1):
                for a in range(max(a0, 0), min(a1, nx - 1) + 1):
                    out[b * nx + a].append(i)
        return out

    # -- writing ----------------------------------------------------------------------------------------------
    def to_bytes(self):
        if not self.changed():
            return self.raw
        e = self.e
        trigs = self.ordered()
        for t in trigs:
            t.to_f32()
        if len(trigs) > 0xFFFF:
            raise TriggerError(f'{len(trigs)} triggers: the grid holds at most 65535')
        n = self.counts()
        out = bytearray(self.first)
        offs = {}
        for shape in (BOX, SPHERE, LOCATOR):
            offs[shape] = len(out)
            for t in trigs:
                if t.shape != shape:
                    continue
                rec = bytearray(struct.pack(e + '3fI', *t.pos, t.owner & 0xFFFFFFFF))
                rec += bytes((t.kind & 0xFF, shape, t.flags & 0xFF, t.pad & 0xFF))
                if shape == BOX:
                    rec += struct.pack(e + '4f3f', *t.rot, *t.half)
                elif shape == SPHERE:
                    rec += struct.pack(e + 'f', t.radius)
                else:
                    rec += struct.pack(e + '3f', *t.dir)
                out += rec
        nx, nz, ox, oz, cw, ch = self.grid
        grid = len(out)
        out += bytes(8 * nx * nz)
        cells = self.build_cells(trigs)
        align = 4 if e == '<' else 16
        for a in range(nx):
            for b in range(nz):
                i = b * nx + a
                lst = cells[i]
                out += bytes(-len(out) % align)
                struct.pack_into(e + 'I2H', out, grid + 8 * i, len(out), len(lst), 0)
                out += struct.pack(e + f'{len(lst)}H', *lst)
        out += bytes(-len(out) % 16)
        struct.pack_into(e + '7I', out, 0, self.version, offs[BOX], n[BOX], offs[SPHERE], n[SPHERE],
                         offs[LOCATOR], n[LOCATOR])
        struct.pack_into(e + '3I4f', out, 0x1C, grid, nx, nz, ox, oz, cw, ch)
        return bytes(out)

    def rebuilt(self):
        """The file written from scratch (also when unchanged): to check the writer against the original."""
        raw, self.raw = self.raw, None
        try:
            return self.to_bytes()
        finally:
            self.raw = raw


# -- CSV ---------------------------------------------------------------------------------------------------------
CSV_FIELDS = ['shape', 'type', 'flags', 'owner', 'x', 'y', 'z', 'qx', 'qy', 'qz', 'qw', 'half_x', 'half_y',
              'half_z', 'radius', 'dir_x', 'dir_y', 'dir_z']


def to_csv(td, names=None):
    names = names or {}
    lines = [';'.join(CSV_FIELDS + ['type_name'])]
    for t in td.ordered():
        row = [SHAPES[t.shape], t.kind, t.flags, f'{t.owner}', *t.pos, *t.rot, *t.half, t.radius, *t.dir]
        lines.append(';'.join(f'{v:.9g}' if isinstance(v, float) else str(v) for v in row)
                     + ';' + names.get(t.kind, ''))
    return '\r\n'.join(lines) + '\r\n'


def from_csv(text):
    """[Trigger] from to_csv's text (';' or ',' separated; decimal commas allowed with ';')."""
    rows = [r for r in text.replace('\r', '').split('\n') if r.strip()]
    if not rows:
        raise TriggerError('empty CSV')
    sep = ';' if rows[0].count(';') >= rows[0].count(',') else ','
    head = [h.strip().lower() for h in rows[0].split(sep)]
    missing = [f for f in CSV_FIELDS if f not in head]
    if missing:
        raise TriggerError('CSV without the columns ' + ', '.join(missing))
    col = {h: i for i, h in enumerate(head)}
    shapes = {v.lower(): k for k, v in SHAPES.items()}
    out = []
    for ln, r in enumerate(rows[1:], 2):
        cells = [c.strip() for c in r.split(sep)]

        def num(name):
            s = cells[col[name]] if col[name] < len(cells) else ''
            if sep == ';':
                s = s.replace(',', '.')
            try:
                return float(s)
            except ValueError:
                raise TriggerError(f'line {ln}: {name} {s!r} is not a number') from None
        shape = shapes.get(cells[col['shape']].lower())
        if shape is None:
            raise TriggerError(f'line {ln}: shape {cells[col["shape"]]!r} (Box, Sphere or Locator)')
        t = Trigger(shape, [num('x'), num('y'), num('z')], int(num('owner')), int(num('type')), int(num('flags')))
        t.rot = [num('qx'), num('qy'), num('qz'), num('qw')]
        t.half = [num('half_x'), num('half_y'), num('half_z')]
        t.radius = num('radius')
        t.dir = [num('dir_x'), num('dir_y'), num('dir_z')]
        out.append(t)
    return out
