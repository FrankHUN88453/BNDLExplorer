"""FBX export and import in the real window, on a copy of a car bundle: select the car, Export FBX (with texture
PNGs), import the file back (the report dialog, the data unchanged), then an edited copy (one object moved) and
undo it. Needs BNDLX_PC (see test_core.py)."""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from imgui_bundle import hello_imgui, immvision  # noqa: E402

from bndlx import app as A  # noqa: E402
from bndlx import fbx, mesh, roots  # noqa: E402

PC = os.environ['BNDLX_PC']
TMP = tempfile.mkdtemp(prefix='bndlx_fbx_')
failures = []


def check(cond, what):
    print(('ok    ' if cond else 'FAIL  ') + what)
    if not cond:
        failures.append(what)


def moved_copy(src, dst, name):
    """The FBX with object `name` moved 0.1 m up (FBX Y): its Vertices changed in the node tree."""
    _, nodes = fbx.read_binary(open(src, 'rb').read())
    objs = next(n for n in nodes if n.name == 'Objects')
    geo = next(g for g in objs.findall('Geometry') if g.props[1].split(chr(0))[0] == name)
    v = geo.find('Vertices')
    arr = v.props[0].reshape(-1, 3).copy()
    arr[:, 1] += 0.1
    v.props[0] = arr.ravel()
    open(dst, 'wb').write(fbx.write_binary(nodes))


def main():
    car = os.path.join(TMP, 'VEH_1085007_HI.BNDL')
    shutil.copyfile(os.path.join(PC, 'VEHICLES', 'VEH_1085007_HI.BNDL'), car)
    roots.set_fallbacks([PC])
    immvision.use_rgb_color_order()
    app = A.App([car])
    app.pinned = lambda: [PC]          # the copy is outside the game folder: shaders come from the game
    params = A.runner_params(app, 'BNDL Explorer FBX test')
    frame = [0]
    state = {}
    out = os.path.join(TMP, 'car.fbx')

    def gui():
        app.gui()
        f = frame[0] = frame[0] + 1
        d = app.cur
        if 'vgs' not in state and d is not None:
            state['vgs'] = next(r.id for r in d.b.resources if r.type == mesh.T_VGS)
            app.goto(state['vgs'], d)
        elif 'export' not in state and app.preview_ready() and f > 5:
            r = d.b.find(state['vgs'])
            state['export'] = app.export_fbx(d, r, out)
            state['data'] = {x.id: x.data(1) for x in d.b.resources if x.type == mesh.T_RENDERABLE}
            app.import_fbx(d, r, out)
            state['report'] = (app.modal or {}).get('text', '')
            state['same'] = all(d.b.find(k).data(1) is not None for k in state['data'])
            app.modal = None
            body = next(n for n in (m.name for m in fbx.load_meshes(open(out, 'rb').read())) if '~' not in n)
            moved_copy(out, out.replace('.fbx', '_moved.fbx'), body)
            state['body'] = body
            before = app.model_meshes(d, r)
            app.import_fbx(d, r, out.replace('.fbx', '_moved.fbx'))
            after = app.model_meshes(d, r)
            names = __import__('bndlx.meshimport', fromlist=['x']).export_names(before)
            i = names.index(body)
            state['shift'] = float(after[i].pos[:, 1].mean() - before[i].pos[:, 1].mean())
            app.modal = None
            d.do_undo()
            back = app.model_meshes(d, r)
            state['undo'] = float(abs(back[i].pos[:, 1].mean() - before[i].pos[:, 1].mean()))
            state['done'] = f
        elif 'done' in state and f > state['done'] + 3 or f > 3000:
            hello_imgui.get_runner_params().app_shall_exit = True
            app.exit_ok = True

    params.callbacks.show_gui = gui
    hello_imgui.run(params)
    n, nt = state.get('export', (0, 0))
    pngs = os.listdir(os.path.join(TMP, 'car_textures')) if os.path.isdir(os.path.join(TMP, 'car_textures')) else []
    check(n == 45 and nt > 0 and len(pngs) == nt, f'Export FBX: {n} meshes, {nt} textures as PNG next to it')
    check('replaced' in state.get('report', '') and 'further copies' in state.get('report', ''),
          'Import FBX of the exported file reports the replaced meshes: '
          + state.get('report', '').split(chr(10))[0])
    check(abs(state.get('shift', 0) - 0.1) < 2e-3, f"an object moved in the FBX moves in the car ({state.get('shift')})")
    check(state.get('undo', 1) < 1e-6, 'Ctrl+Z undoes the import')
    shutil.rmtree(TMP, ignore_errors=True)
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
