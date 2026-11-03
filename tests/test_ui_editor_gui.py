"""UI editor in the real window, on a copy of UI/SCREENS2/371621.BNDL (free-roam HUD): the screen's Preview tab
draws the HUD widget's layouts (textures and quads found), an element moved and resized is written, a fade in
added to an element reads back and plays, a duplicated element appears, undo restores the bytes.
BNDLX_SHOT=<file.png> saves the window once the preview is drawn. Needs BNDLX_PC (see test_core.py)."""
import os
import shutil
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import numpy as np  # noqa: E402
from imgui_bundle import hello_imgui, imgui, immvision  # noqa: E402

from bndlx import app as A, genesys, roots, uiscene as U  # noqa: E402

PC = os.environ['BNDLX_PC']
SHOT = os.environ.get('BNDLX_SHOT')
SHOT2 = os.environ.get('BNDLX_SHOT2')       # the panel with the new text selected
SHOT3 = os.environ.get('BNDLX_SHOT3')       # the screen list of the game
SHOT4 = os.environ.get('BNDLX_SHOT4')       # free transform (Ctrl+T) on the tacho
TMP = tempfile.mkdtemp(prefix='bndlx_uied_')
SCREEN = 0x10000000005aba5
failures = []


def check(cond, what):
    print(('ok    ' if cond else 'FAIL  ') + what)
    if not cond:
        failures.append(what)


def screenshot(path):
    from OpenGL import GL
    vp = GL.glGetIntegerv(GL.GL_VIEWPORT)
    w, h = int(vp[2]), int(vp[3])
    px = GL.glReadPixels(0, 0, w, h, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
    img = np.frombuffer(px, np.uint8).reshape(h, w, 4)[::-1]
    from PIL import Image
    Image.fromarray(img).save(path)


def main():
    path = os.path.join(TMP, '371621.BNDL')
    shutil.copyfile(os.path.join(PC, 'UI', 'SCREENS2', '371621.BNDL'), path)
    roots.set_fallbacks([PC])
    immvision.use_rgb_color_order()
    app = A.App([path])
    app.pinned = lambda: [PC]
    params = A.runner_params(app, 'BNDL Explorer UI editor test')
    params.app_window_params.window_geometry.size = (1600, 900)
    state = {'f': 0, 'errors': []}

    def view():
        return app.uied['views'].get(('screen', app.docs[0].uid, SCREEN)) if hasattr(app, 'uied') else None

    def layout_of(d, rid):
        n = genesys.Reader(app.types, d.b.e).read_resource(d.b.find(rid))
        return U.elements(app.types, n)

    def step():
        f = state['f']
        if not app.docs:
            return
        d = app.docs[0]
        if f == 10:
            app.goto(SCREEN, d)
        if f == 30:                          # the large editor window
            app.uied_state(('screen', d.uid, SCREEN))
            app.uied['window'] = {'key': ('screen', d.uid, SCREEN), 'focus': True}
        if f == 58:                          # how images draw: game pictures, additive glows, the mirror
            modes = {}
            for res in d.b.resources:
                if res.type != 0x15:
                    continue
                n = app.ui_read(d, res)
                if n is None or app.types.name(n.type) != 'UILayout':
                    continue
                for el in U.elements(app.types, n):
                    if el.kind == 'Image' or el.texture is not None:
                        modes.setdefault(app.ui_mode(d, el), []).append(el)
            state['modes'] = {k: len(v) for k, v in modes.items()}
            state['mirror_ok'] = all(el.image_bind == 'Graphics.RearViewMirror' or 'Mirror' in
                                     app.ui_shader(d, el.material) for el in modes.get('mirror', []))
            state['bound_ok'] = all(el.image_bind for el in modes.get('bound', []))
            state['additive_ok'] = all('Additive' in app.ui_shader(d, el.material) for el in modes.get('additive', []))
        if f == 60:
            st = view()
            state['drawn'] = st is not None
            state['textures'] = len(app.uied['tex']) if st is not None else 0
            state['quads'] = sum(1 for v in app.uied['quads'].values() if v) if st is not None else 0
            if SHOT:
                state['shot'] = True
        if f == 66:                          # the HUD's texts use the game's fonts; a font with ő ű made from a TTF
            fonts = app.uied.get('gfonts', {})
            state['game_fonts'] = sorted(k for k, v in fonts.items() if v is not None)
            ttf = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', 'arialbd.ttf')
            out = os.path.join(TMP, '2113929999.BNDL')
            ok, msg, fid = app.ui_make_font(d, 1206239, ttf, out, keep=True)
            from bndlx import uifont
            from bndlx.bundle import Bundle
            nf = uifont.from_bundle(Bundle.open(out))[0] if ok else None
            state['font_made'] = (ok and fid == 2113929999 and nf is not None
                                  and all(uifont.drawn(nf.glyph(c)) for c in 'őűŐŰAa0'))
            state['font_msg'] = msg
        if f == 70:                          # move + resize the first image of the first HUD layout
            st = view()
            widgets = U.screen_widgets(app.types, genesys.Reader(app.types, d.b.e).read_resource(d.b.find(SCREEN)))
            lid = widgets[0][1][0][0]
            r = d.b.find(lid)
            state['lid'], state['old'] = lid, bytes(r.data(0))
            el = next(e for e in layout_of(d, lid) if e.kind == 'Image')
            state['idx'] = el.index
            st['sel'] = (lid, el.index)
            app.ui_set(d, r, el.node, {U.H_X: el.x + 33, U.H_Y: el.y - 7, U.H_W: el.w + 10})
            back = next(e for e in layout_of(d, lid) if e.index == el.index)
            state['moved'] = (back.x, back.y, back.w) == (el.x + 33, el.y - 7, el.w + 10)
        if f == 75:                          # fade in on that element, then play it
            r = d.b.find(state['lid'])
            el = next(e for e in layout_of(d, state['lid']) if e.index == state['idx'])
            n_in = len(el.behaviours['in'])
            app.ui_add_behaviour(d, r, el, U.H_IN, 'IN', 'fade in')
            back = next(e for e in layout_of(d, state['lid']) if e.index == state['idx'])
            b = back.behaviours['in'][-1] if back.behaviours['in'] else None
            state['fade'] = (len(back.behaviours['in']) == n_in + 1 and b is not None
                             and U.behaviour(b)['params'] == 'IN' and U.behaviour(b)['duration'] == 300.0)
            state['alpha'] = (U.animate(back, 'in', 0).alpha, U.animate(back, 'in', 1000).alpha)
            view()['play'] = ('in', __import__('time').time())
        if f == 85:                          # duplicate
            r = d.b.find(state['lid'])
            n0 = len(layout_of(d, state['lid']))
            el = next(e for e in layout_of(d, state['lid']) if e.index == state['idx'])
            from bndlx import gjson

            def dup(root, els):
                c = gjson.clone(els[el.index])
                c.fields[U.H_GC] = app.ui_new_gc(els)
                els.insert(el.index + 1, c)
            app.ui_restructure(d, r, el.index, dup, 'duplicate')
            els = layout_of(d, state['lid'])
            state['dup'] = len(els) == n0 + 1 and els[el.index + 1].gc >= 0x7E000001
        if f == 88:                          # new elements: an image and a text, on top, at the centre
            r = d.b.find(state['lid'])
            n0 = len(layout_of(d, state['lid']))
            i1 = app.ui_add_element(d, r, 'Image')
            i2 = app.ui_add_element(d, d.b.find(state['lid']), 'Label')
            els = layout_of(d, state['lid'])
            state['added'] = (len(els) == n0 + 2 and i1 is not None and i2 is not None
                              and els[i1].kind == 'Image' and els[i2].kind == 'Label'
                              and (els[i1].x, els[i1].y) == (640.0, 360.0) and els[i2].gc > els[i1].gc >= 0x7E000001
                              and not els[i1].behaviours['in'] and not els[i1].vis)
            view()['sel'] = (state['lid'], i2)          # the panel draws the text style / all properties
        if f == 91:                          # own style for the new text, a bigger size and another font
            r = d.b.find(state['lid'])
            els = layout_of(d, state['lid'])
            lab = els[-1]
            old_style = lab.style
            app.ui_fork_style(d, r, lab)
            lab = layout_of(d, state['lid'])[-1]
            sr = d.b.find(lab.style)
            sn = genesys.Reader(app.types, d.b.e).read_resource(sr) if sr is not None else None
            if sn is not None:
                from bndlx.gnames import name_hash
                app.ui_set(d, sr, sn, {U.H_FONTSIZE: 33.0, name_hash('Font_Asset'): 1206238})
                sn = genesys.Reader(app.types, d.b.e).read_resource(d.b.find(lab.style))
            state['style'] = (sn is not None and lab.style != old_style and d.b.find(old_style) is not None
                              and sn.fields.get(U.H_FONTSIZE) == 33.0
                              and sn.fields.get(name_hash('Font_Asset')) == 1206238
                              and sn.fields.get(U.H_GC) == lab.style & 0xFFFFFFFF)
        if f == 91:
            view()['tab_request'] = 'Text'
        if f == 92 and SHOT2:
            state['shot2'] = True
        if f == 93:                          # the new text shows a game string chosen by id
            from bndlx import ops
            strings = app.ui_strings(d)
            sid = next((k for k, v in strings.items() if v.strip().upper() == 'HEAT'), None) or next(iter(strings))
            lab = layout_of(d, state['lid'])[-1]
            app.ui_set(d, d.b.find(state['lid']), lab.node.fields[U.H_TEXT], {U.H_STRING_ID: sid})
            lab = layout_of(d, state['lid'])[-1]
            state['text'] = lab.string_id == sid and app.ui_text(d, lab, U.Evaluator()) == strings[sid]
        if f == 94:                          # another material for the first image
            r = d.b.find(state['lid'])
            mats = app.ui_materials(d)
            el = next(e for e in layout_of(d, state['lid']) if e.index == state['idx'])
            other = next((m[0] for m in mats if m[0] != el.material), None)
            if other is not None:
                def mutate(root, els):
                    els[el.index].fields[U.H_MATERIAL] = genesys.Ref(other)
                app.ui_restructure(d, r, el.index, mutate, 'material')
            back = next(e for e in layout_of(d, state['lid']) if e.index == state['idx'])
            state['material'] = other is not None and back.material == other
        if f == 96:                          # Ctrl+T: scale from the corner (Shift), rotate, apply (one undo step)
            st = view()
            r = d.b.find(state['lid'])
            els = layout_of(d, state['lid'])
            lays = [(r, None, els)]
            el = next(e for e in els if e.index == state['idx'])
            st['sel'] = (state['lid'], el.index)
            n_undo = len(d.undo)
            app.ui_command(d, st, lays, 'transform')
            xf = st['xf']
            corner = U.corners(el.x, el.y, el.w, el.h, el.anchor, el.rot)[2]
            app.ui_xf_apply_drag(xf, {'handle': 'br', 'mx': corner[0], 'my': corner[1],
                                      'start': (xf['x'], xf['y'], xf['w'], xf['h'], xf['rot'])},
                                 corner[0] + 40, corner[1] + 5, shift=True)
            app.ui_xf_apply_drag(xf, {'handle': 'rot', 'mx': xf['x'], 'my': xf['y'] - 50,
                                      'start': (xf['x'], xf['y'], xf['w'], xf['h'], xf['rot'])},
                                 xf['x'] + 50, xf['y'], shift=True)
            want = (xf['x'], xf['y'], xf['w'], xf['h'], xf['rot'])
            app.ui_command(d, st, lays, 'apply')
            back = next(e for e in layout_of(d, state['lid']) if e.index == el.index)
            state['xf'] = ((back.x, back.y, back.w, back.h, back.rot) == want and st['xf'] is None
                           and len(d.undo) == n_undo + 1 and abs(back.w / max(el.w, 1) - back.h / max(el.h, 1)) < 0.05
                           and back.rot == el.rot + 90)
            app.ui_command(d, st, lays, 'undo')
            back = next(e for e in layout_of(d, state['lid']) if e.index == el.index)
            state['xf_undo'] = (back.x, back.y, back.w, back.h, back.rot) == (el.x, el.y, el.w, el.h, el.rot)
        if f == 97:                          # Ctrl+J, Delete, Ctrl+] / Ctrl+[
            st = view()
            r = d.b.find(state['lid'])
            n0 = len(layout_of(d, state['lid']))
            lays = lambda: [(d.b.find(state['lid']), None, layout_of(d, state['lid']))]  # noqa: E731
            st['sel'] = (state['lid'], state['idx'])
            app.ui_command(d, st, lays(), 'duplicate')
            n1 = len(layout_of(d, state['lid']))
            sel1 = st['sel']
            app.ui_command(d, st, lays(), 'up')
            sel2 = st['sel']
            app.ui_command(d, st, lays(), 'down')
            app.ui_command(d, st, lays(), 'delete')
            n2 = len(layout_of(d, state['lid']))
            state['cmds'] = (n1 == n0 + 1 and sel1 == (state['lid'], state['idx'] + 1)
                             and sel2 == (state['lid'], state['idx'] + 2) and n2 == n0 and st['sel'] is None)
        if f == 98:                          # Delete goes to the editor while it has the keyboard, not the list
            d.sel = {d.b.resources[0].id}
            orig = A.imgui.is_key_pressed
            A.imgui.is_key_pressed = lambda key, repeat=True: key == imgui.Key.delete
            try:
                app.modal = None
                app.uied['focus_frame'] = imgui.get_frame_count()
                app.shortcuts()
                routed = app.modal is None
                app.uied['focus_frame'] = -9
                app.shortcuts()
                listed = app.modal is not None and app.modal.get('kind') == 'confirm_delete'
            finally:
                A.imgui.is_key_pressed = orig
                app.modal = None
                d.sel = set()
            state['keys'] = routed and listed
        if f == 99:
            for _ in range(60):
                if bytes(d.b.find(state['lid']).data(0)) == state['old'] or not d.undo:
                    break
                d.do_undo()
                app.changed(d, d.b.find(state['lid']))
            state['undo'] = bytes(d.b.find(state['lid']).data(0)) == state['old']
        if f == 102 and SHOT4:               # a transform in progress, for the picture (cancelled after)
            st = view()
            lid = next(l for l, _p in U.screen_widgets(app.types, genesys.Reader(app.types, d.b.e).read_resource(
                d.b.find(SCREEN)))[0][1] if any(e.w > 150 and e.kind == 'Image' for e in layout_of(d, l)))
            el = max((e for e in layout_of(d, lid) if e.kind == 'Image'), key=lambda e: e.w * e.h)
            st['sel'] = (lid, el.index)
            lays = [(d.b.find(lid), None, layout_of(d, lid))]
            app.ui_command(d, st, lays, 'transform')
            xf = st['xf']
            app.ui_xf_apply_drag(xf, {'handle': 'rot', 'mx': xf['x'], 'my': xf['y'] - 50,
                                      'start': (xf['x'], xf['y'], xf['w'], xf['h'], xf['rot'])}, xf['x'] + 20, xf['y'] - 50)
            cx, cy = U.corners(xf['x'], xf['y'], xf['w'], xf['h'], xf['anchor'], xf['rot'])[2]
            app.ui_xf_apply_drag(xf, {'handle': 'br', 'mx': cx, 'my': cy, 'start': (xf['x'], xf['y'], xf['w'], xf['h'],
                                                                                    xf['rot'])}, cx + 30, cy + 20,
                                 shift=True)
            state['shot4'] = f + 3
            state['xf_lays'] = lays
        if f == 107 and SHOT4:
            app.ui_command(d, view(), state['xf_lays'], 'cancel')
        if f == 110:                         # every screen of the game folder, HUDs first; one picked opens
            app.open_ui_browser()
        if f > 110 and 'picked' not in state and app.job is None:
            br = app.uied['browser']
            scr = br['screens'] or []
            state['screens'] = (len(scr), sum(1 for x in scr if x['hud']))
            pick = next((x for x in scr if x['id'] == 371621 and os.path.basename(x['path']) == '371621.BNDL'), None)
            if pick is None:
                state['picked'] = None
                state['end'] = f + 5
            else:
                br['pending'] = pick
                state['picked'] = pick
        if state.get('picked') and 'opened' not in state and f > 112:
            key = app.uied['window'].get('key')
            od = app.doc_by_uid(key[1]) if key else None
            state['opened'] = (od is not None and key[2] == state['picked']['root']
                               and os.path.normcase(od.path) == os.path.normcase(state['picked']['path']))
            state['end'] = f + 10
            state['shot3'] = f + 6
        if f == state.get('end') or f > 1500:
            hello_imgui.get_runner_params().app_shall_exit = True
            app.exit_ok = True

    def gui():
        try:
            app.gui()
        except Exception:
            state['errors'].append((state['f'], traceback.format_exc()))
        state['f'] += 1
        try:
            step()
        except Exception:
            state['errors'].append((state['f'], traceback.format_exc()))
        if state['f'] > 2000:
            hello_imgui.get_runner_params().app_shall_exit = True
            app.exit_ok = True

    params.callbacks.show_gui = gui
    prev_swap = params.callbacks.before_swap

    def before_swap():                       # the frame is drawn: the back buffer holds the window
        if prev_swap:
            prev_swap()
        if SHOT and state['f'] >= 62 and state.pop('shot', False):
            screenshot(SHOT)
        if SHOT2 and state['f'] >= 93 and state.pop('shot2', False):
            screenshot(SHOT2)
        if SHOT4 and state.get('shot4') and state['f'] >= state['shot4']:
            state['shot4'] = None
            screenshot(SHOT4)
        if SHOT3 and state.get('shot3') and state['f'] >= state['shot3']:
            state['shot3'] = None
            screenshot(SHOT3)

    params.callbacks.before_swap = before_swap
    hello_imgui.run(params)
    for f, tb in state['errors'][:3]:
        print(f'frame {f}:')
        print(tb)
    check(not state['errors'], f'no frame raised ({len(state["errors"])} errors)')
    check(state.get('drawn') and state.get('textures', 0) > 5 and state.get('quads', 0) > 5,
          f'the screen preview draws the HUD ({state.get("textures")} textures, {state.get("quads")} quads)')
    m = state.get('modes', {})
    check(m.get('additive', 0) > 0 and m.get('bound', 0) > 0 and state.get('mirror_ok') and state.get('bound_ok')
          and state.get('additive_ok'), f'images draw by their kind: {m} (mirror: game camera stand-in; bound: '
                                        'game pictures; additive: UIAdditiveShader)')
    check(len(state.get('game_fonts', [])) >= 1, f'the HUD texts are drawn with the game fonts {state.get("game_fonts")}')
    check(state.get('font_made'), f'a font with ő ű made from a TTF reads back ({state.get("font_msg", "")[:90]})')
    check(state.get('moved'), 'an element moved / resized in place reads back')
    check(state.get('fade') and state.get('alpha') == (0.0, 1.0),
          f'a fade in added to the element reads back and animates (alpha {state.get("alpha")})')
    check(state.get('dup'), 'a duplicated element appears with a new id')
    check(state.get('added'), 'new image and text elements are added on top at the centre with new ids')
    check(state.get('style'), 'the text gets its own style copy; its size and font change, the shared one stays')
    check(state.get('text'), 'the new text shows the game string given by its id')
    check(state.get('material'), 'an image gets another material')
    check(state.get('xf') and state.get('xf_undo'), 'Ctrl+T: proportional scale + 90 degrees applied as one undo step, '
                                                    'Ctrl+Z restores')
    check(state.get('cmds'), 'Ctrl+J duplicates, Ctrl+] / Ctrl+[ reorder, Delete deletes')
    check(state.get('keys'), 'while the editor has the keyboard, Delete does not delete resources of the list')
    check(state.get('undo'), 'undo restores the layout bytes')
    n, h = state.get('screens', (0, 0))
    check(n > 10 and h > 5 and state.get('opened'), f'all screens of the game listed ({n}, {h} HUDs); the free roam '
                                                    'HUD picked opens in the editor')
    shutil.rmtree(TMP, ignore_errors=True)
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
