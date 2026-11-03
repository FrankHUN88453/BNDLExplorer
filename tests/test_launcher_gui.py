"""Mod Launcher in its real window, on temporary game folders (see test_modpack.py): Make a package finds the
changed files of a game folder and writes a package; two packages that change the same trigger resource are added; Apply asks which one is to win, the answer reorders them and the game files are
built with the winner; Apply again does not ask; Apply and start runs the (stubbed) game start; the order is
exported and read back. BNDLX_SHOT=<file.png> saves the conflict question, BNDLX_SHOT2 the list with the conflicts
tab, BNDLX_SHOT3 the Make a package tab. Needs BNDLX_PC."""
import os
import sys
import tempfile
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['APPDATA'] = tempfile.mkdtemp(prefix='bndlx_launcher_cfg_')      # the user's settings stay untouched
import numpy as np  # noqa: E402
from imgui_bundle import hello_imgui  # noqa: E402

import test_modpack as T  # noqa: E402
from bndlx import launcher as L, modpack as M  # noqa: E402

SHOT = os.environ.get('BNDLX_SHOT')
SHOT2 = os.environ.get('BNDLX_SHOT2')
SHOT3 = os.environ.get('BNDLX_SHOT3')
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
    a = T.game('l_author1')
    x1 = T.move_trigger(a, 100.0)
    z1 = os.path.join(T.TMP, 'Faster jack spots.zip')            # made in the Make a package tab
    b = T.game('l_author2')
    x2 = T.move_trigger(b, -50.0)
    T.edit_screen(b)
    z2 = os.path.join(T.TMP, 'HUD tweaks.zip')
    M.make_package(b, z2, M.scan(b), {'name': 'HUD tweaks', 'mod_version': '2.1', 'author': 'two',
                                      'description': 'Moves a trigger and changes a UI screen.'})
    g = T.game('l_player')
    state = {'errors': [], 'phase': 0, 't': 0}
    shots = {}

    def script(app, f):
        v = app.view
        try:
            mk = app.maker
            if f == 2:
                app.want_tab = 'make'
                mk.root = a
                mk.scan(a)
                state['phase'] = -1
            if state['phase'] == -1 and mk.changes is not None:
                state['scan'] = {c.path: (c.kind, c.resources) for c in mk.changes}
                mk.meta.update(name='Faster jack spots', mod_version='1.0', author='one')
                mk.write(z1)
                shots['make'] = f + 2
                state['phase'] = -2
            if state['phase'] == -2 and not mk.job.busy() and os.path.isfile(z1):
                state['made'] = [x['path'] for x in M.Package(z1).files]
                app.want_tab = 'order'
                state['phase'] = 0
                state['start'] = f + 2
            if state['phase'] == 0 and f == state.get('start'):
                v.add([z1, z2])
                v.sel = 1
                v.start_fn = lambda root: root
                state['added'] = [m.name for m in v.profile.mods]
            if state['phase'] == 0 and f == state.get('start', -9) + 3:
                v.request_apply()
                state['asked'] = v.ask is not None and len(v.ask) == 1 and len(v.ask[0][2]) == 1
                state['ask_label'] = v.ask[0][2][0].label if v.ask else ''
                shots['ask'] = f + 3
            if state['phase'] == 0 and f == state.get('start', -9) + 9 and v.ask:
                md5 = {m.name: m.md5 for m in v.profile.mods}
                v.answer(md5['Faster jack spots'], md5['HUD tweaks'])      # the first one is to win
                state['order'] = [m.name for m in v.profile.mods]
                state['phase'] = 1
            if state['phase'] == 1 and not v.job.busy() and v.report is not None:
                state['won'] = abs(T.trigger_x(g) - x1) < 1e-3
                state['screen'] = (M.md5_file(T.path(g, 'UI/SCREENS2/371621.BNDL'))
                                   == M.md5_file(T.path(b, 'UI/SCREENS2/371621.BNDL')))
                v.report = None
                v.request_apply(start_after=True)                          # decided: no question this time
                state['asked_again'] = v.ask is not None
                state['phase'] = 2
            if state['phase'] == 2 and not v.job.busy() and v.report is not None:
                state['started'] = v.started == g
                order = os.path.join(T.TMP, 'order.json')
                v.profile.export_order(order)
                other = M.Profile.load(T.game('l_friend'))
                state['order_file'] = (not other.import_order(order, [T.TMP])
                                       and [m.name for m in other.mods] == state['order'])
                shots['main'] = f + 3
                state['phase'] = 3
                state['end'] = f + 8
            if state['phase'] == 3 and f >= state['end']:
                hello_imgui.get_runner_params().app_shall_exit = True
            if f > 3000:
                hello_imgui.get_runner_params().app_shall_exit = True
        except Exception:
            state['errors'].append(traceback.format_exc())
            print(state['errors'][-1], flush=True)
            hello_imgui.get_runner_params().app_shall_exit = True

    frame = [0]
    orig_run = hello_imgui.run

    def run_with_shots(params):
        prev = params.callbacks.before_swap

        def before_swap():
            if prev:
                prev()
            frame[0] += 1
            for key, path in (('ask', SHOT), ('main', SHOT2), ('make', SHOT3)):
                if path and shots.get(key) and frame[0] >= shots[key]:
                    shots[key] = None
                    screenshot(path)
        params.callbacks.before_swap = before_swap
        orig_run(params)

    hello_imgui.run = run_with_shots
    t0 = time.time()
    try:
        L.run(g, script=script)
    finally:
        hello_imgui.run = orig_run
    check(not state['errors'], f'no frame raised ({len(state["errors"])} errors)')
    check(state.get('scan') == {'HAWAII/TRIGGERS.BNDL': ('bundle', 1)} and state.get('made') == ['HAWAII/TRIGGERS.BNDL'],
          f'Make a package: {state.get("scan")} -> {state.get("made")}')
    check(state.get('added') == ['Faster jack spots', 'HUD tweaks'], f'two packages added: {state.get("added")}')
    check(state.get('asked'), f'Apply asks which package wins the shared element ({state.get("ask_label")!r})')
    check(state.get('order') == ['HUD tweaks', 'Faster jack spots'], f'the answer reorders: {state.get("order")}')
    check(state.get('won') and state.get('screen'), 'the chosen package wins the trigger, the other keeps its screen')
    check(state.get('asked_again') is False, 'a settled conflict is not asked again')
    check(state.get('started'), 'Apply and start the game starts the game after building')
    check(state.get('order_file'), 'the exported order reads back in the same order')
    print(f'({time.time() - t0:.0f} s)')
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
