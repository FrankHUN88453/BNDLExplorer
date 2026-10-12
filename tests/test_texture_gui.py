"""Textures in the real window, on a copy of a car bundle: the car's Textures window lists its textures, a texture
replaced from a PNG shows in the 3D view (re-uploaded) and in the full-size window (at its full resolution), undo
brings it back. Needs BNDLX_PC (see test_core.py)."""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import numpy as np  # noqa: E402
from imgui_bundle import hello_imgui, immvision  # noqa: E402

from bndlx import app as A  # noqa: E402
from bndlx import mesh, ops, raster, roots  # noqa: E402

PC = os.environ['BNDLX_PC']
TMP = tempfile.mkdtemp(prefix='bndlx_tex_')
failures = []


def check(cond, what):
    print(('ok    ' if cond else 'FAIL  ') + what)
    if not cond:
        failures.append(what)


def main():
    car = os.path.join(TMP, 'VEH_1085007_HI.BNDL')
    shutil.copyfile(os.path.join(PC, 'VEHICLES', 'VEH_1085007_HI.BNDL'), car)
    png = os.path.join(TMP, 'red.png')
    img = np.zeros((128, 256, 4), np.uint8)
    img[..., 0], img[..., 3] = 230, 255
    ops.save_png(img, png)
    roots.set_fallbacks([PC])
    immvision.use_rgb_color_order()
    app = A.App([car])
    app.pinned = lambda: [PC]
    params = A.runner_params(app, 'BNDL Explorer texture test')
    state = {'f': 0}

    def gui():
        app.gui()
        f = state['f'] = state['f'] + 1
        d = app.cur
        if d is None or f > 3000:
            if f > 3000:
                hello_imgui.get_runner_params().app_shall_exit = True
                app.exit_ok = True
            return
        if 'vgs' not in state:
            state['vgs'] = next(r.id for r in d.b.resources if r.type == mesh.T_VGS)
            app.goto(state['vgs'], d)
        elif 'tid' not in state and app.preview_ready() and f > 5 and app.viewer is not None and app.viewer.meshes:
            res = app.model['result']
            ids = [m.texture for m in res[1] if m.texture and d.b.find(m.texture) is not None]
            state['tid'] = ids[0]
            app.model_tex = {'doc': d.uid, 'rid': state['vgs'], 'focus': True}
            state['t'] = f
        elif 'replaced' not in state and 'tid' in state and f > state['t'] + 5:
            state['listed'] = app.model_tex is not None
            r = d.b.find(state['tid'])
            state['old'] = raster.info(r, d.b.platform).describe()
            state['replaced'] = app.replace_from_file(d, r, png)
            state['t'] = f
        elif 'view' not in state and 'replaced' in state and f > state['t'] + 10 and app.preview_ready():
            res = app.model['result']
            if res and res[0] == 'ok' and res[2].get(state['tid']) is not None and res[2][state['tid']].shape[0] == 128:
                state['view'] = res[2][state['tid']]
                from OpenGL import GL                  # what is on the GPU for that texture now
                gl = app.viewer.textures.get(state['tid'])
                if gl:
                    GL.glBindTexture(GL.GL_TEXTURE_2D, gl)
                    state['gpu'] = (GL.glGetTexLevelParameteriv(GL.GL_TEXTURE_2D, 0, GL.GL_TEXTURE_WIDTH),
                                    GL.glGetTexLevelParameteriv(GL.GL_TEXTURE_2D, 0, GL.GL_TEXTURE_HEIGHT))
                    GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
                app.open_texture_window(d, d.b.find(state['tid']))
                state['t'] = f
        elif 'full' not in state and 'view' in state and f > state['t'] + 5:
            st = app.tex_full or {}
            if st.get('img') is not None:
                state['full'] = st['img']
                d.do_undo()
                app.changed(d)
                state['back'] = raster.info(d.b.find(state['tid']), d.b.platform).describe()
                state['done'] = f
        elif 'done' in state and f > state['done'] + 3:
            hello_imgui.get_runner_params().app_shall_exit = True
            app.exit_ok = True

    params.callbacks.show_gui = gui
    hello_imgui.run(params)
    check(state.get('listed'), 'the car\'s Textures window lists its textures')
    v = state.get('view')
    check(state.get('replaced') and v is not None and v[..., 0].mean() > 200 and v[..., 1].mean() < 30
          and tuple(int(x) for x in state.get('gpu', (0, 0))) == (256, 128),
          f'a texture replaced from a PNG shows in the 3D view, uploaded again ({state.get("old")} -> '
          f'{state.get("gpu")} on the GPU)')
    full = state.get('full')
    check(full is not None and full.shape[:2] == (128, 256), 'the full-size window shows it at its full resolution')
    check(state.get('back') == state.get('old'), f'Ctrl+Z brings the old texture back ({state.get("back")})')
    shutil.rmtree(TMP, ignore_errors=True)
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
