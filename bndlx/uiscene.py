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


# behaviour parameters (the 'Parameters' text) as fields: type -> [(field, kind)], kind 'num' / 'word' / 'text'
PARAM_FIELDS = {
    0: [('direction', ('IN', 'OUT'))],                      # Fade IN / OUT [, EASIN]
    1: [('dx', 'num'), ('dy', 'num')],                      # Move by (dx, dy)
    2: [('x', 'num'), ('y', 'num')],                        # MoveTo the point
    3: [('x', 'num'), ('y', 'num')],                        # MoveFrom the point
    4: [('from %', 'num'), ('to %', 'num')],                # Scale
    5: [('dx', 'num'), ('dy', 'num')],                      # Approach from (dx, dy) away
    8: [('degrees', 'num')], 9: [('degrees', 'num')], 10: [('degrees', 'num')],
    11: [('sound id', 'num')],                              # PlaySound
    13: [('colour', 'text')],                               # Tint TO,<palette colour>
    17: [('from x %', 'num'), ('from y %', 'num'), ('to x %', 'num'), ('to y %', 'num')],
    18: [('to %', 'num')],                                  # FadeTo
}
EASE_WORDS = ('EASIN', 'EASEIN', 'EASEOUT', 'EASOUT', 'EASEINOUT', 'LINEAR')


def parse_params(typ, params):
    """{field: value} of a behaviour's parameters, plus 'extra' (the words left, e.g. EASIN)."""
    parts = [p.strip() for p in (params or '').split(',')]
    spec = PARAM_FIELDS.get(typ)
    out = {'extra': []}
    if spec is None:
        out['extra'] = [p for p in parts if p]
        return out
    if typ == 13:                                            # TO,<name>
        rest = [p for p in parts if p.upper() != 'TO']
        out['colour'] = rest[0] if rest else ''
        out['extra'] = rest[1:]
        return out
    if typ == 0:
        out['direction'] = next((p.upper() for p in parts if p.upper() in ('IN', 'OUT')), 'IN')
        out['extra'] = [p for p in parts if p and p.upper() not in ('IN', 'OUT')]
        return out
    nums = []
    for p in parts:
        try:
            nums.append(float(p))
        except ValueError:
            if p:
                out['extra'].append(p)
    for i, (name, _kind) in enumerate(spec):
        out[name] = nums[i] if i < len(nums) else 0.0
    return out


def _fmt_num(v):
    return f'{int(v)}' if float(v).is_integer() else f'{v:g}'


def format_params(typ, fields):
    spec = PARAM_FIELDS.get(typ)
    extra = [e for e in fields.get('extra', []) if e]
    if spec is None:
        return ','.join(extra)
    if typ == 13:
        return ','.join(['TO', fields.get('colour', '')] + extra)
    if typ == 0:
        return ','.join([fields.get('direction', 'IN')] + extra)
    return ','.join([_fmt_num(fields.get(name, 0.0)) for name, _k in spec] + extra)


class Anim:
    """What a transition does to an element at a time: alpha, offset, scale x / y, rotation, tint, decode."""
    __slots__ = ('alpha', 'dx', 'dy', 'sx', 'sy', 'drot', 'tint', 'decode')

    def __init__(self):
        self.alpha, self.dx, self.dy, self.sx, self.sy, self.drot = 1.0, 0.0, 0.0, 1.0, 1.0, 0.0
        self.tint = None
        self.decode = None


def animate(el, phase, t, palette=None):
    """Anim of the element at t ms into the transition `phase` ('in' / 'out' / 'idle'): Fade / FadeTo, Move,
    MoveTo / MoveFrom (points), Approach (from an offset), Scale / ScaleXY, Rotate / RotateTo / RotateFrom, Tint (to
    a palette colour), Decode (text appearing). Behaviours before their delay hold their start."""
    a = Anim()
    for b in el.behaviours.get(phase, []):
        p = behaviour(b)
        dur = max(p['duration'], 1.0)
        raw = (t - p['delay']) / dur
        if phase == 'idle' and dur > 1:
            raw = ((t - p['delay']) % dur) / dur if t >= p['delay'] else 0.0
        k = _ease(raw, p['ease'])
        typ = p['type']
        f = parse_params(typ, p['params'])
        if typ == 0:
            out = f['direction'] == 'OUT'
            a.alpha *= (1 - k) if out else k
        elif typ == 18:
            a.alpha *= 1 + (f['to %'] / 100.0 - 1) * k
        elif typ == 1:
            a.dx += f['dx'] * k
            a.dy += f['dy'] * k
        elif typ == 5:
            a.dx += f['dx'] * (1 - k)
            a.dy += f['dy'] * (1 - k)
        elif typ == 2:
            a.dx += (f['x'] - el.x) * k
            a.dy += (f['y'] - el.y) * k
        elif typ == 3:
            a.dx += (f['x'] - el.x) * (1 - k)
            a.dy += (f['y'] - el.y) * (1 - k)
        elif typ == 4:
            s = (f['from %'] + (f['to %'] - f['from %']) * k) / 100.0
            a.sx *= s
            a.sy *= s
        elif typ == 17:
            a.sx *= (f['from x %'] + (f['to x %'] - f['from x %']) * k) / 100.0
            a.sy *= (f['from y %'] + (f['to y %'] - f['from y %']) * k) / 100.0
        elif typ in (8, 9):
            a.drot += f['degrees'] * k
        elif typ == 10:
            a.drot += f['degrees'] * (1 - k)
        elif typ == 13 and palette:
            col = palette.get(f['colour'])
            if col is not None:
                base = list(el.tint or [1.0, 1.0, 1.0, 1.0])
                a.tint = [x + (y - x) * k for x, y in zip(base, col)]
        elif typ == 7:
            out = 'OUT' in [e.upper() for e in f['extra']]
            a.decode = (1 - k) if out else k
    return a


def decoded(text, k, seed=0):
    """Text k (0..1) of the way through the Decode effect: the first letters final, the rest scrambled."""
    import random
    n = int(round(len(text) * max(0.0, min(1.0, k))))
    rnd = random.Random(seed + int(k * 20))
    pool = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    return text[:n] + ''.join(c if c == ' ' else rnd.choice(pool) for c in text[n:])


# -- layout transitions (UILayoutInstanceParams.TimelineParameters) ------------------------------------------------
H_TIMELINE = 0xac3e9556
H_LAYOUT_IN = 0xaedfbe15
H_LAYOUT_OUT = 0x0b603df7
LAYOUT_FUNCS = {'FADE': ('delay', 'duration', 'direction', 'ease'),
                'APPROACH': ('delay', 'duration', 'dx', 'dy', 'ease'),
                'MOVE': ('delay', 'duration', 'dx', 'dy', 'ease')}
_FUNC = re.compile(r'\s*([A-Za-z_]+)\s*\(([^)]*)\)\s*,?')


def parse_layout_transition(text):
    """[(FUNC, [args as text])] of a layout transition, e.g. 'APPROACH (0, 200, -600, 0, EASEIN), FADE(0, 200, IN)'."""
    out = []
    for m in _FUNC.finditer(text or ''):
        out.append((m.group(1).upper(), [a.strip() for a in m.group(2).split(',')]))
    return out


def format_layout_transition(parts):
    return ', '.join(f'{fn}({", ".join(a for a in args)})' for fn, args in parts)


def layout_anim(text, phase, t):
    """Anim of a whole layout at t ms into its transition text (FADE / APPROACH / MOVE)."""
    a = Anim()
    for fn, args in parse_layout_transition(text):
        nums = []
        words = []
        for x in args:
            try:
                nums.append(float(x))
            except ValueError:
                words.append(x.upper())
        if len(nums) < 2:
            continue
        delay, dur = nums[0], max(nums[1], 1.0)
        ease = 1 if any('IN' in w and 'OUT' not in w and w.startswith('EAS') for w in words) else \
            3 if 'EASEINOUT' in words else 0
        k = _ease((t - delay) / dur, ease)
        if fn == 'FADE':
            out = 'OUT' in words or (phase == 'out' and 'IN' not in words)
            a.alpha *= (1 - k) if out else k
        elif fn == 'APPROACH' and len(nums) >= 4:
            a.dx += nums[2] * (1 - k)
            a.dy += nums[3] * (1 - k)
        elif fn == 'MOVE' and len(nums) >= 4:
            a.dx += nums[2] * k
            a.dy += nums[3] * k
    return a


def layout_length(text):
    end = 0.0
    for fn, args in parse_layout_transition(text):
        try:
            end = max(end, float(args[0]) + float(args[1]))
        except (ValueError, IndexError):
            pass
    return end


# -- sequences (UI timelines) -------------------------------------------------------------------------------------
H_ITEMS = name_hash('SequenceItems')
H_START = name_hash('StartTime')
H_END = name_hash('EndTime')
H_ENABLED = name_hash('EnabledBinding')
H_SEQ_BINDING = name_hash('Binding')
H_SEQ_MAX = name_hash('Binding_Max')
H_ITEM_LAYOUT = name_hash('Layout')
H_ITEM_PARAMS = name_hash('InstanceParams')
H_ITEM_SEQUENCE = name_hash('Sequence')
H_ITEM_WAVES = name_hash('Waves')
H_ITEM_MOVIE = name_hash('Movie')


class SeqItem:
    __slots__ = ('index', 'node', 'kind', 'start', 'end', 'enabled', 'layout', 'params', 'sequence', 'detail')


def sequence_items(types, seq):
    """[SeqItem] of a Sequence node: kind (type name without 'SequenceItem'), start / end, enabled binding and
    what it shows / plays (layout + instance params, nested sequence, waves, movie)."""
    out = []
    for i, n in enumerate(seq.fields.get(H_ITEMS) or []):
        if not isinstance(n, Node):
            continue
        it = SeqItem()
        it.index, it.node = i, n
        name = types.name(n.type).replace('SequenceItems.', '')
        it.kind = name[:-len('SequenceItem')] if name.endswith('SequenceItem') and name != 'SequenceItem' else name
        f = n.fields
        it.start, it.end = _num(f.get(H_START)), _num(f.get(H_END))
        it.enabled = f.get(H_ENABLED) or ''
        it.layout = _ref(f.get(H_ITEM_LAYOUT))
        it.params = _ref(f.get(H_ITEM_PARAMS))
        it.sequence = _ref(f.get(H_ITEM_SEQUENCE))
        waves = f.get(H_ITEM_WAVES)
        it.detail = ''
        if it.layout:
            it.detail = f'layout {it.layout & 0xFFFFFFFF}'
        elif it.sequence:
            it.detail = f'sequence {it.sequence & 0xFFFFFFFF}'
        elif isinstance(waves, list) and waves:
            it.detail = f'{len(waves)} sound(s)'
        elif f.get(H_ITEM_MOVIE):
            it.detail = f'movie {f.get(H_ITEM_MOVIE)}'
        out.append(it)
    return out


def sequence_axis(seq, items):
    """(binding or None, end): a binding-driven sequence runs over the binding's value 0..Binding_Max."""
    binding = seq.fields.get(H_SEQ_BINDING) or None
    end = max([it.end for it in items] + [0.0])
    if binding:
        end = max(end, _num(seq.fields.get(H_SEQ_MAX), 1.0))
    return binding, max(end, 0.1)


def load_palette(root):
    """{colour name: [r, g, b, a] 0..1} of the game's UI palette (UI/UICONFIG.BNDL, UIColourPalette)."""
    import os
    from .bundle import Bundle
    from .genesys import Reader, TypeDB
    path = os.path.join(root, 'UI', 'UICONFIG.BNDL') if root else None
    pal = {}
    if not path or not os.path.isfile(path):
        return pal
    try:
        b = Bundle.open(path)
        types = TypeDB()
        types.add_bundle(b)
        for r in b.resources:
            if r.type != 0x15 or not r.imports() or types.name(r.imports()[0].id) != 'UIColourPalette':
                continue
            n = Reader(types, b.e).read_resource(r)
            for c in n.fields.get(0xd46fdc01) or []:
                if isinstance(c, Node):
                    name, col = c.fields.get(0x263d15bc), c.fields.get(name_hash('Colour'))
                    if isinstance(name, str) and isinstance(col, list) and len(col) == 4:
                        pal[name] = [x / 255.0 for x in col]
    except Exception:
        pass
    return pal


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
