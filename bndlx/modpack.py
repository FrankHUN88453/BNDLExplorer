"""Mod packages: the changes made to a game folder, as a zip that installs on another copy of the game.

A package (.zip) holds manifest.json and, per changed file of the game folder:
  bundles (.BNDL)  only the resources that differ from the original bundle, as .bres files
                   (bundles/<path>/<id>_<stream>.bres), plus what it removes and, if it reorders, the order;
  other files      the whole file (files/<path>).
manifest.json: {"format": "bndlx-mod", "version": 1, "name", "author", "mod_version", "description", "platform",
"created", "tool", "files": [{"path", "kind": "bundle" | "file", "base_md5", "result_md5", "resources":
[{"key", "type", "name", "md5"}], "removed": [key], "order": [key] (only when the mod reorders), "size"}]}. A
resource key is "0x<id>/<stream>" (multi-stream bundles hold some ids once per stream), its file
bundles/<path>/<16 hex digits of the id>_<stream>.bres; paths are relative to the game folder, with '/'. Only
"format", "version", "name", "platform" and each file's "path", "kind" (and a bundle's "resources") are needed:
a package can also be zipped by hand.

Installing is a load order (Profile): every time, each file any enabled mod touches is rebuilt from its original
(<file>.orig, kept once the first time the file is changed) with the mods applied in order; for a bundle the mods'
resources are merged, so two mods only meet where they change the same resource (the later one wins: a conflict,
reported). Files no mod touches any more go back to their original; files the mods created are removed. The profile
(<game>/bndlx_mods/profile.json) remembers what it wrote, so a file changed outside since (an edit in BNDL Explorer)
is reported instead of being overwritten.
"""
import datetime
import hashlib
import json
import os
import posixpath
import shutil
import zipfile
from dataclasses import dataclass, field

from . import resfile
from .bundle import Bundle, BundleError

FORMAT = 'bndlx-mod'
ORDER_FORMAT = 'bndlx-mod-order'
VERSION = 1
STATE_DIR = 'bndlx_mods'
SKIP_DIRS = {STATE_DIR, '__overlay', '__installer', 'backup', 'support'}
EXE_EXT = ('.exe', '.dll', '.asi')


class ModError(ValueError):
    pass


def md5_bytes(data):
    return hashlib.md5(data).hexdigest()


def md5_file(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def rel(root, path):
    return os.path.relpath(path, root).replace(os.sep, '/')


def safe_rel(p):
    """A package path that stays inside the game folder (no drive, no absolute path, no '..')."""
    p = str(p).replace('\\', '/')
    norm = posixpath.normpath(p)
    if (not p or p.startswith('/') or ':' in p or norm.startswith('..') or '/../' in f'/{norm}/'
            or norm in ('.', '')):
        raise ModError(f'unsafe path in the package: {p!r}')
    return norm


def original_of(root, relpath):
    return os.path.join(root, *relpath.split('/')) + '.orig'


def is_bundle_bytes(data):
    return data[:4] == b'bnd2'


# a resource is (id, stream): multi-stream bundles hold the same id once per stream (the Genesys types)
def rkey(r):
    return (r.id, r.stream)


def key_str(k):
    return f'{k[0]:#x}/{k[1]}'


def parse_key(s):
    i, _, st = str(s).partition('/')
    return int(i, 16), int(st or 0)


def bres_name(path, k):
    return f'bundles/{path}/{k[0]:016x}_{k[1]}.bres'


def put(b, res):
    """res into bundle b, replacing the resource of the same id and stream (else appended)."""
    res._e = b.e
    for i, r in enumerate(b.resources):
        if rkey(r) == rkey(res):
            b.resources[i] = res
            b.modified = True
            return
    b.resources.append(res)
    b.modified = True


# ---------------------------------------------------------------------------------------------------------------
# what changed in a game folder
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class Change:
    path: str                  # relative, '/'
    kind: str                  # 'bundle' | 'file'
    size: int
    base: str = ''             # the original it differs from (absolute), '' = a new file
    resources: int = 0         # bundles: changed / added resources
    removed: int = 0
    note: str = ''
    default: bool = True       # ticked in the list (an exe is not)


def resource_key(r):
    return (r.type, r.stream, r.flags, r.import_offset, r.import_count, tuple(r.us_bits), tuple(r.cs_bits),
            tuple(md5_bytes(c) for c in r.chunks()))


def diff_bundles(base, new):
    """(changed / added resources of `new`, (id, stream) keys `new` no longer has, key order of `new`)."""
    old = {rkey(r): r for r in base.resources}
    changed = [r for r in new.resources if rkey(r) not in old or resource_key(r) != resource_key(old[rkey(r)])]
    keys = {rkey(r) for r in new.resources}
    removed = [rkey(r) for r in base.resources if rkey(r) not in keys]
    return changed, removed, [rkey(r) for r in new.resources]


def scan(root, base_root=None, progress=None):
    """Files of the game folder that differ from their original: <file>.orig next to it, or the same file in
    base_root (a clean copy of the game)."""
    out = []
    todo = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [x for x in dn if x.lower() not in SKIP_DIRS]
        for f in fn:
            if f.lower().endswith(('.orig', '.tmp')):
                continue
            p = os.path.join(dp, f)
            if base_root:
                b = os.path.join(base_root, os.path.relpath(p, root))
                if os.path.isfile(b):
                    todo.append((p, b))
            elif os.path.isfile(p + '.orig'):
                todo.append((p, p + '.orig'))
    for i, (p, b) in enumerate(todo):
        if progress:
            progress(i, len(todo))
        if os.path.getsize(p) == os.path.getsize(b) and md5_file(p) == md5_file(b):
            continue
        out.append(describe(root, p, b))
    return sorted(out, key=lambda c: c.path.lower())


def describe(root, path, base=''):
    r = rel(root, path)
    size = os.path.getsize(path)
    exe = path.lower().endswith(EXE_EXT)
    c = Change(r, 'file', size, base, default=not exe)
    if exe:
        c.note = 'an executable: share a patch instead of the whole file'
    if base and path.upper().endswith('.BNDL'):
        try:
            changed, removed, _ = diff_bundles(Bundle.open(base), Bundle.open(path))
            c.kind, c.resources, c.removed = 'bundle', len(changed), len(removed)
        except (BundleError, OSError, ValueError) as ex:
            c.note = f'stored whole ({ex})'
    return c


# ---------------------------------------------------------------------------------------------------------------
# writing a package
# ---------------------------------------------------------------------------------------------------------------
def make_package(root, out, changes, meta, tool='', progress=None):
    """Write the package `out` (.zip) from `changes` (Change list, or relative paths of new files)."""
    root = os.path.abspath(root)
    files = []
    plat = None
    with zipfile.ZipFile(out + '.tmp', 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for i, c in enumerate(changes):
            if isinstance(c, str):
                p = os.path.join(root, *safe_rel(c).split('/'))
                c = describe(root, p, p + '.orig' if os.path.isfile(p + '.orig') else '')
            if progress:
                progress(i, len(changes))
            path = os.path.join(root, *c.path.split('/'))
            entry = {'path': c.path, 'kind': c.kind, 'size': os.path.getsize(path),
                     'base_md5': md5_file(c.base) if c.base else None, 'result_md5': md5_file(path)}
            if c.kind == 'bundle':
                base, new = Bundle.open(c.base), Bundle.open(path)
                if plat is None:
                    plat = new.platform
                changed, removed, order = diff_bundles(base, new)
                entry['resources'] = []
                for r in changed:
                    data = resfile.dump(r, new.platform, tool)
                    z.writestr(bres_name(c.path, rkey(r)), data)
                    entry['resources'].append({'key': key_str(rkey(r)), 'type': r.type, 'name': r.name or '',
                                               'md5': md5_bytes(data)})
                entry['removed'] = [key_str(x) for x in removed]
                old_keys = [rkey(r) for r in base.resources]
                gone, had = set(removed), set(old_keys)
                plain = [k for k in old_keys if k not in gone] + [k for k in order if k not in had]
                if order != plain:                         # only when the mod moved resources around
                    entry['order'] = [key_str(x) for x in order]
            else:
                z.write(path, f'files/{c.path}')
                if plat is None and path.upper().endswith('.BNDL'):
                    try:
                        plat = Bundle.open(path).platform
                    except (BundleError, OSError):
                        pass
            files.append(entry)
        manifest = {'format': FORMAT, 'version': VERSION, 'name': meta.get('name') or 'Unnamed mod',
                    'author': meta.get('author', ''), 'mod_version': meta.get('mod_version', ''),
                    'description': meta.get('description', ''), 'platform': plat or meta.get('platform') or 'PC',
                    'created': datetime.datetime.now().isoformat(timespec='seconds'), 'tool': tool, 'files': files}
        z.writestr('manifest.json', json.dumps(manifest, indent=1))
    Package(out + '.tmp')                                  # reads back: a broken package never replaces anything
    os.replace(out + '.tmp', out)
    return manifest


# ---------------------------------------------------------------------------------------------------------------
# reading a package
# ---------------------------------------------------------------------------------------------------------------
class Package:
    def __init__(self, path):
        self.path = path
        try:
            with zipfile.ZipFile(path) as z:
                self.names = set(z.namelist())
                m = json.loads(z.read('manifest.json').decode('utf-8'))
        except (OSError, zipfile.BadZipFile, KeyError, ValueError) as ex:
            raise ModError(f'{os.path.basename(path)} is not a mod package: {ex}') from None
        if m.get('format') != FORMAT:
            raise ModError(f'{os.path.basename(path)} is not a BNDL Explorer mod package')
        if int(m.get('version', 0)) > VERSION:
            raise ModError(f'{os.path.basename(path)} needs a newer BNDL Explorer (package version {m["version"]})')
        self.manifest = m
        self.files = []
        for f in m.get('files', []):
            f = dict(f)
            f['path'] = safe_rel(f['path'])
            if f.get('kind') not in ('bundle', 'file'):
                raise ModError(f'{f["path"]}: unknown kind {f.get("kind")!r}')
            if f['kind'] == 'file' and f'files/{f["path"]}' not in self.names:
                raise ModError(f'{f["path"]} is missing from the package')
            if f['kind'] == 'bundle':
                for r in f.get('resources', []):
                    if bres_name(f['path'], parse_key(r['key'])) not in self.names:
                        raise ModError(f'{f["path"]}: resource {r["key"]} is missing from the package')
            self.files.append(f)
        self.md5 = md5_file(path)

    @property
    def name(self):
        return self.manifest.get('name') or os.path.basename(self.path)

    @property
    def platform(self):
        return self.manifest.get('platform', 'PC')

    def read(self, member):
        with zipfile.ZipFile(self.path) as z:
            return z.read(member)

    def resources(self, entry):
        """[Resource] of a bundle entry, in the package's order."""
        out = []
        with zipfile.ZipFile(self.path) as z:
            for r in entry.get('resources', []):
                res, plat = resfile.load(z.read(bres_name(entry['path'], parse_key(r['key']))))
                if plat != self.platform:
                    raise ModError(f'{entry["path"]}: a {plat} resource in a {self.platform} package')
                out.append(res)
        return out

    def file_bytes(self, entry):
        return self.read(f'files/{entry["path"]}')

    def touches(self):
        """{path: set of (id, stream) keys or None (the whole file)}"""
        out = {}
        for f in self.files:
            if f['kind'] == 'file':
                out[f['path']] = None
            else:
                out[f['path']] = ({parse_key(r['key']) for r in f.get('resources', [])}
                                  | {parse_key(x) for x in f.get('removed', [])})
        return out

    def resource_label(self, path, k):
        """'name (type)' of a resource the package changes, for conflict lists."""
        from .restypes import name as type_name
        for f in self.files:
            if f['path'] == path:
                for r in f.get('resources', []):
                    if parse_key(r['key']) == k:
                        return (r.get('name') or f'{k[0]:#x}') + f' ({type_name(r.get("type", 0))})'
        return f'{k[0]:#x} (removed)'



# ---------------------------------------------------------------------------------------------------------------
# load order
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class Conflict:
    path: str
    rid: tuple                 # (id, stream); None: the whole file
    mods: list                 # names, in load order (the last one wins)
    label: str = ''            # the resource's name and type
    md5s: list = field(default_factory=list)          # the packages, same order


def conflicts(packages):
    """Where enabled packages (in load order) change the same file (a whole file) or the same resource."""
    by_path = {}
    for p in packages:
        for path, ids in p.touches().items():
            by_path.setdefault(path, []).append((p, ids))
    out = []
    for path in sorted(by_path):
        users = by_path[path]
        if len(users) < 2:
            continue
        if any(ids is None for _p, ids in users):
            out.append(Conflict(path, None, [p.name for p, _ids in users], 'the whole file',
                                [p.md5 for p, _ids in users]))
            continue
        per = {}
        for p, ids in users:
            for rid in ids:
                per.setdefault(rid, []).append(p)
        for rid, ps in sorted(per.items()):
            if len(ps) > 1:
                out.append(Conflict(path, rid, [p.name for p in ps], ps[-1].resource_label(path, rid),
                                    [p.md5 for p in ps]))
    return out


def conflict_pairs(found):
    """{(md5 a, md5 b): [Conflict]} for every two packages that meet (a loads before b)."""
    out = {}
    for c in found:
        for i in range(len(c.md5s)):
            for j in range(i + 1, len(c.md5s)):
                if c.md5s[i] != c.md5s[j]:
                    out.setdefault((c.md5s[i], c.md5s[j]), []).append(c)
    return out


@dataclass
class ModEntry:
    path: str                  # the .zip
    enabled: bool = True
    md5: str = ''
    name: str = ''


@dataclass
class Profile:
    root: str
    mods: list = field(default_factory=list)          # [ModEntry] in load order
    applied: dict = field(default_factory=dict)       # path -> md5 of what the last apply wrote
    created: list = field(default_factory=list)       # paths the mods created (no original)
    decided: list = field(default_factory=list)       # [winner md5, loser md5]: conflicts the user settled

    @staticmethod
    def file(root):
        return os.path.join(root, STATE_DIR, 'profile.json')

    @classmethod
    def load(cls, root):
        p = cls(os.path.abspath(root))
        try:
            with open(cls.file(root), encoding='utf-8') as f:
                js = json.load(f)
        except (OSError, ValueError):
            return p
        p.mods = [ModEntry(m['path'], bool(m.get('enabled', True)), m.get('md5', ''), m.get('name', ''))
                  for m in js.get('mods', [])]
        p.applied = dict(js.get('applied', {}))
        p.created = list(js.get('created', []))
        p.decided = [list(x) for x in js.get('decided', [])]
        return p

    def save(self):
        os.makedirs(os.path.join(self.root, STATE_DIR), exist_ok=True)
        js = {'format': 'bndlx-mod-profile', 'mods': [m.__dict__ for m in self.mods], 'applied': self.applied,
              'created': self.created, 'decided': self.decided}
        tmp = self.file(self.root) + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(js, f, indent=1)
        os.replace(tmp, self.file(self.root))

    def add(self, zip_path):
        pkg = Package(zip_path)
        ap = os.path.abspath(zip_path)
        for m in self.mods:
            if os.path.normcase(m.path) == os.path.normcase(ap):
                m.md5, m.name = pkg.md5, pkg.name
                return pkg
        self.mods.append(ModEntry(ap, True, pkg.md5, pkg.name))
        return pkg

    def undecided(self, found):
        """The conflict pairs (conflict_pairs of `found`) the user has not settled yet: {(a, b): [Conflict]}."""
        done = {frozenset(x) for x in self.decided}
        return {k: v for k, v in conflict_pairs(found).items() if frozenset(k) not in done}

    def decide(self, winner, loser):
        """The package `winner` (md5) is to win over `loser`: it loads after it (moved just behind it if it is
        before), and the pair is not asked about again."""
        idx = {m.md5: i for i, m in enumerate(self.mods)}
        if winner in idx and loser in idx and idx[winner] < idx[loser]:
            m = self.mods.pop(idx[winner])
            self.mods.insert(self.mods.index(next(x for x in self.mods if x.md5 == loser)) + 1, m)
        self.decided = [x for x in self.decided if frozenset(x) != frozenset((winner, loser))]
        self.decided.append([winner, loser])

    def packages(self, enabled_only=True):
        """[(ModEntry, Package or ModError)]"""
        out = []
        for m in self.mods:
            if enabled_only and not m.enabled:
                continue
            try:
                out.append((m, Package(m.path)))
            except ModError as ex:
                out.append((m, ex))
        return out

    # -- order files ------------------------------------------------------------------------------------------
    def export_order(self, path):
        js = {'format': ORDER_FORMAT, 'version': 1,
              'mods': [{'name': m.name, 'file': os.path.basename(m.path), 'md5': m.md5, 'enabled': m.enabled}
                       for m in self.mods]}
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(js, f, indent=1)

    def import_order(self, path, search=()):
        """Take the order (and enabled flags) from an order file. Packages are matched by md5, then by file name,
        among the profile's packages and the .zip files of the `search` folders (and the order file's folder).
        Returns the names that were not found."""
        try:
            with open(path, encoding='utf-8') as f:
                js = json.load(f)
        except (OSError, ValueError) as ex:
            raise ModError(f'cannot read {path}: {ex}') from None
        if js.get('format') != ORDER_FORMAT:
            raise ModError(f'{os.path.basename(path)} is not a mod order file')
        pool = {}
        for m in self.mods:
            pool.setdefault(m.md5, m.path)
            pool.setdefault(os.path.basename(m.path).lower(), m.path)
        for folder in list(search) + [os.path.dirname(os.path.abspath(path))]:
            try:
                names = os.listdir(folder)
            except OSError:
                continue
            for n in names:
                if n.lower().endswith('.zip'):
                    p = os.path.join(folder, n)
                    pool.setdefault(n.lower(), p)
        missing = []
        new = []
        for item in js.get('mods', []):
            p = pool.get(item.get('md5', '')) or pool.get(str(item.get('file', '')).lower())
            if not p:
                missing.append(item.get('name') or item.get('file') or '?')
                continue
            try:
                pkg = Package(p)
            except ModError:
                missing.append(item.get('name') or item.get('file') or '?')
                continue
            new.append(ModEntry(os.path.abspath(p), bool(item.get('enabled', True)), pkg.md5, pkg.name))
        keep = [m for m in self.mods if os.path.normcase(m.path) not in {os.path.normcase(n.path) for n in new}]
        self.mods = new + [ModEntry(m.path, False, m.md5, m.name) for m in keep]
        return missing


# ---------------------------------------------------------------------------------------------------------------
# applying
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class Report:
    written: list = field(default_factory=list)       # paths rebuilt with mods
    restored: list = field(default_factory=list)      # paths back to their original
    removed: list = field(default_factory=list)       # files the mods had created, deleted
    conflicts: list = field(default_factory=list)     # [Conflict]
    outside: list = field(default_factory=list)       # paths changed outside the launcher (left alone)
    errors: list = field(default_factory=list)        # (mod or path, message)
    skipped: list = field(default_factory=list)       # packages left out (wrong platform, unreadable)


def game_platform(root):
    from .roots import platform_of
    return platform_of(root) or 'PC'


def changed_outside(profile, paths):
    """Of `paths`, the files with changes the launcher did not make: different from what the last apply wrote,
    or (never applied) different from their .orig (an edit saved in BNDL Explorer, another tool's install)."""
    out = []
    for path in paths:
        p = os.path.join(profile.root, *path.split('/'))
        if not os.path.isfile(p):
            continue
        if path in profile.applied:
            if md5_file(p) != profile.applied[path]:
                out.append(path)
        elif os.path.isfile(p + '.orig') and (os.path.getsize(p) != os.path.getsize(p + '.orig')
                                              or md5_file(p) != md5_file(p + '.orig')):
            out.append(path)
    return out


def apply(profile, progress=None, force=False):
    """Rebuild the game files from their originals with the enabled packages in load order (see the module text).
    force: also overwrite files changed outside since the last apply."""
    root = profile.root
    rep = Report()
    plat = game_platform(root)
    pkgs = []
    for m, p in profile.packages():
        if isinstance(p, ModError):
            rep.skipped.append((m.name or os.path.basename(m.path), str(p)))
        elif p.platform != plat:
            rep.skipped.append((p.name, f'a {p.platform} package, the game folder is {plat}'))
        else:
            pkgs.append(p)
    rep.conflicts = conflicts(pkgs)
    plan = {}
    for p in pkgs:
        for f in p.files:
            plan.setdefault(f['path'], []).append((p, f))
    touched = set(plan) | set(profile.applied) | set(profile.created)
    if not force:
        rep.outside = changed_outside(profile, sorted(touched))
    done_applied, done_created = {}, []
    paths = sorted(touched)
    for i, path in enumerate(paths):
        if progress:
            progress(i, len(paths))
        if path in rep.outside:
            if path in profile.applied:
                done_applied[path] = profile.applied[path]
            if path in profile.created:
                done_created.append(path)
            continue
        target = os.path.join(root, *path.split('/'))
        orig = target + '.orig'
        try:
            if path not in plan:                               # no mod wants it any more
                if os.path.isfile(orig):
                    shutil.copyfile(orig, target)
                    rep.restored.append(path)
                elif path in profile.created and os.path.isfile(target):
                    os.remove(target)
                    rep.removed.append(path)
                continue
            if os.path.isfile(target) and not os.path.isfile(orig) and path not in profile.created:
                shutil.copyfile(target, orig)                  # the original, kept once
            base = orig if os.path.isfile(orig) else None
            data = build_file(base, plan[path])
            os.makedirs(os.path.dirname(target), exist_ok=True)
            tmp = target + '.tmp'
            with open(tmp, 'wb') as f:
                f.write(data)
            os.replace(tmp, target)
            done_applied[path] = md5_bytes(data)
            if base is None:
                done_created.append(path)
            rep.written.append(path)
        except (OSError, ModError, BundleError, ValueError) as ex:
            rep.errors.append((path, str(ex)))
            if path in profile.applied:
                done_applied[path] = profile.applied[path]
    profile.applied, profile.created = done_applied, done_created
    profile.save()
    return rep


def build_file(base, steps):
    """The file from its original (path or None) with [(package, entry)] applied in order."""
    data = None
    if base is not None:
        with open(base, 'rb') as f:
            data = f.read()
    b = None
    for pkg, entry in steps:
        if entry['kind'] == 'file':
            data, b = pkg.file_bytes(entry), None
            continue
        if b is None:
            if data is None:
                raise ModError(f'{entry["path"]}: the mod changes a bundle this game folder does not have')
            if not is_bundle_bytes(data):
                raise ModError(f'{entry["path"]} is not a bundle here')
            b = Bundle.from_bytes(data)
        for r in pkg.resources(entry):
            put(b, r)
        gone = {parse_key(x) for x in entry.get('removed', [])}
        if gone:
            b.resources = [r for r in b.resources if rkey(r) not in gone]
            b.modified = True
        order = [parse_key(x) for x in entry.get('order', [])]
        if order:
            pos = {k: i for i, k in enumerate(order)}
            cur = {id(r): i for i, r in enumerate(b.resources)}
            b.resources.sort(key=lambda r: (pos.get(rkey(r), len(order)), cur[id(r)]))
        data = None
    if b is not None:
        data = b.to_bytes()
        Bundle.from_bytes(data)                              # it reads back
    return data


def restore_all(profile, progress=None):
    """Every file back to its original: the load order stays, nothing enabled is applied."""
    keep = [m.enabled for m in profile.mods]
    for m in profile.mods:
        m.enabled = False
    try:
        return apply(profile, progress, force=False)
    finally:
        for m, k in zip(profile.mods, keep):
            m.enabled = k
        profile.save()
