"""Trigger map (mixed into the App): the TriggerData of HAWAII\\TRIGGERS.BNDL drawn from above over the districts
and the traffic lanes of the same folder; triggers can be selected, moved, edited, added, duplicated and deleted,
and exported / imported as CSV. The cell grid the game searches is rebuilt from the shapes on every change
(triggers.py)."""
import colorsys
import math
import os
import struct

import numpy as np
from imgui_bundle import imgui

from . import filedialog, ops, traffic
from . import triggers as TR
from . import zonelist as ZL
from .bundle import Bundle


def _u32(c, a=1.0):
    return imgui.color_convert_float4_to_u32(imgui.ImVec4(c[0], c[1], c[2], a))


# fixed colours for the gameplay types, the rest spread around the colour wheel
TYPE_COLOURS = {5: (1.0, 0.25, 0.2), 12: (0.45, 0.7, 1.0), 35: (1.0, 0.95, 0.3), 36: (0.75, 0.7, 0.25),
                48: (1.0, 0.4, 0.85), 50: (0.65, 0.45, 1.0), 52: (1.0, 0.65, 0.9), 58: (0.3, 1.0, 0.4),
                60: (1.0, 0.6, 0.1), 61: (0.2, 0.95, 0.95), 63: (0.65, 1.0, 0.65)}


def type_colour(kind):
    if kind in TYPE_COLOURS:
        return TYPE_COLOURS[kind]
    return colorsys.hsv_to_rgb((kind * 0.61803398875) % 1.0, 0.75, 1.0)


# what the map shows first: the gameplay places, without the thousands of event checkpoints and props
GAMEPLAY_TYPES = {5, 12, 35, 36, 48, 50, 52, 58, 60, 61, 63}


def type_label(kind):
    name = TR.TYPE_NAMES.get(kind, '')
    return f'{kind} ({name})' if name else f'{kind}'


class TriggerUI:
    """Mixin for App: self.trig holds the view state."""

    def trig_state(self):
        st = getattr(self, 'trig', None)
        if st is None:
            st = self.trig = {'key': None, 'td': None, 'error': None, 'zoom': 1.0, 'pan': [0.0, 0.0],
                              'sel': None, 'hidden': set(), 'find': '', 'drag': None, 'pending': None,
                              'shapes': {TR.BOX: True, TR.SPHERE: True, TR.LOCATOR: True},
                              'layers': {'districts': True, 'roads': True, 'grid': False},
                              'bg_folder': None, 'zones': [], 'roads': None, 'ext': None, 'outline': None}
        return st

    # -- background layers --------------------------------------------------------------------------------------
    def trig_background(self, st, folder):
        """Districts (PVS.BNDL) and traffic lanes (TRAFFIC.BNDL) of the trigger file's folder."""
        if st['bg_folder'] == folder:
            return
        st['bg_folder'], st['zones'], st['roads'] = folder, [], None
        try:
            p = os.path.join(folder, 'PVS.BNDL')
            if os.path.isfile(p):
                b = Bundle.open(p)
                r = next((x for x in b.resources if x.type == ZL.T_ZONELIST), None)
                if r is not None:
                    st['zones'] = ZL.read(r, b.e)
        except Exception:
            st['zones'] = []
        try:
            p = os.path.join(folder, 'TRAFFIC.BNDL')
            if os.path.isfile(p):
                b = Bundle.open(p)
                r = next((x for x in b.resources if x.type == traffic.T_TRAFFIC), None)
                if r is not None and b.e == '<':
                    lines = [np.array([(q[0], q[2]) for q in line], np.float32)
                             for hull in traffic.parse(r.data(0))['hulls'] for line in hull['lines'] if len(line) > 1]
                    boxes = np.array([(a[:, 0].min(), a[:, 1].min(), a[:, 0].max(), a[:, 1].max()) for a in lines],
                                     np.float32).reshape(-1, 4)
                    st['roads'] = (lines, boxes)
        except Exception:
            st['roads'] = None

    # -- geometry cache -----------------------------------------------------------------------------------------
    def trig_geometry(self, st, td):
        if st['ext'] is None or len(st['ext']) != len(td.triggers):
            st['ext'] = np.array([t.extent() for t in td.triggers], np.float32).reshape(-1, 4)
            st['outline'] = [t.corners() if t.shape == TR.BOX else None for t in td.triggers]
            st['area'] = np.array([self.trig_area(t) for t in td.triggers], np.float32)

    def trig_touch(self, st, td, i):
        """Trigger i changed shape or place: its cached geometry too."""
        if st['ext'] is not None and i < len(st['ext']):
            t = td.triggers[i]
            st['ext'][i] = t.extent()
            st['outline'][i] = t.corners() if t.shape == TR.BOX else None
            st['area'][i] = self.trig_area(t)

    # -- view ---------------------------------------------------------------------------------------------------
    def trigger_view(self, d, r):
        """The Preview tab: the map, or a pointer to its large window."""
        st = self.trig_state()
        win = st.get('window')
        if win and win['key'] == (d.uid, r.id):
            imgui.text_disabled('The triggers are open in the trigger map window.')
            if imgui.button('Bring the window to the front'):
                win['focus'] = True
            return
        if imgui.button('Open the trigger map in a large window'):
            st['window'] = {'key': (d.uid, r.id), 'focus': True}
            return
        self.trig_body(d, r)

    def trigger_window(self):
        """The trigger map in its own window over the app (called every frame)."""
        st = getattr(self, 'trig', None)
        win = st.get('window') if st else None
        if not win:
            return
        d = self.doc_by_uid(win['key'][0])
        r = d.b.find(win['key'][1]) if d is not None else None
        if r is None:
            st['window'] = None
            return
        vp = imgui.get_main_viewport()
        imgui.set_next_window_pos(imgui.ImVec2(vp.work_pos.x + 24, vp.work_pos.y + 24), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size(imgui.ImVec2(vp.work_size.x - 48, vp.work_size.y - 48), imgui.Cond_.first_use_ever)
        if win.get('focus'):
            imgui.set_next_window_focus()
            win['focus'] = False
        title = f'Trigger map - {os.path.basename(d.path or "")}  ({r.id:#x})###trigmap'
        _, keep = imgui.begin(title, True, imgui.WindowFlags_.no_collapse | imgui.WindowFlags_.no_scrollbar)
        focused = imgui.is_window_focused(imgui.FocusedFlags_.root_and_child_windows)
        if not keep or (focused and imgui.is_key_pressed(imgui.Key.escape) and not imgui.get_io().want_text_input):
            imgui.end()
            st['window'] = None
            return
        self.trig_body(d, r)
        imgui.end()

    def trig_body(self, d, r):
        st = self.trig_state()
        key = (d.uid, r.id, id(r.data(0)))
        if st['key'] != key:
            st['key'], st['ext'], st['drag'], st['pending'] = key, None, None, None
            try:
                st['td'], st['error'] = TR.TriggerData.read(r.data(0), d.b.e), None
            except (TR.TriggerError, struct.error) as ex:
                st['td'], st['error'] = None, str(ex)
            if st['td'] is not None and st['sel'] is not None and st['sel'] >= len(st['td'].triggers):
                st['sel'] = None
            if st['td'] is not None and not st.get('preset_done'):
                st['preset_done'] = True
                st['hidden'] = {t.kind for t in st['td'].triggers} - GAMEPLAY_TYPES
        td = st['td']
        if td is None:
            imgui.text_wrapped(f'Cannot read this TriggerData: {st["error"]}')
            return
        self.trig_background(st, os.path.dirname(d.path or ''))
        ro = d.b.truncated

        def commit(desc):
            """td (edited in place) into the resource as one undo step; the grid is rebuilt."""
            d.checkpoint(desc, [r.id])
            r.set_data(0, td.to_bytes())
            td.raw, td.snapshot = bytes(r.data(0)), td.state()
            st['key'] = (d.uid, r.id, id(r.data(0)))
            st['pending'] = None
            d.summary.pop(r.id, None)
            d.b.modified = True
            d.edits += 1
            self.edit_gen += 1

        n = td.counts()
        nx, nz = td.grid[:2]
        imgui.text(f'{n[TR.BOX]} boxes, {n[TR.SPHERE]} spheres, {n[TR.LOCATOR]} locators; '
                   f'grid {nx} x {nz} cells of {td.grid[4]:g} m')
        imgui.same_line()
        imgui.text_disabled('click: select, drag: move, wheel: zoom, right drag: pan')
        if not ro:
            for shape in (TR.BOX, TR.SPHERE, TR.LOCATOR):
                if imgui.button(f'Add {TR.SHAPES[shape].lower()}'):
                    self.trig_add(st, td, shape)
                    commit(f'add {TR.SHAPES[shape].lower()} trigger')
                imgui.same_line()
            sel = st['sel']
            imgui.begin_disabled(sel is None)
            if imgui.button('Duplicate') and sel is not None:
                t = td.triggers[sel].copy()
                t.pos[0] += 10.0
                td.triggers.append(t)
                st['sel'] = len(td.triggers) - 1
                st['ext'] = None
                commit('duplicate trigger')
            imgui.same_line()
            if imgui.button('Delete') and sel is not None:
                del td.triggers[sel]
                st['sel'] = None
                st['ext'] = None
                commit('delete trigger')
            imgui.end_disabled()
            imgui.same_line()
        if imgui.button('Export CSV'):
            path = filedialog.save_file('Export triggers (CSV)', 'triggers.csv', filedialog.CSV, 'csv')
            if path:
                ops.write_file(path, TR.to_csv(td, TR.TYPE_NAMES).encode('utf-8'))
                self.status = f'{len(td.triggers)} triggers written to {path}'
        if not ro:
            imgui.same_line()
            if imgui.button('Import CSV'):
                path = filedialog.open_file('Import triggers (CSV)', self.cfg.get('last_import', ''), filedialog.CSV)
                if path:
                    try:
                        with open(path, encoding='utf-8-sig') as f:
                            td.triggers = TR.from_csv(f.read())
                        st['sel'], st['ext'] = None, None
                        commit('import triggers')
                        self.status = f'{len(td.triggers)} triggers read from {path}'
                    except (OSError, TR.TriggerError) as ex:
                        self.status = f'Cannot import {path}: {ex}'
        lay = st['layers']
        for k, label in (('districts', 'Districts'), ('roads', 'Traffic lanes'), ('grid', 'Grid')):
            imgui.same_line()
            _, lay[k] = imgui.checkbox(label, lay[k])

        self.trig_geometry(st, td)
        avail = imgui.get_content_region_avail()
        side = 250.0
        imgui.begin_child('trig_types', imgui.ImVec2(side, avail.y), imgui.ChildFlags_.borders)
        self.trig_type_list(st, td)
        imgui.end_child()
        imgui.same_line()
        props_w = 380.0 if st['sel'] is not None else 0.0
        map_w = max(200.0, avail.x - side - props_w - 2 * imgui.get_style().item_spacing.x)
        imgui.begin_child('trig_map', imgui.ImVec2(map_w, avail.y), imgui.ChildFlags_.none,
                          imgui.WindowFlags_.no_scrollbar | imgui.WindowFlags_.no_scroll_with_mouse)
        self.trig_map(st, td, ro)
        imgui.end_child()
        if st['sel'] is not None:
            imgui.same_line()
            imgui.begin_child('trig_props', imgui.ImVec2(0, avail.y), imgui.ChildFlags_.borders)
            self.trig_props(st, td, ro)
            imgui.end_child()
        # an edit (drag, typing, slider) becomes one undo step when the mouse / keyboard lets go of it
        if st['pending'] and st['drag'] is None and not imgui.is_any_item_active() and not ro:
            commit(st['pending'])

    def trig_add(self, st, td, shape):
        nx, nz, ox, oz, cw, ch = td.grid
        cx, cz = st.get('centre', (ox + nx * cw / 2, oz + nz * ch / 2))
        near = min(td.triggers, key=lambda t: math.hypot(t.pos[0] - cx, t.pos[2] - cz), default=None)
        t = TR.Trigger(shape, [cx, near.pos[1] if near is not None else 0.0, cz])
        if st['sel'] is not None and td.triggers[st['sel']].shape == shape:
            src = td.triggers[st['sel']]
            t.kind, t.flags, t.owner = src.kind, src.flags, src.owner
            t.rot, t.half, t.radius, t.dir = list(src.rot), list(src.half), src.radius, list(src.dir)
        td.triggers.append(t)
        st['sel'] = len(td.triggers) - 1
        st['ext'] = None

    def trig_type_list(self, st, td):
        counts = {}
        for t in td.triggers:
            counts[t.kind] = counts.get(t.kind, 0) + 1
        imgui.text('Shapes')
        for s in (TR.BOX, TR.SPHERE, TR.LOCATOR):
            _, st['shapes'][s] = imgui.checkbox(TR.SHAPES[s] + f'##shape{s}', st['shapes'][s])
            if s != TR.LOCATOR:
                imgui.same_line()
        imgui.set_next_item_width(-1)
        _, st['find'] = imgui.input_text_with_hint('##trigfind', 'owner id (decimal or 0x...)', st['find'])
        imgui.separator()
        imgui.text('Trigger types')
        imgui.same_line()
        if imgui.small_button('all'):
            st['hidden'].clear()
        imgui.same_line()
        if imgui.small_button('none'):
            st['hidden'] = set(counts)
        imgui.same_line()
        if imgui.small_button('gameplay'):
            st['hidden'] = set(counts) - GAMEPLAY_TYPES
        if imgui.is_item_hovered():
            imgui.set_tooltip('jack spots, roadblocks, safehouses, drift markers, collectables, compound objects')
        imgui.text_disabled('click a name: only that type')
        for kind in sorted(counts):
            c = type_colour(kind)
            ch, vis = imgui.checkbox(f'##tk{kind}', kind not in st['hidden'])
            if ch:
                (st['hidden'].discard if vis else st['hidden'].add)(kind)
            imgui.same_line()
            imgui.color_button(f'##tc{kind}', imgui.ImVec4(c[0], c[1], c[2], 1), imgui.ColorEditFlags_.no_tooltip,
                               imgui.ImVec2(12, 12))
            imgui.same_line()
            solo = st['hidden'] == set(counts) - {kind}
            if imgui.selectable(f'{type_label(kind)}  x{counts[kind]}##tn{kind}', solo)[0]:
                st['hidden'] = set() if solo else set(counts) - {kind}
            if imgui.is_item_hovered():
                imgui.set_tooltip((TR.TYPE_NOTES.get(kind) or 'not referred to by any data') + chr(10)
                                  + ('click: show every type' if solo else 'click: show only this type'))

    def trig_shown(self, st, td):
        """Indices of the triggers the filters let through."""
        find = st['find'].strip()
        want = None
        if find:
            try:
                want = int(find, 0)
            except ValueError:
                want = -1
        hidden, shapes = st['hidden'], st['shapes']
        return np.array([i for i, t in enumerate(td.triggers)
                         if t.kind not in hidden and shapes[t.shape] and (want is None or t.owner == want)], np.int64)

    def trig_map(self, st, td, ro):
        nx, nz, ox, oz, cw, ch = td.grid
        x0, z0, x1, z1 = ox, oz, ox + nx * cw, oz + nz * ch
        avail = imgui.get_content_region_avail()
        w, h = max(100.0, avail.x), max(100.0, avail.y)
        p0 = imgui.get_cursor_screen_pos()
        imgui.invisible_button('##trigmap', imgui.ImVec2(w, h),
                               imgui.ButtonFlags_.mouse_button_left | imgui.ButtonFlags_.mouse_button_right)
        hovered = imgui.is_item_hovered()
        clicked = imgui.is_item_clicked(imgui.MouseButton_.left)
        active = imgui.is_item_active()
        io = imgui.get_io()
        cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
        base = 0.96 * min(w / max(x1 - x0, 1), h / max(z1 - z0, 1))
        scale = base * st['zoom']
        vx, vz = cx + st['pan'][0], cz + st['pan'][1]
        mx = (io.mouse_pos.x - p0.x - w / 2) / scale + vx
        mz = (io.mouse_pos.y - p0.y - h / 2) / scale + vz
        if hovered and io.mouse_wheel:
            st['zoom'] = min(400.0, max(0.5, st['zoom'] * 1.2 ** io.mouse_wheel))
            scale = base * st['zoom']
            st['pan'][0] = mx - (io.mouse_pos.x - p0.x - w / 2) / scale - cx
            st['pan'][1] = mz - (io.mouse_pos.y - p0.y - h / 2) / scale - cz
            vx, vz = cx + st['pan'][0], cz + st['pan'][1]
        if active and imgui.is_mouse_down(1):
            st['pan'][0] -= io.mouse_delta.x / scale
            st['pan'][1] -= io.mouse_delta.y / scale
            vx, vz = cx + st['pan'][0], cz + st['pan'][1]
        st['centre'] = (vx, vz)
        wx0, wx1 = vx - w / 2 / scale, vx + w / 2 / scale
        wz0, wz1 = vz - h / 2 / scale, vz + h / 2 / scale
        sx, sy = p0.x + w / 2 - vx * scale, p0.y + h / 2 - vz * scale

        def scr(x, z):
            return imgui.ImVec2(sx + x * scale, sy + z * scale)

        dl = imgui.get_window_draw_list()
        dl.push_clip_rect(p0, imgui.ImVec2(p0.x + w, p0.y + h), True)
        dl.add_rect_filled(p0, imgui.ImVec2(p0.x + w, p0.y + h), imgui.get_color_u32(imgui.Col_.frame_bg))
        lay = st['layers']
        if lay['districts'] and st['zones']:
            ds = sorted({z.district for z in st['zones']})
            pal = {dd: colorsys.hsv_to_rgb(i / max(len(ds), 1), 0.35, 0.55) for i, dd in enumerate(ds)}
            for z in st['zones']:
                bx = z.box
                if bx[2] < wx0 or bx[0] > wx1 or bx[3] < wz0 or bx[1] > wz1 or len(z.points) < 3:
                    continue
                pts = [scr(x, y) for x, y in z.points]
                dl.add_concave_poly_filled(pts, _u32(pal[z.district], 0.18))
                dl.add_polyline(pts, _u32((0.6, 0.6, 0.6), 0.35), 1.0, imgui.ImDrawFlags_.closed)
        if lay['roads'] and st['roads']:
            lines, boxes = st['roads']
            vis = np.nonzero((boxes[:, 2] >= wx0) & (boxes[:, 0] <= wx1) & (boxes[:, 3] >= wz0)
                             & (boxes[:, 1] <= wz1))[0]
            step = 1 if scale > 0.25 else 4
            col = _u32((0.8, 0.8, 0.82), 0.45)
            for j in vis:
                a = lines[j]
                if step > 1 and len(a) > 2 * step:
                    a = np.vstack([a[::step], a[-1:]])
                pts = (a * scale + (sx, sy)).tolist()
                dl.add_polyline([imgui.ImVec2(p[0], p[1]) for p in pts], col, 1.0, 0)
        if lay['grid']:
            gc = _u32((1, 1, 1), 0.12)
            for a in range(nx + 1):
                dl.add_line(scr(ox + a * cw, z0), scr(ox + a * cw, z1), gc)
            for b in range(nz + 1):
                dl.add_line(scr(x0, oz + b * ch), scr(x1, oz + b * ch), gc)
        # triggers in view, under the mouse
        ext = st['ext']
        shown = self.trig_shown(st, td)
        if len(shown):
            e = ext[shown]
            shown = shown[(e[:, 2] >= wx0) & (e[:, 0] <= wx1) & (e[:, 3] >= wz0) & (e[:, 1] <= wz1)]
        sel = st['sel']
        hot = None
        if hovered and st['drag'] is None and len(shown):
            m = 6.0 / scale
            e = ext[shown]
            near = shown[(e[:, 0] - m <= mx) & (e[:, 2] + m >= mx) & (e[:, 1] - m <= mz) & (e[:, 3] + m >= mz)]
            cand = [int(i) for i in near if td.triggers[int(i)].contains(mx, mz, m)]
            if cand:
                hot = min(cand, key=lambda i: self.trig_area(td.triggers[i]))
        if len(shown):
            shown = shown[np.argsort(-st['area'][shown], kind='stable')]
        for i in shown.tolist():
            if i != sel and i != hot:
                self.trig_draw(dl, td.triggers[i], st['outline'][i], scr, scale, type_colour(td.triggers[i].kind),
                               0)
        if hot is not None and hot != sel:
            self.trig_draw(dl, td.triggers[hot], st['outline'][hot], scr, scale, (1, 1, 1), 1)
        if sel is not None and sel < len(td.triggers):
            self.trig_draw(dl, td.triggers[sel], st['outline'][sel], scr, scale, (1.0, 0.92, 0.25), 2)
        legend = ['coloured outlines: triggers (colour = type, list on the left)']
        if lay['districts'] and st['zones']:
            legend.append('grey areas: districts / track units')
        if lay['roads'] and st['roads']:
            legend.append('white lines: traffic lanes')
        legend.append(f'{len(shown)} triggers in view')
        tp = imgui.ImVec2(p0.x + 8, p0.y + 6)
        for k, txt in enumerate(legend):
            q = imgui.ImVec2(tp.x, tp.y + k * (imgui.get_text_line_height() + 1))
            dl.add_text(imgui.ImVec2(q.x + 1, q.y + 1), _u32((0, 0, 0), 0.8), txt)
            dl.add_text(q, _u32((0.9, 0.9, 0.9), 0.9), txt)
        dl.pop_clip_rect()
        # click selects; dragging the selected trigger moves it
        if clicked:
            if (sel is not None and not ro
                    and td.triggers[sel].contains(mx, mz, 6.0 / scale)):
                t = td.triggers[sel]
                st['drag'] = (sel, mx - t.pos[0], mz - t.pos[2], (t.pos[0], t.pos[2]), False)
            else:
                st['sel'] = hot
        if st['drag'] is not None:
            i, dx, dz, start, moved = st['drag']
            t = td.triggers[i]
            if imgui.is_mouse_down(0):
                px, pz = mx - dx, mz - dz
                if moved or math.hypot(px - start[0], pz - start[1]) * scale > 3:
                    t.pos[0], t.pos[2] = px, pz
                    st['drag'] = (i, dx, dz, start, True)
                    self.trig_touch(st, td, i)
            else:
                st['drag'] = None
                if moved:
                    st['pending'] = f'move trigger {i}'
        if hovered and hot is not None:
            t = td.triggers[hot]
            imgui.set_tooltip(f'{TR.SHAPES[t.shape]} {hot}: type {type_label(t.kind)}\n'
                              f'owner {t.owner} ({t.owner:#x})\n'
                              f'x {t.pos[0]:.1f}  y {t.pos[1]:.1f}  z {t.pos[2]:.1f}')
        elif hovered:
            imgui.set_tooltip(f'x {mx:.1f}  z {mz:.1f}')

    @staticmethod
    def trig_area(t):
        if t.shape == TR.BOX:
            return 4 * t.half[0] * t.half[2]
        if t.shape == TR.SPHERE:
            return math.pi * t.radius * t.radius
        return 0.0

    @staticmethod
    def trig_draw(dl, t, outline, scr, scale, c, level):
        """level 0 normal, 1 under the mouse, 2 selected."""
        lw = (1.0, 1.5, 2.5)[level]
        line = _u32(c, 1.0)
        if t.shape == TR.BOX:
            if max(t.half[0], t.half[2]) * scale < 1.5 and level == 0:
                p = scr(t.pos[0], t.pos[2])
                dl.add_rect_filled(imgui.ImVec2(p.x - 1, p.y - 1), imgui.ImVec2(p.x + 1, p.y + 1), line)
                return
            pts = [scr(x, z) for x, z in outline]
            if level:
                dl.add_convex_poly_filled(pts, _u32(c, (0.0, 0.25, 0.35)[level]))
            dl.add_polyline(pts, line if level else _u32(c, 0.85), lw, imgui.ImDrawFlags_.closed)
        elif t.shape == TR.SPHERE:
            p = scr(t.pos[0], t.pos[2])
            rad = max(2.0, t.radius * scale)
            if level:
                dl.add_circle_filled(p, rad, _u32(c, (0.0, 0.25, 0.35)[level]))
            dl.add_circle(p, rad, line if level else _u32(c, 0.85), 0, lw)
        else:
            p = scr(t.pos[0], t.pos[2])
            dl.add_circle_filled(p, (2.0, 3.0, 4.0)[level], line)
            if level or scale > 0.5:
                ln = (8.0, 12.0, 18.0)[level]
                k = math.hypot(t.dir[0], t.dir[2]) or 1.0
                dl.add_line(p, imgui.ImVec2(p.x + t.dir[0] / k * ln, p.y + t.dir[2] / k * ln), line, lw)

    def trig_props(self, st, td, ro):
        imgui.push_item_width(-110)
        try:
            self.trig_props_body(st, td, ro)
        finally:
            imgui.pop_item_width()

    def trig_props_body(self, st, td, ro):
        i = st['sel']
        t = td.triggers[i]
        imgui.text(f'{TR.SHAPES[t.shape]} {i}')
        imgui.same_line()
        imgui.text_disabled(f'cell {td.cell_of(t.pos[0], t.pos[2])}')
        if imgui.small_button('Centre the map on it'):
            nx, nz, ox, oz, cw, ch = td.grid
            st['pan'] = [t.pos[0] - (ox + nx * cw / 2), t.pos[2] - (oz + nz * ch / 2)]
            st['zoom'] = max(st['zoom'], 20.0)
        imgui.separator()
        changed = False
        imgui.begin_disabled(ro)
        imgui.set_next_item_width(120)
        ch, v = imgui.input_int('Type', t.kind)
        if ch:
            t.kind, changed = max(0, min(255, v)), True
        if TR.TYPE_NAMES.get(t.kind):
            imgui.same_line()
            imgui.text_disabled(TR.TYPE_NAMES[t.kind])
        imgui.set_next_item_width(120)
        ch, v = imgui.input_int('Flags', t.flags)
        if ch:
            t.flags, changed = max(0, min(255, v)), True
        imgui.set_next_item_width(160)
        ch, v = imgui.input_text('Owner id', str(t.owner), imgui.InputTextFlags_.enter_returns_true)
        if ch:
            try:
                t.owner, changed = int(v, 0) & 0xFFFFFFFF, True
            except ValueError:
                self.status = f'{v!r} is not a number'
        imgui.separator()
        ch, v = imgui.input_float3('Position', t.pos, '%.2f')
        if ch:
            t.pos, changed = list(v), True
        if t.shape == TR.BOX:
            ch, v = imgui.input_float3('Half size', t.half, '%.2f')
            if ch:
                t.half, changed = [max(0.01, x) for x in v], True
            ch, v = imgui.slider_float('Heading', t.yaw(), -180.0, 180.0, '%.1f deg')
            if ch:
                t.set_yaw(v)
                changed = True
            ch, v = imgui.input_float4('Rotation', t.rot, '%.4f')
            if ch:
                k = math.sqrt(sum(x * x for x in v)) or 1.0
                t.rot, changed = [x / k for x in v], True
            if imgui.is_item_hovered():
                imgui.set_tooltip('quaternion x, y, z, w')
        elif t.shape == TR.SPHERE:
            ch, v = imgui.input_float('Radius', t.radius, 0.5, 5.0, '%.2f')
            if ch:
                t.radius, changed = max(0.01, v), True
        else:
            ch, v = imgui.slider_float('Heading', t.yaw(), -180.0, 180.0, '%.1f deg')
            if ch:
                t.set_yaw(v)
                changed = True
            ch, v = imgui.input_float3('Direction', t.dir, '%.4f')
            if ch:
                k = math.sqrt(sum(x * x for x in v)) or 1.0
                t.dir, changed = [x / k for x in v], True
        imgui.end_disabled()
        if changed and not ro:
            self.trig_touch(st, td, i)
            st['pending'] = f'edit trigger {i}'
        imgui.separator()
        same = [j for j, u in enumerate(td.triggers) if u.owner == t.owner and j != i]
        imgui.text(f'Same owner: {len(same)} more')
        for j in same[:40]:
            u = td.triggers[j]
            if imgui.selectable(f'{TR.SHAPES[u.shape]} {j}  type {type_label(u.kind)}##own{j}', False)[0]:
                st['sel'] = j
        where = self.names.where.get(t.owner) if getattr(self, 'names', None) is not None else None
        if where:
            imgui.separator()
            imgui.text('Owner id found in:')
            for p in where:
                imgui.text_disabled(p)
