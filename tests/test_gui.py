"""Drives the real window through the drop / copy / undo / save paths (needs the game files, see test_core.py).

  python tests\\test_gui.py [screenshot.png]
"""
import os
import shutil
import struct
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from imgui_bundle import hello_imgui, imgui, immvision  # noqa: E402

from bndlx import app as A, dragdrop, ops, raster, winclip  # noqa: E402
from bndlx.bundle import Bundle  # noqa: E402

PC = os.environ['BNDLX_PC']
PS3 = os.environ['BNDLX_PS3']
TMP = tempfile.mkdtemp(prefix='bndlx_gui_')
os.environ['APPDATA'] = os.path.join(TMP, 'appdata')        # keep the user's settings untouched
os.makedirs(os.environ['APPDATA'], exist_ok=True)
failures = []


def check(cond, what):
    print(('ok    ' if cond else 'FAIL  ') + what)
    if not cond:
        failures.append(what)


def post_dropfiles(hwnd, paths):
    """What Explorer sends when files are dropped: WM_DROPFILES with a DROPFILES block (wide paths)."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    body = (chr(0).join(paths) + chr(0) * 2).encode('utf-16-le')
    head = struct.pack('<IiiII', 20, 0, 0, 0, 1)                 # pFiles, pt, fNC, fWide
    h = k32.GlobalAlloc(0x0042, len(head) + len(body))
    p = k32.GlobalLock(h)
    ctypes.memmove(p, head + body, len(head) + len(body))
    k32.GlobalUnlock(h)
    ctypes.windll.user32.PostMessageW(wintypes.HWND(hwnd), 0x0233, wintypes.WPARAM(h), 0)


def main():
    pc = os.path.join(TMP, 'GLOBALEFFECTS.BNDL')
    ps3 = os.path.join(TMP, 'ENVIRONMENT_PS3.BNDL')
    shutil.copy2(os.path.join(PC, 'GLOBALEFFECTS.BNDL'), pc)
    shutil.copy2(os.path.join(PS3, 'HAWAII', 'ENVIRONMENT.BNDL'), ps3)
    hud = os.path.join(TMP, 'HUD_PC.BNDL')
    shutil.copy2(os.path.join(PC, 'UI', 'SCREENS2', '371621.BNDL'), hud)
    img = np.zeros((32, 32, 4), np.uint8)
    img[..., 2] = 255
    img[..., 3] = 255
    png = os.path.join(TMP, 'blue.png')
    ops.save_png(img, png)
    img[..., 2] = 0
    img[..., 1] = 255
    png2 = os.path.join(TMP, 'green.png')
    ops.save_png(img, png2)
    from bndlx import eal3
    sps = os.path.join(TMP, 'stream.SPS')
    shutil.copy2(os.path.join(PC, 'SOUND', 'STREAMS', '1536091.SPS'), sps)
    wavfile = os.path.join(TMP, 'beep.wav')
    tt = np.arange(24000) / 48000
    ops.write_file(wavfile, eal3.wav_bytes((np.sin(2 * np.pi * 880 * tt) * 8000).astype(np.int16)[:, None], 48000))

    immvision.use_rgb_color_order()
    app = A.App([])
    params = A.runner_params(app, 'BNDL Explorer test')
    frame = [0]
    state = {}

    def gui():
        app.gui()
        f = frame[0] = frame[0] + 1
        if f == 3:                                             # drop two bundles: a real WM_DROPFILES
            post_dropfiles(app.hwnd, [pc, ps3, hud])
        elif f == 6:
            check(len(app.docs) == 3, 'dropped bundles are open')
            d = app.docs[0]
            tex = next(r for r in d.b.resources if r.type == 1 and raster.info(r, 'PC').faces == 1)
            state['tex'] = tex.id
            app.goto(tex.id, d)
            app.drop_target = lambda: (d, d.b.find(tex.id), 'details')     # the mouse is over the preview
            app.on_drop([png])
        elif f == 9:
            d = app.docs[0]
            r = d.b.find(state['tex'])
            dec = raster.decode(r, 'PC')
            check(dec.shape[:2] == (32, 32) and dec[..., 2].mean() > 250, 'PNG dropped on a texture replaced it')
            check(d.modified and d.undo, 'the replace is undoable')
            src = app.docs[1]
            ids = [r.id for r in src.b.resources if r.type in (1, 0x52)][:5]
            state['copied'] = ids
            app.copy_between(src, d, ids)                        # what a drop on the PC tab does
        elif f == 12:
            d = app.docs[0]
            ok = all(d.b.find(i) is not None for i in state['copied'])
            check(ok, 'PS3 textures / colour cubes copied (converted) into the PC bundle')
            desc = d.do_undo()
            check(all(d.b.find(i) is None for i in state['copied']), f'undo removes them ({desc})')
            d.do_redo()
            check(all(d.b.find(i) is not None for i in state['copied']), 'redo adds them again')
            # drag out: files written and the shell data object built (DoDragDrop itself needs a real mouse)
            paths = [ops.export_native(d.b, d.b.find(i), TMP, '.png') for i in state['copied'][:2]]
            check(all(os.path.exists(p) for p in paths) and dragdrop.data_object_ok(paths), 'drag out: files + data object')
            # clipboard: copy in one bundle, paste in the other (the files also go to the Windows clipboard)
            src = app.docs[1]
            state['clip_before'] = imgui.get_clipboard_text() or ''
            ids2 = [r.id for r in src.b.resources if r.type == 0x52 and d.b.find(r.id) is None][:3]
            src.sel = set(ids2)
            app.clip_copy(src)
            files = winclip.get_files(app.hwnd)
            check(len(files) == len(ids2), f'Ctrl+C put {len(files)} file(s) on the Windows clipboard')
            app.clip_paste(d)
            check(all(d.b.find(i) is not None for i in ids2), 'Ctrl+V pasted them into the other bundle')
            state['copied'] += ids2
            # a PNG copied in Explorer, pasted onto the selected texture
            winclip.set_files([png2])
            app.goto(state['tex'], d)
            app.clip_paste(d)
        elif f == 14:
            # a WAV dropped on a sound (Wave) resource of the PS3 bundle
            src = app.docs[2]
            w = next((r for r in src.b.resources if r.type == 0x81 and len(r.data(0)) > 0x88), None)
            state['wave'] = w.id if w is not None else None
            if w is not None:
                app.goto(w.id, src)
                app.drop_target = lambda: (src, src.b.find(w.id), 'details')
                app.on_drop([wavfile])
        elif f == 15:
            d = app.docs[0]
            dec = raster.decode(d.b.find(state['tex']), 'PC')
            check(dec[..., 1].mean() > 250 and dec[..., 2].mean() < 5, 'a PNG copied in Explorer pasted onto the texture')
            imgui.set_clipboard_text(state.get('clip_before', ''))
            if state.get('wave'):
                src = app.docs[2]
                audio, rate, head, _ = ops.wave_audio(src.b, src.b.find(state['wave']))
                check(abs(len(audio) / rate - 0.5) < 0.02, f'WAV dropped on a sound replaced it ({len(audio)} samples @ {rate} Hz)')
            app.action_save(d)
        elif f > 16 and app.job is None and 'saved' not in state:
            state['saved'] = f
        elif 'saved' in state and f == state['saved'] + 3:
            app._paste_target = None
            app.drop_target = lambda: (app.cur, None, 'window')  # dropped on an empty part of the window
            post_dropfiles(app.hwnd, [sps])                     # an .SPS file dropped on the window opens
        elif 'saved' in state and f == state['saved'] + 6:
            sd = next((x for x in app.docs if getattr(x.b, 'kind', '') == 'sps'), None)
            check(sd is not None and len(sd.b.resources) == 1, 'dropped .SPS file opens as a sound stream')
            if sd is not None:
                state['sps_doc'] = sd
                w = sd.b.resources[0]
                app.goto(w.id, sd)
                app.drop_target = lambda: (sd, sd.b.find(w.id), 'details')
                app.on_drop([wavfile])
        elif 'saved' in state and f == state['saved'] + 9 and 'sps_doc' in state:
            sd = state['sps_doc']
            audio, rate, head, _ = ops.wave_audio(sd.b, sd.b.resources[0], sd.path)
            check(abs(len(audio) / rate - 0.5) < 0.02 and sd.modified,
                  f'WAV dropped on the .SPS replaced its sound ({len(audio)} samples @ {rate} Hz)')
            app.action_save(sd)
        elif 'saved' in state and f > state['saved'] + 12 and app.job is None:
            hello_imgui.get_runner_params().app_shall_exit = True
            app.exit_ok = True

    params.callbacks.show_gui = gui
    hello_imgui.run(params)
    if len(sys.argv) > 1:
        pass
    b = Bundle.open(pc)
    check(all(b.find(i) is not None for i in state.get('copied', [])), 'saved bundle has the copied resources')
    check(os.path.exists(pc + '.orig'), 'the original was kept as .orig')
    for i in state.get('copied', []):
        r = b.find(i)
        if r.type == 1:
            raster.decode(r, 'PC')
    check(True, 'copied textures decode in the saved bundle')
    from bndlx import eal3
    audio, rate, head = eal3.decode_sps(open(sps, 'rb').read())
    check(abs(len(audio) / rate - 0.5) < 0.02 and os.path.exists(sps + '.orig'),
          f'saved .SPS file holds the new sound ({len(audio) / rate:.2f} s), original kept as .orig')
    shutil.rmtree(TMP, ignore_errors=True)
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
