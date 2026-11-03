"""The PS3 prototype HUD in the UI editor, on copies of FREEDRIVEHUD.BNDL (PS3) and UI/SCREENS2/371621.BNDL (PC):
its widget objects and GameChanger-id layouts are followed, the preview draws it (PS3 textures), an element moved
is written into its base struct, an element pasted into the PC HUD is converted (merged base, PC types) with its
texture converted and its material mapped to the PC bundle's; undo restores both. BNDLX_SHOT=<file.png> saves
the window. Needs BNDLX_PC and BNDLX_PS3 (see test_core.py)."""
import os
import shutil
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import numpy as np  # noqa: E402
from imgui_bundle import hello_imgui, immvision  # noqa: E402

from bndlx import app as A, genesys, raster, roots, uiscene as U  # noqa: E402

PC = os.environ['BNDLX_PC']
PS3 = os.environ['BNDLX_PS3']
SHOT = os.environ.get('BNDLX_SHOT')
TMP = tempfile.mkdtemp(prefix='bndlx_uips3_')
SCREEN = 0x10000000005aba5
failures = []


def check(cond, what):
    print(('ok    ' if cond else 'FAIL  ') + what)
    if not cond:
        failures.append(what)


def screenshot(path):
    from OpenGL import GL
    from PIL import Image
    vp = GL.glGetIntegerv(GL.GL_VIEWPORT)
    w, h = int(vp[2]), int(vp[3])
    px = GL.glReadPixels(0, 0, w, h, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
    Image.fromarray(np.frombuffer(px, np.uint8).reshape(h, w, 4)[::-1]).save(path)


def main():
    ps3 = os.path.join(TMP, 'FREEDRIVEHUD.BNDL')
    pc = os.path.join(TMP, '371621.BNDL')
    shutil.copyfile(os.path.join(PS3, 'UI', 'SCREENS2', 'FREEDRIVEHUD.BNDL'), ps3)
    shutil.copyfile(os.path.join(PC, 'UI', 'SCREENS2', '371621.BNDL'), pc)
    roots.set_fallbacks([PC, PS3])
    immvision.use_rgb_color_order()
    app = A.App([ps3, pc])
    app.pinned = lambda: [PC, PS3]
    params = A.runner_params(app, 'BNDL Explorer PS3 UI test')
    params.app_window_params.window_geometry.size = (1600, 900)
    state = {'f': 0, 'errors': []}

    def els(d, lid):
        return U.elements(app.types, genesys.Reader(app.types, d.b.e).read_resource(d.b.find(lid)))

    def step():
        f = state['f']
        if len(app.docs) < 2:
            return
        dp, dc = app.docs[0], app.docs[1]
        if f == 10:
            app.goto(SCREEN, dp)
            app.uied_state(('screen', dp.uid, SCREEN))
            app.uied['window'] = {'key': ('screen', dp.uid, SCREEN), 'focus': True}
        if f == 45:
            st = app.uied['views'][('screen', dp.uid, SCREEN)]
            lids = list(st.get('params') or {})
            state['ps3_layouts'] = len(lids)
            state['ps3_tex'] = len(app.uied['tex'])
            lid = lids[0] if lids else None
            state['lid'] = lid
            state['old_ps3'] = bytes(dp.b.find(lid).data(0))
            el = next(e for e in els(dp, lid) if e.kind == 'Image' and e.texture)
            app.ui_set(dp, dp.b.find(lid), el.node, {U.H_X: el.x + 12, U.H_Y: el.y - 4})
            back = next(e for e in els(dp, lid) if e.index == el.index)
            state['ps3_move'] = (back.x, back.y) == (el.x + 12, el.y - 4) and el.base is not None
            state['shot'] = f + 4
        if f == 55:                                       # the prototype element into the PC HUD
            st = app.uied['views'][('screen', dp.uid, SCREEN)]
            cands = [(lid, e) for lid in st['params'] for e in els(dp, lid) if e.kind == 'Image' and e.texture
                     and not e.image_bind]
            lid, src = next(((l_, e) for l_, e in cands if dc.b.find(e.texture) is None), cands[0])
            state['tex_new'] = dc.b.find(src.texture) is None
            app.ui_copy(dp, dp.b.find(lid), src)
            root = genesys.Reader(app.types, dc.b.e).read_resource(dc.b.find(SCREEN))
            lc = U.screen_widgets(app.types, root)[0][1][0][0]
            state['lc'], state['old_pc'] = lc, bytes(dc.b.find(lc).data(0))
            state['n_pc'] = len(dc.b.resources)
            idx = app.ui_paste(dc, dc.b.find(lc))
            got = els(dc, lc)[idx] if idx is not None else None
            tex = dc.b.find(got.texture) if got is not None and got.texture else None
            inf = raster.info(tex, 'PC') if tex is not None else None
            state['paste'] = (got is not None and got.kind == 'Image' and (got.x, got.y, got.w, got.h) ==
                              (src.x, src.y, src.w, src.h) and tex is not None and inf is not None
                              and got.material is not None and dc.b.find(got.material) is not None
                              and got.quad is not None and got.base is None)
            state['paste_info'] = (src.gc, 'texture converted' if state['tex_new'] else 'texture already there',
                                   inf.describe() if inf else None)
        if f == 65:
            for d, rid, old in ((dp, state['lid'], state['old_ps3']), (dc, state['lc'], state['old_pc'])):
                for _ in range(20):
                    if bytes(d.b.find(rid).data(0)) == old or not d.undo:
                        break
                    d.do_undo()
                    app.changed(d)
            state['undo'] = (bytes(dp.b.find(state['lid']).data(0)) == state['old_ps3']
                             and bytes(dc.b.find(state['lc']).data(0)) == state['old_pc']
                             and len(dc.b.resources) == state['n_pc'])
        if f == 75:
            hello_imgui.get_runner_params().app_shall_exit = True
            app.exit_ok = True

    def gui():
        try:
            app.gui()
        except Exception:
            state['errors'].append((state['f'], traceback.format_exc()))
            if len(state['errors']) == 1:
                print(state['errors'][0][1], flush=True)
        state['f'] += 1
        try:
            step()
        except Exception:
            state['errors'].append((state['f'], traceback.format_exc()))
            if len(state['errors']) == 1:
                print(state['errors'][0][1], flush=True)
        if state['f'] > 2000:
            hello_imgui.get_runner_params().app_shall_exit = True
            app.exit_ok = True

    params.callbacks.show_gui = gui
    prev = params.callbacks.before_swap

    def before_swap():
        if prev:
            prev()
        if SHOT and state.get('shot') and state['f'] >= state['shot']:
            state['shot'] = None
            screenshot(SHOT)

    params.callbacks.before_swap = before_swap
    hello_imgui.run(params)
    check(not state['errors'], f'no frame raised ({len(state["errors"])} errors)')
    check(state.get('ps3_layouts', 0) >= 5 and state.get('ps3_tex', 0) > 5,
          f'the prototype HUD shows {state.get("ps3_layouts")} layouts with {state.get("ps3_tex")} textures')
    check(state.get('ps3_move'), 'a prototype element moved: written into its base struct')
    check(state.get('paste'), f'a prototype element pasted into the PC HUD, converted: {state.get("paste_info")}')
    check(state.get('undo'), 'undo restores both bundles')
    shutil.rmtree(TMP, ignore_errors=True)
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
