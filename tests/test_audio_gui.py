"""Wave preview playback in the real window: Play, jump to the middle, and the sound stops when another item is
selected. A fake player records the calls, so nothing is heard. Needs BNDLX_PC (see test_core.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from imgui_bundle import hello_imgui, immvision  # noqa: E402

from bndlx import app as A  # noqa: E402

PC = os.environ['BNDLX_PC']
failures = []


def check(cond, what):
    print(('ok    ' if cond else 'FAIL  ') + what)
    if not cond:
        failures.append(what)


class FakePlayer:
    def __init__(self):
        self.calls = []
        self.opened = False
        self.key = None
        self.paused = False
        self.pos = 0

    def play(self, audio, rate, start=0, key=None):
        self.calls.append(('play', int(start)))
        self.opened, self.key, self.paused, self.pos = True, key, False, int(start)

    @property
    def done(self):
        return False

    @property
    def playing(self):
        return self.opened

    def position(self):
        return self.pos if self.opened else None

    def pause(self):
        self.calls.append(('pause',))
        self.paused = True

    def resume(self):
        self.calls.append(('resume',))
        self.paused = False

    def stop(self):
        if self.opened:
            self.calls.append(('stop',))
        self.opened, self.key = False, None


def main():
    songs = os.path.join(PC, 'UI', 'SONGS')
    first, second = sorted(f for f in os.listdir(songs) if f.lower().endswith('.sps'))[:2]
    immvision.use_rgb_color_order()
    app = A.App([os.path.join(songs, first)])
    app.player = FakePlayer()
    params = A.runner_params(app, 'BNDL Explorer audio test')
    frame = [0]
    state = {}

    def gui():
        app.gui()
        f = frame[0] = frame[0] + 1
        d, r = app.focused()
        key = ('wave', d.uid, r.id, id(r.data(0))) if r is not None else None
        hit = app.gcache.get(key) if key else None
        if 'key' not in state and isinstance(hit, tuple):
            audio, rate, _, _ = hit
            state.update(key=key, n=len(audio))
            app.player.play(audio, rate, app.wave_cursor.get(key, 0), key)       # the Play button
        elif 'key' in state and 'seek' not in state:
            audio, rate, _, _ = app.gcache[state['key']]
            app.wave_seek(state['key'], audio, rate, len(audio) // 2)            # releasing a click at 50 %
            state['seek'] = f
        elif 'seek' in state and 'switched' not in state and f > state['seek'] + 2:
            state['calls'] = list(app.player.calls)
            app.open_path(os.path.join(songs, second))
            state['switched'] = f
        elif 'switched' in state and f > state['switched'] + 3:
            state['after'] = (app.player.opened, app.player.calls[len(state['calls']):])
            hello_imgui.get_runner_params().app_shall_exit = True
            app.exit_ok = True
        elif f > 2000:
            hello_imgui.get_runner_params().app_shall_exit = True
            app.exit_ok = True

    params.callbacks.show_gui = gui
    hello_imgui.run(params)
    calls = state.get('calls', [])
    check(len(calls) == 2 and calls[1][0] == 'play' and abs(calls[1][1] - state['n'] // 2) <= 1,
          f'jumping to the middle continues playing there ({calls})')
    check(state.get('after', (True,))[0] is False and ('stop',) in state.get('after', (0, []))[1],
          'selecting another sound stops the playing one')
    print(f'{len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
