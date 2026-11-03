"""Trigger map in the real window, on copies of HAWAII\\TRIGGERS.BNDL, PVS.BNDL and TRAFFIC.BNDL: the triggers,
districts and traffic lanes load; a box moved with the mouse, a sphere added and a locator deleted are written
(one undo step each, the grid rebuilt) and undo restores the bytes; drawing the whole map stays fast.
BNDLX_SHOT=<file.png> saves the window zoomed in on a jack spot, BNDLX_SHOT2 the whole city. Needs BNDLX_PC (see test_core.py)."""
import os
import shutil
import sys
import tempfile
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import numpy as np  # noqa: E402
from imgui_bundle import hello_imgui, imgui, immvision  # noqa: E402

from bndlx import app as A, roots, triggers as TR  # noqa: E402

PC = os.environ['BNDLX_PC']
SHOT = os.environ.get('BNDLX_SHOT')
SHOT2 = os.environ.get('BNDLX_SHOT2')
TMP = tempfile.mkdtemp(prefix='bndlx_trig_')
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
    hawaii = os.path.join(TMP, 'HAWAII')
    os.makedirs(hawaii)
    for n in ('TRIGGERS.BNDL', 'PVS.BNDL', 'TRAFFIC.BNDL'):
        shutil.copyfile(os.path.join(PC, 'HAWAII', n), os.path.join(hawaii, n))
    path = os.path.join(hawaii, 'TRIGGERS.BNDL')
    roots.set_fallbacks([PC])
    immvision.use_rgb_color_order()
    app = A.App([path])
    app.pinned = lambda: [PC]
    params = A.runner_params(app, 'BNDL Explorer trigger map test')
    params.app_window_params.window_geometry.size = (1600, 900)
    state = {'f': 0, 'errors': [], 'times': []}

    def res(d):
        return next(x for x in d.b.resources if x.type == TR.T_TRIGGERS)

    def step():
        f = state['f']
        if not app.docs:
            return
        d = app.docs[0]
        st = getattr(app, 'trig', None)
        if f == 5:
            app.goto(res(d).id, d)
            state['orig'] = bytes(res(d).data(0))
        if f == 20:
            td = st['td']
            state['loaded'] = (td is not None and len(td.triggers) == 9042, len(st['zones']),
                               len(st['roads'][0]) if st['roads'] else 0)
        if f == 40:                                   # drag the first jack spot 200 m east
            td = st['td']
            i = next(j for j, t in enumerate(td.triggers) if t.kind == 60)
            state['moved'] = (i, td.triggers[i].pos[0] + 200.0, td.triggers[i].owner)
            st['sel'] = i
            td.triggers[i].pos[0] += 200.0
            app.trig_touch(st, td, i)
            st['pending'] = 'move trigger'
            state['undo0'] = len(d.undo)
        if f == 44:
            td = TR.TriggerData.read(res(d).data(0), d.b.e)
            i, x, owner = state['moved']
            t = next(t for t in td.triggers if t.owner == owner and t.kind == 60)
            cells = td.cells()
            a, b = td.cell_of(t.pos[0], t.pos[2])
            state['move_ok'] = (abs(t.pos[0] - x) < 0.01 and td.ordered().index(t) in cells[b * td.grid[0] + a]
                                and len(d.undo) == state['undo0'] + 1 and d.b.modified)
            app.trig_add(st, st['td'], TR.SPHERE)           # the Add sphere button
            st['pending'] = 'add sphere trigger'
        if f == 48:
            td = TR.TriggerData.read(res(d).data(0), d.b.e)
            state['add_ok'] = td.counts()[TR.SPHERE] == 54
            loc = next(j for j, t in enumerate(st['td'].triggers) if t.shape == TR.LOCATOR)
            del st['td'].triggers[loc]
            st['ext'], st['sel'] = None, None
            st['pending'] = 'delete trigger'
        if f == 52:
            td = TR.TriggerData.read(res(d).data(0), d.b.e)
            state['del_ok'] = td.counts()[TR.LOCATOR] == 2584 and td.cells() == td.build_cells(td.ordered())
            state['t0'] = f
            st['window'] = {'key': (d.uid, res(d).id), 'focus': True}     # the large window
            st['zoom'], st['pan'], st['sel'] = 1.0, [0.0, 0.0], None
            state['preset'] = set(st['hidden'])
            state['preset_ok'] = bool(st['hidden']) and not (st['hidden'] & {60, 61, 5, 58})
            state['shot2'] = f + 4                     # the whole city, the gameplay types (first view)
        if 60 <= f < 80:                                 # whole map, every type drawn
            st['hidden'] = set()
        if f == 80:                                      # zoom onto the moved jack spot for the picture
            td = st['td']
            i = state['moved'][0]
            t = td.triggers[i]
            nx, nz, ox, oz, cw, ch = td.grid
            st['hidden'] = state['preset']
            st['sel'] = i
            st['zoom'] = 8.0
            st['pan'] = [t.pos[0] - (ox + nx * cw / 2), t.pos[2] - (oz + nz * ch / 2)]
            state['shot'] = f + 4
        if f == 86:
            state['window_ok'] = st.get('window') is not None and st['key'][1] == res(d).id
        if f == 90:
            for _ in range(10):
                if bytes(res(d).data(0)) == state['orig'] or not d.undo:
                    break
                d.do_undo()
                app.changed(d)
            state['undo_ok'] = bytes(res(d).data(0)) == state['orig']
        if f == 96:
            hello_imgui.get_runner_params().app_shall_exit = True
            app.exit_ok = True

    def gui():
        t0 = time.perf_counter()
        try:
            app.gui()
        except Exception:
            state['errors'].append((state['f'], traceback.format_exc()))
            if len(state['errors']) == 1:
                print(state['errors'][0][1], flush=True)
        if 62 <= state['f'] < 80:
            state['times'].append(time.perf_counter() - t0)
        state['f'] += 1
        try:
            step()
        except Exception:
            state['errors'].append((state['f'], traceback.format_exc()))
            print(state['errors'][-1][1], flush=True)
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
        if SHOT2 and state.get('shot2') and state['f'] >= state['shot2']:
            state['shot2'] = None
            screenshot(SHOT2)

    params.callbacks.before_swap = before_swap
    hello_imgui.run(params)
    for f, tb in state['errors'][:3]:
        print(f'frame {f}:')
        print(tb)
    check(not state['errors'], f'no frame raised ({len(state["errors"])} errors)')
    ok, zones, roads = state.get('loaded', (False, 0, 0))
    check(ok and zones > 100 and roads > 1000, f'9042 triggers, {zones} districts zones, {roads} traffic lanes')
    check(state.get('move_ok'), 'a moved jack spot is written, in its new grid cell, as one undo step')
    check(state.get('add_ok'), 'an added sphere is written')
    check(state.get('del_ok'), 'a deleted locator is gone and the grid matches the shapes')
    ms = 1000 * float(np.median(state['times'])) if state['times'] else 1e9
    check(ms < 120, f'the whole map draws in {ms:.0f} ms a frame')
    check(state.get('window_ok'), 'the map opens in its large window')
    check(state.get('preset_ok'), 'the first view shows the gameplay types (jack spots, roadblocks, safehouses...)')
    check(state.get('undo_ok'), 'undo restores the original bytes')
    shutil.rmtree(TMP, ignore_errors=True)
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
