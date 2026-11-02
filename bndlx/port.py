"""Port one build's bundle into the game's own bundle: scan the game first, then convert only what the game can read.

Converting a bundle on its own (convert.py) writes a NEW bundle that holds just the resources that could be converted.
That cannot replace a bundle of the game: the engine asks for every resource it knows and crashes on the ones that are
missing, and a converted Genesys object is only readable if the game's own schema has exactly the layout the object
was written with. So porting works the other way round. It starts from the game's bundle and keeps its table exactly
(same ids, types, order and count: nothing is added, nothing is removed). For every resource that the source bundle
also has:

  Texture, ColourCube, TextFile, LocalisedText, Wave
        converted (a texture only when both are the same kind: 2D or cube map)
  GenesysObject
        converted only when the object's type and every type it uses are identical in the source build and in the
        game (id, size, every field), it converts cleanly, it reads back with the game's schema, it converts back to
        the very same bytes, and every resource it imports exists in the game
  GenesysType
        never replaced: the game's own schema is what its code was built for
  everything else (materials, shaders, vertex layouts, meshes, ...)
        platform-specific: the game's own is kept

Whatever is not converted stays exactly as the game has it, and the report says which resources and why. Resources
that only the source has are not added.

"Scanning the game" reads every bundle of the game folder once and keeps two things: all Genesys types (the schemas
the game is built for) and the id of every resource (so imports can be checked). The result is cached per game folder.
"""
import hashlib
import os
import pickle
import re
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import convert, genesys, raster, roots
from .bundle import Bundle
from .labels import config_dir
from .restypes import T_CUBE, T_GOBJECT, T_GTYPE, T_STRINGS, T_TEXT, T_TEXTURE, name

REPLACEABLE = {T_TEXTURE, T_GOBJECT, T_CUBE, T_TEXT, T_STRINGS, convert.T_WAVE}


class PortError(ValueError):
    pass


class _Keep(Exception):
    """The game's own resource is kept; the message says why."""


# ---------------------------------------------------------------------------------------------------------------
# scanning the game
# ---------------------------------------------------------------------------------------------------------------
class GameScan:
    """What porting needs to know about a game folder: its Genesys types and the id of every resource."""
    VERSION = 1

    def __init__(self, root, platform=None):
        self.root = root
        self.platform = platform
        self.types = genesys.TypeDB()
        self.ids = {}                 # resource id -> (bundle path relative to root, type id)
        self.bundles = 0
        self.unreadable = []
        self.cached = False

    def add_file(self, path, rel=None):
        rel = rel or os.path.basename(path)
        try:
            b = Bundle.open(path)
        except Exception:
            self.unreadable.append(rel)
            return None
        self.bundles += 1
        if self.platform is None:
            self.platform = b.platform
        self.types.add_bundle(b)
        for r in b.resources:
            self.ids.setdefault(r.id, (rel, r.type))
        return b

    def summary(self):
        return (f'{self.bundles} bundles {"from the cache" if self.cached else "scanned"}, '
                f'{len(self.types.types)} Genesys types, {len(self.ids):,} resources'
                + (f', {len(self.unreadable)} unreadable' if self.unreadable else ''))

    @staticmethod
    def _listing(root):
        out = []
        for dp, _dirs, files in os.walk(root):
            for f in files:
                if f.upper().endswith('.BNDL'):
                    p = os.path.join(dp, f)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    out.append((os.path.relpath(p, root), st.st_size, st.st_mtime_ns))
        out.sort()
        return out

    @classmethod
    def scan(cls, root, progress=None, extra=(), use_cache=True):
        """Scan every .BNDL under `root` (plus the files in `extra`, e.g. a bundle kept outside the folder)."""
        if not root or not os.path.isdir(root):
            raise PortError(f'not a folder: {root}')
        files = cls._listing(root)
        key = hashlib.sha1(repr((cls.VERSION, os.path.abspath(root), files)).encode()).hexdigest()[:20]
        cache = os.path.join(config_dir(), f'gamescan_{key}.pkl')
        gs = None
        if use_cache and os.path.isfile(cache):
            try:
                with open(cache, 'rb') as f:
                    gs = pickle.load(f)
                gs.cached = True
            except Exception:
                gs = None
        if gs is None:
            gs = cls(root, roots.platform_of(root))
            n = len(files)
            for i, (rel, _size, _mtime) in enumerate(files):
                if progress:
                    progress(i, n)
                gs.add_file(os.path.join(root, rel), rel)
            if use_cache:
                try:
                    with open(cache + '.tmp', 'wb') as f:
                        pickle.dump(gs, f, protocol=pickle.HIGHEST_PROTOCOL)
                    os.replace(cache + '.tmp', cache)
                except OSError:
                    pass
        inside = {os.path.abspath(os.path.join(root, rel)).lower() for rel, _s, _m in files}
        for p in extra:
            if p and os.path.abspath(p).lower() not in inside:
                gs.add_file(p, os.path.basename(p))
        return gs


# ---------------------------------------------------------------------------------------------------------------
# schema comparison
# ---------------------------------------------------------------------------------------------------------------
def type_closure(types, tid):
    """Every type `tid` needs (itself, its base, the types of its fields, recursively) as comparable tuples."""
    seen, todo, out = set(), [tid], {}
    while todo:
        x = todo.pop()
        if not x or x in seen:
            continue
        seen.add(x)
        t = types.get(x)
        if t is None:
            out[x] = None
            continue
        out[x] = (t.kind, t.size, t.align, t.schema, t.base,
                  tuple((f.name_hash, f.type_hash, f.type_id, f.offset, f.size, f.count, f.align, f.flags, f.count_ptr)
                        for f in t.fields))
        todo.append(t.base)
        todo.extend(f.type_id for f in t.fields)
    return out


def closure_diff(a, b, types_a, types_b, limit=3):
    """A short description of how two closures differ."""
    out = []
    for tid in sorted(set(a) | set(b)):
        x, y = a.get(tid), b.get(tid)
        if x == y:
            continue
        nm = types_a.name(tid) if types_a.get(tid) else types_b.name(tid)
        if x is None and tid in a:
            out.append(f'{nm}: not in the source data')
        elif y is None and tid in b:
            out.append(f'{nm}: not in the game')
        elif tid not in a or tid not in b:
            out.append(f'{nm}: only in the {"game" if tid in b else "source"}')
        elif x[1] != y[1]:
            out.append(f'{nm}: size {x[1]} in the source, {y[1]} in the game')
        elif len(x[5]) != len(y[5]):
            out.append(f'{nm}: {len(x[5])} fields in the source, {len(y[5])} in the game')
        else:
            n = sum(1 for p, q in zip(x[5], y[5]) if p != q)
            out.append(f'{nm}: {n} field(s) differ' if n else f'{nm}: header differs')
        if len(out) >= limit:
            break
    return '; '.join(out) or 'schemas differ'


def object_type(res):
    return next((i.id for i in res.imports() if i.offset == 0), None)


# ---------------------------------------------------------------------------------------------------------------
# the port
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class Decision:
    id: int
    type: int
    action: str            # 'replace' | 'keep' | 'source-only'
    reason: str = ''
    note: str = ''


@dataclass
class PortResult:
    bundle: Bundle
    decisions: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    src_name: str = ''
    dst_name: str = ''

    @property
    def replaced(self):
        return [d for d in self.decisions if d.action == 'replace']

    @property
    def kept(self):
        return [d for d in self.decisions if d.action == 'keep']

    @property
    def source_only(self):
        return [d for d in self.decisions if d.action == 'source-only']

    def text(self, details=12):
        total = len(self.replaced) + len(self.kept)
        lines = [f'Ported {len(self.replaced)} of {total} resources from {self.src_name or "the source bundle"} '
                 f'into {self.dst_name or "the game bundle"}.',
                 f'The result has the game bundle\'s {total} resources with the same ids, types and order: '
                 f'nothing was added or removed.']
        by_type = Counter(name(d.type) for d in self.replaced)
        if by_type:
            lines.append('Converted: ' + ', '.join(f'{n} x{c}' for n, c in sorted(by_type.items())) + '.')
        guessed = [d for d in self.replaced if 'float' in d.note]
        if guessed:
            lines.append(f'{len(guessed)} object(s) needed the float guess for data the schema does not describe '
                         f'(lenient mode).')
        reasons = defaultdict(list)
        for d in self.kept:
            reasons[d.reason].append(d)
        if reasons:
            lines.append('')
            lines.append(f'Kept the game\'s own version of {len(self.kept)} resource(s):')
            for reason, ds in sorted(reasons.items(), key=lambda kv: -len(kv[1])):
                kinds = Counter(name(d.type) for d in ds)
                lines.append(f'  {", ".join(f"{n} x{c}" for n, c in sorted(kinds.items()))}: {reason}')
        notes = [d for d in self.replaced if d.note and 'float' not in d.note]
        if notes:
            lines.append('')
            lines.append('Notes on converted resources:')
            for d in notes[:details]:
                lines.append(f'  {d.id:#x} {name(d.type)}: {d.note}')
            if len(notes) > details:
                lines.append(f'  ... and {len(notes) - details} more')
        if self.source_only:
            lines.append('')
            lines.append(f'{len(self.source_only)} resource(s) exist only in the source and were not added '
                         f'(the game bundle has no entry for them).')
        for w in self.warnings:
            lines.append('')
            lines.append('Warning: ' + w)
        return '\n'.join(lines)

    def details_text(self):
        """One line per resource, for a report file."""
        out = [self.text(details=10 ** 9), '', 'Per resource:']
        for d in self.decisions:
            out.append(f'{d.id:#018x}  {name(d.type):<16} {d.action:<11} {d.reason}{(" [" + d.note + "]") if d.note else ""}')
        return '\n'.join(out)


def _same_kind_texture(s, r, src_p, dst_p):
    try:
        a, b = raster.info(s, src_p), raster.info(r, dst_p)
    except raster.RasterError as ex:
        raise _Keep(f'texture header not understood ({ex})') from None
    if a.faces != b.faces:
        raise _Keep('a cube map cannot replace a 2D texture (or the other way round)')
    notes = []
    if (a.w, a.h) != (b.w, b.h):
        notes.append(f'{b.w}x{b.h} -> {a.w}x{a.h}')
    if a.srgb != b.srgb:
        notes.append(f'sRGB {"on" if a.srgb else "off"} (the game\'s has it {"on" if b.srgb else "off"})')
    return '; '.join(notes)


def _keep_game_header_bits(conv, r, platform):
    """A PC texture header holds a few values the PS3 header cannot carry (flags, one unknown byte). The game's own
    texture of that id has them set the way its shaders and loader expect, so they are kept; only sRGB, which the
    PS3 header does state, comes from the source. Skipped when the mip count changes between 1 and several."""
    if platform != 'PC':
        return
    h, t = bytearray(conv.data(0)), r.data(0)
    if len(h) < 48 or len(t) < 48 or (h[0x2D] > 1) != (t[0x2D] > 1):
        return
    srgb = raster.PC_FLAG_SRGB
    flags = (struct.unpack_from('<I', h, 0x20)[0] & srgb) | (struct.unpack_from('<I', t, 0x20)[0] & ~srgb)
    struct.pack_into('<I', h, 0x20, flags)
    h[0x2C] = t[0x2C]
    conv.set_data(0, bytes(h))


def port_bundle(src, dst, src_types, game, strict=True, lenient=False, progress=None):
    """Port `src` (a bundle of another platform / build) into a copy of `dst` (a bundle of the game).

    src_types: TypeDB with the Genesys types of the source build (read in the source's byte order);
    game:      GameScan of the game folder `dst` belongs to. Returns a PortResult; its .bundle is ready to save."""
    if src.platform == dst.platform:
        raise PortError(f'both bundles are {src.platform} bundles: use Convert for a platform change, '
                        f'Copy / Paste or Replace to move resources between bundles of one platform')
    if dst.truncated or src.truncated:
        raise PortError('a truncated file cannot be ported')
    data = dst.original if (dst.original is not None and not dst.is_modified) else dst.to_bytes()
    out = Bundle.from_bytes(data)
    result = PortResult(out, src_name=os.path.basename(src.path or '') or 'the source bundle',
                        dst_name=os.path.basename(dst.path or '') or 'the game bundle')
    if any(g == b'' for g in dst.chunk_gap if g is not None) and dst.debug is None and dst.trailer:
        # the data blocks of the original overlap (an entry reaches past the point where the header says the next
        # block starts) and a tail follows: written as it is, the result would keep that inconsistency
        result.warnings.append(
            f'the game bundle\'s layout is irregular: its data blocks overlap (entries reach past where the header '
            f'says the next block starts) and {len(dst.trailer):,} bytes follow the last block. The ported file is '
            f'written with a consistent header and without that tail. If the game folder holds an untouched '
            f'original, port into that one instead.')
        out.trailer = b''
        out.dbg_value = None
    src_idx = {r.id: r for r in src.resources}
    dst_ids = {r.id for r in out.resources}
    dtypes = game.types
    closures = {}

    def closure(which, tid):
        k = (which, tid)
        if k not in closures:
            closures[k] = type_closure(src_types if which == 's' else dtypes, tid)
        return closures[k]

    n = len(out.resources)
    for i, r in enumerate(out.resources):
        if progress and i % 20 == 0:
            progress(i, n)
        s = src_idx.get(r.id)
        try:
            if s is None:
                raise _Keep('not in the source bundle')
            if r.missing or s.missing:
                raise _Keep('no data in the file')
            if s.type != r.type:
                raise _Keep(f'a different kind of resource in the source ({name(s.type)})')
            if r.type == T_GTYPE:
                raise _Keep('the game\'s own schema is always kept')
            if r.type not in REPLACEABLE:
                raise _Keep('platform-specific data (the game\'s own is kept)')
            note = ''
            if r.type == T_TEXTURE:
                note = _same_kind_texture(s, r, src.platform, dst.platform)
            elif r.type == T_GOBJECT:
                ts, td = object_type(s), object_type(r)
                if ts is None or ts != td:
                    raise _Keep('the object has a different type in the source')
                cs, cd = closure('s', ts), closure('d', ts)
                if cs != cd:
                    raise _Keep('schema differs between the source build and the game (' +
                                closure_diff(cs, cd, src_types, dtypes) + ')')
            try:
                conv = convert.convert_resource(s, src.platform, dst.platform, src_types, strict, lenient)
            except Exception as ex:
                msg = str(ex)
                if re.search(r'\d+ bytes of this .* not understood', msg):
                    msg = 'part of its data is not described by the schema (--lenient / the lenient menu entry ' \
                          'converts it when that data is clearly float32)'
                raise _Keep(f'cannot be converted safely: {msg}') from None
            if r.type == T_TEXTURE:
                _keep_game_header_bits(conv, r, dst.platform)
            if r.type == T_GOBJECT:
                try:
                    genesys.Reader(dtypes, dst.e).read_resource(conv)
                except Exception as ex:
                    raise _Keep(f'does not read with the game\'s schema ({type(ex).__name__}: {ex})') from None
                try:
                    back, _unk, _g = genesys.swap_object_ex(conv, dtypes, dst.e, lenient)
                except Exception as ex:
                    raise _Keep(f'cannot be converted back to check it ({ex})') from None
                if back != s.data(0):
                    raise _Keep('converting back does not give the original bytes')
                if getattr(conv, 'lenient_words', 0):
                    note = f'{conv.lenient_words} float word(s) guessed'
            absent = sorted({imp.id for imp in conv.imports()} - dst_ids - set(game.ids))
            if absent:
                raise _Keep('imports resources the game does not have (' + ', '.join(f'{x:#x}' for x in absent[:2]) +
                            (', ...' if len(absent) > 2 else '') + ')')
        except _Keep as k:
            result.decisions.append(Decision(r.id, r.type, 'keep', str(k)))
            continue
        conv.stream, conv.flags, conv.pad = r.stream, r.flags, r.pad
        conv.us_bits, conv.cs_bits = list(r.us_bits), list(r.cs_bits)
        conv.name, conv.debug_type = r.name, r.debug_type
        conv._e = out.e
        out.resources[i] = conv
        out.modified = True
        result.decisions.append(Decision(r.id, r.type, 'replace', '', note))
    for s in src.resources:
        if s.id not in dst_ids:
            result.decisions.append(Decision(s.id, s.type, 'source-only', 'not in the game bundle'))
    if progress:
        progress(n, n)
    verify(out, dst)
    return result


def verify(out, dst):
    """The result must have the game bundle's table exactly, and every kept resource must be its own bytes."""
    if [(r.id, r.type, r.stream) for r in out.resources] != [(r.id, r.type, r.stream) for r in dst.resources]:
        raise PortError('internal error: the ported table differs from the game bundle\'s')
    for a, b in zip(out.resources, dst.resources):
        if not a.modified and a.chunks() != b.chunks():
            raise PortError(f'internal error: {a.id:#x} should be unchanged but differs')


# ---------------------------------------------------------------------------------------------------------------
# from file names to a result (used by the Port window and by the command line)
# ---------------------------------------------------------------------------------------------------------------
def peek(path):
    """(platform, resource count) from the header of a bundle file; raises PortError / OSError."""
    with open(path, 'rb') as f:
        h = f.read(20)
    if len(h) < 20 or h[:4] != b'bnd2':
        raise PortError('not a bnd2 bundle')
    e = '>' if struct.unpack('>H', h[4:6])[0] < 0x100 else '<'
    platform = struct.unpack(e + 'H', h[6:8])[0]
    return {1: 'PC', 2: 'PS3'}.get(platform, f'platform {platform}'), struct.unpack(e + 'I', h[12:16])[0]


def inspect(path):
    """None for no file name, (platform, count) for a bundle, else a sentence saying what is wrong with the file."""
    if not path:
        return None
    if not os.path.isfile(path):
        return 'the file does not exist'
    try:
        return peek(path)
    except (OSError, PortError, struct.error) as ex:
        return f'cannot be read ({ex})'


def problems(src_path, src_info, dst_path, dst_info, out):
    """What is still missing or wrong in the three file names, as sentences (an empty list = ready).
    src_info / dst_info: what inspect() returned for them."""
    todo = []
    if not src_path:
        todo.append('Step 1: choose the source bundle (the PS3 beta file).')
    elif isinstance(src_info, str):
        todo.append(f'Step 1: the source bundle {src_info}.')
    if not dst_path:
        todo.append('Step 2: choose the game bundle to port into (the PC file of the same name).')
    elif isinstance(dst_info, str):
        todo.append(f'Step 2: the game bundle {dst_info}.')
    if isinstance(src_info, tuple) and isinstance(dst_info, tuple) and src_info[0] == dst_info[0]:
        todo.append(f'Both bundles are {src_info[0]} bundles: the source and the game bundle must be for different '
                    f'platforms.')
    if not out:
        todo.append('Step 3: choose where to save the result.')
    else:
        def same(a, b):
            return bool(a) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
        if same(out, dst_path):
            todo.append('Step 3: the result must not replace the game bundle: choose another file name.')
        elif same(out, src_path):
            todo.append('Step 3: the result must not replace the source bundle: choose another file name.')
    return todo


def suggest_output(dst_path):
    base, ext = os.path.splitext(dst_path)
    return f'{base}_ported{ext or ".BNDL"}'


def suggest_target(src_path):
    """The game's bundle at the same place in its folder, when the source lives in a game folder and a game folder of
    another platform is pinned in the program; '' when there is none."""
    sroot = roots.game_root(src_path) if src_path else None
    if not sroot:
        return ''
    for root in roots.FALLBACK.values():
        if root and os.path.normcase(root) != os.path.normcase(sroot):
            cand = os.path.join(root, os.path.relpath(src_path, sroot))
            if os.path.isfile(cand):
                return cand
    return ''


def plan(src_path, dst_path, lenient=False, progress=None, status=None, game_dir=None, source_game_dir=None,
         use_cache=True):
    """Open both bundles, scan the game folder (and the source build's folder when it can be found), and work out
    the port. Returns (PortResult, notes); nothing is written. status(text) is told what is going on."""
    say = status or (lambda text: None)
    notes = []
    src, dst = Bundle.open(src_path), Bundle.open(dst_path)
    groot = game_dir or roots.root_for(dst_path, dst.platform) or os.path.dirname(os.path.abspath(dst_path))
    say('Scanning the game folder...')
    game = GameScan.scan(groot, progress, extra=[dst_path], use_cache=use_cache)
    notes.append(f'Game folder {groot}: {game.summary()}.')
    types = genesys.TypeDB()
    types.add_bundle(src)
    sroot = source_game_dir or roots.root_for(src_path, src.platform)
    if sroot:
        say('Scanning the source build\'s folder...')
        sg = GameScan.scan(sroot, progress, extra=[src_path], use_cache=use_cache)
        notes.append(f'Source folder {sroot}: {sg.summary()}.')
        for tid, t in sg.types.types.items():
            types.types.setdefault(tid, t)
    else:
        notes.append('The source build\'s folder was not found, so only the types inside the source bundle are known; '
                     'objects that use other types will be kept as the game has them.')
    say('Checking every resource...')
    return port_bundle(src, dst, types, game, lenient=lenient, progress=progress), notes


def write(result, out_path):
    """Save the ported bundle and its per-resource report (OUT.port.txt); returns the report's path."""
    result.bundle.save(out_path)
    rep = out_path + '.port.txt'
    with open(rep, 'w', encoding='utf-8') as f:
        f.write(result.details_text())
    return rep
