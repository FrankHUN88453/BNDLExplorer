"""Data bindings of UI elements ('Players.LocalPlayer.HeatProgress(LERP, ...)'): which exist in the retail game.

A binding path counts as valid when it appears in the retail UI data (GenesysObjects and TextFiles of the UI
bundles), or every one of its segments is a word in NFS13.exe (GetPlayerRPM is only in the exe and works), or it is
a widget's own (Widget.*, resolved per widget at run time). Prototype elements read sources retail removed
(CopAmmo, RechargedFraction[4], the impact shakes): pasted into the game such an element can make the widget
vanish, so they are neutralised to 'Signals.GetIntValue[0]' (constant 0: hidden), after the known renames.
"""
import json
import os
import re

PATH = re.compile(r'\b([A-Z][A-Za-z0-9_]*(?:\[[^\]]*\])?(?:\.[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?)+)')
RENAMES = {'Players.LocalPlayer.CooldownProgress': 'Players.LocalPlayer.PursuitBarProgress'}
NEUTRAL = 'Signals.GetIntValue[0]'
UI_FOLDERS = (('UI', 'SCREENS2'), ('UI', 'LAYOUTS'), ('UI', 'SEQUENCES'), ('UI', 'SEQUENCEITEMS'), ('UI',))


def _norm(p):
    return re.sub(r'\[[^\]]*\]', '', p)


class Bindings:
    """The binding paths a retail PC game folder knows (cached in the config folder)."""

    def __init__(self, root, cache_dir=None):
        self.root = root
        self.known = set()
        self.exe_words = set()
        cache = os.path.join(cache_dir, 'known_bindings.json') if cache_dir else None
        files = self._files()
        stamp = [len(files), sum(int(os.path.getmtime(p)) for p in files) if files else 0]
        hit = None
        if cache and os.path.isfile(cache):
            try:
                with open(cache, encoding='utf-8') as fh:
                    hit = json.load(fh)
            except (OSError, ValueError):
                hit = None
        if hit and hit.get('root') == root and hit.get('stamp') == stamp:
            self.known = set(hit['known'])
            self.exe_words = set(hit['exe'])
            return
        self._scan(files)
        if cache:
            try:
                with open(cache, 'w', encoding='utf-8') as fh:
                    json.dump({'root': root, 'stamp': stamp, 'known': sorted(self.known),
                               'exe': sorted(self.exe_words)}, fh)
            except OSError:
                pass

    def _files(self):
        out = []
        for parts in UI_FOLDERS:
            folder = os.path.join(self.root, *parts)
            if os.path.isdir(folder):
                out += [os.path.join(folder, n) for n in sorted(os.listdir(folder))
                        if n.upper().endswith('.BNDL') and not n.lower().startswith('lol')]
        return out

    def _scan(self, files):
        from .bundle import Bundle
        pat = re.compile(PATH.pattern.encode())
        for p in files:
            try:
                b = Bundle.open(p)
            except Exception:
                continue
            for r in b.resources:
                if r.type in (0x15, 0x70) and not r.missing:
                    try:
                        data = bytes(r.data(0))
                    except Exception:
                        continue
                    self.known.update(_norm(m.group(1).decode('latin1')) for m in pat.finditer(data))
        exe = os.path.join(self.root, 'NFS13.exe')
        if os.path.isfile(exe):
            with open(exe, 'rb') as fh:
                self.exe_words = {w.decode() for w in re.findall(rb'[A-Za-z_][A-Za-z0-9_]{2,}', fh.read())}

    def valid(self, path):
        n = _norm(path)
        if n in self.known or n in RENAMES or n.startswith('Widget.') or n.startswith('Signals.'):
            return True
        return bool(self.exe_words) and all(seg in self.exe_words for seg in n.split('.'))

    def bad(self, text):
        return [m.group(1) for m in PATH.finditer(text or '') if not self.valid(m.group(1))]

    def fix(self, text):
        """(new text, notes): renames applied; an expression reading an unknown source becomes NEUTRAL."""
        notes = []
        for old, new in RENAMES.items():
            if old in (text or ''):
                text = text.replace(old, new)
                notes.append(f'{old} -> {new}')
        bad = self.bad(text)
        if bad:
            notes.append(f'{_norm(bad[0])}: not in the retail game -> {NEUTRAL}')
            return NEUTRAL, notes
        return text, notes


def fix_node(node, checker):
    """Fix every text of a node tree in place; -> notes."""
    from .genesys import Node
    notes = []

    def walk(v):
        if isinstance(v, Node):
            for k, x in list(v.fields.items()):
                if isinstance(x, str) and x and PATH.search(x):
                    nx, nn = checker.fix(x)
                    if nn:
                        v.fields[k] = nx
                        notes.extend(nn)
                else:
                    walk(x)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                if isinstance(x, str) and x and PATH.search(x):
                    nx, nn = checker.fix(x)
                    if nn:
                        v[i] = nx
                        notes.extend(nn)
                else:
                    walk(x)
    walk(node)
    return notes
