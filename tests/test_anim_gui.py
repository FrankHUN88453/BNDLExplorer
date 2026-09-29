"""Animations in the real window: a car's damage animation list plays on the car (the body moves between the start
and the end), a camera animation shows its path, a skeleton shows its bones. Needs BNDLX_PC (see test_core.py)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from imgui_bundle import hello_imgui, immvision  # noqa: E402

from bndlx import app as A  # noqa: E402
from bndlx import anim  # noqa: E402

PC = os.environ['BNDLX_PC']
failures = []


def check(cond, what):
    print(('ok    ' if cond else 'FAIL  ') + what)
    if not cond:
        failures.append(what)


def main():
    car = os.path.join(PC, 'VEHICLES', 'VEH_1085007_HI.BNDL')
    cam = os.path.join(PC, 'EN_US', 'FEEDBACKGROUPS', '1156428.BNDL')
    immvision.use_rgb_color_order()
    app = A.App([car, cam])
    params = A.runner_params(app, 'BNDL Explorer animation test')
    frame = [0]
    state = {}
    docs = {os.path.basename(d.path): d for d in app.docs}
    steps = [('list', docs['VEH_1085007_HI.BNDL'], anim.T_ANIMLIST),
             ('camera', docs['1156428.BNDL'], anim.T_ANIMATION),
             ('skeleton', docs['VEH_1085007_HI.BNDL'], anim.T_SKELETON)]

    def gui():
        app.gui()
        f = frame[0] = frame[0] + 1
        i = state.get('step', 0)
        if i >= len(steps) or f > 3000:
            hello_imgui.get_runner_params().app_shall_exit = True
            app.exit_ok = True
            return
        name, d, t = steps[i]
        if 'at' not in state:
            r = next(x for x in d.b.resources if x.type == t and (t != anim.T_ANIMLIST or len(x.data(0)) > 4000))
            app.goto(r.id, d)
            state['at'] = f
            return
        st = getattr(app, 'anim_st', None)
        if st is None or st.get('posed') is None or f < state['at'] + 4:
            return
        sc = st['scene']
        if name == 'list' and 'end' not in state:
            st['t'] = sc['anims'][0].duration
            state['end'] = f
            return
        if name == 'list' and f < state['end'] + 3:
            return
        state[name] = (st.get('error'), sc, dict(st))
        state['step'] = i + 1
        state.pop('at')

    params.callbacks.show_gui = gui
    hello_imgui.run(params)
    err, sc, st = state.get('list', ('missing', None, {}))
    check(err is None and sc is not None and sc['car'] and len(sc['anims']) == 8 and sc['skinned'],
          f"damage animation list: {len(sc['anims']) if sc else 0} animations on the car "
          f"({len(sc['skinned']) if sc else 0} skinned meshes)")
    err, sc, st = state.get('camera', ('missing', None, {}))
    check(err is None and sc is not None and not sc['car'] and sc['bounds'] is not None
          and np.ptp(np.array(sc['bounds']), 0).max() > 1,
          'camera animation: its path is framed')
    err, sc, st = state.get('skeleton', ('missing', None, {}))
    check(err is None and sc is not None and sc['skel'].count == 37 and sc['car'], 'skeleton shown on its car')
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
