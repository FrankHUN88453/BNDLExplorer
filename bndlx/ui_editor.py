"""UI layout editor with a live preview (mixed into App): the elements of a UILayout, or of all the layouts a
screen's widget shows, drawn as the game draws them (textured quads with their UVs, tint, opacity, masks as
clip rectangles, labels in the style's size and colour) against made-up game values.

Click selects, dragging moves, the corner handle resizes, arrow keys nudge (Shift: 10 px); the panel edits
position, size, rotation, anchor, bindings, tint, opacity, text and the transition behaviours (fade in / out
...), which can be played in the preview. Values are written in place; adding / removing elements and
behaviours re-writes the layout object (gwrite), only when the object comes back byte for byte unchanged.
"""
import math
import os
import struct
import time

from imgui_bundle import imgui, immvision

from . import filedialog, genesys, gjson, mesh, ops, roots, uiscene as U
from .genesys import Node, Ref

T_TEXTURE = 0x01
T_RENDERABLE = 0x05
HANDLE = 9          # px


def _u32(c):
    r, g, b, a = (max(0.0, min(1.0, x)) for x in c)
    return imgui.color_convert_float4_to_u32(imgui.ImVec4(r, g, b, a))


class UIEditorUI:
    # -- state ----------------------------------------------------------------------------------------------
    def uied_state(self, key):
        if not hasattr(self, 'uied'):
            self.uied = {'tex': {}, 'quads': {}, 'styles': {}, 'bg': None, 'views': {}}
        v = self.uied['views'].get(key)
        if v is None:
            v = self.uied['views'][key] = {
                'sel': None, 'drag': None, 'state': {}, 'respect_vis': True, 'unknown_visible': True,
                'show_hidden': False, 'show_masks': False, 'outlines': False, 'play': None, 'widget': 0,
                'zoom': 1.0, 'clip': True}
        return v

    # -- entry points (tabs of the object view) -------------------------------------------------------------
    def ui_layout_preview(self, d, r, node):
        self.ui_editor(d, [(r, node)], ('layout', d.uid, r.id))

    def ui_screen_preview(self, d, r, node):
        widgets = U.screen_widgets(self.types, node)
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
        self.ui_editor(d, items, ('screen', d.uid, r.id))

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
    def ui_texture(self, d, tid):
        """(GL texture, w, h) of a texture (decoded once per data)."""
        if not tid:
            return None
        cache = self.uied['tex']
        img = self.texture_image(d, tid, 1024)
        if img is None:
            return None
        key = (tid, id(img))
        hit = cache.get(key)
        if hit is None:
            for k in [k for k in cache if k[0] == tid]:
                del cache[k]
            hit = cache[key] = (immvision.GlTexture(img), img.shape[1], img.shape[0], img)
        return hit

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
        """(font size, RGBA) of a TextStyle object."""
        cache = self.uied['styles']
        key = (d.uid, sid)
        if key in cache:
            return cache[key]
        out = (None, None)
        r = d.b.find(sid) if sid else None
        if r is not None and r.type == 0x15:
            n = self.ui_read(d, r)
            if n is not None:
                col = n.fields.get(U.H_STYLE_COLOUR)
                out = (n.fields.get(U.H_FONTSIZE), [float(x) for x in col] if isinstance(col, list) else None)
        cache[key] = out
        return out

    def ui_text(self, d, el, ev):
        if el.string_id:
            s = ops.game_strings(d.path).get(el.string_id)
            if s is not None:
                return s
        if el.text:
            v = ev.value(el.text)
            if v is not None:
                return f'{v:.0f}' if abs(v - round(v)) < 1e-6 else f'{v:.1f}'
            path = U.binding_path(el.text)
            if path and '.' in path:
                return '[' + path.split('.')[-1] + ']'
            return el.text
        return ''

    # -- the editor -----------------------------------------------------------------------------------------
    def ui_editor(self, d, items, key):
        st = self.uied_state(key)
        ev = U.Evaluator(st['state'])
        layouts = [(r, n, U.elements(self.types, n)) for r, n in items]
        avail = imgui.get_content_region_avail()
        panel = 360
        cw = max(320.0, avail.x - panel - 12)
        ch = cw * U.H / U.W
        if ch > avail.y - 8:
            ch = max(180.0, avail.y - 8)
            cw = ch * U.W / U.H
        imgui.begin_child('##uicanvas', imgui.ImVec2(cw, ch))
        self.ui_canvas(d, st, layouts, ev, cw, ch)
        imgui.end_child()
        imgui.same_line()
        imgui.begin_child('##uipanel', imgui.ImVec2(0, ch))
        self.ui_panel(d, st, layouts, ev)
        imgui.end_child()

    def ui_canvas(self, d, st, layouts, ev, cw, ch):
        dl = imgui.get_window_draw_list()
        o = imgui.get_cursor_screen_pos()
        s = cw / U.W

        def P(x, y):
            return imgui.ImVec2(o.x + x * s, o.y + y * s)

        imgui.invisible_button('##uihit', imgui.ImVec2(cw, ch))
        hovered = imgui.is_item_hovered()
        dl.add_rect_filled(o, imgui.ImVec2(o.x + cw, o.y + ch), _u32((0.11, 0.12, 0.14, 1)))
        if self.uied['bg'] is not None:
            dl.add_image(imgui.ImTextureRef(self.uied['bg'][0].texture_id), o, imgui.ImVec2(o.x + cw, o.y + ch))
        else:
            for k in range(0, U.W, 80):                       # a faint grid (80 px)
                dl.add_line(P(k, 0), P(k, U.H), _u32((1, 1, 1, 0.035)))
            for k in range(0, U.H, 80):
                dl.add_line(P(0, k), P(U.W, k), _u32((1, 1, 1, 0.035)))
        dl.push_clip_rect(o, imgui.ImVec2(o.x + cw, o.y + ch), True)
        now = time.time()
        play = st['play']
        if play is not None:
            t = (now - play[1]) * 1000.0
            longest = max((U.phase_length(el, play[0]) for _, _, els in layouts for el in els), default=0.0)
            if play[0] != 'idle' and t > longest + 400:
                st['play'] = play = None
        sel = st['sel']
        drag = st['drag']
        shapes = []                                           # (rid, el, corners) of the drawn elements, for picking
        for r, _n, els in layouts:
            clips = 0
            for el in els:
                x, y, w, h, rot = el.x, el.y, el.w, el.h, el.rot
                if drag is not None and drag['key'] == (r.id, el.index):
                    x, y, w, h = drag['x'], drag['y'], drag['w'], drag['h']
                visible = ev.visible(el.vis, st['unknown_visible']) or not st['respect_vis']
                rb = ev.value(el.rot_bind)
                if rb is not None:
                    rot += rb
                alpha, dx, dy, scale, drot = 1.0, 0.0, 0.0, 1.0, 0.0
                if play is not None:
                    alpha, dx, dy, scale, drot = U.animate(el, play[0], (now - play[1]) * 1000.0)
                x, y, rot = x + dx, y + dy, rot + drot
                pts = U.corners(x, y, w, h, el.anchor, rot, scale)
                if el.kind == 'Mask':
                    if el.mask_end:
                        if clips:
                            dl.pop_clip_rect()
                            clips -= 1
                    elif st['clip'] and visible:
                        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                        dl.push_clip_rect(P(min(xs), min(ys)), P(max(xs), max(ys)), True)
                        clips += 1
                    if st['show_masks']:
                        self.ui_outline(dl, P, pts, (0.3, 0.8, 1.0, 0.7))
                    shapes.append((r, el, pts))
                    continue
                if not visible:
                    if st['show_hidden']:
                        self.ui_outline(dl, P, pts, (1, 1, 1, 0.18))
                        shapes.append((r, el, pts))
                    continue
                shapes.append((r, el, pts))
                op = max(0.0, min(1.0, el.opacity / 100.0)) * alpha
                if el.texture is not None or el.kind == 'Image':
                    tex = self.ui_texture(d, el.texture)
                    if tex is None:
                        self.ui_outline(dl, P, pts, (1, 0.4, 0.3, 0.6))
                        continue
                    uv = self.ui_quad(d, el.quad) or [(0, 0), (1, 0), (1, 1), (0, 1)]
                    tint = el.tint or [1, 1, 1, 1]
                    col = (tint[0], tint[1], tint[2], tint[3] * op)
                    q = [P(*p) for p in pts]
                    dl.add_image_quad(imgui.ImTextureRef(tex[0].texture_id), q[0], q[1], q[2], q[3],
                                      imgui.ImVec2(*uv[0]), imgui.ImVec2(*uv[1]), imgui.ImVec2(*uv[2]),
                                      imgui.ImVec2(*uv[3]), _u32(col))
                elif el.kind in ('Label', 'BackedLabel', 'ScrollingText', 'ScrollableLabel'):
                    text = self.ui_text(d, el, ev)
                    size, colour = self.ui_style(d, el.style)
                    size = (size or h * 0.8) * scale
                    colour = list(colour or [1, 1, 1, 1])
                    if el.tint:
                        colour = [a * b for a, b in zip(colour, el.tint)]
                    colour[3] *= op
                    if text:
                        imgui.push_font(None, max(4.0, size * s))
                        tw = imgui.calc_text_size(text).x / s
                        imgui.pop_font()
                        ax, ay = U.anchor_frac(el.anchor)
                        left, top = x - ax * w * scale, y - ay * h * scale
                        tx = left + {1: (w * scale - tw) / 2, 2: w * scale - tw}.get(el.just, 0)
                        ty = top + (h * scale - size) / 2
                        dl.add_text(imgui.get_font(), max(4.0, size * s), P(tx, ty), _u32(colour), text)
                elif el.kind == 'Shape':
                    c = el.colour or [1, 1, 1, 1]
                    q = [P(*p) for p in pts]
                    dl.add_quad_filled(q[0], q[1], q[2], q[3], _u32((c[0], c[1], c[2], c[3] * op)))
                else:
                    self.ui_outline(dl, P, pts, (0.9, 0.8, 0.3, 0.5))
                    dl.add_text(P(*pts[0]), _u32((0.9, 0.8, 0.3, 0.8)), el.kind)
                if st['outlines']:
                    self.ui_outline(dl, P, pts, (1, 1, 1, 0.25))
            for _ in range(clips):
                dl.pop_clip_rect()
        # selection: outline and the resize handle (bottom-right corner)
        cur = None
        for r, el, pts in shapes:
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

    @staticmethod
    def ui_outline(dl, P, pts, colour, thick=1.0):
        q = [P(*p) for p in pts]
        dl.add_quad(q[0], q[1], q[2], q[3], _u32(colour), thick)

    def ui_mouse(self, d, st, shapes, cur, o, s, hovered):
        io = imgui.get_io()
        mx, my = (io.mouse_pos.x - o.x) / s, (io.mouse_pos.y - o.y) / s
        drag = st['drag']
        if drag is not None:
            if imgui.is_mouse_down(0):
                ddx, ddy = mx - drag['mx'], my - drag['my']
                if drag['mode'] == 'move':
                    drag['x'], drag['y'] = round(drag['x0'] + ddx), round(drag['y0'] + ddy)
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
                if U.inside(pts, mx, my) and (el.kind != 'Mask' or st['show_masks']):
                    hit = (r, el, pts)
                    break
            if hit is None:
                st['sel'] = None
                return
            st['sel'] = (hit[0].id, hit[1].index)
            if not d.b.truncated:
                st['drag'] = self.ui_drag(hit, 'move', mx, my)
        if cur is not None and imgui.is_window_focused(imgui.FocusedFlags_.root_and_child_windows) \
                and not imgui.get_io().want_text_input and not d.b.truncated:
            step = 10 if io.key_shift else 1
            for k, (dx, dy) in ((imgui.Key.left_arrow, (-step, 0)), (imgui.Key.right_arrow, (step, 0)),
                                (imgui.Key.up_arrow, (0, -step)), (imgui.Key.down_arrow, (0, step))):
                if imgui.is_key_pressed(k):
                    r, el = cur[0], cur[1]
                    self.ui_set(d, r, el.node, {U.H_X: el.x + dx, U.H_Y: el.y + dy}, 'nudge element')
                    break

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

    def ui_set(self, d, r, node, values, desc=None):
        """Write scalar fields of a node in place: {field hash: value}."""
        for h, v in values.items():
            loc = node.locs.get(h)
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
        loc = node.locs.get(h)
        if loc is not None:
            self.write_string(d, r, loc[0], text)

    def ui_restructure(self, d, r, index, mutate, desc):
        """Change the layout's node tree (elements / behaviours) and write the object again."""
        if not self.genesys_rewritable(d, r):
            self.status = 'This layout cannot be re-written exactly, so elements and behaviours cannot be added here.'
            return False
        root = genesys.Reader(self.types, d.b.e).read_resource(r)
        els = root.fields.get(U.H_ELEMENTS)
        return self.genesys_rewrite(d, r, root, lambda: mutate(root, els), desc)

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
            imgui.text('Play:')
            for ph, label in (('in', 'Transition in'), ('out', 'Transition out'), ('idle', 'Idle')):
                imgui.same_line()
                if imgui.small_button(label):
                    st['play'] = (ph, time.time())
            if st['play'] is not None:
                imgui.same_line()
                if imgui.small_button('Stop'):
                    st['play'] = None
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
            imgui.begin_child('##uiels', imgui.ImVec2(0, 150))
            for r, _n, els in layouts:
                if len(layouts) > 1:
                    imgui.text_disabled(f'layout {r.id:#x}')
                for el in els:
                    shown = ev.visible(el.vis, st['unknown_visible'])
                    label = el.label + ('' if shown else '  (hidden)')
                    if imgui.selectable(f'{label}##{r.id}.{el.index}', st['sel'] == (r.id, el.index))[0]:
                        st['sel'] = (r.id, el.index)
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
        if ro:
            imgui.text_disabled('(read-only bundle)')
            return
        self.ui_props(d, r, el, node)
        self.ui_behaviours(d, r, el, node)
        self.ui_element_ops(d, r, el)

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
            if h in node.locs:
                self.ui_text_field(d, r, node, h, label, node.fields.get(h) or '')
        txt = node.fields.get(U.H_TEXT)
        if isinstance(txt, Node):
            self.ui_text_field(d, r, txt, U.H_BINDING, 'Text binding', txt.fields.get(U.H_BINDING) or '')
            if U.H_STRING_ID in txt.locs:
                imgui.set_next_item_width(200)
                ch, sid = imgui.input_int('String id', int(el.string_id or 0), 0)
                if ch:
                    self.ui_set(d, r, txt, {U.H_STRING_ID: max(0, sid)})
                s = ops.game_strings(d.path).get(el.string_id) if el.string_id else None
                if s:
                    imgui.text_disabled(f'"{s[:60]}"')
        if el.texture is not None:
            imgui.text_disabled(f'texture {el.texture:#x}, quad {el.quad or 0:#x}')

    def ui_text_field(self, d, r, node, h, label, value):
        key = f'##uitf{label}{id(node)}'
        imgui.set_next_item_width(200)
        ch, v = imgui.input_text(f'{label}{key}', value, imgui.InputTextFlags_.enter_returns_true)
        if ch and v != value:
            self.ui_set_text(d, r, node, h, v)

    def ui_behaviours(self, d, r, el, node):
        if not imgui.collapsing_header('Transitions', imgui.TreeNodeFlags_.default_open):
            return
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
        if imgui.small_button('+ Fade in'):
            self.ui_add_behaviour(d, r, el, U.H_IN, 'IN', 'fade in')
        imgui.same_line()
        if imgui.small_button('+ Fade out'):
            self.ui_add_behaviour(d, r, el, U.H_OUT, 'OUT', 'fade out')
        if imgui.is_item_hovered():
            imgui.set_tooltip('Retail fades: Type Fade, parameters IN / OUT, delay and duration in ms')

    def ui_add_behaviour(self, d, r, el, h, params, desc):
        def mutate(root, els):
            n = els[el.index]
            b = self.ui_new_behaviour(n, h, params, 300.0)
            cur = n.fields.get(h)
            n.fields[h] = (cur if isinstance(cur, list) else []) + [b]
        self.ui_restructure(d, r, el.index, mutate, f'add a {desc} to element {el.index}')

    def ui_element_ops(self, d, r, el):
        if not imgui.collapsing_header('Element', imgui.TreeNodeFlags_.default_open):
            return
        i = el.index
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
