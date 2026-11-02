"""Drives the Port window (bndlx/port_ui.py) without a display: a small fake `imgui` stands in for Dear ImGui and
the test "clicks" buttons by label. Like the real library, the fake ignores clicks on disabled widgets, so this checks
that Write really cannot be pressed before a preview, that nothing is written before Write, and that the whole flow
works. No game files needed.  python tests\\test_port_ui.py
"""
import os
import sys
import tempfile
import types

TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TESTS))
sys.path.insert(0, TESTS)
os.environ.setdefault('APPDATA', tempfile.mkdtemp(prefix='bndlx_appdata_'))


class _Enum:
    def __getattr__(self, name):
        return 1


class FakeImgui:
    """Just enough of imgui_bundle.imgui for port_ui.py. clicks: labels (substrings) pressed this frame."""

    def __init__(self):
        self.clicks = set()
        self.typed = {}
        self.close = False
        self.disabled = 0
        self.texts = []
        self.buttons = {}              # label -> enabled, as drawn in the last frame
        self._stack = []
        for name in ('Cond_', 'TableFlags_', 'TableColumnFlags_', 'WindowFlags_', 'Col_', 'Dir', 'Key'):
            setattr(self, name, _Enum())

    def __getattr__(self, name):         # every other call: draw nothing, answer "no"
        return lambda *a, **k: None

    @staticmethod
    def ImVec2(x, y):
        return (x, y)

    @staticmethod
    def ImVec4(*c):
        return tuple(c)

    def _pressed(self, label):
        hit = any(c in label for c in self.clicks)
        return hit and not self.disabled

    def begin(self, title, p_open=None, flags=0):
        return True, not self.close

    def begin_disabled(self, flag=True):
        self.disabled += 1 if flag else 0
        self._stack.append(bool(flag))

    def end_disabled(self):
        if self._stack.pop():
            self.disabled -= 1

    def button(self, label, size=None):
        self.buttons[label] = not self.disabled
        return self._pressed(label)

    def radio_button(self, label, active):
        return self._pressed(label)

    def checkbox(self, label, value):
        return (True, not value) if self._pressed(label) else (False, value)

    def input_text_with_hint(self, label, hint, text):
        if label in self.typed:
            return True, self.typed.pop(label)
        return False, text

    def begin_table(self, *a, **k):
        return True

    def get_frame_height_with_spacing(self):
        return 20.0

    class ListClipper:
        display_start = 0
        display_end = 0

        def begin(self, n, *a):
            self.n, self._more = n, True

        def step(self):
            if not self._more:
                return False
            self._more, self.display_end = False, self.n
            return True

    def _text(self, *args):
        self.texts.append(args[-1] if isinstance(args[-1], str) else '')

    text = text_disabled = text_wrapped = text_unformatted = _text

    def text_colored(self, color, text):
        self.texts.append(text)


fake = FakeImgui()
mod = types.ModuleType('imgui_bundle')
mod.imgui = fake
sys.modules['imgui_bundle'] = mod

import test_port as T  # noqa: E402  (synthetic bundle builders; its own checks are not run here)
from bndlx import convert, genesys, port  # noqa: E402
from bndlx import port_ui  # noqa: E402
from bndlx.bundle import Bundle  # noqa: E402

failures = []


def check(cond, what):
    print(('ok    ' if cond else 'FAIL  ') + what)
    if not cond:
        failures.append(what)


class Doc:
    def __init__(self, path):
        self.path = path
        self.b = Bundle.open(path)


class Host(port_ui.PortUI):
    """The parts of the App that the window uses; jobs run at once instead of in a thread."""

    def __init__(self):
        self.port_ui = None
        self.busy = None
        self.job = None
        self.cfg = {}
        self.status = ''
        self.cur = None
        self.opened = []
        self.modal = None

    def run_job(self, text, fn, done=None):
        self.busy = [text, 0.0]
        try:
            res = fn(lambda i, n: None)
        finally:
            self.busy = None
        if done:
            done(res)

    def open_path(self, p):
        self.opened.append(p)

    def frame(self, *clicks, **typed):
        fake.clicks, fake.typed, fake.texts = set(clicks), dict(typed), []
        self.port_window()
        fake.clicks = set()


def main():
    base = tempfile.mkdtemp(prefix='bndlx_portui_')
    game = T.make_pc(1)
    gpath = os.path.join(base, 'game', 'ENV.BNDL')
    os.makedirs(os.path.dirname(gpath))
    game.save(gpath)
    ps3, _ = convert.convert_bundle(T.make_pc(2), 'PS3')
    spath = os.path.join(base, 'ps3', 'ENV.BNDL')
    os.makedirs(os.path.dirname(spath))
    ps3.save(spath)

    h = Host()
    h.frame()
    check(h.port_ui is None, 'nothing is drawn while the window is closed')

    h.open_port(Doc(spath))                     # as from the menu with a PS3 bundle open
    ui = h.port_ui
    check(ui['src'] == spath and ui['src_info'] == ('PS3', 3), 'the open PS3 bundle becomes the source')
    h.frame()
    check(fake.buttons.get('Scan the game and preview') is False, 'Scan is disabled until every step is filled in')
    check(any('Step 2' in t for t in fake.texts), 'the window says what is still missing (step 2)')

    h.frame(**{'##port_dst': gpath})            # typing the game bundle's path
    check(ui['dst'] == gpath and ui['dst_info'] == ('PC', 4), 'typing the game bundle checks the file')
    check(ui['out'] == os.path.join(base, 'game', 'ENV_ported.BNDL'), 'a result file name is suggested next to it')
    h.frame()
    check(fake.buttons.get('Scan the game and preview') is True, 'Scan is enabled once the steps are filled in')

    h.frame('Write the ported bundle')
    check(not os.path.exists(ui['out']), 'Write does nothing before a preview (and writes nothing)')

    h.frame('Scan the game and preview')
    res = ui['result']
    check(res is not None and len(res.replaced) == 3 and len(res.kept) == 1, 'preview: 3 taken from the source, 1 kept (the material)')
    check(not os.path.exists(ui['out']), 'the preview writes nothing')
    h.frame()
    check(any('3 resource(s) will come from the source' in t for t in fake.texts), 'the summary is shown')
    check(any('0x10' in t for t in fake.texts), 'the table lists the resources')
    check(fake.buttons.get('Write the ported bundle') is True, 'Write is enabled after the preview')

    h.frame('Kept as the game has it')          # filter radio
    check(ui['filter'] == 2 and {d.action for d in h.port_rows(ui, res)} == {'keep'}, 'the filter shows only kept resources')
    h.frame(**{'##port_search': 'material'})
    check([d.type for d in h.port_rows(ui, res)] == [2], 'the search narrows the table')
    h.frame(**{'##port_search': ''})

    h.frame('Write the ported bundle')
    check(os.path.isfile(ui['out']) and ui['saved'] == ui['out'], 'Write saves the file')
    check(os.path.isfile(ui['out'] + '.port.txt'), 'the per-resource report is saved next to it')
    out = Bundle.open(ui['out'])
    check([(r.id, r.type) for r in out.resources] == [(r.id, r.type) for r in Bundle.open(gpath).resources],
          'the saved file has the game bundle\'s table')
    h.frame()
    check(any(t.startswith('Saved:') for t in fake.texts), 'the window shows where it was saved')
    h.frame('Open the result here')
    check(h.opened == [ui['out']], '"Open the result here" opens the file in the explorer')

    h.frame(**{'##port_out': gpath})            # result == game bundle: refused
    h.frame()
    check(fake.buttons.get('Scan the game and preview') is False and ui['result'] is None,
          'a result name equal to the game bundle disables Scan (and drops the old preview)')
    check(any('must not replace the game bundle' in t for t in fake.texts), 'and says why')

    h.frame(**{'##port_dst': spath})            # same platform on both sides
    h.frame()
    check(any('different platforms' in t for t in fake.texts), 'two bundles of one platform are refused')

    h.frame(**{'##port_dst': os.path.join(base, 'missing.BNDL')})
    h.frame()
    check(any('does not exist' in t for t in fake.texts), 'a missing file is reported in red')

    ui['lenient'] = False
    h.frame('Also convert objects')
    check(ui['lenient'] is True, 'the float-guess checkbox toggles')

    fake.close = True
    h.frame()
    fake.close = False
    check(h.port_ui is None, 'closing the window clears its state')

    # opening again with a PC bundle current does not take it as the source
    h.open_port(Doc(gpath))
    check(h.port_ui['src'] == '', 'a PC bundle is not offered as the source')

    print()
    print(f'{len(failures)} failure(s)' if failures else 'all tests passed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
