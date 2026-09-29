"""Stand-alone .SPS sound streams (SOUND\\STREAMS, UI\\SONGS, UI\\SEQUENCES\\STREAMS, ...), opened like a bundle
that holds one Wave resource. The Wave keeps the whole stream in memory, so the Wave preview, WAV export and
audio replacing work unchanged; saving writes the .SPS file again.

An .SPS file that starts with a data block continues a prefetched sound: its start (header + about a second)
is in the Wave resource of a bundle whose id is 0x01000000_<file number>. That start is looked up (Find names
index, else the bundles of the folders that use such files) so the sound plays whole; changing such a sound
is done in its bundle."""
import os
import re
import struct
import zlib

from . import eal3
from .bundle import Bundle, BundleError

T_WAVE = 0x81
GC = 0x01000000 << 32
# folders (relative to the game folder) whose bundles hold the starts of prefetched streams
OWNER_FOLDERS = ('UI\\SEQUENCEITEMS', 'EN_US\\FEEDBACKGROUPS', 'SOUND', 'HAWAII', 'UI\\MOVIES', 'UI\\SONGS')


def is_sps_file(path):
    return os.path.splitext(path)[1].lower() == '.sps'


def file_id(path):
    """Resource id of the Wave that plays this file: 0x01000000_<number> for numbered files."""
    stem = os.path.splitext(os.path.basename(path))[0]
    if re.fullmatch(r'\d+', stem):
        return GC | int(stem)
    return GC | zlib.crc32(stem.lower().encode('latin1', 'replace'))


_OWNER_INDEX = {}


def _entry_ids(path):
    """Resource ids in the entry table of a bundle (read without loading the data)."""
    try:
        with open(path, 'rb') as f:
            head = f.read(0x30)
            if len(head) < 0x30 or head[:4] != b'bnd2':
                return []
            e = '>' if struct.unpack('>H', head[4:6])[0] < 0x100 else '<'
            n, eo = struct.unpack(e + 'II', head[0x0C:0x14])
            f.seek(eo)
            table = f.read(0x48 * n)
    except OSError:
        return []
    return [struct.unpack_from(e + 'Q', table, 0x48 * i)[0] for i in range(len(table) // 0x48)]


def owner_index(root):
    """{resource id: [bundle paths]} of the bundles in OWNER_FOLDERS (built once per game folder)."""
    if root not in _OWNER_INDEX:
        idx = {}
        for sub in OWNER_FOLDERS:
            for dp, _, fn in os.walk(os.path.join(root, sub)):
                for f in fn:
                    if f.lower().endswith(('.bndl', '.bundle')):
                        p = os.path.join(dp, f)
                        for rid in _entry_ids(p):
                            if rid >> 32 == 0x01000000:
                                idx.setdefault(rid, []).append(p)
        _OWNER_INDEX[root] = idx
    return _OWNER_INDEX[root]


def find_owner(path, rid, locator=None):
    """(bundle path, Bundle, Wave resource) that holds the start of a prefetched stream, or None."""
    from .ops import game_root
    root = game_root(path)
    candidates = []
    if locator is not None:
        nroot = os.path.normcase(os.path.abspath(root)) if root else None
        candidates += [p for p in (locator(rid) or ()) if nroot is None or os.path.normcase(os.path.abspath(p)).startswith(nroot)]
    if root:
        candidates += owner_index(root).get(rid, [])
    seen = set()
    for p in candidates:
        k = os.path.normcase(p)
        if k in seen:
            continue
        seen.add(k)
        try:
            b = Bundle.open(p)
        except (OSError, BundleError):
            continue
        r = b.find(rid)
        if r is not None and r.type == T_WAVE and not r.missing:
            return p, b, r
    return None


class SpsBundle(Bundle):
    """A .SPS file as a one-resource bundle (see the module description)."""
    kind = 'sps'

    def __init__(self):
        super().__init__('PC')
        self.prefix = b''          # start of a prefetched sound (from its bundle) for continuation files
        self.owner = None          # (bundle path, wave id) of that bundle
        self.headerless = False

    @classmethod
    def open(cls, path, locator=None):
        with open(path, 'rb') as f:
            data = f.read()
        if not data or data[0] not in (0x48, 0x44, 0x45):
            raise BundleError('not an EA sound stream (.SPS)')
        b = cls()
        b.path = path
        rid = file_id(path)
        sps = data
        if data[0] != 0x48:
            b.headerless = True
            found = find_owner(path, rid, locator)
            if found is not None:
                bp, ob, wr = found
                part = eal3.wave_stream(wr.data(0), ob.e) or b''
                b.prefix = part
                b.owner = (bp, rid)
                sps = part + data
        head = None
        if sps[:1] == b'H':
            size = struct.unpack_from('>I', sps, 0)[0] & 0xFFFFFF
            h1, h2 = struct.unpack_from('>II', sps, 4)
            loop = struct.unpack_from('>I', sps, 12)[0] if size >= 16 else None
            head = eal3.snr_header(h1, h2, loop)
        if head is not None:
            c0 = eal3.build_wave(None, sps, head, '<')
        else:
            hdr = bytearray(eal3.WAVE_HEADER)
            struct.pack_into('<I', hdr, 0, eal3.WAVE_HEADER)
            struct.pack_into('<f', hdr, 0x10, 1.0)
            struct.pack_into('<I', hdr, 0x18, len(sps))
            c0 = bytes(hdr) + sps
        r = b.new_resource(rid, T_WAVE, [c0, b'', b'', b''])
        r.name = os.path.basename(path)
        r.modified = False
        b.resources = [r]
        b.modified = False
        return b

    @property
    def compressed(self):
        return False

    def add(self, res, replace=True):
        if not self.resources or (res.id == self.resources[0].id and res.type == T_WAVE):
            res._e = self.e
            self.resources = [res]
            self.modified = True
            return res
        raise BundleError('an .SPS file holds exactly one sound: drop an audio file on it to replace it')

    def to_bytes(self):
        if len(self.resources) != 1 or self.resources[0].type != T_WAVE:
            raise BundleError('an .SPS file holds exactly one sound')
        c0 = self.resources[0].data(0)
        f = eal3.wave_fields(c0, self.e)
        sps = eal3.wave_stream(c0, self.e)
        if f['kind'] != 'memory' or sps is None:
            raise BundleError('this sound does not hold its stream')
        if self.prefix:
            if not sps.startswith(self.prefix):
                raise BundleError('this file continues a sound whose start is in ' + os.path.basename(self.owner[0])
                                  + '; change the sound there')
            sps = sps[len(self.prefix):]
        return sps

    def save(self, path=None, verify=True):
        path = path or self.path
        data = self.to_bytes()
        if verify and data[:1] == b'H':
            eal3.parse_sps(data)
        tmp = path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)
        self.path = path
        self.modified = False
        for r in self.resources:
            r.modified = False
        return path
