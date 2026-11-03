"""BNDL Explorer: view, edit, import / export and convert the .BNDL bundles of NFS Most Wanted (2012),
PC retail and PS3 prototype. Dear ImGui (imgui-bundle) front end."""
import collections
import itertools
import math
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

from . import (convert, dragdrop, eal3, event_data, filedialog, gameplay_data, genesys, gltf, hsm, mesh, ops,
               raster, resfile, textfile, theme, traffic, ts_names, compare)
from . import luabc
from . import luafile
from . import vehiclelist as VL
from . import zonelist as ZL
from . import spsfile
from . import platetext
from . import ginsu
from . import gnames
from . import datfile
from . import gjson
from . import gwrite
from . import genesys_insight
from . import knownids
from . import anim
from .anim_ui import AnimUI
from . import fbx
from . import meshimport
from . import roots
from .audioplay import Player
from .soundtrack_ui import SoundtrackUI
from .port_ui import PortUI
from .ui_editor import UIEditorUI
from .explorer import Browser, ExplorerUI
from .thumbs import Thumbs
from .bundle import FLAG_NAMES, Bundle, BundleError
from .genesys import Node, Ref, TypeDB, leaf_kind
from .labels import Labels, config_dir
from .localised import StringTable
from . import names as N
from .restypes import (T_CUBE, T_GOBJECT, T_GTYPE, T_LUA_CODE, T_STRINGS, T_TEXT, T_TEXTURE,
                       name as type_name)

APP = 'BNDL Explorer'
VERSION = '0.26'
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
        self.edits = 0                      # counts changes (views drawn from several resources re-read on it)

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
        self.edits += 1
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


MODEL_IMPORT_TYPES = (0x05, 0x51, 0x106, 0x50)     # Renderable, Model, car, track unit: Import FBX
EVENT_ID = 0x655d3f5f          # the unique id field of events / challenges (their names: ts_names.EVENT_NAMES)


def _clock(seconds):
    """m:ss.s"""
    seconds = max(0.0, seconds)
    return f'{int(seconds // 60)}:{seconds % 60:04.1f}'


def waveform(audio, cols=1024):
    """(min, max) per column of a sound, for drawing its waveform."""
    mono = audio.astype(np.float32).mean(1) / 32768.0
    parts = np.array_split(mono, cols) if len(mono) >= cols else [mono]
    return (np.array([p.min() if len(p) else 0 for p in parts]),
            np.array([p.max() if len(p) else 0 for p in parts]))


class App(ExplorerUI, SoundtrackUI, AnimUI, PortUI, UIEditorUI):
    def __init__(self, paths=(), select=None):
        self.dashboard_icons = False
        self.tab_content_transitions = {}
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
                      'nb': False, 'progress': '', 'cm': False, 'dent': 0.0, 'dented': None}
        self.zmap = {'key': None}
        self.player = Player()
        self.st_ui = None                # soundtrack editor window state
        self.port_ui = None              # Port window state (port_ui.py)
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
        self.labels.found = self.names.gnames
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
        self.tex_full = None             # the full-size texture window: {'doc', 'rid', ...}
        self.tex_cache = collections.OrderedDict()   # decoded textures of the 3D views (texture_image)
        self.tex_cache_lock = threading.Lock()
        self.model_tex = None            # the textures-of-a-model window: {'doc', 'rid'}
        self.edit_gen = 0                # counts edits in every open bundle (views built from several bundles)
        self.tex_lock = threading.Lock()
        self.gcache = {}
        self.genesys_insight_state = {}
        self.genesys_string_edit = None
        self.text_edit = {'key': None, 'text': ''}
        self.lua_edit = {'key': None, 'text': '', 'encoding': 'utf-8', 'dirty': False}
        self.lua_bc = {}
        self.str_filter = ''
        self.str_cache = {'key': None, 'table': None}
        self.hsm_cache = {'key': None, 'result': None, 'error': None}
        self.hsm_filter = ''
        self.event_view_state = {}
        self.traffic_cache = {'key': None, 'result': None, 'error': None, 'zoom': 1.0, 'pan': [0.0, 0.0]}
        self.gameplay_view = {'data': None, 'folder': '', 'open': False, 'race': '', 'filter': '',
                              'zoom': 1.0, 'pan': [0.0, 0.0], 'custom_route': []}
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

    def dashboard_icon(self, kind, size=17, color=None):
        if not self.dashboard_icons:
            return False
        from . import dash_icons

        if color is None:
            color = imgui.get_style_color_vec4(imgui.Col_.text_disabled)
        height = max(imgui.get_frame_height(), size + 4)
        pos = imgui.get_cursor_screen_pos()
        center = imgui.ImVec2(pos.x + (size + 4) * 0.5, pos.y + height * 0.5)
        dash_icons.draw(imgui.get_window_draw_list(), kind, center, size, imgui.get_color_u32(color))
        imgui.dummy(imgui.ImVec2(size + 4, height))
        return True

    def dashboard_resource_icon(self, type_id, size=24):
        if not self.dashboard_icons:
            return False
        from . import dash_icons

        height = max(imgui.get_frame_height(), size + 4)
        pos = imgui.get_cursor_screen_pos()
        center = imgui.ImVec2(pos.x + (size + 4) * 0.5, pos.y + height * 0.5)
        dash_icons.resource_badge(imgui.get_window_draw_list(), type_id, center, size)
        imgui.dummy(imgui.ImVec2(size + 4, height))
        return True

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
            peers = [other for other in self.docs if other is not d and compare.same_bundle_name(path, other.path)]
            if peers:
                self.status += f' Same-name bundle open: {peers[0].path}. Use … > Compare same-name bundle to see differences.'
        return d

    def compare_same_name(self, d, other):
        rows = compare.bundle_differences(d.b, other.b, self.types, self.labels)
        self.modal = {'kind': 'compare', 'left': d.uid, 'right': other.uid, 'rows': rows}

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
        d.edits += 1
        self.edit_gen += 1
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
            if target == 'PC' and (rep['skipped'] or rep['failed']):
                self.modal['action'] = ('Port into a game bundle...', lambda: self.open_port(d))
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
        m = {'kind': 'plate', 'root': root, 'state': ops.plate_editing_state(root), 'gl': {}, 'atlas': None, 'texts': None}
        try:
            m['exe'] = platetext.current(root)
            m['texts'] = {orig: cur for _, orig, cur, _ in m['exe']}
        except (OSError, platetext.PlateError, struct.error) as ex:
            m['exe'] = []
            m['exe_error'] = str(ex)
        try:
            vb = Bundle.open(os.path.join(root, 'VEHICLES', 'VEHICLETEX.BNDL'))
            m['atlas'] = raster.decode(vb.find(platetext.ATLAS_ID), vb.platform)
        except Exception:
            m['atlas'] = None
        self.modal = m

    def plate_dialog(self, m):
        """The License plates dialog: the car packs' plate texts (NFS13.exe) and the in-game registration editor.
        Returns True to close."""
        root = m['root']
        imgui.text('Plate texts of the car packs')
        imgui.text_wrapped('Every car shows the text of its content pack (the BMW M3 GTR of the NFS Heroes pack: NFS HERO). '
                           'Type up to 8 characters: A-Z, 1-9, - and space (0 is shown as O). They are written into '
                           'NFS13.exe (every copy of the table); the original exe is kept once as NFS13.exe.orig.')
        if m.get('exe_error'):
            imgui.text_colored(imgui.ImVec4(1, 0.5, 0.4, 1), m['exe_error'])
        elif m['texts'] is not None:
            flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v
            if imgui.begin_table('plates', 3, flags):
                imgui.table_setup_column('Pack', imgui.TableColumnFlags_.width_fixed, 190)
                imgui.table_setup_column('Text', imgui.TableColumnFlags_.width_fixed, 120)
                imgui.table_setup_column('Preview', imgui.TableColumnFlags_.width_fixed, 230)
                for name, orig, cur, copies in m['exe']:
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.text(name)
                    imgui.text_disabled(f'original: {orig}  ({copies} copies)')
                    imgui.table_next_column()
                    imgui.set_next_item_width(-1)
                    ch, txt = imgui.input_text(f'##pt{orig}', m['texts'][orig].strip(), imgui.InputTextFlags_.chars_uppercase)
                    if ch:
                        m['texts'][orig] = txt[:platetext.LETTERS]
                    try:
                        shown = platetext.clean(m['texts'][orig])
                        err = None
                    except platetext.PlateError as ex:
                        shown, err = None, str(ex)
                    imgui.table_next_column()
                    if err:
                        imgui.text_colored(imgui.ImVec4(1, 0.5, 0.4, 1), err)
                    elif m['atlas'] is not None:
                        gl = m['gl'].get(shown)
                        if gl is None:
                            gl = m['gl'][shown] = immvision.GlTexture(platetext.preview(m['atlas'], shown))
                        imgui.image(imgui.ImTextureRef(gl.texture_id), imgui.ImVec2(224, 56))
                    else:
                        imgui.text(shown)
                imgui.end_table()
            if imgui.button('Write the plate texts', imgui.ImVec2(200, 0)):
                try:
                    n = platetext.write(root, dict(m['texts']))
                    m['exe'] = platetext.current(root)
                    self.status = (f'Plate texts written into NFS13.exe ({n} places); the original is NFS13.exe.orig.'
                                   if n else 'The plate texts are already like that.')
                except (OSError, platetext.PlateError) as ex:
                    self.status = f'Plate texts not written: {ex}'
            imgui.same_line()
            imgui.begin_disabled(not os.path.isfile(platetext.exe_path(root) + '.orig'))
            if imgui.button('Restore the original exe', imgui.ImVec2(200, 0)):
                try:
                    platetext.restore(root)
                    m['exe'] = platetext.current(root)
                    m['texts'] = {orig: cur for _, orig, cur, _ in m['exe']}
                    self.status = 'NFS13.exe restored from NFS13.exe.orig.'
                except (OSError, platetext.PlateError) as ex:
                    self.status = str(ex)
            imgui.end_disabled()
        imgui.spacing()
        imgui.separator()
        imgui.text("The player's own plate text (registration)")
        imgui.text_wrapped('The game also has an editor for your own plate text: Easydrive > EDIT LICENSE PLATE > '
                           'REGISTRATION, typed with the keyboard, unlocked at Speed Level 15 in multiplayer. Enabling it '
                           'here makes the menu offer it right away. The bundles are kept once as .orig.')
        for p, state in m['state']:
            col = {'enabled': imgui.ImVec4(0.4, 0.85, 0.4, 1), 'locked': imgui.ImVec4(0.9, 0.75, 0.35, 1)}.get(
                state, imgui.ImVec4(0.6, 0.6, 0.6, 1))
            imgui.text_colored(col, {'enabled': 'enabled', 'locked': 'locked (Speed Level 15)'}.get(state, 'not found'))
            imgui.same_line()
            imgui.text_disabled(os.path.relpath(p, root))
        if imgui.button('Enable registration editing', imgui.ImVec2(210, 0)):
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
        imgui.spacing()
        imgui.separator()
        return imgui.button('Close', imgui.ImVec2(100, 0))

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
        folder = filedialog.pick_folder(
            'Import resources from this folder (.bres files, <id>.dds / .png / .txt / .lua / .csv)'
        )
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
            elif kind == 'luac':
                p = filedialog.save_file('Export Lua bytecode', base + '.luac', filedialog.LUA, 'luac')
                if p:
                    ops.write_file(p, textfile.read(res, d.b.e))
            elif kind == 'lua':
                p = filedialog.save_file('Export Lua source', base + '.lua', filedialog.TEXT, 'lua')
                if p:
                    luafile.decode(res.data(0))
                    ops.write_file(p, res.data(0))
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
            elif kind == 'fbx':
                p = filedialog.save_file('Export model as FBX', base + '.fbx', filedialog.FBX, 'fbx')
                if p:
                    n, nt = self.export_fbx(d, res, p)
                    self.status = f'Exported {n} mesh(es) and {nt} texture(s) to {p}.'
            elif kind == 'wav':
                from . import eal3
                p = filedialog.save_file('Export sound as WAV', base + '.wav', filedialog.WAV, 'wav')
                if p and res.type == ginsu.T_GINSU:
                    g = ginsu.read(res, d.b.e)
                    ops.write_file(p, eal3.wav_bytes(g.pcm().reshape(-1, 1), g.rate))
                elif p:
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
        if ext == '.fbx' and res.type in MODEL_IMPORT_TYPES:
            return self.import_fbx(d, res, path)
        if res.type == ginsu.T_GINSU and ext in eal3.AUDIO_EXT:
            self.ginsu_replace_dialog(d, res, path)
            return False
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
            elif res.type == T_TEXT and ext in ('.txt', '.json', '.xml', '.lua', '.luac'):
                if luabc.is_bytecode(data):
                    luabc.read(data)               # a damaged chunk is not put into the game
                ops.replace_text(d.b, res, data)
                msg = f'Text {res.id:#x} replaced ({len(data)} bytes).'
            elif res.type == T_LUA_CODE and ext == '.lua':
                luafile.decode(data)
                res.set_data(0, data)
                msg = f'Lua source {res.id:#x} replaced ({len(data)} bytes).'
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
               T_LUA_CODE: filedialog.TEXT,
               T_STRINGS: filedialog.CSV, 0x81: filedialog.AUDIO, VL.T_VEHICLELIST: filedialog.CSV,
               ginsu.T_GINSU: filedialog.AUDIO,
               **{t: filedialog.FBX + filedialog.RES for t in MODEL_IMPORT_TYPES}}.get(res.type, filedialog.RES)
        p = filedialog.open_file(f'Replace {type_name(res.type)} {res.id:#x}', self.cfg.get('last_import', ''), flt)
        if not p:
            return
        self.remember('last_import', p)
        if res.type == T_TEXTURE:
            inf = raster.info(res, d.b.platform)
            self.modal = {'kind': 'texture_options', 'doc': d.uid, 'rid': res.id, 'path': p,
                          'fmt': 0, 'mips': 0, 'srgb': inf.srgb}
        elif res.type == ginsu.T_GINSU and os.path.splitext(p)[1].lower() in eal3.AUDIO_EXT:
            self.ginsu_replace_dialog(d, res, p)
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
            paths = filedialog.open_files('Import resources (.bres, .dat)', self.cfg.get('last_import', ''),
                                          filedialog.RES)
        if not paths:
            return
        dats = [p for p in paths if p.lower().endswith('.dat')]
        if dats:
            rows = []
            for p in dats:
                rid = datfile.id_from_name(p, d.b)
                try:
                    with open(p, 'rb') as f:
                        t = datfile.guess_type(f.read(), rid, d.b) if rid is not None else None
                except OSError:
                    t = None
                rows.append({'path': p, 'id': f'{rid:016X}' if rid is not None else '',
                             'type': datfile.CHOICES.index(t) if t in datfile.CHOICES else 0,
                             'other': t if t is not None and t not in datfile.CHOICES else None})
            self.modal = {'kind': 'dat_import', 'doc': d.uid, 'rows': rows,
                          'bres': [p for p in paths if p not in dats]}
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
        fresh = [r.id for r in news if r.id not in existing and r.type not in (T_GTYPE,)]
        idle = ops.unused(d.b, fresh)
        if idle:
            self.modal = {'kind': 'message', 'title': 'Resources added', 'text':
                          f'{len(news)} resource(s) added to {d.name} (sorted in with the others, as the game '
                          f'expects).' + chr(10) + chr(10) + f'{len(idle)} of them are not used by any resource of '
                          f'this bundle: ' + ', '.join(ops.id_text(x) for x in idle[:8])
                          + (' ...' if len(idle) > 8 else '') + '. The game loads them with the bundle but only '
                          'uses a resource something refers to: point an import at it (Imports tab of the resource '
                          'that should use it), or replace an existing resource instead of adding a new one. '
                          '(Resources of other bundles may refer to it too.)'}

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
            if os.path.splitext(p)[1].lower() == '.dat':       # raw resource: this resource's content
                try:
                    r, notes = datfile.load(p, d.b, target.id, target.type)
                except (ValueError, OSError) as ex:
                    self.status = f'{os.path.basename(p)}: {ex}'
                    return
                self._confirmed_replace = True
                self.add_resources(d, [r], f'replace {target.id:#x} from {os.path.basename(p)}')
                notes += datfile.check(d.b, self.types, r) if r.type in (T_GTYPE, T_GOBJECT) else []
                if notes:
                    self.modal = {'kind': 'message', 'title': 'Import .dat', 'text': chr(10).join(notes)}
                return
            self.focus = (d.uid, target.id)
            d.sel = {target.id}
            self.replace_from_file(d, target, p)
            return
        bres = [p for p, m in others if m == resfile.MAGIC or p.lower().endswith('.dat')]
        if bres:
            self.action_import_resources(d, bres)
        rest = [p for p, m in others if m != resfile.MAGIC and not p.lower().endswith('.dat')]
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
        dashboard = getattr(self, 'dashboard_ui', None)
        hex_dirty = [document for document in getattr(dashboard, 'hex_documents', ())
                     if document.modified]
        if p.app_shall_exit and not self.exit_ok and (
                any(d.modified for d in self.docs) or st_dirty or hex_dirty):
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
        known = knownids.GAMECHANGER.get(gc) if gc is not None else None
        if known is None and gc is not None:
            mapped = ts_names.EVENT_NAMES.get(gc) or ts_names.CAR_NAMES.get(gc)
            known = (mapped, 'TypeScript event / car') if mapped else None
        if known is None and r.type == 0x53:
            sh = knownids.SHADERS.get(r.id & 0xFFFFFFFF)
            known = (sh, 'shader') if sh else None
        if known is not None:
            return (known[0], 'exact', f'{known[0]}\n(known {known[1]} id {r.id & 0xFFFFFFFF})')
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
            self.labels.found = db.gnames
            self.gcache.clear()
            gtext = (f'\n\nGenesys field names: {st["gnamed"]} of {st["gfields"]} found (the real names: their hashes '
                     'match).' if st.get('gfields') else '')
            self.modal = {'kind': 'message', 'title': 'Resource names',
                          'text': f'Scanned {st["files"]} bundles in {st["seconds"]} s.\n\n'
                                  f'{st["resources"]} different resources, {st["named"]} of them named '
                                  f'({st["exact"]} exact names from the game data, the rest from object names).\n'
                                  'Other resources get names from what uses them (models, materials, vehicles).'
                                  + gtext}
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

    def begin_tab_content_transition(self, group, tab):
        if not self.dashboard_icons:
            return False
        now = imgui.get_time()
        state = self.tab_content_transitions.get(group)
        if state is None:
            self.tab_content_transitions[group] = (tab, now - 0.62)
            progress = 1.0
        elif state[0] != tab:
            self.tab_content_transitions[group] = (tab, now)
            progress = 0.0
        else:
            progress = min(1.0, max(0.0, (now - state[1]) / 0.62))
        eased = 1 - (1 - progress) ** 3
        content_pos = imgui.get_cursor_screen_pos()
        imgui.set_cursor_screen_pos(imgui.ImVec2(content_pos.x + 16 * (1 - eased), content_pos.y))
        imgui.push_style_var(imgui.StyleVar_.alpha, 0.10 + 0.90 * eased)
        return True

    @staticmethod
    def end_tab_content_transition(animated):
        if animated:
            imgui.pop_style_var()

    # -- details panel ----------------------------------------------------------------------------------------
    def details_panel(self):
        d, r = self.focused()
        if r is None or d is not self.cur:
            if self.cur is not None:
                self.bundle_summary(self.cur)
            return
        if self.dashboard_icons:
            self.dashboard_resource_icon(r.type, 40)
        else:
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
        dashboard = getattr(self, 'dashboard_ui', None)
        if dashboard is not None:
            imgui.spacing()
            label = 'Collapse' if dashboard.inspector_expanded else 'Expand'
            if imgui.small_button(f'{label} inspector'):
                dashboard.inspector_expanded = not dashboard.inspector_expanded
        if r.missing:
            imgui.text_colored(imgui.ImVec4(1, 0.5, 0.3, 1), 'The data of this resource is not in the file (truncated file).')
            return
        tab = self.test_tab
        self.test_tab = None
        sel = [imgui.TabItemFlags_.set_selected if tab == i else 0 for i in range(4)]
        if imgui.begin_tab_bar('details'):
            if imgui.begin_tab_item_simple('Preview', sel[0]):
                animated = self.begin_tab_content_transition('details', 'preview')
                imgui.begin_child('preview')
                try:
                    self.preview(d, r)
                except Exception as e:
                    traceback.print_exc()
                    imgui.text_colored(imgui.ImVec4(1, 0.4, 0.3, 1), f'Cannot show this resource: {e}')
                imgui.end_child()
                self.end_tab_content_transition(animated)
                imgui.end_tab_item()
            if imgui.begin_tab_item_simple('Info', sel[1]):
                animated = self.begin_tab_content_transition('details', 'info')
                self.info_tab(d, r)
                self.end_tab_content_transition(animated)
                imgui.end_tab_item()
            if imgui.begin_tab_item_simple(f'Imports ({r.import_count})###imports', sel[2]):
                animated = self.begin_tab_content_transition('details', 'imports')
                self.imports_tab(d, r)
                self.end_tab_content_transition(animated)
                imgui.end_tab_item()
            if imgui.begin_tab_item_simple('Hex', sel[3]):
                animated = self.begin_tab_content_transition('details', 'hex')
                self.hex_tab(d, r)
                self.end_tab_content_transition(animated)
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
        dash = getattr(self, 'dashboard', None)
        if dash is not None:
            imgui.same_line()
            if imgui.button('Edit in Hex Editor...') and not d.b.truncated:
                dash.open_resource_in_hex(d, r, self.hex_chunk)
            if imgui.is_item_hovered():
                imgui.set_tooltip('Open this chunk in the Hex Editor (click bytes to edit, insert / delete, search, '
                                  'compare); its Save writes the chunk back into the resource')
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
        elif t == T_LUA_CODE:
            self.lua_view(d, r)
        elif t == T_STRINGS:
            self.strings_view(d, r)
        elif t == T_CUBE:
            self.cube_view(d, r)
        elif t == 0x81:
            self.wave_view(d, r)
        elif t == ginsu.T_GINSU:
            self.ginsu_view(d, r)
        elif t in (anim.T_ANIMLIST, anim.T_ANIMATION, anim.T_SKELETON):
            self.anim_view(d, r)
        elif t in mesh.MODEL_TYPES:
            self.model_view(d, r)
        elif t == 0x02:
            self.material_view(d, r)
        elif t == VL.T_VEHICLELIST:
            self.vehicle_list_view(d, r)
        elif t == ZL.T_ZONELIST:
            self.zone_map_view(d, r)
        elif t == hsm.T_HSM:
            self.hsm_view(d, r)
        elif t == traffic.T_TRAFFIC:
            self.traffic_view(d, r)
        else:
            imgui.text_wrapped(f'{type_name(t)}: no viewer for this type yet. The Imports and Hex tabs show its data; '
                               'Export / Replace work for every type (.bres or raw chunks).')

    def hsm_view(self, d, r):
        data = r.data(0)
        key = (d.uid, r.id, id(data))
        if self.hsm_cache['key'] != key:
            self.hsm_cache = {'key': key, 'result': None, 'error': None}
            try:
                self.hsm_cache['result'] = hsm.parse(data)
            except hsm.HSMError as exc:
                self.hsm_cache['error'] = str(exc)
        parsed = self.hsm_cache['result']
        if parsed is None:
            imgui.text_colored(imgui.ImVec4(1, 0.4, 0.3, 1),
                               f'Cannot parse HSMData: {self.hsm_cache["error"]}')
            return

        imgui.text(f'HSMData version {parsed["version"]}: {len(parsed["states"])} states, '
                   f'{len(parsed["data_definitions"])} data definitions, '
                   f'{len(parsed["behaviours"])} behaviour interfaces')
        imgui.text_disabled('Parsed from the selected BNDL resource; the original bytes remain unchanged.')
        if imgui.button('Export HSM report...'):
            base = os.path.splitext(r.name or f'{r.id:016X}')[0] + '.txt'
            path = filedialog.save_file('Export HSM report', base, filedialog.TEXT, 'txt')
            if path:
                try:
                    with open(path, 'w', encoding='utf-8', newline='') as f:
                        f.write(hsm.format_text(parsed))
                    self.status = f'Exported HSM report: {path}'
                except OSError as exc:
                    self.status = f'Cannot export HSM report: {exc}'
        imgui.same_line()
        imgui.set_next_item_width(220)
        _, self.hsm_filter = imgui.input_text('Filter', self.hsm_filter)
        filter_text = self.hsm_filter.casefold()

        if imgui.begin_tab_bar('hsmviews'):
            if imgui.begin_tab_item(f'States ({len(parsed["states"])})')[0]:
                if not parsed['top_states']:
                    imgui.text_disabled('No top-level state links.')
                for top in parsed['top_states']:
                    imgui.text(f'Top-level state {top["definition_id"]} / behaviour {top["behaviour_id"]} '
                               f'/ instance {top["instance_id"]}')
                for index, state in enumerate(parsed['states']):
                    if filter_text and filter_text not in f'{state["name"]} {state["id"]} {state["behaviour_id"]}'.casefold():
                        continue
                    label = f'{state["name"] or "(unnamed)"}  [{state["id"]}]##hsm.state.{index}'
                    if imgui.tree_node_ex(label, imgui.TreeNodeFlags_.span_full_width):
                        imgui.text(f'Behaviour id: {state["behaviour_id"]}; fallback: {state["fallback"]}')
                        if state['data_definition']:
                            imgui.text(f'Data: {state["data_definition"]["name"]} '
                                       f'({state["data_definition"]["path"]})')
                        for entry in state['entries']:
                            imgui.text(f'Entry {entry["id"]} (action {entry["action"]})')
                            for spawn in entry['spawns']:
                                imgui.bullet_text(f'{spawn["name"] or "(unnamed)"} [{spawn["state_id"]}]')
                        for child in state['exits']:
                            imgui.text(f'Child exit: {child["name"] or "(unnamed)"} [{child["state_id"]}]')
                            for point in child['points']:
                                imgui.bullet_text(f'{point["parent_id"]} -> {point["child_id"]} '
                                                  f'(action {point["action"]})')
                                for spawn in point['spawns']:
                                    imgui.text(f'    -> {spawn["name"] or "(unnamed)"} [{spawn["state_id"]}]')
                        for parameter in state['parameters']:
                            imgui.text_wrapped(f'Parameter {parameter["id"]} ({parameter["type"]}): '
                                               f'{parameter["values"]}')
                        imgui.tree_pop()
                imgui.end_tab_item()
            if imgui.begin_tab_item(f'Data ({len(parsed["data_definitions"])})')[0]:
                for index, definition in enumerate(parsed['data_definitions']):
                    if filter_text and filter_text not in (
                            f'{definition["name"]} {definition["path"]} {definition["id"]}'.casefold()):
                        continue
                    if imgui.tree_node_ex(
                            f'{definition["name"] or "(unnamed)"}  [{definition["id"]}]##hsm.data.{index}',
                            imgui.TreeNodeFlags_.span_full_width):
                        imgui.text_wrapped(f'Path: {definition["path"]}')
                        imgui.text_wrapped(definition['data'])
                        imgui.tree_pop()
                imgui.end_tab_item()
            if imgui.begin_tab_item(f'Behaviours ({len(parsed["behaviours"])})')[0]:
                for index, behaviour in enumerate(parsed['behaviours']):
                    if filter_text and filter_text not in f'{behaviour["name"]} {behaviour["id"]}'.casefold():
                        continue
                    if imgui.tree_node_ex(
                            f'{behaviour["name"] or "(unnamed)"}  [{behaviour["id"]}]##hsm.behaviour.{index}',
                            imgui.TreeNodeFlags_.span_full_width):
                        imgui.text(f'{len(behaviour["entries"])} entry point(s), '
                                   f'{len(behaviour["exits"])} exit point(s)')
                        for entry in behaviour['entries']:
                            imgui.bullet_text(f'Entry: {entry}')
                        for exit_id in behaviour['exits']:
                            imgui.bullet_text(f'Exit: {exit_id}')
                        for parameter in behaviour['parameters']:
                            imgui.text_wrapped(f'Parameter {parameter["id"]} ({parameter["type"]}): '
                                               f'{parameter["values"]}')
                        imgui.tree_pop()
                imgui.end_tab_item()
            imgui.end_tab_bar()
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
        if self.dashboard_icon('search', 15):
            imgui.same_line(0, 2)
            fit_label = 'Fit'
        else:
            fit_label = (f'{theme.I.ICON_FA_EXPAND if hasattr(theme.I, "ICON_FA_EXPAND") else theme.I.ICON_FA_MAGNIFYING_GLASS}  Fit')
        if imgui.button(fit_label):
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
        imgui.same_line(0, 12)
        if self.dashboard_icon('expand', 15):
            imgui.same_line(0, 2)
            full_size_label = 'Full size'
        else:
            full_size_label = f'{theme.I.ICON_FA_UP_RIGHT_AND_DOWN_LEFT_FROM_CENTER}  Full size'
        if imgui.button(full_size_label):
            self.open_texture_window(d, r)
        if imgui.is_item_hovered():
            imgui.set_tooltip('Show the texture at its full resolution in a big window (or double click it)')
        imgui.same_line(0, 2)
        if imgui.button('Replace...') and not b.truncated:
            self.action_replace(d, r)
        if imgui.is_item_hovered():
            imgui.set_tooltip('Replace it with a PNG / JPG / TGA / BMP / DDS (any size; format, mip maps and sRGB '
                              'can be chosen); or drop an image file on the preview')
        imgui.same_line(0, 2)
        if imgui.button('Export...'):
            self.action_export(d, r)
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
        if mi.is_mouse_hovering and imgui.is_mouse_double_clicked(imgui.MouseButton_.left):
            self.open_texture_window(d, r)
        self.pixel_line(img, mi, 'Mouse wheel: zoom, drag: pan, double click: full size')

    @staticmethod
    def pixel_line(img, mi, hint):
        h, w = img.shape[:2]
        if mi.is_mouse_hovering:
            x, y = int(mi.mouse_position[0]), int(mi.mouse_position[1])
            if 0 <= x < w and 0 <= y < h:
                px = img[y, x]
                vals = '  '.join(f'{c} {int(v)}' for c, v in zip('RGBA', px))
                imgui.text(f'x {x}, y {y}:  {vals}')
            else:
                imgui.text_disabled(' ')
        else:
            imgui.text_disabled(hint)

    # -- full-size texture window ---------------------------------------------------------------------------
    def open_texture_window(self, d, r):
        self.tex_full = {'doc': d.uid, 'rid': r.id, 'focus': True, 'mip': 0, 'face': 0, 'channel': -1,
                         'key': None, 'img': None, 'err': None, 'params': None, 'zoom': 'auto'}

    def decode_texture(self, st, d, r):
        """(image or None while decoding, error) of st's mip / face of a texture, decoded on a thread."""
        key = (d.uid, r.id, id(r.data(0)), id(r.data(d.b.gfx_chunk)), st['mip'], st['face'])
        if st.get('key') != key:
            st.update(key=key, img=None, err=None, params=None)
            plat, mip, face = d.b.platform, st['mip'], st['face']

            def work():
                try:
                    img = np.ascontiguousarray(raster.decode(r, plat, mip, face))
                    with self.tex_lock:
                        if st.get('key') == key:
                            st['img'] = img
                except Exception as e:
                    with self.tex_lock:
                        if st.get('key') == key:
                            st['err'] = str(e)

            threading.Thread(target=work, daemon=True).start()
        with self.tex_lock:
            return st.get('img'), st.get('err')

    def texture_window(self):
        """The texture at full resolution in a window over the whole app (1:1 to start, wheel zooms)."""
        st = self.tex_full
        if not st:
            return
        d = self.doc_by_uid(st['doc'])
        r = d.b.find(st['rid']) if d is not None else None
        if r is None or r.type != T_TEXTURE:
            self.tex_full = None
            return
        inf = raster.info(r, d.b.platform)
        st['mip'] = min(st['mip'], inf.mips - 1)
        st['face'] = min(st['face'], inf.faces - 1)
        vp = imgui.get_main_viewport()
        imgui.set_next_window_pos(imgui.ImVec2(vp.work_pos.x + 16, vp.work_pos.y + 16))
        imgui.set_next_window_size(imgui.ImVec2(vp.work_size.x - 32, vp.work_size.y - 32))
        if st['focus']:
            imgui.set_next_window_focus()
            st['focus'] = False
        flags = (imgui.WindowFlags_.no_collapse | imgui.WindowFlags_.no_move | imgui.WindowFlags_.no_resize
                 | imgui.WindowFlags_.no_saved_settings | imgui.WindowFlags_.no_scrollbar)
        _, keep = imgui.begin(f'{self.display_name(d, r)[0]}  -  {inf.describe()}###texfull', True, flags)
        if not keep or (imgui.is_window_focused() and imgui.is_key_pressed(imgui.Key.escape)):
            imgui.end()
            self.tex_full = None
            return
        zoom = None
        if imgui.button('Fit'):
            zoom = 'fit'
        imgui.same_line(0, 2)
        if imgui.button('1:1'):
            zoom = 'one'
        imgui.same_line(0, 2)
        if imgui.button('2:1'):
            zoom = 2.0
        imgui.same_line(0, 2)
        if imgui.button('4:1'):
            zoom = 4.0
        imgui.same_line(0, 12)
        for c, lbl in ((-1, 'RGBA'), (0, 'R'), (1, 'G'), (2, 'B'), (3, 'A')):
            on = st['channel'] == c
            if on:
                imgui.push_style_color(imgui.Col_.button, imgui.get_style_color_vec4(imgui.Col_.header))
            if imgui.button(f'{lbl}##fch{c}'):
                st['channel'] = c
                zoom = zoom or 'keep'
            if on:
                imgui.pop_style_color()
            imgui.same_line(0, 2)
        if inf.mips > 1:
            imgui.same_line(0, 12)
            imgui.set_next_item_width(150)
            ch, st['mip'] = imgui.slider_int('mip##full', st['mip'], 0, inf.mips - 1)
        if inf.faces > 1:
            imgui.same_line(0, 12)
            imgui.set_next_item_width(150)
            ch, st['face'] = imgui.slider_int('face##full', st['face'], 0, inf.faces - 1)
        imgui.same_line(0, 12)
        if imgui.button('Replace...') and not d.b.truncated:
            self.action_replace(d, r)
        imgui.same_line(0, 2)
        if imgui.button('Export...'):
            self.action_export(d, r)
        imgui.same_line(0, 12)
        if imgui.button('Close (Esc)'):
            self.tex_full = None
            imgui.end()
            return
        img, err = self.decode_texture(st, d, r)
        if err:
            imgui.text_colored(imgui.ImVec4(1, 0.4, 0.3, 1), f'Cannot decode: {err}')
        elif img is None:
            imgui.text_disabled('decoding...')
        else:
            h, w = img.shape[:2]
            avail = imgui.get_content_region_avail()
            size = (max(64, int(avail.x)), max(64, int(avail.y - imgui.get_text_line_height_with_spacing() - 4)))
            p = st['params']
            if p is None or tuple(p.image_display_size) != size:
                p = immvision.ImageParams()
                p.image_display_size = size
                p.show_options_button = False
                p.show_zoom_buttons = False
                p.show_pixel_info = False
                p.show_image_info = False
                p.show_school_paper_background = False
                p.zoom_key = 'texfull'
                p.refresh_image = True
                if st['zoom'] == 'auto':                 # full resolution; small textures as big as they fit
                    k = max(1, int(min(size[0] / w, size[1] / h)))
                    zoom = 'one' if k == 1 else float(k)
                    st['zoom'] = None
                st['params'] = p
            else:
                p.refresh_image = False
            p.selected_channel = st['channel']
            if zoom == 'fit':
                p.zoom_pan_matrix = immvision.make_zoom_pan_matrix_full_view((w, h), size)
            elif zoom == 'one':
                p.zoom_pan_matrix = immvision.make_zoom_pan_matrix_scale_one((w, h), size)
            elif isinstance(zoom, float):
                p.zoom_pan_matrix = immvision.make_zoom_pan_matrix((w / 2, h / 2), zoom, size)
            if zoom == 'keep':
                p.refresh_image = True
            immvision.image(f'##texfull{r.id}', img, p)
            self.pixel_line(img, p.mouse_info, f'{w} x {h} pixels  -  mouse wheel: zoom, drag: pan, Esc: close')
        imgui.end()

    # -- the textures of a model ----------------------------------------------------------------------------
    def model_textures_window(self):
        """Every texture the model in the 3D view uses: thumbnail, size, full-size view, replace (the 3D view
        follows)."""
        st = self.model_tex
        if not st:
            return
        d = self.doc_by_uid(st['doc'])
        res = self.model.get('result')
        if d is None or not res or res[0] != 'ok' or (self.model.get('key') or (None, None))[1] != st['rid']:
            self.model_tex = None
            return
        meshes = res[1]
        roles = {}
        for m in meshes:
            for tid, role in ((m.texture, 'colour'), (getattr(m, 'normal_tex', None), 'normal'),
                              (getattr(m, 'spec_tex', None), 'specular'), (getattr(m, 'lights_tex', None), 'lights')):
                if tid:
                    roles.setdefault(tid, set()).add(role)
        vp = imgui.get_main_viewport()
        imgui.set_next_window_pos(imgui.ImVec2(vp.work_pos.x + vp.work_size.x / 2, vp.work_pos.y + vp.work_size.y / 2),
                                  imgui.Cond_.first_use_ever, imgui.ImVec2(0.5, 0.5))
        imgui.set_next_window_size(imgui.ImVec2(620, 600), imgui.Cond_.first_use_ever)
        if st.get('focus'):
            imgui.set_next_window_focus()
            st['focus'] = False
        _, keep = imgui.begin(f'Textures of {self.display_name(d, d.b.find(st["rid"]))[0]}###modeltex', True,
                              imgui.WindowFlags_.no_collapse)
        if not keep:
            imgui.end()
            self.model_tex = None
            return
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled('Replace swaps a texture in its bundle (Ctrl+Z undoes, Save keeps it); the 3D view follows. '
                            'Textures in bundles that are not open (VEHICLETEX, ...) need Open first.')
        imgui.pop_text_wrap_pos()
        root = roots.root_for(d.path, d.b.platform)
        bundles = self.model_bundles(d)
        flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.scroll_y
        if imgui.begin_table('mtexw', 3, flags):
            imgui.table_setup_column('', imgui.TableColumnFlags_.width_fixed, 72)
            imgui.table_setup_column('Texture', imgui.TableColumnFlags_.width_stretch)
            imgui.table_setup_column('', imgui.TableColumnFlags_.width_fixed, 150)
            for k, (tid, rl) in enumerate(sorted(roles.items(), key=lambda x: (sorted(x[1]), x[0]))):
                imgui.table_next_row()
                imgui.table_next_column()
                od, orr = self.where(tid)
                tb, tr = (od.b, orr) if orr is not None else self.mesh_lib.find(tid, bundles, root)
                if tr is not None and tr.type == T_TEXTURE:
                    gl = self.thumbs.get(('mt', tid, id(tr.data(0)), id(tr.data(tb.gfx_chunk))), tr, tb.platform,
                                         opaque='normal' in rl)
                    if gl is not None:
                        self.thumbs.draw(gl, 64)
                    else:
                        imgui.dummy(imgui.ImVec2(64, 64))
                else:
                    imgui.dummy(imgui.ImVec2(64, 64))
                imgui.table_next_column()
                if orr is not None:
                    imgui.text(self.display_name(od, orr)[0])
                else:
                    full = self.names.exact.get(tid)
                    imgui.text(N.short(full) if full else ops.id_text(tid))
                imgui.text_disabled(', '.join(sorted(rl)) + (f'  -  {raster.info(tr, tb.platform).describe()}'
                                                             if tr is not None else '  -  not found'))
                if orr is None and tb is not None:
                    imgui.text_disabled(f'in {os.path.basename(tb.path or "")} (not open)')
                imgui.table_next_column()
                if orr is not None:
                    if imgui.small_button(f'View##mv{k}'):
                        self.open_texture_window(od, orr)
                    imgui.same_line()
                    if imgui.small_button(f'Replace...##mr{k}') and not od.b.truncated:
                        self.action_replace(od, orr)
                    imgui.same_line()
                self.locate(tid, f'mtw{k}', tb.path if tb is not None else None)
            imgui.end_table()
        imgui.end()

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
                    self.gcache[('wavegfx',) + key[1:]] = waveform(res[0])
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
        self.audio_panel(d, r, key, audio, rate, replace=True)

    def audio_panel(self, d, r, key, audio, rate, replace=False):
        """Play / Pause / Stop, the clock, Export WAV (and Replace) and the waveform with its play head for the
        sound `audio` (int16 (n, channels)) of the shown resource."""
        n = len(audio)
        pl = self.player
        mine = pl.opened and pl.key == key
        if mine and pl.done:                 # played to the end: back to the start
            pl.stop()
            self.wave_cursor[key] = 0
            mine = False
        pos = pl.position() if mine else self.wave_cursor.get(key, 0)
        if mine and not pl.paused:
            kind, action = 'pause', 'Pause'
        elif mine:
            kind, action = 'play', 'Resume'
        else:
            kind, action = 'play', 'Play'
        if self.dashboard_icon(kind, 15):
            imgui.same_line(0, 2)
            label = action
        else:
            label = f'{theme.I.ICON_FA_PAUSE}  Pause' if action == 'Pause' else f'{theme.I.ICON_FA_PLAY}  {action}'
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
        if self.dashboard_icon('stop', 15):
            imgui.same_line(0, 2)
            stop_label = 'Stop'
        else:
            stop_label = f'{theme.I.ICON_FA_STOP}  Stop'
        if imgui.button(stop_label):
            if mine:
                pl.stop()
            self.wave_cursor[key] = 0
            pos = 0
        imgui.same_line()
        imgui.text(f'{_clock(pos / max(rate, 1))} / {_clock(n / max(rate, 1))}')
        imgui.same_line()
        if imgui.button('Export WAV...'):
            self.action_export(d, r, 'wav')
        if replace:
            imgui.same_line()
            if imgui.button('Replace...') and not d.b.truncated:
                self.action_replace(d, r)
        gkey = ('wavegfx',) + key[1:]
        if gkey not in self.gcache:
            self.gcache[gkey] = waveform(audio)
        lo, hi = self.gcache[gkey]
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

    def ginsu_view(self, d, r):
        """Ginsu engine sound: what it holds, the whole rev sweep, and the engine held at a chosen RPM."""
        key = ('wave', d.uid, r.id, id(r.data(0)))
        g = self.gcache.get(key)
        if g is None:
            try:
                g = ginsu.read(r, d.b.e)
                g.pcm()
            except Exception as e:
                g = e
            self.gcache[key] = g
        if isinstance(g, Exception):
            imgui.text_wrapped(f'Cannot read this engine sound: {g}')
            return
        imgui.text(g.describe())
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled('Ginsu granular engine sound: a recording of the engine sweeping through its revs, cut '
                            'into grains of one engine cycle; the game plays the grains of the current RPM.')
        imgui.pop_text_wrap_pos()
        imgui.spacing()
        audio = g.pcm().reshape(-1, 1)
        self.audio_panel(d, r, key, audio, g.rate)
        pl = self.player
        mine = pl.opened and pl.key == key
        pos = pl.position() if mine else self.wave_cursor.get(key, 0)
        imgui.text(f'Play position: {g.rpm_at(pos):.0f} RPM, grain {g.grain_at(pos) + 1} / {g.grains}')
        imgui.separator()
        imgui.text('Hold the engine at a steady RPM:')
        st = self.ginsu_ui if getattr(self, 'ginsu_ui', None) and self.ginsu_ui['key'] == key[1:] else None
        if st is None:
            st = self.ginsu_ui = {'key': key[1:], 'rpm': round((g.min_rpm + g.max_rpm) / 2)}
        imgui.set_next_item_width(min(420, imgui.get_content_region_avail().x - 160))
        ch, rpm = imgui.slider_float('RPM##ginsu', float(st['rpm']), g.min_rpm, g.max_rpm, '%.0f')
        if ch:
            st['rpm'] = rpm
        hkey = key + ('hold',)
        holding = pl.opened and pl.key == hkey and not pl.done
        imgui.same_line()
        if self.dashboard_icon('stop' if holding else 'play', 15):
            imgui.same_line(0, 2)
            hold_label = 'Stop' if holding else 'Play'
        else:
            hold_label = f'{theme.I.ICON_FA_STOP}  Stop' if holding else f'{theme.I.ICON_FA_PLAY}  Play'
        if imgui.button(hold_label + '##hold'):
            if holding:
                pl.stop()
            else:
                try:
                    pl.play(g.hold(st['rpm']), g.rate, 0, hkey)
                except OSError as ex:
                    self.status = str(ex)
        if holding and imgui.is_item_hovered():
            imgui.set_tooltip('Move the slider, then press Play again for another RPM')
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled('Plays the few grains at that RPM in turn for 3 s (a rough preview; the game blends and '
                            'pitches them).')
        imgui.pop_text_wrap_pos()
        imgui.separator()
        if imgui.button('Replace with a recording...') and not d.b.truncated:
            self.action_replace(d, r)
        imgui.same_line()
        imgui.text_disabled('a WAV / FLAC / OGG / MP3 of an engine sweeping through its revs')

    def ginsu_replace_dialog(self, d, r, path):
        """Options for turning a recording into this engine sound (defaults from the sound now)."""
        try:
            g = ginsu.read(r, d.b.e)
            cyl, _ = ginsu.cylinders_of(g)
            src, rate = eal3.read_audio(path)
        except Exception as e:
            self.modal = {'kind': 'message', 'title': 'Replace engine sound', 'text': str(e)}
            return
        self.modal = {'kind': 'ginsu_options', 'doc': d.uid, 'rid': r.id, 'path': path, 'decel': g.decel,
                      'mode': 1 if g.decel else 0, 'cyl': int(cyl), 'start': float(g.max_rpm if g.decel else g.min_rpm),
                      'lo': float(g.min_rpm), 'hi': float(g.max_rpm),
                      'src': f'{src.shape[1]} channel(s), {rate} Hz, {len(src) / rate:.2f} s'}

    def ginsu_replace(self, d, r, m):
        """Encode the recording into the engine sound (undoable) and report what came out."""
        try:
            audio, rate = eal3.read_audio(m['path'])
            g = ginsu.read(r, d.b.e)
            curve = m.get('curve') if m.get('curve_key') == (m['cyl'], m['start']) else None
            data, info = ginsu.from_recording(g, audio, rate, 'linear' if m['mode'] else 'track', m['cyl'],
                                              m['start'], (min(m['lo'], m['hi']), max(m['lo'], m['hi'])), curve)
        except Exception as e:
            traceback.print_exc()
            self.modal = {'kind': 'message', 'title': 'Replace engine sound failed', 'text': str(e)}
            return
        d.checkpoint(f'replace engine sound {r.id:#x}', [r.id])
        r.set_data(0, data)
        self.changed(d, r)
        text = (f"New engine sound: {info['min']:.0f} - {info['max']:.0f} RPM, {info['grains']} grains of one engine "
                f"cycle, {info['seconds']:.2f} s at {info['rate']} Hz (Ctrl+Z to undo, Save to keep).")
        if info['weak'] > 0.3:
            text += (f"\n\nNo clear engine pitch in {info['weak'] * 100:.0f} % of the recording: the RPM found may be "
                     "off. Check the cylinders and the start RPM (Analyse shows the curve), or use a steady sweep "
                     "with the recording's RPM range.")
        self.modal = {'kind': 'message', 'title': 'Replace engine sound', 'text': text}
        self.status = f'Engine sound {r.id:#x} replaced (Ctrl+Z to undo).'

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

    def update_roots(self):
        """Keep the per-platform fallback game folders in step with the pinned folders."""
        key = tuple(self.pinned())
        if key != getattr(self, '_roots_key', None):
            self._roots_key = key
            roots.set_fallbacks(key)

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
        shown = (('wave', d.uid, r.id, id(r.data(0))) if d is not None and r is not None
                 and r.type in (0x81, ginsu.T_GINSU) else None)
        if shown != (self.player.key or ())[:4] or not self.cfg.get('preview', True):
            self.player.stop()

    # -- 3D models ---------------------------------------------------------------------------------------------
    def model_bundles(self, d):
        return [d.b] + [o.b for o in self.docs if o is not d]

    def texture_image(self, d, tid, max_size=1024):
        """RGBA array of a texture found in the open bundles or the game's global bundles (None if missing)."""
        if not tid:
            return None
        b, r = self.mesh_lib.find(tid, self.model_bundles(d), roots.root_for(d.path, d.b.platform))
        if r is None or r.type != T_TEXTURE:
            return None
        # the same texture data gives the same image object (the 3D view uploads only changed ones); the entry
        # keeps the data alive so that its id is not reused
        d0, px = r.data(0), r.data(b.gfx_chunk)
        key = (tid, id(d0), id(px), max_size)
        with self.tex_cache_lock:
            hit = self.tex_cache.get(key)
            if hit is not None:
                self.tex_cache.move_to_end(key)
                return hit[0]
        try:
            inf = raster.info(r, b.platform)
            mip = 0
            while mip + 1 < inf.mips and max(inf.w >> mip, inf.h >> mip) > max_size:
                mip += 1
            img = raster.decode(r, b.platform, mip)
        except Exception:
            return None
        with self.tex_cache_lock:
            self.tex_cache[key] = (img, d0, px)
            while len(self.tex_cache) > 96:
                self.tex_cache.popitem(last=False)
        return img

    def model_view(self, d, r):
        st = self.model
        show = st['show'] if r.type == mesh.T_INSTANCELIST else 0
        nb = st['nb'] and r.type == mesh.T_INSTANCELIST
        cmr = mesh.vgs_control_mesh(d.b, r) if r.type == mesh.T_VGS else None
        cm = bool(st['cm'] and cmr is not None)
        # the car is drawn from its renderables, materials, ...: an edit of any of them (FBX import) re-reads it
        key = (d.uid, r.id, (id(r.data(0)), d.edits, self.edit_gen), st['lod'], show, nb, cm,
               id(cmr.data(0)) if cm else 0)
        if st['key'] != key:
            st['key'] = key
            st['result'] = None

            def work(key=key, lod=st['lod'], show=show, nb=nb, cm=cm):
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
                        if cm:
                            meshes = meshes + mesh.control_mesh_meshes(*mesh.read_control_mesh(d.b, cmr)[:3])
                        stats, nlod, size = {'car': vst}, 4, 1024
                    elif r.type == mesh.T_CONTROLMESH:
                        meshes, cst = mesh.decode_control_mesh(d.b, r, self.mesh_lib, self.model_bundles(d)[1:],
                                                               d.path, lod)
                        stats, nlod, size = {'control': cst}, 1, 256
                    else:
                        meshes, nlod = mesh.decode_resource(d.b, r, self.mesh_lib, self.model_bundles(d)[1:], d.path, lod)
                        stats, size = None, 1024
                    self.model['progress'] = 'loading the textures...'
                    texs = {t: self.texture_image(d, t, size) for t in mesh.texture_ids(meshes, size >= 1024)}
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
            self.viewer.paint = tuple(self.cfg.get('paint', self.viewer.paint))
        ukey = (key, id(res))                # what is on the GPU: the request and the result shown
        if st['uploaded'] != ukey:
            old = st['uploaded'][0] if st['uploaded'] else None
            same = old is not None and old[:2] == key[:2] and old[3:] == key[3:]
            try:
                fit = (1.1 if r.type in (mesh.T_INSTANCELIST, mesh.T_POLYSOUP) else
                       2.1 if r.type == mesh.T_CONTROLMESH else 2.6)
                self.viewer.set_meshes(key, meshes, texs, fit, keep_view=same)
            except Exception as e:
                traceback.print_exc()
                st['result'] = ('error', f'OpenGL: {e}')
                return
            st['uploaded'] = ukey
        v = self.viewer
        ntri = sum(len(m.tris) for m in meshes)
        nvert = sum(len(m.pos) for m in meshes)
        stats = st.get('stats') if r.type in (mesh.T_INSTANCELIST, mesh.T_POLYSOUP, mesh.T_VGS,
                                              mesh.T_CONTROLMESH) else None
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
            if cmr is not None:
                imgui.same_line()
                _, st['cm'] = imgui.checkbox('Control points', st['cm'])
                if imgui.is_item_hovered():
                    imgui.set_tooltip('The prototype crash deformation lattice (ControlMesh): 64 points on the body; '
                                      'grey = rigid, yellow .. red = how deep it can dent, the line = the way it moves')
        elif stats and 'control' in stats:
            cs = stats['control']
            imgui.text(f'{cs["points"]} control points, {cs["moving"]} can dent (deepest {cs["deepest"] * 100:.0f} cm)'
                       + (' (shown over the car)' if cs['car'] else ''))
            if cs['car']:
                imgui.set_next_item_width(200)
                _, st['dent'] = imgui.slider_float('Dent (x max)', st['dent'], 0.0, 5.0, '%.2f')
                if imgui.is_item_hovered():
                    imgui.set_tooltip('Dents the car the prototype\'s way: every vertex follows its two control points '
                                      'along their directions, this many times their maximum displacement (1 = the '
                                      'deepest the game allows; more exaggerates to show the areas)')
                self.dent_car(d, r, meshes, st)
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
        _, v.shaded = imgui.checkbox('Shaded', v.shaded)
        if imgui.is_item_hovered():
            imgui.set_tooltip('The materials as the game shades them: normal and specular maps, roughness, metal, '
                              'car paint, ambient occlusion (off: the texture under a head light)')
        imgui.same_line()
        _, v.wire = imgui.checkbox('Wireframe', v.wire)
        imgui.same_line()
        _, v.z_up = imgui.checkbox('Z up', v.z_up)
        imgui.same_line()
        if imgui.button('Reset view'):
            v.pan[:] = 0
            v.dist = v.radius * v.fit
            v.yaw, v.pitch = 0.6, 0.35
        if any(m.paint for m in meshes):
            ch, col = imgui.color_edit3('##paint', list(v.paint), imgui.ColorEditFlags_.no_inputs)
            if ch:
                v.paint = tuple(col)
                self.cfg['paint'] = list(col)
            if imgui.is_item_hovered():
                imgui.set_tooltip('Car paint colour (the player chooses it in the game)')
            imgui.same_line()
            imgui.text('Paint')
            imgui.same_line()
        if any(m.lights_tex for m in meshes):
            on = v.lights_on                               # mask channels: R brake, G running, B head, A tail
            ch, lit = imgui.checkbox('Lights', bool(on[2]))
            if ch:
                on[1] = on[2] = on[3] = 1.0 if lit else 0.0
            if imgui.is_item_hovered():
                imgui.set_tooltip('Headlights, running and tail lights on: the car\'s light masks (LightmapLights) in '
                                  'the colours of its materials')
            imgui.same_line()
            ch, br = imgui.checkbox('Brake', bool(on[0]))
            if ch:
                on[0] = 1.0 if br else 0.0
            imgui.same_line()
        if imgui.button('Textures...'):
            self.model_tex = {'doc': d.uid, 'rid': r.id, 'focus': True}
        if imgui.is_item_hovered():
            imgui.set_tooltip('Every texture of this model: view at full size, replace (the 3D view follows)')
        imgui.same_line()
        if imgui.button('Export glTF...'):             # own row: the view toolbar above is full
            self.action_export(d, r, 'glb')
        imgui.same_line()
        if imgui.button('Export FBX...'):
            self.action_export(d, r, 'fbx')
        if r.type in MODEL_IMPORT_TYPES:
            imgui.same_line()
            if imgui.button('Import FBX...') and not d.b.truncated:
                self.action_import_fbx(d, r)
            if imgui.is_item_hovered():
                imgui.set_tooltip('Write an edited FBX back: objects keep the names Export FBX gave them (R<id>_<n>); '
                                  'positions, UVs and normals are replaced, the rest comes from the nearest original '
                                  'vertex')
        missing = sum(1 for m in meshes if m.texture and texs.get(m.texture) is None)
        if missing:
            imgui.text_disabled(f'{missing} mesh(es) use textures that are not in the open bundles or the global ones.')
        avail = imgui.get_content_region_avail()
        extra = (int(imgui.get_frame_height_with_spacing() * 6.6) + 8 if r.type == mesh.T_VGS else    # wheel table
                 int(imgui.get_frame_height_with_spacing() * 8.5) + 8 if r.type == mesh.T_CONTROLMESH else 0)
        w, h = max(64, int(avail.x) - 4), max(64, int(avail.y) - 24 - extra)
        v.widget(w, h)
        imgui.text_disabled('Left drag: turn, right / middle drag: move, wheel: zoom, double click: fit')
        if r.type == mesh.T_VGS:
            self.wheel_editor(d, r)
        elif r.type == mesh.T_CONTROLMESH:
            self.control_point_table(d, r)

    def dent_car(self, d, r, meshes, st):
        """Move the car's vertices (and the markers) of the ControlMesh view to the Dent amount."""
        dk = (st['uploaded'], st['dent'])
        if st['dented'] == dk or self.viewer is None:
            return
        st['dented'] = dk
        pos, dirs, disp, _ = mesh.read_control_mesh(d.b, r)
        changes = {}
        first = None
        for i, m in enumerate(meshes):
            if m.overlay:
                first = i if first is None else first
                continue
            if m.dent_points is not None:
                changes[i] = (mesh.dented(m, pos, dirs, disp, st['dent']), m.normals())
        if first is not None:                      # the markers move with their points
            moved = pos + dirs * (disp * st['dent'])[:, None]
            for k, mk in enumerate(mesh.control_mesh_meshes(moved, dirs, disp)):
                if first + k < len(meshes):
                    changes[first + k] = (mk.pos, mk.normals())
        self.viewer.update_vertices(changes)

    def control_point_table(self, d, r):
        """The points of a ControlMesh; the max displacement (how deep the body can dent there) is editable."""
        try:
            pos, dirs, disp, doff = mesh.read_control_mesh(d.b, r)
        except (ValueError, IndexError):
            return
        flags = (imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.scroll_y
                 | imgui.TableFlags_.resizable)
        if not imgui.begin_table('ctrlpts', 4, flags, imgui.ImVec2(0, imgui.get_content_region_avail().y)):
            return
        imgui.table_setup_scroll_freeze(0, 1)
        imgui.table_setup_column('#', imgui.TableColumnFlags_.width_fixed, 28)
        imgui.table_setup_column('Position (x left, y up, z forward; m)', imgui.TableColumnFlags_.width_stretch)
        imgui.table_setup_column('Direction', imgui.TableColumnFlags_.width_stretch)
        imgui.table_setup_column('Max dent (m)', imgui.TableColumnFlags_.width_fixed, 110)
        imgui.table_headers_row()
        ro = d.b.truncated
        used = mesh.control_points_used(pos, dirs)
        for i in range(len(pos)):
            imgui.table_next_row()
            imgui.table_next_column()
            col = mesh.dent_colour(disp[i])
            imgui.text_colored(imgui.ImVec4(col[0], col[1], col[2], 1.0), str(i))
            imgui.table_next_column()
            if not used[i]:
                imgui.text_disabled('unused')
                continue
            imgui.text('%.3f  %.3f  %.3f' % tuple(pos[i]))
            imgui.table_next_column()
            imgui.text('%.2f  %.2f  %.2f' % tuple(dirs[i]))
            imgui.table_next_column()
            imgui.set_next_item_width(-1)
            ch, v = imgui.drag_float(f'##dent{i}', float(disp[i]), 0.001, 0.0, 1.0, '%.3f')
            if ch and not ro:
                v = v if v >= 1e-4 else mesh.RIGID          # rigid: the tiny value the game's own data holds
                self.write(d, r, doff + 16 * i, d.b.e + '4f', v, v, v, v)
        imgui.end_table()

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
        if f.type in (anim.T_ANIMLIST, anim.T_ANIMATION, anim.T_SKELETON):
            st = getattr(self, 'anim_st', None)
            return st is not None and st.get('base', (None,))[1] == f.id and st.get('posed') is not None
        if f.type in mesh.MODEL_TYPES:
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
        root = roots.root_for(d.path, d.b.platform)
        bundles = self.model_bundles(d)
        sb, sh = self.mesh_lib.find(info['shader'], bundles, root) if info['shader'] else (None, None)
        sname = ''
        if sh is not None:
            sc = sh.data(0)
            np_ = struct.unpack_from(sb.e + 'I', sc, 8)[0]
            sname = bytes(sc[np_:np_ + 128]).split(b'\0')[0].decode('latin1', 'replace')
        if not sname and info['shader']:
            sname = knownids.SHADERS.get(info['shader'] & 0xFFFFFFFF, '')
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
                    if orr is not None and orr.type == T_TEXTURE:
                        if imgui.small_button(f'View##mtv{k}'):
                            self.open_texture_window(od, orr)
                        imgui.same_line()
                        if imgui.small_button(f'Replace...##mtr{k}') and not od.b.truncated:
                            self.action_replace(od, orr)
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

    def traffic_view(self, d, r):
        data = r.data(0)
        key = (d.uid, r.id, id(data))
        state = self.traffic_cache
        if state['key'] != key:
            try:
                parsed = traffic.parse(data)
                self.traffic_cache = state = {'key': key, 'result': parsed, 'error': None,
                                              'zoom': 1.0, 'pan': [0.0, 0.0]}
            except traffic.TrafficError as exc:
                self.traffic_cache = state = {'key': key, 'result': None, 'error': str(exc),
                                              'zoom': 1.0, 'pan': [0.0, 0.0]}
        parsed = state['result']
        if parsed is None:
            imgui.text_colored(imgui.ImVec4(1, 0.4, 0.3, 1),
                               f'Cannot parse TrafficData: {state["error"]}')
            return

        points = [p for hull in parsed['hulls'] for line in hull['lines'] for p in line]
        imgui.text(f'TrafficData version {parsed["version"]}: {len(parsed["hulls"])} hulls, '
                   f'{sum(len(h["sections"]) for h in parsed["hulls"])} sections, {len(points)} lane points')
        imgui.text_disabled('Wheel: zoom; right-drag: pan. Lane points are projected onto the X-Z plane.')
        if not points:
            imgui.text_disabled('No lane geometry in this resource.')
            return

        avail = imgui.get_content_region_avail()
        w, h = max(100.0, avail.x), max(100.0, avail.y - 4)
        p0 = imgui.get_cursor_screen_pos()
        imgui.invisible_button('##traffic-map', imgui.ImVec2(w, h),
                               imgui.ButtonFlags_.mouse_button_left | imgui.ButtonFlags_.mouse_button_right)
        hovered = imgui.is_item_hovered()
        io = imgui.get_io()
        x0, z0, x1, z1 = parsed['bounds']
        cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
        base = 0.94 * min(w / max(x1 - x0, 1), h / max(z1 - z0, 1))
        scale = base * state['zoom']
        ox, oz = cx + state['pan'][0], cz + state['pan'][1]
        mx = (io.mouse_pos.x - p0.x - w / 2) / scale + ox
        mz = (io.mouse_pos.y - p0.y - h / 2) / scale + oz
        if hovered and io.mouse_wheel:
            factor = 1.2 ** io.mouse_wheel
            new_zoom = min(50.0, max(0.1, state['zoom'] * factor))
            new_scale = base * new_zoom
            state['pan'][0] += (mx - (io.mouse_pos.x - p0.x - w / 2) / new_scale - cx)
            state['pan'][1] += (mz - (io.mouse_pos.y - p0.y - h / 2) / new_scale - cz)
            state['zoom'] = new_zoom
            scale = new_scale
            ox, oz = cx + state['pan'][0], cz + state['pan'][1]
        if imgui.is_item_active() and imgui.is_mouse_down(1):
            state['pan'][0] -= io.mouse_delta.x / scale
            state['pan'][1] += io.mouse_delta.y / scale
            ox, oz = cx + state['pan'][0], cz + state['pan'][1]

        def screen(point):
            return imgui.ImVec2(p0.x + w / 2 + (point[0] - ox) * scale,
                                p0.y + h / 2 - (point[2] - oz) * scale)

        dl = imgui.get_window_draw_list()
        dl.push_clip_rect(p0, imgui.ImVec2(p0.x + w, p0.y + h), True)
        dl.add_rect_filled(p0, imgui.ImVec2(p0.x + w, p0.y + h), imgui.get_color_u32(imgui.Col_.frame_bg))
        import colorsys
        count = max(1, len(parsed['hulls']))
        for index, hull in enumerate(parsed['hulls']):
            color = colorsys.hsv_to_rgb(index / count, 0.78, 0.95)
            color_u32 = imgui.color_convert_float4_to_u32(imgui.ImVec4(*color, 1.0))
            for line in hull['lines']:
                dl.add_polyline([screen(point) for point in line], color_u32, 2.0)
        dl.pop_clip_rect()
        if hovered:
            imgui.set_tooltip(f'X {mx:.1f}, Z {mz:.1f}')

    def load_gameplay_reference(self):
        folder = filedialog.pick_folder('Select the event-traffic-visualizer static JSON folder')
        if not folder:
            return
        try:
            data = gameplay_data.load(folder)
        except gameplay_data.GameplayDataError as exc:
            self.status = f'Cannot load gameplay reference data: {exc}'
            return
        state = self.gameplay_view
        state.update(data=data, folder=folder, open=True, race='', filter='', zoom=1.0, pan=[0.0, 0.0],
                     custom_route=[])
        self.status = f'Loaded gameplay reference data from {folder}'

    def gameplay_reference_window(self):
        state = self.gameplay_view
        if state['data'] is None:
            return
        visible, state['open'] = imgui.begin('Event and gameplay reference viewer###gameplay-ref', state['open'])
        if visible:
            data = state['data']
            imgui.text_disabled(state['folder'])
            imgui.text(f'{len(data["races"])} events, {len(data["checkpoints"])} checkpoints, '
                       f'{len(data["triggers"])} gameplay triggers, {len(data["trigger_locations"])} trigger locations')
            imgui.set_next_item_width(310)
            _, state['filter'] = imgui.input_text_with_hint('##gameplay-filter', 'filter ids / names', state['filter'])
            if imgui.begin_tab_bar('gameplay-data-tabs'):
                if imgui.begin_tab_item('Event routes')[0]:
                    races = data['races']
                    names = sorted(races)
                    if not names:
                        imgui.text_disabled('No events found in the race dataset.')
                    elif state['race'] not in races:
                        state['race'] = names[0]
                    imgui.set_next_item_width(360)
                    if names and imgui.begin_combo('Event', state['race']):
                        for name in names:
                            if imgui.selectable(f'{name}##race', name == state['race'])[0]:
                                state['race'] = name
                                state['zoom'] = 1.0
                                state['pan'] = [0.0, 0.0]
                        imgui.end_combo()
                    if state['race'] in races:
                        race = races[state['race']]
                        checkpoint_ids = race.get('checkpoints', [])
                        trigger_ids = race.get('triggers', [])
                        imgui.same_line()
                        if imgui.button('Clear route'):
                            state['custom_route'].clear()
                        imgui.same_line()
                        if imgui.button('Export route...'):
                            path = filedialog.save_file('Export custom checkpoint route', 'route.json',
                                                        filedialog.TEXT, 'json')
                            if path:
                                try:
                                    with open(path, 'w', encoding='utf-8', newline='') as f:
                                        json.dump({'event': state['race'],
                                                   'checkpoints': state['custom_route']},
                                                  f, indent=2)
                                    self.status = f'Exported custom route: {path}'
                                except OSError as exc:
                                    self.status = f'Cannot export custom route: {exc}'
                        imgui.text(f'{race.get("kind", "Event")}: {len(checkpoint_ids)} checkpoints, '
                                   f'{len(trigger_ids)} triggers; click checkpoint markers to add route entries.')
                        self.gameplay_route_map(data, state, checkpoint_ids, trigger_ids)
                        if state['custom_route']:
                            imgui.text('Custom route: ' + ' -> '.join(str(x) for x in state['custom_route']))
                    imgui.end_tab_item()
                for tab, key, title in (
                        ('Gameplay triggers', 'triggers', 'Triggers'),
                        ('Trigger locations', 'trigger_locations', 'Trigger locations'),
                        ('AI player types', 'ai_players', 'AI player types'),
                        ('Cop AI types', 'cop_types', 'Cop AI types'),
                        ('Rollout types', 'rollouts', 'Rollout types')):
                    if imgui.begin_tab_item(tab)[0]:
                        self.gameplay_json_table(data[key], title, state['filter'], tab)
                        imgui.end_tab_item()
                imgui.end_tab_bar()
        imgui.end()

    def gameplay_route_map(self, data, state, checkpoint_ids, trigger_ids):
        checkpoints = data['checkpoints']
        selected = []
        for checkpoint_id in checkpoint_ids:
            record = checkpoints.get(str(checkpoint_id))
            position = record.get('position') if isinstance(record, dict) else None
            if isinstance(position, dict) and all(k in position for k in ('x', 'y', 'z')):
                try:
                    point = (float(position['x']), float(position['z']))
                except (TypeError, ValueError):
                    continue
                if all(math.isfinite(v) for v in point):
                    selected.append((checkpoint_id, record, point))
        trigger_points = []
        for trigger_id in trigger_ids:
            record = data['trigger_locations'].get(str(trigger_id))
            position = record.get('data', {}).get('position') if isinstance(record, dict) else None
            if isinstance(position, dict) and all(k in position for k in ('x', 'z')):
                try:
                    point = (float(position['x']), float(position['z']))
                except (TypeError, ValueError):
                    continue
                if all(math.isfinite(v) for v in point):
                    trigger_points.append((trigger_id, record, point))
        all_points = [p for _, _, p in selected] + [p for _, _, p in trigger_points]
        if not all_points:
            imgui.text_disabled('This event has no checkpoint or trigger positions in the reference data.')
            return
        avail = imgui.get_content_region_avail()
        w, h = max(100.0, avail.x), max(120.0, min(520.0, avail.y * 0.72))
        p0 = imgui.get_cursor_screen_pos()
        imgui.invisible_button('##gameplay-route-map', imgui.ImVec2(w, h),
                               imgui.ButtonFlags_.mouse_button_left | imgui.ButtonFlags_.mouse_button_right)
        hovered = imgui.is_item_hovered()
        active = imgui.is_item_active()
        io = imgui.get_io()
        xs, zs = [p[0] for p in all_points], [p[1] for p in all_points]
        x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
        cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
        base = 0.92 * min(w / max(x1 - x0, 1), h / max(z1 - z0, 1))
        scale = base * state['zoom']
        ox, oz = cx + state['pan'][0], cz + state['pan'][1]

        def screen(point):
            return imgui.ImVec2(p0.x + w / 2 + (point[0] - ox) * scale,
                                p0.y + h / 2 - (point[1] - oz) * scale)

        mouse_x = (io.mouse_pos.x - p0.x - w / 2) / scale + ox
        mouse_z = (h / 2 - (io.mouse_pos.y - p0.y)) / scale + oz
        if hovered and io.mouse_wheel:
            factor = 1.2 ** io.mouse_wheel
            new_zoom = min(40.0, max(0.5, state['zoom'] * factor))
            new_scale = base * new_zoom
            state['pan'][0] += mouse_x - (io.mouse_pos.x - p0.x - w / 2) / new_scale - cx
            state['pan'][1] += mouse_z - (h / 2 - (io.mouse_pos.y - p0.y)) / new_scale - cz
            state['zoom'] = new_zoom
            scale = new_scale
            ox, oz = cx + state['pan'][0], cz + state['pan'][1]
        if active and imgui.is_mouse_down(1):
            state['pan'][0] -= io.mouse_delta.x / scale
            state['pan'][1] += io.mouse_delta.y / scale
            ox, oz = cx + state['pan'][0], cz + state['pan'][1]

        draw = imgui.get_window_draw_list()
        draw.push_clip_rect(p0, imgui.ImVec2(p0.x + w, p0.y + h), True)
        draw.add_rect_filled(p0, imgui.ImVec2(p0.x + w, p0.y + h), imgui.get_color_u32(imgui.Col_.frame_bg))
        if len(selected) > 1:
            draw.add_polyline([screen(p) for _, _, p in selected],
                              imgui.get_color_u32(imgui.Col_.plot_lines), 2.0)

        hit = None
        for index, (checkpoint_id, record, point) in enumerate(selected):
            pos = screen(point)
            color = (0xFF00FFFF if index == 0 else
                     0xFF00A5FF if index == len(selected) - 1 else
                     imgui.get_color_u32(imgui.Col_.text))
            draw.add_circle_filled(pos, 5.0, color)
            draw.add_text(imgui.ImVec2(pos.x + 6, pos.y - 7),
                          imgui.get_color_u32(imgui.Col_.text), str(index + 1))
            if hovered and math.hypot(io.mouse_pos.x - pos.x, io.mouse_pos.y - pos.y) < 9:
                hit = ('checkpoint', checkpoint_id, record)

        for trigger_id, record, point in trigger_points:
            pos = screen(point)
            draw.add_circle(pos, 5.0, imgui.get_color_u32(imgui.Col_.plot_histogram), 12, 1.5)
            if hovered and math.hypot(io.mouse_pos.x - pos.x, io.mouse_pos.y - pos.y) < 9:
                hit = ('trigger', trigger_id, record)
        draw.pop_clip_rect()

        if hit:
            kind, identifier, record = hit
            imgui.set_tooltip(f'{kind.title()} {identifier}\\n{json.dumps(record, indent=2)[:1200]}')
            if kind == 'checkpoint' and imgui.is_item_clicked(imgui.MouseButton_.left):
                route = state['custom_route']
                if not route or route[-1] != identifier:
                    route.append(identifier)

    @staticmethod
    def gameplay_json_table(records, title, filter_text, key):
        needle = filter_text.casefold()
        visible = [(identifier, value) for identifier, value in records.items()
                   if not needle or needle in str(identifier).casefold()
                   or needle in json.dumps(value, ensure_ascii=False).casefold()]
        imgui.text(f'{len(visible)} {title.lower()}')
        if not imgui.begin_table(f'gameplay-data-{key}', 2,
                                 imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v
                                 | imgui.TableFlags_.scroll_y | imgui.TableFlags_.resizable):
            return
        imgui.table_setup_column('Id', imgui.TableColumnFlags_.width_fixed, 100)
        imgui.table_setup_column('Data', imgui.TableColumnFlags_.width_stretch)
        imgui.table_headers_row()
        clipper = imgui.ListClipper()
        clipper.begin(len(visible))
        while clipper.step():
            for index in range(clipper.display_start, clipper.display_end):
                identifier, value = visible[index]
                imgui.table_next_row()
                imgui.table_next_column()
                imgui.text(str(identifier))
                imgui.table_next_column()
                imgui.text_wrapped(json.dumps(value, ensure_ascii=False, sort_keys=True))
        imgui.end_table()

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
        meshes = self.model_meshes(d, res)
        size = 1024 if res.type == mesh.T_INSTANCELIST else 1 << 14
        texs = {t: self.texture_image(d, t, size) for t in {m.texture for m in meshes if m.texture}}
        name = self.display_name(d, res)[0]
        ops.write_file(path, gltf.write_glb(meshes, texs, name))
        return len(meshes)

    def model_meshes(self, d, res):
        """The meshes of a model resource as shown (the LOD chosen in the 3D view)."""
        lod = self.model['lod'] if self.model['key'] and self.model['key'][1] == res.id else 0
        return mesh.decode_resource(d.b, res, self.mesh_lib, self.model_bundles(d)[1:], d.path, lod)[0]

    def export_fbx(self, d, res, path):
        """FBX with every mesh named after its renderable mesh (for Import FBX), the textures as PNG files in
        <name>_textures next to it; animations and skeletons with their bones (and their car, skinned) and every
        animation as a take. Returns (meshes, textures)."""
        name = self.display_name(d, res)[0]
        rig = None
        if res.type in (anim.T_ANIMLIST, anim.T_ANIMATION, anim.T_SKELETON):
            meshes, rig = self.anim_export_scene(d, res, name)
        else:
            meshes = self.model_meshes(d, res)
        size = 1024 if res.type == mesh.T_INSTANCELIST else 1 << 14
        files = self.fbx_texture_files(d, meshes, path, size)
        mats = self.fbx_materials(d, meshes, path, size)
        ops.write_file(path, fbx.write_fbx(meshes, files, name, meshimport.export_names(meshes, 'mesh'), rig, mats))
        return len(meshes), len(files) + sum(len(m['maps']) for m in mats.values())

    def fbx_materials(self, d, meshes, path, size):
        """Per material the look as PNG maps for an FBX: base colour (the car paint under the livery, the specular
        colour on metal), normal (RGB), roughness and metalness, and their constants. The game has no metalness:
        metal-like parts have a dark albedo and a bright specular colour (F0), which becomes Blender's metallic here;
        the specular A of the Alpha badge / wheel shaders (a mirror coat) too."""
        from PIL import Image
        folder = os.path.splitext(path)[0] + '_textures'
        paint = np.array(self.viewer.paint if self.viewer is not None else self.cfg.get('paint', (0.55, 0.05, 0.05)))
        out = {}

        def img(tid):
            return self.texture_image(d, tid, size) if tid else None

        def fit(a, shape):
            if a.shape[:2] == shape:
                return a
            return np.asarray(Image.fromarray(a).resize((shape[1], shape[0]), Image.BILINEAR))

        def save(a, fn):
            os.makedirs(folder, exist_ok=True)
            a = np.asarray(a, np.uint8)
            if a.shape[-1] == 3:
                a = np.concatenate([a, np.full(a.shape[:2] + (1,), 255, np.uint8)], -1)
            ops.save_png(np.ascontiguousarray(a), os.path.join(folder, fn))
            return os.path.basename(folder) + '/' + fn

        for m in meshes:
            if not m.material or m.material in out or not (m.texture or m.normal_tex or m.spec_tex or m.paint
                                                            or m.blend or m.lights_tex):
                continue
            if m.uv is None:
                continue
            diff, nrm, spec = img(m.texture), img(m.normal_tex), img(m.spec_tex)
            key = f'{m.material:016X}'
            maps = {}
            rough = m.rough if m.rough is not None else (0.25 if m.paint else 0.55)
            metal = 0.0
            base = diff.astype(np.float32) if diff is not None else None
            if m.paint:
                p = np.append(paint * 255.0, 255.0)
                if base is None:
                    base = np.tile(p, (4, 4, 1))
                else:
                    a = base[..., 3:4] / 255.0
                    base = np.concatenate([p[:3] * (1 - a) + base[..., :3] * a, np.full_like(a, 255.0)], -1)
            if spec is not None and m.spec_mode == 0:
                if base is None:
                    base = np.tile(np.append(np.array(m.tint) * 255.0, 255.0), (4, 4, 1)).astype(np.float32)
                s = fit(spec, base.shape[:2]).astype(np.float32)
                f0 = (s[..., :3] / 255.0) ** 2.2                # linear F0: 0.04 plastic .. 0.3+ metal
                mt = ((f0.max(-1, keepdims=True) - 0.04) / 0.26).clip(0, 1)
                if m.spec_alpha in (1, 2):                     # mirror coat / (PS3) metalness in A
                    mt = np.maximum(mt, s[..., 3:4] / 255.0)
                    s[..., :3] = np.maximum(s[..., :3], (s[..., 3:4] * 0.9) if m.spec_alpha == 1 else 0)
                base[..., :3] = base[..., :3] * (1 - mt) + s[..., :3] * mt
                maps['ReflectionFactor'] = save(np.repeat(mt * 255.0, 3, -1).clip(0, 255), f'{key}_metal.png')
                metal = 1.0
            if base is not None:
                if not (m.alpha_test or m.blend or 'alpha' in (m.shader or '').lower()):
                    base[..., 3] = 255                         # Blender takes A as opacity; the shader ignores it
                maps['DiffuseColor'] = save(np.clip(base, 0, 255), f'{key}_base.png')
            if nrm is not None:
                if m.normal_mode in (0, 3, 4):                 # 1, 2: the map holds only a roughness
                    n = nrm.copy()
                    n[..., 3] = 255
                    maps['NormalMap'] = save(n, f'{key}_normal.png')
                if m.spec_mode == 0 and m.normal_mode < 3:
                    ch = 1 if m.normal_mode == 1 else 3
                    maps['ShininessExponent'] = save(np.repeat(nrm[..., ch:ch + 1], 3, -1), f'{key}_rough.png')
                    rough = 1.0
                if m.normal_mode == 4 and m.alpha_test and 'DiffuseColor' in maps:
                    b = np.asarray(Image.open(os.path.join(folder, os.path.basename(maps['DiffuseColor']))))
                    b = np.array(fit(b, nrm.shape[:2]))
                    b[..., 3] = nrm[..., 3]                    # the cut-out lives in the normal map's A
                    maps['DiffuseColor'] = save(b, f'{key}_base.png')
            if spec is not None and m.spec_mode == 0 and m.spec_alpha == 3:
                maps['ShininessExponent'] = save(np.repeat(spec[..., 3:4], 3, -1), f'{key}_rough.png')
                rough = 1.0
            if spec is not None and m.spec_mode == 1:
                r = (255 - 0.85 * spec[..., 1:2].astype(np.float32)).clip(0, 255)
                maps['ShininessExponent'] = save(np.repeat(r, 3, -1), f'{key}_rough.png')
                rough = 1.0
            colour = tuple(paint) if m.paint else (1.0, 1.0, 1.0) if 'DiffuseColor' in maps else m.tint
            entry = {'colour': colour, 'rough': rough, 'metal': metal, 'maps': maps}
            if m.blend == 1:                           # see-through glass: Blender's alpha
                entry.update(opacity=max(m.opacity, 0.12), rough=0.05)
                if 'DiffuseColor' not in maps:
                    entry['colour'] = tuple(x ** (1 / 2.2) for x in (m.glass_tint or (0.012, 0.014, 0.016)))
            elif m.blend == 2:                         # colouring glass (tail lights): its colour, half see-through
                entry.update(opacity=0.6, rough=0.05, colour=tuple(x ** (1 / 2.2) for x in (m.glass_tint or (1, 0, 0))))
            lit = self.viewer.lights_on if self.viewer is not None else [0, 0, 0, 0]
            if m.lights_tex and m.lights_emit and m.lights_uv is m.uv and any(lit):
                lm = img(m.lights_tex)                 # the lit light masks in their colours, as an emission map
                if lm is not None:
                    e = sum(lm[..., k:k + 1].astype(np.float32) / 255.0 * np.array(m.light_colours[k]) * lit[k]
                            for k in range(4))
                    peak = float(e.max())
                    if peak > 0:
                        maps['EmissiveColor'] = save((e / peak * 255).clip(0, 255), f'{key}_emission.png')
                        entry['emission'] = min(peak, 50.0)
            out[m.material] = entry
        return out

    def fbx_texture_files(self, d, meshes, path, size):
        """The meshes' diffuse textures as PNG files in <name>_textures next to the FBX: {texture id: path}."""
        folder = os.path.splitext(path)[0] + '_textures'
        files = {}
        for t in sorted({m.texture for m in meshes if m.texture and m.uv is not None}):
            img = self.texture_image(d, t, size)
            if img is None:
                continue
            os.makedirs(folder, exist_ok=True)
            fn = f'{t:016X}.png'
            ops.save_png(img, os.path.join(folder, fn))
            files[t] = os.path.basename(folder) + '/' + fn
        return files

    def import_targets(self, d, res):
        """The meshes of every LOD of a model, for finding where an imported object belongs."""
        look = self.model_bundles(d)[1:]
        meshes, nlod = mesh.decode_resource(d.b, res, self.mesh_lib, look, d.path, 0)
        for lod in range(1, 4 if res.type == mesh.T_VGS else nlod):
            try:
                meshes = meshes + mesh.decode_resource(d.b, res, self.mesh_lib, look, d.path, lod)[0]
            except mesh.MeshError:
                break
        return meshes

    def import_fbx(self, d, res, path):
        """Write the objects of an FBX exported from this model (and edited) back into its renderables. Undoable."""
        try:
            with open(path, 'rb') as f:
                fms = fbx.load_meshes(f.read())
            targets = self.import_targets(d, res)
        except Exception as e:
            self.modal = {'kind': 'message', 'title': 'Import FBX', 'text': f'Cannot read {os.path.basename(path)}: {e}'}
            return False
        rids = sorted({k[0] for k in (meshimport.parse_name(m.name) for m in fms) if k and d.b.find(k[0])})
        if not rids:
            self.modal = {'kind': 'message', 'title': 'Import FBX', 'text':
                          'None of the objects in this file belongs to this model. Export FBX first, edit the objects '
                          '(keep their names, R<id>_<n>), then import that file here.'}
            return False
        d.checkpoint(f'import FBX {os.path.basename(path)}', rids)
        try:
            done, skipped = meshimport.import_fbx_meshes(d.b, targets, fms, self.mesh_lib, self.model_bundles(d),
                                                         roots.root_for(d.path, d.b.platform))
        except Exception as e:
            traceback.print_exc()
            d.do_undo()
            d.redo.clear()
            self.modal = {'kind': 'message', 'title': 'Import FBX failed', 'text': str(e)}
            return False
        for rid in done:
            self.changed(d, d.b.find(rid))
        nm = sum(len(v) for v in done.values())
        lines = [f'{nm} mesh(es) in {len(done)} renderable(s) replaced:']
        for rid, rep in done.items():
            for k, nv, nt, notes in rep:
                lines.append(f'  R{rid:016X}_{k}: {nv} vertices, {nt} triangles' + (f' ({"; ".join(notes)})' if notes else ''))
        copies = [s for s in skipped if (meshimport.parse_name(s) or (0, 0, 0))[2] > 0]
        other = [s for s in skipped if s not in copies]
        if copies:
            lines.append(f'{len(copies)} further copies (other wheels, ~n) follow their first object.')
        if other:
            lines.append(f'{len(other)} object(s) skipped (not from this model or in another bundle): '
                         + ', '.join(other[:8]) + (' ...' if len(other) > 8 else ''))
        self.modal = {'kind': 'message', 'title': 'Import FBX', 'text': chr(10).join(lines)}
        self.status = f'Imported {nm} mesh(es) from {os.path.basename(path)} (Ctrl+Z to undo).'
        return True

    def action_import_fbx(self, d, res):
        p = filedialog.open_file('Import FBX into this model', self.cfg.get('last_import', ''), filedialog.FBX)
        if p:
            self.remember('last_import', p)
            self.import_fbx(d, res, p)

    def cube_view(self, d, r):
        lut = ops.cube_lut(d.b, r)
        strip = ops.cube_strip(lut)
        big = np.ascontiguousarray(np.repeat(np.repeat(strip, 3, 0), 3, 1)[..., :3])
        imgui.text('16 x 16 x 16 colour-grading cube: 16 slices (blue) of red (x) by green (y).')
        immvision.image_display(f'##cube{r.id}{id(r.data(0))}', big, (768, 48), True, False)
        imgui.text_disabled('Export / replace as a 256 x 16 PNG (Export... / Replace...).')

    def text_view(self, d, r):
        key = (d.uid, r.id, id(r.data(0)))
        if luabc.is_bytecode(textfile.read(r, d.b.e)[:4]):
            self.lua_bytecode_view(d, r, key)
            return
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

    def lua_bytecode_view(self, d, r, key):
        """A compiled UI script: listing, constants (editable), export / replace."""
        st = self.lua_bc
        if st.get('key') != key:
            raw = textfile.read(r, d.b.e)
            try:
                chunk = luabc.read(raw)
                st.clear()
                st.update(key=key, chunk=chunk, listing=luabc.listing(chunk, ops.id_text(r.id)), dirty=False,
                          error=None, filter='')
            except luabc.LuaError as ex:
                st.clear()
                st.update(key=key, chunk=None, error=str(ex))
        if st.get('error'):
            imgui.text_colored(imgui.ImVec4(1.0, 0.35, 0.3, 1.0), f'Lua bytecode cannot be read: {st["error"]}')
            return
        chunk = st['chunk']
        funcs = luabc.functions(chunk)
        imgui.text(f'Lua 5.1 bytecode: {len(funcs)} functions, '
                   f'{sum(len(f.constants) for _, f in funcs)} constants')
        imgui.same_line()
        if imgui.button('Apply changes') and st['dirty'] and not d.b.truncated:
            d.checkpoint(f'lua constants {r.id:#x}', [r.id])
            ops.replace_text(d.b, r, luabc.write(chunk))
            self.changed(d, r)
            st['key'] = None
            self.status = f'Lua constants of {r.id:#x} updated (Ctrl+Z to undo).'
            return
        imgui.same_line()
        if imgui.button('Export .luac...'):
            self.action_export(d, r, 'luac')
        imgui.same_line()
        if imgui.button('Export listing...'):
            p = filedialog.save_file('Export Lua listing', os.path.join(self.cfg.get('last_export', ''),
                                     ops.id_text(r.id) + '.txt'), filedialog.TEXT)
            if p:
                self.remember('last_export', os.path.dirname(p))
                ops.write_file(p, st['listing'].encode('utf-8'))
                self.status = f'Listing written to {os.path.basename(p)}.'
        imgui.same_line()
        if imgui.button('Replace...') and not d.b.truncated:
            p = filedialog.open_file('Replace the script (.luac bytecode, or .lua source)',
                                     self.cfg.get('last_import', ''), filedialog.LUA)
            if p:
                self.remember('last_import', p)
                self.replace_from_file(d, r, p)
        if imgui.is_item_hovered():
            imgui.set_tooltip('.luac: Lua 5.1 bytecode (luac 5.1, 32-bit). .lua: source text; the game has the '
                              'Lua compiler, so it should load source as well (test it in game).')
        if st['dirty']:
            imgui.same_line()
            imgui.text_colored(imgui.ImVec4(1.0, 0.75, 0.3, 1.0), 'modified')
        if imgui.begin_tab_bar('luabc'):
            if imgui.begin_tab_item('Constants')[0]:
                imgui.set_next_item_width(260)
                _, st['filter'] = imgui.input_text_with_hint('##lfilter', 'filter', st['filter'])
                flt = st['filter'].lower()
                flags = (imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.resizable
                         | imgui.TableFlags_.scroll_y)
                if imgui.begin_table('luak', 3, flags):
                    imgui.table_setup_scroll_freeze(0, 1)
                    imgui.table_setup_column('Function', imgui.TableColumnFlags_.width_fixed, 150)
                    imgui.table_setup_column('#', imgui.TableColumnFlags_.width_fixed, 40)
                    imgui.table_setup_column('Value', imgui.TableColumnFlags_.width_stretch)
                    imgui.table_headers_row()
                    for path, f in funcs:
                        for i, k in enumerate(f.constants):
                            if flt and flt not in luabc.constant_text(k).lower():
                                continue
                            imgui.table_next_row()
                            imgui.table_next_column()
                            imgui.text_disabled(f'[{path}] line {f.line}' if path != '0' else 'main')
                            imgui.table_next_column()
                            imgui.text_disabled(str(i + 1))
                            imgui.table_next_column()
                            imgui.set_next_item_width(-1)
                            if isinstance(k, str):
                                ch, v = imgui.input_text(f'##k{path}.{i}', k)
                                if ch:
                                    try:
                                        v.encode('latin1')
                                        f.constants[i] = v
                                        st['dirty'] = True
                                    except UnicodeEncodeError:
                                        self.status = 'Lua strings take Latin-1 characters only.'
                            elif isinstance(k, bool):
                                ch, v = imgui.checkbox(f'##k{path}.{i}', k)
                                if ch:
                                    f.constants[i] = v
                                    st['dirty'] = True
                            elif isinstance(k, (int, float)):
                                ch, v = imgui.input_double(f'##k{path}.{i}', float(k), 0.0, 0.0, '%.10g')
                                if ch:
                                    f.constants[i] = v
                                    st['dirty'] = True
                            else:
                                imgui.text_disabled('nil')
                    imgui.end_table()
                imgui.end_tab_item()
            if imgui.begin_tab_item('Listing')[0]:
                imgui.input_text_multiline('##llist', st['listing'], imgui.ImVec2(-1, -1),
                                           imgui.InputTextFlags_.read_only)
                imgui.end_tab_item()
            imgui.end_tab_bar()

    def lua_view(self, d, r):
        key = (d.uid, r.id, id(r.data(0)))
        if self.lua_edit['key'] != key:
            try:
                source, encoding = luafile.decode(r.data(0))
            except (UnicodeDecodeError, ValueError) as e:
                imgui.text_wrapped(f'LuaCode is not editable as source: {e}')
                imgui.text_disabled(
                    'Export it as a resource file (.bres) or use Raw chunk (.bin) to preserve the original data.'
                )
                return
            self.lua_edit = {
                'key': key, 'text': source, 'encoding': encoding, 'dirty': False
            }
        editor = self.lua_edit
        if imgui.button('Apply changes') and editor['dirty'] and not d.b.truncated:
            try:
                data = luafile.encode(editor['text'], editor['encoding'])
            except UnicodeEncodeError as e:
                self.status = f'Lua source cannot be saved in its original encoding: {e}'
            else:
                d.checkpoint(f'lua source {r.id:#x}', [r.id])
                r.set_data(0, data)
                self.changed(d, r)
                editor['key'] = (d.uid, r.id, id(r.data(0)))
                editor['dirty'] = False
                self.status = f'Lua source {r.id:#x} updated (Ctrl+Z to undo).'
        imgui.same_line()
        imgui.text_disabled(
            ('modified · ' if editor['dirty'] else '')
            + f'{len(editor["text"])} characters ({editor["encoding"]})'
        )
        ch, editor['text'] = imgui.input_text_multiline(
            '##lua', editor['text'], imgui.ImVec2(-1, -1), imgui.InputTextFlags_.allow_tab_input
        )
        if ch:
            editor['dirty'] = True
        if imgui.button('Export .lua...'):
            self.action_export(d, r, 'lua')

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
        if imgui.begin_tab_bar('typeviews'):
            if imgui.begin_tab_item('Fields')[0]:
                animated = self.begin_tab_content_transition('typeviews', 'fields')
                flags = (imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v
                         | imgui.TableFlags_.scroll_y | imgui.TableFlags_.resizable)
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
                elif t.fields and imgui.begin_table('fields', 6, flags):
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
                self.end_tab_content_transition(animated)
                imgui.end_tab_item()
            if imgui.begin_tab_item('Folder')[0]:
                animated = self.begin_tab_content_transition('typeviews', 'folder')
                self.type_folder_view(d, r, t)
                self.end_tab_content_transition(animated)
                imgui.end_tab_item()
            if imgui.begin_tab_item('Inference')[0]:
                animated = self.begin_tab_content_transition('typeviews', 'inference')
                self.genesys_prediction_view(d, r, t)
                self.end_tab_content_transition(animated)
                imgui.end_tab_item()
            imgui.end_tab_bar()

    def type_folder_view(self, d, r, t):
        imgui.text_wrapped(
            'Type schemas are shown as folders. Struct and enum types expand into their fields or values; '
            'select a type name to open its schema.'
        )
        root_name = self.display_name(d, r)[0]
        label = f'{theme.FOLDER}  {root_name} ({self.types.name(t.id)})##type_folder_root'
        if imgui.tree_node_ex(label, imgui.TreeNodeFlags_.default_open
                              | imgui.TreeNodeFlags_.span_full_width):
            if t.fields or t.base:
                self.type_folder_contents(d, t, 'type.folder.root', {t.id}, 0)
            else:
                imgui.text_disabled('This type has no fields.')
            imports = r.imports()
            if imports and imgui.tree_node_ex(
                    f'{theme.FOLDER}  Type imports ({len(imports)})##type.folder.imports',
                    imgui.TreeNodeFlags_.span_full_width):
                for index, imp in enumerate(imports):
                    self.object_folder_reference(
                        d, imp.id, f'type.folder.import.{index}', imp.offset
                    )
                imgui.tree_pop()
            imgui.tree_pop()

    def type_folder_contents(self, d, t, path, ancestors, depth):
        if depth >= 32:
            imgui.text_disabled('Maximum folder depth reached.')
            return

        if t.base:
            base = self.types.get(t.base)
            base_name = self.types.name(t.base)
            base_is_folder = base is not None and bool(base.fields or base.base)
            if t.base in ancestors:
                base_is_folder = False
            base_flags = imgui.TreeNodeFlags_.span_full_width
            if not base_is_folder:
                base_flags |= (imgui.TreeNodeFlags_.leaf
                               | imgui.TreeNodeFlags_.no_tree_push_on_open
                               | imgui.TreeNodeFlags_.bullet)
            opened = imgui.tree_node_ex(
                f'{theme.FOLDER if base_is_folder else ""}  Base: {base_name}##{path}.base',
                base_flags
            )
            imgui.same_line()
            if imgui.small_button(f'{base_name}##{path}.base.link'):
                self.goto(t.base, d)
            if base_is_folder and opened:
                self.type_folder_contents(
                    d, base, f'{path}.base', ancestors | {t.base}, depth + 1
                )
                imgui.tree_pop()
            elif t.base in ancestors:
                imgui.same_line()
                imgui.text_disabled('(recursive type)')

        for field in t.fields:
            field_name = (self.labels.name(field.name_hash)
                          or genesys.hash_name(field.name_hash)
                          or f'{field.name_hash:08x}')
            child_type = self.types.get(field.type_id) if field.type_id else None
            child_is_folder = child_type is not None and bool(
                child_type.fields or child_type.base
            )
            recursive = child_is_folder and child_type.id in ancestors
            child_is_folder = child_is_folder and not recursive
            count = f' [{field.count}]' if field.count > 1 else ''
            enum_value = f' = {field.offset}' if t.kind == 5 else ''
            type_name = self.types.name(field.type_id) if field.type_id else f'{field.type_hash:#x}'
            suffix = (
                f'{enum_value}  type {type_name}, offset {field.offset:#x}, size {field.size}, '
                f'count {field.count}, align {1 << field.align}, flags {field_flags(field.flags)}'
            )
            tree_flags = imgui.TreeNodeFlags_.span_full_width
            if not child_is_folder:
                tree_flags |= (imgui.TreeNodeFlags_.leaf
                               | imgui.TreeNodeFlags_.no_tree_push_on_open
                               | imgui.TreeNodeFlags_.bullet)
            opened = imgui.tree_node_ex(
                f'{theme.FOLDER if child_is_folder else ""}  {field_name}{count}##{path}.{field.index}',
                tree_flags
            )
            if field.type_id:
                imgui.same_line()
                if imgui.small_button(f'{type_name}##{path}.{field.index}.type'):
                    self.goto(field.type_id, d)
            imgui.same_line()
            imgui.text_disabled(suffix)
            if recursive:
                imgui.same_line()
                imgui.text_disabled('(recursive type)')
            if child_is_folder and opened:
                self.type_folder_contents(
                    d, child_type, f'{path}.{field.index}', ancestors | {child_type.id}, depth + 1
                )
                imgui.tree_pop()

    def genesys_pc_executable(self, d):
        roots_to_check = []
        if d.path:
            root = roots.game_root(d.path)
            if root:
                roots_to_check.append(root)
        for folder in self.cfg.get('nav_folders', []):
            root = roots.game_root(os.path.join(folder, '_bndlx_probe'))
            if root:
                roots_to_check.append(root)
        fallback = roots.FALLBACK.get('PC')
        if fallback:
            roots_to_check.append(fallback)
        seen = set()
        for root in roots_to_check:
            normalized = os.path.normcase(os.path.abspath(root))
            if normalized in seen:
                continue
            seen.add(normalized)
            if roots.platform_of(root) != 'PC':
                continue
            exe = os.path.join(root, 'NFS13.exe')
            if os.path.isfile(exe):
                return exe
        return None

    def genesys_prediction_view(self, d, r, t, node=None):
        if t is None:
            imgui.text_wrapped('The Genesys type schema is not loaded, so a prediction cannot be generated.')
            return

        if node is None:
            source = genesys_insight.schema_source(self.types, t, self.labels)
        else:
            source = genesys_insight.object_source(self.types, node, self.labels, r.id)
        imgui.text_wrapped(
            'Generated pseudocode describes the schema and, for objects, serialized values. It is not the '
            'original source or executable code, and editing it does not change the resource.'
        )
        if imgui.button('Export inferred pseudocode...'):
            filename = f'{ops.id_text(r.id)}_inferred.txt'
            base = os.path.join(
                os.path.dirname(self.cfg.get('last_export', '') or (d.path or '')), filename
            )
            out_path = filedialog.save_file(
                'Export inferred Genesys pseudocode', base, filedialog.TEXT, 'txt'
            )
            if out_path:
                try:
                    ops.write_file(out_path, source.encode('utf-8'))
                    self.remember('last_export', out_path)
                    self.status = f'Exported inferred Genesys pseudocode to {out_path}.'
                except OSError as e:
                    self.status = f'Export failed: {e}'

        imgui.separator()
        imgui.text('PC executable assembly candidates')
        imgui.text_disabled(
            'Heuristic only: instructions are matched by field offsets. Common offsets can produce unrelated '
            'matches; these are not confirmed callers or decompiled functions.'
        )
        exe_path = self.genesys_pc_executable(d)
        if exe_path is None:
            imgui.text_wrapped(
                'No PC NFS13.exe was found for this bundle. Open a bundle inside the PC game folder or add the '
                'PC game folder in the navigation pane. PS3 executables are intentionally not used.'
            )
        else:
            cache_key = (
                os.path.normcase(os.path.abspath(exe_path)), t.id,
                tuple((f.offset, f.name_hash, self.labels.name(f.name_hash)) for f in t.fields),
            )
            state = self.genesys_insight_state.get(cache_key)
            if state is None:
                state = {'loading': False, 'candidates': None, 'error': None}
                self.genesys_insight_state[cache_key] = state
            imgui.text_disabled(os.path.basename(exe_path))
            imgui.begin_disabled(self.job is not None or state['loading'])
            if imgui.button('Find candidate references in NFS13.exe...'):
                state.update(loading=True, candidates=None, error=None)

                def scan(progress):
                    try:
                        return genesys_insight.assembly_candidates(
                            exe_path, t, self.labels, progress, self.types
                        )
                    except (OSError, ValueError, struct.error, genesys_insight.CsError) as e:
                        return e

                def scan_done(result):
                    state['loading'] = False
                    if isinstance(result, Exception):
                        state['error'] = str(result)
                    else:
                        state['candidates'] = result

                self.run_job(
                    f'Scanning PC executable for {self.types.name(t.id)} field references...',
                    scan, scan_done
                )
            imgui.end_disabled()
            if state['loading']:
                imgui.text_disabled('Scanning executable code sections in the background...')
            elif state['error']:
                imgui.text_colored(
                    imgui.ImVec4(1.0, 0.5, 0.4, 1.0), f'Assembly scan failed: {state["error"]}'
                )
            elif state['candidates'] is not None:
                report = genesys_insight.candidate_report(state['candidates'])
                if not state['candidates']:
                    imgui.text_disabled(
                        'No matching references in statically reachable code; the executable may contain packed '
                        'or indirectly dispatched code.'
                    )
                if imgui.button('Export candidate assembly...'):
                    filename = f'{ops.id_text(r.id)}_assembly_candidates.txt'
                    base = os.path.join(
                        os.path.dirname(self.cfg.get('last_export', '') or (d.path or '')), filename
                    )
                    out_path = filedialog.save_file(
                        'Export heuristic assembly candidates', base, filedialog.TEXT, 'txt'
                    )
                    if out_path:
                        try:
                            ops.write_file(out_path, report.encode('utf-8'))
                            self.remember('last_export', out_path)
                            self.status = f'Exported assembly candidates to {out_path}.'
                        except OSError as e:
                            self.status = f'Export failed: {e}'
                imgui.input_text_multiline(
                    '##genesys_assembly_candidates', report, imgui.ImVec2(-1, 230),
                    imgui.InputTextFlags_.read_only
                )
        imgui.input_text_multiline(
            '##genesys_inferred_source', source, imgui.ImVec2(-1, -1),
            imgui.InputTextFlags_.read_only
        )

    def field_label(self, h):
        n = self.labels.name(h)
        imgui.text(n if n else f'{h:08x}')
        if imgui.is_item_hovered():
            imgui.set_tooltip(f'name hash {h:08x}: {self.labels.source(h)}')
        if imgui.begin_popup_context_item(f'lbl{h}'):
            key = ('rename', h)
            if self.gcache.get(key) is None:
                self.gcache[key] = self.labels.name(h) or ''
            imgui.text(f'Name for {h:08x} (saved for every field with this hash):')
            ch, self.gcache[key] = imgui.input_text('##rename', self.gcache[key], imgui.InputTextFlags_.enter_returns_true)
            self.hash_check(h, self.gcache[key])
            if ch or imgui.button('OK'):
                self.labels.rename(h, self.gcache[key])
                self.gcache.pop(key, None)
                imgui.close_current_popup()
            imgui.end_popup()

    def object_view(self, d, r):
        event_catalog = None
        event_error = None
        try:
            event_catalog = event_data.parse(r.data(0))
        except event_data.EventDataError as exc:
            event_error = str(exc)
        key = (d.uid, r.id, id(r.data(0)), len(self.types.types))
        node = self.gcache.get(key)
        if node is None:
            try:
                node = genesys.Reader(self.types, d.b.e).read_resource(r)
            except genesys.MissingType as m:
                if event_catalog is not None:
                    self.event_catalog_view(d, r, event_catalog)
                    return
                imgui.text_wrapped(f'This object uses the Genesys type {m.args[0]:#x}, which is not in any open bundle. '
                                   'Open the bundle that holds its types (usually the same folder) to see the fields.')
                return
            except (struct.error, ValueError, IndexError, UnicodeDecodeError) as ex:
                if event_catalog is not None:
                    self.event_catalog_view(d, r, event_catalog)
                    return
                imgui.text_colored(imgui.ImVec4(1.0, 0.35, 0.3, 1.0), f'This object cannot be read: {ex}')
                return
            self.gcache[key] = node
        if event_error:
            imgui.text_colored(imgui.ImVec4(1.0, 0.35, 0.3, 1.0), f'Event catalog: {event_error}')
        self._obj_events = event_catalog is not None
        imgui.text(self.types.name(node.type))
        imgui.same_line()
        imgui.text_disabled('(Fields edits values; Folder shows nested structs and referenced resources)')
        rewritable = self.genesys_rewritable(d, r)
        if imgui.small_button('Export JSON...'):
            self.genesys_export_json(d, r, node)
        imgui.same_line()
        if imgui.small_button('Import JSON...') and not d.b.truncated:
            self.genesys_import_json(d, r)
        if imgui.is_item_hovered():
            imgui.set_tooltip('Set this object\'s fields from a JSON file (as Export JSON writes it; fields left out '
                              'stay as they are, a longer array copies its last element first)')
        imgui.same_line()
        if rewritable:
            imgui.text_disabled('arrays: + adds an element (a copy of the last), x removes one')
        else:
            imgui.text_disabled('(this object cannot be rewritten exactly: arrays and longer texts stay fixed)')
        self._obj_rewritable = rewritable
        self._obj_edit = None
        if not self.labels.found:
            imgui.push_text_wrap_pos(0.0)
            imgui.text_colored(imgui.ImVec4(1.0, 0.75, 0.3, 1.0), 'Bundled source maps name many fields already; '
                               'Find names (Home, or ... menu) can find more from the game files.')
            imgui.pop_text_wrap_pos()
        if imgui.begin_tab_bar('objviews'):
            tname = self.types.name(node.type)
            if tname in ('UILayout', 'Screen2') and imgui.begin_tab_item('Preview')[0]:
                animated = self.begin_tab_content_transition('objviews', 'preview')
                if tname == 'UILayout':
                    self.ui_layout_preview(d, r, node)
                else:
                    self.ui_screen_preview(d, r, node)
                self.end_tab_content_transition(animated)
                imgui.end_tab_item()
            if imgui.begin_tab_item('Fields')[0]:
                animated = self.begin_tab_content_transition('objviews', 'fields')
                flags = (imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v
                         | imgui.TableFlags_.resizable | imgui.TableFlags_.scroll_y)
                if imgui.begin_table('obj', 3, flags):
                    imgui.table_setup_scroll_freeze(0, 1)
                    imgui.table_setup_column('Field', imgui.TableColumnFlags_.width_fixed, 240)
                    imgui.table_setup_column('Value', imgui.TableColumnFlags_.width_stretch)
                    imgui.table_setup_column('Type', imgui.TableColumnFlags_.width_fixed, 140)
                    imgui.table_headers_row()
                    self.node_rows(d, r, node, 'n')
                    imgui.end_table()
                if self._obj_edit is not None:          # after the rows: the tree is not changed while drawn
                    fn, desc = self._obj_edit
                    self._obj_edit = None
                    self.genesys_rewrite(d, r, node, fn, desc)
                self.end_tab_content_transition(animated)
                imgui.end_tab_item()
            if imgui.begin_tab_item('Folder')[0]:
                animated = self.begin_tab_content_transition('objviews', 'folder')
                self.object_folder_view(d, r, node)
                self.end_tab_content_transition(animated)
                imgui.end_tab_item()
            if imgui.begin_tab_item('Inference')[0]:
                animated = self.begin_tab_content_transition('objviews', 'inference')
                self.genesys_prediction_view(d, r, self.types.get(node.type), node)
                self.end_tab_content_transition(animated)
                imgui.end_tab_item()
            if event_catalog is not None and imgui.begin_tab_item('Event catalog')[0]:
                animated = self.begin_tab_content_transition('objviews', 'events')
                self.event_catalog_view(d, r, event_catalog)
                self.end_tab_content_transition(animated)
                imgui.end_tab_item()
            imgui.end_tab_bar()

    def event_catalog_view(self, d, r, catalog):
        imgui.text(f'Event catalog: {len(catalog.records)} records at {catalog.table_offset:#x}; '
                   f'header id {catalog.unknown_id:#x}')
        if imgui.begin_tab_bar('event-catalog-tabs'):
            if imgui.begin_tab_item('Fields')[0]:
                self._event_fields_table(d, r, 'Event catalog', catalog.fields)
                selectable = [record for record in catalog.records if record.offset is not None]
                if selectable:
                    key = (d.uid, r.id)
                    state = self.event_view_state.setdefault(key, {'index': selectable[0].index})
                    records_by_index = {record.index: record for record in selectable}
                    if state['index'] not in records_by_index:
                        state['index'] = selectable[0].index
                    current = records_by_index[state['index']]
                    current_name = event_data.event_name(current) or current.kind
                    imgui.set_next_item_width(360)
                    if imgui.begin_combo('Event fields', f'#{current.index}: {current_name}'):
                        for record in selectable:
                            name = event_data.event_name(record) or record.kind
                            label = f'#{record.index}: {name}##event-field-{record.index}'
                            if imgui.selectable(label, state['index'] == record.index)[0]:
                                state['index'] = record.index
                        imgui.end_combo()
                    if current.fields:
                        imgui.text_disabled(f'{current.kind} fields at {current.offset:#x}; '
                                            'pointer/reference fields are read-only.')
                        self._event_fields_table(d, r, f'event-{current.index}', current.fields)
                        self._event_arrays_view(d, r, current)
                    else:
                        imgui.text_disabled(f'{current.kind} has no specialized field layout yet.')
                imgui.end_tab_item()
            if imgui.begin_tab_item(f'Event records ({len(catalog.records)})')[0]:
                App._event_records_table(catalog)
                imgui.end_tab_item()
            imgui.end_tab_bar()

    def _event_fields_table(self, d, r, table_id, fields):
        flags = (imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v
                 | imgui.TableFlags_.scroll_y | imgui.TableFlags_.resizable)
        if not imgui.begin_table(f'event-fields-{table_id}', 5, flags):
            return
        imgui.table_setup_column('Field', imgui.TableColumnFlags_.width_stretch)
        imgui.table_setup_column('Offset', imgui.TableColumnFlags_.width_fixed, 75)
        imgui.table_setup_column('Type', imgui.TableColumnFlags_.width_fixed, 165)
        imgui.table_setup_column('Value', imgui.TableColumnFlags_.width_stretch)
        imgui.table_setup_column('Edit', imgui.TableColumnFlags_.width_fixed, 42)
        imgui.table_headers_row()
        for name, offset, kind, value, fmt, editable in fields:
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.text(name)
            imgui.table_next_column()
            imgui.text(f'{offset:#x}')
            imgui.table_next_column()
            imgui.text_disabled(kind)
            imgui.table_next_column()
            if fmt is None:
                imgui.text_disabled('structured / raw bytes')
                imgui.table_next_column()
                imgui.text_disabled('-')
                continue
            can_edit = editable and not d.b.truncated
            imgui.begin_disabled(not can_edit)
            changed = False
            if kind == 'bool':
                changed, new_value = imgui.checkbox(f'##{table_id}-{offset:x}', bool(value))
                if changed:
                    value = int(new_value)
            elif kind == 'float':
                changed, value = imgui.input_float(f'##{table_id}-{offset:x}', float(value), 0, 0, '%.7g')
            elif can_edit:
                changed, text = imgui.input_text(f'##{table_id}-{offset:x}', str(value),
                                                 imgui.InputTextFlags_.enter_returns_true)
                if changed:
                    try:
                        value = int(text, 0)
                    except ValueError:
                        self.status = f'Invalid integer for {name}: {text}'
                        changed = False
            else:
                imgui.text(f'{value:#x}' if isinstance(value, int) else str(value))
            if changed:
                try:
                    self.write(d, r, offset, fmt, value)
                except (OverflowError, struct.error) as exc:
                    self.status = f'Cannot update {name}: {exc}'
            imgui.end_disabled()
            imgui.table_next_column()
            imgui.text_disabled('editable' if can_edit else 'read only')
        imgui.end_table()

    def _event_arrays_view(self, d, r, record):
        if not record.arrays:
            return
        imgui.separator()
        imgui.text('Inline and pointer-backed arrays')
        for array in record.arrays:
            label = f'{array.name} [{array.count}]##event-array-{record.index}-{array.offset:x}'
            if not imgui.tree_node_ex(label, imgui.TreeNodeFlags_.span_full_width):
                continue
            flags = (imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v
                     | imgui.TableFlags_.scroll_y | imgui.TableFlags_.resizable)
            if imgui.begin_table(f'event-array-table-{record.index}-{array.offset:x}', 3, flags):
                imgui.table_setup_column('Index', imgui.TableColumnFlags_.width_fixed, 70)
                imgui.table_setup_column('Offset', imgui.TableColumnFlags_.width_fixed, 90)
                imgui.table_setup_column('Value', imgui.TableColumnFlags_.width_stretch)
                imgui.table_headers_row()
                clipper = imgui.ListClipper()
                clipper.begin(array.count)
                while clipper.step():
                    for index in range(clipper.display_start, clipper.display_end):
                        offset = array.offset + index * struct.calcsize(array.fmt)
                        value = struct.unpack_from(array.fmt, r.data(0), offset)[0]
                        imgui.table_next_row()
                        imgui.table_next_column()
                        imgui.text(str(index))
                        imgui.table_next_column()
                        imgui.text(f'{offset:#x}')
                        imgui.table_next_column()
                        imgui.begin_disabled(d.b.truncated or not array.editable)
                        if array.kind == 'float':
                            changed, new_value = imgui.input_float(
                                f'##event-array-value-{record.index}-{offset:x}',
                                float(value), 0, 0, '%.7g')
                        else:
                            changed, text = imgui.input_text(
                                f'##event-array-value-{record.index}-{offset:x}',
                                str(value), imgui.InputTextFlags_.enter_returns_true)
                            new_value = value
                            if changed:
                                try:
                                    new_value = int(text, 0)
                                except ValueError:
                                    self.status = f'Invalid integer in {array.name}: {text}'
                                    changed = False
                        if changed:
                            try:
                                self.write(d, r, offset, array.fmt, new_value)
                            except (OverflowError, struct.error) as exc:
                                self.status = f'Cannot update {array.name}[{index}]: {exc}'
                        imgui.end_disabled()
                imgui.end_table()
            imgui.tree_pop()

    @staticmethod
    def _event_records_table(catalog):
        flags = (imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v
                 | imgui.TableFlags_.scroll_y | imgui.TableFlags_.resizable)
        if imgui.begin_table('event-catalog-records', 4, flags):
            imgui.table_setup_column('Index', imgui.TableColumnFlags_.width_fixed, 80)
            imgui.table_setup_column('Event', imgui.TableColumnFlags_.width_stretch)
            imgui.table_setup_column('Event id', imgui.TableColumnFlags_.width_fixed, 100)
            imgui.table_setup_column('Offset / type version', imgui.TableColumnFlags_.width_fixed, 180)
            imgui.table_headers_row()
            clipper = imgui.ListClipper()
            clipper.begin(len(catalog.records))
            while clipper.step():
                for index in range(clipper.display_start, clipper.display_end):
                    record = catalog.records[index]
                    imgui.table_next_row()
                    imgui.table_next_column()
                    imgui.text(str(record.index))
                    imgui.table_next_column()
                    name = event_data.event_name(record)
                    imgui.text(f'{name} ({record.kind})' if name else record.kind)
                    imgui.table_next_column()
                    imgui.text(f'{record.name_id:#x}' if record.name_id is not None else '-')
                    imgui.table_next_column()
                    if record.offset is None:
                        imgui.text_disabled('null')
                    else:
                        imgui.text(f'{record.offset:#x} / {record.type_version:08X}')
            imgui.end_table()

    def object_folder_view(self, d, r, node):
        imgui.text_wrapped(
            'Structs and arrays are shown as folders. Resource references are clickable '
            'files; selecting one opens its resource preview.'
        )
        root_name = self.display_name(d, r)[0]
        label = f'{theme.FOLDER}  {root_name} ({self.types.name(node.type)})##object_folder_root'
        if imgui.tree_node_ex(label, imgui.TreeNodeFlags_.default_open
                              | imgui.TreeNodeFlags_.span_full_width):
            self.object_folder_node(d, r, node, 'folder.root', set(), 0)
            imports = r.imports()
            if imports and imgui.tree_node_ex(
                    f'{theme.FOLDER}  Resource imports ({len(imports)})##folder.imports',
                    imgui.TreeNodeFlags_.span_full_width):
                for index, imp in enumerate(imports):
                    self.object_folder_reference(
                        d, imp.id, f'import_{imp.offset:x}_{index}', imp.offset
                    )
                imgui.tree_pop()
            imgui.tree_pop()

    def object_folder_node(self, d, r, node, path, seen_nodes, depth):
        if depth >= 32:
            imgui.text_disabled('Maximum folder depth reached.')
            return
        node_key = id(node)
        if node_key in seen_nodes:
            imgui.text_disabled('Already shown above.')
            return
        seen_nodes.add(node_key)
        schema = self.types.get(node.type)
        if schema is None:
            imgui.text_disabled(f'Missing Genesys type {node.type:#x}.')
            return
        for field in schema.fields:
            label = (self.labels.name(field.name_hash)
                     or genesys.hash_name(field.name_hash)
                     or f'{field.name_hash:08x}')
            self.object_folder_value(
                d, r, label, node.fields.get(field.name_hash),
                f'{path}.{field.name_hash:x}', seen_nodes, depth + 1,
                node.locs.get(field.name_hash)
            )

    def object_folder_value(self, d, r, label, value, path, seen_nodes, depth, loc=None):
        if isinstance(value, Node):
            folder_label = f'{theme.FOLDER}  {label} ({self.types.name(value.type)})##{path}'
            opened = imgui.tree_node_ex(
                folder_label, imgui.TreeNodeFlags_.span_full_width
            )
            if opened:
                self.object_folder_node(
                    d, r, value, path, seen_nodes, depth + 1
                )
                imgui.tree_pop()
            return

        if isinstance(value, Ref):
            self.object_folder_reference(d, value.id, path, label=label)
            return

        if isinstance(value, list) and any(
                isinstance(item, (Node, Ref, list)) for item in value):
            folder_label = f'{theme.FOLDER}  {label} [{len(value)}]##{path}'
            opened = imgui.tree_node_ex(
                folder_label, imgui.TreeNodeFlags_.span_full_width
            )
            if opened:
                for index, item in enumerate(value):
                    item_loc = None
                    if loc is not None and loc[1].flags & 2 and not loc[1].flags & 1:
                        field_type = self.types.get(loc[1].type_id)
                        stride = field_type.size if field_type is not None else loc[1].size
                        item_loc = (loc[0] + index * stride, loc[1])
                    self.object_folder_value(
                        d, r, f'[{index}]', item, f'{path}.{index}',
                        seen_nodes, depth + 1, item_loc
                    )
                imgui.tree_pop()
            return

        if isinstance(value, str):
            self.object_folder_string(d, r, label, value, path, loc)
            return

        if not self.dashboard_icon('text' if isinstance(value, str) else 'file', 16):
            icon, colour = theme.type_icon(T_TEXT if isinstance(value, str) else 0)
            theme.icon_text(icon, colour)
        imgui.same_line()
        display_value = fmt_value(value)
        if len(display_value) > 320:
            display_value = display_value[:317] + '...'
        imgui.text_wrapped(f'{label}: {display_value}')
        if imgui.is_item_hovered() and value is not None:
            imgui.set_tooltip(fmt_value(value))

    def object_folder_string(self, d, r, label, value, path, loc):
        if not self.dashboard_icon('text', 16):
            icon, colour = theme.type_icon(T_TEXT)
            theme.icon_text(icon, colour)
        imgui.same_line()
        imgui.text(label)
        if imgui.is_item_hovered():
            imgui.set_tooltip(value)
        if loc is None:
            imgui.same_line()
            imgui.text_disabled('(read only)')
            return
        imgui.same_line()
        if imgui.small_button(f'Edit##{path}.edit'):
            self.genesys_string_edit = {
                'doc': d.uid, 'rid': r.id, 'offset': loc[0], 'label': label,
                'name_hash': loc[1].name_hash, 'path': path, 'text': value, 'dirty': False,
            }
        imgui.same_line()
        if imgui.small_button(f'Export##{path}.export'):
            indexes = ''.join(re.findall(r'\.\d+', path))
            filename = f'{ops.id_text(r.id)}_{loc[1].name_hash:08X}{indexes}.txt'
            base = os.path.join(os.path.dirname(self.cfg.get('last_export', '') or (d.path or '')), filename)
            out_path = filedialog.save_file('Export Genesys text field', base, filedialog.TEXT, 'txt')
            if out_path:
                try:
                    ops.write_file(out_path, value.encode('latin1'))
                    self.remember('last_export', out_path)
                    self.status = f'Exported Genesys text field {label} to {out_path}.'
                except (OSError, UnicodeEncodeError) as e:
                    self.status = f'Export failed: {e}'

    def genesys_string_window(self):
        state = self.genesys_string_edit
        if state is None:
            return
        d = self.doc_by_uid(state['doc'])
        r = d.b.find(state['rid']) if d is not None else None
        if r is None:
            self.genesys_string_edit = None
            return
        opened, keep = imgui.begin(f'Edit Genesys text: {state["label"]}###genesys_string_editor')
        if opened:
            imgui.text_disabled('Text is stored as Latin-1 in the Genesys object.')
            ch, state['text'] = imgui.input_text_multiline(
                '##genesys_string', state['text'], imgui.ImVec2(620, 300),
                imgui.InputTextFlags_.allow_tab_input
            )
            if ch:
                state['dirty'] = True
            if imgui.button('Apply to object') and state['dirty'] and not d.b.truncated:
                if '\0' in state['text']:
                    self.status = 'Genesys text fields cannot contain NUL characters.'
                else:
                    try:
                        state['text'].encode('latin1')
                    except UnicodeEncodeError as e:
                        self.status = f'Text contains characters not supported by the game: {e}'
                    else:
                        if self.write_string(d, r, state['offset'], state['text']):
                            self.changed(d, r)
                            state['dirty'] = False
                            self.status = f'Genesys text field {state["label"]} updated (Ctrl+Z to undo).'
            imgui.same_line()
            if imgui.button('Export .txt...'):
                indexes = ''.join(re.findall(r'\.\d+', state['path']))
                filename = f'{ops.id_text(r.id)}_{state["name_hash"]:08X}{indexes}.txt'
                base = os.path.join(os.path.dirname(self.cfg.get('last_export', '') or (d.path or '')), filename)
                out_path = filedialog.save_file('Export Genesys text field', base, filedialog.TEXT, 'txt')
                if out_path:
                    try:
                        ops.write_file(out_path, state['text'].encode('latin1'))
                        self.remember('last_export', out_path)
                        self.status = f'Exported Genesys text field {state["label"]} to {out_path}.'
                    except (OSError, UnicodeEncodeError) as e:
                        self.status = f'Export failed: {e}'
            imgui.same_line()
            if imgui.button('Close'):
                self.genesys_string_edit = None
        imgui.end()
        if keep is False:
            self.genesys_string_edit = None

    def object_folder_reference(self, d, rid, path, offset=None, label=None):
        target_doc = d
        target = d.b.find(rid)
        if target is None:
            target_doc, target = self.where(rid)

        if target is None:
            label = f'-> {ops.id_text(rid)} (not open)##{path}'
            imgui.tree_node_ex(
                label, imgui.TreeNodeFlags_.leaf
                | imgui.TreeNodeFlags_.no_tree_push_on_open
                | imgui.TreeNodeFlags_.span_full_width
            )
            return

        name, _, tip = self.display_name(target_doc, target)
        if not self.dashboard_icon('file', 16):
            icon, colour = theme.type_icon(target.type)
            theme.icon_text(icon, colour)
        imgui.same_line()
        suffix = f' [{offset:#x}]' if offset is not None else ''
        selected = self.focus == (target_doc.uid, target.id)
        link_label = f'{label}: {name}' if label else name
        clicked = imgui.selectable(
            f'{link_label} ({type_name(target.type)}){suffix}##{path}', selected
        )[0]
        if imgui.is_item_hovered():
            imgui.set_tooltip(tip)
        if clicked:
            self.goto(rid, target_doc)

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
            self.label_tip(f.name_hash)
            self.label_menu(f.name_hash, path)
            imgui.table_next_column()
            imgui.text_disabled(f'[{n}]' if n is not None else '{...}')
            edit = (isinstance(v, list) and gjson.resizable(f) and getattr(self, '_obj_rewritable', False)
                    and not d.b.truncated)
            if edit:
                imgui.same_line()
                if imgui.small_button(f'+##add{path}'):
                    self._obj_edit = (lambda arr=v, fld=f: gjson.add_element(self.types, fld, arr),
                                      f'add an element to {name}')
                if imgui.is_item_hovered():
                    imgui.set_tooltip('Add an element (a copy of the last one) and rewrite the object')
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
                            hint = ''
                            if getattr(self, '_obj_events', False):     # events / challenges: their name by id
                                uid = x.fields.get(EVENT_ID)
                                if isinstance(uid, int) and uid:
                                    nm = event_data.EVENT_NAMES.get(uid) or event_data.EVENT_NAMES.get(uid - 1)
                                    hint = f'  {nm}' if nm else ''
                            op = imgui.tree_node_ex(f'[{i}] {self.types.name(x.type)}{hint}##{path}.{i}',
                                                    imgui.TreeNodeFlags_.span_full_width)
                            imgui.table_next_column()
                            if edit:
                                if imgui.small_button(f'duplicate##dup{path}.{i}'):
                                    self._obj_edit = (lambda arr=v, k=i: arr.insert(k + 1, gjson.clone(arr[k])),
                                                      f'duplicate element {i} of {name}')
                                if imgui.is_item_hovered():
                                    imgui.set_tooltip('Insert a copy of this element after it (ids and names inside '
                                                      'stay the same: change them to make it a new entry)')
                            imgui.table_next_column()
                            if edit and imgui.small_button(f'x##rm{path}.{i}'):
                                self._obj_edit = (lambda arr=v, k=i: arr.pop(k), f'remove element {i} of {name}')
                            if op:
                                self.node_rows(d, r, x, f'{path}.{i}')
                                imgui.tree_pop()
                        else:
                            self.value_row(d, r, f, x, None, f'{path}.{i}', label=f'[{i}]')
                            if edit:
                                imgui.same_line()
                                if imgui.small_button(f'x##rm{path}.{i}'):
                                    self._obj_edit = (lambda arr=v, k=i: arr.pop(k), f'remove element {i} of {name}')
                imgui.tree_pop()
            return
        imgui.tree_node_ex(f'{name}##{path}', imgui.TreeNodeFlags_.leaf | imgui.TreeNodeFlags_.no_tree_push_on_open
                           | imgui.TreeNodeFlags_.span_full_width | imgui.TreeNodeFlags_.bullet)
        if not label:
            self.label_tip(f.name_hash)
        self.label_menu(f.name_hash, path)
        imgui.table_next_column()
        imgui.set_next_item_width(-1)
        self.leaf_widget(d, r, f, ft, v, loc, path)
        imgui.table_next_column()
        imgui.text_disabled(tname)

    def label_tip(self, h):
        if imgui.is_item_hovered():
            imgui.set_tooltip(f'name hash {h:08x}: {self.labels.source(h)}')

    @staticmethod
    def hash_check(h, text):
        """Under a rename box: whether the typed name is the real one (its Genesys hash matches)."""
        if text.strip():
            ok = gnames.name_hash(text.strip()) == h
            imgui.text_colored(imgui.ImVec4(0.4, 0.85, 0.4, 1) if ok else imgui.ImVec4(0.6, 0.6, 0.6, 1),
                               'this is the real name: its hash matches' if ok else
                               f'not the real name (its hash is {gnames.name_hash(text.strip()):08x})')

    def label_menu(self, h, path):
        if imgui.begin_popup_context_item(f'ctx{path}'):
            key = ('rename', h)
            if self.gcache.get(key) is None:
                self.gcache[key] = self.labels.name(h) or ''
            imgui.text(f'Name for field {h:08x} (used everywhere this hash appears):')
            ch, self.gcache[key] = imgui.input_text('##rename', self.gcache[key], imgui.InputTextFlags_.enter_returns_true)
            self.hash_check(h, self.gcache[key])
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
            ch, txt = imgui.input_text(
                f'##{path}', v, imgui.InputTextFlags_.enter_returns_true
            )
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

    # -- Genesys rewriting (gwrite): arrays, longer texts, JSON ------------------------------------------------
    def genesys_rewritable(self, d, r):
        """Whether the object re-written unchanged gives its exact bytes (cached)."""
        key = ('rt', d.uid, r.id, id(r.data(0)), len(self.types.types))
        hit = self.gcache.get(key)
        if hit is None:
            try:
                hit = gwrite.roundtrips(self.types, r, d.b.e)[1]
            except Exception:
                hit = False
            self.gcache[key] = hit
        return hit

    def genesys_rewrite(self, d, r, root, mutate, desc, checkpoint=True):
        """Change the node tree with mutate(), then write the object again (undoable; checkpoint=False when the
        caller made the undo step already)."""
        try:
            mutate()
            data, off, cnt = gwrite.write(self.types, d.b.e, root)
        except Exception as ex:
            self.gcache.clear()
            self.status = f'Cannot rewrite {r.id:#x}: {ex}'
            return False
        if checkpoint:
            d.checkpoint(desc, [r.id])
        r.set_data(0, data)
        r.import_offset, r.import_count = off, cnt
        self.changed(d, r)
        self.status = f'{desc[0].upper() + desc[1:]} ({r.id:#x} rewritten; Ctrl+Z to undo).'
        return True

    def genesys_export_json(self, d, r, node):
        p = filedialog.save_file('Export as JSON', os.path.join(self.cfg.get('last_export', ''),
                                                                ops.id_text(r.id) + '.json'), filedialog.JSON)
        if not p:
            return
        self.remember('last_export', os.path.dirname(p))
        ops.write_file(p, gjson.dumps(self.types, self.labels, node, r.id).encode('utf-8'))
        self.status = f'{ops.id_text(r.id)} exported as {os.path.basename(p)}.'

    def genesys_import_json(self, d, r):
        p = filedialog.open_file('Import JSON into this object', self.cfg.get('last_import', ''), filedialog.JSON)
        if not p:
            return
        self.remember('last_import', p)
        self.genesys_apply_json_file(d, r, p)

    def genesys_apply_json_file(self, d, r, p):
        import json
        try:
            with open(p, encoding='utf-8') as fh:
                js = json.load(fh)
            root = genesys.Reader(self.types, d.b.e).read_resource(r)
        except Exception as ex:
            self.modal = {'kind': 'message', 'title': 'Import JSON', 'text': f'{os.path.basename(p)}: {ex}'}
            return False
        if not self.genesys_rewritable(d, r):
            self.modal = {'kind': 'message', 'title': 'Import JSON', 'text':
                          'This object cannot be rewritten exactly (its retail layout is not the writer\'s), so it is '
                          'not changed. Values can still be edited one by one in the Fields view.'}
            return False
        ok = self.genesys_rewrite(d, r, root, lambda: gjson.apply_json(self.types, self.labels, root, js),
                                  f'import {os.path.basename(p)}')
        if not ok:
            self.modal = {'kind': 'message', 'title': 'Import JSON', 'text': self.status}
        return ok

    @staticmethod
    def find_text_at(node, off):
        """(node, field hash) of the text field at absolute offset `off` in a node tree."""
        stack = [node]
        while stack:
            n = stack.pop()
            for h, (o, f) in n.locs.items():
                if o == off and f.type_id == genesys.STRINGBASE and not f.flags & 3:
                    return n, h
            for v in n.fields.values():
                if isinstance(v, Node):
                    stack.append(v)
                elif isinstance(v, list):
                    stack.extend(x for x in v if isinstance(x, Node))
        return None

    def write_string(self, d, r, off, text):
        e = d.b.e
        buf = bytearray(r.data(0))
        rel, ln = struct.unpack_from(e + 'II', buf, off)
        if '\0' in text:
            self.status = 'Genesys text fields cannot contain NUL characters.'
            return False
        try:
            raw = text.encode('latin1')
        except UnicodeEncodeError as ex:
            self.status = f'Text contains characters not supported by the game: {ex}'
            return False
        new_len = len(raw) + 1
        if new_len > ln and self.genesys_rewritable(d, r):
            root = genesys.Reader(self.types, d.b.e).read_resource(r)
            hit = self.find_text_at(root, off)
            if hit is not None:
                node_, fh = hit
                return self.genesys_rewrite(d, r, root, lambda: node_.fields.__setitem__(fh, text),
                                            f'edit text at {off:#x}')
        if new_len <= ln:
            buf[off + rel:off + rel + ln] = raw + b'\0' * (ln - len(raw))
        else:
            new_rel = len(buf) - off
            if new_rel > 0xFFFFFFFF or new_len > 0xFFFFFFFF:
                self.status = 'The Genesys text exceeds the 32-bit string offset or length limit.'
                return False
            buf.extend(raw + b'\0')
            struct.pack_into(e + 'I', buf, off, new_rel)
        struct.pack_into(e + 'I', buf, off + 4, new_len)
        d.edit_checkpoint(r.id)
        r.set_data(0, buf)
        self.gcache.clear()
        d.summary.pop(r.id, None)
        return True

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
                 'pick_chunk': 'Replace chunk', 'properties': 'Bundle properties', 'dat_import': 'Import .dat',
                 'plate': 'License plates', 'ginsu_options': 'Replace engine sound',
                 'compare': 'Bundle differences'}.get(m['kind'], 'Message')
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
            act = m.get('action')
            if act:
                if imgui.button(act[0]):
                    close = True
                    act[1]()
                imgui.same_line()
            if m.get('open') and imgui.button('Open it'):
                self.open_path(m['open'])
                close = True
                imgui.same_line()
            if imgui.button('OK', imgui.ImVec2(120, 0)) or imgui.is_key_pressed(imgui.Key.enter):
                close = True
        elif k == 'compare':
            left = self.doc_by_uid(m['left'])
            right = self.doc_by_uid(m['right'])
            if left is None or right is None:
                imgui.text_disabled('One of the compared bundles is no longer open.')
                close = True
            else:
                imgui.text(left.path)
                imgui.text(f'Compared with: {right.path}')
                if not m['rows']:
                    imgui.text_disabled('No resource or field differences found.')
                else:
                    imgui.text(f'{len(m["rows"])} difference(s)')
                    flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.scroll_y
                    if imgui.begin_table('bundle-differences', 4, flags, imgui.ImVec2(760, 420)):
                        imgui.table_setup_column('Resource', imgui.TableColumnFlags_.width_fixed, 150)
                        imgui.table_setup_column('Field', imgui.TableColumnFlags_.width_stretch)
                        imgui.table_setup_column(left.name, imgui.TableColumnFlags_.width_stretch)
                        imgui.table_setup_column(right.name, imgui.TableColumnFlags_.width_stretch)
                        imgui.table_headers_row()
                        for rid, field, value_a, value_b in m['rows']:
                            imgui.table_next_row()
                            imgui.table_next_column()
                            imgui.text(f'{rid:016X}')
                            imgui.table_next_column()
                            imgui.text_wrapped(field)
                            imgui.table_next_column()
                            imgui.text_wrapped(value_a)
                            imgui.table_next_column()
                            imgui.text_wrapped(value_b)
                        imgui.end_table()
                if imgui.button('Close', imgui.ImVec2(120, 0)):
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
            dashboard = getattr(self, 'dashboard_ui', None)
            hex_dirty = [document.path for document in getattr(dashboard, 'hex_documents', ())
                         if document.modified]
            names = ', '.join([d.name for d in self.docs if d.modified]
                              + (['the soundtrack editor'] if self.st_ui is not None and self.st_ui['model'].dirty else [])
                              + hex_dirty)
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
        elif k == 'ginsu_options':
            d = self.doc_by_uid(m['doc'])
            r = d.b.find(m['rid']) if d else None
            imgui.text(os.path.basename(m['path']) + ':  ' + m['src'])
            imgui.text_disabled('The sound now is ' + ('an off-load (engine braking) sweep, high to low RPM.'
                                                       if m['decel'] else 'an on-load sweep, low to high RPM.'))
            imgui.set_next_item_width(300)
            _, m['mode'] = imgui.combo('RPM', m['mode'], ['follow the pitch of the recording',
                                                           'a steady sweep between two RPMs'])
            if m['mode'] == 0:
                imgui.set_next_item_width(140)
                _, m['cyl'] = imgui.input_int('cylinders', m['cyl'])
                m['cyl'] = max(2, min(16, m['cyl']))
                imgui.set_next_item_width(140)
                _, m['start'] = imgui.input_float('RPM at the start', m['start'], 100.0, 500.0, '%.0f')
                imgui.push_text_wrap_pos(520)
                imgui.text_disabled('The engine fires cylinders / 2 times a revolution: the pitch gives the RPM, so '
                                    'the cylinders must be those of the recorded engine (V8: 8, flat six: 6). Set the RPM '
                                    'the recording starts at (idle for a rising sweep, the top for a falling one; '
                                    'within 30 %).')
                imgui.pop_text_wrap_pos()
                if imgui.button('Analyse'):
                    try:
                        g = ginsu.read(r, d.b.e)
                        audio, rate = eal3.read_audio(m['path'])
                        x = ginsu.recording_mono(audio, rate, g)
                        m['curve'] = ginsu.track_rpm(x, g.rate, m['cyl'], m['start'], g.decel)
                        m['curve_key'] = (m['cyl'], m['start'])
                    except Exception as e:
                        m['curve'], m['curve_key'] = None, None
                        self.status = f'Analysis failed: {e}'
                if m.get('curve') is not None:
                    t, rpm, strength = m['curve']
                    imgui.same_line()
                    stale = m.get('curve_key') != (m['cyl'], m['start'])
                    imgui.text(f'{rpm.min():.0f} - {rpm.max():.0f} RPM' + (' (settings changed: Analyse again)'
                                                                          if stale else ''))
                    weak = ginsu.weak_share(strength)
                    if weak > 0.3:
                        imgui.text_colored(imgui.ImVec4(1.0, 0.6, 0.2, 1.0),
                                           f'No clear engine pitch in {weak * 100:.0f} % of it: other cylinders or '
                                           f'start RPM, or a steady sweep?')
                    imgui.plot_lines('##rpmcurve', np.asarray(rpm, np.float32), 0, 'RPM through the recording',
                                     float(rpm.min()) * 0.95, float(rpm.max()) * 1.05, imgui.ImVec2(520, 90))
            else:
                imgui.set_next_item_width(140)
                _, m['lo'] = imgui.input_float('low RPM', m['lo'], 100.0, 500.0, '%.0f')
                imgui.set_next_item_width(140)
                _, m['hi'] = imgui.input_float('high RPM', m['hi'], 100.0, 500.0, '%.0f')
                imgui.text_disabled('The RPM rises (falls) evenly over the whole recording.')
            imgui.push_text_wrap_pos(520)
            imgui.text_disabled('The recording is cut into grains of one engine cycle (120 / RPM s) and encoded as '
                                'EA-XAS like the game\'s own; the sample rate stays as the sound now.')
            imgui.pop_text_wrap_pos()
            if imgui.button('Replace', imgui.ImVec2(120, 0)) and r is not None:
                self.modal = None
                imgui.close_current_popup()
                imgui.end_popup()
                self.ginsu_replace(d, r, m)
                return
            imgui.same_line()
            if imgui.button('Cancel', imgui.ImVec2(120, 0)):
                close = True
        elif k == 'dat_import':
            d = self.doc_by_uid(m['doc'])
            imgui.push_text_wrap_pos(640)
            imgui.text_disabled('Raw resources (.dat, as Bundle Manager exports them): chunk 0 with the import table '
                                'at its end. The id comes from the file name, the type is not stored: check both.')
            imgui.pop_text_wrap_pos()
            names = [f'{type_name(t)} ({t:#x})' for t in datfile.CHOICES]
            for i, row in enumerate(m['rows']):
                imgui.text(os.path.basename(row['path']))
                imgui.same_line(220)
                imgui.set_next_item_width(170)
                _, row['id'] = imgui.input_text(f'id##dat{i}', row['id'])
                imgui.same_line()
                imgui.set_next_item_width(220)
                if row['other'] is not None:
                    imgui.text(f'{type_name(row["other"])} (as in the bundle)')
                else:
                    _, row['type'] = imgui.combo(f'type##dat{i}', row['type'], names)
            if imgui.button('Import', imgui.ImVec2(120, 0)) and d is not None:
                news, notes = [], []
                for row in m['rows']:
                    try:
                        rid = int(row['id'].strip().replace('0x', ''), 16)
                        t = row['other'] if row['other'] is not None else datfile.CHOICES[row['type']]
                        r, n = datfile.load(row['path'], d.b, rid, t)
                        news.append(r)
                        notes += [f'{os.path.basename(row["path"])}: {x}' for x in n]
                    except (ValueError, OSError) as ex:
                        notes.append(f'{os.path.basename(row["path"])}: not imported: {ex}')
                for p in m['bres']:
                    try:
                        with open(p, 'rb') as f:
                            news.append(ops.import_bres(d.b, f.read(), self.types))
                    except Exception as ex:
                        notes.append(f'{os.path.basename(p)}: not imported: {ex}')
                self.modal = None
                imgui.close_current_popup()
                imgui.end_popup()
                if news:
                    self._confirmed_replace = True
                    self.add_resources(d, [ops.fit_stream(d.b, r) for r in news], f'import {len(news)} file(s)')
                    for r in news:
                        if r.type in (0x14, 0x15):
                            notes += [f'{ops.id_text(r.id)}: {x}' for x in datfile.check(d.b, self.types, r)]
                if notes:
                    extra = self.modal['text'] + chr(10) + chr(10) if self.modal and self.modal.get('kind') == 'message' else ''
                    self.modal = {'kind': 'message', 'title': 'Import .dat', 'text': extra + chr(10).join(notes)}
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
            close = self.plate_dialog(m) or close
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
(Alt+Left / Alt+Right / Alt+Up). The pane on the right shows the selected resource: textures (Full size or a
double click shows one at its full resolution; Replace swaps it for an image of any size; zoom, mips,
channels), Genesys objects (every field, editable), Genesys types, text files, the game's strings, colour
cubes, imports and the raw bytes (Hex). Genesys field names are hashes in the data: Find names recovers their real
names (hover a field for its source); right click a field to name it yourself (the box shows whether your name is
the real one).

Edit: values in the object view, strings, text, bytes in Hex; Rename (F2) changes a resource id (imports in
the bundle follow); Delete; Ctrl+Z / Ctrl+Y undo and redo. Save (Ctrl+S) keeps the original as .orig the first
time; a bundle saved without changes is byte-identical to the original.

Copy and paste, drag and drop (like Explorer):
- Ctrl+C / Ctrl+X, then Ctrl+V in another bundle: copies / moves resources (PS3 <-> PC converted as needed);
  the copied resources are also on the Windows clipboard as files (paste them into an Explorer folder);
- files copied in Explorer and pasted with Ctrl+V, or dropped on the window: a PNG / DDS / TGA / JPG onto a
  texture replaces it, a .txt onto a text file, a .lua onto a plain-text LuaCode resource, a .csv onto the strings,
  a .bres onto any resource;
  files named <resource id>.<ext> replace those resources; .bres files add resources; bundles open;
- drag resources onto another bundle's tab (or its entry in the navigation pane) to copy them;
- drag resources out of the window into Explorer to save them (textures as PNG or DDS: ... > Options).

Models (Renderable, Model): a 3D view with textures (left drag turns, right drag moves, wheel zooms); Textures...
lists the model's textures to view at full size or replace (the view follows); LOD
choice for models; Shaded shows the materials as the game shades them (normal and specular maps, roughness,
the channels each shader reads, ambient occlusion, clear-coated car paint in the colour chosen next to Export, see-through glass with
reflections, tinted tail-light glass, and the lamps: Lights / Brake light the car's light masks in the colours
of its materials), Export glTF (.glb with textures, opens in Blender) and Export FBX (the full materials as PNG maps in
<name>_textures: base colour with the paint, normal, roughness, metal; Blender links them to its material). Import FBX (or Replace with an .fbx) writes an edited FBX back: keep the object names Export
FBX gave (R<id>_<n>); join new parts into an existing object. Positions, UV sets and normals are replaced, the
rest (tangents, colours, damage weights) comes from the nearest original vertex. Works for renderables, models,
cars (VehicleGraphicsSpec) and track units; Ctrl+Z undoes it. Shaders, materials and shared textures are
found in the open bundles and in the game's global bundles (SHADERS, GLOBALMATERIALDICTIONARY, ...).

Materials: the shader, the textures by slot (Diffuse, Normal, Specular, ...) with thumbnails, and the shader
constants by name (PbrMaterialDiffuseColour, ...), editable. Go jumps to an open resource; Open opens the
bundle that has it (known after Find names).

Cars: select the VehicleGraphicsSpec of a VEH_* bundle to see the assembled car (body + wheels) in 3D; drag the
wheel positions / scales below the view (track width, wheelbase, ride height, wheel size; Mirror keeps it
symmetric), then Save.

Animations (Animation, AnimationList, Skeleton): a car's damage animations (its AnimationList, used by the
DamageBehaviour) play on the car itself: its body is skinned to the damage skeleton, so bumpers fall off,
bonnets pop up and doors swing as the damage grows. Other animations play on their skeleton (joints and bones)
with the paths the moving bones follow and a line for their forward axis (camera shots in EN_US FEEDBACKGROUPS).
Play / Pause, the time slider, Loop and speed; lists have a choice of their animations (front, rear left, ...).
A skeleton shows its bones on its car and lists them. Export FBX writes the bones, the car skinned to them and
every animation as a take (Blender: an armature with one action per animation). PS3 prototype animations and
cars work too.

ControlMesh (PS3 prototype cars): the crash deformation lattice, up to 64 points on the body drawn over the car
(grey = rigid, yellow .. red = how deep it can dent, the line = the way it moves); the max dent of each point
is editable in the table, and Dent crushes the car the prototype's way (every vertex follows its two control
points). The car view has a Control points switch for the same.

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

Engine sounds (GinsuEngineSound, in car bundles): the rev sweep with its RPM range and grains; play it,
Export WAV, or hold the engine at an RPM chosen with the slider. Replace with a recording (or drop a WAV / FLAC /
OGG / MP3 on it): a recording of an engine sweeping through its revs becomes the new sound; its RPM is followed
from the pitch (give the recorded engine's cylinders and start RPM; Analyse shows the curve) or taken as a steady
sweep between two RPMs.

Sounds (Wave): Play / Pause / Stop and a waveform with the play position (click it to jump); Export WAV; Replace (or drop) a WAV / FLAC / OGG / MP3 /
AIFF file: it is encoded as EALayer3 like every sound of the game (sample rate and channels as the old sound
by default). Streamed sounds are read from and written to their .SPS files (the original is kept as .orig).

Export / Replace / Import buttons: DDS, PNG, WAV, FBX, glTF, text, Lua source, CSV, .bres (one resource with all its data),
raw chunks.
... (See more) > Extract all / Import resources from folder work on whole bundles.

Convert: saves a PS3 bundle for PC or a PC bundle for PS3: textures, Genesys types and objects, colour cubes,
text files and strings are converted; meshes, materials and shaders are platform-specific and left out (the
report lists them). The result holds only what could be converted, so it is not a drop-in game bundle.

Port into a game bundle (... > Port into a PC game bundle...): a window in three steps. 1: the PS3 bundle with
the data you want. 2: the PC game's bundle of the same name. 3: where to save the result. "Scan the game and
preview" then reads the game folder (once; remembered) and lists exactly what will come from the PS3 bundle
(textures, colour cubes, texts, and Genesys objects whose schema is identical in both builds and that convert
back to the same bytes) and what stays as the game has it, with the reason for each; nothing is written until you
press "Write the ported bundle". The result has the game bundle's resources (same ids, types, order and count:
nothing is added or removed). The checkbox also converts objects whose data the schema does not fully describe
when it is clearly float32 (a guess).

The application uses a dark theme."""

ABOUT = f"""{APP} {VERSION}
Bundle explorer for Need for Speed: Most Wanted (2012): PC retail and PS3 prototype (bnd2 version 5).

Built with Dear ImGui (imgui-bundle). No game data is included; the program works on your own files.
Always keep backups of the game files you edit."""


ERROR_LOG = os.path.join(config_dir(), 'errors.log')


def log_error():
    """Append the current exception to %APPDATA%\\BNDLExplorer\\errors.log (kept short)."""
    try:
        text = f'--- {time.strftime("%Y-%m-%d %H:%M:%S")}  {APP} {VERSION}\n{traceback.format_exc()}\n'
        old = ''
        if os.path.isfile(ERROR_LOG):
            with open(ERROR_LOG, encoding='utf-8', errors='replace') as f:
                old = f.read()[-200000:]
        with open(ERROR_LOG, 'w', encoding='utf-8') as f:
            f.write(old + text)
    except OSError:
        pass


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
    params.imgui_window_params.background_color = imgui.ImVec4(*theme.PALETTE['bg'])
    params.callbacks.post_init = app.post_init
    params.callbacks.load_additional_fonts = theme.load_fonts
    params.callbacks.setup_imgui_style = theme.apply
    return params


def run(paths=(), screenshot=None, frames=12, select=None, tab=None, expand=False, view=None, folder=None,
        type_filter=None):
    from .new_ui import run as dashboard_run
    return dashboard_run(paths, screenshot, frames, select, tab, expand, view, folder, type_filter)
