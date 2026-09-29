"""VehicleList (resource type 0x105, VEHICLES\\VEHICLELIST.BNDL): the game's list of cars and manufacturers.

Header (platform byte order): u32 version (PC retail 1017, PS3 prototype 1016), u32 vehicle count,
u32 manufacturer count, u32 vehicles offset (0x20), u32 manufacturers offset; then 0x70-byte vehicle records and
20-byte (1017) / 16-byte (1016) manufacturer records. Ids are 32-bit numbers: the low half of a resource id
(`0x01000000_xxxxxxxx`) for images, sounds and Genesys objects, LocalisedText ids for names (UI\\LANGUAGE)."""
import csv
import io
import struct

T_VEHICLELIST = 0x105

# (offset, struct code, key, label, kind); kinds: int, float, hex, string (LocalisedText id), maker (manufacturer
# id), ref (resource id), vehicle (vehicle id = VEH_<id> bundles), id (other number)
FIELDS = {
    1017: [
        (0x00, 'I', 'id', 'Vehicle id', 'vehicle'),
        (0x04, 'I', 'model', 'Model / livery (VEH_<n>)', 'vehicle'),
        (0x08, 'I', 'name', 'Name', 'string'),
        (0x0C, 'I', 'description', 'Description', 'string'),
        (0x10, 'I', 'short_name', 'Short name', 'string'),
        (0x14, 'I', 'manufacturer', 'Manufacturer', 'maker'),
        (0x18, 'I', 'category', 'Category (VehicleCategoryInfo)', 'ref'),
        (0x1C, 'I', 'image', 'Car select image', 'ref'),
        (0x20, 'I', 'image_streamed', 'Large image (UI\\IMAGES\\STREAMED)', 'ref'),
        (0x24, 'I', 'image2', 'Car select image 2', 'ref'),
        (0x28, 'I', 'sound', 'Sound (Wave)', 'ref'),
        (0x2C, 'I', 'pack', 'Content pack (17127 = base game)', 'id'),
        (0x30, 'I', 'upgrades', 'Performance upgrades', 'ref'),
        (0x34, 'I', 'events', 'Event list (GAMEPLAY)', 'id'),
        (0x38, 'f', 'top_speed', 'Top speed (mph)', 'float'),
        (0x3C, 'f', 'top_speed_2', 'Top speed 2 (mph)', 'float'),
        (0x40, 'f', 'zero_to_60', '0-60 mph (s)', 'float'),
        (0x44, 'I', 'power', 'Power (hp)', 'int'),
        (0x48, 'f', 'rating_acceleration', 'Rating: acceleration', 'float'),
        (0x4C, 'f', 'rating_top_speed', 'Rating: top speed', 'float'),
        (0x50, 'f', 'rating_handling', 'Rating: handling', 'float'),
        (0x54, 'f', 'rating_strength', 'Rating: strength', 'float'),
        (0x58, 'f', 'rating_5', 'Rating 5', 'float'),
        (0x5C, 'f', 'rating_6', 'Rating 6', 'float'),
        (0x60, 'I', 'unknown_60', 'Unknown 0x60', 'int'),
        (0x64, 'I', 'flags', 'Flags (1 police, 8 traffic, 18 player cars)', 'hex'),
        (0x68, 'H', 'unknown_68', 'Unknown 0x68', 'int'),
        (0x6A, 'H', 'redline', 'Redline (rpm)', 'int'),
        (0x6C, 'H', 'year', 'Year', 'int'),
        (0x6E, 'H', 'unknown_6e', 'Unknown 0x6E', 'int'),
    ],
    1016: [
        (0x00, 'I', 'id', 'Vehicle id', 'vehicle'),
        (0x04, 'I', 'name', 'Name', 'string'),
        (0x08, 'I', 'description', 'Description', 'string'),
        (0x0C, 'I', 'slogan', 'Slogan', 'string'),
        (0x10, 'I', 'short_name', 'Short name', 'string'),
        (0x14, 'I', 'manufacturer', 'Manufacturer', 'maker'),
        (0x18, 'I', 'drive', 'Drivetrain', 'string'),
        (0x1C, 'I', 'image', 'Car select image', 'ref'),
        (0x20, 'I', 'image_2', 'Image 2', 'ref'),
        (0x24, 'I', 'image2', 'Car select image 2', 'ref'),
        (0x28, 'I', 'sound', 'Sound (Wave)', 'ref'),
        (0x2C, 'I', 'pack', 'Content pack (17127 = base game)', 'id'),
        (0x30, 'f', 'top_speed', 'Top speed (mph)', 'float'),
        (0x34, 'f', 'top_speed_2', 'Top speed 2 (mph)', 'float'),
        (0x38, 'f', 'zero_to_60', '0-60 mph (s)', 'float'),
        (0x3C, 'I', 'price', 'Price ($)', 'int'),
        (0x40, 'I', 'power', 'Power (hp)', 'int'),
        (0x44, 'I', 'redline', 'Redline (rpm)', 'int'),
        (0x48, 'I', 'year', 'Year', 'int'),
    ] + [(0x4C + 4 * k, 'f', f'rating_{k + 1}', f'Rating {k + 1}', 'float') for k in range(7)] + [
        (0x68, 'I', 'flags', 'Flags', 'hex'),
        (0x6C, 'H', 'unknown_6c', 'Unknown 0x6C', 'int'),
        (0x6E, 'H', 'unknown_6e', 'Unknown 0x6E', 'int'),
    ],
}
MAKER_FIELDS = {
    1017: [(0x00, 'I', 'id', 'Manufacturer id', 'id'), (0x04, 'I', 'name', 'Name', 'string'),
           (0x08, 'I', 'region', 'Region', 'id'), (0x0C, 'I', 'logo', 'Logo', 'ref'),
           (0x10, 'I', 'logo_2', 'Logo 2', 'ref')],
    1016: [(0x00, 'I', 'id', 'Manufacturer id', 'id'), (0x04, 'I', 'name', 'Name', 'string'),
           (0x08, 'I', 'region', 'Region', 'id'), (0x0C, 'I', 'logo', 'Logo', 'ref')],
}
ROW = 0x70
HEADER = 0x20


class VehicleListError(Exception):
    pass


def f32_text(v):
    """Shortest text that reads back as the same float32 (4.2, not 4.19999981)."""
    bits = struct.pack('<f', v)
    for k in range(10):
        t = f'{v:.{k}f}'
        if struct.pack('<f', float(t)) == bits:
            return t
    return repr(v)


def _size(fields):
    return max(o + struct.calcsize('<' + c) for o, c, *_ in fields)


class VehicleList:
    def __init__(self, version, rows, makers, e, header_extra=b'', tail=b''):
        if version not in FIELDS:
            raise VehicleListError(f'unknown VehicleList version {version}')
        self.version = version
        self.rows = [bytearray(r) for r in rows]
        self.makers = [bytearray(m) for m in makers]
        self.e = e
        self.header_extra = header_extra
        self.tail = tail

    @property
    def fields(self):
        return FIELDS[self.version]

    @property
    def maker_fields(self):
        return MAKER_FIELDS[self.version]

    @property
    def maker_size(self):
        return _size(MAKER_FIELDS[self.version])

    @classmethod
    def read(cls, data, e):
        data = bytes(data)
        if len(data) < HEADER:
            raise VehicleListError('too short')
        version, n, nm, off, moff = struct.unpack_from(e + '5I', data, 0)
        if version not in FIELDS:
            raise VehicleListError(f'unknown VehicleList version {version}')
        msz = _size(MAKER_FIELDS[version])
        if off + ROW * n > len(data) or moff + msz * nm > len(data):
            raise VehicleListError('records run past the end')
        rows = [data[off + ROW * i: off + ROW * (i + 1)] for i in range(n)]
        makers = [data[moff + msz * i: moff + msz * (i + 1)] for i in range(nm)]
        return cls(version, rows, makers, e, data[20:off], data[moff + msz * nm:])

    def build(self):
        off = 20 + len(self.header_extra)
        moff = off + ROW * len(self.rows)
        out = bytearray(struct.pack(self.e + '5I', self.version, len(self.rows), len(self.makers), off, moff))
        out += self.header_extra
        for r in self.rows:
            out += r
        for m in self.makers:
            out += m
        out += self.tail
        out += bytes(-len(out) % 16)
        return bytes(out)

    # -- values -----------------------------------------------------------------------------------------------
    def get(self, rec, field):
        o, c = field[0], field[1]
        return struct.unpack_from(self.e + c, rec, o)[0]

    def set(self, rec, field, value):
        o, c = field[0], field[1]
        struct.pack_into(self.e + c, rec, o, float(value) if c == 'f' else int(value))

    def value(self, rec, key, fields=None):
        for f in fields or self.fields:
            if f[2] == key:
                return self.get(rec, f)
        return None

    def maker_by_id(self, mid):
        for m in self.makers:
            if self.value(m, 'id', self.maker_fields) == mid:
                return m
        return None

    # -- CSV (the vehicle table; manufacturers as a second block) ---------------------------------------------
    @staticmethod
    def _fmt(field, v):
        if field[1] == 'f':
            return f32_text(v)
        if field[4] == 'hex':
            return f'0x{v:x}'
        return str(v)

    def to_csv(self, strings=None):
        """Vehicles, a blank line, then manufacturers. The '(name)' columns are for reading only."""
        strings = strings or {}
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator='\n')
        w.writerow([f[2] for f in self.fields] + ['(name)'])
        for r in self.rows:
            w.writerow([self._fmt(f, self.get(r, f)) for f in self.fields]
                       + [strings.get(self.value(r, 'name'), '')])
        w.writerow([])
        w.writerow(['manufacturer:' + self.maker_fields[0][2]] + [f[2] for f in self.maker_fields[1:]] + ['(name)'])
        for m in self.makers:
            w.writerow([self._fmt(f, self.get(m, f)) for f in self.maker_fields]
                       + [strings.get(self.value(m, 'name', self.maker_fields), '')])
        return buf.getvalue()

    def _parse_rows(self, header, lines, fields, size, decimal_comma=False):
        cols = {k: i for i, k in enumerate(header)}
        missing = [f[2] for f in fields if f[2] not in cols]
        if missing:
            raise VehicleListError('CSV is missing the column(s): ' + ', '.join(missing))
        out = []
        for n, line in lines:
            rec = bytearray(size)
            for f in fields:
                txt = line[cols[f[2]]].strip() if cols[f[2]] < len(line) else ''
                try:
                    if not txt:
                        v = 0
                    elif f[1] == 'f':
                        v = float(txt.replace(',', '.') if decimal_comma else txt)
                    else:
                        v = int(txt, 16) if txt.lower().startswith('0x') else int(txt)
                    self.set(rec, f, v)
                except (ValueError, struct.error) as ex:
                    raise VehicleListError(f'line {n}, column {f[2]}: {txt!r} ({ex})')
            out.append(rec)
        return out

    def update_from_csv(self, text):
        """Replace the vehicle (and manufacturer) records with the CSV's rows. Returns (vehicles, makers)."""
        text = text.lstrip('\ufeff')
        first = text.split('\n', 1)[0]
        delim = ';' if first.count(';') > first.count(',') else ','      # Excel with a decimal-comma locale
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
        blocks, cur = [], None
        for n, line in enumerate(rows, 1):
            if not any(c.strip() for c in line):
                cur = None
                continue
            if cur is None:
                cur = [line, []]
                blocks.append(cur)
            else:
                cur[1].append((n, line))
        if not blocks:
            raise VehicleListError('empty CSV')
        vh, vlines = blocks[0]
        if vh[0].strip().lower() != self.fields[0][2]:
            raise VehicleListError(f'the first column must be "{self.fields[0][2]}"')
        dc = delim == ';'
        new_rows = self._parse_rows([c.strip() for c in vh], vlines, self.fields, ROW, dc)
        new_makers = self.makers
        for mh, mlines in blocks[1:]:
            if mh[0].strip().lower().startswith('manufacturer:'):
                head = [mh[0].strip()[len('manufacturer:'):]] + [c.strip() for c in mh[1:]]
                new_makers = self._parse_rows(head, mlines, self.maker_fields, self.maker_size, dc)
        self.rows, self.makers = new_rows, [bytearray(m) for m in new_makers]
        return len(self.rows), len(self.makers)


def read(res, e):
    return VehicleList.read(res.data(0), e)
