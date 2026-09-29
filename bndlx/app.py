"""BNDL Explorer: view, edit, import / export and convert the .BNDL bundles of NFS Most Wanted (2012),
PC retail and PS3 prototype. Dear ImGui (imgui-bundle) front end."""
import itertools
import re
import json
import os
import shutil
import struct
import tempfile
import threading
import time
import traceback

import numpy as np
from imgui_bundle import hello_imgui, imgui, immvision

from . import convert, dragdrop, eal3, filedialog, genesys, gltf, mesh, ops, raster, resfile, textfile, theme
from . import vehiclelist as VL
from . import zonelist as ZL
from . import spsfile
from .audioplay import Player
from .soundtrack_ui import SoundtrackUI
from .explorer import Browser, ExplorerUI
from .thumbs import Thumbs
from .bundle import FLAG_NAMES, Bundle, BundleError
from .genesys import Node, Ref, TypeDB, leaf_kind
from .labels import Labels, config_dir
from .localised import StringTable
from . import names as N
from .restypes import T_CUBE, T_GOBJECT, T_GTYPE, T_STRINGS, T_TEXT, T_TEXTURE, name as type_name

APP = 'BNDL Explorer'
VERSION = '0.15'
PAYLOAD = 'BNDLX_RES'
_uid = itertools.count(1)


def human(n):
    if n < 1024:
        return f'{n} B'
    for unit in ('KB', 'MB', 'GB'):
        n /= 1024.0
        if n < 1024:
            return f'{n:.1f} {unit}'
    return f'{n:.1f} TB'


def parse_id(text):
    t = text.strip().lower().replace('_', '').replace(' ', '')
    if t.startswith('0x'):
        t = t[2:]
    if not t or len(t) > 16:
        raise ValueError('an id is up to 16 hex digits')
    return int(t, 16)


class Doc:
    """An open bundle with its view state and undo history."""

    def __init__(self, bundle, path):
        self.b = bundle
        self.path = path
        self.uid = next(_uid)
        self.filter = ''
        self.type_filter = None
        self.sel = set()
        self.anchor = None
        self.sort = (0, True)
        self.rows = None
        self.rows_key = None
        self.summary = {}
        self.dname = {}                     # id -> (display name, kind, tooltip)
        self.users = None                   # id -> [(user id, offset)] (built in the background)
        self.undo = []
        self.redo = []
        self.back = []
        self.fwd = []
        self.last_edit = (None, 0.0)

    @property
    def name(self):
        return os.path.basename(self.path) if self.path else 'untitled.BNDL'

    @property
    def modified(self):
        return self.b.is_modified

    def title(self):
        return f'{self.name}{" *" if self.modified else ""}  [{self.b.platform}]'

    def invalidate(self):
        self.rows = None

    # -- undo -------------------------------------------------------------------------------------------------
    def checkpoint(self, desc, rids=None):
        """Remember the resources about to change (None = all of them)."""
        if rids is None:
            snap = [(r.id, r.copy()) for r in self.b.resources]
            full = True
        else:
            snap = [(rid, self.b.find(rid).copy() if self.b.find(rid) else None) for rid in rids]
            full = False
        self.undo.append((desc, full, snap, self.b.root_id, self.b.flags))
        del self.undo[:-40]
        self.redo.clear()

    def edit_checkpoint(self, rid):
        """Checkpoint for value edits: consecutive edits of one resource within 1.5 s are one undo step."""
        last, t = self.last_edit
        now = time.time()
        if last != rid or now - t > 1.5:
            self.checkpoint(f'edit {rid:#x}', [rid])
        self.last_edit = (rid, now)

    def _state(self, full, rids):
        if full:
            return [(r.id, r.copy()) for r in self.b.resources]
        return [(rid, self.b.find(rid).copy() if self.b.find(rid) else None) for rid in rids]

    def _restore(self, full, snap, root, flags):
        if full:
            self.b.resources = [r for _, r in snap]
        else:
            for rid, old in snap:
                cur = self.b.find(rid)
                if cur is not None:
                    self.b.resources.remove(cur)
                if old is not None:
                    self.b.add(old.copy())
        self.b.root_id, self.b.flags = root, flags
        self.b.modified = True
        self.invalidate()
        self.summary.clear()

    def do_undo(self):
        if not self.undo:
            return None
        desc, full, snap, root, flags = self.undo.pop()
        cur = self._state(full, [rid for rid, _ in snap])
        self.redo.append((desc, full, cur, self.b.root_id, self.b.flags))
        self._restore(full, snap, root, flags)
        self.last_edit = (None, 0.0)
        return desc

    def do_redo(self):
        if not self.redo:
            return None
        desc, full, snap, root, flags = self.redo.pop()
        cur = self._state(full, [rid for rid, _ in snap])
        self.undo.append((desc, full, cur, self.b.root_id, self.b.flags))
        self._restore(full, snap, root, flags)
        return desc


def _clock(seconds):
    """m:ss.s"""
    seconds = max(0.0, seconds)
    return f'{int(seconds // 60)}:{seconds % 60:04.1f}'


class App(ExplorerUI, SoundtrackUI):
    def __init__(self, paths=(), select=None):
        self.docs = []
        self.tabs = [Browser()]
        self.tab = self.tabs[0]
        self.clip = None
        self.clip_dir = None
        self.renaming = None
        self.thumbs = Thumbs()
        self.names = N.NameDB.load()
        self.mesh_lib = mesh.Library(lambda rid: self.names.where.get(rid))
        self.viewer = None
        self.model = {'key': None, 'result': None, 'uploaded': None, 'lod': 0, 'show': 0, 'stats': None,
                      'nb': False, 'progress': ''}
        self.zmap = {'key': None}
        self.player = Player()
        self.st_ui = None                # soundtrack editor window state
        self.wave_cursor = {}            # sound key -> sample where Play starts (set by clicking the waveform)
        self.vlist = {'key': None, 'obj': None, 'sel': 0, 'msel': 0, 'filter': '', 'error': None}
        self.folder_cache = {}
        self.addr_edit = None
        self.addr_focus = False
        self.search_focus = False
        self._themed = False
        self._paste_target = None
        self.focus = None                   # (doc uid, resource id)
        self.types = TypeDB()
        self.labels = Labels()
        self.cfg = self.load_cfg()
        self.status = 'Open a bundle: the Open button, double click one in a folder, or drop .BNDL files on the window.'
        self.dropped = []
        self.drop_lock = threading.Lock()
        self.modal = None
        self.busy = None                    # (text, progress 0..1) while a background job runs
        self.job = None
        self.hwnd = None
        self.row_rects = []                 # [(y0, y1, rid)] of the visible rows, screen coordinates
        self.list_rect = self.details_rect = None
        self.drag = None                    # (doc uid, [ids]) being dragged
        self.drag_out_done = False
        self.tex = {'key': None, 'img': None, 'thread': None, 'params': None, 'mip': 0, 'face': 0, 'err': None}
        self.tex_lock = threading.Lock()
        self.gcache = {}
        self.text_edit = {'key': None, 'text': ''}
        self.str_filter = ''
        self.str_cache = {'key': None, 'table': None}
        self.hex_chunk = 0
        self.hex_patch = ['', '']
        self.find_text = ''
        self.results = None
        self.list_rect = self.details_rect = None
        self.want_select = select
        self._select_tab = id(self.tab)
        self.test_tab = None
        self.expand_all = False
        self.exit_ok = False
        for p in paths:
            self.open_path(p)

    # -- config -----------------------------------------------------------------------------------------------
    def load_cfg(self):
        try:
            with open(os.path.join(config_dir(), 'config.json'), encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save_cfg(self):
        try:
            with open(os.path.join(config_dir(), 'config.json'), 'w', encoding='utf-8') as f:
                json.dump(self.cfg, f, indent=1)
        except OSError:
            pass

    def remember(self, key, path):
        self.cfg[key] = path
        if key == 'last_bundle':
            rec = [p for p in self.cfg.get('recent', []) if os.path.normcase(p) != os.path.normcase(path)]
            self.cfg['recent'] = [path] + rec[:11]
        self.save_cfg()

    # -- documents --------------------------------------------------------------------------------------------
    def doc_by_uid(self, uid):
        return next((d for d in self.docs if d.uid == uid), None)

    def open_path(self, path):
        path = os.path.abspath(path)
        for d in self.docs:
            if d.path and os.path.normcase(d.path) == os.path.normcase(path):
                self.show_doc(d)
                self.status = f'{d.name} is already open.'
                return d
        try:
            if spsfile.is_sps_file(path):
                b = spsfile.SpsBundle.open(path, lambda rid: self.names.where.get(rid))
                title = ops.song_titles(path).get(os.path.basename(path).upper())
                if title:
                    b.resources[0].name = f'{title} ({os.path.basename(path)})'
            else:
                b = Bundle.open(path)
        except (OSError, BundleError, eal3.AudioError) as e:
            self.status = f'Cannot open {path}: {e}'
            return None
        d = Doc(b, path)
        self.docs.append(d)
        self.show_doc(d)
        self.names.learn_bundle(b, path)
        self.build_users(d)
        self.types.add_bundle(b)
        self.remember('last_bundle', path)
        extra = ' The file is truncated: read only.' if b.truncated else ''
        if getattr(b, 'kind', '') == 'sps':
            self.goto(b.resources[0].id, d)
            self.status = f'Opened the sound stream {path}.' + (
                f' Its start is in {os.path.basename(b.owner[0])}.' if b.owner else
                ' It continues a sound whose start was not found (run Find names).' if b.headerless and not b.prefix else '')
        else:
            self.status = f'Opened {path}: {b.platform}, {len(b.resources)} resources.{extra}'
        return d

    def rebuild_types(self):
        self.types = TypeDB()
        for d in self.docs:
            self.types.add_bundle(d.b)
        self.gcache.clear()

    def close_doc(self, d, force=False):
        if d.modified and not force:
            self.modal = {'kind': 'confirm_close', 'doc': d.uid}
            return
        self.docs.remove(d)
        if d in self.tabs:
            i = self.tabs.index(d)
            self.tabs.remove(d)
            if self.tab is d:
                self.tab = self.tabs[min(i, len(self.tabs) - 1)] if self.tabs else None
        if self.clip and self.clip['doc'] == d.uid:
            self.clip = None
        if self.focus and self.focus[0] == d.uid:
            self.focus = None
        self.rebuild_types()

    def focused(self):
        if not self.focus:
            return None, None
        d = self.doc_by_uid(self.focus[0])
        if d is None:
            return None, None
        return d, d.b.find(self.focus[1])

    def goto(self, rid, prefer=None):
        """Select a resource by id in the preferred document, else in any open bundle."""
        for d in ([prefer] if prefer else []) + self.docs:
            if d is not None and d.b.find(rid) is not None:
                self.show_doc(d)
                if d.type_filter is not None and d.b.find(rid).type != d.type_filter:
                    d.type_filter = None
                    d.invalidate()
                d.sel = {rid}
                d.anchor = rid
                self.focus = (d.uid, rid)
                d.scroll_to = rid
                return True
        self.status = f'{rid:#x} is not in any open bundle.'
        return False

    def changed(self, d, res=None):
        d.invalidate()
        d.dname.clear()
        self.build_users(d)
        if res is not None:
            d.summary.pop(res.id, None)
        if res is not None and res.type == T_GTYPE:
            self.rebuild_types()
        self.gcache.clear()
        self.tex['key'] = None
        self.str_cache['key'] = None

    # -- background jobs --------------------------------------------------------------------------------------
    def run_job(self, text, fn, done=None):
        """Run fn(progress) in a thread; done(result) runs on the GUI thread afterwards."""
        if self.job is not None:
            return
        self.busy = [text, 0.0]

        def progress(i, n):
            self.busy[1] = i / max(n, 1)

        def work():
            try:
                res = fn(progress)
                self.job_result = ('ok', res, done)
            except Exception as e:
                traceback.print_exc()
                self.job_result = ('error', e, done)

        self.job_result = None
        self.job = threading.Thread(target=work, daemon=True)
        self.job.start()

    def pump_job(self):
        if self.job is None or self.job.is_alive():
            return
        self.job = None
        self.busy = None
        kind, res, done = self.job_result
        if kind == 'error':
            self.status = f'Failed: {res}'
            self.modal = {'kind': 'message', 'title': 'Error', 'text': str(res)}
        elif done:
            done(res)

    # -- file actions -----------------------------------------------------------------------------------------
    def action_open(self):
        paths = filedialog.open_files('Open bundles', self.cfg.get('last_bundle', ''), filedialog.BUNDLES)
        for p in paths or []:
            self.open_path(p)

    def action_save(self, d, path=None):
        if d is None:
            return
        path = path or d.path
        if path is None:
            return self.action_save_as(d)
        if d.b.truncated:
            self.status = 'This file is truncated (resources without data); it cannot be saved.'
            return
        backup = None
        if os.path.exists(path) and not os.path.exists(path + '.orig') and os.path.normcase(path) == os.path.normcase(d.path or ''):
            backup = path + '.orig'

        def job(progress):
            if backup:
                shutil.copy2(path, backup)
            d.b.save(path)
            return path

        def done(p):
            d.path = p
            self.remember('last_bundle', p)
            self.changed(d)
            self.rebuild_types()
            self.status = f'Saved {p}.' + (f' The original is kept as {os.path.basename(backup)}.' if backup else '')

        self.run_job(f'Saving {os.path.basename(path)}...', job, done)

    def action_save_as(self, d):
        if d is None:
            return
        if getattr(d.b, 'kind', '') == 'sps':
            p = filedialog.save_file('Save sound stream as', d.path or '', filedialog.SPS, 'SPS')
            if p:
                self.action_save(d, p)
            return
        p = filedialog.save_file('Save bundle as', d.path or self.cfg.get('last_bundle', ''), filedialog.BUNDLES, 'BNDL')
        if p:
            self.action_save(d, p)

    def action_convert(self, d, target):
        if d is None:
            return
        if getattr(d.b, 'kind', '') == 'sps':
            self.status = 'Sound streams (.SPS) are the same on PC and PS3; nothing to convert.'
            return
        if d.b.platform == target:
            self.status = f'{d.name} is already a {target} bundle.'
            return
        base, ext = os.path.splitext(d.path or 'bundle.BNDL')
        p = filedialog.save_file(f'Save the {target} version as', f'{base}_{target}{ext or ".BNDL"}', filedialog.BUNDLES, 'BNDL')
        if not p:
            return
        types = self.types

        def job(progress):
            out, rep = convert.convert_bundle(d.b, target, types, progress=progress)
            out.save(p)
            return rep

        def done(rep):
            self.modal = {'kind': 'message', 'title': f'Converted to {target}',
                          'text': f'Saved {p}\n\n' + convert.report_text(rep), 'open': p}
            self.status = f'Converted {d.name} to {target}: {rep["converted"]} resources.'

        self.run_job(f'Converting to {target}...', job, done)

    def action_plate(self):
        d = self.cur
        root = ops.game_root(d.path) if d is not None and d.path else None
        if root is None:
            root = next((f for f in self.pinned() if os.path.isfile(os.path.join(f, ops.PLATE_BUNDLES[0]))), None)
        if root is None:
            root = filedialog.pick_folder('The game folder (PC)')
            if not root:
                return
        self.modal = {'kind': 'plate', 'root': root, 'state': ops.plate_editing_state(root)}

    def action_export_sps(self, src=None):
        """Every .SPS file of a folder (and its sub folders) as WAV files."""
        src = src or filedialog.pick_folder('Export the .SPS sound streams of this folder (and its sub folders)')
        if not src:
            return
        dst = filedialog.pick_folder('Write the WAV files into this folder')
        if not dst:
            return

        def loc(rid):
            return self.names.where.get(rid)

        def done(res):
            n, errors = res
            text = f'{n} sound stream(s) written as WAV to {dst}.'
            if errors:
                text += chr(10) * 2 + 'Not exported:' + chr(10) + chr(10).join(
                    f'{os.path.basename(p)}: {e}' for p, e in errors[:20])
            self.modal = {'kind': 'message', 'title': 'Export sound streams', 'text': text}
            self.status = text.split(chr(10))[0]

        self.run_job('Exporting sound streams...', lambda pr: ops.export_sps_folder(src, dst, loc, pr), done)

    def action_extract(self, d):
        if d is None:
            return
        folder = filedialog.pick_folder('Extract every resource into this folder')
        if not folder:
            return
        fmt = self.cfg.get('texture_ext', '.dds')
        self.run_job('Extracting...', lambda pr: ops.extract_all(d.b, folder, pr, fmt),
                     lambda n: setattr(self, 'status', f'Extracted {n} resources to {folder}.'))

    def action_import_folder(self, d):
        if d is None:
            return
        folder = filedialog.pick_folder('Import resources from this folder (.bres files, <id>.dds / .png / .txt / .csv)')
        if not folder:
            return
        d.checkpoint('import folder')
        types = self.types

        def done(res):
            changes, errors = res
            self.changed(d)
            self.rebuild_types()
            text = f'{len(changes)} resource(s) added or replaced.'
            if errors:
                text += '\n\nErrors:\n' + '\n'.join(f'{os.path.basename(p)}: {e}' for p, e in errors[:20])
            self.modal = {'kind': 'message', 'title': 'Import from folder', 'text': text}
            self.status = text.split('\n')[0]

        self.run_job('Importing...', lambda pr: ops.import_folder(d.b, folder, types, pr), done)

    # -- resource actions -------------------------------------------------------------------------------------
    def selected(self, d):
        if d is None:
            return []
        return [r for r in d.b.resources if r.id in d.sel]

    def action_delete(self, d, ids):
        if not ids:
            return
        d.checkpoint(f'delete {len(ids)}', list(ids))
        for rid in ids:
            d.b.remove(rid)
        d.sel.clear()
        if self.focus and self.focus[1] in ids:
            self.focus = None
        self.changed(d)
        self.rebuild_types()
        self.status = f'Deleted {len(ids)} resource(s) (Ctrl+Z to undo).'

    def action_export(self, d, res, kind='native'):
        if res is None:
            return
        base = os.path.join(os.path.dirname(self.cfg.get('last_export', '') or (d.path or '')), ops.id_text(res.id))
        try:
            if kind == 'bres':
                p = filedialog.save_file('Export resource', base + '.bres', filedialog.RES, 'bres')
                if p:
                    ops.export_bres(d.b, res, p)
            elif kind == 'dds':
                p = filedialog.save_file('Export texture as DDS', base + '.dds', filedialog.DDS, 'dds')
                if p:
                    ops.export_texture(d.b, res, p)
            elif kind == 'png':
                p = filedialog.save_file('Export texture as PNG', base + '.png', filedialog.PNG, 'png')
                if p:
                    if res.type == T_CUBE:
                        ops.save_png(ops.cube_strip(ops.cube_lut(d.b, res)), p)
                    else:
                        ops.export_texture(d.b, res, p, self.tex['mip'], self.tex['face'])
            elif kind == 'text':
                p = filedialog.save_file('Export text', base + '.txt', filedialog.TEXT, 'txt')
                if p:
                    ops.write_file(p, textfile.read(res, d.b.e))
            elif kind == 'csv' and res.type == VL.T_VEHICLELIST:
                p = filedialog.save_file('Export vehicle list as CSV', base + '.csv', filedialog.CSV, 'csv')
                if p:
                    ops.write_file(p, ops.vehicles_to_csv(d.b, res).encode('utf-8-sig'))
            elif kind == 'csv':
                p = filedialog.save_file('Export strings as CSV', base + '.csv', filedialog.CSV, 'csv')
                if p:
                    ops.write_file(p, ops.strings_to_csv(d.b, res).encode('utf-8-sig'))
            elif kind == 'glb':
                p = filedialog.save_file('Export model as glTF', base + '.glb', [('glTF binary (*.glb)', '*.glb')], 'glb')
                if p:
                    n = self.export_glb(d, res, p)
                    self.status = f'Exported {n} mesh(es) to {p}.'
            elif kind == 'wav':
                from . import eal3
                p = filedialog.save_file('Export sound as WAV', base + '.wav', filedialog.WAV, 'wav')
                if p:
                    audio, rate, _, _ = self.wave_audio(d, res)
                    ops.write_file(p, eal3.wav_bytes(audio, rate))
            elif kind.startswith('chunk'):
                k = int(kind[5:])
                p = filedialog.save_file(f'Export chunk {k}', f'{base}.chunk{k}.bin', filedialog.BIN, 'bin')
                if p:
                    ops.write_file(p, res.data(k))
            else:
                p = None
            if p:
                self.remember('last_export', p)
                self.status = f'Exported {res.id:#x} to {p}.'
        except Exception as e:
            traceback.print_exc()
            self.status = f'Export failed: {e}'

    def replace_from_file(self, d, res, path, options=None):
        """Replace resource data from a file (type decided by the file and the resource). Undoable."""
        options = options or {}
        ext = os.path.splitext(path)[1].lower()
        with open(path, 'rb') as f:
            data = f.read()
        d.checkpoint(f'replace {res.id:#x}', [res.id])
        try:
            if resfile.is_resfile(data):
                new = ops.import_bres(d.b, data, self.types)
                if new.id != res.id:
                    new.id = res.id
                d.b.add(new)
                msg = f'Replaced {res.id:#x} with {os.path.basename(path)}.'
            elif res.type == T_TEXTURE and (data[:4] == b'DDS ' or ext in ops.IMAGE_EXT):
                inf = ops.replace_texture(d.b, res, data=data if data[:4] == b'DDS ' else None,
                                          path=path, fmt=options.get('fmt'), mips=options.get('mips'),
                                          srgb=options.get('srgb'))
                msg = f'Texture {res.id:#x} replaced: {inf.describe()}.'
            elif res.type == T_CUBE and ext in ops.IMAGE_EXT:
                ops.replace_cube(d.b, res, path)
                msg = f'Colour cube {res.id:#x} replaced.'
            elif res.type == T_TEXT and ext in ('.txt', '.json', '.xml', '.lua'):
                ops.replace_text(d.b, res, data)
                msg = f'Text {res.id:#x} replaced ({len(data)} bytes).'
            elif res.type == T_STRINGS and ext == '.csv':
                ch, add, unk = ops.strings_from_csv(d.b, res, data.decode('utf-8-sig'), options.get('add_new', False))
                msg = f'{ch} string(s) changed, {add} added' + (f', {len(unk)} unknown id(s) skipped' if unk else '') + '.'
            elif res.type == VL.T_VEHICLELIST and ext == '.csv':
                nv, nm = ops.vehicles_from_csv(d.b, res, data.decode('utf-8-sig'))
                self.vlist['key'] = None
                msg = f'Vehicle list {res.id:#x} replaced: {nv} vehicles, {nm} manufacturers.'
            elif res.type == 0x81 and ext in eal3.AUDIO_EXT and getattr(d.b, 'prefix', b''):
                d.undo.pop()
                self.modal = {'kind': 'message', 'title': 'Replace sound',
                              'text': f'{d.name} continues a sound whose start is in '
                                      f'{os.path.basename(d.b.owner[0])}. Open that bundle and replace the sound '
                                      f'there: both the bundle and this file are rewritten.'}
                return False
            elif res.type == 0x81 and ext in eal3.AUDIO_EXT:
                desc = ops.replace_wave(d.b, res, path=path, rate=options.get('rate'), channels=options.get('channels'),
                                        quality=options.get('quality', 0.2), bundle_path=d.path)
                msg = f'Sound {res.id:#x} replaced: {desc}.'
            elif ext == '.bin' and 'chunk' in options:
                res.set_data(options['chunk'], data)
                msg = f'Chunk {options["chunk"]} of {res.id:#x} replaced ({len(data)} bytes).'
            else:
                d.undo.pop()
                self.status = f'{os.path.basename(path)} does not fit a {type_name(res.type)} resource.'
                return False
        except Exception as e:
            traceback.print_exc()
            d.do_undo()
            d.redo.clear()
            self.status = f'Replace failed: {e}'
            self.modal = {'kind': 'message', 'title': 'Replace failed', 'text': str(e)}
            return False
        self.changed(d, d.b.find(res.id))
        self.status = msg + ' (Ctrl+Z to undo)'
        return True

    def action_replace(self, d, res):
        if res is None:
            return
        flt = {T_TEXTURE: filedialog.IMAGES, T_CUBE: filedialog.PNG, T_TEXT: filedialog.TEXT,
               T_STRINGS: filedialog.CSV, 0x81: filedialog.AUDIO, VL.T_VEHICLELIST: filedialog.CSV}.get(res.type, filedialog.RES)
        p = filedialog.open_file(f'Replace {type_name(res.type)} {res.id:#x}', self.cfg.get('last_import', ''), flt)
        if not p:
            return
        self.remember('last_import', p)
        if res.type == T_TEXTURE:
            inf = raster.info(res, d.b.platform)
            self.modal = {'kind': 'texture_options', 'doc': d.uid, 'rid': res.id, 'path': p,
                          'fmt': 0, 'mips': 0, 'srgb': inf.srgb}
        elif res.type == 0x81 and os.path.splitext(p)[1].lower() in eal3.AUDIO_EXT:
            try:
                _, rate, head, _ = self.wave_audio(d, res)
            except Exception:
                rate, head = None, None
            try:
                src, src_rate = eal3.read_audio(p)
                src_info = f'{src.shape[1]} channel(s), {src_rate} Hz, {len(src) / src_rate:.2f} s'
            except Exception as e:
                self.status = f'Cannot read {os.path.basename(p)}: {e}'
                return
            self.modal = {'kind': 'wave_options', 'doc': d.uid, 'rid': res.id, 'path': p, 'src': src_info,
                          'old': (f'{head["channels"]} channel(s), {rate} Hz' if head else 'unknown'),
                          'rate': 0, 'channels': 0, 'quality': 0.2}
        else:
            self.replace_from_file(d, res, p)

    def action_import_resources(self, d, paths=None):
        if d is None:
            return
        if paths is None:
            paths = filedialog.open_files('Import resources (.bres)', self.cfg.get('last_import', ''), filedialog.RES)
        if not paths:
            return
        news = []
        for p in paths:
            try:
                with open(p, 'rb') as f:
                    news.append(ops.import_bres(d.b, f.read(), self.types))
            except Exception as e:
                self.status = f'{os.path.basename(p)}: {e}'
                return
        self.add_resources(d, news, f'import {len(news)} file(s)')

    def add_resources(self, d, news, desc):
        existing = [r.id for r in news if d.b.find(r.id) is not None]
        if existing and not getattr(self, '_confirmed_replace', False):
            self.modal = {'kind': 'confirm_replace', 'doc': d.uid, 'news': news, 'desc': desc, 'n': len(existing)}
            return
        self._confirmed_replace = False
        d.checkpoint(desc, [r.id for r in news])
        for r in news:
            d.b.add(r)
        d.sel = {r.id for r in news}
        self.changed(d)
        if any(r.type == T_GTYPE for r in news):
            self.rebuild_types()
        self.status = f'{len(news)} resource(s) added to {d.name} ({len(existing)} replaced). Ctrl+Z to undo.'

    def copy_between(self, src, dst, ids):
        news, failed = [], []
        for rid in ids:
            r = src.b.find(rid)
            if r is None:
                continue
            try:
                news.append(ops.adopt(dst.b, r, src.b.platform, self.types))
            except Exception as e:
                failed.append(f'{rid:#x} {type_name(r.type)}: {e}')
        if failed:
            self.modal = {'kind': 'message', 'title': 'Some resources were not copied',
                          'text': f'{len(news)} resource(s) can be copied; {len(failed)} cannot:\n\n' + '\n'.join(failed[:20])}
        if news:
            self.add_resources(dst, news, f'copy {len(news)} from {src.name}')

    def action_change_id(self, d, res, new_id, duplicate=False):
        if d.b.find(new_id) is not None:
            self.status = f'{new_id:#x} already exists in {d.name}.'
            return
        if duplicate:
            c = res.copy()
            c.id = new_id
            c.modified = True
            d.checkpoint(f'duplicate {res.id:#x}', [new_id])
            d.b.add(c)
            self.status = f'Duplicated {res.id:#x} as {new_id:#x}.'
        else:
            d.checkpoint(f'change id {res.id:#x}')
            n = d.b.change_id(res.id, new_id)
            self.status = f'{res.id:#x} is now {new_id:#x}; {n} import(s) in this bundle updated.'
        d.sel = {new_id}
        self.focus = (d.uid, new_id)
        self.changed(d)
        self.rebuild_types()

    def find_users(self, rid):
        out = []
        for d in self.docs:
            for r in d.b.resources:
                if r.missing or not r.import_count:
                    continue
                for imp in r.imports():
                    if imp.id == rid:
                        out.append((d.uid, r.id, imp.offset))
        self.results = {'title': f'Resources that import {rid:#x}', 'items': out}

    def find_everywhere(self, text):
        t = text.strip().lower()
        out = []
        try:
            rid = parse_id(t)
        except ValueError:
            rid = None
        for d in self.docs:
            for r in d.b.resources:
                hit = (rid is not None and r.id == rid) or (t and (t in r.name.lower() or t in f'{r.id:016x}'))
                if hit:
                    out.append((d.uid, r.id, None))
        self.results = {'title': f'Search: {text}', 'items': out}

    # -- drag and drop ----------------------------------------------------------------------------------------
    def on_drop(self, paths):
        with self.drop_lock:
            self.dropped.extend(paths)

    def cursor_client(self):
        import ctypes
        from ctypes import wintypes
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        if self.hwnd:
            ctypes.windll.user32.ScreenToClient(ctypes.c_void_p(self.hwnd), ctypes.byref(pt))
        return pt.x, pt.y

    def drop_target(self):
        """(doc, resource or None, area) under the mouse when files were dropped (or pasted)."""
        if self._paste_target is not None:
            d, r = self._paste_target
            self._paste_target = None
            return d, r, ('details' if r is not None else 'list')
        x, y = self.cursor_client()

        def inside(rect):
            return rect is not None and rect[0] <= x < rect[2] and rect[1] <= y < rect[3]

        if inside(self.details_rect):
            d, r = self.focused()
            if r is not None:
                return d, r, 'details'
        if inside(self.list_rect) and self.cur is not None:
            for rect in self.row_rects:
                y0, y1, rid = rect[:3]
                if y0 <= y < y1 and (len(rect) < 5 or rect[3] <= x < rect[4]):
                    return self.cur, self.cur.b.find(rid), 'row'
            return self.cur, None, 'list'
        return self.cur, None, 'window'

    def pump_drops(self):
        with self.drop_lock:
            paths, self.dropped = self.dropped, []
        if not paths:
            return
        d, target, area = self.drop_target()
        bundles, others = [], []
        for p in paths:
            try:
                with open(p, 'rb') as f:
                    magic = f.read(8)
            except OSError:
                continue
            (bundles if magic[:4] == b'bnd2' else others).append((p, magic))
        # .SPS files open as sound streams unless one is dropped on a Wave (then it replaces that sound)
        on_wave = target is not None and target.type == 0x81 and area in ('details', 'row') and len(others) == 1
        if not on_wave:
            for p, _ in [x for x in others if spsfile.is_sps_file(x[0])]:
                bundles.append((p, None))
            others = [x for x in others if not spsfile.is_sps_file(x[0])]
        for p, _ in bundles:
            self.open_path(p)
        if not others:
            return
        if d is None:
            self.status = 'Open a bundle first, then drop resource files on it.'
            return
        if target is not None and len(others) == 1:
            p, magic = others[0]
            if magic == resfile.MAGIC and area == 'list':
                self.action_import_resources(d, [p])
                return
            if os.path.splitext(p)[1].lower() == '.bin':
                self.modal = {'kind': 'pick_chunk', 'doc': d.uid, 'rid': target.id, 'path': p}
                return
            self.focus = (d.uid, target.id)
            d.sel = {target.id}
            self.replace_from_file(d, target, p)
            return
        bres = [p for p, m in others if m == resfile.MAGIC]
        if bres:
            self.action_import_resources(d, bres)
        rest = [p for p, m in others if m != resfile.MAGIC]
        matched = 0
        for p in rest:
            rid = ops.id_from_filename(p)
            r = d.b.find(rid) if rid is not None else None
            if r is not None and self.replace_from_file(d, r, p):
                matched += 1
        if rest and not matched:
            self.status = ('Drop a file on a resource (row or preview) to replace it, or name it <resource id>.<ext> '
                           'and drop it on the list.')

    def drag_out(self):
        """Our resource drag left the window: hand the files to Explorer with an OLE drag."""
        if self.drag is None or self.drag_out_done or not self.hwnd or not dragdrop.cursor_outside(self.hwnd):
            return
        self.drag_out_done = True
        d = self.doc_by_uid(self.drag[0])
        if d is None:
            return
        folder = tempfile.mkdtemp(prefix='bndlx_')
        paths = []
        ext = self.cfg.get('drag_texture_ext', '.png')
        for rid in self.drag[1]:
            r = d.b.find(rid)
            if r is None or r.missing:
                continue
            try:
                paths.append(ops.export_native(d.b, r, folder, ext))
            except Exception as e:
                traceback.print_exc()
                self.status = f'{rid:#x}: {e}'
        if paths:
            ok = dragdrop.drag_files_out(paths)
            self.status = f'Dragged {len(paths)} file(s) out.' if ok else 'Drag cancelled.'
        io = imgui.get_io()
        io.add_mouse_button_event(0, False)
        try:
            imgui.internal.clear_drag_drop()
        except Exception:
            pass
        self.drag = None

    # -- GUI: frame -------------------------------------------------------------------------------------------
    def post_init(self):
        try:
            addr = hello_imgui.get_glfw_window_address()
            dragdrop.install_drop(addr, self.on_drop)
            self.hwnd = dragdrop.window_handle(addr)
        except Exception:
            traceback.print_exc()

    def exit_guard(self):
        p = hello_imgui.get_runner_params()
        st_dirty = self.st_ui is not None and self.st_ui['model'].dirty
        if p.app_shall_exit and not self.exit_ok and (any(d.modified for d in self.docs) or st_dirty):
            p.app_shall_exit = False
            try:
                lib = dragdrop._glfw()
                lib.glfwSetWindowShouldClose(ctypes_ptr(hello_imgui.get_glfw_window_address()), 0)
            except Exception:
                pass
            if self.modal is None:
                self.modal = {'kind': 'confirm_exit'}

    # -- menus ------------------------------------------------------------------------------------------------
    # -- list panel -------------------------------------------------------------------------------------------
    def visible_rows(self, d):
        key = (d.filter, d.type_filter, d.sort, len(d.b.resources), id(d.b.resources))
        if d.rows is not None and d.rows_key == key:
            return d.rows
        t = d.filter.strip().lower()
        if t.startswith('0x'):
            t = t[2:]
        rows = []
        for r in d.b.resources:
            if d.type_filter is not None and r.type != d.type_filter:
                continue
            if t and not (t in f'{r.id:016x}' or t in self.display_name(d, r)[0].lower() or t in r.name.lower()
                          or t in type_name(r.type).lower() or t in self.summary(d, r).lower()):
                continue
            rows.append(r)
        col, asc = d.sort
        keyf = [lambda r: self.display_name(d, r)[0].lower(), lambda r: r.id, lambda r: (type_name(r.type), r.id),
                lambda r: self.summary(d, r).lower(), lambda r: r.size(0) + r.size(1) + r.size(2) + r.size(3),
                lambda r: r.import_count][min(col, 5)]
        rows.sort(key=keyf, reverse=not asc)
        d.rows, d.rows_key = rows, key
        return rows

    # -- names ------------------------------------------------------------------------------------------------
    def build_users(self, d):
        """Reverse import map of a bundle (who uses each resource), for derived names, plus the names that can
        be worked out inside the bundle; background thread."""
        d.users = None
        res = list(d.b.resources)
        first = not getattr(d, 'names_done', False)
        d.names_done = True

        def work():
            if first:
                try:
                    bn = N.bundle_names(d.b, self.types)
                    self.names.learn_names(bn)
                    if bn['car'] and d.path:
                        self.names.cars.setdefault(os.path.basename(d.path).upper(), bn['car'])
                except Exception:
                    traceback.print_exc()
            users = {}
            for r in res:
                if r.missing or not r.import_count:
                    continue
                try:
                    for imp in r.imports():
                        users.setdefault(imp.id, []).append((r.id, imp.offset))
                except Exception:
                    continue
            d.users = users
            d.dname.clear()
            d.rows = None

        threading.Thread(target=work, daemon=True).start()

    def display_name(self, d, r, depth=0):
        """(name, kind, tooltip); kind 'exact' (a real name), 'derived' (from how it is used) or 'id'."""
        hit = d.dname.get(r.id)
        if hit is not None:
            return hit
        out = self._display_name(d, r, depth)
        if depth == 0:
            d.dname[r.id] = out
        return out

    def _display_name(self, d, r, depth):
        full = self.names.exact.get(r.id)
        if full is None and r.name and not N.GC_RE.match(r.name):
            full = r.name
        if full is None and r.type == T_GTYPE:
            t = self.types.get(r.id)
            full = t.name if t is not None and t.name else None
        if full:
            m = N.QUAD_RE.match(full)
            if m and depth < 2:
                tex = d.b.find((0x01000000 << 32) | int(m.group(2)))
                if tex is not None:
                    tname_, kind, _ = self.display_name(d, tex, depth + 1)
                    flip = ' '.join(x for x in (m.group(3) and 'flipped H', m.group(4) and 'flipped V') if x)
                    return (f'{tname_} quad' + (f' ({flip})' if flip else ''), 'exact', full)
            return (N.short(full), 'exact', full)
        gc = N.gc_number(r.id)
        tn = type_name(r.type)
        stored = self.names.objnames.get(r.id)
        if stored and r.type != T_GOBJECT:
            return (stored, 'derived', f'{stored}\n(name inside the resource)')
        if r.type == T_GOBJECT:
            nm = stored
            if nm is None and not r.missing:
                try:
                    node = genesys.Reader(self.types, d.b.e).read_resource(r)
                    nm = N.object_name(node, self.types)
                except Exception:
                    nm = None
            if nm:
                return (nm, 'derived', f'{nm}\n(name field of the object)')
            s = self.summary(d, r)
            if s:
                return (f'{s} {gc}' if gc is not None else s, 'derived', f'{s} object (no name in the data)')
        if r.type == 0x106:
            car = self.doc_car(d)
            if car:
                return (f'{car} graphics spec', 'derived', f'{car} graphics spec\n(the car of this vehicle bundle)')
        if depth < 3 and d.users:
            users = d.users.get(r.id, [])
            for uid, off in users[:8]:
                u = d.b.find(uid)
                if u is None:
                    continue
                if u.type == 0x106 and r.type == 0x51:
                    car = self.doc_car(d) or 'vehicle'
                    role = N.vgs_role(off)
                    nm = f'{car} {role}'
                    return (nm, 'derived', f'{nm}\n(part of the VehicleGraphicsSpec)')
                un, kind, _ = self.display_name(d, u, depth + 1)
                if kind == 'id':
                    continue
                base = re.sub(r' \(\+\d+\)$', '', un)
                base = re.sub(r' (LOD\d|OCCLUSION)$', '', base)
                if u.type == 0x02:
                    base = re.sub(r' material$', '', base)
                if r.type == 0x05 and u.type == 0x51:
                    lod = self.model_lod(d, u, off)
                    nm = f'{base} LOD{lod}' if lod is not None else f'{base} renderable'
                elif r.type == T_TEXTURE and u.type == 0x02:
                    nm = f'{base} {self.material_slot(d, u, off)}'
                elif r.type == T_TEXTURE:
                    nm = f'{base} texture'
                else:
                    nm = f'{base} {tn.lower()}'
                more = len({x for x, _ in users}) - 1
                if more > 0:
                    nm += f' (+{more})'
                return (nm, 'derived', f'{nm}\n(used by {type_name(u.type)} {un}' + (f' and {more} more)' if more else ')'))
        car = self.doc_car(d)
        if car:
            nm = f'{car} {tn.lower()}' + (f' {gc}' if gc is not None else '')
            return (nm, 'derived', f'{nm}\n(resource of the {car} vehicle bundle)')
        return ((f'{tn} {gc}' if gc is not None else ops.id_text(r.id)), 'id', 'no name known')

    def doc_car(self, d):
        """Car name of a vehicle bundle (from its damage behaviour object), or None."""
        car = getattr(d, 'car', False)
        if car is not False:
            return car
        car = self.names.cars.get(os.path.basename(d.path or '').upper())
        if car is None and os.path.basename(d.path or '').upper().startswith('VEH_'):
            rd = genesys.Reader(self.types, d.b.e)
            for r in d.b.resources:
                if r.type != T_GOBJECT or r.missing:
                    continue
                try:
                    nm = N.object_name(rd.read_resource(r), self.types)
                except Exception:
                    continue
                if nm:
                    for suf in N.VEH_SUFFIXES:
                        if nm.endswith(suf):
                            car = nm[:-len(suf)].strip()
                            break
                if car:
                    break
        d.car = car
        return car

    def model_lod(self, d, m, off):
        """LOD index of a renderable import in a model's renderable table."""
        try:
            c = m.data(0)
            table = struct.unpack_from(d.b.e + 'I', c, 0)[0]
            k = (off - table) // 4
            return k if 0 <= k < c[0x14] else None
        except Exception:
            return None

    def material_slot(self, d, m, off):
        """Texture slot name of a material import offset (Diffuse, Normal, ...)."""
        try:
            c = m.data(0)
            e = d.b.e
            ntex = c[0x20]
            tp, sp, tip = struct.unpack_from(e + '3I', c, 0x24)
            k = (off - tip) // 4
            if 0 <= k < ntex:
                h = struct.unpack_from(e + 'H', c, tp + 2 * k)[0]
                return N.SLOTS.get(h, f'texture {k}')
        except Exception:
            pass
        return 'texture'

    def action_find_names(self):
        folders = list(self.pinned())
        for d in self.docs:
            if d.path:
                folders.append(os.path.dirname(d.path))
        roots = []
        for f in sorted(set(folders), key=len):
            if not any(f.lower().startswith(r.lower().rstrip('\\/') + os.sep) for r in roots):
                roots.append(f)
        if not roots:
            self.status = 'Add the game folder (and the PS3 prototype folder, if you have it) with "Add a folder" first.'
            return
        exes = []
        for r in roots:
            for n in ('NFS13.exe', 'EBOOT.BIN'):
                p = os.path.join(r, n)
                if os.path.exists(p):
                    exes.append(p)
        db = self.names

        def done(st):
            for d in self.docs:
                d.dname.clear()
                d.rows = None
            self.modal = {'kind': 'message', 'title': 'Resource names',
                          'text': f'Scanned {st["files"]} bundles in {st["seconds"]} s.\n\n'
                                  f'{st["resources"]} different resources, {st["named"]} of them named '
                                  f'({st["exact"]} exact names from the game data, the rest from object names).\n'
                                  'Other resources get names from what uses them (models, materials, vehicles).'}
            self.status = f'Names: {st["named"]} of {st["resources"]} resources.'

        label = ', '.join(os.path.basename(r.rstrip('\\/')) or r for r in roots)
        self.run_job(f'Finding names in {label}...', lambda pr: db.scan(roots, pr, exe_paths=exes), done)

    def summary(self, d, r):
        s = d.summary.get(r.id)
        if s is None:
            s = ops.text_summary(d.b, r, self.types)
            d.summary[r.id] = s
        return s

    def drop_on(self, d):
        """Accept resources dragged from another bundle onto the last item."""
        if imgui.begin_drag_drop_target():
            p = imgui.accept_drag_drop_payload_py_id(PAYLOAD)
            if p is not None and self.drag is not None:
                src = self.doc_by_uid(self.drag[0])
                if src is not None and src is not d:
                    self.copy_between(src, d, self.drag[1])
                    self.show_doc(d)
                self.drag = None
            imgui.end_drag_drop_target()

    def undo(self, d):
        desc = d.do_undo()
        if desc:
            self.changed(d)
            self.rebuild_types()
            self.status = f'Undone: {desc}.'

    def redo(self, d):
        desc = d.do_redo()
        if desc:
            self.changed(d)
            self.rebuild_types()
            self.status = f'Redone: {desc}.'

    # -- details panel ----------------------------------------------------------------------------------------
    def details_panel(self):
        d, r = self.focused()
        if r is None or d is not self.cur:
            if self.cur is not None:
                self.bundle_summary(self.cur)
            return
        ic, colr = theme.type_icon(r.type)
        if theme.FONTS['big_icons'] is not None:
            imgui.push_font(theme.FONTS['big_icons'], 30.0)
        theme.icon_text(ic, colr)
        if theme.FONTS['big_icons'] is not None:
            imgui.pop_font()
        imgui.same_line()
        imgui.begin_group()
        nm, kind, tip = self.display_name(d, r)
        imgui.text(nm)
        if imgui.is_item_hovered():
            imgui.set_tooltip(tip)
        imgui.text_disabled(f'{type_name(r.type)}  -  {ops.id_text(r.id)}  -  {human(sum(r.size(k) for k in range(4)))}')
        imgui.end_group()
        if r.missing:
            imgui.text_colored(imgui.ImVec4(1, 0.5, 0.3, 1), 'The data of this resource is not in the file (truncated file).')
            return
        tab = self.test_tab
        self.test_tab = None
        sel = [imgui.TabItemFlags_.set_selected if tab == i else 0 for i in range(4)]
        if imgui.begin_tab_bar('details'):
            if imgui.begin_tab_item_simple('Preview', sel[0]):
                imgui.begin_child('preview')
                try:
                    self.preview(d, r)
                except Exception as e:
                    traceback.print_exc()
                    imgui.text_colored(imgui.ImVec4(1, 0.4, 0.3, 1), f'Cannot show this resource: {e}')
                imgui.end_child()
                imgui.end_tab_item()
            if imgui.begin_tab_item_simple('Info', sel[1]):
                self.info_tab(d, r)
                imgui.end_tab_item()
            if imgui.begin_tab_item_simple(f'Imports ({r.import_count})###imports', sel[2]):
                self.imports_tab(d, r)
                imgui.end_tab_item()
            if imgui.begin_tab_item_simple('Hex', sel[3]):
                self.hex_tab(d, r)
                imgui.end_tab_item()
            imgui.end_tab_bar()

    def bundle_summary(self, d):
        b = d.b
        imgui.text(d.name)
        imgui.text_disabled(d.path or '')
        imgui.separator()
        if getattr(b, 'kind', '') == 'sps':
            imgui.text('Sound stream (.SPS): one sound, the same on PC and PS3.')
            if b.owner:
                imgui.text_disabled(f'Its first part is in {os.path.basename(b.owner[0])}.')
            return
        flags = ', '.join(n for bit, n in FLAG_NAMES if b.flags & bit) or 'none'
        imgui.text(f'Platform: {b.platform}    version {b.version}    {len(b.resources)} resources')
        imgui.text(f'Flags: {b.flags:#x} ({flags})')
        imgui.text(f'Root resource: {b.root_id:#x}' if b.root_id else 'Root resource: none')
        names = b.stream_names()
        if names:
            imgui.text('Streams: ' + ', '.join(names))
        if b.debug is not None:
            imgui.text('Debug data: resource names and types (ResourceStringTable)')
        counts = {}
        sizes = {}
        for r in b.resources:
            counts[r.type] = counts.get(r.type, 0) + 1
            sizes[r.type] = sizes.get(r.type, 0) + sum(r.size(k) for k in range(4))
        imgui.spacing()
        if imgui.begin_table('types', 3, imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v):
            imgui.table_setup_column('Type')
            imgui.table_setup_column('Count')
            imgui.table_setup_column('Size')
            imgui.table_headers_row()
            for t in sorted(counts, key=lambda t: -sizes[t]):
                imgui.table_next_row()
                imgui.table_next_column()
                if imgui.selectable(f'{type_name(t)}##bt{t}', False, imgui.SelectableFlags_.span_all_columns)[0]:
                    d.type_filter = t
                imgui.table_next_column()
                imgui.text(str(counts[t]))
                imgui.table_next_column()
                imgui.text(human(sizes[t]))
            imgui.end_table()
        imgui.spacing()
        imgui.text_disabled('Select a resource on the left to see and edit it.')

    def info_tab(self, d, r):
        b = d.b
        rows = [('Id', f'{r.id:016X}  ({r.id})'), ('Type', f'{type_name(r.type)}  ({r.type:#x})')]
        names = b.stream_names()
        rows.append(('Stream', f'{r.stream}' + (f' ({names[r.stream]})' if r.stream < len(names) else '')))
        if r.name or r.debug_type:
            rows.append(('Debug name', r.name))
            rows.append(('Debug type', r.debug_type))
        for k in range(4):
            if r.size(k):
                rows.append((f'Chunk {k}', f'{human(r.size(k))} ({r.size(k)} bytes), alignment bits {r.us_bits[k]}/{r.cs_bits[k]}'))
        rows.append(('Imports', f'{r.import_count} at {r.import_offset:#x}' if r.import_count else 'none'))
        rows.append(('Modified', 'yes' if r.modified else 'no'))
        if r.type == T_TEXTURE:
            try:
                rows.append(('Texture', raster.info(r, b.platform).describe()))
            except Exception as e:
                rows.append(('Texture', str(e)))
        if imgui.begin_table('info', 2, imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v):
            imgui.table_setup_column('k', imgui.TableColumnFlags_.width_fixed, 110)
            imgui.table_setup_column('v', imgui.TableColumnFlags_.width_stretch)
            for k, v in rows:
                imgui.table_next_row()
                imgui.table_next_column()
                imgui.text_disabled(k)
                imgui.table_next_column()
                imgui.text_wrapped(v)
            imgui.end_table()

    def where(self, rid):
        for d in self.docs:
            r = d.b.find(rid)
            if r is not None:
                return d, r
        return None, None

    def imports_tab(self, d, r):
        imps = r.imports()
        if not imps:
            imgui.text_disabled('This resource does not import other resources.')
            return
        flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.scroll_y | imgui.TableFlags_.resizable
        if imgui.begin_table('imps', 4, flags):
            imgui.table_setup_scroll_freeze(0, 1)
            imgui.table_setup_column('Offset', imgui.TableColumnFlags_.width_fixed, 70)
            imgui.table_setup_column('Id (Enter to change)', imgui.TableColumnFlags_.width_fixed, 170)
            imgui.table_setup_column('Resource', imgui.TableColumnFlags_.width_stretch)
            imgui.table_setup_column('', imgui.TableColumnFlags_.width_fixed, 40)
            imgui.table_headers_row()
            for i, imp in enumerate(imps):
                imgui.table_next_row()
                imgui.table_next_column()
                imgui.text(f'{imp.offset:#x}')
                imgui.table_next_column()
                imgui.set_next_item_width(-1)
                ch, txt = imgui.input_text(f'##imp{i}', f'{imp.id:016X}', imgui.InputTextFlags_.enter_returns_true
                                           | imgui.InputTextFlags_.chars_hexadecimal)
                if ch and not d.b.truncated:
                    try:
                        nid = parse_id(txt)
                        if nid != imp.id:
                            d.checkpoint(f'import of {r.id:#x}', [r.id])
                            r.set_import_id(i, nid)
                            self.changed(d, r)
                            self.status = f'Import {i} of {r.id:#x} now points to {nid:#x}.'
                    except ValueError as e:
                        self.status = str(e)
                imgui.table_next_column()
                od, orr = self.where(imp.id)
                if orr is not None:
                    where = '' if od is d else f'  in {od.name}'
                    imgui.text(f'{self.display_name(od, orr)[0]}  ({type_name(orr.type)}){where}')
                else:
                    paths = self.names.where.get(imp.id)
                    imgui.text_disabled(f'in {os.path.basename(paths[0])} (not open)' if paths else 'not in an open bundle')
                imgui.table_next_column()
                self.locate(imp.id, f'g{i}')
            imgui.end_table()

    def hex_tab(self, d, r):
        sizes = [r.size(k) for k in range(4)]
        for k in range(4):
            if k:
                imgui.same_line()
            if imgui.radio_button(f'Chunk {k} ({human(sizes[k])})', self.hex_chunk == k):
                self.hex_chunk = k
        data = r.data(self.hex_chunk)
        imgui.set_next_item_width(90)
        _, self.hex_patch[0] = imgui.input_text_with_hint('##hexoff', 'offset (hex)', self.hex_patch[0])
        imgui.same_line()
        imgui.set_next_item_width(-120)
        _, self.hex_patch[1] = imgui.input_text_with_hint('##hexbytes', 'new bytes (hex), e.g. 00 00 80 3F', self.hex_patch[1])
        imgui.same_line()
        if imgui.button('Write bytes') and not d.b.truncated:
            try:
                off = int(self.hex_patch[0].strip() or '0', 16)
                new = bytes.fromhex(self.hex_patch[1])
                if off + len(new) > len(data):
                    raise ValueError('past the end of the chunk')
                d.checkpoint(f'hex edit {r.id:#x}', [r.id])
                buf = bytearray(data)
                buf[off:off + len(new)] = new
                r.set_data(self.hex_chunk, buf)
                self.changed(d, r)
                self.status = f'Wrote {len(new)} byte(s) at {off:#x} (Ctrl+Z to undo).'
                data = r.data(self.hex_chunk)
            except ValueError as e:
                self.status = f'Hex edit: {e}'
        imps = {imp.offset for imp in r.imports()} if self.hex_chunk == 0 else set()
        imp_bytes = set()
        for o in imps:
            imp_bytes.update(range(o, o + 4))
        imgui.begin_child('hex', imgui.ImVec2(0, 0), imgui.ChildFlags_.borders)
        if theme.FONTS['mono'] is not None:
            imgui.push_font(theme.FONTS['mono'], 0.0)
        n = (len(data) + 15) // 16
        clipper = imgui.ListClipper()
        clipper.begin(n)
        while clipper.step():
            for line in range(clipper.display_start, clipper.display_end):
                o = line * 16
                chunk = data[o:o + 16]
                hexs = ' '.join(f'{c:02x}' for c in chunk)
                asc = ''.join(chr(c) if 32 <= c < 127 else '.' for c in chunk)
                if imp_bytes & set(range(o, o + 16)):
                    imgui.text_colored(imgui.ImVec4(0.45, 0.8, 1.0, 1.0), f'{o:08x}  {hexs:<47}  {asc}')
                else:
                    imgui.text(f'{o:08x}  {hexs:<47}  {asc}')
        if theme.FONTS['mono'] is not None:
            imgui.pop_font()
        imgui.end_child()

    # -- previews ---------------------------------------------------------------------------------------------
    def preview(self, d, r):
        t = r.type
        if t == T_TEXTURE:
            self.texture_preview(d, r)
        elif t == T_GOBJECT:
            self.object_view(d, r)
        elif t == T_GTYPE:
            self.type_view(d, r)
        elif t == T_TEXT:
            self.text_view(d, r)
        elif t == T_STRINGS:
            self.strings_view(d, r)
        elif t == T_CUBE:
            self.cube_view(d, r)
        elif t == 0x81:
            self.wave_view(d, r)
        elif t in (0x05, 0x51, 0x50, 0x60, 0x106):
            self.model_view(d, r)
        elif t == 0x02:
            self.material_view(d, r)
        elif t == VL.T_VEHICLELIST:
            self.vehicle_list_view(d, r)
        elif t == ZL.T_ZONELIST:
            self.zone_map_view(d, r)
        else:
            imgui.text_wrapped(f'{type_name(t)}: no viewer for this type yet. The Imports and Hex tabs show its data; '
                               'Export / Replace work for every type (.bres or raw chunks).')
            if r.import_count:
                imgui.spacing()
                imgui.text('Uses:')
                for imp in r.imports()[:64]:
                    od, orr = self.where(imp.id)
                    lbl = f'{self.display_name(od, orr)[0]}  ({type_name(orr.type)})' if orr else f'{imp.id:016X} (not open)'
                    if imgui.selectable(f'{lbl}##u{imp.offset}', False)[0] and orr is not None:
                        self.goto(imp.id, od)

    def texture_preview(self, d, r):
        b = d.b
        inf = raster.info(r, b.platform)
        imgui.text(inf.describe())
        if inf.mips > 1:
            imgui.set_next_item_width(150)
            ch, m = imgui.slider_int('mip', self.tex['mip'], 0, inf.mips - 1)
            if ch:
                self.tex['mip'] = m
        else:
            self.tex['mip'] = 0
        if inf.faces > 1:
            imgui.same_line()
            imgui.set_next_item_width(150)
            ch, f = imgui.slider_int('face', self.tex['face'], 0, inf.faces - 1)
            if ch:
                self.tex['face'] = f
        else:
            self.tex['face'] = 0
        imgui.same_line()
        ch, srgb = imgui.checkbox('sRGB', inf.srgb)
        if ch and not b.truncated:
            d.checkpoint(f'sRGB flag {r.id:#x}', [r.id])
            h = bytearray(r.data(0))
            if b.platform == 'PC':
                v = struct.unpack_from('<I', h, 0x20)[0]
                struct.pack_into('<I', h, 0x20, (v | raster.PC_FLAG_SRGB) if srgb else (v & ~raster.PC_FLAG_SRGB))
            else:
                v = struct.unpack_from('>I', h, 0x20)[0]
                struct.pack_into('>I', h, 0x20, (v | 0x20) if srgb else (v & ~0x20))
            r.set_data(0, h)
            self.changed(d, r)
        if imgui.is_item_hovered():
            imgui.set_tooltip('Colour data sampled as sRGB (off for normal maps, masks and other data textures)')
        key = (d.uid, r.id, id(r.data(0)), id(r.data(b.gfx_chunk)), self.tex['mip'], self.tex['face'])
        if self.tex['key'] != key:
            self.tex['key'] = key
            self.tex['img'] = None
            self.tex['err'] = None
            self.tex['new'] = True

            def work(key=key):
                try:
                    img = raster.decode(r, b.platform, self.tex['mip'], self.tex['face'])
                    img = np.ascontiguousarray(img)
                    with self.tex_lock:
                        if self.tex['key'] == key:
                            self.tex['img'] = img
                except Exception as e:
                    with self.tex_lock:
                        self.tex['err'] = str(e)

            th = threading.Thread(target=work, daemon=True)
            self.tex['thread'] = th
            th.start()
        with self.tex_lock:
            img = self.tex['img']
            err = self.tex['err']
        if err:
            imgui.text_colored(imgui.ImVec4(1, 0.4, 0.3, 1), f'Cannot decode: {err}')
            return
        if img is None:
            imgui.text_disabled('decoding...')
            return
        # viewer toolbar: fit / 1:1 / channels
        h, w = img.shape[:2]
        imgui.push_style_var(imgui.StyleVar_.frame_padding, imgui.ImVec2(8, 3))
        zoom_cmd = None
        if imgui.button(f'{theme.I.ICON_FA_EXPAND if hasattr(theme.I, "ICON_FA_EXPAND") else theme.I.ICON_FA_MAGNIFYING_GLASS}  Fit'):
            zoom_cmd = 'fit'
        imgui.same_line(0, 2)
        if imgui.button('1:1'):
            zoom_cmd = 'one'
        imgui.same_line(0, 12)
        chan = self.tex.get('channel', -1)
        for c, lbl in ((-1, 'RGBA'), (0, 'R'), (1, 'G'), (2, 'B'), (3, 'A')):
            on = chan == c
            if on:
                imgui.push_style_color(imgui.Col_.button, imgui.get_style_color_vec4(imgui.Col_.header))
            if imgui.button(f'{lbl}##ch{c}'):
                self.tex['channel'] = c
                zoom_cmd = zoom_cmd or 'keep'
            if on:
                imgui.pop_style_color()
            imgui.same_line(0, 2)
        imgui.new_line()
        imgui.pop_style_var()
        avail = imgui.get_content_region_avail()
        dw, dh = max(64, int(avail.x) - 8), max(64, int(avail.y) - 30)
        scale = min(dw / w, dh / h, 8.0)
        size = (max(1, int(w * scale)), max(1, int(h * scale)))
        p = self.tex.get('params')
        if p is None or self.tex.get('new') or tuple(p.image_display_size) != size:
            p = immvision.ImageParams()
            p.image_display_size = size
            p.show_options_button = False
            p.show_zoom_buttons = False
            p.show_pixel_info = False
            p.show_image_info = False
            p.show_school_paper_background = False
            p.zoom_key = 'tex'
            p.refresh_image = True
            self.tex['params'] = p
            self.tex['new'] = False
        else:
            p.refresh_image = False
        p.selected_channel = self.tex.get('channel', -1)
        if zoom_cmd == 'fit':
            p.zoom_pan_matrix = immvision.make_zoom_pan_matrix_full_view((w, h), size)
        elif zoom_cmd == 'one':
            p.zoom_pan_matrix = immvision.make_zoom_pan_matrix_scale_one((w, h), size)
        if zoom_cmd == 'keep':
            p.refresh_image = True
        immvision.image(f'##tex{r.id}', img, p)
        mi = p.mouse_info
        if mi.is_mouse_hovering:
            x, y = int(mi.mouse_position[0]), int(mi.mouse_position[1])
            if 0 <= x < w and 0 <= y < h:
                px = img[y, x]
                vals = '  '.join(f'{c} {int(v)}' for c, v in zip('RGBA', px))
                imgui.text(f'x {x}, y {y}:  {vals}')
            else:
                imgui.text_disabled(' ')
        else:
            imgui.text_disabled('Mouse wheel: zoom, drag: pan')

    def wave_audio(self, d, r):
        """(int16 audio, rate, header, stream file) of a Wave resource, cached (decodes on this thread)."""
        key = ('wave', d.uid, r.id, id(r.data(0)))
        hit = self.gcache.get(key)
        if hit is None or isinstance(hit, Exception):
            hit = ops.wave_audio(d.b, r, d.path)
            self.gcache[key] = hit
        return hit

    def wave_view(self, d, r):
        if getattr(d.b, 'kind', '') == 'sps':
            if d.b.owner:
                imgui.text_disabled(f'Sound stream file; its first part is in {os.path.basename(d.b.owner[0])}.')
                imgui.same_line()
                if imgui.small_button('Open that bundle'):
                    nd = self.open_path(d.b.owner[0])
                    if nd is not None:
                        self.goto(d.b.owner[1], nd)
            elif d.b.headerless:
                imgui.text_wrapped('This file continues a sound whose start is stored in a bundle, which was not '
                                   'found. Run Find names (... menu), then open the file again.')
            else:
                imgui.text_disabled('Sound stream file (.SPS): Replace or drop an audio file, then Save (Ctrl+S).')
        key = ('wave', d.uid, r.id, id(r.data(0)))
        hit = self.gcache.get(key)
        if hit is None:
            self.gcache[key] = 'decoding'

            def work():
                try:
                    res = ops.wave_audio(d.b, r, d.path)
                    audio = res[0]
                    cols = 1024
                    mono = audio.astype(np.float32).mean(1) / 32768.0
                    parts = np.array_split(mono, cols) if len(mono) >= cols else [mono]
                    wave = (np.array([p.min() if len(p) else 0 for p in parts]),
                            np.array([p.max() if len(p) else 0 for p in parts]))
                    self.gcache[('wavegfx',) + key[1:]] = wave
                    self.gcache[key] = res
                except Exception as e:
                    self.gcache[key] = e

            threading.Thread(target=work, daemon=True).start()
            hit = 'decoding'
        if isinstance(hit, str):
            imgui.text_disabled('decoding...')
            return
        if isinstance(hit, Exception):
            imgui.text_wrapped(str(hit))
            return
        audio, rate, head, stream = hit
        n, ch = audio.shape
        imgui.text(f'EALayer3, {ch} channel(s), {rate} Hz, {n / max(rate, 1):.2f} s ({n} samples)'
                   + (', looped' if head['loop'] else ''))
        if stream:
            imgui.text_disabled(f'Stream file: {stream}')
        elif head.get('prefetch_only'):
            imgui.text_colored(imgui.ImVec4(1, 0.7, 0.3, 1), 'Only the start of this streamed sound is in the bundle; '
                               f'its {r.id & 0xFFFFFFFF}.SPS file was not found in the game folder.')
        pl = self.player
        mine = pl.opened and pl.key == key
        if mine and pl.done:                 # played to the end: back to the start
            pl.stop()
            self.wave_cursor[key] = 0
            mine = False
        pos = pl.position() if mine else self.wave_cursor.get(key, 0)
        if mine and not pl.paused:
            label = f'{theme.I.ICON_FA_PAUSE}  Pause'
        elif mine:
            label = f'{theme.I.ICON_FA_PLAY}  Resume'
        else:
            label = f'{theme.I.ICON_FA_PLAY}  Play'
        if imgui.button(label + '##playbtn'):
            try:
                if mine and not pl.paused:
                    pl.pause()
                elif mine:
                    pl.resume()
                else:
                    pl.play(audio, rate, self.wave_cursor.get(key, 0), key)
            except OSError as ex:
                self.status = str(ex)
        imgui.same_line()
        if imgui.button(f'{theme.I.ICON_FA_STOP}  Stop'):
            if mine:
                pl.stop()
            self.wave_cursor[key] = 0
            pos = 0
        imgui.same_line()
        imgui.text(f'{_clock(pos / max(rate, 1))} / {_clock(n / max(rate, 1))}')
        imgui.same_line()
        if imgui.button('Export WAV...'):
            self.action_export(d, r, 'wav')
        imgui.same_line()
        if imgui.button('Replace...') and not d.b.truncated:
            self.action_replace(d, r)
        lo, hi = self.gcache.get(('wavegfx',) + key[1:], (np.zeros(1), np.zeros(1)))
        avail = imgui.get_content_region_avail()
        w, h = max(100, int(avail.x) - 8), 140
        p0 = imgui.get_cursor_screen_pos()
        imgui.invisible_button('##wave', imgui.ImVec2(w, h))
        self.wave_rect = (p0.x, p0.y, w, h)
        hovered, active = imgui.is_item_hovered(), imgui.is_item_active()
        released = imgui.is_item_deactivated()
        mx = min(max(imgui.get_io().mouse_pos.x - p0.x, 0.0), float(w))
        target = int(mx / w * n)
        dl = imgui.get_window_draw_list()
        col = imgui.get_color_u32(imgui.Col_.plot_lines)
        played = imgui.get_color_u32(imgui.Col_.plot_lines_hovered)
        head_x = int(pos / max(n, 1) * w)
        mid = p0.y + h / 2
        m = len(lo)
        for x in range(w):
            i = min(m - 1, x * m // w)
            dl.add_line(imgui.ImVec2(p0.x + x, mid - hi[i] * h / 2), imgui.ImVec2(p0.x + x, mid - lo[i] * h / 2 + 1),
                        played if x < head_x else col)
        accent = imgui.get_color_u32(imgui.Col_.check_mark)
        dl.add_line(imgui.ImVec2(p0.x + head_x, p0.y), imgui.ImVec2(p0.x + head_x, p0.y + h), accent, 2.0)
        if active:                           # dragging: show where it will jump to
            dl.add_line(imgui.ImVec2(p0.x + mx, p0.y), imgui.ImVec2(p0.x + mx, p0.y + h), accent, 1.0)
        if hovered or active:
            imgui.set_tooltip(f'{_clock(target / max(rate, 1))}  (click to jump here)')
        if released:
            self.wave_seek(key, audio, rate, target)
        imgui.text_disabled('Click or drag on the waveform to move the play position.')

    def wave_seek(self, key, audio, rate, target):
        """Move the play position of a sound; if it is playing (or paused) it continues from there."""
        pl = self.player
        self.wave_cursor[key] = target
        if pl.opened and pl.key == key:
            was_paused = pl.paused
            try:
                pl.play(audio, rate, target, key)
                if was_paused:
                    pl.pause()
            except OSError as ex:
                self.status = str(ex)

    def audio_guard(self):
        """Stop the sound when its item is no longer the one shown (another item, bundle or tab was selected, or
        the sound was changed)."""
        if not self.player.opened:
            return
        if self.player.key and self.player.key[0] == 'song':      # a soundtrack editor preview
            if self.st_ui is None:
                self.player.stop()
            return
        d, r = self.focused()
        shown = ('wave', d.uid, r.id, id(r.data(0))) if d is not None and r is not None and r.type == 0x81 else None
        if shown != self.player.key or not self.cfg.get('preview', True):
            self.player.stop()

    # -- 3D models ---------------------------------------------------------------------------------------------
    def model_bundles(self, d):
        return [d.b] + [o.b for o in self.docs if o is not d]

    def texture_image(self, d, tid, max_size=1024):
        """RGBA array of a texture found in the open bundles or the game's global bundles (None if missing)."""
        if not tid:
            return None
        b, r = self.mesh_lib.find(tid, self.model_bundles(d), mesh.game_root(d.path))
        if r is None or r.type != T_TEXTURE:
            return None
        try:
            inf = raster.info(r, b.platform)
            mip = 0
            while mip + 1 < inf.mips and max(inf.w >> mip, inf.h >> mip) > max_size:
                mip += 1
            return raster.decode(r, b.platform, mip)
        except Exception:
            return None

    def model_view(self, d, r):
        st = self.model
        show = st['show'] if r.type == mesh.T_INSTANCELIST else 0
        nb = st['nb'] and r.type == mesh.T_INSTANCELIST
        key = (d.uid, r.id, id(r.data(0)), st['lod'], show, nb)
        if st['key'] != key:
            st['key'] = key
            st['result'] = None

            def work(key=key, lod=st['lod'], show=show, nb=nb):
                try:
                    if r.type == mesh.T_INSTANCELIST:
                        meshes, size = [], 128 if nb else 256        # whole track units: small textures
                        units = [(d.b, r, d.path)]
                        if nb:
                            for p in ZL.neighbour_paths(d.path):
                                self.model['progress'] = f'loading {os.path.basename(p)}...'
                                ub = self.mesh_lib.open_extra(p)
                                ur = next((x for x in ub.resources if x.type == mesh.T_INSTANCELIST), None) if ub else None
                                if ur is not None:
                                    units.append((ub, ur, p))
                        stats = {'instances': 0, 'shown': 0, 'models': 0, 'missing': [], 'kinds': {}, 'units': len(units)}
                        csum = {'soups': 0, 'polygons': 0, 'tags': {}}
                        for ub, ur, up in units:
                            self.model['progress'] = f'decoding {os.path.basename(up)}...'
                            if show in (0, 2):
                                ms, s1 = mesh.decode_instances(ub, ur, self.mesh_lib, self.model_bundles(d)[1:], up, lod)
                                meshes += ms
                                for k in ('instances', 'shown', 'models'):
                                    stats[k] += s1[k]
                                stats['missing'] += s1['missing']
                                for k, v in s1['kinds'].items():
                                    stats['kinds'][k] = stats['kinds'].get(k, 0) + v
                            if show in (1, 2):
                                soup = next((x for x in ub.resources if x.type == mesh.T_POLYSOUP), None)
                                if soup is None:
                                    continue
                                cm, cst = mesh.decode_polysoup(ub, soup)
                                for m in cm:
                                    m.wire = show == 2
                                meshes += cm
                                csum['soups'] += cst['soups']
                                csum['polygons'] += cst['polygons']
                                for t, n in cst['tags'].items():
                                    csum['tags'][t] = csum['tags'].get(t, 0) + n
                        if show in (1, 2):
                            stats['collision'] = csum
                        if not meshes:
                            raise mesh.MeshError('nothing to show')
                        nlod = 4
                    elif r.type == mesh.T_POLYSOUP:
                        meshes, cst = mesh.decode_polysoup(d.b, r)
                        stats, nlod, size = {'collision': cst}, 1, 256
                    elif r.type == mesh.T_VGS:
                        meshes, vst = mesh.decode_vgs(d.b, r, self.mesh_lib, self.model_bundles(d)[1:], d.path, lod)
                        stats, nlod, size = {'car': vst}, 4, 1024
                    else:
                        meshes, nlod = mesh.decode_resource(d.b, r, self.mesh_lib, self.model_bundles(d)[1:], d.path, lod)
                        stats, size = None, 1024
                    self.model['progress'] = 'loading the textures...'
                    texs = {t: self.texture_image(d, t, size) for t in {m.texture for m in meshes if m.texture}}
                    res = ('ok', meshes, texs, nlod, stats)
                except Exception as e:
                    res = ('error', str(e))
                if self.model['key'] == key:
                    self.model['result'] = res

            threading.Thread(target=work, daemon=True).start()
        res = st['result']
        prev = st.get('prev')
        if res is None and prev is not None and prev[0][:2] == key[:2] and prev[0][3:] == key[3:]:
            res = prev[1]                    # the same object is being re-decoded after an edit: keep showing it
        if res is None:
            imgui.text_disabled(st.get('progress') or 'loading the geometry...')
            return
        if res[0] == 'ok' and res is st['result']:
            st['prev'] = (key, res)
        if res[0] == 'error':
            imgui.text_wrapped(f'Cannot show this model: {res[1]}')
            if r.type == mesh.T_INSTANCELIST and (st['show'] or st['nb']) and imgui.button('Back to the world view'):
                st['show'], st['nb'] = 0, False
            return
        _, meshes, texs, nlod, st['stats'] = res
        if self.viewer is None:
            from .viewer3d import Viewer
            self.viewer = Viewer()
        ukey = (key, id(res))                # what is on the GPU: the request and the result shown
        if st['uploaded'] != ukey:
            old = st['uploaded'][0] if st['uploaded'] else None
            same = old is not None and old[:2] == key[:2] and old[3:] == key[3:]
            try:
                self.viewer.set_meshes(key, meshes, texs, 1.1 if r.type in (mesh.T_INSTANCELIST, mesh.T_POLYSOUP) else 2.6,
                                       keep_view=same)
            except Exception as e:
                traceback.print_exc()
                st['result'] = ('error', f'OpenGL: {e}')
                return
            st['uploaded'] = ukey
        v = self.viewer
        ntri = sum(len(m.tris) for m in meshes)
        nvert = sum(len(m.pos) for m in meshes)
        stats = st.get('stats') if r.type in (mesh.T_INSTANCELIST, mesh.T_POLYSOUP, mesh.T_VGS) else None
        cst = (stats or {}).get('collision')
        if r.type == mesh.T_POLYSOUP and cst:
            imgui.text(f'{cst["soups"]} soups, {cst["polygons"]:,} polygons, {len(cst["tags"])} surface tags'.replace(',', ' '))
            if imgui.is_item_hovered():
                imgui.set_tooltip('Triangles per collision tag (colour in the view):' + chr(10) + chr(10).join(
                    f'{t:#010x}  {n}' for t, n in sorted(cst['tags'].items(), key=lambda x: -x[1])[:24]))
        elif stats and 'car' in stats:
            cs = stats['car']
            imgui.text(f'Body + {cs["wheels"]} wheels ({cs["parts"]} wheel parts), {ntri:,} triangles'.replace(',', ' '))
            if cs['missing'] and imgui.is_item_hovered():
                imgui.set_tooltip('Models not found: ' + ', '.join(ops.id_text(x) for x in cs['missing'][:12]))
        elif stats and 'instances' in stats:
            units = f'{stats["units"]} units, ' if stats.get('units', 1) > 1 else ''
            imgui.text(f'{units}{stats["shown"]} of {stats["instances"]} instances ({stats["models"]} models), '
                       f'{ntri:,} triangles'.replace(',', ' '))
            if imgui.is_item_hovered():
                k = stats.get('kinds', {})
                tip = ', '.join(f'{k[n]} {n}' for n in ('world', 'props', 'dynamic', 'compound') if k.get(n))
                if stats['missing']:
                    tip += '\nModels not found: ' + ', '.join(ops.id_text(x) for x in stats['missing'][:12])
                imgui.set_tooltip(tip)
        else:
            imgui.text(f'{len(meshes)} mesh(es), {ntri:,} triangles, {nvert:,} vertices'.replace(',', ' '))
        if r.type == mesh.T_INSTANCELIST:
            imgui.set_next_item_width(150)          # own row: the summary line is long for whole units
            ch, sh = imgui.combo('##show', st['show'], ['World', 'Collision', 'World + collision'])
            if ch:
                st['show'] = sh
            imgui.same_line()
            ch, st['nb'] = imgui.checkbox('Neighbours', st['nb'])
            if imgui.is_item_hovered():
                imgui.set_tooltip('Also show the track units that share a border with this one (from HAWAII' + chr(92)
                                  + 'PVS.BNDL)')
        if nlod > 1 or r.type in (0x51, mesh.T_VGS):
            imgui.same_line()
            imgui.set_next_item_width(90)
            ch, lod = imgui.combo('##lod', st['lod'], [f'LOD {k}' for k in range(max(nlod, 1))])
            if ch:
                st['lod'] = lod
        _, v.use_tex = imgui.checkbox('Textures', v.use_tex)
        imgui.same_line()
        _, v.wire = imgui.checkbox('Wireframe', v.wire)
        imgui.same_line()
        _, v.z_up = imgui.checkbox('Z up', v.z_up)
        imgui.same_line()
        if imgui.button('Reset view'):
            v.pan[:] = 0
            v.dist = v.radius * v.fit
            v.yaw, v.pitch = 0.6, 0.35
        imgui.same_line()
        if imgui.button('Export glTF...'):
            self.action_export(d, r, 'glb')
        missing = sum(1 for m in meshes if m.texture and texs.get(m.texture) is None)
        if missing:
            imgui.text_disabled(f'{missing} mesh(es) use textures that are not in the open bundles or the global ones.')
        avail = imgui.get_content_region_avail()
        extra = int(imgui.get_frame_height_with_spacing() * 6.6) + 8 if r.type == mesh.T_VGS else 0   # wheel table
        w, h = max(64, int(avail.x) - 4), max(64, int(avail.y) - 24 - extra)
        v.widget(w, h)
        imgui.text_disabled('Left drag: turn, right / middle drag: move, wheel: zoom, double click: fit')
        if r.type == mesh.T_VGS:
            self.wheel_editor(d, r)

    def wheel_editor(self, d, r):
        """Wheel positions and scales of a VehicleGraphicsSpec (metres; x = left, y = up, z = forward)."""
        try:
            _, wheels = mesh.vgs_layout(d.b, r)
        except (struct.error, IndexError):
            return
        ro = d.b.truncated
        _, self.cfg['wheel_mirror'] = imgui.checkbox('Mirror left / right', self.cfg.get('wheel_mirror', True))
        if imgui.is_item_hovered():
            imgui.set_tooltip('Changing a left wheel also changes the right one of the same axle (x mirrored), and back')
        flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.resizable
        if not imgui.begin_table('wheels', 3, flags):
            return
        imgui.table_setup_column('Wheel', imgui.TableColumnFlags_.width_fixed, 90)
        imgui.table_setup_column('Position (x left, y up, z forward; m)', imgui.TableColumnFlags_.width_stretch)
        imgui.table_setup_column('Scale', imgui.TableColumnFlags_.width_stretch)
        imgui.table_headers_row()
        e = d.b.e
        by_name = {w['name']: w for w in wheels}
        for i, w in enumerate(wheels):
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.text(w['name'] or f'wheel {i}')
            imgui.table_next_column()
            imgui.set_next_item_width(-1)
            ch, pos = imgui.drag_float3(f'##wp{i}', list(w['pos']), 0.002, -5.0, 5.0, '%.3f')
            if ch and not ro:
                self.write(d, r, w['pos_off'], e + '3f', *pos)
                other = w['name'].replace('Left', '#').replace('Right', 'Left').replace('#', 'Right')
                if self.cfg.get('wheel_mirror', True) and other != w['name'] and other in by_name:
                    self.write(d, r, by_name[other]['pos_off'], e + '3f', -pos[0], pos[1], pos[2])
            imgui.table_next_column()
            imgui.set_next_item_width(-1)
            ch, sc = imgui.drag_float3(f'##ws{i}', list(w['scale']), 0.002, 0.2, 5.0, '%.3f')
            if ch and not ro:
                self.write(d, r, w['scale_off'], e + '3f', *sc)
                other = w['name'].replace('Left', '#').replace('Right', 'Left').replace('#', 'Right')
                if self.cfg.get('wheel_mirror', True) and other != w['name'] and other in by_name:
                    self.write(d, r, by_name[other]['scale_off'], e + '3f', *sc)
        imgui.end_table()

    def preview_ready(self):
        """True when the preview of the selected resource is complete (used by --screenshot)."""
        d, f = self.focused()
        if f is None or not self.cfg.get('preview', True):
            return True
        if f.type == T_TEXTURE:
            return self.tex.get('img') is not None or self.tex.get('err') is not None
        if f.type in (0x05, 0x51, 0x50, 0x60, 0x106):
            res = self.model['result']
            return res is not None and (res[0] == 'error' or self.model['uploaded'] == (self.model['key'], id(res)))
        if f.type == 0x81:
            hit = self.gcache.get(('wave', d.uid, f.id, id(f.data(0))))
            return hit is not None and not isinstance(hit, str)
        return True

    def locate(self, rid, key, fallback=None):
        """A link to a resource: go to it if open, else offer to open the bundle that has it (from the Find names
        index, or `fallback`)."""
        od, orr = self.where(rid)
        if orr is not None:
            if imgui.small_button(f'Go##{key}'):
                self.goto(rid, od)
            return
        paths = [p for p in self.names.where.get(rid, []) + ([fallback] if fallback else []) if os.path.isfile(p)]
        if paths:
            if imgui.small_button(f'Open##{key}'):
                d = self.open_path(paths[0])
                if d is not None:
                    self.goto(rid, d)
            if imgui.is_item_hovered():
                imgui.set_tooltip('Open ' + paths[0])
        else:
            imgui.text_disabled('-')

    def material_view(self, d, r):
        info = mesh.material_info(d.b, r)
        root = mesh.game_root(d.path)
        bundles = self.model_bundles(d)
        sb, sh = self.mesh_lib.find(info['shader'], bundles, root) if info['shader'] else (None, None)
        sname = ''
        if sh is not None:
            sc = sh.data(0)
            np_ = struct.unpack_from(sb.e + 'I', sc, 8)[0]
            sname = bytes(sc[np_:np_ + 128]).split(b'\0')[0].decode('latin1', 'replace')
        imgui.text(f'Shader: {sname or "?"}')
        if info['shader']:
            imgui.same_line()
            self.locate(info['shader'], 'msh', sb.path if sb is not None else None)
        imgui.text_disabled(f'{info["shader"] or 0:016X}' + (f'  in {os.path.basename(sb.path or "")}' if sb is not None and sb is not d.b else ''))
        imgui.spacing()
        imgui.text('Textures')
        flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.resizable
        if info['textures'] and imgui.begin_table('mtex', 4, flags):
            imgui.table_setup_column('', imgui.TableColumnFlags_.width_fixed, 72)
            imgui.table_setup_column('Slot', imgui.TableColumnFlags_.width_fixed, 110)
            imgui.table_setup_column('Texture', imgui.TableColumnFlags_.width_stretch)
            imgui.table_setup_column('', imgui.TableColumnFlags_.width_fixed, 50)
            imgui.table_headers_row()
            for k, (slot, tid, sid, off) in enumerate(info['textures']):
                imgui.table_next_row()
                imgui.table_next_column()
                tb, tr = self.mesh_lib.find(tid, bundles, root) if tid else (None, None)
                if tr is not None and tr.type == T_TEXTURE:
                    gl = self.thumbs.get(('mat', tid, id(tr.data(0))), tr, tb.platform, opaque=True)
                    if gl is not None:
                        self.thumbs.draw(gl, 64)
                    else:
                        imgui.dummy(imgui.ImVec2(64, 64))
                else:
                    imgui.dummy(imgui.ImVec2(64, 64))
                imgui.table_next_column()
                imgui.text(mesh.SLOT_NAMES.get(slot, f'slot {slot:04x}'))
                imgui.table_next_column()
                if tid:
                    od, orr = self.where(tid)
                    if orr is not None:
                        nm = self.display_name(od, orr)[0]
                    else:
                        full = self.names.exact.get(tid)
                        nm = N.short(full) if full else ops.id_text(tid)
                    imgui.text(nm)
                    if tb is not None and orr is None:
                        imgui.text_disabled(f'in {os.path.basename(tb.path or "")}')
                else:
                    imgui.text_disabled('none')
                imgui.table_next_column()
                if tid:
                    self.locate(tid, f'mt{k}', tb.path if tb is not None else None)
            imgui.end_table()
        imgui.spacing()
        imgui.text('Constants')
        names = self.mesh_lib.constant_names(root)
        if info['constants'] and imgui.begin_table('mconst', 2, flags):
            imgui.table_setup_column('Name', imgui.TableColumnFlags_.width_fixed, 240)
            imgui.table_setup_column('Value (Enter / drag)', imgui.TableColumnFlags_.width_stretch)
            imgui.table_headers_row()
            for k, (h, vals, off) in enumerate(info['constants']):
                imgui.table_next_row()
                imgui.table_next_column()
                imgui.text(names.get(h, f'{h:08x}'))
                imgui.table_next_column()
                imgui.set_next_item_width(-1)
                ch, nv = imgui.drag_float4(f'##c{k}', list(vals), 0.005, 0.0, 0.0, '%.4g')
                if ch and not d.b.truncated:
                    self.write(d, r, off, d.b.e + '4f', *nv)
            imgui.end_table()

    # -- vehicle list -----------------------------------------------------------------------------------------
    def vehicle_list_view(self, d, r):
        st = self.vlist
        key = (d.uid, r.id, id(r.data(0)))
        if st['key'] != key:
            st['key'] = key
            try:
                st['obj'], st['error'] = VL.read(r, d.b.e), None
            except (VL.VehicleListError, struct.error) as ex:
                st['obj'], st['error'] = None, str(ex)
        if st['obj'] is None:
            imgui.text_wrapped(f'Cannot read this vehicle list: {st["error"]}')
            return
        v = st['obj']
        strings = ops.game_strings(d.path)
        ro = d.b.truncated

        def commit(desc=None):
            if desc:
                d.checkpoint(desc, [r.id])
            else:
                d.edit_checkpoint(r.id)
            r.set_data(0, v.build())
            st['key'] = (d.uid, r.id, id(r.data(0)))
            d.summary.pop(r.id, None)
            d.b.modified = True

        def maker_name(mid):
            m = v.maker_by_id(mid)
            return strings.get(v.value(m, 'name', v.maker_fields), f'{mid}') if m is not None else f'{mid}'

        imgui.text(f'{len(v.rows)} vehicles, {len(v.makers)} manufacturers')
        if imgui.is_item_hovered():
            imgui.set_tooltip(f'VehicleList version {v.version}; '
                              + ('names from UI\\LANGUAGE\\0001.BNDL' if strings else 'the game strings were not found'))
        imgui.same_line()
        if imgui.button('Export CSV'):
            self.action_export(d, r, 'csv')
        imgui.same_line()
        if imgui.button('Import CSV') and not ro:
            p = filedialog.open_file('Import vehicle list (CSV)', self.cfg.get('last_import', ''), filedialog.CSV)
            if p:
                self.replace_from_file(d, r, p)
        if imgui.begin_tab_bar('vltabs'):
            if imgui.begin_tab_item('Vehicles')[0]:
                self.vehicle_rows(d, r, v, strings, commit, maker_name, ro)
                imgui.end_tab_item()
            if imgui.begin_tab_item('Manufacturers')[0]:
                self.maker_rows(d, r, v, strings, commit, ro)
                imgui.end_tab_item()
            imgui.end_tab_bar()

    def record_buttons(self, recs, sel_key, commit, what, ro):
        """Duplicate / Delete / Up / Down for a record list; returns nothing, edits `recs` in place."""
        st = self.vlist
        i = st[sel_key]
        ok = 0 <= i < len(recs) and not ro
        imgui.begin_disabled(not ok)
        if imgui.button(f'Duplicate##{sel_key}'):
            recs.insert(i + 1, bytearray(recs[i]))
            st[sel_key] = i + 1
            commit(f'duplicate {what}')
        imgui.same_line()
        if imgui.button(f'Delete##{sel_key}'):
            del recs[i]
            st[sel_key] = min(i, len(recs) - 1)
            commit(f'delete {what}')
        imgui.same_line()
        imgui.begin_disabled(i <= 0)
        if imgui.arrow_button(f'##up{sel_key}', imgui.Dir.up):
            recs[i - 1], recs[i] = recs[i], recs[i - 1]
            st[sel_key] = i - 1
            commit(f'move {what}')
        imgui.end_disabled()
        imgui.same_line()
        imgui.begin_disabled(i >= len(recs) - 1)
        if imgui.arrow_button(f'##dn{sel_key}', imgui.Dir.down):
            recs[i + 1], recs[i] = recs[i], recs[i + 1]
            st[sel_key] = i + 1
            commit(f'move {what}')
        imgui.end_disabled()
        imgui.end_disabled()

    def vehicle_rows(self, d, r, v, strings, commit, maker_name, ro):
        st = self.vlist
        imgui.set_next_item_width(260)
        _, st['filter'] = imgui.input_text_with_hint('##vlfilter', 'search name, id or manufacturer', st['filter'])
        imgui.same_line()
        self.record_buttons(v.rows, 'sel', commit, 'vehicle', ro)
        f = st['filter'].lower()
        shown = []
        for i, rec in enumerate(v.rows):
            nm = strings.get(v.value(rec, 'name'), '')
            mk = maker_name(v.value(rec, 'manufacturer'))
            if not f or f in nm.lower() or f in mk.lower() or f in str(v.value(rec, 'id')):
                shown.append((i, rec, nm, mk))
        cols = [('#', 28), ('Id', 62), ('Name', 0), ('Manufacturer', 104), ('mph', 34), ('0-60', 32), ('hp', 36),
                ('Year', 36)]
        flags = (imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.scroll_y
                 | imgui.TableFlags_.resizable)
        avail = imgui.get_content_region_avail().y
        if imgui.begin_table('vlrows', len(cols), flags, imgui.ImVec2(0, max(120, avail * 0.45))):
            imgui.table_setup_scroll_freeze(0, 1)
            for name, w in cols:
                imgui.table_setup_column(name, imgui.TableColumnFlags_.width_fixed if w else imgui.TableColumnFlags_.width_stretch, w)
            imgui.table_headers_row()
            clipper = imgui.ListClipper()
            clipper.begin(len(shown))
            while clipper.step():
                for j in range(clipper.display_start, clipper.display_end):
                    i, rec, nm, mk = shown[j]
                    imgui.table_next_row()
                    imgui.table_next_column()
                    if imgui.selectable(f'{i}##vr{i}', st['sel'] == i, imgui.SelectableFlags_.span_all_columns)[0]:
                        st['sel'] = i
                    imgui.table_next_column()
                    imgui.text(str(v.value(rec, 'id')))
                    imgui.table_next_column()
                    imgui.text(nm or '-')
                    imgui.table_next_column()
                    imgui.text(mk)
                    imgui.table_next_column()
                    imgui.text(f'{v.value(rec, "top_speed_2"):.0f}')
                    imgui.table_next_column()
                    imgui.text(f'{v.value(rec, "zero_to_60"):.1f}')
                    imgui.table_next_column()
                    imgui.text(str(v.value(rec, 'power')))
                    imgui.table_next_column()
                    imgui.text(str(v.value(rec, 'year')))
            imgui.end_table()
        i = st['sel']
        if 0 <= i < len(v.rows):
            imgui.begin_child('vldetail', imgui.ImVec2(0, 0))
            self.record_editor(d, v, v.rows[i], v.fields, strings, commit, f'v{i}', ro)
            imgui.end_child()

    def maker_rows(self, d, r, v, strings, commit, ro):
        st = self.vlist
        self.record_buttons(v.makers, 'msel', commit, 'manufacturer', ro)
        flags = (imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.scroll_y
                 | imgui.TableFlags_.resizable)
        avail = imgui.get_content_region_avail().y
        if imgui.begin_table('vlmakers', 4, flags, imgui.ImVec2(0, max(120, avail * 0.5))):
            imgui.table_setup_scroll_freeze(0, 1)
            imgui.table_setup_column('#', imgui.TableColumnFlags_.width_fixed, 34)
            imgui.table_setup_column('Id', imgui.TableColumnFlags_.width_fixed, 70)
            imgui.table_setup_column('Name', imgui.TableColumnFlags_.width_stretch)
            imgui.table_setup_column('Vehicles', imgui.TableColumnFlags_.width_fixed, 60)
            imgui.table_headers_row()
            for i, m in enumerate(v.makers):
                mid = v.value(m, 'id', v.maker_fields)
                imgui.table_next_row()
                imgui.table_next_column()
                if imgui.selectable(f'{i}##mr{i}', st['msel'] == i, imgui.SelectableFlags_.span_all_columns)[0]:
                    st['msel'] = i
                imgui.table_next_column()
                imgui.text(str(mid))
                imgui.table_next_column()
                imgui.text(strings.get(v.value(m, 'name', v.maker_fields), '-'))
                imgui.table_next_column()
                imgui.text(str(sum(1 for rec in v.rows if v.value(rec, 'manufacturer') == mid)))
            imgui.end_table()
        i = st['msel']
        if 0 <= i < len(v.makers):
            imgui.begin_child('vlmdetail', imgui.ImVec2(0, 0))
            self.record_editor(d, v, v.makers[i], v.maker_fields, strings, commit, f'm{i}', ro)
            imgui.end_child()

    def record_editor(self, d, v, rec, fields, strings, commit, key, ro):
        """Every field of one record: an editor, and what the number refers to."""
        flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.resizable
        if not imgui.begin_table(f'vlrec{key}', 3, flags):
            return
        imgui.table_setup_column('Field', imgui.TableColumnFlags_.width_fixed, 200)
        imgui.table_setup_column('Value', imgui.TableColumnFlags_.width_fixed, 120)
        imgui.table_setup_column('Refers to', imgui.TableColumnFlags_.width_stretch)
        imgui.table_headers_row()
        for f in fields:
            off, code, name, label, kind = f
            val = v.get(rec, f)
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.text(label)
            if imgui.is_item_hovered():
                imgui.set_tooltip(f'{name}, offset {off:#04x}')
            imgui.table_next_column()
            imgui.set_next_item_width(-1)
            fid = f'##{key}{off}'
            new = None
            if code == 'f':
                ch, nv = imgui.input_float(fid, val, 0, 0, '%.6g', imgui.InputTextFlags_.enter_returns_true)
                if ch:
                    new = nv
            elif kind == 'hex':
                ch, txt = imgui.input_text(fid, f'0x{val:x}', imgui.InputTextFlags_.enter_returns_true)
                if ch:
                    try:
                        new = int(txt, 0)
                    except ValueError:
                        self.status = f'Not a number: {txt}'
            elif kind == 'maker':
                cur = v.maker_by_id(val)
                cur_name = strings.get(v.value(cur, 'name', v.maker_fields), str(val)) if cur is not None else str(val)
                if imgui.begin_combo(fid, cur_name):
                    for m in v.makers:
                        mid = v.value(m, 'id', v.maker_fields)
                        if imgui.selectable(f'{strings.get(v.value(m, "name", v.maker_fields), mid)}##mk{mid}', mid == val)[0]:
                            new = mid
                    imgui.end_combo()
            else:
                ch, txt = imgui.input_text(fid, str(val), imgui.InputTextFlags_.enter_returns_true | imgui.InputTextFlags_.chars_decimal)
                if ch:
                    try:
                        new = int(txt, 0)
                    except ValueError:
                        self.status = f'Not a number: {txt}'
            if new is not None and new != val and not ro:
                try:
                    v.set(rec, f, new)
                    commit()
                    self.status = f'{label} set to {new} (Ctrl+Z to undo).'
                except struct.error as ex:
                    self.status = f'Value out of range: {ex}'
            imgui.table_next_column()
            if kind == 'string' and val:
                txt = strings.get(val)
                if txt is None:
                    imgui.text_disabled('(not in the game strings)')
                else:
                    one = ' '.join(txt.split())
                    imgui.text(one[:90] + ('...' if len(one) > 90 else ''))
                    if len(one) > 90 and imgui.is_item_hovered():
                        imgui.push_text_wrap_pos(600)
                        imgui.set_tooltip(txt)
                        imgui.pop_text_wrap_pos()
            elif kind == 'ref' and val:
                rid = (0x01000000 << 32) | val
                od, orr = self.where(rid)
                full = self.names.exact.get(rid)
                self.locate(rid, f'{key}{off}')
                imgui.same_line()
                if orr is not None:
                    imgui.text(self.display_name(od, orr)[0])
                elif full:
                    imgui.text(N.short(full))
                else:
                    paths = self.names.where.get(rid)
                    imgui.text_disabled(f'{ops.id_text(rid)}' + (f' in {os.path.basename(paths[0])}' if paths else ''))
            elif kind == 'vehicle' and val:
                root = ops.game_root(d.path)
                found = None
                for suffix in ('HI', 'MS', 'LO'):
                    p = os.path.join(root or '', 'VEHICLES', f'VEH_{val}_{suffix}.BNDL')
                    if root and os.path.isfile(p):
                        found = p
                        break
                if found:
                    if imgui.small_button(f'Open {os.path.basename(found)}##{key}{off}'):
                        self.open_path(found)
                else:
                    imgui.text_disabled('no VEH bundle')
        imgui.end_table()

    # -- zone map ---------------------------------------------------------------------------------------------
    def zone_map_view(self, d, r):
        """HAWAII\\PVS.BNDL: the track units as a map; click one to open it."""
        key = (d.uid, r.id, id(r.data(0)))
        zm = self.zmap
        if zm.get('key') != key:
            try:
                zones = ZL.read(r, d.b.e)
            except (struct.error, IndexError) as ex:
                zones = []
                self.status = f'Cannot read the zone list: {ex}'
            self.zmap = zm = {'key': key, 'zones': zones, 'zoom': 1.0, 'pan': [0.0, 0.0]}
        zones = zm['zones']
        if not zones:
            imgui.text_disabled('No zones.')
            return
        districts = sorted({z.district for z in zones})
        import colorsys
        pal = {dd: colorsys.hsv_to_rgb(i / max(len(districts), 1), 0.55, 0.85) for i, dd in enumerate(districts)}
        imgui.text(f'{len(zones)} track units in {len(districts)} districts')
        imgui.same_line()
        imgui.text_disabled('click: open the unit, wheel: zoom, right drag: move')
        line_w = imgui.get_content_region_avail().x
        used = 0.0
        for i, dd in enumerate(districts):
            c = pal[dd]
            label = f'DISTRICT_{dd} ({sum(1 for z in zones if z.district == dd)})'
            w = 12 + 3 * imgui.get_style().item_spacing.x + imgui.calc_text_size(label).x
            if i and used + w <= line_w:
                imgui.same_line()
            else:
                used = 0.0
            used += w
            imgui.color_button(f'##dc{dd}', imgui.ImVec4(c[0], c[1], c[2], 1), 0, imgui.ImVec2(12, 12))
            imgui.same_line()
            imgui.text(label)
        avail = imgui.get_content_region_avail()
        w, h = max(100.0, avail.x), max(100.0, avail.y - 4)
        p0 = imgui.get_cursor_screen_pos()
        imgui.invisible_button('##zmap', imgui.ImVec2(w, h), imgui.ButtonFlags_.mouse_button_left | imgui.ButtonFlags_.mouse_button_right)
        hovered = imgui.is_item_hovered()
        clicked = imgui.is_item_clicked(imgui.MouseButton_.left)
        io = imgui.get_io()
        xs = [x for z in zones for x, _ in z.points]
        ys = [y for z in zones for _, y in z.points]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        base = 0.95 * min(w / max(max(xs) - min(xs), 1), h / max(max(ys) - min(ys), 1))
        scale = base * zm['zoom']
        ox, oy = cx + zm['pan'][0], cy + zm['pan'][1]

        def scr(x, y):
            return imgui.ImVec2(p0.x + w / 2 + (x - ox) * scale, p0.y + h / 2 + (y - oy) * scale)

        mx, my = (io.mouse_pos.x - p0.x - w / 2) / scale + ox, (io.mouse_pos.y - p0.y - h / 2) / scale + oy
        if hovered and io.mouse_wheel:
            f = 1.2 ** io.mouse_wheel
            zm['zoom'] = min(40.0, max(0.5, zm['zoom'] * f))
            ns = base * zm['zoom']
            zm['pan'][0] = mx - (io.mouse_pos.x - p0.x - w / 2) / ns - cx      # keep the point under the mouse
            zm['pan'][1] = my - (io.mouse_pos.y - p0.y - h / 2) / ns - cy
        if imgui.is_item_active() and imgui.is_mouse_down(1):
            zm['pan'][0] -= io.mouse_delta.x / scale
            zm['pan'][1] -= io.mouse_delta.y / scale

        def inside(z, x, y):
            pts, c = z.points, False
            for i in range(len(pts)):
                (x1, y1), (x2, y2) = pts[i], pts[i - 1]
                if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                    c = not c
            return c

        hot = next((z for z in zones if hovered and inside(z, mx, my)), None)
        near = {zones[j].index for j, fl in hot.neighbours if fl & 2} if hot is not None else set()
        dl = imgui.get_window_draw_list()
        dl.push_clip_rect(p0, imgui.ImVec2(p0.x + w, p0.y + h), True)
        dl.add_rect_filled(p0, imgui.ImVec2(p0.x + w, p0.y + h), imgui.get_color_u32(imgui.Col_.frame_bg))
        for z in zones:
            c = pal[z.district]
            a = 0.95 if z is hot else 0.75 if z.index in near else 0.45
            pts = [scr(x, y) for x, y in z.points]
            if len(pts) >= 3:
                dl.add_concave_poly_filled(pts, imgui.color_convert_float4_to_u32(imgui.ImVec4(c[0], c[1], c[2], a)))
                dl.add_polyline(pts, imgui.get_color_u32(imgui.Col_.border), 1.0, imgui.ImDrawFlags_.closed)
            if scale * min(z.box[2] - z.box[0], z.box[3] - z.box[1]) > 28:
                sx = sum(p.x for p in pts) / len(pts)
                sy = sum(p.y for p in pts) / len(pts)
                t = str(z.unit)
                ts = imgui.calc_text_size(t)
                dl.add_text(imgui.ImVec2(sx - ts.x / 2, sy - ts.y / 2), imgui.get_color_u32(imgui.Col_.text), t)
        dl.pop_clip_rect()
        if hot is not None:
            nbs = ', '.join(str(zones[j].unit) for j, fl in hot.neighbours if fl & 2)
            imgui.set_tooltip(f'TRK_UNIT{hot.unit}.BNDL' + chr(10) + f'DISTRICT_{hot.district}' + chr(10)
                              + f'next to: {nbs}' + chr(10) + 'click to open')
            if clicked:
                self.open_unit(os.path.join(os.path.dirname(d.path or ''), ZL.unit_file(hot.unit)))

    def open_unit(self, path):
        """Open a TRK_UNIT bundle and select its InstanceList (the 3D view of the unit)."""
        if not os.path.isfile(path):
            self.status = f'{path} not found.'
            return
        nd = self.open_path(path)
        if nd is not None:
            rid = next((x.id for x in nd.b.resources if x.type == mesh.T_INSTANCELIST), None)
            if rid is not None:
                self.goto(rid, nd)

    def export_glb(self, d, res, path):
        lod = self.model['lod'] if self.model['key'] and self.model['key'][1] == res.id else 0
        meshes, _ = mesh.decode_resource(d.b, res, self.mesh_lib, self.model_bundles(d)[1:], d.path, lod)
        size = 1024 if res.type == mesh.T_INSTANCELIST else 1 << 14
        texs = {t: self.texture_image(d, t, size) for t in {m.texture for m in meshes if m.texture}}
        name = self.display_name(d, res)[0]
        ops.write_file(path, gltf.write_glb(meshes, texs, name))
        return len(meshes)

    def cube_view(self, d, r):
        lut = ops.cube_lut(d.b, r)
        strip = ops.cube_strip(lut)
        big = np.ascontiguousarray(np.repeat(np.repeat(strip, 3, 0), 3, 1)[..., :3])
        imgui.text('16 x 16 x 16 colour-grading cube: 16 slices (blue) of red (x) by green (y).')
        immvision.image_display(f'##cube{r.id}{id(r.data(0))}', big, (768, 48), True, False)
        imgui.text_disabled('Export / replace as a 256 x 16 PNG (Export... / Replace...).')

    def text_view(self, d, r):
        key = (d.uid, r.id, id(r.data(0)))
        if self.text_edit['key'] != key:
            self.text_edit = {'key': key, 'text': textfile.decode(textfile.read(r, d.b.e)), 'dirty': False}
        te = self.text_edit
        if imgui.button('Apply changes') and te['dirty'] and not d.b.truncated:
            d.checkpoint(f'text {r.id:#x}', [r.id])
            ops.replace_text(d.b, r, te['text'].encode('utf-8'))
            self.changed(d, r)
            self.text_edit['key'] = (d.uid, r.id, id(r.data(0)))
            te['dirty'] = False
            self.status = f'Text {r.id:#x} updated (Ctrl+Z to undo).'
        imgui.same_line()
        imgui.text_disabled('modified' if te['dirty'] else f'{len(te["text"])} characters')
        ch, te['text'] = imgui.input_text_multiline('##text', te['text'], imgui.ImVec2(-1, -1),
                                                    imgui.InputTextFlags_.allow_tab_input)
        if ch:
            te['dirty'] = True

    def strings_view(self, d, r):
        key = (d.uid, r.id, id(r.data(0)))
        if self.str_cache['key'] != key:
            self.str_cache = {'key': key, 'table': StringTable.read(r, d.b.e)}
        tbl = self.str_cache['table']
        imgui.set_next_item_width(-200)
        _, self.str_filter = imgui.input_text_with_hint('##sfilter', 'search id or text', self.str_filter)
        imgui.same_line()
        if imgui.button('Export CSV'):
            self.action_export(d, r, 'csv')
        imgui.same_line()
        if imgui.button('Import CSV'):
            p = filedialog.open_file('Import strings (CSV: id,text)', self.cfg.get('last_import', ''), filedialog.CSV)
            if p:
                self.replace_from_file(d, r, p)
        f = self.str_filter.lower()
        ents = [(i, e) for i, e in enumerate(tbl.entries) if not f or f in f'{e[0]:08x}' or f in e[1].lower()]
        flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.scroll_y | imgui.TableFlags_.resizable
        if imgui.begin_table('strings', 2, flags):
            imgui.table_setup_scroll_freeze(0, 1)
            imgui.table_setup_column('Id', imgui.TableColumnFlags_.width_fixed, 80)
            imgui.table_setup_column('Text (Enter to apply)', imgui.TableColumnFlags_.width_stretch)
            imgui.table_headers_row()
            clipper = imgui.ListClipper()
            clipper.begin(len(ents))
            while clipper.step():
                for j in range(clipper.display_start, clipper.display_end):
                    i, (sid, text) = ents[j]
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.text(f'{sid:08X}')
                    imgui.table_next_column()
                    imgui.set_next_item_width(-1)
                    ch, new = imgui.input_text(f'##s{i}', text, imgui.InputTextFlags_.enter_returns_true)
                    if ch and new != text and not d.b.truncated:
                        d.edit_checkpoint(r.id)
                        tbl.set(sid, new)
                        r.set_data(0, tbl.build(d.b.e))
                        self.str_cache['key'] = (d.uid, r.id, id(r.data(0)))
                        d.summary.pop(r.id, None)
                        self.status = f'String {sid:08X} changed (Ctrl+Z to undo).'
            imgui.end_table()

    # -- Genesys ----------------------------------------------------------------------------------------------
    def type_view(self, d, r):
        t = self.types.get(r.id) or genesys.parse_type(r, d.b.e)
        imgui.text(f'{t.name or "(no name)"}')
        imgui.text_disabled(f'kind {genesys.KIND.get(t.kind, t.kind)}, size {t.size}, alignment {1 << t.align}, '
                            f'schema {t.schema[0]:08x} / {t.schema[1]:#x}')
        if t.base:
            imgui.text('Base: ')
            imgui.same_line()
            if imgui.small_button(f'{self.types.name(t.base)}##base'):
                self.goto(t.base, d)
        if not t.fields:
            return
        flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.scroll_y | imgui.TableFlags_.resizable
        if t.kind == 5:
            if imgui.begin_table('enum', 2, flags):
                imgui.table_setup_column('Value name')
                imgui.table_setup_column('Value')
                imgui.table_headers_row()
                for f in t.fields:
                    imgui.table_next_row()
                    imgui.table_next_column()
                    self.field_label(f.name_hash)
                    imgui.table_next_column()
                    imgui.text(str(f.offset))
                imgui.end_table()
            return
        if imgui.begin_table('fields', 6, flags):
            imgui.table_setup_scroll_freeze(0, 1)
            for c in ('Field', 'Type', 'Offset', 'Size', 'Count', 'Flags'):
                imgui.table_setup_column(c)
            imgui.table_headers_row()
            for f in t.fields:
                imgui.table_next_row()
                imgui.table_next_column()
                self.field_label(f.name_hash)
                imgui.table_next_column()
                if imgui.selectable(f'{self.types.name(f.type_id)}##ft{f.index}', False)[0] and f.type_id:
                    self.goto(f.type_id, d)
                imgui.table_next_column()
                imgui.text(f'{f.offset:#x}')
                imgui.table_next_column()
                imgui.text(str(f.size))
                imgui.table_next_column()
                imgui.text(str(f.count))
                imgui.table_next_column()
                imgui.text(field_flags(f.flags))
            imgui.end_table()

    def field_label(self, h):
        n = self.labels.name(h)
        imgui.text(n if n else f'{h:08x}')
        if imgui.is_item_hovered():
            imgui.set_tooltip(f'name hash {h:08x}; right click to name it')
        if imgui.begin_popup_context_item(f'lbl{h}'):
            key = ('rename', h)
            if self.gcache.get(key) is None:
                self.gcache[key] = self.labels.name(h) or ''
            imgui.text(f'Name for {h:08x} (saved for every field with this hash):')
            ch, self.gcache[key] = imgui.input_text('##rename', self.gcache[key], imgui.InputTextFlags_.enter_returns_true)
            if ch or imgui.button('OK'):
                self.labels.rename(h, self.gcache[key])
                self.gcache.pop(key, None)
                imgui.close_current_popup()
            imgui.end_popup()

    def object_view(self, d, r):
        key = (d.uid, r.id, id(r.data(0)), len(self.types.types))
        node = self.gcache.get(key)
        if node is None:
            try:
                node = genesys.Reader(self.types, d.b.e).read_resource(r)
            except genesys.MissingType as m:
                imgui.text_wrapped(f'This object uses the Genesys type {m.args[0]:#x}, which is not in any open bundle. '
                                   'Open the bundle that holds its types (usually the same folder) to see the fields.')
                return
            self.gcache[key] = node
        imgui.text(self.types.name(node.type))
        imgui.same_line()
        imgui.text_disabled('(drag a number to change it, Ctrl+click to type; right click a field to name it)')
        flags = (imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.resizable
                 | imgui.TableFlags_.scroll_y)
        if imgui.begin_table('obj', 3, flags):
            imgui.table_setup_scroll_freeze(0, 1)
            imgui.table_setup_column('Field', imgui.TableColumnFlags_.width_fixed, 240)
            imgui.table_setup_column('Value', imgui.TableColumnFlags_.width_stretch)
            imgui.table_setup_column('Type', imgui.TableColumnFlags_.width_fixed, 140)
            imgui.table_headers_row()
            self.node_rows(d, r, node, 'n')
            imgui.end_table()

    def node_rows(self, d, r, node, path):
        t = self.types.get(node.type)
        if t is None:
            return
        for f in t.fields:
            v = node.fields.get(f.name_hash)
            loc = node.locs.get(f.name_hash)
            self.value_row(d, r, f, v, loc, f'{path}.{f.name_hash:x}')

    def value_row(self, d, r, f, v, loc, path, label=None):
        imgui.table_next_row()
        imgui.table_next_column()
        ft = self.types.get(f.type_id)
        tname = self.types.name(f.type_id) if f.type_id else f'{f.size} bytes'
        container = isinstance(v, (Node, list)) and not (isinstance(v, list) and v and not isinstance(v[0], (Node, list, Ref)))
        name = label or (self.labels.name(f.name_hash) or f'{f.name_hash:08x}')
        if container:
            n = len(v) if isinstance(v, list) else None
            if self.expand_all:
                imgui.set_next_item_open(True, imgui.Cond_.always)
            opened = imgui.tree_node_ex(f'{name}##{path}', imgui.TreeNodeFlags_.span_full_width)
            self.label_menu(f.name_hash, path)
            imgui.table_next_column()
            imgui.text_disabled(f'[{n}]' if n is not None else '{...}')
            imgui.table_next_column()
            imgui.text_disabled(tname)
            if opened:
                if isinstance(v, Node):
                    self.node_rows(d, r, v, path)
                else:
                    for i, x in enumerate(v):
                        if isinstance(x, Node):
                            imgui.table_next_row()
                            imgui.table_next_column()
                            if self.expand_all and i < 2:
                                imgui.set_next_item_open(True, imgui.Cond_.always)
                            op = imgui.tree_node_ex(f'[{i}] {self.types.name(x.type)}##{path}.{i}', imgui.TreeNodeFlags_.span_full_width)
                            imgui.table_next_column()
                            imgui.table_next_column()
                            if op:
                                self.node_rows(d, r, x, f'{path}.{i}')
                                imgui.tree_pop()
                        else:
                            self.value_row(d, r, f, x, None, f'{path}.{i}', label=f'[{i}]')
                imgui.tree_pop()
            return
        imgui.tree_node_ex(f'{name}##{path}', imgui.TreeNodeFlags_.leaf | imgui.TreeNodeFlags_.no_tree_push_on_open
                           | imgui.TreeNodeFlags_.span_full_width | imgui.TreeNodeFlags_.bullet)
        self.label_menu(f.name_hash, path)
        imgui.table_next_column()
        imgui.set_next_item_width(-1)
        self.leaf_widget(d, r, f, ft, v, loc, path)
        imgui.table_next_column()
        imgui.text_disabled(tname)

    def label_menu(self, h, path):
        if imgui.begin_popup_context_item(f'ctx{path}'):
            key = ('rename', h)
            if self.gcache.get(key) is None:
                self.gcache[key] = self.labels.name(h) or ''
            imgui.text(f'Name for field {h:08x} (used everywhere this hash appears):')
            ch, self.gcache[key] = imgui.input_text('##rename', self.gcache[key], imgui.InputTextFlags_.enter_returns_true)
            if ch or imgui.button('OK'):
                self.labels.rename(h, self.gcache[key])
                self.gcache.pop(key, None)
                imgui.close_current_popup()
            imgui.end_popup()

    def leaf_widget(self, d, r, f, ft, v, loc, path):
        if isinstance(v, Ref):
            od, orr = self.where(v.id)
            lbl = f'-> {self.display_name(od, orr)[0]}  ({type_name(orr.type)})' if orr else f'-> {v.id:016X}  (not open)'
            if imgui.selectable(f'{lbl}##{path}', False)[0] and orr is not None:
                self.goto(v.id, od)
            return
        if v is None:
            imgui.text_disabled('null')
            return
        kind = leaf_kind(self.types, f) if loc is not None else None
        editable = kind is not None and not d.b.truncated
        if not editable:
            imgui.text(fmt_value(v))
            return
        off = loc[0]
        e = d.b.e
        if kind == 'float':
            speed = max(abs(v) * 0.005, 0.001)
            ch, nv = imgui.drag_float(f'##{path}', float(v), speed, 0.0, 0.0, '%.6g')
            if ch:
                self.write(d, r, off, e + 'f', nv)
        elif kind == 'vector':
            vals = [float(x) for x in v]
            n = len(vals)
            speed = max(max(abs(x) for x in vals) * 0.005, 0.001) if vals else 0.01
            fn = {2: imgui.drag_float2, 3: imgui.drag_float3, 4: imgui.drag_float4}.get(n)
            if fn is not None:
                if n >= 3 and 0 <= min(vals[:3]) and max(vals[:3]) <= 64:
                    ch, col = imgui.color_edit3(f'##c{path}', vals[:3], imgui.ColorEditFlags_.no_inputs
                                                | imgui.ColorEditFlags_.hdr | imgui.ColorEditFlags_.float)
                    if ch:
                        self.write(d, r, off, e + '3f', *col)
                        vals[:3] = list(col)
                    imgui.same_line()
                    imgui.set_next_item_width(-1)
                ch, nv = fn(f'##{path}', vals, speed, 0.0, 0.0, '%.4g')
                if ch:
                    self.write(d, r, off, e + f'{n}f', *nv)
            else:
                w = max(30.0, (imgui.get_content_region_avail().x - 4 * (n - 1)) / max(n, 1))
                for i, x in enumerate(vals):
                    if i:
                        imgui.same_line(0, 4)
                    imgui.set_next_item_width(w)
                    ch, nx = imgui.drag_float(f'##{path}.{i}', x, speed, 0.0, 0.0, '%.4g')
                    if ch:
                        self.write(d, r, off + 4 * i, e + 'f', nx)
        elif kind == 'bool':
            ch, nv = imgui.checkbox(f'##{path}', bool(v))
            if ch:
                size = ft.size if ft else f.size
                self.write(d, r, off, e + {1: 'B', 2: 'H', 4: 'I'}.get(size, 'B'), int(nv))
        elif kind == 'enum':
            size = ft.size
            names = [(x.offset, self.labels.name(x.name_hash) or f'{x.name_hash:08x}') for x in ft.fields]
            cur = next((n for val, n in names if val == v), str(v))
            if imgui.begin_combo(f'##{path}', f'{cur} ({v})'):
                for val, n in names:
                    if imgui.selectable(f'{n} ({val})##{path}{val}', val == v)[0]:
                        self.write(d, r, off, e + {1: 'B', 2: 'H', 4: 'I'}[size], val)
                imgui.end_combo()
        elif kind in ('int', 'uint'):
            size = ft.size if ft else f.size
            code = {1: 'b', 2: 'h', 4: 'i', 8: 'q'}[size]
            if kind == 'uint':
                code = code.upper()
            ch, txt = imgui.input_text(f'##{path}', str(v), imgui.InputTextFlags_.enter_returns_true)
            if ch:
                try:
                    nv = int(txt, 0)
                    self.write(d, r, off, e + code, nv)
                except (ValueError, struct.error) as ex:
                    self.status = f'Not a valid value: {ex}'
        elif kind == 'string':
            ch, txt = imgui.input_text(f'##{path}', v, imgui.InputTextFlags_.enter_returns_true)
            if ch and txt != v:
                self.write_string(d, r, off, txt)
        else:
            imgui.text(fmt_value(v))

    def write(self, d, r, off, fmt, *vals):
        d.edit_checkpoint(r.id)
        buf = bytearray(r.data(0))
        try:
            struct.pack_into(fmt, buf, off, *vals)
        except struct.error as ex:
            self.status = f'Value out of range: {ex}'
            return
        r.set_data(0, buf)
        self.gcache.clear()
        d.summary.pop(r.id, None)

    def write_string(self, d, r, off, text):
        e = d.b.e
        buf = bytearray(r.data(0))
        rel, ln = struct.unpack_from(e + 'II', buf, off)
        raw = text.encode('latin1', 'replace')
        if len(raw) + 1 > ln:
            self.status = f'The new text is longer than the old one ({ln - 1} characters); longer strings are not supported yet.'
            return
        d.edit_checkpoint(r.id)
        buf[off + rel:off + rel + ln] = raw + b'\0' * (ln - len(raw))
        struct.pack_into(e + 'I', buf, off + 4, len(raw) + 1)
        r.set_data(0, buf)
        self.gcache.clear()

    # -- status / modals --------------------------------------------------------------------------------------
    def results_window(self):
        if self.results is None:
            return
        imgui.set_next_window_size(imgui.ImVec2(620, 360), imgui.Cond_.first_use_ever)
        opened, keep = imgui.begin(f'{self.results["title"]}###results', True)
        if opened:
            items = self.results['items']
            imgui.text(f'{len(items)} result(s)')
            for i, (uid, rid, off) in enumerate(items[:2000]):
                d = self.doc_by_uid(uid)
                r = d.b.find(rid) if d else None
                if r is None:
                    continue
                extra = f' (import at {off:#x})' if off is not None else ''
                if imgui.selectable(f'{d.name}: {self.display_name(d, r)[0]}  ({type_name(r.type)} {ops.id_text(rid)}){extra}##res{i}', False)[0]:
                    self.goto(rid, d)
        imgui.end()
        if keep is False:
            self.results = None

    def modals(self):
        if self.busy:
            imgui.open_popup('Working')
            if imgui.begin_popup_modal('Working', None, imgui.WindowFlags_.always_auto_resize)[0]:
                imgui.text(self.busy[0])
                imgui.progress_bar(self.busy[1], imgui.ImVec2(320, 0))
                imgui.end_popup()
            return
        m = self.modal
        if m is None:
            return
        title = {'message': m.get('title', 'Message'), 'confirm_delete': 'Delete resources',
                 'confirm_close': 'Unsaved changes', 'confirm_exit': 'Unsaved changes',
                 'confirm_replace': 'Replace resources', 'change_id': 'Duplicate resource' if m.get('dup') else 'Change id',
                 'texture_options': 'Replace texture', 'wave_options': 'Replace sound', 'open_path': 'Open by path', 'find': 'Find', 'goto': 'Go to id',
                 'pick_chunk': 'Replace chunk', 'properties': 'Bundle properties',
                 'plate': 'License plate registration'}.get(m['kind'], 'Message')
        popup = f'{title}###modal'
        if not imgui.is_popup_open(popup):
            imgui.open_popup(popup)
        opened, _ = imgui.begin_popup_modal(popup, None, imgui.WindowFlags_.always_auto_resize)
        if not opened:
            return
        close = False
        k = m['kind']
        if k == 'message':
            imgui.push_text_wrap_pos(620)
            imgui.text_unformatted(m['text'])
            imgui.pop_text_wrap_pos()
            if m.get('open') and imgui.button('Open it'):
                self.open_path(m['open'])
                close = True
                imgui.same_line()
            if imgui.button('OK', imgui.ImVec2(120, 0)) or imgui.is_key_pressed(imgui.Key.enter):
                close = True
        elif k == 'confirm_delete':
            d = self.doc_by_uid(m['doc'])
            imgui.text(f'Delete {len(m["ids"])} resource(s) from {d.name if d else "?"}? (Ctrl+Z undoes it)')
            if imgui.button('Delete', imgui.ImVec2(120, 0)) and d is not None:
                self.action_delete(d, m['ids'])
                close = True
            imgui.same_line()
            if imgui.button('Cancel', imgui.ImVec2(120, 0)):
                close = True
        elif k == 'confirm_close':
            d = self.doc_by_uid(m['doc'])
            imgui.text(f'{d.name if d else "?"} has unsaved changes.')
            if imgui.button('Save', imgui.ImVec2(120, 0)) and d is not None:
                self.action_save(d)
                close = True
            imgui.same_line()
            if imgui.button('Close without saving', imgui.ImVec2(170, 0)) and d is not None:
                self.close_doc(d, force=True)
                close = True
            imgui.same_line()
            if imgui.button('Cancel', imgui.ImVec2(120, 0)):
                close = True
        elif k == 'confirm_exit':
            names = ', '.join([d.name for d in self.docs if d.modified]
                              + (['the soundtrack editor'] if self.st_ui is not None and self.st_ui['model'].dirty else []))
            imgui.text(f'Unsaved changes in: {names}')
            if imgui.button('Exit without saving', imgui.ImVec2(170, 0)):
                self.exit_ok = True
                hello_imgui.get_runner_params().app_shall_exit = True
                close = True
            imgui.same_line()
            if imgui.button('Cancel', imgui.ImVec2(120, 0)):
                close = True
        elif k == 'confirm_replace':
            d = self.doc_by_uid(m['doc'])
            imgui.text(f'{m["n"]} of the {len(m["news"])} resource(s) already exist in {d.name if d else "?"}.')
            if imgui.button('Replace them', imgui.ImVec2(140, 0)) and d is not None:
                self._confirmed_replace = True
                self.add_resources(d, m['news'], m['desc'])
                close = True
            imgui.same_line()
            if imgui.button('Only add the new ones', imgui.ImVec2(170, 0)) and d is not None:
                news = [r for r in m['news'] if d.b.find(r.id) is None]
                if news:
                    self.add_resources(d, news, m['desc'])
                close = True
            imgui.same_line()
            if imgui.button('Cancel', imgui.ImVec2(120, 0)):
                close = True
        elif k == 'change_id':
            d = self.doc_by_uid(m['doc'])
            imgui.text('New id (hex):')
            imgui.set_next_item_width(260)
            if imgui.is_window_appearing():
                imgui.set_keyboard_focus_here()
            ch, m['text'] = imgui.input_text('##newid', m['text'], imgui.InputTextFlags_.chars_hexadecimal
                                             | imgui.InputTextFlags_.enter_returns_true)
            if not m['dup']:
                imgui.text_disabled('Imports of the old id in this bundle are updated too.')
            if (imgui.button('OK', imgui.ImVec2(120, 0)) or ch) and d is not None:
                try:
                    nid = parse_id(m['text'])
                    r = d.b.find(m['rid'])
                    if r is not None and nid != r.id:
                        self.action_change_id(d, r, nid, m['dup'])
                    close = True
                except (ValueError, BundleError) as e:
                    self.status = str(e)
            imgui.same_line()
            if imgui.button('Cancel', imgui.ImVec2(120, 0)):
                close = True
        elif k == 'texture_options':
            d = self.doc_by_uid(m['doc'])
            r = d.b.find(m['rid']) if d else None
            imgui.text(os.path.basename(m['path']))
            fmts = ['keep the current format', 'BC1 / DXT1 (no or 1-bit alpha)', 'BC2 / DXT3', 'BC3 / DXT5 (smooth alpha)',
                    'uncompressed RGBA']
            imgui.set_next_item_width(300)
            _, m['fmt'] = imgui.combo('format', m['fmt'], fmts)
            imgui.set_next_item_width(300)
            _, m['mips'] = imgui.combo('mip maps', m['mips'], ['as before (full chain if it had mips)', 'full chain', 'none'])
            _, m['srgb'] = imgui.checkbox('sRGB (colour texture)', m['srgb'])
            imgui.text_disabled('DDS files with BC1 / BC2 / BC3 data are stored as they are (no re-compression).')
            if imgui.button('Replace', imgui.ImVec2(120, 0)) and r is not None:
                fmt = [None, raster.BC1, raster.BC2, raster.BC3,
                       raster.RGBA8 if d.b.platform == 'PC' else raster.ARGB8][m['fmt']]
                mips = [None, 'full', 'none'][m['mips']]
                opts = {'fmt': fmt, 'mips': mips, 'srgb': m['srgb']}
                self.modal = None
                imgui.close_current_popup()
                imgui.end_popup()
                self.replace_from_file(d, r, m['path'], opts)
                return
            imgui.same_line()
            if imgui.button('Cancel', imgui.ImVec2(120, 0)):
                close = True
        elif k == 'wave_options':
            d = self.doc_by_uid(m['doc'])
            r = d.b.find(m['rid']) if d else None
            imgui.text(os.path.basename(m['path']) + ':  ' + m['src'])
            imgui.text_disabled('The sound now: ' + m['old'])
            imgui.set_next_item_width(300)
            _, m['rate'] = imgui.combo('sample rate', m['rate'], ['as the sound now', 'as the file (nearest MPEG rate)'])
            imgui.set_next_item_width(300)
            _, m['channels'] = imgui.combo('channels', m['channels'], ['as the sound now', 'as the file', 'mono', 'stereo'])
            imgui.set_next_item_width(300)
            _, m['quality'] = imgui.slider_float('compression', m['quality'], 0.0, 1.0, '%.2f (0 = best quality)')
            imgui.text_disabled('Encoded as EALayer3 (MP3 based), like every sound of the game.')
            if imgui.button('Replace', imgui.ImVec2(120, 0)) and r is not None:
                opts = {'quality': m['quality']}
                if m['rate'] == 1:
                    opts['rate'] = eal3.read_audio(m['path'])[1]
                if m['channels'] == 1:
                    opts['channels'] = eal3.read_audio(m['path'])[0].shape[1]
                elif m['channels'] in (2, 3):
                    opts['channels'] = m['channels'] - 1
                self.modal = None
                imgui.close_current_popup()
                imgui.end_popup()
                self.replace_from_file(d, r, m['path'], opts)
                return
            imgui.same_line()
            if imgui.button('Cancel', imgui.ImVec2(120, 0)):
                close = True
        elif k == 'pick_chunk':
            d = self.doc_by_uid(m['doc'])
            r = d.b.find(m['rid']) if d else None
            imgui.text(f'Replace which chunk of {m["rid"]:#x} with {os.path.basename(m["path"])}?')
            for c in range(4):
                if imgui.button(f'Chunk {c}', imgui.ImVec2(90, 0)) and r is not None:
                    self.replace_from_file(d, r, m['path'], {'chunk': c})
                    close = True
                imgui.same_line()
            if imgui.button('Cancel', imgui.ImVec2(90, 0)):
                close = True
        elif k == 'open_path':
            imgui.text('Path of a .BNDL file:')
            imgui.set_next_item_width(560)
            ch, m['text'] = imgui.input_text('##path', m['text'], imgui.InputTextFlags_.enter_returns_true)
            if imgui.button('Open', imgui.ImVec2(120, 0)) or ch:
                p = m['text'].strip().strip('"')
                if os.path.isfile(p):
                    self.open_path(p)
                    close = True
                else:
                    self.status = f'Not a file: {p}'
            imgui.same_line()
            if imgui.button('Cancel', imgui.ImVec2(120, 0)):
                close = True
        elif k in ('find', 'goto'):
            imgui.text('Id (hex) or part of a name:' if k == 'find' else 'Resource id (hex):')
            imgui.set_next_item_width(360)
            if imgui.is_window_appearing():
                imgui.set_keyboard_focus_here()
            ch, m['text'] = imgui.input_text('##q', m['text'], imgui.InputTextFlags_.enter_returns_true)
            if imgui.button('OK', imgui.ImVec2(120, 0)) or ch:
                if k == 'find':
                    self.find_text = m['text']
                    self.find_everywhere(m['text'])
                else:
                    try:
                        self.goto(parse_id(m['text']), self.cur)
                    except ValueError as e:
                        self.status = str(e)
                close = True
            imgui.same_line()
            if imgui.button('Cancel', imgui.ImVec2(120, 0)):
                close = True
        elif k == 'plate':
            root = m['root']
            imgui.text_wrapped('The plate text ("registration") has its own editor in the game: Easydrive > EDIT LICENSE '
                               'PLATE > REGISTRATION, typed with the keyboard. The game unlocks it at Speed Level 15 in '
                               'multiplayer. Enabling it here makes the menu offer it right away (the locked menu list gets '
                               'the unlocked one\'s items). The bundles are kept once as .orig.')
            imgui.spacing()
            for p, state in m['state']:
                col = {'enabled': imgui.ImVec4(0.4, 0.85, 0.4, 1), 'locked': imgui.ImVec4(0.9, 0.75, 0.35, 1)}.get(
                    state, imgui.ImVec4(0.6, 0.6, 0.6, 1))
                imgui.text_colored(col, {'enabled': 'enabled', 'locked': 'locked (Speed Level 15)'}.get(state, 'not found'))
                imgui.same_line()
                imgui.text_disabled(os.path.relpath(p, root))
            imgui.spacing()
            if imgui.button('Enable plate text editing', imgui.ImVec2(210, 0)):
                try:
                    w = ops.set_plate_editing(root, True)
                    self.status = f'Plate registration editing enabled ({len(w)} bundle(s) changed).'
                except Exception as ex:
                    self.status = f'Failed: {ex}'
                m['state'] = ops.plate_editing_state(root)
            imgui.same_line()
            if imgui.button('Restore (locked)', imgui.ImVec2(150, 0)):
                try:
                    w = ops.set_plate_editing(root, False)
                    self.status = f'Plate registration editing restored to the game\'s rule ({len(w)} bundle(s) changed).'
                except Exception as ex:
                    self.status = f'Failed: {ex}'
                m['state'] = ops.plate_editing_state(root)
            imgui.same_line()
            if imgui.button('Close', imgui.ImVec2(100, 0)):
                close = True
        elif k == 'properties':
            d = self.doc_by_uid(m['doc'])
            if d is None:
                close = True
            else:
                b = d.b
                imgui.text(f'{d.name}: {b.platform}, bnd2 version {b.version}')
                for bit, n in FLAG_NAMES:
                    ch, on = imgui.checkbox(f'{n} ({bit:#x})', bool(b.flags & bit))
                    if ch:
                        d.checkpoint('flags', [])
                        b.flags = (b.flags | bit) if on else (b.flags & ~bit)
                        b.modified = True
                imgui.text_disabled('Change flags only if you know the game accepts them (compressed = zlib).')
                imgui.text('Root resource id (hex):')
                imgui.set_next_item_width(260)
                ch, txt = imgui.input_text('##root', f'{b.root_id:016X}', imgui.InputTextFlags_.chars_hexadecimal
                                           | imgui.InputTextFlags_.enter_returns_true)
                if ch:
                    try:
                        d.checkpoint('root id', [])
                        b.root_id = parse_id(txt)
                        b.modified = True
                    except ValueError as e:
                        self.status = str(e)
                if imgui.button('Close', imgui.ImVec2(120, 0)):
                    close = True
        if close:
            self.modal = None
            imgui.close_current_popup()
        imgui.end_popup()


def ctypes_ptr(addr):
    import ctypes
    return ctypes.c_void_p(addr)


def field_flags(fl):
    parts = []
    if fl & 1:
        parts.append('pointer')
    if fl & 2:
        parts.append('inline array')
    if fl & 8:
        parts.append('array')
    if fl & 16:
        parts.append('objects')
    return ', '.join(parts) or '-'


def fmt_value(v):
    if isinstance(v, float):
        return f'{v:.6g}'
    if isinstance(v, list):
        return '[' + ', '.join(fmt_value(x) for x in v[:16]) + (', ...' if len(v) > 16 else '') + ']'
    if isinstance(v, (bytes, bytearray)):
        return v.hex(' ')
    return str(v)


HELP = """It works like Windows File Explorer, with bundles as folders and resources as files.

Open: the Open button (Ctrl+O), double click a bundle in a folder (add the game folder to the navigation pane
with "Add a folder"), or drop .BNDL files on the window. PC (retail) and PS3 (prototype) bundles both work;
every bundle gets its own tab, and the navigation pane lists its resource types as folders.

Names: resources are stored by number; the Name column shows the real name where the game data has one
(bright), or a name worked out from how the resource is used (dimmer, e.g. "Rock_01 Diffuse", "<car> body LOD0").
Home > Find names (or ... > Find names) scans the game folders once to collect them; add the PS3 prototype
folder too if you have it (its debug data names many retail resources). Hover a name for its source.

Browse: Details or Large icons view (textures show thumbnails), search box, Sort, Back / Forward / Up
(Alt+Left / Alt+Right / Alt+Up). The pane on the right shows the selected resource: textures (zoom, mips,
channels), Genesys objects (every field, editable), Genesys types, text files, the game's strings, colour
cubes, imports and the raw bytes (Hex).

Edit: values in the object view, strings, text, bytes in Hex; Rename (F2) changes a resource id (imports in
the bundle follow); Delete; Ctrl+Z / Ctrl+Y undo and redo. Save (Ctrl+S) keeps the original as .orig the first
time; a bundle saved without changes is byte-identical to the original.

Copy and paste, drag and drop (like Explorer):
- Ctrl+C / Ctrl+X, then Ctrl+V in another bundle: copies / moves resources (PS3 <-> PC converted as needed);
  the copied resources are also on the Windows clipboard as files (paste them into an Explorer folder);
- files copied in Explorer and pasted with Ctrl+V, or dropped on the window: a PNG / DDS / TGA / JPG onto a
  texture replaces it, a .txt onto a text file, a .csv onto the strings, a .bres onto any resource;
  files named <resource id>.<ext> replace those resources; .bres files add resources; bundles open;
- drag resources onto another bundle's tab (or its entry in the navigation pane) to copy them;
- drag resources out of the window into Explorer to save them (textures as PNG or DDS: ... > Options).

Models (Renderable, Model): a 3D view with textures (left drag turns, right drag moves, wheel zooms), LOD
choice for models, Export glTF (.glb with textures, opens in Blender). Shaders, materials and shared textures are
found in the open bundles and in the game's global bundles (SHADERS, GLOBALMATERIALDICTIONARY, ...).

Materials: the shader, the textures by slot (Diffuse, Normal, Specular, ...) with thumbnails, and the shader
constants by name (PbrMaterialDiffuseColour, ...), editable. Go jumps to an open resource; Open opens the
bundle that has it (known after Find names).

Cars: select the VehicleGraphicsSpec of a VEH_* bundle to see the assembled car (body + wheels) in 3D; drag the
wheel positions / scales below the view (track width, wheelbase, ride height, wheel size; Mirror keeps it
symmetric), then Save.

Track units (HAWAII\\TRK_UNIT*): select the InstanceList to see the whole piece of the city in 3D with its props
(World / Collision / World + collision, Neighbours); the PolygonSoupList is the collision, coloured by surface tag.
Shared models come from the DISTRICT and GLOBALRESOURCES bundles (found faster after Find names).
HAWAII\\PVS.BNDL: a map of all track units; click one to open it.

License plate text (... menu): enables the game's own plate registration editor without Speed Level 15.

Soundtrack editor (... menu): add songs from audio files, edit artist / title, replace audio, remove, choose
playlists, reorder; Save writes SONGS.BNDL, the language strings and the .SPS files (originals kept as .orig).

Sound streams (.SPS files: music, ambience, sequence and video sound): open them like bundles; play, export WAV,
replace and save. ... > Export sound streams (.SPS) of a folder as WAV converts a whole folder.

Vehicle list (VEHICLES\\VEHICLELIST): every car with its name, manufacturer, speed, power, ratings, ...; select a
car to edit its fields; Duplicate / Delete / arrows change the rows. Export / Import CSV (Excel with ';' and
decimal commas works too).

Sounds (Wave): Play / Pause / Stop and a waveform with the play position (click it to jump); Export WAV; Replace (or drop) a WAV / FLAC / OGG / MP3 /
AIFF file: it is encoded as EALayer3 like every sound of the game (sample rate and channels as the old sound
by default). Streamed sounds are read from and written to their .SPS files (the original is kept as .orig).

Export / Replace / Import buttons: DDS, PNG, WAV, text, CSV, .bres (one resource with all its data), raw chunks.
... (See more) > Extract all / Import resources from folder work on whole bundles.

Convert: saves a PS3 bundle for PC or a PC bundle for PS3: textures, Genesys types and objects, colour cubes,
text files and strings are converted; meshes, materials and shaders are platform-specific and left out (the
report lists them).

Themes: ... > Options (dark is the default, light is available)."""

ABOUT = f"""{APP} {VERSION}
Bundle explorer for Need for Speed: Most Wanted (2012): PC retail and PS3 prototype (bnd2 version 5).

Built with Dear ImGui (imgui-bundle). No game data is included; the program works on your own files.
Always keep backups of the game files you edit."""


def runner_params(app, title=f'{APP} {VERSION}'):
    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = title
    params.app_window_params.window_geometry.size = (1500, 900)
    params.imgui_window_params.show_menu_bar = False
    params.imgui_window_params.show_menu_app = False
    params.imgui_window_params.show_menu_view = False
    params.ini_folder_type = hello_imgui.IniFolderType.app_user_config_folder
    params.ini_filename = 'BNDLExplorer/imgui.ini'
    params.imgui_window_params.default_imgui_window_type =         hello_imgui.DefaultImGuiWindowType.provide_full_screen_window
    dark = app.cfg.get('theme', 'dark') == 'dark'
    params.imgui_window_params.background_color = imgui.ImVec4(*(theme.DARK if dark else theme.LIGHT)['bg'])
    params.callbacks.post_init = app.post_init
    params.callbacks.load_additional_fonts = theme.load_fonts
    params.callbacks.setup_imgui_style = lambda: theme.apply(dark)
    return params


def run(paths=(), screenshot=None, frames=12, select=None, tab=None, expand=False, view=None, folder=None,
        type_filter=None):
    immvision.use_rgb_color_order()
    app = App(paths, select)
    app.test_tab = tab
    app.expand_all = expand
    if view:
        app.cfg['view'] = view
    if folder:
        app.go_folder(folder)
    if type_filter is not None and app.cur is not None:
        app.cur.type_filter = type_filter
    params = runner_params(app)
    count = [0]

    def gui():
        app.gui()
        if screenshot:
            ready = app.preview_ready()
            if ready and app.job is None and not app.thumbs.pending:
                count[0] += 1
            if count[0] > frames:
                hello_imgui.get_runner_params().app_shall_exit = True
                app.exit_ok = True

    params.callbacks.show_gui = gui
    hello_imgui.run(params)
    if app.names.dirty:
        try:
            app.names.save()
        except OSError:
            pass
    if screenshot:
        img = hello_imgui.final_app_window_screenshot()
        from PIL import Image
        Image.fromarray(np.asarray(img)[..., :3]).save(screenshot)
