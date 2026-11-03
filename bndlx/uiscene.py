"""UI screens and layouts (UI/SCREENS2 bundles, Genesys objects): their elements for the live preview / editor.

Space: the HUD's 1280 x 720 (y down). A UILayout holds Elements (inline polymorphic objects, UIElement_*):
  PositionX / PositionY   the point of the element's rect given by Anchor (0..8 = Top_Left .. Bottom_Right)
  Width / Height, Rotation (degrees, clockwise, about the position)
  VisibilityBinding, Rotation_Binding, PositionX/Y_Binding   expressions on game values ('Signals.False' hides)
  Tint.Value (RGBA multiply), Opacity.Value (0..100)
  render data 9c8f13f0 {TextureHandle, quads 6b739c66}: unit quad renderables, pos 0..1 (y down) with the UVs
      (the crop of padded textures and the flips are in the quad, not in FlipHorizontal / FlipVertical)
  labels: DisplayText {Binding, localised string id cb33ade5}, TextProperties {TextStyle, justification unoO}
  masks (UIElement_Mask) clip the following elements; one with 528d6281 set ends the mask
  behaviours TransitionInBehaviour / TransitionOutBehaviour / IdleBehaviour:
      {Parameters, Sound, Delay ms, Duration ms, Type, Ease, Noise}; retail fades are Type Fade with the
      parameters 'IN' / 'IN,EASIN' / 'OUT', moves 'dx,dy', scales 'from,to' (percent), rotations degrees.
A Screen2 (the bundle's root) lists WidgetGroups -> widgets -> Layouts {Layout, LayoutInstanceParams}; the
retail layout transforms are HUD aspect corrections (0 at 16:9), so layouts draw at their own coordinates.
"""
import math
import re

from .gnames import name_hash
from .genesys import Node, Ref

W, H = 1280, 720


def _h(*names):
    return [name_hash(n) for n in names]


(H_ELEMENTS, H_X, H_Y, H_W, H_H, H_ROT, H_ANCHOR, H_VIS, H_ROTBIND, H_XBIND, H_YBIND, H_TINT, H_OPACITY,
 H_VALUE, H_TEXT, H_PROPS, H_STYLE, H_BINDING, H_FONTSIZE, H_GC, H_TEXHANDLE, H_NAME, H_LAYOUTS, H_LAYOUT,
 H_GROUPS, H_IN, H_OUT, H_IDLE, H_PARAMS, H_DELAY, H_DURATION, H_TYPE, H_EASE) = _h(
    'Elements', 'PositionX', 'PositionY', 'Width', 'Height', 'Rotation', 'Anchor', 'VisibilityBinding',
    'Rotation_Binding', 'PositionX_Binding', 'PositionY_Binding', 'Tint', 'Opacity', 'Value', 'DisplayText',
    'TextProperties', 'TextStyle', 'Binding', 'Font_Size', 'GameChangerID', 'TextureHandle', 'Name', 'Layouts',
    'Layout', 'WidgetGroups', 'TransitionInBehaviour', 'TransitionOutBehaviour', 'IdleBehaviour', 'Parameters',
    'Delay', 'Duration', 'Type', 'Ease')
H_RDATA = 0x9c8f13f0           # render data {TextureHandle, df023fd3, quads, a1df20a8}
H_QUADS = 0x6b739c66
H_STRING_ID = 0xcb33ade5       # UIElementBase.String: localised string id
H_JUST = 0x756e6f4f            # 'unoO': justification 0 LEFT, 1 CENTRE, 2 RIGHT, 3 FULL
H_MASK_END = 0x528d6281
H_SHAPE_COLOUR = 0x9e7e5867
H_STYLE_COLOUR = 0x80ea78f4
H_WIDGETS = 0xc7413006         # WidgetGroup's widgets
H_IMAGE_BIND = name_hash('ImageBinding')     # the picture comes from the game at run time (mirror, icons, ...)
H_MATERIAL = name_hash('Material')           # UIMaterial object {Material: GameChanger id of the material}
T_MINIMAP = 0x100000000002a7d                # UIElement_MiniMap (unnamed in the type data)
KIND_NAMES = {T_MINIMAP: 'MiniMap'}

BEHAVIOURS = (('in', H_IN, 'Transition in'), ('out', H_OUT, 'Transition out'), ('idle', H_IDLE, 'Idle'))
BEHAVIOUR_TYPES = ('Fade', 'Move', 'MoveTo', 'MoveFrom', 'Scale', 'Approach', 'Type 6', 'Decode', 'Rotate',
                   'RotateTo', 'RotateFrom', 'PlaySound', 'Type 12', 'Tint', 'CountUp', 'Shake',
                   'TroubleshootCurve', 'ScaleXY', 'FadeTo')
EASES = ('None', 'In', 'Out', 'InOut')
ANCHORS = ('Top left', 'Top middle', 'Top right', 'Centre left', 'Centre', 'Centre right', 'Bottom left',
           'Bottom middle', 'Bottom right')


class Element:
    """One entry of a layout's Elements, with the values the preview needs (the node keeps the offsets)."""
    __slots__ = ('index', 'node', 'kind', 'x', 'y', 'w', 'h', 'rot', 'anchor', 'vis', 'rot_bind', 'x_bind',
                 'y_bind', 'tint', 'opacity', 'texture', 'quad', 'text', 'string_id', 'style', 'just', 'mask_end',
                 'colour', 'behaviours', 'gc', 'image_bind', 'material')

    def __init__(self, index, node, kind):
        self.index = index
        self.node = node
        self.kind = kind

    @property
    def label(self):
        return f'{self.index}: {self.kind} {self.gc or ""}'.rstrip()


def short_kind(type_name):
    return type_name.replace('UIElement_', '').replace('Prototype', '')


def _num(v, default=0.0):
    return float(v) if isinstance(v, (int, float)) else default


def _sub(node, h):
    v = node.fields.get(h)
    return v if isinstance(v, Node) else None


def _ref(v):
    return v.id if isinstance(v, Ref) else None


def elements(types, layout):
    """[Element] of a UILayout node (only the entries that are objects)."""
    out = []
    for i, n in enumerate(layout.fields.get(H_ELEMENTS) or []):
        if not isinstance(n, Node):
            continue
        f = n.fields
        el = Element(i, n, KIND_NAMES.get(n.type) or short_kind(types.name(n.type)))
        el.gc = f.get(H_GC)
        el.x, el.y = _num(f.get(H_X)), _num(f.get(H_Y))
        el.w, el.h = _num(f.get(H_W)), _num(f.get(H_H))
        el.rot = _num(f.get(H_ROT))
        el.anchor = int(f.get(H_ANCHOR) or 0) if isinstance(f.get(H_ANCHOR), int) else 0
        el.vis = f.get(H_VIS) or ''
        el.rot_bind = f.get(H_ROTBIND) or ''
        el.x_bind = f.get(H_XBIND) or ''
        el.y_bind = f.get(H_YBIND) or ''
        tint = _sub(n, H_TINT)
        tv = tint.fields.get(H_VALUE) if tint is not None else None
        el.tint = [float(x) for x in tv] if isinstance(tv, list) and len(tv) == 4 else None
        op = _sub(n, H_OPACITY)
        el.opacity = _num(op.fields.get(H_VALUE), 100.0) if op is not None else 100.0
        rd = _sub(n, H_RDATA)
        el.texture = _ref(rd.fields.get(H_TEXHANDLE)) if rd is not None else None
        quads = rd.fields.get(H_QUADS) if rd is not None else None
        el.quad = _ref(quads[0]) if isinstance(quads, list) and quads else None
        txt = _sub(n, H_TEXT)
        el.text = (txt.fields.get(H_BINDING) or '') if txt is not None else None
        el.string_id = (txt.fields.get(H_STRING_ID) or 0) if txt is not None else 0
        props = _sub(n, H_PROPS)
        el.style = _ref(props.fields.get(H_STYLE)) if props is not None else None
        el.just = int(props.fields.get(H_JUST) or 0) if props is not None else 0
        el.mask_end = bool(f.get(H_MASK_END))
        col = f.get(H_SHAPE_COLOUR)
        el.colour = [float(x) for x in col] if isinstance(col, list) and len(col) == 4 else None
        el.behaviours = {k: [b for b in (f.get(h) or []) if isinstance(b, Node)] for k, h, _ in BEHAVIOURS}
        el.image_bind = f.get(H_IMAGE_BIND) or ''
        el.material = _ref(f.get(H_MATERIAL))
        out.append(el)
    return out


def behaviour(node):
    """{params, delay, duration, type, ease} of a behaviour node."""
    f = node.fields
    return {'params': f.get(H_PARAMS) or '', 'delay': _num(f.get(H_DELAY)), 'duration': _num(f.get(H_DURATION)),
            'type': int(f.get(H_TYPE) or 0), 'ease': int(f.get(H_EASE) or 0)}


# -- screens --------------------------------------------------------------------------------------------------
def screen_widgets(types, root):
    """[(widget name, [(layout id, params id)])] of a Screen2 node: the widgets that show layouts."""
    out = []

    def walk(v, depth):
        if depth > 8:
            return
        if isinstance(v, Node):
            lay = v.fields.get(H_LAYOUTS)
            if isinstance(lay, list) and lay:
                ids = []
                for e in lay:
                    if isinstance(e, Node):
                        lid = _ref(e.fields.get(H_LAYOUT))
                        if lid:
                            ids.append((lid, _ref(e.fields.get(name_hash('LayoutInstanceParams')))))
                if ids:
                    out.append((v.fields.get(H_NAME) or types.name(v.type), ids))
            for x in v.fields.values():
                if isinstance(x, (Node, list)):
                    walk(x, depth + 1)
        elif isinstance(v, list):
            for x in v:
                walk(x, depth + 1)

    walk(root, 0)
    return out


# -- geometry -------------------------------------------------------------------------------------------------
def anchor_frac(anchor):
    a = anchor if 0 <= anchor <= 8 else 0
    return (a % 3) / 2.0, (a // 3) / 2.0


def corners(x, y, w, h, anchor, rot, scale=1.0):
    """Corners of the element rect (top-left, top-right, bottom-right, bottom-left) in layout space."""
    ax, ay = anchor_frac(anchor)
    w, h = w * scale, h * scale
    pts = [(-ax * w, -ay * h), ((1 - ax) * w, -ay * h), ((1 - ax) * w, (1 - ay) * h), (-ax * w, (1 - ay) * h)]
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    return [(x + px * c - py * s, y + px * s + py * c) for px, py in pts]


def unit_point(x, y, w, h, anchor, rot, u, v, scale=1.0):
    """The layout-space point at (u, v) of the rect (0..1 each, y down)."""
    ax, ay = anchor_frac(anchor)
    px, py = (u - ax) * w * scale, (v - ay) * h * scale
    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    return x + px * c - py * s, y + px * s + py * c


def inside(pts, px, py):
    """Point in a convex quad (any winding)."""
    sign = 0
    for i in range(4):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % 4]
        cr = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
        if cr:
            if sign and (cr > 0) != (sign > 0):
                return False
            sign = cr
    return True


# -- bindings -------------------------------------------------------------------------------------------------
DEFAULT_STATE = {
    'Players.LocalPlayer.GetPlayerRPM': 5200,
    'Players.LocalPlayer.GetPlayerVehicleGear': 3,
    'Players.GetLocalCompetitor.GetPlayerSpeed': 38.0,
    'Players.LocalPlayer.GetPlayerDirection': 30.0,
    'Players.LocalPlayer.NitrousState.NitrousAmount': 0.7,
    'Players.LocalPlayer.HeatProgress': 0.35,
    'Players.LocalPlayer.HeatLevelInt': 2,
    'Players.LocalPlayer.DamageBarState': 0,
    'Language.IsMetric': 1,
    'CurrentRoad.Visible': 1,
    'GameWorld.Compass': 35.0,
}
CONSTANTS = {'Signals.False': 0.0, 'Signals.True': 1.0, 'Signals.One': 1.0, 'Signals.Zero': 0.0}
_LP = 'Players.LocalPlayer.'
# game situations for the preview (made-up values of the bindings the HUDs read)
STATE_PRESETS = {
    'Free roam': {_LP + 'IsInChase': 0, _LP + 'IsInCooldown': 0, _LP + 'IsInChaseOrCooldown': 0,
                  _LP + 'PursuitBarEnum': 0, _LP + 'HeatLevelInt': 1, _LP + 'Jammed': 0, _LP + 'DamageBarState': 0,
                  _LP + 'NitrousState.UsingNitrous': 0, 'CurrentRoad.Visible': 1},
    'Pursuit': {_LP + 'IsInChase': 1, _LP + 'IsInCooldown': 0, _LP + 'IsInChaseOrCooldown': 1,
                _LP + 'PursuitBarEnum': 1, _LP + 'HeatLevelInt': 4, _LP + 'HeatProgress': 0.6,
                _LP + 'ChaseHeat': 4, _LP + 'CopsInChase': 5, _LP + 'PursuitBarProgress': 0.55,
                _LP + 'StaticBustProgress': 0.0, 'CurrentRoad.Visible': 1},
    'Busted soon': {_LP + 'IsInChase': 1, _LP + 'IsInChaseOrCooldown': 1, _LP + 'PursuitBarEnum': 1,
                    _LP + 'HeatLevelInt': 5, _LP + 'StaticBustProgress': 0.75, _LP + 'CopsInChase': 8},
    'Cooldown': {_LP + 'IsInChase': 0, _LP + 'IsInCooldown': 1, _LP + 'IsInChaseOrCooldown': 1,
                 _LP + 'PursuitBarEnum': 2, _LP + 'HeatLevelInt': 3, _LP + 'PursuitBarProgress': 0.35,
                 _LP + 'ChanceOfEscape': 0.6},
    'Nitro': {_LP + 'NitrousState.UsingNitrous': 1, _LP + 'NitrousState.NitrousAmount': 0.45,
              _LP + 'GetPlayerRPM': 7400, 'Players.GetLocalCompetitor.GetPlayerSpeed': 62.0},
    'Damaged': {_LP + 'DamageBarState': 2, _LP + 'IsHealthRecharging': 1},
    'Jammed': {_LP + 'Jammed': 1},
    'Miles (not metric)': {'Language.IsMetric': 0},
}


def live_state(t):
    """Values that move with the time t (seconds): revs sweep, speed and gear follow, the car turns."""
    phase = (t % 6.0) / 6.0
    rpm = 1200 + 6800 * (phase * 4 % 1.0) ** 0.7
    gear = 1 + int(phase * 4)
    return {_LP + 'GetPlayerRPM': rpm, _LP + 'GetPlayerVehicleGear': gear,
            'Players.GetLocalCompetitor.GetPlayerSpeed': 8.0 + 60.0 * phase,
            _LP + 'GetPlayerDirection': (t * 25.0) % 360.0, 'GameWorld.Compass': (t * 25.0) % 360.0,
            _LP + 'NitrousState.NitrousAmount': 0.5 + 0.5 * math.sin(t)}
_EXPR = re.compile(r'\s*([A-Za-z_][\w\.]*(?:\[[^\]]*\])?(?:\.[\w\.]+)*)\s*(?:\((.*)\))?\s*$')


def binding_path(expr):
    """The value a binding reads ('Players.LocalPlayer.X(LERP, ...)' -> 'Players.LocalPlayer.X')."""
    m = _EXPR.match(expr or '')
    return m.group(1) if m else None


def bindings(els):
    """The distinct binding paths the elements use (for the game state panel)."""
    out = set()
    for el in els:
        for ex in (el.vis, el.rot_bind, el.x_bind, el.y_bind, el.text):
            p = binding_path(ex)
            if p and '.' in p and p not in CONSTANTS and not p.startswith('Signals.GetIntValue'):
                out.add(p)
    return sorted(out)


class Evaluator:
    """A small subset of the binding language against a made-up game state (preview only)."""

    def __init__(self, state=None):
        self.state = dict(DEFAULT_STATE, **(state or {}))

    def value(self, expr):
        if not expr:
            return None
        m = _EXPR.match(expr)
        if not m:
            return None
        path, args = m.group(1), m.group(2) or ''
        if path in CONSTANTS:
            return CONSTANTS[path]
        gi = re.match(r'Signals\.GetIntValue\[(-?\d+)\]$', path)
        if gi:
            return float(gi.group(1))
        v = self.state.get(path)
        if v is None:
            return None
        v = float(v)
        opts = {}
        nums = []
        for part in args.split(','):
            part = part.strip()
            if ':' in part:
                k, x = part.split(':', 1)
                try:
                    opts[k.strip().upper()] = float(x)
                except ValueError:
                    opts[k.strip().upper()] = x.strip()
            elif re.fullmatch(r'-?\d+(\.\d+)?', part):
                nums.append(float(part))
            elif part:
                opts[part.upper()] = True
        if 'LERP' in opts:
            lo, hi = opts.get('MINVALUE', 0), opts.get('MAXVALUE', 1)
            rlo, rhi = opts.get('MINRANGE', 0), opts.get('MAXRANGE', 1)
            t = 0 if hi == lo else min(1, max(0, (v - lo) / (hi - lo)))
            v = rlo + t * (rhi - rlo)
        if isinstance(opts.get('MULT'), float):
            v *= opts['MULT']
        if isinstance(opts.get('DIV'), float) and opts['DIV']:
            v /= opts['DIV']
        if isinstance(opts.get('ADD'), float):
            v += opts['ADD']
        if isinstance(opts.get('GTEQ'), float):
            v = float(v >= opts['GTEQ'])
        if 'EQUALS' in opts:
            v = float(bool(nums) and v == nums[0])
        if 'NOT' in opts:
            v = float(not v)
        return v

    def visible(self, expr, unknown=True):
        """Shown by its visibility binding (an unknown value counts as `unknown`)."""
        if not expr:
            return True
        v = self.value(expr)
        return unknown if v is None else bool(v)


# -- transitions ----------------------------------------------------------------------------------------------
def _ease(t, ease):
    t = min(1.0, max(0.0, t))
    if ease == 1:
        return t * t
    if ease == 2:
        return 1 - (1 - t) * (1 - t)
    if ease == 3:
        return t * t * (3 - 2 * t)
    return t


def _floats(params):
    out = []
    for p in (params or '').split(','):
        try:
            out.append(float(p))
        except ValueError:
            pass
    return out


def phase_length(el, phase):
    return max((behaviour(b)['delay'] + behaviour(b)['duration'] for b in el.behaviours.get(phase, [])), default=0.0)


def animate(el, phase, t):
    """(alpha, dx, dy, scale, drot) of the element at t ms into the transition `phase` ('in' / 'out' / 'idle').
    Fade, Move / MoveFrom / MoveTo / Approach, Scale, Rotate / RotateFrom / RotateTo are shown; others are not."""
    alpha, dx, dy, scale, drot = 1.0, 0.0, 0.0, 1.0, 0.0
    for b in el.behaviours.get(phase, []):
        p = behaviour(b)
        dur = max(p['duration'], 1.0)
        raw = (t - p['delay']) / dur
        if phase == 'idle' and dur > 1:
            raw = ((t - p['delay']) % dur) / dur if t >= p['delay'] else 0.0
        k = _ease(raw, p['ease'])
        typ, words = p['type'], p['params'].upper().split(',')
        nums = _floats(p['params'])
        if typ == 0:                                            # Fade IN / OUT
            out = 'OUT' in words or (phase == 'out' and 'IN' not in words)
            alpha *= (1 - k) if out else k
        elif typ == 18 and nums:                               # FadeTo percent
            alpha *= 1 + (nums[0] / 100.0 - 1) * k
        elif typ in (1, 3, 5) and len(nums) >= 2:              # Move / MoveFrom / Approach: from the offset to 0
            f = (1 - k) if phase != 'out' else k
            dx += nums[0] * f
            dy += nums[1] * f
        elif typ == 2 and len(nums) >= 2:                      # MoveTo
            dx += nums[0] * k
            dy += nums[1] * k
        elif typ in (4, 17) and len(nums) >= 2:                # Scale from,to (percent)
            scale *= (nums[0] + (nums[1] - nums[0]) * k) / 100.0
        elif typ in (8, 9) and nums:                           # Rotate / RotateTo (degrees)
            drot += nums[0] * k
        elif typ == 10 and nums:                               # RotateFrom
            drot += nums[0] * (1 - k)
    return alpha, dx, dy, scale, drot


# -- the game's screens -----------------------------------------------------------------------------------------
KNOWN_HUDS = {371621: 'free roam', 604153: 'race', 371678: 'Speed Run', 371670: 'Ambush', 371720: 'online free roam',
              371725: 'online team', 1019407: 'online', 1019757: 'online', 1019814: 'online'}
SCREEN_FOLDERS = (('UI', 'SCREENS2'), ('UI', 'SCREENS'))


def screen_info(path):
    """{path, id, name, root, layouts, hud, platform} of a UI screen bundle (None when it has no Screen2)."""
    from .bundle import Bundle
    from .genesys import Reader, TypeDB
    b = Bundle.open(path)
    types = TypeDB()
    types.add_bundle(b)
    cands = [b.find(b.root_id)] if b.root_id else []
    cands += [r for r in b.resources if r.type == 0x15]
    for r in cands:
        if r is None or r.type != 0x15 or r.missing:
            continue
        imps = r.imports()
        if not imps or types.name(imps[0].id) != 'Screen2':
            continue
        n = Reader(types, b.e).read_resource(r)
        widgets = screen_widgets(types, n)
        gc = n.fields.get(H_GC)
        name = n.fields.get(name_hash('BundleName')) or ''
        hud = 'hud' in name.lower() or gc in KNOWN_HUDS or any(w[0] == 'HUD' for w in widgets)
        return {'path': path, 'id': gc, 'name': name, 'root': r.id, 'hud': bool(hud), 'platform': b.platform,
                'layouts': sum(len(ls) for _, ls in widgets), 'note': KNOWN_HUDS.get(gc, '')}
    return None


def screen_files(roots):
    import os
    out = []
    for root in roots:
        for parts in SCREEN_FOLDERS:
            folder = os.path.join(root, *parts)
            if os.path.isdir(folder):
                out += [os.path.join(folder, n) for n in sorted(os.listdir(folder)) if n.upper().endswith('.BNDL')]
    return out


def scan_screens(roots, progress=None, cache=None):
    """screen_info of every screen bundle under the game folders; cache {path: [size, mtime, info]} is used and
    updated (unchanged files are not read again)."""
    import os
    cache = cache if cache is not None else {}
    files = screen_files(roots)
    out = []
    for i, p in enumerate(files):
        if progress:
            progress(i, len(files))
        try:
            st = os.stat(p)
        except OSError:
            continue
        hit = cache.get(p)
        if hit and hit[0] == st.st_size and hit[1] == int(st.st_mtime):
            info = hit[2]
        else:
            try:
                info = screen_info(p)
            except Exception:
                info = None
            cache[p] = [st.st_size, int(st.st_mtime), info]
        if info:
            out.append(info)
    return out
