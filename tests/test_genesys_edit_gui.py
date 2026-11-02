"""Genesys object editing in the real window, on a copy of GAMELOGIC/GAMEMODES.BNDL: the event list (its largest
object, which did not open before) shows; "duplicate" on an element (a real click inside the frame) adds an event,
undo takes it back; an edited JSON imported into the object lands in it; a longer text (in a copy of the WEAPONS
bundle) is written through the writer; a UI script (Lua bytecode) shows, a changed constant is applied and undone.
Needs BNDLX_PC (see test_core.py)."""
import json
import os
import shutil
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from imgui_bundle import hello_imgui, imgui, immvision  # noqa: E402

from bndlx import app as A, genesys, gjson, luabc, roots, textfile  # noqa: E402

PC = os.environ['BNDLX_PC']
TMP = tempfile.mkdtemp(prefix='bndlx_gedit_')
EVENTS = 0x100000000045d5b
ITEMS = 0x4974656d
failures = []


def check(cond, what):
    print(('ok    ' if cond else 'FAIL  ') + what)
    if not cond:
        failures.append(what)


def main():
    path = os.path.join(TMP, 'GAMEMODES.BNDL')
    shutil.copyfile(os.path.join(PC, 'GAMELOGIC', 'GAMEMODES.BNDL'), path)
    weapons = os.path.join(TMP, '457359.BNDL')
    shutil.copyfile(os.path.join(PC, 'GAMELOGIC', 'WEAPONS', '457359.BNDL'), weapons)
    screen = os.path.join(TMP, '1019407.BNDL')           # a UI screen with Lua scripts
    shutil.copyfile(os.path.join(PC, 'UI', 'SCREENS2', '1019407.BNDL'), screen)
    roots.set_fallbacks([PC])
    immvision.use_rgb_color_order()
    app = A.App([path, weapons, screen])
    app.pinned = lambda: [PC]
    params = A.runner_params(app, 'BNDL Explorer Genesys edit test')
    state = {'f': 0, 'errors': [], 'press': None}
    orig_button = imgui.small_button
    orig_big = imgui.button

    def small_button(label):
        hit = orig_button(label)
        if state['press'] and label.startswith(state['press']):
            state['press'] = None              # the first such button drawn this frame is clicked
            return True
        return hit

    def button(label, *a):
        hit = orig_big(label, *a)
        if state['press'] and label.startswith(state['press']):
            state['press'] = None
            return True
        return hit

    A.imgui.small_button = small_button
    A.imgui.button = button

    def items(d):
        return genesys.Reader(app.types, d.b.e).read_resource(d.b.find(EVENTS)).fields[ITEMS]

    def step():
        f = state['f']
        d = app.cur
        if d is None or not app.docs:
            return
        d = app.docs[0]
        r = d.b.find(EVENTS)
        if f == 10:
            app.expand_all = True
            app.goto(EVENTS, d)
            state['old'] = bytes(r.data(0))
        if f == 40:
            state['shown'] = app._obj_rewritable
            state['n'] = len(items(d))
            state['press'] = 'duplicate##dup'
        if f == 45:
            new = items(d)
            state['dup'] = (len(new) == state['n'] + 1
                            and gjson.to_json(app.types, None, new[1]) == gjson.to_json(app.types, None, new[0]))
            d.do_undo()
            app.changed(d, r)
            state['undo'] = bytes(d.b.find(EVENTS).data(0)) == state['old']
        if f == 50:                          # JSON: export, change the first event's first number, import
            node = genesys.Reader(app.types, d.b.e).read_resource(r)
            js = json.loads(gjson.dumps(app.types, app.labels, node, r.id))
            ev = js[next(k for k in js if isinstance(js[k], list))][0]
            key = next(k for k, v in ev.items() if not k.startswith('$') and isinstance(v, int) and v < 1000)
            ev[key] += 7
            state['json_key'], state['json_val'] = key, ev[key]
            p = os.path.join(TMP, 'events.json')
            with open(p, 'w', encoding='utf-8') as fh:
                json.dump(js, fh)
            state['json_ok'] = app.genesys_apply_json_file(d, r, p)
        if f == 55:
            node = genesys.Reader(app.types, d.b.e).read_resource(d.b.find(EVENTS))
            js = json.loads(gjson.dumps(app.types, app.labels, node, r.id))
            ev = js[next(k for k in js if isinstance(js[k], list))][0]
            state['json_back'] = ev.get(state['json_key'])
        if f == 60:                          # a text longer than its room (WEAPONS copy): written through the writer
            w = app.docs[1]
            hit = None
            for res in w.b.resources:
                if res.type != 0x15:
                    continue
                stack = [genesys.Reader(app.types, w.b.e).read_resource(res)]
                while stack and hit is None:
                    n = stack.pop()
                    for h, (o, fl) in n.locs.items():
                        if fl.type_id == genesys.STRINGBASE and not fl.flags & 3 and isinstance(n.fields.get(h), str):
                            hit = (res, o, n.fields[h])
                            break
                    for v in n.fields.values():
                        if isinstance(v, genesys.Node):
                            stack.append(v)
                        elif isinstance(v, list):
                            stack.extend(x for x in v if isinstance(x, genesys.Node))
                if hit:
                    break
            if hit:
                res, o, old = hit
                state['text'] = old + '_EXTENDED_BY_TEST'
                state['text_ok'] = app.write_string(w, res, o, state['text'])
                res = w.b.find(res.id)
                back = genesys.Reader(app.types, w.b.e).read_resource(res)
                state['text_back'] = any(isinstance(v, str) and v == state['text'] for v in iter_values(back))
                state['text_clean'] = app.genesys_rewritable(w, res)     # still a well-formed object
        if f == 70:                          # a UI script: Lua view, a constant changed, Apply (clicked), undo
            s3 = app.docs[2]
            state['lua_rid'] = next(x.id for x in s3.b.resources if x.type == 0x70 and len(x.data(0)) > 8
                                    and luabc.is_bytecode(textfile.read(x, s3.b.e)))
            state['lua_old'] = bytes(s3.b.find(state['lua_rid']).data(0))
            app.goto(state['lua_rid'], s3)
        if f == 80:
            st = app.lua_bc
            state['lua_shown'] = st.get('chunk') is not None and 'GETGLOBAL' in st.get('listing', '')
            if state['lua_shown']:
                k = st['chunk'].main.constants
                i = next(j for j, v in enumerate(k) if isinstance(v, str) and v)
                state['lua_new'] = k[i] + '_X'
                k[i] = state['lua_new']
                st['dirty'] = True
                state['press'] = 'Apply changes'
        if f == 85:
            s3 = app.docs[2]
            raw = textfile.read(s3.b.find(state['lua_rid']), s3.b.e)
            state['lua_applied'] = state.get('lua_new') in luabc.read(raw).main.constants
            s3.do_undo()
            app.changed(s3, s3.b.find(state['lua_rid']))
            state['lua_undo'] = bytes(s3.b.find(state['lua_rid']).data(0)) == state['lua_old']
        if f == 95:
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
    try:
        hello_imgui.run(params)
    finally:
        A.imgui.small_button = orig_button
        A.imgui.button = orig_big
    for f, tb in state['errors'][:3]:
        print(f'frame {f}:')
        print(tb)
    check(not state['errors'], f'no frame raised ({len(state["errors"])} errors)')
    check(state.get('shown') and state.get('n', 0) > 400,
          f'the event list ({state.get("n")} events) opens and can be rewritten')
    check(state.get('dup') and state.get('undo'), 'duplicate (clicked) inserts a copy of the event; undo removes it')
    check(state.get('json_ok') and state.get('json_back') == state.get('json_val'),
          f'an edited JSON imported: {state.get("json_key")} = {state.get("json_back")} (expected '
          f'{state.get("json_val")})')
    check(state.get('lua_shown') and state.get('lua_applied') and state.get('lua_undo'),
          'a UI script shows as Lua bytecode; a changed constant is applied (clicked) and undone')
    check(state.get('text_ok') and state.get('text_back') and state.get('text_clean'),
          f'a longer text written through the writer: {state.get("text")!r}')
    shutil.rmtree(TMP, ignore_errors=True)
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


def iter_values(node):
    stack = [node]
    while stack:
        n = stack.pop()
        for v in n.fields.values():
            if isinstance(v, genesys.Node):
                stack.append(v)
            elif isinstance(v, list):
                for x in v:
                    if isinstance(x, genesys.Node):
                        stack.append(x)
                    else:
                        yield x
            else:
                yield v


if __name__ == '__main__':
    sys.exit(main())
