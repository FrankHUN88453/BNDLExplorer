"""Resource names.

Bundles store resources by id only. Names come from:
  exact    - the debug data (ResourceStringTable) of bundles that have it (the PS3 prototype, some PC files);
           - names whose zlib CRC32 (of the lower-case name) equals a 32-bit resource id: the game builds
             32-bit ids that way, so a candidate is either exactly right or wrong. Candidates: debug names,
             strings in Genesys objects, Genesys type names, asset paths in the executable, and patterns
             (<script>.lua, <widget>_<id>.json, <id>_VEHICLESOUND, trk_unit<n>_list, ...);
           - the renderables of every model path found (`..._LOD0`, `..._-1_LOD0`, ...).
             Guessing asset numbers is NOT used: with CRC32, crc(name + suffix) follows from crc(name), so a
             wrong number matching a model id would also "match" its renderables.
           - Genesys type names (stored in the types).
  derived  - Genesys objects: their name-like string field, else "<type> <GameChanger number>";
           - vehicles: the car name from the bundle's behaviour objects;
           - textures / materials: the model or object that uses them.
  Genesys field and enum value names (hashed in the data): gnames.solve over the identifiers of the executables
  (NFS13.exe, the PS3 debug SELFs when their folder is scanned) and the bundles' strings.
The scan result is kept in %APPDATA%\\BNDLExplorer\\names.json.gz.
"""
import gzip
import json
import os
import re
import struct
import time
import zlib
from concurrent.futures import ProcessPoolExecutor

from . import gnames as gn
from .labels import config_dir

GC_RE = re.compile(r'^GameChanger \(ID=(\d+),type=(\d+),index=(\d+)\)$')
PATH_RE = re.compile(r'^(gamedb://.*?)\?ID=(\d+)(.*)$')
QUAD_RE = re.compile(r'^(\d+)_(\d+)(_fh)?(_fv)?_renderable$', re.I)
NAME_FIELDS = (0x4E616D65, 0xD11FCE34, 0x42655941, 0xDFFF737A, 0xB759E734, 0xADA190B9, 0x716EE533)
VEH_SUFFIXES = ('_AnimationList_DamageBehaviour', '_DamageBehaviour', ' DamageBehaviour')
LOD_SUFFIXES = [f'_LOD{k}' for k in range(8)] + [f'_-1_LOD{k}' for k in range(8)] + ['_OCCLUSION']
T_RENDERABLE, T_MODEL = 0x05, 0x51
SLOTS = {0x0E88: 'Diffuse', 0x0D9C: 'Normal', 0x31F2: 'Specular', 0x2837: 'Effects', 0x27D6: 'Crumple',
         0x84E0: 'LightmapLights', 0x192D: 'AO', 0x5C7F: 'SpecAndAO'}


WHEEL_PARTS = ('tyre', 'brake disc', 'rim', 'caliper')


def plausible(name, t):
    """Is `name` the kind of name a resource of type t has? (keeps random CRC32 matches out)"""
    low = name.lower()
    is_path = low.startswith('gamedb://')
    if t == 0x05 and low.endswith('_renderable'):
        return True
    if t in (0x05, 0x51):
        return is_path and re.search(r'\.(mb|ma|fbx)\?id=', low) is not None
    if t in (0x205, 0x215, 0x217):
        return is_path
    if t == 0x70:
        return low.endswith(('.json', '.lua', '.xml'))
    if t == 0x207:
        return low.endswith('sound')
    if t == 0x82:
        return re.search(r'_swc\d$', low) is not None
    if t in (0x50, 0x204, 0x206, 0x213, 0x216, 0x218):
        return low.startswith('trk_unit')
    if t in (0x20F, 0x214):
        return 'gcvr' in low
    if t == 0x01:
        return low.endswith('_texture') or is_path
    if t in (0x03, 0x04, 0xC0, 0x07):
        return low.startswith(('vertexdesc', 'cgsvertexprogramstate', 'samplerstate', 'vertexprogramstate'))
    return not is_path or t in (0x60,)


def crc(s):
    return zlib.crc32(s.encode('latin1', 'replace'))


def crc_lower(s):
    return zlib.crc32(s.lower().encode('latin1', 'replace'))


def short(name):
    """Display form: file name of an asset path (+ LOD suffix), otherwise the name itself."""
    m = PATH_RE.match(name)
    if m:
        base, num, rest = m.groups()
        stem = base.rsplit('/', 1)[-1]
        stem = re.sub(r'\.(mb|ma|fbx)$', '', stem, flags=re.I)
        rest = rest.replace('_-1_', ' ').replace('_', ' ').strip()
        return f'{stem} {rest}'.strip()
    return name


def vgs_role(off):
    """Role of a model imported by a VehicleGraphicsSpec at `off` (0x30 body, 0x8D0.. 4 parts per wheel)."""
    if off == 0x30:
        return 'body'
    if off >= 0x8D0:
        w, p = divmod(off - 0x8D0, 16)
        return f'wheel {w + 1} {WHEEL_PARTS[(p // 4) % 4]}'
    return 'part'


def gc_number(rid):
    return rid & 0xFFFFFFFF if (rid >> 32) in (0x01000000, 0x01010000, 0x01020000) else None


# ---------------------------------------------------------------------------------------------------------------
# per-bundle harvest (runs in worker processes during a scan)
# ---------------------------------------------------------------------------------------------------------------
def _strings(node, out, depth=0):
    from .genesys import Node
    if depth > 16:
        return
    for v in node.fields.values():
        if isinstance(v, str):
            if v:
                out.add(v[:240])
        elif isinstance(v, Node):
            _strings(v, out, depth + 1)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, Node):
                    _strings(x, out, depth + 1)
                elif isinstance(x, str) and x:
                    out.add(x[:240])


def object_name(node, types):
    t = types.get(node.type)
    for f in (t.fields if t else []):
        if f.name_hash in NAME_FIELDS:
            v = node.fields.get(f.name_hash)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _json_strings(v, out):
    if isinstance(v, str):
        if v and len(v) < 200:
            out.add(v)
    elif isinstance(v, dict):
        for k, x in v.items():
            out.add(k)
            _json_strings(x, out)
    elif isinstance(v, list):
        for x in v:
            _json_strings(x, out)


def bundle_names(b, types=None):
    """Names that can be worked out inside one bundle.
    Returns {'exact': {id: name}, 'objnames': {id: name}, 'strings': set, 'car': str|None, 'typenames': {}}."""
    from . import genesys
    out = {'exact': {}, 'objnames': {}, 'strings': set(), 'car': None, 'typenames': {}, 'gtypes': {}}
    T = types
    if T is None:
        T = genesys.TypeDB()
        T.add_bundle(b)
    rd = genesys.Reader(T, b.e)
    gcs = {gc_number(r.id) for r in b.resources if gc_number(r.id) is not None}
    tex_gcs = [gc_number(r.id) for r in b.resources if r.type == 0x01 and gc_number(r.id) is not None]
    for r in b.resources:
        if r.missing:
            continue
        if r.type == 0x14:
            t = T.get(r.id)
            if t is not None and t.name:
                out['typenames'][r.id] = t.name
                out['strings'].add(t.name)
            if t is not None and t.fields:
                fl = []
                for f in t.fields:
                    cf = t.count_field(f) if t.kind == 7 and f.flags & 8 else None
                    fl.append((f.name_hash, cf.name_hash if cf is not None else None))
                out['gtypes'][t.name or f'{r.id:x}'] = fl
        elif r.type == 0x15:
            try:
                node = rd.read_resource(r)
            except Exception:
                continue
            _strings(node, out['strings'])
            n = object_name(node, T)
            if n:
                out['objnames'][r.id] = n
                for suf in VEH_SUFFIXES:
                    if n.endswith(suf) and out['car'] is None:
                        out['car'] = n[:-len(suf)].strip()
        elif r.type == 0x70 and not r.id >> 32:
            try:
                ln = struct.unpack_from(b.e + 'I', r.data(0), 0)[0]
                txt = bytes(r.data(0)[4:4 + ln])
            except Exception:
                continue
            for m in re.finditer(rb'name="([^"]{1,120})"', txt):
                out['strings'].add(m.group(1).decode('latin1'))
            if txt[:1] == b'{':
                try:
                    js = json.loads(txt.decode('utf-8', 'replace'))
                except ValueError:
                    js = None
                if isinstance(js, dict):
                    _json_strings(js, out['strings'])
                    nm = js.get('Name')
                    if isinstance(nm, str) and nm:
                        for gc in gcs:
                            cand = f'{nm}_{gc}.json'
                            if crc_lower(cand) == r.id:
                                out['exact'][r.id] = cand
                                break
                        else:
                            out['objnames'][r.id] = f'{nm}.json'
        elif r.type == 0x05 and not r.id >> 32 and r.import_count == 1 and tex_gcs:
            try:
                imp = r.imports()[0]
            except Exception:
                continue
            mat = gc_number(imp.id)
            if mat is None:
                continue
            for tg in tex_gcs:
                for fl in ('', '_fh', '_fv', '_fh_fv'):
                    cand = f'{mat}_{tg}{fl}_renderable'
                    if crc_lower(cand) == r.id:
                        out['exact'][r.id] = cand
                        break
                if r.id in out['exact']:
                    break
    return out


def harvest(path):
    """Everything a scan needs from one bundle file (a plain dict so it pickles cheaply)."""
    from .bundle import Bundle
    out = {'path': path, 'ids': [], 'debug': {}, 'strings': set(), 'objnames': {}, 'typenames': {}, 'car': None,
           'exact': {}, 'gtypes': {}, 'idents': set()}
    try:
        b = Bundle.open(path)
    except Exception:
        return out
    for r in b.resources:
        out['ids'].append((r.id, r.type))
        if r.name:
            out['debug'][r.id] = r.name
    try:
        bn = bundle_names(b)
    except Exception:
        return out
    for k in ('strings', 'objnames', 'typenames', 'car', 'exact', 'gtypes'):
        out[k] = bn[k]
    idents = set()
    for r in b.resources:
        if r.type in (0x15, 0x70, 0x74, 0x105) and not r.missing:
            try:
                idents |= gn.identifiers(bytes(r.data(0)))
            except Exception:
                pass
    out['idents'] = idents
    return out


# ---------------------------------------------------------------------------------------------------------------
# database
# ---------------------------------------------------------------------------------------------------------------
class NameDB:
    VERSION = 4

    def __init__(self):
        self.path = os.path.join(config_dir(), 'names.json.gz')
        self.exact = {}          # id -> name (full)
        self.objnames = {}       # id -> name found in the object (Genesys)
        self.cars = {}           # bundle file name (upper case) -> car name
        self.bases = set()       # asset path bases (gamedb://...), for new bundles
        self.scanned = []        # folders scanned
        self.where = {}          # id -> [bundle paths] (up to 3)
        self.gnames = {}         # Genesys field / enum value name hash -> name
        self.stats = {}
        self.dirty = False

    @classmethod
    def load(cls):
        db = cls()
        try:
            with gzip.open(db.path, 'rt', encoding='utf-8') as f:
                js = json.load(f)
            if js.get('version') == cls.VERSION:
                db.exact = {int(k, 16): v for k, v in js['exact'].items()}
                db.objnames = {int(k, 16): v for k, v in js.get('objnames', {}).items()}
                db.cars = js.get('cars', {})
                db.bases = set(js.get('bases', []))
                db.scanned = js.get('scanned', [])
                db.stats = js.get('stats', {})
                files = js.get('files', [])
                db.where = {int(k, 16): [files[i] for i in v] for k, v in js.get('where', {}).items()}
                db.gnames = {int(k, 16): v for k, v in js.get('gnames', {}).items()}
        except (OSError, ValueError, KeyError):
            pass
        return db

    def save(self):
        files = sorted({p for v in self.where.values() for p in v})
        fidx = {p: i for i, p in enumerate(files)}
        js = {'version': self.VERSION, 'scanned': self.scanned, 'stats': self.stats, 'files': files,
              'where': {f'{k:x}': [fidx[p] for p in v] for k, v in self.where.items()},
              'exact': {f'{k:x}': v for k, v in self.exact.items()},
              'objnames': {f'{k:x}': v for k, v in self.objnames.items()},
              'cars': self.cars, 'bases': sorted(self.bases),
              'gnames': {f'{k:x}': v for k, v in self.gnames.items()}}
        tmp = self.path + '.tmp'
        with gzip.open(tmp, 'wt', encoding='utf-8') as f:
            json.dump(js, f)
        os.replace(tmp, self.path)
        self.dirty = False

    # -- quick learning from an opened bundle -------------------------------------------------------------------
    def learn_bundle(self, b, path=None):
        """Debug names of an opened bundle (cheap). Returns the number of new names."""
        n = 0
        for r in b.resources:
            if r.name and not GC_RE.match(r.name) and r.id not in self.exact:
                self.exact[r.id] = r.name
                n += 1
                m = PATH_RE.match(r.name)
                if m:
                    self.bases.add(m.group(1))
        if n:
            self.dirty = True
        return n

    def learn_names(self, bn):
        """Merge bundle_names() output (from an opened bundle)."""
        n = 0
        for rid, nm in list(bn['exact'].items()) + list(bn['typenames'].items()):
            if rid not in self.exact:
                self.exact[rid] = nm
                n += 1
        for rid, nm in bn['objnames'].items():
            if rid not in self.objnames:
                self.objnames[rid] = nm
                n += 1
        if n:
            self.dirty = True
        return n

    def name(self, rid):
        return self.exact.get(rid)

    @staticmethod
    def genesys_names(gtypes, strings, exe_paths=(), selfs=()):
        """Names of the Genesys fields / enum values of gtypes from the identifiers of the executables (the PC
        exe, the largest PS3 debug SELF: it has the most symbols) and the bundles' strings."""
        idents = set()
        for s in strings:
            idents.update(m.decode('latin1') for m in gn.IDENT.findall(s.encode('latin1', 'replace')))
        bins = [p for p in exe_paths if os.path.basename(p).lower() != 'eboot.bin']
        big = sorted(selfs, key=lambda p: ('INTERNAL' not in os.path.basename(p).upper(), -os.path.getsize(p)))
        for p in bins + big[:1]:
            try:
                with open(p, 'rb') as f:
                    idents |= gn.identifiers(f.read())
            except OSError:
                pass
        idents.update(tn for tn in gtypes)
        return gn.solve(gtypes, idents) if gtypes and idents else {}

    # -- full scan ----------------------------------------------------------------------------------------------
    def scan(self, folders, progress=None, workers=None, exe_paths=()):
        """Scan every bundle under `folders` and work out as many names as possible."""
        self.exact, self.objnames, self.cars = {}, {}, {}
        t0 = time.time()
        files = []
        selfs = []
        for root in folders:
            for dp, _, fn in os.walk(root):
                files += [os.path.join(dp, f) for f in fn if f.lower().endswith(('.bndl', '.bundle'))]
                selfs += [os.path.join(dp, f) for f in fn if f.lower().endswith('.self')]
        ids = {}
        where = {}
        debug = {}
        strings = set()
        nums = set()
        widget = []
        gtypes = {}
        idents = set()
        n = max(len(files), 1)
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for i, h in enumerate(ex.map(harvest, files, chunksize=4)):
                if progress:
                    progress(i, n)
                for rid, t in h['ids']:
                    ids.setdefault(rid, t)
                    w = where.setdefault(rid, [])
                    if len(w) < 3 and h['path'] not in w:
                        w.append(h['path'])
                debug.update(h['debug'])
                strings |= h['strings']
                idents |= h.get('idents', set())
                for rid, nm in h['objnames'].items():
                    self.objnames.setdefault(rid, nm)
                    widget.append((nm, rid & 0xFFFFFFFF))
                for rid, nm in h['typenames'].items():
                    self.exact.setdefault(rid, nm)
                for rid, nm in h['exact'].items():
                    self.exact.setdefault(rid, nm)
                for tn, fl in h.get('gtypes', {}).items():
                    gtypes.setdefault(tn, fl)
                fname = os.path.basename(h['path']).upper()
                if h['car']:
                    self.cars[fname] = h['car']
                for x in re.findall(r'\d{3,8}', fname):
                    nums.add(int(x))
        # exact: debug names
        for rid, nm in debug.items():
            if not GC_RE.match(nm):
                self.exact.setdefault(rid, nm)
                m = PATH_RE.match(nm)
                if m:
                    self.bases.add(m.group(1))
        for p in exe_paths:
            try:
                with open(p, 'rb') as f:
                    data = f.read()
                for m in re.finditer(rb'gamedb://[\x20-\x7e]{1,240}', data):
                    s = m.group(0).decode('latin1')
                    strings.add(s)
                    mm = PATH_RE.match(s)
                    if mm:
                        self.bases.add(mm.group(1))
            except OSError:
                pass
        for s in strings:
            if s.startswith('gamedb://'):
                mm = PATH_RE.match(s)
                if mm:
                    self.bases.add(mm.group(1))
        # exact: CRC32 candidates
        unknown = {rid for rid, t in ids.items() if not rid >> 32 and rid not in self.exact}
        pool = set(strings) | set(debug.values()) | set(self.objnames.values())

        def try_name(s):
            for c in (crc_lower(s), crc(s)):
                if c in unknown and plausible(s, ids[c]):
                    self.exact[c] = s
                    unknown.discard(c)

        for s in pool:
            try_name(s)
            for ext in ('.json', '.lua', '.xml'):
                try_name(s + ext)
        for nm, gc in widget:
            try_name(f'{nm}_{gc}.json')
        for num in nums:
            for suf in ('_VEHICLESOUND', '_COMPETITORSOUND', '_SWC0', '_texture'):
                try_name(f'{num}{suf}')
        for k in range(4000):
            for suf in ('list', 'dynlist', 'proplist', 'lightlist', 'cmplist', 'hdr'):
                try_name(f'trk_unit{k}_{suf}')
            try_name(f'TRK_UNIT{k}_GCVR')
            for g in range(8):
                try_name(f'TRK_UNIT{k}_GCVR_InstanceGroup{g}')
        # LOD renderables of every model path we know
        for nm in [v for v in self.exact.values() if PATH_RE.match(v) and not re.search(r'_(LOD\d|OCCLUSION)$', v)]:
            for suf in LOD_SUFFIXES:
                c = crc_lower(nm + suf)
                if c in unknown and ids[c] == T_RENDERABLE:
                    self.exact[c] = nm + suf
                    unknown.discard(c)
        self.gnames = self.genesys_names(gtypes, strings | idents, exe_paths, selfs)
        named = sum(1 for r in ids if r in self.exact or r in self.objnames)
        nf = {h for fl in gtypes.values() for h, _ in fl}
        self.stats = {'resources': len(ids), 'named': named, 'exact': sum(1 for r in ids if r in self.exact),
                      'files': len(files), 'seconds': round(time.time() - t0),
                      'gfields': len(nf), 'gnamed': sum(1 for h in nf if h in self.gnames or gn.ascii_name(h))}
        self.where = where
        self.scanned = sorted(set(self.scanned) | set(folders))
        self.dirty = True
        self.save()
        return self.stats
