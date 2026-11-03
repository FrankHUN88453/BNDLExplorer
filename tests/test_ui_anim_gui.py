"""UI editor animation in the real window, on a copy of UI/SCREENS2/371621.BNDL: the timeline lists the layout
transitions and the element transitions; scrubbing the play head fades the layouts in; a bar's new timing and a
layout transition are written and read back; a slide-in is added with its parameters; the preview models MoveFrom
(a point), ScaleXY, Tint (a palette colour) and Decode; undo restores the bytes. BNDLX_SHOT=<file.png> saves the
window with the timeline. Needs BNDLX_PC (see test_core.py)."""
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
TMP = tempfile.mkdtemp(prefix='bndlx_uianim_')
SCREEN = 0x10000000005aba5
DAMAGE = 0x1000000000462c9                    # the damage lights sequence
INTRO = 0x1000000000f7a02                     # 8.5 s with a black quad 0..8 s
SHOT2 = os.environ.get('BNDLX_SHOT2')
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


def model_checks():
    class E:
        pass
    el = U.Element(0, genesys.Node(0, 0), 'Image')
    el.x, el.y, el.tint, el.gc = 100.0, 200.0, [1, 1, 1, 1], 1

    def beh(typ, params, delay=0.0, dur=100.0):
        n = genesys.Node(0, 0)
        n.fields = {U.H_PARAMS: params, U.H_DELAY: delay, U.H_DURATION: dur, U.H_TYPE: typ, U.H_EASE: 0}
        return n
    el.behaviours = {'in': [beh(3, '2570,634')], 'out': [], 'idle': []}
    a0, a1 = U.animate(el, 'in', 0), U.animate(el, 'in', 100)
    move_from = (a0.dx, a0.dy) == (2470.0, 434.0) and (a1.dx, a1.dy) == (0.0, 0.0)
    el.behaviours['in'] = [beh(17, '100,100,110,100')]
    a = U.animate(el, 'in', 100)
    scale_xy = abs(a.sx - 1.1) < 1e-6 and abs(a.sy - 1.0) < 1e-6
    el.behaviours['in'] = [beh(13, 'TO,RED')]
    a = U.animate(el, 'in', 50, {'RED': [1.0, 0.0, 0.0, 1.0]})
    tint = a.tint is not None and abs(a.tint[1] - 0.5) < 1e-6
    el.behaviours['in'] = [beh(7, 'RANDOM,LINEAR,IN')]
    a = U.animate(el, 'in', 50)
    dec = a.decode is not None and U.decoded('HEAT LEVEL', 1.0) == 'HEAT LEVEL' and U.decoded('HEAT', 0.0) != 'HEAT'
    return move_from, scale_xy, tint, dec


def main():
    path = os.path.join(TMP, '371621.BNDL')
    shutil.copyfile(os.path.join(PC, 'UI', 'SCREENS2', '371621.BNDL'), path)
    roots.set_fallbacks([PC])
    immvision.use_rgb_color_order()
    app = A.App([path])
    app.pinned = lambda: [PC]
    params = A.runner_params(app, 'BNDL Explorer UI animation test')
    params.app_window_params.window_geometry.size = (1600, 900)
    state = {'f': 0, 'errors': []}

    def view():
        return app.uied['views'].get(('screen', app.docs[0].uid, SCREEN))

    def layouts(d, st):
        out = []
        for lid in st['params']:
            r = d.b.find(lid)
            if r is not None:
                n = genesys.Reader(app.types, d.b.e).read_resource(r)
                out.append((r, n, U.elements(app.types, n)))
        return out

    def step():
        f = state['f']
        if not app.docs:
            return
        d = app.docs[0]
        if f == 10:
            app.goto(SCREEN, d)
            app.uied_state(('screen', d.uid, SCREEN))
            app.uied['window'] = {'key': ('screen', d.uid, SCREEN), 'focus': True}
        if f == 40:
            st = view()
            lays = layouts(d, st)
            state['old'] = {r.id: bytes(r.data(0)) for r, _n, _e in lays}
            state['old_params'] = {pid: bytes(d.b.find(pid).data(0)) for pid in st['params'].values()
                                   if d.b.find(pid) is not None}
            rows = app.ui_timeline_rows(d, st, lays, 'in', False)
            state['rows'] = (len(rows), sum(1 for r in rows if r[2] is None))
            # scrub: 50 ms into the fade in of the layouts -> see-through
            plain = app.ui_build(d, st, lays, U.Evaluator(st['state']), None, 0, 1.0)[0]
            st['scrub'] = ('in', 50.0)
            early = app.ui_build(d, st, lays, U.Evaluator(st['state']), None, 0, 1.0)[0]
            st['scrub'] = None
            a_plain = [op[4][3] for op in plain if op[0] == 'quad']
            a_early = [op[4][3] for op in early if op[0] == 'quad']
            state['scrub'] = len(a_plain) == len(a_early) and sum(a_early) < sum(a_plain) * 0.9
        if f == 50:                                      # a bar's timing, a layout transition, a slide in
            st = view()
            lays = layouts(d, st)
            r, _n, els = next((x for x in lays if any(e.behaviours['in'] for e in x[2])), lays[0])
            el = next(e for e in els if e.behaviours['in'])
            app.ui_timeline_write(d, st, ('el', r.id, el.index, 'in', 0), 120.0, 480.0)
            back = next(e for e in layouts(d, st) if e[0].id == r.id)[2][el.index]
            p = U.behaviour(back.behaviours['in'][0])
            state['bar'] = (p['delay'], p['duration']) == (120.0, 480.0)
            lr = next((x[0] for x in lays if app.ui_layout_transitions(d, st, x[0].id)), None)
            tr = app.ui_layout_transitions(d, st, lr.id) if lr is not None else None
            if tr is not None:
                app.ui_set_layout_transition(d, tr, 'in', 'APPROACH(0, 300, -600, 0, EASEIN), FADE(0, 300, IN)')
                app.ui_timeline_write(d, st, ('lay', lr.id, 'in', 1), 100.0, 250.0)
                tr2 = app.ui_layout_transitions(d, st, lr.id)
                state['layout_tr'] = tr2['in'] == 'APPROACH(0, 300, -600, 0, EASEIN), FADE(100, 250, IN)'
            app.ui_add_behaviour(d, r, el, U.H_IN, '-600,0,EASIN', 'slide in', 5, 300.0)
            back = next(e for e in layouts(d, st) if e[0].id == r.id)[2][el.index]
            p = U.behaviour(back.behaviours['in'][-1])
            state['slide'] = (p['type'], p['params'], p['duration']) == (5, '-600,0,EASIN', 300.0)
            st['sel'] = (r.id, el.index)
            st['scrub'] = ('in', 150.0)
            state['shot'] = f + 8
        if f == 58:                                      # the damage lights sequence (runs on DamageBarState 0..3)
            app.uied['window'] = {'key': ('sequence', d.uid, DAMAGE), 'focus': True}
            app.uied_state(('sequence', d.uid, DAMAGE)).setdefault('seq', {'t': 0.0, 'play': None, 'drag': None,
                                                                          'sel': None})
            seq = app.uied['views'][('sequence', d.uid, DAMAGE)]['seq']
            seq['t'] = 0.5
            state['old_seq'] = bytes(d.b.find(DAMAGE).data(0))
            state['seq_shot'] = f + 3
        if f == 62:
            st = app.uied['views'][('sequence', d.uid, DAMAGE)]
            shown = set(st.get('layout_time') or {})
            state['seq_shown'] = {0x10000000007a3bd, 0x10000000007a3c2} <= shown
            node = genesys.Reader(app.types, d.b.e).read_resource(d.b.find(DAMAGE))
            it = U.sequence_items(app.types, node)[0]
            app.ui_step(d, d.b.find(DAMAGE), 'seq')
            app.ui_set(d, d.b.find(DAMAGE), it.node, {U.H_START: 0.25, U.H_END: 2.5})
            back = U.sequence_items(app.types, genesys.Reader(app.types, d.b.e).read_resource(d.b.find(DAMAGE)))[0]
            state['seq_write'] = (back.start, back.end) == (0.25, 2.5)
            app.uied['window'] = {'key': ('sequence', d.uid, INTRO), 'focus': True}
            app.uied_state(('sequence', d.uid, INTRO)).setdefault('seq', {'t': 4.0, 'play': None, 'drag': None,
                                                                         'sel': None})
            state['shot2'] = f + 4
        if f == 66:
            st = app.uied['views'][('sequence', d.uid, INTRO)]
            state['black'] = (st.get('black') or 0) > 0.5
            state['old']['seq'] = None
            app.uied['window'] = {'key': ('screen', d.uid, SCREEN), 'focus': True}
        if f == 70:
            st = view()
            st['scrub'] = None
            state['old'].pop('seq', None)
            state['old'][DAMAGE] = state['old_seq']
            for _ in range(40):
                if (all(bytes(d.b.find(k).data(0)) == v for k, v in state['old'].items())
                        and all(bytes(d.b.find(k).data(0)) == v for k, v in state['old_params'].items())) or not d.undo:
                    break
                d.do_undo()
                app.changed(d)
            state['undo'] = (all(bytes(d.b.find(k).data(0)) == v for k, v in state['old'].items())
                             and all(bytes(d.b.find(k).data(0)) == v for k, v in state['old_params'].items()))
        if f == 80:
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
        if SHOT2 and state.get('shot2') and state['f'] >= state['shot2'] - 2 and state.get('shot2_done') is None:
            pass
        if SHOT2 and state.get('seq_shot') and state['f'] >= state['seq_shot']:
            state['seq_shot'] = None
            screenshot(SHOT2)

    params.callbacks.before_swap = before_swap
    hello_imgui.run(params)
    for f, tb in state['errors'][:3]:
        print(f'frame {f}:')
        print(tb)
    move_from, scale_xy, tint, dec = model_checks()
    check(not state['errors'], f'no frame raised ({len(state["errors"])} errors)')
    rows, lay_rows = state.get('rows', (0, 0))
    check(rows > 3 and lay_rows >= 1, f'the timeline has {rows} rows, {lay_rows} of them layout transitions')
    check(state.get('scrub'), 'scrubbing 50 ms into the transition in shows the layouts fading in')
    check(state.get('bar'), 'a bar dragged to 120 ms / 480 ms is written to the element')
    check(state.get('layout_tr'), 'a layout transition is set and its FADE re-timed from the timeline')
    check(state.get('slide'), 'a slide in (Approach -600,0,EASIN) is added')
    check(move_from and scale_xy and tint and dec, f'MoveFrom {move_from}, ScaleXY {scale_xy}, Tint {tint}, '
                                                   f'Decode {dec} in the preview model')
    check(state.get('seq_shown'), 'the damage sequence at 0.5 shows its two layouts')
    check(state.get('seq_write'), 'a sequence item dragged to 0.25 - 2.5 is written')
    check(state.get('black'), 'the intro sequence at 4 s is dark behind its black quad')
    check(state.get('undo'), 'undo restores the layouts, their parameters and the sequence')
    shutil.rmtree(TMP, ignore_errors=True)
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
