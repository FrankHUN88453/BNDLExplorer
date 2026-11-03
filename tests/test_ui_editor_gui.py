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
from imgui_bundle import hello_imgui, immvision  # noqa: E402

from bndlx import app as A, genesys, roots, uiscene as U  # noqa: E402

PC = os.environ['BNDLX_PC']
SHOT = os.environ.get('BNDLX_SHOT')
SHOT2 = os.environ.get('BNDLX_SHOT2')       # the panel with the new text selected
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
            state['alpha'] = (U.animate(back, 'in', 0)[0], U.animate(back, 'in', 1000)[0])
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
        if f == 99:
            for _ in range(40):
                if bytes(d.b.find(state['lid']).data(0)) == state['old'] or not d.undo:
                    break
                d.do_undo()
                app.changed(d, d.b.find(state['lid']))
            state['undo'] = bytes(d.b.find(state['lid']).data(0)) == state['old']
        if f == 115:
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
    check(state.get('moved'), 'an element moved / resized in place reads back')
    check(state.get('fade') and state.get('alpha') == (0.0, 1.0),
          f'a fade in added to the element reads back and animates (alpha {state.get("alpha")})')
    check(state.get('dup'), 'a duplicated element appears with a new id')
    check(state.get('added'), 'new image and text elements are added on top at the centre with new ids')
    check(state.get('style'), 'the text gets its own style copy; its size and font change, the shared one stays')
    check(state.get('text'), 'the new text shows the game string given by its id')
    check(state.get('material'), 'an image gets another material')
    check(state.get('undo'), 'undo restores the layout bytes')
    shutil.rmtree(TMP, ignore_errors=True)
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
