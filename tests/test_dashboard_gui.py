"""The dashboard (the launched UI) in the real window, on a copy of a bundle: open it, select resources of several
kinds, go Home and back, open the Hex Editor workspace, and no frame raises. Needs BNDLX_PC (see test_core.py)."""
import os
import shutil
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from imgui_bundle import hello_imgui, immvision  # noqa: E402

from bndlx import app as A, new_ui, roots, theme  # noqa: E402

PC = os.environ['BNDLX_PC']
TMP = tempfile.mkdtemp(prefix='bndlx_dash_')
failures = []


def check(cond, what):
    print(('ok    ' if cond else 'FAIL  ') + what)
    if not cond:
        failures.append(what)


def main():
    car = os.path.join(TMP, 'VEH_1085007_HI.BNDL')
    shutil.copyfile(os.path.join(PC, 'VEHICLES', 'VEH_1085007_HI.BNDL'), car)
    roots.set_fallbacks([PC])
    immvision.use_rgb_color_order()
    app = A.App([car])
    app.pinned = lambda: [PC]
    theme.select('Violet')
    dash = new_ui.Dashboard(app)
    dash.loading_screen_enabled = False
    dash.launch_fade_enabled = False
    params = A.runner_params(app, 'BNDL Explorer dashboard test')
    params.callbacks.setup_imgui_style = new_ui.apply_style
    state = {'f': 0, 'errors': [], 'steps': [], 'click': None}
    orig_select = dash.sidebar_icon_selectable

    def sidebar_icon_selectable(kind, label, key, selected, type_id=None):
        hit = orig_select(kind, label, key, selected, type_id)
        if key == state['click']:            # a real click on the sidebar entry, inside the frame
            state['click'] = None
            return True
        return hit

    dash.sidebar_icon_selectable = sidebar_icon_selectable
    kinds = [0x01, 0x15, 0x14, 0x05, 0x81, 0x02, 0x103]

    def step():
        f = state['f']
        d = app.cur
        if f == 20 and d is not None:
            state['picked'] = []
            for t in kinds:
                r = next((x for x in d.b.resources if x.type == t), None)
                if r is not None:
                    state['picked'].append(r.id)
        if 20 < f < 20 + 25 * 8 and state.get('picked') and (f - 20) % 25 == 0:
            k = (f - 20) // 25 - 1
            if k < len(state['picked']):
                app.goto(state['picked'][k], app.docs[0])
                state['steps'].append(f'select {state["picked"][k]:#x}')
        if f == 250:
            state['click'] = 'home'
            state['steps'].append('home (clicked in the sidebar)')
        if f == 280:
            state['home_ok'] = app.cur is None
            app.show_doc(app.docs[0]) if hasattr(app, 'show_doc') else None
            state['steps'].append('back to the bundle')
        if f == 300:
            app.goto(state['picked'][0], app.docs[0])
        if f == 310:
            state['click'] = 'home'
            state['steps'].append('home again with a texture shown')
        if f == 340:                          # a resource chunk in the Hex Editor, saved back into the resource
            d = app.docs[0]
            r = d.b.find(state['picked'][1])
            state['hex_rid'] = r.id
            state['hex_old'] = bytes(r.data(0))
            dash.open_resource_in_hex(d, r, 0)
            doc = dash.hex_document
            doc.replace(8, 1, bytes([state['hex_old'][8] ^ 0xFF]))
            doc.save(doc.path)
            state['hex_msg'] = dash._write_back_hex(doc.path)
            state['hex_new'] = bytes(r.data(0))
            state['steps'].append('hex edit')
        if f == 360:
            d = app.docs[0]
            d.do_undo()
            state['hex_undo'] = bytes(d.b.find(state['hex_rid']).data(0)) == state['hex_old']
        if f == 380:
            hello_imgui.get_runner_params().app_shall_exit = True
            app.exit_ok = True

    def gui():
        try:
            dash.gui()
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
    hello_imgui.run(params)
    for f, tb in state['errors'][:3]:
        print(f'frame {f}:')
        print(tb)
    check(not state['errors'], f'no frame raised ({len(state["errors"])} errors; steps: {", ".join(state["steps"])})')
    check(state.get('home_ok'), 'Home leaves the bundle for the home view')
    old, new = state.get('hex_old', b''), state.get('hex_new', b'')
    check(len(old) == len(new) and old[:8] == new[:8] and new[8] == old[8] ^ 0xFF and old[9:] == new[9:]
          and state.get('hex_undo'), f'a resource chunk edited in the Hex Editor goes back into the resource and '
          f'Ctrl+Z undoes it ({state.get("hex_msg")})')
    shutil.rmtree(TMP, ignore_errors=True)
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
