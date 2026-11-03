"""UI layout editor with a live preview (mixed into App): the elements of a UILayout, or of all the layouts a
screen's widget shows, drawn as the game draws them (textured quads with their UVs, tint, opacity, masks as
clip rectangles, labels in the style's size and colour) against made-up game values.

Ctrl+C / Ctrl+X / Ctrl+V copy, cut and paste elements between layouts, screens and bundles (the materials,
textures, quads and styles they use come along; PS3 elements are converted field by field by name); a PNG
dropped on the canvas (or Image from PNG...) becomes an image element with its own texture.
Click selects, dragging moves, the corner handle resizes, arrow keys nudge (Shift: 10 px); Ctrl+T transforms
freely (8 handles: scale, Shift proportional, Alt from the centre; the knob rotates, Shift 15 degrees; Enter
applies as one undo step, Esc cancels); Ctrl+Z / Ctrl+Y (Ctrl+Shift+Z) undo / redo the edited bundle, Ctrl+J
duplicates, Delete deletes, Ctrl+D deselects, Ctrl+[ / Ctrl+] draw earlier / later. The panel edits
position, size, rotation, anchor, bindings, tint, opacity, text and the transition behaviours (fade in / out
...), which can be played in the preview. Values are written in place; adding / removing elements and
behaviours re-writes the layout object (gwrite), only when the object comes back byte for byte unchanged.
"""
import math
import os
import re
import struct
import time

import numpy as np

from imgui_bundle import imgui, immvision

from . import bindings as BD
from . import filedialog, genesys, gjson, gwrite, mesh, ops, raster, roots, uifont, uirender, uiscene as U
from .bundle import Bundle
from .genesys import Node, Ref, STRINGBASE
from .gnames import name_hash

T_TEXTURE = 0x01
T_RENDERABLE = 0x05
HANDLE = 9          # px
# the game's UI fonts (UI/FONTS/<id>.BNDL): what they are, as far as known
FONT_NOTES = {1206237: 'sans bold', 1206238: 'sans regular', 1206239: 'sans bold', 1206240: 'sans',
              581007: 'digits only', 97111: 'CJK (fallback)', 1022112: 'sans', 1022113: 'sans'}
REGULAR_FONTS = {1206238}
ADD_KINDS = (('Image', 'Image'), ('Label', 'Text'), ('Shape', 'Shape (filled)'), ('Mask', 'Mask'),
             ('MaskEnd', 'Mask end'))
STYLE_FIELDS = (('Font_Size', 'Size'), ('80ea78f4', 'Colour'), ('eeddd506', 'Effect colour'),
                ('Text_Effect', 'Effect'), ('EffectDistance', 'Effect distance'),
                ('Character_Spacing', 'Character spacing'), ('Line_Spacing', 'Line spacing'),
                ('Italics', 'Italics'), ('Italic_Angle', 'Italic angle'))
PROPS_FIELDS = (('unoO', 'Justification'), ('AutoEllipsis', 'Auto ellipsis'), ('WordWrap', 'Word wrap'),
                ('WordSquash', 'Word squash'), ('ColourBinding', 'Colour binding'))
JUSTIFY = ('Left', 'Centre', 'Right', 'Full')
TYPE_COLOURS = {0: (0.35, 0.75, 1.0, 0.9), 18: (0.35, 0.75, 1.0, 0.9), 1: (1.0, 0.65, 0.25, 0.9),
                5: (1.0, 0.55, 0.15, 0.9), 2: (0.95, 0.75, 0.3, 0.9), 3: (0.95, 0.75, 0.3, 0.9),
                4: (0.55, 0.9, 0.45, 0.9), 17: (0.55, 0.9, 0.45, 0.9), 8: (0.85, 0.5, 1.0, 0.9),
                9: (0.85, 0.5, 1.0, 0.9), 10: (0.85, 0.5, 1.0, 0.9), 13: (1.0, 0.45, 0.6, 0.9),
                7: (0.7, 0.7, 0.7, 0.9), 11: (0.5, 0.5, 0.5, 0.9)}
NEW_BEHAVIOURS = (('Fade in', 'in', 0, 'IN', 300), ('Fade out', 'out', 0, 'OUT', 300),
                  ('Slide in from the left', 'in', 5, '-600,0,EASIN', 300), ('Slide out to the left', 'out', 1,
                                                                             '-600,0,EASIN', 300),
                  ('Pop (scale 0 -> 100%)', 'in', 4, '0,100', 250), ('Pulse (idle scale)', 'idle', 4, '100,110', 800),
                  ('Spin (idle 360)', 'idle', 8, '360', 2000), ('Blink (idle fade)', 'idle', 0, 'OUT', 800),
                  ('Tint to white', 'in', 13, 'TO,WHITE', 300), ('Decode text in', 'in', 7, 'RANDOM,LINEAR,IN', 500))
LAYOUT_NEW = (('FADE', ['0', '300', 'IN']), ('APPROACH', ['0', '300', '-600', '0', 'EASEIN']),
              ('MOVE', ['0', '200', '-600', '0']))
TL_H = 190



def _hash(name):
    return int(name, 16) if len(name) == 8 and all(c in '0123456789abcdef' for c in name) else name_hash(name)


def _u32(c):
    r, g, b, a = (max(0.0, min(1.0, x)) for x in c)
    return imgui.color_convert_float4_to_u32(imgui.ImVec4(r, g, b, a))


class UIEditorUI:
    # -- state ----------------------------------------------------------------------------------------------
    def uied_state(self, key):
        if not hasattr(self, 'uied'):
            self.uied = {'tex': {}, 'quads': {}, 'styles': {}, 'bg': None, 'views': {}, 'shaders': {}}
        v = self.uied['views'].get(key)
        if v is None:
            v = self.uied['views'][key] = {
                'sel': None, 'drag': None, 'state': {}, 'respect_vis': True, 'unknown_visible': False,
                'show_hidden': False, 'show_masks': False, 'outlines': False, 'play': None, 'widget': 0,
                'zoom': 1.0, 'clip': True, 'bound_faded': True, 'hide': set(), 'lock': set(), 'snap': 1,
                'add_kind': 0, 'xf': None}
        return v

    # -- entry points (tabs of the object view, and the large window) --------------------------------------
    def ui_layout_preview(self, d, r, node, window=False):
        key = ('layout', d.uid, r.id)
        if not window and self.ui_in_window(key):
            return
        self.ui_editor(d, [(r, node)], key, window)

    def ui_in_window(self, key):
        """In the object view: the button for the large window, or a note when it shows this already."""
        self.uied_state(key)
        win = self.uied.get('window')
        if win is not None and win['key'] == key:
            imgui.text_disabled('Shown in the UI editor window.')
            imgui.same_line()
            if imgui.small_button('Close the window'):
                self.uied['window'] = None
            return True
        if imgui.button('Open in a large window'):
            self.uied['window'] = {'key': key, 'focus': True}
            return True
        imgui.same_line()
        if imgui.button('All screens...'):
            self.open_ui_browser()
            self.uied['window']['key'] = key
            return True
        imgui.same_line()
        imgui.text_disabled('(or edit here)')
        return False

    # -- every screen of the game (the browser) --------------------------------------------------------------
    def ui_game_roots(self):
        out = []
        for p in self.nav_roots() + [v for v in roots.FALLBACK.values() if v]:
            r = ops.game_root(p) or (p if os.path.isdir(os.path.join(p, 'UI')) else None)
            if r and os.path.normcase(r) not in [os.path.normcase(x) for x in out]:
                out.append(r)
        return out

    def open_ui_browser(self):
        """The UI editor with a list of every UI screen (HUDs first) of the game folders added to the app."""
        self.uied_state(('browser',))
        br = self.uied.setdefault('browser', {'screens': None, 'filter': '', 'huds': True, 'pending': None})
        win = self.uied.get('window') or {}
        self.uied['window'] = {'key': win.get('key'), 'focus': True, 'browser': True}
        if br['screens'] is None:
            self.ui_scan_screens()

    def ui_scan_screens(self):
        br = self.uied['browser']
        folders = self.ui_game_roots()
        if not folders:
            br['screens'] = []
            return
        from .labels import config_dir
        import json
        cpath = os.path.join(config_dir(), 'ui_screens.json')
        try:
            with open(cpath, encoding='utf-8') as fh:
                cache = json.load(fh)
        except (OSError, ValueError):
            cache = {}
        if cache.get('__version__') != 2:          # 2: PS3 prototype widgets / layouts are followed
            cache = {'__version__': 2}

        def work(progress):
            res = U.scan_screens(folders, progress, cache)
            try:
                with open(cpath, 'w', encoding='utf-8') as fh:
                    json.dump(cache, fh)
            except OSError:
                pass
            return res

        def done(res):
            br['screens'] = sorted(res, key=lambda s: (not s['hud'], s['platform'], s['name'].lower(), s['path']))

        if self.job is None:
            br['screens'] = []
            br['scan_later'] = False
            self.run_job('Reading the UI screens of the game', work, done)
        else:                                      # another job runs: start when it is done
            br['scan_later'] = True

    def ui_browser_list(self):
        br = self.uied['browser']
        win = self.uied['window']
        if br.get('scan_later') and self.job is None:
            self.ui_scan_screens()
        if imgui.small_button('Rescan'):
            self.ui_scan_screens()
        imgui.same_line()
        _, br['huds'] = imgui.checkbox('HUDs only', br['huds'])
        imgui.set_next_item_width(-1)
        _, br['filter'] = imgui.input_text_with_hint('##uibf', 'filter (name, id, file)', br['filter'])
        q = br['filter'].strip().lower()
        screens = br['screens'] or []
        if not screens:
            imgui.text_disabled('Reading the screens...' if self.job is not None else
                                'No UI screens found: add the game folder (Add a folder in the navigation pane).')
        cur = None
        if win.get('key'):
            d = self.doc_by_uid(win['key'][1])
            cur = os.path.normcase(d.path) if d is not None and d.path else None
        last_plat = None
        for s in screens:
            if br['huds'] and not s['hud']:
                continue
            fname = os.path.basename(s['path'])
            if q and q not in f'{s["name"]} {s["id"]} {fname}'.lower():
                continue
            if s['platform'] != last_plat:
                imgui.text_disabled(f'{s["platform"]}')
                last_plat = s['platform']
            label = f'{s["name"] or "?"}  {s["id"]}'
            if s['note']:
                label += f'  ({s["note"]})'
            if not fname.upper().startswith(str(s['id'])):
                label += f'  [{fname}]'
            if imgui.selectable(f'{label}##{s["path"]}', cur == os.path.normcase(s['path']))[0]:
                br['pending'] = s
            if imgui.is_item_hovered():
                imgui.set_tooltip(f'{s["path"]}\n{s["layouts"]} layouts')

    def ui_browser_open(self):
        """Open the screen picked in the list (at the start of the window, outside the frame's other panels)."""
        br = self.uied.get('browser') or {}
        s = br.get('pending')
        if not s:
            return
        br['pending'] = None
        d = self.open_path(s['path'])
        if d is None:
            return
        r = d.b.find(s['root'])
        if r is None:
            self.status = f'{os.path.basename(s["path"])}: its screen object is not there any more (Rescan).'
            return
        self.uied['window']['key'] = ('screen', d.uid, r.id)

    def ui_editor_window(self):
        """The editor in its own window over the app (called every frame)."""
        win = getattr(self, 'uied', {}).get('window')
        if not win:
            return
        if win.get('browser'):
            self.ui_browser_open()
        node = d = r = None
        if win.get('key'):
            kind, uid, rid = win['key']
            d = self.doc_by_uid(uid)
            r = d.b.find(rid) if d is not None else None
            node = self.ui_read(d, r) if r is not None else None
        if node is None and not win.get('browser'):
            self.uied['window'] = None
            return
        vp = imgui.get_main_viewport()
        imgui.set_next_window_pos(imgui.ImVec2(vp.work_pos.x + 24, vp.work_pos.y + 24), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size(imgui.ImVec2(vp.work_size.x - 48, vp.work_size.y - 48), imgui.Cond_.first_use_ever)
        if win.get('focus'):
            imgui.set_next_window_focus()
            win['focus'] = False
        title = (f'UI editor - {self.display_name(d, r)[0]}  ({r.id:#x})' if node is not None else 'UI editor') \
            + '###uieditor'
        _, keep = imgui.begin(title, True, imgui.WindowFlags_.no_collapse | imgui.WindowFlags_.no_scrollbar)
        self.uied['win_focus'] = imgui.is_window_focused(imgui.FocusedFlags_.root_and_child_windows)
        transforming = any(v.get('xf') for v in self.uied['views'].values())
        if not keep or (self.uied['win_focus'] and imgui.is_key_pressed(imgui.Key.escape) and not transforming
                        and not imgui.get_io().want_text_input):
            imgui.end()
            self.uied['window'] = None
            return
        if win.get('browser'):
            imgui.begin_child('##uibrowser', imgui.ImVec2(300, 0), imgui.ChildFlags_.borders)
            self.ui_browser_list()
            imgui.end_child()
            imgui.same_line()
            imgui.begin_child('##uiedright', imgui.ImVec2(0, 0))
        if node is None:
            imgui.text_disabled('Pick a screen on the left.')
        elif win['key'][0] == 'layout':
            self.ui_layout_preview(d, r, node, window=True)
        elif win['key'][0] == 'sequence':
            self.ui_sequence_preview(d, r, node, window=True)
        else:
            self.ui_screen_preview(d, r, node, window=True)
        if win.get('browser'):
            imgui.end_child()
        imgui.end()

    def ui_screen_preview(self, d, r, node, window=False):
        self.types.prefer = d.b.e
        if not window and self.ui_in_window(('screen', d.uid, r.id)):
            return
        node = self.ui_read(d, r) or node                 # read with this bundle's schemas
        widgets = U.screen_widgets(self.types, node, self.ui_resolver(d))
        if not widgets:
            imgui.text_disabled('This screen has no widgets with layouts.')
            return
        st = self.uied_state(('screen', d.uid, r.id))
        st['widget'] = min(st['widget'], len(widgets) - 1)
        imgui.set_next_item_width(260)
        names = [f'{n} ({len(ls)} layouts)' for n, ls in widgets]
        ch, st['widget'] = imgui.combo('Widget', st['widget'], names)
        if ch:
            st['sel'] = None
        items, missing = [], 0
        st['params'] = {lid: pid for lid, pid in widgets[st['widget']][1] if pid}
        for lid, _params in widgets[st['widget']][1]:
            lr = d.b.find(lid)
            if lr is None or lr.type != 0x15:
                missing += 1
                continue
            ln = self.ui_read(d, lr)
            if ln is not None:
                items.append((lr, ln))
        if missing:
            imgui.same_line()
            imgui.text_disabled(f'({missing} layouts are in other bundles and not shown)')
        self.ui_editor(d, items, ('screen', d.uid, r.id), window)

    def ui_sequence_preview(self, d, r, node, window=False):
        """A Sequence: its items on a time line (drag start / end), the layouts it shows at the play head drawn
        above with their transitions; binding-driven sequences run over the binding's value."""
        key = ('sequence', d.uid, r.id)
        if not window and self.ui_in_window(key):
            return
        st = self.uied_state(key)
        sq = st.setdefault('seq', {'t': 0.0, 'play': None, 'drag': None, 'sel': None})
        items = U.sequence_items(self.types, node)
        binding, end = U.sequence_axis(node, items)
        if sq['play'] is not None:
            sq['t'] = (time.time() - sq['play']) * (1.0 if not binding else end / 3.0)
            if sq['t'] > end:
                sq['t'], sq['play'] = end, None
        t = sq['t']
        state = dict(st['state'])
        if binding:
            state[U.binding_path(binding) or binding] = t
        ev = U.Evaluator(state)
        shown, params, times = [], {}, {}
        black = [0.0]

        def collect(its, t, on_binding, depth):
            for it in its:
                if not (it.start <= t < it.end or (it.end <= it.start and abs(t - it.start) < 1e-6)):
                    continue
                if it.enabled and not ev.visible(it.enabled, True):
                    continue
                if it.layout:
                    lr = d.b.find(it.layout)
                    ln = self.ui_read(d, lr) if lr is not None and lr.type == 0x15 else None
                    if ln is not None and all(x[0].id != lr.id for x in shown):
                        shown.append((lr, ln))
                        if it.params:
                            params[lr.id] = it.params
                        times[lr.id] = (t - it.start) * 1000.0 if not on_binding else 10000.0
                elif it.kind == 'BlackQuad':
                    span = max(it.end - it.start, 1e-6)
                    k = (t - it.start) / span
                    black[0] = max(black[0], min(1.0, 2 * min(k, 1 - k) * 2))
                elif it.sequence and depth < 4:          # a nested sequence: its own items on its own clock
                    sr = d.b.find(it.sequence)
                    sn = self.ui_read(d, sr) if sr is not None and sr.type == 0x15 else None
                    if sn is None:
                        continue
                    sub = U.sequence_items(self.types, sn)
                    sub_binding, sub_end = U.sequence_axis(sn, sub)
                    if sub_binding:
                        continue
                    if on_binding:                       # it loops while the parent's value keeps it on
                        local = time.time() % max(sub_end, 0.05)
                    else:
                        local = t - it.start
                    collect(sub, local, False, depth + 1)

        collect(items, t, bool(binding), 0)
        black = black[0]
        st['params'] = params
        st['layout_time'] = times
        st['black'] = black
        imgui.text(f'{len(items)} items; ' + (f'runs on {binding} (0 .. {end:g})' if binding else f'{end:g} s')
                   + f'; at {t:.2f}: {len(shown)} layout(s)')
        self.ui_editor(d, shown, key, window, bottom=lambda w: self.ui_sequence_timeline(d, r, node, st, items,
                                                                                         binding, end, w))

    def ui_sequence_timeline(self, d, r, node, st, items, binding, end, width):
        sq = st['seq']
        playing = sq['play'] is not None
        if imgui.small_button('Stop' if playing else 'Play'):
            sq['play'] = None if playing else time.time() - (0 if sq['t'] >= end else sq['t'])
            if not playing and sq['t'] >= end:
                sq['t'] = 0.0
        imgui.same_line()
        imgui.set_next_item_width(220)
        ch, v = imgui.slider_float('##seqt', sq['t'], 0.0, end, '%.2f')
        if ch:
            sq['t'], sq['play'] = v, None
        imgui.same_line()
        imgui.text_disabled(binding if binding else 'seconds')
        dl = imgui.get_window_draw_list()
        o = imgui.get_cursor_screen_pos()
        label_w, row_h, ruler = 190.0, 18.0, 16.0
        area = max(100.0, width - label_w - 24)
        k = area / end
        h = ruler + row_h * max(1, len(items)) + 4
        imgui.invisible_button('##seqhit', imgui.ImVec2(label_w + area, h))
        hovered = imgui.is_item_hovered()
        x0 = o.x + label_w
        dl.add_rect_filled(imgui.ImVec2(x0, o.y), imgui.ImVec2(x0 + area, o.y + ruler), _u32((0.2, 0.2, 0.23, 1)))
        mag = 10 ** math.floor(math.log10(max(end / 8, 1e-3)))
        step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= end / 8)     # at most ~8 labels
        tt = 0.0
        while tt <= end + 1e-9:
            x = x0 + tt * k
            dl.add_line(imgui.ImVec2(x, o.y + 6), imgui.ImVec2(x, o.y + ruler), _u32((1, 1, 1, 0.4)))
            dl.add_text(imgui.ImVec2(x + 2, o.y), _u32((1, 1, 1, 0.5)), f'{tt:g}')
            tt += step
        io = imgui.get_io()
        mx, my = io.mouse_pos.x, io.mouse_pos.y
        drag = sq['drag']
        hit = None
        colours = {'Layout': (0.35, 0.75, 1.0, 0.9), 'Wave': (0.55, 0.9, 0.45, 0.9), 'Conductor': (0.85, 0.5, 1.0, 0.9),
                   'BlackQuad': (0.45, 0.45, 0.45, 0.95), 'BusMixerChannel': (0.4, 0.7, 0.4, 0.9)}
        for i, it in enumerate(items):
            y = o.y + ruler + i * row_h + 2
            s0, s1 = it.start, it.end
            if drag is not None and drag['i'] == it.index:
                s0, s1 = drag['b0'], drag['b1']
            sel = sq['sel'] == it.index
            dl.add_text(imgui.ImVec2(o.x + 2, y), _u32((1, 0.8, 0.3, 1) if sel else (1, 1, 1, 0.8)),
                        f'{it.kind} {it.detail}'[:28])
            bx0, bx1 = x0 + s0 * k, x0 + max(s1 * k, s0 * k + 4)
            col = colours.get(it.kind, (0.8, 0.7, 0.4, 0.9))
            if it.enabled:
                col = (col[0] * 0.8, col[1] * 0.8, col[2] * 0.8, col[3])
            dl.add_rect_filled(imgui.ImVec2(bx0, y + 1), imgui.ImVec2(bx1, y + row_h - 3), _u32(col), 2)
            if bx1 - bx0 > 40:
                dl.add_text(imgui.ImVec2(bx0 + 3, y), _u32((0, 0, 0, 0.85)), it.kind)
            if hovered and drag is None and bx0 - 3 <= mx <= bx1 + 3 and y <= my <= y + row_h:
                hit = (it, 'end' if mx >= bx1 - 5 else 'start' if mx <= bx0 + 5 else 'move')
                imgui.set_tooltip(f'{it.kind} {it.detail}: {it.start:g} - {it.end:g}'
                                  + (f'\nenabled: {it.enabled}' if it.enabled else '')
                                  + '\ndrag: move, edges: start / end; double click a nested sequence opens it')
        px = x0 + min(sq['t'], end) * k
        dl.add_line(imgui.ImVec2(px, o.y), imgui.ImVec2(px, o.y + h), _u32((1, 0.3, 0.3, 1)), 1.5)
        if drag is not None:
            if imgui.is_mouse_down(0):
                dt = (mx - drag['mx']) / k
                q = step / 10 if not io.key_shift else step / 2
                snap = lambda v: max(0.0, round(v / q) * q)  # noqa: E731
                if drag['mode'] == 'move':
                    drag['b0'] = snap(drag['s0'] + dt)
                    drag['b1'] = drag['b0'] + (drag['s1'] - drag['s0'])
                elif drag['mode'] == 'start':
                    drag['b0'] = min(snap(drag['s0'] + dt), drag['s1'])
                else:
                    drag['b1'] = max(snap(drag['s1'] + dt), drag['s0'])
            else:
                sq['drag'] = None
                if (drag['b0'], drag['b1']) != (drag['s0'], drag['s1']):
                    it = next((x for x in items if x.index == drag['i']), None)
                    if it is not None:
                        self.ui_step(d, r, f'timing of sequence item {it.index}')
                        self.ui_set(d, r, it.node, {U.H_START: drag['b0'], U.H_END: drag['b1']},
                                    f'{it.kind} item {drag["b0"]:g} - {drag["b1"]:g}')
            return
        if hovered and imgui.is_mouse_down(0) and my <= o.y + ruler and mx >= x0:
            sq['t'], sq['play'] = max(0.0, min(end, (mx - x0) / k)), None
        elif hovered and hit is not None and imgui.is_mouse_double_clicked(0) and hit[0].sequence:
            self.goto(hit[0].sequence, d)
        elif hovered and imgui.is_mouse_clicked(0) and hit is not None and not d.b.truncated:
            it, mode = hit
            sq['sel'] = it.index
            sq['drag'] = {'i': it.index, 'mode': mode, 'mx': mx, 's0': it.start, 's1': it.end, 'b0': it.start,
                          'b1': it.end}

    def ui_resolver(self, d):
        def resolve(rid):
            r = d.b.find(rid)
            return self.ui_read(d, r) if r is not None and r.type == 0x15 else None
        return resolve

    def ui_read(self, d, r):
        key = ('uilayout', d.uid, r.id, id(r.data(0)), len(self.types.types))
        hit = self.gcache.get(key)
        if hit is None:
            try:
                hit = genesys.Reader(self.types, d.b.e).read_resource(r)
            except Exception:
                return None
            self.gcache[key] = hit
        return hit

    # -- resources ------------------------------------------------------------------------------------------
    def ui_texture(self, d, tid, additive=False):
        """(GL texture, w, h) of a texture (decoded once per data). additive: a version that, drawn with the
        usual alpha blending, looks like the additive shader's result (black = see-through)."""
        if not tid:
            return None
        cache = self.uied['tex']
        img = self.texture_image(d, tid, 1024)
        if img is None:
            return None
        key = (tid, id(img), additive)
        hit = cache.get(key)
        if hit is None:
            for k in [k for k in cache if k[0] == tid and k[2] == additive]:
                del cache[k]
            src = img
            if additive:
                rgb = img[..., :3].astype(np.float32)
                m = rgb.max(axis=-1)
                out = np.empty_like(img)
                out[..., :3] = np.clip(rgb * (255.0 / np.maximum(m, 1.0))[..., None], 0, 255).astype(np.uint8)
                out[..., 3] = np.clip(img[..., 3].astype(np.float32) * m / 255.0, 0, 255).astype(np.uint8)
                src = out
            hit = cache[key] = (immvision.GlTexture(src), img.shape[1], img.shape[0], img)
        return hit

    def ui_shader(self, d, uimat):
        """The shader name of an element's UIMaterial (its Material is a GameChanger id of a material in the
        bundle or GLOBALMATERIALDICTIONARY; the name is read from the shader resource)."""
        if not uimat:
            return ''
        cache = self.uied['shaders']
        key = (d.uid, uimat)
        if key in cache:
            return cache[key]
        name = ''
        try:
            r = d.b.find(uimat)
            n = self.ui_read(d, r) if r is not None and r.type == 0x15 else None
            gc = n.fields.get(U.H_MATERIAL) if n is not None else None
            if isinstance(gc, int) and gc:
                root = roots.root_for(d.path, d.b.platform)
                mb, mr = self.mesh_lib.find((1 << 56) | gc, self.model_bundles(d), root)
                if mr is not None:
                    imps = {i.offset: i.id for i in mr.imports()}
                    sh = imps.get(0x8) or next(iter(imps.values()), None)
                    sb, sr = self.mesh_lib.find(sh, self.model_bundles(d), root) if sh else (None, None)
                    for c in range(3):
                        if sr is None:
                            break
                        try:
                            m = re.search(rb'UI[A-Za-z0-9_]*Shader', bytes(sr.data(c)))
                        except Exception:
                            m = None
                        if m:
                            name = m.group(0).decode()
                            break
        except Exception:
            name = ''
        cache[key] = name
        return name

    def ui_mode(self, d, el):
        """How an image draws: 'mirror' (the game's mirror camera), 'minimap', 'bound' (the picture comes from
        the game: profile pictures, icons, map tiles; the stored texture is a stand-in), 'additive', 'normal'."""
        shader = self.ui_shader(d, el.material)
        if el.image_bind == 'Graphics.RearViewMirror' or 'Mirror' in shader:
            return 'mirror'
        if el.kind == 'MiniMap' or 'RoadRibbon' in shader:
            return 'minimap'
        if el.image_bind:
            return 'bound'
        if 'Additive' in shader:
            return 'additive'
        return 'normal'

    def ui_quad(self, d, qid):
        """The quad's corners: [(u, v)] for top-left, top-right, bottom-right, bottom-left (None: 0..1)."""
        if not qid:
            return None
        cache = self.uied['quads']
        if qid in cache:
            return cache[qid]
        uv = None
        try:
            root = roots.root_for(d.path, d.b.platform)
            b, res = self.mesh_lib.find(qid, self.model_bundles(d), root)
            if res is not None and res.type == T_RENDERABLE:
                m = mesh.decode_renderable(b, res, self.mesh_lib, self.model_bundles(d), root)[0]
                pts = {(round(float(p[0])), round(float(p[1]))): (float(t[0]), float(t[1]))
                       for p, t in zip(m.pos, m.uv)}
                uv = [pts.get(k, k) for k in ((0, 0), (1, 0), (1, 1), (0, 1))]
        except Exception:
            uv = None
        cache[qid] = uv
        return uv

    def ui_style(self, d, sid):
        """{size, colour, font, spacing, italic} of a TextStyle object (None values when unknown)."""
        r = d.b.find(sid) if sid else None
        cache = self.uied['styles']
        key = (d.uid, sid, id(r.data(0)) if r is not None else None)
        if key in cache:
            return cache[key]
        out = {'size': None, 'colour': None, 'font': None, 'spacing': 0.0, 'italic': 0.0}
        if r is not None and r.type == 0x15:
            n = self.ui_read(d, r)
            if n is not None:
                f = n.fields
                col = f.get(U.H_STYLE_COLOUR)
                out = {'size': f.get(U.H_FONTSIZE), 'colour': [float(x) for x in col] if isinstance(col, list) else None,
                       'font': f.get(name_hash('Font_Asset')),
                       'spacing': float(f.get(name_hash('Character_Spacing')) or 0.0),
                       'italic': float(f.get(name_hash('Italic_Angle')) or 0.0) if f.get(name_hash('Italics')) else 0.0}
        cache[key] = out
        return out

    def ui_text(self, d, el, ev):
        if el.string_id:
            s = self.ui_strings(d).get(el.string_id)
            if s is not None:
                return s
        if el.text:
            v = ev.value(el.text)
            if v is not None:
                return f'{v:.0f}'                              # HUD numbers are whole numbers
            path = U.binding_path(el.text)
            if path and '.' in path:
                return '[' + path.split('.')[-1] + ']'
            return el.text
        return ''

    # -- the editor -----------------------------------------------------------------------------------------
    def ui_editor(self, d, items, key, window=False, bottom=None):
        self.types.prefer = d.b.e
        st = self.uied_state(key)
        state = dict(st['state'])
        if st.get('live'):
            state.update(U.live_state(time.time()))
        ev = U.Evaluator(state)
        layouts = [(r, n, U.elements(self.types, n)) for r, n in items]
        avail = imgui.get_content_region_avail()
        panel = 420 if window else 360
        tl_h = TL_H if st.get('timeline', True) else 26
        cw = max(320.0, avail.x - panel - 12)
        ch = cw * U.H / U.W
        if ch > avail.y - tl_h - 12:
            ch = max(180.0, avail.y - tl_h - 12)
            cw = ch * U.W / U.H
        imgui.begin_group()
        imgui.begin_child('##uicanvas', imgui.ImVec2(cw, ch))
        self.ui_canvas(d, st, layouts, ev, cw, ch)
        focus = imgui.is_window_focused(imgui.FocusedFlags_.child_windows)
        imgui.end_child()
        imgui.begin_child('##uitimeline', imgui.ImVec2(cw, tl_h), imgui.ChildFlags_.borders)
        if bottom is not None:
            bottom(cw)
        else:
            self.ui_timeline(d, st, layouts, cw)
        focus = focus or imgui.is_window_focused(imgui.FocusedFlags_.child_windows)
        imgui.end_child()
        imgui.end_group()
        imgui.same_line()
        imgui.begin_child('##uipanel', imgui.ImVec2(0, max(ch, avail.y - 4)))
        self.ui_panel(d, st, layouts, ev)
        focus = focus or imgui.is_window_focused(imgui.FocusedFlags_.child_windows)
        imgui.end_child()
        if window:
            focus = self.uied.get('win_focus', False)
        if focus:
            self.uied['focus_frame'] = imgui.get_frame_count()
            self.uied['focus_doc'] = d.uid
            self.ui_keys(d, st, layouts)

    def ui_canvas(self, d, st, layouts, ev, cw, ch):
        dl = imgui.get_window_draw_list()
        o = imgui.get_cursor_screen_pos()
        s = cw / U.W

        def P(x, y):
            return imgui.ImVec2(o.x + x * s, o.y + y * s)

        imgui.invisible_button('##uihit', imgui.ImVec2(cw, ch))
        hovered = imgui.is_item_hovered()
        key = next((k for k, v in self.uied['views'].items() if v is st), None)
        self.uied['canvas'] = {'frame': imgui.get_frame_count(), 'rect': (o.x, o.y, cw, ch, s, d.uid, key),
                               'first': layouts[0][0].id if layouts else None}
        now = time.time()
        play = st['play']
        if play is not None:
            t = (now - play[1]) * 1000.0
            if play[0] != 'idle' and t > self.ui_anim_length(d, st, layouts, play[0]) + 400:
                st['play'] = play = None
        fbs = imgui.get_io().display_framebuffer_scale.x or 1.0
        k = s * fbs                                          # layout units -> render target pixels
        ops, shapes, overlays = self.ui_build(d, st, layouts, ev, play, now, k)
        tex = None
        if st.get('gl', True):
            if not hasattr(self, 'uirender'):
                self.uirender = uirender.UIRenderer()
            bg = []
            if self.uied['bg'] is not None:
                bg = [('quad', self.uied['bg'][0].texture_id, [(0, 0), (U.W, 0), (U.W, U.H), (0, U.H)],
                       [(0, 0), (1, 0), (1, 1), (0, 1)], (1, 1, 1, 1), 'alpha')]
            scaled = [op if op[0] == 'unmask' else
                      (op[0], op[1], [(x * k, y * k) for x, y in op[2]]) + tuple(op[3:]) for op in bg + ops]
            tex = self.uirender.render(cw * fbs, ch * fbs, scaled)
        if tex is not None:
            dl.add_image(imgui.ImTextureRef(tex), o, imgui.ImVec2(o.x + cw, o.y + ch), imgui.ImVec2(0, 1),
                         imgui.ImVec2(1, 0))
        else:                                                # no GL renderer: ImGui quads (masks as rectangles)
            dl.add_rect_filled(o, imgui.ImVec2(o.x + cw, o.y + ch), _u32((0.11, 0.12, 0.14, 1)))
            dl.push_clip_rect(o, imgui.ImVec2(o.x + cw, o.y + ch), True)
            clips = 0
            for op in ops:
                if op[0] == 'mask':
                    xs, ys = [p[0] for p in op[2]], [p[1] for p in op[2]]
                    dl.push_clip_rect(P(min(xs), min(ys)), P(max(xs), max(ys)), True)
                    clips += 1
                elif op[0] == 'unmask':
                    if clips:
                        dl.pop_clip_rect()
                        clips -= 1
                else:
                    _, t, pts, uv, rgba, _blend = op
                    q = [P(*p) for p in pts]
                    dl.add_image_quad(imgui.ImTextureRef(t or self.ui_white()), q[0], q[1], q[2], q[3],
                                      imgui.ImVec2(*uv[0]), imgui.ImVec2(*uv[1]), imgui.ImVec2(*uv[2]),
                                      imgui.ImVec2(*uv[3]), _u32(rgba))
            for _ in range(clips):
                dl.pop_clip_rect()
            dl.pop_clip_rect()
        dl.push_clip_rect(o, imgui.ImVec2(o.x + cw, o.y + ch), True)
        for pts, colour, text in overlays:
            self.ui_outline(dl, P, pts, colour)
            if text:
                dl.add_text(P(*pts[0]), _u32(colour), text)
        cur = None
        sel = st['sel']
        for r, el, pts in shapes:
            if sel == (r.id, el.index) and st['xf'] is not None and st['xf']['key'] == sel:
                cur = (r, el, pts)
                self.ui_xf_draw(dl, P, st['xf'], s)
                continue
            if sel == (r.id, el.index):
                cur = (r, el, pts)
                self.ui_outline(dl, P, pts, (1.0, 0.75, 0.1, 1), 2.0)
                hx, hy = P(*pts[2])
                dl.add_rect_filled(imgui.ImVec2(hx - HANDLE / 2, hy - HANDLE / 2),
                                   imgui.ImVec2(hx + HANDLE / 2, hy + HANDLE / 2), _u32((1.0, 0.75, 0.1, 1)))
                ax_, ay_ = P(el.x, el.y)
                dl.add_circle(imgui.ImVec2(ax_, ay_), 4, _u32((1.0, 0.75, 0.1, 1)), 0, 1.5)
        dl.pop_clip_rect()
        self.ui_mouse(d, st, shapes, cur, o, s, hovered)

    def ui_white(self):
        if 'white' not in self.uied:
            self.uied['white'] = immvision.GlTexture(np.full((4, 4, 4), 255, np.uint8))
        return self.uied['white'].texture_id

    def ui_stand_in_texture(self, mode):
        """What the game draws there itself: the mirror camera (sky over road) or the minimap (a dark disc)."""
        key = 'stand-in-' + mode
        if key not in self.uied:
            if mode == 'mirror':
                v = np.linspace(0, 1, 64)[:, None]
                sky = np.array([110, 128, 150], np.float32)
                road = np.array([40, 42, 46], np.float32)
                rgb = np.where(v < 0.55, sky * (1 - v * 0.6), road + (1 - v) * 20)
                img = np.dstack([np.repeat(rgb[:, None, c], 64, 1) for c in range(3)]
                                + [np.full((64, 64), 245, np.float32)]).astype(np.uint8)
            else:
                yy, xx = np.mgrid[0:128, 0:128]
                r = np.hypot(xx - 63.5, yy - 63.5) / 63.5
                img = np.zeros((128, 128, 4), np.uint8)
                img[..., 0], img[..., 1], img[..., 2] = 26, 30, 34
                img[..., 3] = np.clip((1.0 - r) * 400, 0, 215).astype(np.uint8)
                road = np.abs((xx - 64) * 0.5 + (yy - 64)) < 3                       # a road through the middle
                img[road & (r < 0.95)] = (200, 200, 200, 220)
            self.uied[key] = immvision.GlTexture(img)
        return self.uied[key].texture_id

    def ui_game_font(self, d, fid):
        """(Font, GL atlas) of a game font id: from an open bundle, else UI/FONTS/<id>.BNDL of the game."""
        if not isinstance(fid, int) or not fid:
            return None
        cache = self.uied.setdefault('gfonts', {})
        if fid in cache:
            return cache[fid]
        hit = None
        try:
            rid = uifont.font_resource_id(fid)
            b = next((o.b for o in self.docs if (o.b.find(rid) is not None and o.b.find(rid).type == uifont.T_FONT)),
                     None)
            if b is None:
                root = roots.root_for(d.path, d.b.platform)
                p = os.path.join(root, 'UI', 'FONTS', f'{fid}.BNDL') if root else None
                b = Bundle.open(p) if p and os.path.isfile(p) else None
            if b is not None:
                font, fr, tex = uifont.from_bundle(b)
                atlas = raster.decode(tex, b.platform, 0)
                hit = (font, immvision.GlTexture(np.ascontiguousarray(atlas)))
        except Exception:
            hit = None
        cache[fid] = hit
        return hit

    def ui_game_text(self, ops, gf, text, size, sty, el, x, y, w, h, scale, colour):
        """The label's text as the game's glyph quads (justified in the element, centred vertically)."""
        font, gl = gf
        quads, tw, th = font.layout(text, size, sty['spacing'])
        ax, ay = U.anchor_frac(el.anchor)
        left, top = x - ax * w * scale, y - ay * h * scale
        tx = left + {1: (w * scale - tw) / 2, 2: w * scale - tw}.get(el.just, 0)
        ty = top + (h * scale - th) / 2
        slant = math.tan(math.radians(sty['italic'])) if sty['italic'] else 0.0
        base = ty + font.ascent * size
        for qx, qy, qw, qh, u0, v0, u1, v1, kind in quads:
            x0, y0 = tx + qx, ty + qy
            pts = [(x0, y0), (x0 + qw, y0), (x0 + qw, y0 + qh), (x0, y0 + qh)]
            if slant:
                pts = [(px + (base - py) * slant, py) for px, py in pts]
            col = tuple(colour) if kind != 3 else (1.0, 1.0, 1.0, colour[3])
            ops.append(('quad', gl.texture_id, pts, [(u0, v0), (u1, v0), (u1, v1), (u0, v1)], col, 'alpha'))

    def ui_palette(self, d):
        root = roots.root_for(d.path, d.b.platform)
        cache = self.uied.setdefault('palettes', {})
        if root not in cache:
            cache[root] = U.load_palette(root)
        return cache[root]

    def ui_layout_transitions(self, d, st, lid):
        """{'in', 'out', 'res', 'node'} of a layout's transition texts (its instance params in the screen)."""
        pid = (st.get('params') or {}).get(lid)
        pr = d.b.find(pid) if pid else None
        pn = self.ui_read(d, pr) if pr is not None and pr.type == 0x15 else None
        tl = pn.fields.get(U.H_TIMELINE) if pn is not None else None
        if not isinstance(tl, Node):
            return None
        return {'in': tl.fields.get(U.H_LAYOUT_IN) or '', 'out': tl.fields.get(U.H_LAYOUT_OUT) or '', 'res': pr,
                'node': tl}

    def ui_anim_length(self, d, st, layouts, phase):
        end = max((U.phase_length(el, phase) for _, _, els in layouts for el in els), default=0.0)
        for r, _n, _e in layouts:
            tr = self.ui_layout_transitions(d, st, r.id)
            if tr is not None and phase in ('in', 'out'):
                end = max(end, U.layout_length(tr[phase]))
        return end

    def ui_timeline(self, d, st, layouts, width):
        """Transitions on a time line: a row per element (or the selected one) and per layout transition; bars
        from delay to delay + duration (drag: delay, right edge: duration); the play head scrubs the preview."""
        tl = st.setdefault('tl', {'phase': 'in', 'only_sel': False, 'drag': None})
        open_ = st.get('timeline', True)
        if imgui.small_button('Timeline' if not open_ else 'Hide timeline'):
            st['timeline'] = not open_
        if not open_:
            return
        for ph, label in (('in', 'In'), ('out', 'Out'), ('idle', 'Idle')):
            imgui.same_line()
            if imgui.radio_button(label, tl['phase'] == ph):
                tl['phase'] = ph
                if st.get('scrub'):
                    st['scrub'] = (ph, st['scrub'][1])
        phase = tl['phase']
        imgui.same_line()
        playing = st['play'] is not None
        if imgui.small_button('Stop' if playing else 'Play'):
            st['play'] = None if playing else (phase, time.time())
            st['scrub'] = None
        imgui.same_line()
        _, tl['only_sel'] = imgui.checkbox('Only the selected element', tl['only_sel'])
        t_now = None
        if st['play'] is not None:
            t_now = (time.time() - st['play'][1]) * 1000.0
        elif st.get('scrub'):
            t_now = st['scrub'][1]
        imgui.same_line()
        imgui.text_disabled(f'{t_now:.0f} ms' if t_now is not None else '(click the ruler to scrub)')
        if st.get('scrub'):
            imgui.same_line()
            if imgui.small_button('Rest'):
                st['scrub'] = None
        rows = self.ui_timeline_rows(d, st, layouts, phase, tl['only_sel'])
        total = max([500.0] + [b[1] for _l, bars, _k in rows for b in bars]) * 1.15
        dl = imgui.get_window_draw_list()
        o = imgui.get_cursor_screen_pos()
        label_w = 170.0
        area = max(100.0, width - label_w - 24)
        k = area / total
        row_h = 18.0
        ruler = 16.0
        h = ruler + row_h * max(1, len(rows)) + 4
        imgui.invisible_button('##tlhit', imgui.ImVec2(label_w + area, h))
        hovered = imgui.is_item_hovered()
        x0 = o.x + label_w
        dl.add_rect_filled(imgui.ImVec2(x0, o.y), imgui.ImVec2(x0 + area, o.y + ruler), _u32((0.2, 0.2, 0.23, 1)))
        step = 100 if total < 2500 else 500 if total < 10000 else 2000
        t = 0
        while t <= total:
            x = x0 + t * k
            big = t % (step * 5) == 0
            dl.add_line(imgui.ImVec2(x, o.y + (4 if big else 9)), imgui.ImVec2(x, o.y + ruler), _u32((1, 1, 1, 0.4)))
            if big:
                dl.add_text(imgui.ImVec2(x + 2, o.y), _u32((1, 1, 1, 0.55)), f'{t / 1000:g}s')
            t += step
        io = imgui.get_io()
        mx, my = io.mouse_pos.x, io.mouse_pos.y
        drag = tl['drag']
        hit = None
        for i, (label, bars, key) in enumerate(rows):
            y = o.y + ruler + i * row_h + 2
            sel = key is not None and st['sel'] == key[:2]
            dl.add_text(imgui.ImVec2(o.x + 2, y), _u32((1, 0.8, 0.3, 1) if sel else (1, 1, 1, 0.8)), label[:24])
            lane = (row_h - 4) / max(1, len(bars))          # overlapping bars of a row share its height
            for j, (b0, b1, colour, text, ref) in enumerate(bars):
                if drag is not None and drag['ref'] == ref:
                    b0, b1 = drag['b0'], drag['b1']
                bx0, bx1 = x0 + b0 * k, x0 + max(b1 * k, b0 * k + 4)
                by0, by1 = y + 1 + j * lane, y + 1 + (j + 1) * lane - (1 if len(bars) > 1 else 0)
                dl.add_rect_filled(imgui.ImVec2(bx0, by0), imgui.ImVec2(bx1, by1), _u32(colour), 2)
                if bx1 - bx0 > 30 and lane >= 12:
                    dl.add_text(imgui.ImVec2(bx0 + 3, by0 - 1), _u32((0, 0, 0, 0.85)), text)
                if hovered and drag is None and bx0 - 3 <= mx <= bx1 + 3 and by0 <= my <= by1:
                    hit = (ref, 'end' if mx >= bx1 - 5 else 'move', b0, b1)
                    imgui.set_tooltip(f'{text}: {b0:.0f} - {b1:.0f} ms (drag: delay, right edge: duration)')
        if t_now is not None:
            px = x0 + min(t_now, total) * k
            dl.add_line(imgui.ImVec2(px, o.y), imgui.ImVec2(px, o.y + h), _u32((1, 0.3, 0.3, 1)), 1.5)
        # mouse: scrub on the ruler, drag bars
        if drag is not None:
            if imgui.is_mouse_down(0):
                dms = (mx - drag['mx']) / k
                snap = 50 if io.key_shift else 10
                if drag['mode'] == 'move':
                    nb0 = max(0.0, round((drag['s0'] + dms) / snap) * snap)
                    drag['b0'], drag['b1'] = nb0, nb0 + (drag['s1'] - drag['s0'])
                else:
                    drag['b1'] = max(drag['s0'] + 10, round((drag['s1'] + dms) / snap) * snap)
            else:
                tl['drag'] = None
                if (drag['b0'], drag['b1']) != (drag['s0'], drag['s1']):
                    self.ui_timeline_write(d, st, drag['ref'], drag['b0'], drag['b1'] - drag['b0'])
            return
        if hovered and imgui.is_mouse_down(0) and my <= o.y + ruler and mx >= x0:
            st['scrub'] = (phase, max(0.0, (mx - x0) / k))
            st['play'] = None
        elif hovered and imgui.is_mouse_clicked(0) and hit is not None and not d.b.truncated:
            ref, mode, b0, b1 = hit
            if ref[0] == 'el':
                st['sel'] = (ref[1], ref[2])
            tl['drag'] = {'ref': ref, 'mode': mode, 'mx': mx, 's0': b0, 's1': b1, 'b0': b0, 'b1': b1}


    def ui_timeline_rows(self, d, st, layouts, phase, only_sel):
        """[(label, [(start ms, end ms, colour, text, ref)], (layout id, element index) or None)]."""
        rows = []
        for r, _n, els in layouts:
            tr = self.ui_layout_transitions(d, st, r.id)
            if tr is not None and phase in ('in', 'out') and tr[phase] and not only_sel:
                bars = []
                for i, (fn, args) in enumerate(U.parse_layout_transition(tr[phase])):
                    try:
                        b0, dur = float(args[0]), float(args[1])
                    except (ValueError, IndexError):
                        continue
                    bars.append((b0, b0 + dur, (0.9, 0.9, 0.95, 0.85), fn.title(), ('lay', r.id, phase, i)))
                if bars:
                    rows.append((f'layout {r.id & 0xFFFFFFFF}', bars, None))
            for el in els:
                if only_sel and st['sel'] != (r.id, el.index):
                    continue
                bars = []
                for i, b in enumerate(el.behaviours.get(phase, [])):
                    p = U.behaviour(b)
                    name = U.BEHAVIOUR_TYPES[p['type']] if p['type'] < len(U.BEHAVIOUR_TYPES) else str(p['type'])
                    bars.append((p['delay'], p['delay'] + p['duration'], TYPE_COLOURS.get(p['type'], (0.7, 0.7, 0.7, 0.9)),
                                 name, ('el', r.id, el.index, phase, i)))
                if bars:
                    rows.append((el.label, bars, (r.id, el.index)))
        return rows

    def ui_timeline_write(self, d, st, ref, delay, duration):
        if ref[0] == 'el':
            _k, rid, idx, phase, i = ref
            r = d.b.find(rid)
            n = self.ui_read(d, r)
            el = next((e for e in U.elements(self.types, n) if e.index == idx), None)
            bs = el.behaviours.get(phase, []) if el is not None else []
            if i < len(bs):
                self.ui_step(d, r, f'transition timing of element {idx}')
                self.ui_set(d, r, bs[i], {U.H_DELAY: delay, U.H_DURATION: duration}, 'transition timing')
        else:
            _k, rid, phase, i = ref
            tr = self.ui_layout_transitions(d, st, rid)
            if tr is None:
                return
            parts = U.parse_layout_transition(tr[phase])
            if i < len(parts):
                parts[i][1][0] = U._fmt_num(round(delay))
                if len(parts[i][1]) > 1:
                    parts[i][1][1] = U._fmt_num(round(duration))
                self.ui_set_layout_transition(d, tr, phase, U.format_layout_transition(parts))

    def ui_set_layout_transition(self, d, tr, phase, text):
        h = U.H_LAYOUT_IN if phase == 'in' else U.H_LAYOUT_OUT
        loc = tr['node'].locs.get(h)
        if loc is not None:
            self.write_string(d, tr['res'], loc[0], text)
            self.status = f'Layout transition ({phase}) set: {text} (Ctrl+Z to undo).'

    def ui_layout_tab(self, d, st, r):
        """The layout's own transitions (its instance params in this screen), part by part."""
        tr = self.ui_layout_transitions(d, st, r.id)
        if tr is None:
            imgui.text_disabled('This layout has no instance parameters here (open the screen, not the layout).')
            return
        imgui.text_disabled(f'layout {r.id:#x}, parameters {tr["res"].id:#x}')
        imgui.text_disabled('(parameter objects can be shared by several screens: this changes this bundle\'s copy)')
        for phase, title in (('in', 'Transition in'), ('out', 'Transition out')):
            imgui.separator()
            imgui.text(title)
            parts = U.parse_layout_transition(tr[phase])
            changed = False
            for i, (fn, args) in enumerate(parts):
                imgui.push_id(f'{phase}{i}')
                names = list(U.LAYOUT_FUNCS)
                imgui.set_next_item_width(90)
                ch, kk = imgui.combo('##fn', names.index(fn) if fn in names else 0, names)
                if ch:
                    parts[i] = (names[kk], parts[i][1])
                    changed = True
                fields = U.LAYOUT_FUNCS.get(parts[i][0], ())
                for j, fname in enumerate(fields):
                    if j >= len(args):
                        break
                    imgui.same_line()
                    imgui.set_next_item_width(52 if fname not in ('direction', 'ease') else 80)
                    ch, v = imgui.input_text(f'##{fname}', args[j], imgui.InputTextFlags_.enter_returns_true)
                    if imgui.is_item_hovered():
                        imgui.set_tooltip(fname + (' (ms)' if fname in ('delay', 'duration') else ''))
                    if ch and v != args[j]:
                        args[j] = v.strip()
                        changed = True
                imgui.same_line()
                if imgui.small_button('x'):
                    parts.pop(i)
                    changed = True
                    imgui.pop_id()
                    break
                imgui.pop_id()
            for fn, args in LAYOUT_NEW:
                if (phase == 'in' and fn == 'MOVE') or (phase == 'out' and fn == 'APPROACH'):
                    continue
                if imgui.small_button(f'+ {fn.title()}##{phase}'):
                    a = list(args)
                    if fn == 'FADE':
                        a[2] = 'IN' if phase == 'in' else 'OUT'
                    parts.append((fn, a))
                    changed = True
                imgui.same_line()
            imgui.new_line()
            if changed:
                self.ui_set_layout_transition(d, tr, phase, U.format_layout_transition(parts))
                return

    def ui_text_texture(self, text, px, bold=True, spacing=0.0, italic=0.0):
        cache = self.uied.setdefault('text', {})
        key = (text, int(round(px)), bold, round(spacing, 1), round(italic, 1))
        hit = cache.get(key)
        if hit is None:
            if len(cache) > 400:
                cache.clear()
            img = uirender.text_image(text, px, bold, spacing, italic)
            hit = cache[key] = (immvision.GlTexture(img), img.shape[1], img.shape[0])
        return hit

    def ui_build(self, d, st, layouts, ev, play, now, k):
        """The draw list (uirender ops, layout coordinates), the pickable shapes and the overlay outlines."""
        ops, shapes, overlays = [], [], []
        unit = [(0, 0), (1, 0), (1, 1), (0, 1)]
        drag = st['drag']
        anim = None
        if play is not None:
            anim = (play[0], (now - play[1]) * 1000.0)
        elif st.get('scrub'):
            anim = st['scrub']
        palette = self.ui_palette(d) if anim is not None else None
        base_anim = anim
        for r, _n, els in layouts:
            anim = base_anim
            depth = 0
            lay = None
            own = (st.get('layout_time') or {}).get(r.id)
            if own is not None:                       # a sequence shows this layout since `own` ms
                anim = ('in', own)
                palette = palette or self.ui_palette(d)
            if anim is not None:
                tr = self.ui_layout_transitions(d, st, r.id)
                if tr is not None:
                    lay = U.layout_anim(tr['in'] if anim[0] == 'in' else tr['out'] if anim[0] == 'out' else '',
                                        anim[0], anim[1])
            for el in els:
                x, y, w, h, rot = el.x, el.y, el.w, el.h, el.rot
                if drag is not None and drag['key'] == (r.id, el.index):
                    x, y, w, h = drag['x'], drag['y'], drag['w'], drag['h']
                xf = st['xf']
                if xf is not None and xf['key'] == (r.id, el.index):
                    x, y, w, h, rot = xf['x'], xf['y'], xf['w'], xf['h'], xf['rot']
                visible = ev.visible(el.vis, st['unknown_visible']) or not st['respect_vis']
                rb = ev.value(el.rot_bind)
                if rb is not None:
                    rot += rb
                an = U.animate(el, anim[0], anim[1], palette) if anim is not None else U.Anim()
                if lay is not None:
                    an.alpha *= lay.alpha
                    an.dx += lay.dx
                    an.dy += lay.dy
                alpha, scale = an.alpha, 1.0
                x, y, rot = x + an.dx, y + an.dy, rot + an.drot
                w, h = w * an.sx, h * an.sy
                pts = U.corners(x, y, w, h, el.anchor, rot, scale)
                if (r.id, el.gc) in st['hide'] and el.kind != 'Mask':
                    if st['show_hidden']:
                        overlays.append((pts, (1, 1, 1, 0.12), None))
                    continue
                if el.kind == 'Mask':
                    if el.mask_end:
                        if depth:
                            ops.append(('unmask',))
                            depth -= 1
                    elif st['clip']:
                        tex = self.ui_texture(d, el.texture)
                        uv = self.ui_quad(d, el.quad) or unit
                        ops.append(('mask', tex[0].texture_id if tex else None, pts, uv))
                        depth += 1
                    if st['show_masks']:
                        overlays.append((pts, (0.3, 0.8, 1.0, 0.7), None))
                    shapes.append((r, el, pts))
                    continue
                if not visible:
                    if st['show_hidden']:
                        overlays.append((pts, (1, 1, 1, 0.18), None))
                        shapes.append((r, el, pts))
                    continue
                shapes.append((r, el, pts))
                op = max(0.0, min(1.0, el.opacity / 100.0)) * alpha
                mode = self.ui_mode(d, el) if (el.texture is not None or el.kind in ('Image', 'MiniMap')) else None
                if mode in ('mirror', 'minimap'):
                    if mode == 'minimap':                    # the game turns the map inside, not the element
                        pts = U.corners(x, y, w, h, el.anchor, el.rot, scale)
                    ops.append(('quad', self.ui_stand_in_texture(mode), pts, unit, (1, 1, 1, op), 'alpha'))
                elif mode is not None:
                    tex = self.ui_texture(d, el.texture)
                    if tex is None:
                        overlays.append((pts, (0.3, 0.8, 1.0, 0.6) if mode == 'bound' else (1, 0.4, 0.3, 0.6),
                                         el.image_bind.split('.')[-1] if mode == 'bound' else None))
                        continue
                    uv = self.ui_quad(d, el.quad) or unit
                    tint = an.tint or el.tint or [1, 1, 1, 1]
                    if el.tint_name and not an.tint:
                        pc = self.ui_palette(d).get(el.tint_name)
                        if pc is not None:
                            tint = [a * b for a, b in zip(tint, pc)]
                    faded = 0.45 if mode == 'bound' and st['bound_faded'] else 1.0
                    ops.append(('quad', tex[0].texture_id, pts, uv, (tint[0], tint[1], tint[2], tint[3] * op * faded),
                                'add' if mode == 'additive' else 'alpha'))
                    if mode == 'bound' and st['bound_faded']:
                        overlays.append((pts, (0.3, 0.8, 1.0, 0.45), None))
                elif el.kind in ('Label', 'BackedLabel', 'ScrollingText', 'ScrollableLabel'):
                    text = self.ui_text(d, el, ev)
                    if an.decode is not None and text:
                        text = U.decoded(text, an.decode, el.gc or 0)
                    sty = self.ui_style(d, el.style)
                    size, colour = sty['size'], sty['colour']
                    size = (size or h * 0.8) * scale * an.sy
                    colour = list(colour or [1, 1, 1, 1])
                    if an.tint or el.tint:
                        colour = [a * b for a, b in zip(colour, an.tint or el.tint)]
                    colour[3] *= op
                    gf = self.ui_game_font(d, sty['font']) if text and st.get('game_fonts', True) else None
                    if gf is not None:
                        self.ui_game_text(ops, gf, text, size, sty, el, x, y, w, h, scale, colour)
                    elif text:
                        tt = self.ui_text_texture(text, size * k, sty['font'] not in REGULAR_FONTS,
                                                  sty['spacing'] * k, sty['italic'])
                        tw, th = tt[1] / k, tt[2] / k
                        ax, ay = U.anchor_frac(el.anchor)
                        left, top = x - ax * w * scale, y - ay * h * scale
                        tx = left + {1: (w * scale - tw) / 2, 2: w * scale - tw}.get(el.just, 0)
                        ty = top + (h * scale - th) / 2
                        ops.append(('quad', tt[0].texture_id, [(tx, ty), (tx + tw, ty), (tx + tw, ty + th), (tx, ty + th)],
                                    unit, tuple(colour), 'alpha'))
                elif el.kind == 'Shape':
                    c = el.colour or [1, 1, 1, 1]
                    ops.append(('quad', None, pts, unit, (c[0], c[1], c[2], c[3] * op), 'alpha'))
                else:
                    overlays.append((pts, (0.9, 0.8, 0.3, 0.5), el.kind))
                if st['outlines']:
                    overlays.append((pts, (1, 1, 1, 0.25), None))
            ops.extend([('unmask',)] * depth)
        black = st.get('black')
        if black:
            ops.append(('quad', None, [(0, 0), (U.W, 0), (U.W, U.H), (0, U.H)], unit, (0, 0, 0, black), 'alpha'))
        return ops, shapes, overlays

    @staticmethod
    def ui_outline(dl, P, pts, colour, thick=1.0):
        q = [P(*p) for p in pts]
        dl.add_quad(q[0], q[1], q[2], q[3], _u32(colour), thick)

    def ui_mouse(self, d, st, shapes, cur, o, s, hovered):
        io = imgui.get_io()
        mx, my = (io.mouse_pos.x - o.x) / s, (io.mouse_pos.y - o.y) / s
        if st['xf'] is not None:
            self.ui_xf_mouse(d, st, mx, my, s, hovered)
            return
        drag = st['drag']
        if drag is not None:
            if imgui.is_mouse_down(0):
                ddx, ddy = mx - drag['mx'], my - drag['my']
                snap = max(1, st.get('snap', 1))
                if drag['mode'] == 'move':
                    drag['x'] = round((drag['x0'] + ddx) / snap) * snap
                    drag['y'] = round((drag['y0'] + ddy) / snap) * snap
                else:                                        # resize: in the element's own axes, anchor kept
                    c, sn = math.cos(math.radians(drag['rot'])), math.sin(math.radians(drag['rot']))
                    lx, ly = ddx * c + ddy * sn, -ddx * sn + ddy * c
                    ax, ay = U.anchor_frac(drag['anchor'])
                    kx = 1.0 / (1 - ax) if ax < 1 else 0.0
                    ky = 1.0 / (1 - ay) if ay < 1 else 0.0
                    drag['w'] = max(1.0, round(drag['w0'] + lx * (kx if kx else 1)))
                    drag['h'] = max(1.0, round(drag['h0'] + ly * (ky if ky else 1)))
                    if io.key_shift and drag['w0'] and drag['h0']:
                        f = max(drag['w'] / drag['w0'], drag['h'] / drag['h0'])
                        drag['w'], drag['h'] = round(drag['w0'] * f), round(drag['h0'] * f)
            else:
                st['drag'] = None
                r = d.b.find(drag['key'][0])
                el = self.ui_find(d, r, drag['key'][1])
                if el is not None and (drag['x'], drag['y'], drag['w'], drag['h']) !=                         (drag['x0'], drag['y0'], drag['w0'], drag['h0']):
                    self.ui_step(d, r, f'{drag["mode"]} element {el.index}')
                if el is not None:
                    if drag['mode'] == 'move' and (drag['x'], drag['y']) != (drag['x0'], drag['y0']):
                        self.ui_set(d, r, el.node, {U.H_X: drag['x'], U.H_Y: drag['y']}, 'move element')
                    elif drag['mode'] == 'size' and (drag['w'], drag['h']) != (drag['w0'], drag['h0']):
                        self.ui_set(d, r, el.node, {U.H_W: drag['w'], U.H_H: drag['h']}, 'resize element')
            return
        if not hovered:
            return
        if imgui.is_mouse_clicked(0):
            if cur is not None:
                hx, hy = cur[2][2]
                if abs(mx - hx) * s <= HANDLE and abs(my - hy) * s <= HANDLE and not d.b.truncated:
                    st['drag'] = self.ui_drag(cur, 'size', mx, my)
                    return
            hit = None
            for r, el, pts in reversed(shapes):
                if (r.id, el.gc) in st['lock']:
                    continue
                if U.inside(pts, mx, my) and (el.kind != 'Mask' or st['show_masks']):
                    hit = (r, el, pts)
                    break
            if hit is None:
                st['sel'] = None
                return
            st['sel'] = (hit[0].id, hit[1].index)
            if not d.b.truncated:
                st['drag'] = self.ui_drag(hit, 'move', mx, my)

    # -- keyboard ---------------------------------------------------------------------------------------------
    def ui_keys(self, d, st, layouts):
        """The editor's shortcuts (while it has the keyboard focus)."""
        io = imgui.get_io()
        if io.want_text_input:
            return
        ctrl, shift = io.key_ctrl, io.key_shift
        k = lambda key: imgui.is_key_pressed(key, False)  # noqa: E731
        cmd = None
        if ctrl and k(imgui.Key.z):
            cmd = 'redo' if shift else 'undo'
        elif ctrl and k(imgui.Key.y):
            cmd = 'redo'
        elif ctrl and k(imgui.Key.t):
            cmd = 'transform'
        elif st['xf'] is not None and (k(imgui.Key.enter) or k(imgui.Key.keypad_enter)):
            cmd = 'apply'
        elif st['xf'] is not None and k(imgui.Key.escape):
            cmd = 'cancel'
        elif ctrl and k(imgui.Key.c):
            cmd = 'copy'
        elif ctrl and k(imgui.Key.x):
            cmd = 'cut'
        elif ctrl and k(imgui.Key.v):
            cmd = 'paste'
        elif ctrl and k(imgui.Key.j):
            cmd = 'duplicate'
        elif k(imgui.Key.delete):
            cmd = 'delete'
        elif ctrl and k(imgui.Key.d):
            cmd = 'deselect'
        elif ctrl and k(imgui.Key.left_bracket):
            cmd = 'down'
        elif ctrl and k(imgui.Key.right_bracket):
            cmd = 'up'
        else:
            step = 10 if shift else 1
            for key, delta in ((imgui.Key.left_arrow, (-step, 0)), (imgui.Key.right_arrow, (step, 0)),
                               (imgui.Key.up_arrow, (0, -step)), (imgui.Key.down_arrow, (0, step))):
                if imgui.is_key_pressed(key):
                    cmd = ('nudge',) + delta
                    break
        if cmd is not None:
            self.ui_command(d, st, layouts, cmd)

    def ui_command(self, d, st, layouts, cmd):
        """One editor command (the shortcuts call these; tests too)."""
        sel = st['sel']
        hit = next(((r, el) for r, _n, els in layouts for el in els if sel == (r.id, el.index)), None)
        ro = d.b.truncated
        if cmd == 'undo':
            st['xf'] = st['drag'] = None
            self.undo(d)
        elif cmd == 'redo':
            st['xf'] = st['drag'] = None
            self.redo(d)
        elif cmd == 'deselect':
            st['sel'] = st['xf'] = None
        elif cmd == 'paste':
            if not ro and layouts:
                r = hit[0] if hit else layouts[0][0]
                idx = self.ui_paste(d, r)
                if idx is not None:
                    st['sel'] = (r.id, idx)
        elif hit is None:
            return
        elif cmd == 'copy':
            self.ui_copy(d, *hit)
        elif ro:
            return
        elif cmd == 'cut':
            r, el = hit
            self.ui_copy(d, r, el)
            if self.ui_restructure(d, r, el.index, lambda root, els: els.pop(el.index), f'cut element {el.index}'):
                st['sel'] = None
        elif cmd == 'transform':
            r, el = hit
            if st['xf'] is None:
                st['xf'] = {'key': sel, 'x': el.x, 'y': el.y, 'w': el.w, 'h': el.h, 'rot': el.rot,
                            'anchor': el.anchor, 'orig': (el.x, el.y, el.w, el.h, el.rot), 'drag': None}
                self.status = 'Free transform: drag the handles (Shift: proportional / 15 degrees, Alt: from the ' \
                              'centre), Enter applies, Esc cancels.'
        elif cmd == 'apply':
            xf, st['xf'] = st['xf'], None
            r, el = hit
            if xf is not None and (xf['x'], xf['y'], xf['w'], xf['h'], xf['rot']) != xf['orig']:
                self.ui_step(d, r, f'transform element {el.index}')
                self.ui_set(d, r, el.node, {U.H_X: xf['x'], U.H_Y: xf['y'], U.H_W: xf['w'], U.H_H: xf['h'],
                                            U.H_ROT: xf['rot']}, f'transform element {el.index}')
        elif cmd == 'cancel':
            st['xf'] = None
        elif isinstance(cmd, tuple) and cmd[0] == 'nudge':
            r, el = hit
            if st['xf'] is not None:
                st['xf']['x'] += cmd[1]
                st['xf']['y'] += cmd[2]
            else:
                self.ui_set(d, r, el.node, {U.H_X: el.x + cmd[1], U.H_Y: el.y + cmd[2]}, 'nudge element')
        elif cmd in ('duplicate', 'delete', 'down', 'up'):
            st['xf'] = None
            r, el = hit
            i = el.index
            if cmd == 'duplicate':
                gc = self.ui_alloc_gc(d)

                def dup(root, els):
                    c = gjson.clone(els[i])
                    c.fields[U.H_GC] = gc
                    els.insert(i + 1, c)
                if self.ui_restructure(d, r, i, dup, f'duplicate element {i}'):
                    st['sel'] = (r.id, i + 1)
            elif cmd == 'delete':
                if self.ui_restructure(d, r, i, lambda root, els: els.pop(i), f'delete element {i}'):
                    st['sel'] = None
            elif cmd == 'down' and i > 0:
                if self.ui_restructure(d, r, i, lambda root, els: els.insert(i - 1, els.pop(i)), 'draw earlier'):
                    st['sel'] = (r.id, i - 1)
            elif cmd == 'up':
                n = len(self.ui_read(d, r).fields.get(U.H_ELEMENTS) or [])
                if i + 1 < n and self.ui_restructure(d, r, i, lambda root, els: els.insert(i + 1, els.pop(i)),
                                                     'draw later'):
                    st['sel'] = (r.id, i + 1)

    # -- clipboard ----------------------------------------------------------------------------------------------
    def ui_refs(self, b, node):
        """The resources of bundle b an element needs (what it refers to, and what those import, in b)."""
        want, out = [], {}

        def walk(v):
            if isinstance(v, Node):
                for x in v.fields.values():
                    walk(x)
            elif isinstance(v, list):
                for x in v:
                    walk(x)
            elif isinstance(v, Ref):
                want.append(v.id)

        walk(node)
        while want:
            rid = want.pop()
            if rid in out:
                continue
            res = b.find(rid)
            if res is None or res.missing:
                continue
            out[rid] = res.copy()
            want.extend(i.id for i in res.imports())
        return out

    def ui_copy(self, d, r, el):
        self.uied['clip'] = {'node': gjson.clone(el.node), 'platform': d.b.platform, 'path': d.path,
                             'refs': self.ui_refs(d.b, el.node), 'kind': el.kind, 'from': (d.uid, r.id, el.index)}
        self.status = f'{el.kind} element {el.gc} copied ({len(self.uied["clip"]["refs"])} resources it uses).'

    def ui_dest_types(self, b):
        """{type name: type id} of the Genesys types in bundle b."""
        db = genesys.TypeDB()
        db.add_bundle(b)
        return {db.name(t): t for t in db.types}, db

    def ui_convert_node(self, node, names, db, template=None, template_all=False):
        """A node of another build as a node of this bundle's types, field by field (by name hash), guided by the
        target's schema: a PS3 prototype element's base struct is merged into the element (retail keeps those
        fields in the element); sub-structs take the target field's type, objects their type by name."""
        def conv_node(v, tid, tpl=None):
            t = db.get(tid)
            if t is None:
                raise ValueError(f'type {tid:#x} is not in this bundle')
            if tpl is not None and tpl.type != tid:
                tpl = None
            src = dict(v.fields)
            base = src.get(U.H_BASE)
            if isinstance(base, Node) and all(f.name_hash != U.H_BASE for f in t.fields):
                merged = dict(base.fields)
                merged.update({k: x for k, x in src.items() if k != U.H_BASE})
                src = merged
            n = Node(tid, -1)
            n.raw = None
            for f in t.fields:
                h = f.name_hash
                sub = tpl.fields.get(h) if tpl is not None else None
                if h in src:
                    n.fields[h] = conv_val(src[h], f, sub if isinstance(sub, Node) else None)
                elif tpl is not None and h in tpl.fields and (template_all or (f.flags & 1 and not f.flags & 8)
                                                               or isinstance(sub, Node)):
                    n.fields[h] = gjson.clone(sub)        # e.g. the minimap's mask / road images, a style's fonts
                    borrowed.append(h)
                else:                                     # a pointer the source lacks stays NULL
                    n.fields[h] = None if f.flags & 1 and not f.flags & 8 else gjson.default_of(db, f)
            return n

        def conv_val(v, f, tpl=None):
            if isinstance(v, list):
                if f.flags & 2 and f.count > 1 and len(v) != f.count:
                    v = (v + [v[-1] if v else 0] * f.count)[:f.count]
                return [conv_one(x, f) for x in v]
            return conv_one(v, f, tpl)

        def conv_one(v, f, tpl=None):
            if not isinstance(v, Node):
                ft = db.get(f.type_id)
                if ft is not None and ft.kind == 2 and isinstance(v, int):
                    return float(v)
                if ft is not None and ft.kind in (0, 1, 5) and isinstance(v, float):
                    return int(round(v))
                return v
            ft = db.get(f.type_id)
            own = names.get(self.types.name(v.type)) or (v.type if db.get(v.type) is not None else None)
            if f.flags & 16 or (ft is not None and ft.base and own is not None):
                return conv_node(v, own or f.type_id, tpl)
            return conv_node(v, f.type_id, tpl)

        borrowed = []
        tid = names.get(self.types.name(node.type)) or (node.type if db.get(node.type) is not None else None)
        if tid is None:
            raise ValueError(f'the element type {self.types.name(node.type)} is not in this bundle')
        out = conv_node(node, tid, template)
        self.uied['borrowed'] = borrowed
        return out

    def ui_template_element(self, d, type_name, type_id=None):
        """An element of this type in the bundle's layouts (to take what a converted one lacks from)."""
        for x in d.b.resources:
            if x.type != 0x15 or not x.imports() or self.types.name(x.imports()[0].id) != 'UILayout':
                continue
            n = self.ui_read(d, x)
            for e in (n.fields.get(U.H_ELEMENTS) or []) if n is not None else []:
                if isinstance(e, Node) and (self.types.name(e.type) == type_name or e.type == type_id):
                    return e
        return None

    def ui_checker(self, d):
        """The retail bindings of the PC game folder (for checking / neutralising prototype bindings)."""
        root = next((r for r in [roots.root_for(d.path, 'PC')] + list(roots.FALLBACK.values())
                     if r and os.path.isfile(os.path.join(r, 'NFS13.exe'))), None)
        if root is None:
            return None
        cache = self.uied.setdefault('checkers', {})
        if root not in cache:
            from .labels import config_dir
            cache[root] = BD.Bindings(root, config_dir())
        return cache[root]

    def ui_convert_style(self, d, res, platform):
        """A text style of another build as a new style object of this bundle (fields it lacks and the fonts the
        game does not have come from this bundle's styles)."""
        tpl_res = next((x for x in d.b.resources if x.type == 0x15 and x.imports()
                        and self.types.name(x.imports()[0].id) == 'TextStyle'), None)
        if tpl_res is None:
            return None, None
        tpl = genesys.Reader(self.types, d.b.e).read_resource(tpl_res)
        src = genesys.Reader(self.types, '>' if platform == 'PS3' else '<').read_resource(res)
        names, db = self.ui_dest_types(d.b)
        node = self.ui_convert_node(src, names, db, tpl, template_all=True)
        fonts = {i for i, _n in self.ui_fonts(d)} - {97111}           # 97111 is a CJK font on PC
        main = node.fields.get(name_hash('Font_Asset'))
        main = main if main in fonts else 1206237
        node.fields[name_hash('Font_Asset')] = main
        for f in db.get(node.type).fields:                            # the other font ids (fallbacks)
            v = node.fields.get(f.name_hash)
            if f.name_hash != U.H_GC and isinstance(v, int) and 90000 < v < 3000000 and \
                    db.name(f.type_id) == 'CgsCore.UniqueId' and v not in fonts:
                node.fields[f.name_hash] = tpl.fields.get(f.name_hash) or main
        gc = self.ui_alloc_gc(d)
        node.fields[U.H_GC] = gc
        data, off, cnt = gwrite.write(self.types, d.b.e, node)
        out = tpl_res.copy()
        out.id = (1 << 56) | gc
        out.set_data(0, data)
        out.import_offset, out.import_count = off, cnt
        out.modified = True
        return out, main

    def ui_map_refs(self, d, node, clip):
        """References of an element pasted from another platform: textures come along (converted), materials,
        text styles and quads become the target bundle's, other references are left out. -> resources to add."""
        news = []
        notes = self.uied.setdefault('paste_notes', [])
        converted = {}
        mats = self.ui_materials(d)
        styles = [x.id for x in d.b.resources if x.type == 0x15 and x.imports()
                  and self.types.name(x.imports()[0].id) == 'TextStyle']
        quad = self.ui_full_quad(d)

        def src_kind(rid):
            res = clip['refs'].get(rid)
            if res is None:
                return None, None
            if res.type != 0x15:
                return res.type, res
            imps = res.imports()
            return (self.types.name(imps[0].id) if imps else None), res

        def mapped(rid):
            if d.b.find(rid) is not None:
                return rid
            kind, res = src_kind(rid)
            if kind == T_TEXTURE:
                news.append(ops.adopt(d.b, res, clip['platform'], self.types))
                return rid
            if kind == 'UIMaterial':
                try:
                    sn = genesys.Reader(self.types, '>' if clip['platform'] == 'PS3' else '<').read_resource(res)
                    gc = sn.fields.get(U.H_MATERIAL)
                except Exception:
                    gc = None
                same = next((m[0] for m in mats if self.ui_read(d, d.b.find(m[0])).fields.get(U.H_MATERIAL) == gc),
                            None)
                return same or (mats[0][0] if mats else None)
            if kind == 'TextStyle':
                conv = converted.get(rid)
                if conv is None:
                    try:
                        conv, font = self.ui_convert_style(d, res, clip['platform'])
                    except Exception:
                        conv, font = None, None
                    if conv is not None:
                        news.append(conv)
                        notes.append(f'text style {rid & 0xFFFFFFFF} converted to {conv.id & 0xFFFFFFFF} (font {font})')
                    converted[rid] = conv
                return conv.id if conv is not None else (styles[0] if styles else None)
            if kind == T_RENDERABLE:
                return quad
            return None

        def walk(v):
            if isinstance(v, Node):
                for k, x in list(v.fields.items()):
                    if isinstance(x, Ref):
                        m = mapped(x.id)
                        v.fields[k] = Ref(m) if m else None
                    elif isinstance(x, list):
                        nl = []
                        for y in x:
                            if isinstance(y, Ref):
                                m = mapped(y.id)
                                if m:
                                    nl.append(Ref(m))
                            else:
                                walk(y)
                                nl.append(y)
                        v.fields[k] = nl
                    else:
                        walk(x)
        walk(node)
        rd = node.fields.get(U.H_RDATA)
        if isinstance(rd, Node) and quad and not rd.fields.get(U.H_QUADS):
            rd.fields[U.H_QUADS] = [Ref(quad)]
        return news

    def ui_paste(self, d, r):
        """The copied element into layout r (on top, same position, new id); -> its index or None."""
        clip = self.uied.get('clip')
        if not clip:
            self.status = 'Nothing copied (Ctrl+C on an element first).'
            return None
        node = gjson.clone(clip['node'])
        cross = clip['platform'] != d.b.platform
        news = []
        self.uied['paste_notes'] = []
        if cross:
            names, db = self.ui_dest_types(d.b)
            tpl = self.ui_template_element(d, self.types.name(node.type), node.type)
            try:
                node = self.ui_convert_node(node, names, db, tpl)
            except ValueError as ex:
                self.modal = {'kind': 'message', 'title': 'Paste', 'text': f'This element cannot go into this '
                              f'{d.b.platform} bundle: {ex}.'}
                return None
            borrowed = list(self.uied.get('borrowed') or [])
            t = db.get(node.type)
            empty = [f for f in t.fields if f.flags & 1 and not f.flags & 8 and node.fields.get(f.name_hash) is None
                     and db.name(f.type_id) == 'UISubImage']
            if empty:
                self.modal = {'kind': 'message', 'title': 'Paste', 'text':
                              f'A {clip["kind"]} element needs its {len(empty)} sub-images (mask, road images) and '
                              f'there is no {clip["kind"]} in this bundle to take them from: in the game it would '
                              f'crash (a NULL sub-image). Paste it into a screen that has one.'}
                return None
            news = self.ui_map_refs(d, node, clip)
            notes = self.uied['paste_notes']
            if borrowed:
                notes.append(f'{len(borrowed)} field(s) the prototype element lacks taken from a '
                             f'{self.types.name(node.type)} of this bundle')
            checker = self.ui_checker(d) if self.uied.get('neutralise', True) else None
            if checker is not None:
                notes.extend(BD.fix_node(node, checker))
        if not self.genesys_rewritable(d, r):
            self.status = 'This layout cannot be re-written exactly.'
            return None
        n = self.ui_read(d, r)
        gc = self.ui_alloc_gc(d, [e.gc for e in U.elements(self.types, n) if isinstance(e.gc, int)])
        node.fields[U.H_GC] = gc
        if not cross:
            for rid, res in clip['refs'].items():
                if d.b.find(rid) is None:
                    news.append(ops.adopt(d.b, res, clip['platform'], self.types))
        out = {}

        def mutate(root, els):
            els.append(node)
            out['index'] = len(els) - 1
        d.checkpoint(f'paste a {clip["kind"]} element', [r.id] + [x.id for x in news])
        for x in news:
            d.b.add(x)
        if any(x.type == 0x14 for x in news):
            self.rebuild_types()
        ok = self.ui_restructure(d, r, None, mutate, f'paste a {clip["kind"]} element ({gc})', checkpoint=False)
        if not ok:
            d.do_undo()
            self.changed(d)
            return None
        self.status = f'{clip["kind"]} element {gc} pasted' + (f' with {len(news)} resource(s) it needs' if news
                                                                 else '') + ' (Ctrl+Z to undo).'
        notes = self.uied.get('paste_notes') or []
        if cross and notes:
            self.modal = {'kind': 'message', 'title': 'Pasted from the ' + clip['platform'],
                          'text': f'{clip["kind"]} element {gc}:\n' + '\n'.join(f'- {n}' for n in notes)}
        return out.get('index')

    # -- own pictures -----------------------------------------------------------------------------------------
    def ui_add_png(self, d, r, path, pos=None):
        """An image element showing a picture file, with a texture of its own; -> its index or None."""
        from PIL import Image
        try:
            im = Image.open(path).convert('RGBA')
        except Exception as ex:
            self.status = f'Cannot read {os.path.basename(path)}: {ex}'
            return None
        if max(im.size) > 1024:
            k = 1024 / max(im.size)
            im = im.resize((max(4, int(im.width * k)), max(4, int(im.height * k))), Image.LANCZOS)
        w, h = im.size
        pw, ph = (w + 3) // 4 * 4, (h + 3) // 4 * 4                 # block compressed: sides of 4
        canvas = Image.new('RGBA', (pw, ph), (0, 0, 0, 0))
        canvas.paste(im, (0, 0))
        img = np.array(canvas, np.uint8)
        tpl = self.ui_templates(d).get('Image')
        quad = self.ui_full_quad(d)
        if tpl is None or quad is None:
            self.status = 'This bundle has no image element / whole-texture quad to start from.'
            return None
        tn = (self.ui_read(d, d.b.find(tpl[0])).fields.get(U.H_ELEMENTS) or [])[tpl[1]]
        src_tex = None
        for x in d.b.resources:                                       # a BC3 texture to take the format from
            if x.type == T_TEXTURE:
                try:
                    if raster.info(x, d.b.platform).fmt == 'BC3':
                        src_tex = x
                        break
                except Exception:
                    continue
        src_tex = src_tex or next((x for x in d.b.resources if x.type == T_TEXTURE), None)
        if src_tex is None or not self.genesys_rewritable(d, r):
            self.status = 'No texture to start from, or the layout cannot be re-written exactly.'
            return None
        n = self.ui_read(d, r)
        gc = self.ui_alloc_gc(d, [e.gc for e in U.elements(self.types, n) if isinstance(e.gc, int)])
        tid = (0x0101 << 48) | gc
        tex = src_tex.copy()
        tex.id = tid
        tex.modified = True
        try:
            ops.replace_texture(d.b, tex, image=img, mips='none')
        except Exception as ex:
            self.status = f'The picture cannot be made a texture: {ex}'
            return None
        x0, y0 = pos if pos is not None else (U.W / 2, U.H / 2)
        out = {}

        def mutate(root, els):
            c = gjson.clone(tn)
            c.fields[U.H_GC] = gc
            c.fields[U.H_X], c.fields[U.H_Y] = float(round(x0)), float(round(y0))
            c.fields[U.H_W], c.fields[U.H_H] = float(w), float(h)
            c.fields[U.H_ANCHOR] = 4
            c.fields[U.H_ROT] = 0.0
            for hh in (U.H_VIS, U.H_ROTBIND, U.H_XBIND, U.H_YBIND, U.H_IMAGE_BIND):
                if hh in c.fields:
                    c.fields[hh] = ''
            for _, hh, _ in U.BEHAVIOURS:
                if hh in c.fields:
                    c.fields[hh] = None
            if name_hash('ImageSource') in c.fields:
                c.fields[name_hash('ImageSource')] = gc
            tint = c.fields.get(U.H_TINT)
            if isinstance(tint, Node) and isinstance(tint.fields.get(U.H_VALUE), list):
                tint.fields[U.H_VALUE] = [1.0, 1.0, 1.0, 1.0]
            op = c.fields.get(U.H_OPACITY)
            if isinstance(op, Node) and U.H_VALUE in op.fields:
                op.fields[U.H_VALUE] = 100
            rd = c.fields.get(U.H_RDATA)
            if isinstance(rd, Node):
                rd.fields[U.H_TEXHANDLE] = Ref(tid)
                if isinstance(rd.fields.get(U.H_QUADS), list) and rd.fields[U.H_QUADS]:
                    rd.fields[U.H_QUADS] = [Ref(quad)]
            els.append(c)
            out['index'] = len(els) - 1
        d.checkpoint(f'image from {os.path.basename(path)}', [r.id, tid])
        d.b.add(tex)
        if not self.ui_restructure(d, r, None, mutate, f'image {gc} from {os.path.basename(path)}', checkpoint=False):
            d.do_undo()
            self.changed(d)
            return None
        self.status = (f'Image element {gc} ({w} x {h}) from {os.path.basename(path)} with its own texture {tid:#x} '
                       '(Ctrl+Z to undo).')
        return out.get('index')

    def ui_drop(self, paths):
        """Pictures dropped on the editor's canvas become image elements there (True when it took them)."""
        info = getattr(self, 'uied', {}).get('canvas')
        if not info or info['frame'] < imgui.get_frame_count() - 2:
            return False
        io = imgui.get_io()
        ox, oy, cw, ch, s, uid, key = info['rect']
        mx, my = io.mouse_pos.x, io.mouse_pos.y
        if not (ox <= mx <= ox + cw and oy <= my <= oy + ch):
            return False
        pics = [p for p in paths if os.path.splitext(p)[1].lower() in ('.png', '.jpg', '.jpeg', '.bmp', '.tga',
                                                                        '.webp')]
        if not pics:
            return False
        d = self.doc_by_uid(uid)
        st = self.uied['views'].get(key)
        if d is None or st is None or d.b.truncated:
            return False
        sel = st['sel']
        r = d.b.find(sel[0]) if sel else None
        if r is None:
            r = d.b.find(info['first'])
        if r is None:
            return False
        pos = ((mx - ox) / s, (my - oy) / s)
        for i, p in enumerate(pics):
            idx = self.ui_add_png(d, r, p, (pos[0] + 20 * i, pos[1] + 20 * i))
            if idx is not None:
                st['sel'] = (r.id, idx)
        return True

    # -- free transform (Ctrl+T) -------------------------------------------------------------------------------
    XF_HANDLES = ('tl', 't', 'tr', 'r', 'br', 'b', 'bl', 'l')

    @staticmethod
    def ui_xf_local(xf):
        """The rect in the element's own axes around its position (anchor point): l, t, r, b."""
        ax, ay = U.anchor_frac(xf['anchor'])
        return -ax * xf['w'], -ay * xf['h'], (1 - ax) * xf['w'], (1 - ay) * xf['h']

    @staticmethod
    def ui_xf_world(xf, lx, ly):
        c, s = math.cos(math.radians(xf['rot'])), math.sin(math.radians(xf['rot']))
        return xf['x'] + lx * c - ly * s, xf['y'] + lx * s + ly * c

    def ui_xf_points(self, xf, s):
        """{handle: layout point} of the transform box, 'rot' the rotation knob."""
        l, t, r, b = self.ui_xf_local(xf)
        mx, my = (l + r) / 2, (t + b) / 2
        loc = {'tl': (l, t), 't': (mx, t), 'tr': (r, t), 'r': (r, my), 'br': (r, b), 'b': (mx, b), 'bl': (l, b),
               'l': (l, my), 'rot': (mx, t - 28 / max(s, 1e-6))}
        return {k: self.ui_xf_world(xf, *v) for k, v in loc.items()}

    def ui_xf_draw(self, dl, P, xf, s):
        pts = self.ui_xf_points(xf, s)
        box = [P(*pts[k]) for k in ('tl', 'tr', 'br', 'bl')]
        col = _u32((0.2, 0.75, 1.0, 1))
        dl.add_quad(box[0], box[1], box[2], box[3], col, 1.5)
        top = P(*pts['t'])
        knob = P(*pts['rot'])
        dl.add_line(top, knob, col, 1.0)
        dl.add_circle_filled(knob, 5, col)
        for k in self.XF_HANDLES:
            p = P(*pts[k])
            dl.add_rect_filled(imgui.ImVec2(p.x - 4, p.y - 4), imgui.ImVec2(p.x + 4, p.y + 4), _u32((1, 1, 1, 1)))
            dl.add_rect(imgui.ImVec2(p.x - 4, p.y - 4), imgui.ImVec2(p.x + 4, p.y + 4), col)
        pv = P(xf['x'], xf['y'])
        dl.add_circle(pv, 4, col, 0, 1.5)

    def ui_xf_mouse(self, d, st, mx, my, s, hovered):
        xf = st['xf']
        io = imgui.get_io()
        g = xf['drag']
        if g is not None:
            if imgui.is_mouse_down(0):
                self.ui_xf_apply_drag(xf, g, mx, my, io.key_shift, io.key_alt, max(1, st.get('snap', 1)))
            else:
                xf['drag'] = None
            return
        if not hovered or not imgui.is_mouse_clicked(0):
            return
        pts = self.ui_xf_points(xf, s)
        near = lambda p: abs(p[0] - mx) * s <= 7 and abs(p[1] - my) * s <= 7  # noqa: E731
        handle = next((k for k in ('rot',) + self.XF_HANDLES if near(pts[k])), None)
        box = [pts[k] for k in ('tl', 'tr', 'br', 'bl')]
        if handle is None and U.inside(box, mx, my):
            handle = 'move'
        if handle is None:                        # a click outside applies the transform (as Photoshop asks)
            self.ui_command(d, st, [(d.b.find(xf['key'][0]), None,
                                     U.elements(self.types, self.ui_read(d, d.b.find(xf['key'][0]))))], 'apply')
            return
        if imgui.is_mouse_double_clicked(0) and handle == 'move':
            self.ui_command(d, st, [(d.b.find(xf['key'][0]), None,
                                     U.elements(self.types, self.ui_read(d, d.b.find(xf['key'][0]))))], 'apply')
            return
        xf['drag'] = {'handle': handle, 'mx': mx, 'my': my,
                      'start': (xf['x'], xf['y'], xf['w'], xf['h'], xf['rot'])}

    def ui_xf_apply_drag(self, xf, g, mx, my, shift=False, alt=False, snap=1):
        """Move / scale / rotate the transform box from the drag start to the mouse (layout coordinates)."""
        x0, y0, w0, h0, r0 = g['start']
        if g['handle'] == 'move':
            xf['x'] = round((x0 + mx - g['mx']) / snap) * snap
            xf['y'] = round((y0 + my - g['my']) / snap) * snap
            return
        if g['handle'] == 'rot':
            a0 = math.atan2(g['my'] - y0, g['mx'] - x0)
            a1 = math.atan2(my - y0, mx - x0)
            rot = r0 + math.degrees(a1 - a0)
            if shift:
                rot = round(rot / 15.0) * 15.0
            xf['rot'] = round(rot, 2)
            return
        start = dict(xf, x=x0, y=y0, w=w0, h=h0, rot=r0)
        l, t, r, b = self.ui_xf_local(start)
        c, s = math.cos(math.radians(r0)), math.sin(math.radians(r0))
        dx, dy = mx - x0, my - y0
        lx, ly = dx * c + dy * s, -dx * s + dy * c                  # the mouse in the element's own axes
        hd = g['handle']
        nl, nt, nr, nb = l, t, r, b
        if 'l' in hd:
            nl = lx
        if 'r' in hd:
            nr = lx
        if hd in ('t', 'tl', 'tr'):
            nt = ly
        if hd in ('b', 'bl', 'br'):
            nb = ly
        if alt:                                                       # from the centre: the other side mirrors
            cx, cy = (l + r) / 2, (t + b) / 2
            if 'l' in hd:
                nr = 2 * cx - nl
            if 'r' in hd:
                nl = 2 * cx - nr
            if hd in ('t', 'tl', 'tr'):
                nb = 2 * cy - nt
            if hd in ('b', 'bl', 'br'):
                nt = 2 * cy - nb
        if shift and len(hd) == 2 and w0 > 0 and h0 > 0:            # proportional from the corner
            k = max(abs(nr - nl) / w0, abs(nb - nt) / h0)
            if 'l' in hd:
                nl = nr - w0 * k if not alt else (l + r) / 2 - w0 * k / 2
                if alt:
                    nr = (l + r) / 2 + w0 * k / 2
            else:
                nr = nl + w0 * k if not alt else (l + r) / 2 + w0 * k / 2
                if alt:
                    nl = (l + r) / 2 - w0 * k / 2
            if 't' in hd:
                nt = nb - h0 * k if not alt else (t + b) / 2 - h0 * k / 2
                if alt:
                    nb = (t + b) / 2 + h0 * k / 2
            else:
                nb = nt + h0 * k if not alt else (t + b) / 2 + h0 * k / 2
                if alt:
                    nt = (t + b) / 2 - h0 * k / 2
        if nr < nl:
            nl, nr = nr, nl
        if nb < nt:
            nt, nb = nb, nt
        w, h = max(1.0, round(nr - nl)), max(1.0, round(nb - nt))
        ax, ay = U.anchor_frac(xf['anchor'])
        px, py = nl + ax * w, nt + ay * h                              # the new anchor point, own axes
        xf['w'], xf['h'] = w, h
        xf['x'] = round(x0 + px * c - py * s, 2)
        xf['y'] = round(y0 + px * s + py * c, 2)

    @staticmethod
    def ui_drag(hit, mode, mx, my):
        r, el, _ = hit
        return {'key': (r.id, el.index), 'mode': mode, 'mx': mx, 'my': my, 'x0': el.x, 'y0': el.y, 'x': el.x,
                'y': el.y, 'w0': el.w, 'h0': el.h, 'w': el.w, 'h': el.h, 'rot': el.rot, 'anchor': el.anchor}

    def ui_find(self, d, r, index):
        if r is None:
            return None
        n = self.ui_read(d, r)
        if n is None:
            return None
        return next((e for e in U.elements(self.types, n) if e.index == index), None)

    # -- writing --------------------------------------------------------------------------------------------
    def ui_fmt(self, f):
        ft = self.types.get(f.type_id)
        kind, size = (ft.kind, ft.size) if ft else (None, f.size)
        if kind == 2:
            return 'f' if size == 4 else 'd'
        return {1: 'b' if kind == 0 else 'B', 2: 'h' if kind == 0 else 'H', 4: 'i' if kind == 0 else 'I',
                8: 'q' if kind == 0 else 'Q'}.get(size)

    @staticmethod
    def ui_step(d, r, desc):
        """An undo step of its own for the value writes that follow (in this frame)."""
        d.checkpoint(desc, [r.id])
        d.last_edit = (r.id, time.time())

    @staticmethod
    def ui_holder(node, h):
        """The node that holds field h: the node itself, or its PS3 prototype base struct."""
        if h in node.locs:
            return node
        base = node.fields.get(U.H_BASE)
        if isinstance(base, Node) and h in base.locs:
            return base
        return node

    def ui_set(self, d, r, node, values, desc=None):
        """Write scalar fields of a node in place: {field hash: value} (fields of a prototype base go there)."""
        for h, v in values.items():
            loc = self.ui_holder(node, h).locs.get(h)
            if loc is None:
                continue
            fmt = self.ui_fmt(loc[1])
            if fmt is None:
                continue
            if fmt in 'fd':
                v = float(v)
            else:
                v = int(round(v))
            self.write(d, r, loc[0], d.b.e + fmt, v)
        if desc:
            self.status = f'{desc[0].upper()}{desc[1:]} ({r.id:#x}; Ctrl+Z to undo).'

    def ui_set_floats(self, d, r, node, h, values):
        """A counted float array (Tint.Value): the floats it points at."""
        loc = node.locs.get(h)
        if loc is None:
            return
        ptr = struct.unpack_from(d.b.e + 'I', r.data(0), loc[0])[0]
        self.write(d, r, ptr, d.b.e + f'{len(values)}f', *[float(x) for x in values])

    def ui_set_text(self, d, r, node, h, text):
        loc = self.ui_holder(node, h).locs.get(h)
        if loc is not None:
            self.write_string(d, r, loc[0], text)

    def ui_restructure(self, d, r, index, mutate, desc, checkpoint=True):
        """Change the layout's node tree (elements / behaviours) and write the object again."""
        if not self.genesys_rewritable(d, r):
            self.status = 'This layout cannot be re-written exactly, so elements and behaviours cannot be added here.'
            return False
        root = genesys.Reader(self.types, d.b.e).read_resource(r)
        els = root.fields.get(U.H_ELEMENTS)
        return self.genesys_rewrite(d, r, root, lambda: mutate(root, els), desc, checkpoint)

    def ui_new_behaviour(self, el_node, phase_hash, params, duration, delay=0.0):
        t = self.types.get(el_node.type)
        f = next((x for x in t.fields if x.name_hash == phase_hash), None)
        if f is None:
            raise ValueError('this element has no such behaviour list')
        b = gjson.default_of(self.types, f)
        b.fields.update({U.H_PARAMS: params, U.H_DELAY: float(delay), U.H_DURATION: float(duration),
                         U.H_TYPE: 0, U.H_EASE: 0})
        return b

    def ui_new_gc(self, els):
        used = [e.fields.get(U.H_GC) for e in els or [] if isinstance(e, Node)]
        return max([0x7E000000] + [u for u in used if isinstance(u, int) and u >= 0x7E000000]) + 1

    # -- the panel ------------------------------------------------------------------------------------------
    def ui_panel(self, d, st, layouts, ev):
        ro = d.b.truncated
        if imgui.collapsing_header('View', imgui.TreeNodeFlags_.default_open):
            _, st['respect_vis'] = imgui.checkbox('Visibility bindings', st['respect_vis'])
            imgui.same_line()
            _, st['unknown_visible'] = imgui.checkbox('Unknown = shown', st['unknown_visible'])
            _, st['show_hidden'] = imgui.checkbox('Hidden as outlines', st['show_hidden'])
            imgui.same_line()
            _, st['outlines'] = imgui.checkbox('All outlines', st['outlines'])
            _, st['show_masks'] = imgui.checkbox('Masks', st['show_masks'])
            imgui.same_line()
            _, st['clip'] = imgui.checkbox('Mask clipping', st['clip'])
            imgui.same_line()
            imgui.set_next_item_width(70)
            snaps = [1, 2, 5, 10, 20]
            ch, k = imgui.combo('Snap', snaps.index(st['snap']) if st['snap'] in snaps else 0,
                                [f'{x} px' for x in snaps])
            if ch:
                st['snap'] = snaps[k]
            _, st['bound_faded'] = imgui.checkbox('Game pictures faded', st['bound_faded'])
            imgui.same_line()
            ch, v = imgui.checkbox('Neutralise unknown bindings on paste', self.uied.get('neutralise', True))
            if ch:
                self.uied['neutralise'] = v
            imgui.same_line()
            _, st['game_fonts'] = imgui.checkbox('Game fonts', st.get('game_fonts', True))
            if imgui.is_item_hovered():
                imgui.set_tooltip('Text drawn with the game\'s own fonts (UI/FONTS); off: Arial')
            if imgui.is_item_hovered():
                imgui.set_tooltip('Images whose picture the game sets at run time (ImageBinding: profile pictures, '
                                  'icons, map tiles) show their stored stand-in texture faded and outlined.\n'
                                  'The rear-view mirror and the minimap are drawn by the game: shown as stand-ins.')
            if imgui.button('Background image...'):
                p = filedialog.open_file('Background image (a screenshot of the game)', self.cfg.get('last_import', ''),
                                         filedialog.IMAGES)
                if p:
                    try:
                        from PIL import Image
                        import numpy as np
                        img = np.asarray(Image.open(p).convert('RGBA'))
                        self.uied['bg'] = (immvision.GlTexture(img), img)
                    except Exception as ex:
                        self.status = f'Cannot load {os.path.basename(p)}: {ex}'
            if self.uied['bg'] is not None:
                imgui.same_line()
                if imgui.button('No background'):
                    self.uied['bg'] = None
            imgui.text_disabled('Ctrl+T transform, Ctrl+Z / Ctrl+Y undo / redo, Ctrl+C / X / V, Ctrl+J duplicate, '
                                'Delete, Ctrl+D deselect, Ctrl+[ ] order, arrows nudge; drop PNGs on the canvas')
            imgui.text('Play:')
            for ph, label in (('in', 'Transition in'), ('out', 'Transition out'), ('idle', 'Idle')):
                imgui.same_line()
                if imgui.small_button(label):
                    st['play'] = (ph, time.time())
            if st['play'] is not None:
                imgui.same_line()
                if imgui.small_button('Stop'):
                    st['play'] = None
        imgui.text('Situation:')
        for name in U.STATE_PRESETS:
            imgui.same_line()
            if imgui.get_content_region_avail().x < imgui.calc_text_size(name).x + 16:
                imgui.new_line()
            if imgui.small_button(name):
                st['state'] = dict(U.STATE_PRESETS['Free roam']) if name != 'Free roam' else {}
                st['state'].update(U.STATE_PRESETS[name])
                st['preset'] = name
        _, st['live'] = imgui.checkbox('Live (revs, speed, gear and heading move)', st.get('live', False))
        if st.get('preset'):
            imgui.same_line()
            imgui.text_disabled(f'({st["preset"]})')
        if imgui.collapsing_header('Game values'):
            imgui.text_disabled('Made-up values the bindings read (preview only).')
            allels = [el for _, _, els in layouts for el in els]
            for p in U.bindings(allels):
                v = ev.state.get(p)
                imgui.set_next_item_width(90)
                ch, nv = imgui.input_float(f'##gv{p}', float(v) if v is not None else 0.0, 0, 0, '%.3g')
                if ch:
                    st['state'][p] = nv
                imgui.same_line()
                imgui.text(p if v is not None or p in st['state'] else p + ' (unset)')
        if imgui.collapsing_header('Elements', imgui.TreeNodeFlags_.default_open):
            if not ro and layouts:
                self.ui_add_controls(d, st, layouts)
            imgui.begin_child('##uiels', imgui.ImVec2(0, 170))
            for r, _n, els in layouts:
                if len(layouts) > 1:
                    imgui.text_disabled(f'layout {r.id:#x}')
                for el in els:
                    key = (r.id, el.gc)
                    imgui.push_id(f'{r.id}.{el.index}')
                    hidden = key in st['hide']
                    if imgui.small_button('o' if not hidden else '-'):
                        (st['hide'].discard if hidden else st['hide'].add)(key)
                    if imgui.is_item_hovered():
                        imgui.set_tooltip('Show / hide in the preview (the game file is not changed)')
                    imgui.same_line()
                    locked = key in st['lock']
                    if imgui.small_button('L' if locked else ' '):
                        (st['lock'].discard if locked else st['lock'].add)(key)
                    if imgui.is_item_hovered():
                        imgui.set_tooltip('Lock: not picked or dragged in the preview')
                    imgui.same_line()
                    shown = ev.visible(el.vis, st['unknown_visible'])
                    label = el.label + ('' if shown else '  (hidden by binding)')
                    if imgui.selectable(f'{label}##sel', st['sel'] == (r.id, el.index))[0]:
                        st['sel'] = (r.id, el.index)
                    imgui.pop_id()
            imgui.end_child()
        sel = st['sel']
        hit = next(((r, n, el) for r, n, els in layouts for el in els if sel == (r.id, el.index)), None)
        if hit is None:
            imgui.text_disabled('Click an element in the preview (or the list) to edit it.')
            return
        r, _n, el = hit
        node = el.node
        imgui.separator()
        imgui.text(f'{el.kind}  #{el.index}  id {el.gc}')
        xf = st['xf']
        if xf is not None and xf['key'] == sel:
            ox, oy, ow, oh, orot = xf['orig']
            imgui.text_colored(imgui.ImVec4(0.3, 0.8, 1.0, 1), f'Free transform: {xf["x"]:.0f}, {xf["y"]:.0f}  '
                               f'{xf["w"]:.0f} x {xf["h"]:.0f} ({100 * xf["w"] / max(ow, 1):.0f}% x '
                               f'{100 * xf["h"] / max(oh, 1):.0f}%)  {xf["rot"]:.1f} deg')
            if imgui.small_button('Apply (Enter)'):
                self.ui_command(d, st, layouts, 'apply')
            imgui.same_line()
            if imgui.small_button('Cancel (Esc)'):
                self.ui_command(d, st, layouts, 'cancel')
        if ro:
            imgui.text_disabled('(read-only bundle)')
            return
        want = st.pop('tab_request', None)

        def tab(name):
            flags = imgui.TabItemFlags_.set_selected if want == name else 0
            return imgui.begin_tab_item(name, None, flags)[0]

        if imgui.begin_tab_bar('##uieltabs'):
            if tab('Element'):
                self.ui_props(d, r, el, node)
                self.ui_material(d, r, el)
                self.ui_element_ops(d, r, el)
                imgui.end_tab_item()
            if isinstance(self.ui_holder(node, U.H_PROPS).fields.get(U.H_PROPS), Node) and tab('Text'):
                self.ui_text_style(d, r, el, node)
                imgui.end_tab_item()
            if tab('Transitions'):
                self.ui_behaviours(d, r, el, node)
                imgui.end_tab_item()
            if tab('All properties'):
                self.ui_all_props(d, r, el)
                imgui.end_tab_item()
            if st.get('params') and tab('Layout'):
                self.ui_layout_tab(d, st, r)
                imgui.end_tab_item()
            imgui.end_tab_bar()

    # -- generic field editing ------------------------------------------------------------------------------
    def ui_field(self, node, name):
        t = self.types.get(node.type)
        h = _hash(name)
        return next((f for f in t.fields if f.name_hash == h), None) if t else None

    def ui_field_edit(self, d, r, node, name, label):
        """An editor for one field by its type: number, bool, enum, text, colour / vector."""
        f = self.ui_field(node, name)
        if f is None or f.name_hash not in node.locs:
            return
        off = node.locs[f.name_hash][0]
        ft = self.types.get(f.type_id)
        v = node.fields.get(f.name_hash)
        imgui.set_next_item_width(200)
        key = f'{label}##uif{id(node)}{f.name_hash:x}'
        if ft is not None and ft.id == STRINGBASE:
            self.ui_text_field(d, r, node, f.name_hash, label, v or '')
        elif ft is not None and ft.kind == 5:
            opts = sorted((x.offset, self.labels.name(x.name_hash) or f'{x.name_hash:#x}') for x in ft.fields)
            vals = [o for o, _ in opts]
            cur = vals.index(v) if v in vals else -1
            ch, k = imgui.combo(key, cur, [f'{n} ({o})' for o, n in opts])
            if ch and k >= 0:
                self.ui_set(d, r, node, {f.name_hash: vals[k]})
        elif ft is not None and ft.kind == 3:
            ch, b = imgui.checkbox(key, bool(v))
            if ch:
                self.ui_set(d, r, node, {f.name_hash: int(b)})
        elif isinstance(v, list) and len(v) in (3, 4) and all(isinstance(x, (int, float)) for x in v):
            vals = [float(x) for x in v]
            if 'olour' in label or 'olor' in label:
                ch, vals = (imgui.color_edit4 if len(vals) == 4 else imgui.color_edit3)(
                    key, vals, imgui.ColorEditFlags_.float)
            else:
                ch, vals = (imgui.drag_float4 if len(vals) == 4 else imgui.drag_float3)(key, vals, 0.01)
            if ch:
                self.write(d, r, off, d.b.e + f'{len(vals)}f', *[float(x) for x in vals])
        elif isinstance(v, float):
            ch, x = imgui.drag_float(key, v, 0.1, -100000, 100000, '%.3f')
            if ch:
                self.ui_set(d, r, node, {f.name_hash: x})
        elif isinstance(v, int):
            ch, x = imgui.input_int(key, v)
            if ch:
                self.ui_set(d, r, node, {f.name_hash: x})

    # -- text -----------------------------------------------------------------------------------------------
    def ui_fonts(self, d):
        """[(GameChanger id, label)] of the game's UI fonts (UI/FONTS/<id>.BNDL)."""
        if 'fonts' not in self.uied:
            ids = set()
            root = roots.root_for(d.path, d.b.platform)
            folder = os.path.join(root, 'UI', 'FONTS') if root else None
            if folder and os.path.isdir(folder):
                for n in os.listdir(folder):
                    stem = os.path.splitext(n)[0]
                    if n.upper().endswith('.BNDL') and stem.isdigit():
                        ids.add(int(stem))
            self.uied['fonts'] = sorted(ids)
        return [(i, f'{i}' + (f'  ({FONT_NOTES[i]})' if i in FONT_NOTES else '')) for i in self.uied['fonts']]

    def ui_style_users(self, d, sid):
        """How many labels of the bundle's layouts use the text style."""
        key = ('style_users', d.uid, d.edits, sid)
        hit = self.gcache.get(key)
        if hit is None:
            hit = 0
            for x in d.b.resources:
                if x.type != 0x15:
                    continue
                n = self.ui_read(d, x)
                if n is None or self.types.name(n.type) != 'UILayout':
                    continue
                hit += sum(1 for e in U.elements(self.types, n) if e.style == sid)
            self.gcache[key] = hit
        return hit

    def ui_text_style(self, d, r, el, node):
        node = self.ui_holder(node, U.H_PROPS)
        txt = self.ui_holder(el.node, U.H_TEXT).fields.get(U.H_TEXT)
        if isinstance(txt, Node) and U.H_STRING_ID in txt.locs:
            self.ui_string_picker(d, r, el, txt)
        props = node.fields[U.H_PROPS]
        for name, label in PROPS_FIELDS:
            if name == 'unoO':
                f = self.ui_field(props, name)
                if f is not None and f.name_hash in props.locs:
                    imgui.set_next_item_width(200)
                    ch, k = imgui.combo('Justification', min(int(props.fields.get(f.name_hash) or 0), 3), list(JUSTIFY))
                    if ch:
                        self.ui_set(d, r, props, {f.name_hash: k})
                continue
            self.ui_field_edit(d, r, props, name, label)
        sr = d.b.find(el.style) if el.style else None
        sn = self.ui_read(d, sr) if sr is not None and sr.type == 0x15 else None
        if sn is None:
            imgui.text_disabled('The text style is not in this bundle.' if el.style else 'No text style.')
            return
        users = self.ui_style_users(d, el.style)
        imgui.text_disabled(f'style {sn.fields.get(U.H_GC)} - used by {users} label(s)')
        if users > 1:
            imgui.same_line()
            if imgui.small_button('Own copy'):
                self.ui_fork_style(d, r, el)
                return
            if imgui.is_item_hovered():
                imgui.set_tooltip('Changes to a shared style change every label using it. This makes a copy of '
                                  'the style (new id from 0x7E000000) used only by this element.')
        fa = self.ui_field(sn, 'Font_Asset')
        if fa is not None and fa.name_hash in sn.locs:
            fonts = self.ui_fonts(d)
            cur = sn.fields.get(fa.name_hash)
            ids = [i for i, _ in fonts]
            if cur not in ids:
                fonts = [(cur, f'{cur} (not in UI/FONTS)')] + fonts
                ids = [cur] + ids
            imgui.set_next_item_width(200)
            ch, k = imgui.combo('Font', ids.index(cur), [n for _, n in fonts])
            if ch:
                self.ui_set(d, sr, sn, {fa.name_hash: ids[k]}, 'set the font')
        for name, label in STYLE_FIELDS:
            self.ui_field_edit(d, sr, sn, name, label)
        gf = self.ui_game_font(d, sn.fields.get(fa.name_hash) if fa is not None else None)
        if gf is not None:
            font = gf[0]
            have = sum(1 for g in font.slots if g.kind in (2, 3) and uifont.drawn(g))
            lacks = [c for c in 'őŐűŰ' if not uifont.drawn(font.glyph(c))]
            imgui.text_disabled(f'font "{font.name}" ({font.style}): {have} characters'
                                + (f'; lacks {" ".join(lacks)}' if lacks else ''))
        else:
            imgui.text_disabled('(this font is not found: the preview uses Arial)')
        self.ui_font_tools(d, sr, sn, fa)

    def ui_strings(self, d):
        return ops.game_strings(d.path, root=roots.root_for(d.path, d.b.platform))

    def ui_string_picker(self, d, r, el, txt):
        """Search the game's (English) strings and give the label one of them."""
        st = self.uied.setdefault('strsearch', {'q': ''})
        cur = self.ui_strings(d).get(el.string_id) if el.string_id else None
        imgui.text_disabled(f'Shows: {cur!r}' if cur else f'String id {el.string_id or 0}'
                            + (f', binding {el.text!r}' if el.text else ''))
        imgui.set_next_item_width(200)
        _, st['q'] = imgui.input_text_with_hint('Find a string', 'text to search for', st['q'])
        q = st['q'].strip().lower()
        if len(q) >= 2:
            hits = [(k, v) for k, v in self.ui_strings(d).items() if q in v.lower()][:40]
            imgui.begin_child('##strhits', imgui.ImVec2(0, 120), imgui.ChildFlags_.borders)
            for k, v in hits:
                if imgui.selectable(f'{k}: {v[:70]}##s{k}')[0]:
                    self.ui_set(d, r, txt, {U.H_STRING_ID: k}, f'set the text of element {el.index}')
            if not hits:
                imgui.text_disabled('no string has that text')
            imgui.end_child()

    def ui_font_tools(self, d, sr, sn, fa):
        """Make a new font from a TrueType / OpenType file (or add the missing characters to a game font)."""
        if fa is None or not imgui.tree_node('New font from a TTF / OTF file'):
            return
        st = self.uied.setdefault('newfont', {'ttf': '', 'keep': True, 'em': 40, 'use': True, 'extra': uifont.EXTRA})
        imgui.text_wrapped('Starts from this style\'s font (its button icons and characters are kept). With "keep the '
                           'game\'s letters" only the characters it lacks (e.g. ő ű) are drawn from the TTF; '
                           'otherwise every letter is.')
        if imgui.button('TTF / OTF file...'):
            p = filedialog.open_file('Font file', self.cfg.get('last_import', ''),
                                     [('Fonts (*.ttf, *.otf, *.ttc)', '*.ttf;*.otf;*.ttc'), ('All files', '*.*')])
            if p:
                st['ttf'] = p
        imgui.same_line()
        imgui.text(os.path.basename(st['ttf']) if st['ttf'] else '(none)')
        _, st['keep'] = imgui.checkbox("Keep the game's letters, add only the missing characters", st['keep'])
        if not st['keep']:
            imgui.set_next_item_width(120)
            _, st['em'] = imgui.slider_int('Pixels per em', st['em'], 16, 96)
        imgui.set_next_item_width(200)
        _, st['extra'] = imgui.input_text('Extra characters', st['extra'])
        _, st['use'] = imgui.checkbox('Use the new font for this style', st['use'])
        if imgui.button('Make the font...') and st['ttf']:
            base_id = sn.fields.get(fa.name_hash)
            root = roots.root_for(d.path, d.b.platform)
            folder = os.path.join(root, 'UI', 'FONTS') if root else ''
            new_id = self.ui_alloc_gc(d)
            p = filedialog.save_file('Save the font bundle (its name is the font id)',
                                     os.path.join(folder, f'{new_id}.BNDL'), filedialog.BUNDLES
                                     if hasattr(filedialog, 'BUNDLES') else filedialog.ANY, 'BNDL')
            if p:
                ok, msg, fid = self.ui_make_font(d, base_id, st['ttf'], p, st['keep'], st['em'], st['extra'])
                self.status = msg
                if ok and st['use'] and fid is not None:
                    self.ui_set(d, sr, sn, {fa.name_hash: fid}, f'use font {fid}')
                self.modal = {'kind': 'message', 'title': 'New font', 'text': msg}
        imgui.tree_pop()

    def ui_make_font(self, d, base_id, ttf, path, keep=True, em=40, extra=uifont.EXTRA):
        """Build and save a font bundle; its font id is the file name (digits), else a new id. An existing file is
        kept as <file>.orig first. -> (ok, message, font id)."""
        root = roots.root_for(d.path, d.b.platform)
        base_path = os.path.join(root, 'UI', 'FONTS', f'{base_id}.BNDL') if root else None
        if not base_path or not os.path.isfile(base_path):
            return False, f'The base font {base_id} is not in the game folder.', None
        stem = os.path.splitext(os.path.basename(path))[0]
        fid = int(stem) if stem.isdigit() else self.ui_alloc_gc(d)
        try:
            base_b = Bundle.open(base_path)
            font, fr, tex = uifont.from_bundle(base_b)
            atlas = raster.decode(tex, base_b.platform, 0)
            data, new_atlas, drawn = uifont.build(font, atlas, ttf, str(fid), em_px=int(em), keep_base=keep,
                                                  extra=extra)
            raw = uifont.make_bundle(Bundle.open(base_path), fid, data, new_atlas)
            if os.path.isfile(path) and not os.path.isfile(path + '.orig'):
                import shutil
                shutil.copy2(path, path + '.orig')
            ops.write_file(path, raw)
        except Exception as ex:
            return False, f'The font could not be made: {ex}', None
        self.uied.setdefault('gfonts', {}).pop(fid, None)
        self.uied.pop('fonts', None)
        n = len(uifont.Font(data).slots)
        msg = (f'Font {fid} saved as {path}: {n} characters, {drawn} drawn from {os.path.basename(ttf)}. '
               f'The game finds it by its id ({fid} = UI/FONTS/{fid}.BNDL); a font id the game never used before is '
               f'untested in game - replacing an existing font file (its .orig is kept) is the sure way.')
        return True, msg, fid

    def ui_alloc_gc(self, d, extra=()):
        """A free GameChanger id for new objects / elements (0x7E000000 and up, not used in the bundle)."""
        used = [x.id & 0xFFFFFFFF for x in d.b.resources] + list(extra) + [self.uied.get('gc_last', 0)]
        n = max([0x7E000000] + [u for u in used if isinstance(u, int) and 0x7E000000 <= u < 0x7F000000]) + 1
        self.uied['gc_last'] = n
        return n

    def ui_fork_style(self, d, r, el):
        sr = d.b.find(el.style)
        if sr is None or not self.genesys_rewritable(d, r):
            self.status = 'This layout cannot be re-written exactly.'
            return
        gc = self.ui_alloc_gc(d)
        new_id = (1 << 56) | gc
        c = sr.copy()
        c.id = new_id
        c.modified = True
        sn = genesys.Reader(self.types, d.b.e).read_resource(sr)
        loc = sn.locs.get(U.H_GC)
        if loc is not None:
            buf = bytearray(c.data(0))
            struct.pack_into(d.b.e + 'I', buf, loc[0], gc)
            c.set_data(0, buf)
        d.checkpoint(f'own text style for element {el.index}', [new_id, r.id])
        d.b.add(c)

        def mutate(root, els):
            els[el.index].fields[U.H_PROPS].fields[U.H_STYLE] = Ref(new_id)
        if self.ui_restructure(d, r, el.index, mutate, f'own text style {gc} for element {el.index}', checkpoint=False):
            self.status = f'Element {el.index} has its own text style {gc} now (Ctrl+Z to undo).'

    # -- material, new elements, all properties --------------------------------------------------------------
    def ui_materials(self, d):
        key = ('uimats', d.uid, d.edits)
        hit = self.gcache.get(key)
        if hit is None:
            hit = []
            for x in d.b.resources:
                if x.type == 0x15:
                    n = self.ui_read(d, x)
                    if n is not None and self.types.name(n.type) == 'UIMaterial':
                        hit.append((x.id, n.fields.get(U.H_GC), self.ui_shader(d, x.id) or 'UI shader'))
            self.gcache[key] = hit
        return hit

    def ui_material(self, d, r, el):
        if el.material is None:
            return
        mats = self.ui_materials(d)
        ids = [m[0] for m in mats]
        if el.material not in ids:
            return
        imgui.set_next_item_width(200)
        names = [f'{gc}  {sh.replace("Shader", "")}' for _, gc, sh in mats]
        ch, k = imgui.combo('Material', ids.index(el.material), names)
        if ch and ids[k] != el.material:
            mid = ids[k]

            def mutate(root, els):
                els[el.index].fields[U.H_MATERIAL] = Ref(mid)
            self.ui_restructure(d, r, el.index, mutate, f'set the material of element {el.index}')
        if imgui.is_item_hovered():
            imgui.set_tooltip('The UIMaterials of this bundle: UI = normal, UIAdditive = glows (adds light), ...')

    def ui_templates(self, d):
        """{kind: (layout id, element index)}: an element of each kind in the bundle, to start new ones from."""
        key = ('uitpl', d.uid, d.edits)
        hit = self.gcache.get(key)
        if hit is None:
            hit = {}
            for x in d.b.resources:
                if x.type != 0x15:
                    continue
                n = self.ui_read(d, x)
                if n is None or self.types.name(n.type) != 'UILayout':
                    continue
                for e in U.elements(self.types, n):
                    k = 'MaskEnd' if e.kind == 'Mask' and e.mask_end else e.kind
                    if k == 'Image' and (e.image_bind or e.texture is None):
                        continue
                    hit.setdefault(k, (x.id, e.index))
            self.gcache[key] = hit
        return hit

    def ui_add_controls(self, d, st, layouts):
        tpl = self.ui_templates(d)
        kinds = [(k, n) for k, n in ADD_KINDS if k in tpl or (k == 'MaskEnd' and 'Mask' in tpl)]
        if not kinds:
            return
        st['add_kind'] = min(st['add_kind'], len(kinds) - 1)
        imgui.set_next_item_width(130)
        _, st['add_kind'] = imgui.combo('##addkind', st['add_kind'], [n for _, n in kinds])
        imgui.same_line()
        sel = st['sel']
        target = next((r for r, _n, _e in layouts if sel and sel[0] == r.id), layouts[0][0])
        if imgui.button('Add element'):
            kind = kinds[st['add_kind']][0]
            idx = self.ui_add_element(d, target, kind)
            if idx is not None:
                st['sel'] = (target.id, idx)
        imgui.same_line()
        if imgui.button('Image from PNG...'):
            p = filedialog.open_file('A picture for a new image element', self.cfg.get('last_import', ''),
                                     filedialog.IMAGES)
            if p:
                self.remember('last_import', p)
                idx = self.ui_add_png(d, target, p)
                if idx is not None:
                    st['sel'] = (target.id, idx)
        if imgui.is_item_hovered():
            imgui.set_tooltip('Or drop picture files on the canvas: an image element with its own texture each')
        clip = self.uied.get('clip')
        imgui.same_line()
        if imgui.button('Paste') and clip:
            idx = self.ui_paste(d, target)
            if idx is not None:
                st['sel'] = (target.id, idx)
        if imgui.is_item_hovered():
            imgui.set_tooltip(f'Ctrl+V: the copied {clip["kind"]} element' if clip else 'Ctrl+C an element first')
        if imgui.is_item_hovered():
            imgui.set_tooltip(f'A new element in layout {target.id:#x} (the selected element\'s), drawn on top, '
                              'started from one of its kind in this bundle (no bindings, no transitions).')

    def ui_add_element(self, d, r, kind):
        tpl = self.ui_templates(d)
        src = tpl.get(kind) or (tpl.get('Mask') if kind == 'MaskEnd' else None)
        if src is None:
            return None
        sn = self.ui_read(d, d.b.find(src[0]))
        tnode = (sn.fields.get(U.H_ELEMENTS) or [])[src[1]]
        n = self.ui_read(d, r)
        gc = self.ui_alloc_gc(d, [e.gc for e in U.elements(self.types, n) if isinstance(e.gc, int)])
        out = {}

        def mutate(root, els):
            c = gjson.clone(tnode)
            c.fields[U.H_GC] = gc
            c.fields[U.H_X], c.fields[U.H_Y] = 640.0, 360.0
            for h in (U.H_VIS, U.H_ROTBIND, U.H_XBIND, U.H_YBIND, U.H_IMAGE_BIND):
                if h in c.fields:
                    c.fields[h] = ''
            for _, h, _ in U.BEHAVIOURS:
                if h in c.fields:
                    c.fields[h] = None
            if kind in ('Mask', 'MaskEnd') and U.H_MASK_END in c.fields:
                c.fields[U.H_MASK_END] = 1 if kind == 'MaskEnd' else 0
            els.append(c)
            out['index'] = len(els) - 1
        if self.ui_restructure(d, r, None, mutate, f'add a {kind} element ({gc})'):
            self.status = f'New {kind} element {gc} added at 640, 360 (Ctrl+Z to undo).'
            return out.get('index')
        return None

    def ui_all_props(self, d, r, el):
        root = self.ui_read(d, r)
        self._obj_rewritable = self.genesys_rewritable(d, r)
        self._obj_edit = None
        flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.borders_inner_v | imgui.TableFlags_.resizable
        if imgui.begin_table('##uiallprops', 3, flags):
            imgui.table_setup_column('Field', imgui.TableColumnFlags_.width_fixed, 170)
            imgui.table_setup_column('Value', imgui.TableColumnFlags_.width_stretch)
            imgui.table_setup_column('Type', imgui.TableColumnFlags_.width_fixed, 90)
            self.node_rows(d, r, el.node, f'uiprop{r.id:x}.{el.index}')
            imgui.end_table()
        if self._obj_edit is not None and root is not None:
            fn, desc = self._obj_edit
            self._obj_edit = None
            self.genesys_rewrite(d, r, root, fn, desc)

    def ui_props(self, d, r, el, node):
        imgui.set_next_item_width(200)
        ch, xy = imgui.drag_float2('Position', [el.x, el.y], 1.0, -4000, 4000, '%.0f')
        if ch:
            self.ui_set(d, r, node, {U.H_X: xy[0], U.H_Y: xy[1]})
        imgui.set_next_item_width(200)
        ch, wh = imgui.drag_float2('Size', [el.w, el.h], 1.0, 0, 8000, '%.0f')
        if ch:
            self.ui_set(d, r, node, {U.H_W: max(0.0, wh[0]), U.H_H: max(0.0, wh[1])})
        imgui.set_next_item_width(200)
        ch, rot = imgui.drag_float('Rotation', el.rot, 0.5, -3600, 3600, '%.1f°')
        if ch:
            self.ui_set(d, r, node, {U.H_ROT: rot})
        imgui.set_next_item_width(200)
        ch, a = imgui.combo('Anchor', el.anchor, list(U.ANCHORS))
        if ch:
            self.ui_set(d, r, node, {U.H_ANCHOR: a})
        if el.tint is not None:
            imgui.set_next_item_width(200)
            ch, c = imgui.color_edit4('Tint', el.tint, imgui.ColorEditFlags_.float)
            if ch:
                self.ui_set_floats(d, r, node.fields[U.H_TINT], U.H_VALUE, c)
        op = node.fields.get(U.H_OPACITY)
        if isinstance(op, Node) and U.H_VALUE in op.locs:
            imgui.set_next_item_width(200)
            ch, v = imgui.slider_int('Opacity', int(el.opacity), 0, 100)
            if ch:
                self.ui_set(d, r, op, {U.H_VALUE: v})
        for h, label in ((U.H_VIS, 'Visibility'), (U.H_ROTBIND, 'Rotation binding')):
            holder = self.ui_holder(node, h)
            if h in holder.locs:
                self.ui_text_field(d, r, node, h, label, holder.fields.get(h) or '')
        txt = node.fields.get(U.H_TEXT)
        if isinstance(txt, Node):
            self.ui_text_field(d, r, txt, U.H_BINDING, 'Text binding', txt.fields.get(U.H_BINDING) or '')
            if U.H_STRING_ID in txt.locs:
                imgui.set_next_item_width(200)
                ch, sid = imgui.input_int('String id', int(el.string_id or 0), 0)
                if ch:
                    self.ui_set(d, r, txt, {U.H_STRING_ID: max(0, sid)})
                s = self.ui_strings(d).get(el.string_id) if el.string_id else None
                if s:
                    imgui.text_disabled(f'"{s[:60]}"')
        if el.texture is not None or el.image_bind:
            imgui.text_disabled(f'texture {el.texture or 0:#x}, quad {el.quad or 0:#x}')
            shader = self.ui_shader(d, el.material)
            if shader:
                imgui.text_disabled(f'shader {shader}')
            if el.image_bind:
                imgui.text_disabled(f'picture from the game: {el.image_bind}')

    def ui_text_field(self, d, r, node, h, label, value):
        key = f'##uitf{label}{id(node)}'
        imgui.set_next_item_width(200)
        ch, v = imgui.input_text(f'{label}{key}', value, imgui.InputTextFlags_.enter_returns_true)
        if ch and v != value:
            self.ui_set_text(d, r, node, h, v)

    def ui_behaviours(self, d, r, el, node):
        types_ = self.types.get(node.type)
        have = {f.name_hash for f in types_.fields}
        for phase, h, title in U.BEHAVIOURS:
            if h not in have:
                continue
            bs = el.behaviours.get(phase, [])
            imgui.text(f'{title} ({len(bs)})')
            for i, b in enumerate(bs):
                p = U.behaviour(b)
                imgui.push_id(f'{phase}{i}')
                imgui.set_next_item_width(90)
                ch, t = imgui.combo('##type', p['type'], list(U.BEHAVIOUR_TYPES))
                if ch:
                    self.ui_set(d, r, b, {U.H_TYPE: t})
                imgui.same_line()
                imgui.set_next_item_width(90)
                ch, prm = imgui.input_text('##prm', p['params'], imgui.InputTextFlags_.enter_returns_true)
                if ch and prm != p['params']:
                    self.ui_set_text(d, r, b, U.H_PARAMS, prm)
                if imgui.is_item_hovered():
                    imgui.set_tooltip('the parameters as text (the fields below edit them)')
                imgui.same_line()
                imgui.set_next_item_width(110)
                ch, dd = imgui.drag_float2('##dd', [p['delay'], p['duration']], 5.0, 0, 60000, '%.0f')
                if ch:
                    self.ui_set(d, r, b, {U.H_DELAY: max(0.0, dd[0]), U.H_DURATION: max(0.0, dd[1])})
                if imgui.is_item_hovered():
                    imgui.set_tooltip('delay, duration (ms)')
                imgui.same_line()
                imgui.set_next_item_width(60)
                ch, ease = imgui.combo('##ease', min(p['ease'], 3), list(U.EASES))
                if ch:
                    self.ui_set(d, r, b, {U.H_EASE: ease})
                imgui.same_line()
                if imgui.small_button('x'):
                    self.ui_restructure(d, r, el.index,
                                        lambda root, els, h=h, i=i: els[el.index].fields[h].pop(i),
                                        f'remove a {title.lower()} behaviour')
                    imgui.pop_id()
                    return
                self.ui_param_fields(d, r, b, p)
                imgui.pop_id()
        imgui.set_next_item_width(200)
        st = self.uied.setdefault('newbehaviour', {'k': 0})
        _, st['k'] = imgui.combo('##newb', st['k'], [n for n, *_ in NEW_BEHAVIOURS])
        imgui.same_line()
        if imgui.small_button('Add'):
            name, phase, typ, params, dur = NEW_BEHAVIOURS[st['k']]
            h = {'in': U.H_IN, 'out': U.H_OUT, 'idle': U.H_IDLE}[phase]
            self.ui_add_behaviour(d, r, el, h, params, name.lower(), typ, dur)
        if imgui.is_item_hovered():
            imgui.set_tooltip('Adds the transition with the parameters the retail data uses for it; the Timeline '
                              'below shows and drags its timing, Play in / out / idle shows it')

    def ui_param_fields(self, d, r, b, p):
        """The behaviour's parameters as fields of its type (numbers, IN / OUT, palette colour)."""
        spec = U.PARAM_FIELDS.get(p['type'])
        if not spec:
            return
        pend = self.uied.get('pending_param')
        f = U.parse_params(p['type'], pend[1] if pend and pend[0] == id(b) else p['params'])
        changed = False
        imgui.indent(16)
        for j, (name, kind) in enumerate(spec):
            if j:
                imgui.same_line()
            if isinstance(kind, tuple):
                imgui.set_next_item_width(70)
                cur = kind.index(f[name]) if f[name] in kind else 0
                ch, kk = imgui.combo(f'##{name}', cur, list(kind))
                if ch:
                    f[name] = kind[kk]
                    changed = True
            elif kind == 'text':
                pal = sorted(self.ui_palette(d))
                imgui.set_next_item_width(200)
                cur = pal.index(f[name]) if f[name] in pal else -1
                ch, kk = imgui.combo(f'##{name}', cur, pal) if pal else (False, -1)
                if ch and kk >= 0:
                    f[name] = pal[kk]
                    changed = True
                if pal and cur >= 0:
                    c = self.ui_palette(d)[pal[cur]]
                    imgui.same_line()
                    imgui.color_button('##pc', imgui.ImVec4(*c))
            else:
                imgui.set_next_item_width(64)
                ch, v = imgui.drag_float(f'##{name}', float(f[name]), 1.0, -10000, 10000, '%.0f')
                if imgui.is_item_hovered():
                    imgui.set_tooltip(name)
                if imgui.is_item_deactivated_after_edit() or (ch and not imgui.is_item_active()):
                    changed = True
                if ch:
                    f[name] = v
                    self.uied['pending_param'] = (id(b), U.format_params(p['type'], f))
            if j == len(spec) - 1:
                imgui.same_line()
                imgui.text_disabled(', '.join(name for name, _k in spec))
        imgui.unindent(16)
        pend = self.uied.get('pending_param')
        if pend and pend[0] == id(b) and not imgui.is_any_item_active():
            self.uied['pending_param'] = None
            if pend[1] != p['params']:
                self.ui_set_text(d, r, b, U.H_PARAMS, pend[1])
            return
        if changed:
            text = U.format_params(p['type'], f)
            if text != p['params']:
                self.ui_set_text(d, r, b, U.H_PARAMS, text)

    def ui_add_behaviour(self, d, r, el, h, params, desc, typ=0, duration=300.0):
        def mutate(root, els):
            n = els[el.index]
            b = self.ui_new_behaviour(n, h, params, duration)
            b.fields[U.H_TYPE] = typ
            cur = n.fields.get(h)
            n.fields[h] = (cur if isinstance(cur, list) else []) + [b]
        self.ui_restructure(d, r, el.index, mutate, f'add a {desc} to element {el.index}')

    def ui_binding_check(self, d, r, el):
        checker = self.ui_checker(d)
        if checker is None:
            return
        bad = []

        def walk(v, holder):
            if isinstance(v, Node):
                for k, x in v.fields.items():
                    if isinstance(x, str) and x and checker.bad(x):
                        bad.append((v, k, x))
                    else:
                        walk(x, v)
            elif isinstance(v, list):
                for x in v:
                    walk(x, holder)
        walk(el.node, el.node)
        if not bad:
            return
        imgui.text_colored(imgui.ImVec4(1.0, 0.6, 0.3, 1), f'{len(bad)} binding(s) the retail game does not have:')
        for _n, _k, x in bad[:6]:
            imgui.text_disabled(f'  {x[:60]}')
        if imgui.small_button('Neutralise them') and not d.b.truncated:
            for n, k, x in bad:
                self.ui_set_text(d, r, n, k, checker.fix(x)[0])
            self.status = f'{len(bad)} binding(s) of element {el.index} set to {BD.NEUTRAL}.'
        if imgui.is_item_hovered():
            imgui.set_tooltip(f'Each becomes {BD.NEUTRAL} (constant 0: hidden / no movement), as a missing source '
                              'can make the whole widget vanish in game')

    def ui_element_ops(self, d, r, el):
        self.ui_binding_check(d, r, el)
        imgui.separator()
        i = el.index
        if imgui.button('Copy'):
            self.ui_copy(d, r, el)
        imgui.same_line()
        if imgui.button('Cut'):
            self.ui_copy(d, r, el)
            if self.ui_restructure(d, r, i, lambda root, els: els.pop(i), f'cut element {i}'):
                self.uied_sel_shift(r, None)
                return
        imgui.same_line()
        if imgui.button('Duplicate'):
            def dup(root, els):
                c = gjson.clone(els[i])
                c.fields[U.H_GC] = self.ui_new_gc(els)
                c.fields[U.H_X] = float(c.fields.get(U.H_X) or 0) + 20
                c.fields[U.H_Y] = float(c.fields.get(U.H_Y) or 0) + 20
                els.insert(i + 1, c)
            if self.ui_restructure(d, r, i, dup, f'duplicate element {i}'):
                self.uied_sel_shift(r, i + 1)
        if imgui.is_item_hovered():
            imgui.set_tooltip('A copy (new id from 0x7E000000, 20 px lower right) drawn above the original')
        imgui.same_line()
        if imgui.button('Delete'):
            if self.ui_restructure(d, r, i, lambda root, els: els.pop(i), f'delete element {i}'):
                self.uied_sel_shift(r, None)
        imgui.same_line()
        if imgui.button('Down') and i > 0:
            if self.ui_restructure(d, r, i, lambda root, els: els.insert(i - 1, els.pop(i)), 'draw an element earlier'):
                self.uied_sel_shift(r, i - 1)
        if imgui.is_item_hovered():
            imgui.set_tooltip('Draw earlier (below the element before it)')
        imgui.same_line()
        if imgui.button('Up'):
            def up(root, els):
                if i + 1 < len(els):
                    els.insert(i + 1, els.pop(i))
            if self.ui_restructure(d, r, i, up, 'draw an element later'):
                self.uied_sel_shift(r, i + 1)
        if imgui.is_item_hovered():
            imgui.set_tooltip('Draw later (above the element after it)')
        if el.texture is not None:
            texs = [x for x in d.b.resources if x.type == T_TEXTURE]
            names = [f'{x.id:#x}' for x in texs]
            cur = next((k for k, x in enumerate(texs) if x.id == el.texture), -1)
            imgui.set_next_item_width(200)
            ch, k = imgui.combo('Texture', cur, names)
            if ch and k >= 0 and texs[k].id != el.texture:
                tid = texs[k].id

                def retex(root, els):
                    rd = els[i].fields.get(U.H_RDATA)
                    rd.fields[U.H_TEXHANDLE] = Ref(tid)
                    full = self.ui_full_quad(d)
                    if full is not None and isinstance(rd.fields.get(U.H_QUADS), list) and rd.fields[U.H_QUADS]:
                        rd.fields[U.H_QUADS][0] = Ref(full)
                self.ui_restructure(d, r, i, retex, f'set the texture of element {i}')
            if imgui.is_item_hovered():
                imgui.set_tooltip('Textures of this bundle (import a new one first to use your own image); the '
                                  'element then shows the whole texture')

    def ui_full_quad(self, d):
        """A quad renderable of the bundle with UVs 0..1 (the whole texture)."""
        for x in d.b.resources:
            if x.type == T_RENDERABLE and self.ui_quad(d, x.id) == [(0, 0), (1, 0), (1, 1), (0, 1)]:
                return x.id
        return None

    def uied_sel_shift(self, r, index):
        for v in self.uied['views'].values():
            if v['sel'] and v['sel'][0] == r.id:
                v['sel'] = (r.id, index) if index is not None else None
