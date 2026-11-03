"""UI editor clipboard, pictures and situations in the real window, on copies of two PC HUD screens
(UI/SCREENS2/371621.BNDL and 604153.BNDL): an element copied to another layout of the same screen, and to the
other screen (with the resources it needs); a PNG becomes an image element with its own texture; the Pursuit
situation shows the pursuit elements, live values move; undo restores everything. BNDLX_SHOT=<file.png> saves
the window with the results. Needs BNDLX_PC (see test_core.py)."""
import os
import shutil
import sys
import tempfile
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import numpy as np  # noqa: E402
from imgui_bundle import hello_imgui, immvision  # noqa: E402

from bndlx import app as A, genesys, raster, roots, uiscene as U  # noqa: E402

PC = os.environ['BNDLX_PC']
SHOT = os.environ.get('BNDLX_SHOT')
TMP = tempfile.mkdtemp(prefix='bndlx_uiclip_')
FREE, RACE = 0x10000000005aba5, None
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
    a = os.path.join(TMP, '371621.BNDL')
    b = os.path.join(TMP, '604153.BNDL')
    shutil.copyfile(os.path.join(PC, 'UI', 'SCREENS2', '371621.BNDL'), a)
    shutil.copyfile(os.path.join(PC, 'UI', 'SCREENS2', '604153.BNDL'), b)
    png = os.path.join(TMP, 'badge.png')
    from PIL import Image, ImageDraw
    im = Image.new('RGBA', (150, 70), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    dr.rounded_rectangle((2, 2, 147, 67), 14, fill=(30, 140, 255, 230), outline=(255, 255, 255, 255), width=4)
    dr.text((28, 24), 'BNDLX', fill=(255, 255, 255, 255))
    im.save(png)
    roots.set_fallbacks([PC])
    immvision.use_rgb_color_order()
    app = A.App([a, b])
    app.pinned = lambda: [PC]
    params = A.runner_params(app, 'BNDL Explorer UI clipboard test')
    params.app_window_params.window_geometry.size = (1600, 900)
    state = {'f': 0, 'errors': []}

    def layouts_of(d, root):
        n = genesys.Reader(app.types, d.b.e).read_resource(d.b.find(root))
        return [lid for _w, ls in U.screen_widgets(app.types, n)[:1] for lid, _p in ls if d.b.find(lid) is not None]

    def els(d, lid):
        return U.elements(app.types, genesys.Reader(app.types, d.b.e).read_resource(d.b.find(lid)))

    def root_of(d):
        return next(r.id for r in d.b.resources if r.type == 0x15 and r.imports()
                    and app.types.name(r.imports()[0].id) == 'Screen2')

    def step():
        f = state['f']
        if len(app.docs) < 2:
            return
        da, db = app.docs[0], app.docs[1]
        if f == 10:
            app.goto(FREE, da)
            app.uied_state(('screen', da.uid, FREE))
            app.uied['window'] = {'key': ('screen', da.uid, FREE), 'focus': True}
        if f == 40:
            st = app.uied['views'][('screen', da.uid, FREE)]
            la = layouts_of(da, FREE)
            state['la'] = la
            state['old_a'] = {lid: bytes(da.b.find(lid).data(0)) for lid in la}
            src = next(e for e in els(da, la[0]) if e.kind == 'Image' and e.texture and not e.image_bind)
            st['sel'] = (la[0], src.index)
            lays = [(da.b.find(lid), None, els(da, lid)) for lid in la]
            app.ui_command(da, st, lays, 'copy')
            st['sel'] = (la[1], 0)                       # paste into the second layout
            n0 = len(els(da, la[1]))
            app.ui_command(da, st, [(da.b.find(lid), None, els(da, lid)) for lid in la], 'paste')
            got = els(da, la[1])
            state['same'] = (len(got) == n0 + 1 and st['sel'] == (la[1], n0) and got[-1].texture == src.texture
                             and got[-1].gc != src.gc and (got[-1].x, got[-1].y) == (src.x, src.y))
        if f == 45:                                      # a picture as an image element
            la = state['la']
            r = da.b.find(la[0])
            n0 = len(els(da, la[0]))
            idx = app.ui_add_png(da, r, png, (640, 300))
            got = els(da, la[0])
            tex = da.b.find(got[idx].texture) if idx is not None else None
            inf = raster.info(tex, da.b.platform) if tex is not None else None
            state['png'] = (idx is not None and len(got) == n0 + 1 and inf is not None and (inf.w, inf.h) == (152, 72)
                            and (got[idx].w, got[idx].h) == (150.0, 70.0) and got[idx].anchor == 4)
            if state['png']:
                img = raster.decode(tex, da.b.platform, 0)
                state['png_pixels'] = int(img[35, 75, 2]) > 200 and int(img[35, 75, 3]) > 180   # the blue fill
            st = app.uied['views'][('screen', da.uid, FREE)]
            st['sel'] = (la[0], idx)
            state['png_idx'] = idx
        if f == 50:                       # the picture's element into the race HUD: its texture comes along
            st = app.uied['views'][('screen', da.uid, FREE)]
            la = state['la']
            src = els(da, la[0])[state['png_idx']]
            app.ui_copy(da, da.b.find(la[0]), src)
            rb = root_of(db)
            lb = layouts_of(db, rb)
            state['lb'], state['rb'] = lb, rb
            state['old_b'] = bytes(db.b.find(lb[0]).data(0))
            state['n_b'] = len(db.b.resources)
            missing = [rid for rid in app.uied['clip']['refs'] if db.b.find(rid) is None]
            idx = app.ui_paste(db, db.b.find(lb[0]))
            got = els(db, lb[0])
            state['cross'] = (idx is not None and got[idx].texture == src.texture
                              and db.b.find(src.texture) is not None
                              and len(db.b.resources) == state['n_b'] + len(missing))
            state['cross_n'] = len(missing)
        if f == 66:                                      # situations
            st = app.uied['views'][('screen', da.uid, FREE)]
            st['state'] = dict(U.STATE_PRESETS['Free roam'])
            st['state'].update(U.STATE_PRESETS['Pursuit'])
            ev = U.Evaluator(st['state'])
            chase = [e for lid in state['la'] for e in els(da, lid) if e.vis == 'Players.LocalPlayer.IsInChase']
            state['pursuit'] = bool(chase) and all(ev.visible(e.vis, False) for e in chase) and \
                not U.Evaluator(U.STATE_PRESETS['Free roam']).visible(chase[0].vis, False)
            s1, s2 = U.live_state(0.5), U.live_state(1.2)
            state['live'] = s1['Players.LocalPlayer.GetPlayerRPM'] != s2['Players.LocalPlayer.GetPlayerRPM']
            st['live'] = True
            state['shot'] = f + 6
        if f == 80:
            for d, keep in ((da, state['old_a']), (db, {state['lb'][0]: state['old_b']})):
                for _ in range(40):
                    if all(bytes(d.b.find(k).data(0)) == v for k, v in keep.items()) or not d.undo:
                        break
                    d.do_undo()
                    app.changed(d)
            state['undo'] = (all(bytes(da.b.find(k).data(0)) == v for k, v in state['old_a'].items())
                             and bytes(db.b.find(state['lb'][0]).data(0)) == state['old_b']
                             and len(db.b.resources) == state['n_b'])
        if f == 90:
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
    prev = params.callbacks.before_swap

    def before_swap():
        if prev:
            prev()
        if SHOT and state.get('shot') and state['f'] >= state['shot']:
            state['shot'] = None
            screenshot(SHOT)

    params.callbacks.before_swap = before_swap
    hello_imgui.run(params)
    for f, tb in state['errors'][:3]:
        print(f'frame {f}:')
        print(tb)
    check(not state['errors'], f'no frame raised ({len(state["errors"])} errors)')
    check(state.get('same'), 'an image copied into another layout of the screen: same texture and place, new id')
    check(state.get('cross') and state.get('cross_n', 0) > 0,
          f'pasted into the race HUD with the {state.get("cross_n")} resource(s) it needed')
    check(state.get('png') and state.get('png_pixels'), 'a PNG becomes an image element with its own 152 x 72 texture')
    check(state.get('pursuit') and state.get('live'), 'the Pursuit situation shows the chase elements; live values move')
    check(state.get('undo'), 'undo restores both bundles')
    shutil.rmtree(TMP, ignore_errors=True)
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
