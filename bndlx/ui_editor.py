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
import re
import struct
import time

import numpy as np

from imgui_bundle import imgui, immvision

from . import filedialog, genesys, gjson, mesh, ops, roots, uirender, uiscene as U
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
                'add_kind': 0}
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
        imgui.text_disabled('(or edit here)')
        return False

    def ui_editor_window(self):
        """The editor in its own window over the app (called every frame)."""
        win = getattr(self, 'uied', {}).get('window')
        if not win:
            return
        kind, uid, rid = win['key']
        d = self.doc_by_uid(uid)
        r = d.b.find(rid) if d is not None else None
        node = self.ui_read(d, r) if r is not None else None
        if node is None:
            self.uied['window'] = None
            return
        vp = imgui.get_main_viewport()
        imgui.set_next_window_pos(imgui.ImVec2(vp.work_pos.x + 24, vp.work_pos.y + 24), imgui.Cond_.first_use_ever)
        imgui.set_next_window_size(imgui.ImVec2(vp.work_size.x - 48, vp.work_size.y - 48), imgui.Cond_.first_use_ever)
        if win.get('focus'):
            imgui.set_next_window_focus()
            win['focus'] = False
        title = f'UI editor - {self.display_name(d, r)[0]}  ({rid:#x})###uieditor'
        _, keep = imgui.begin(title, True, imgui.WindowFlags_.no_collapse | imgui.WindowFlags_.no_scrollbar)
        if not keep or (imgui.is_window_focused(imgui.FocusedFlags_.root_and_child_windows)
                        and imgui.is_key_pressed(imgui.Key.escape)):
            imgui.end()
            self.uied['window'] = None
            return
        if kind == 'layout':
            self.ui_layout_preview(d, r, node, window=True)
        else:
            self.ui_screen_preview(d, r, node, window=True)
        imgui.end()

    def ui_screen_preview(self, d, r, node, window=False):
        if not window and self.ui_in_window(('screen', d.uid, r.id)):
            return
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
        self.ui_editor(d, items, ('screen', d.uid, r.id), window)

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
    def ui_editor(self, d, items, key, window=False):
        st = self.uied_state(key)
        ev = U.Evaluator(st['state'])
        layouts = [(r, n, U.elements(self.types, n)) for r, n in items]
        avail = imgui.get_content_region_avail()
        panel = 420 if window else 360
        cw = max(320.0, avail.x - panel - 12)
        ch = cw * U.H / U.W
        if ch > avail.y - 8:
            ch = max(180.0, avail.y - 8)
            cw = ch * U.W / U.H
        imgui.begin_child('##uicanvas', imgui.ImVec2(cw, ch))
        self.ui_canvas(d, st, layouts, ev, cw, ch)
        imgui.end_child()
        imgui.same_line()
        imgui.begin_child('##uipanel', imgui.ImVec2(0, max(ch, avail.y - 4)))
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
        now = time.time()
        play = st['play']
        if play is not None:
            t = (now - play[1]) * 1000.0
            longest = max((U.phase_length(el, play[0]) for _, _, els in layouts for el in els), default=0.0)
            if play[0] != 'idle' and t > longest + 400:
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
        for r, _n, els in layouts:
            depth = 0
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
                    tint = el.tint or [1, 1, 1, 1]
                    faded = 0.45 if mode == 'bound' and st['bound_faded'] else 1.0
                    ops.append(('quad', tex[0].texture_id, pts, uv, (tint[0], tint[1], tint[2], tint[3] * op * faded),
                                'add' if mode == 'additive' else 'alpha'))
                    if mode == 'bound' and st['bound_faded']:
                        overlays.append((pts, (0.3, 0.8, 1.0, 0.45), None))
                elif el.kind in ('Label', 'BackedLabel', 'ScrollingText', 'ScrollableLabel'):
                    text = self.ui_text(d, el, ev)
                    sty = self.ui_style(d, el.style)
                    size, colour = sty['size'], sty['colour']
                    size = (size or h * 0.8) * scale
                    colour = list(colour or [1, 1, 1, 1])
                    if el.tint:
                        colour = [a * b for a, b in zip(colour, el.tint)]
                    colour[3] *= op
                    if text:
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
        return ops, shapes, overlays

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
            if isinstance(node.fields.get(U.H_PROPS), Node) and tab('Text'):
                self.ui_text_style(d, r, el, node)
                imgui.end_tab_item()
            if tab('Transitions'):
                self.ui_behaviours(d, r, el, node)
                imgui.end_tab_item()
            if tab('All properties'):
                self.ui_all_props(d, r, el)
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
        txt = node.fields.get(U.H_TEXT)
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
        imgui.text_disabled('(the preview draws Arial in the style\'s size, colour, spacing and slant)')

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
        imgui.separator()
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
