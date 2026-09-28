"""Display names for hashed Genesys field names.

The hash function of Genesys names is not known. Names of four characters or fewer are stored as ASCII and
shown as they are; a few environment fields have names inferred from the data ('(?)' = not confirmed). Users
can name any field (right click in the object view); those names are saved in the user config folder.
"""
import json
import os

from .genesys import hash_name

KNOWN = {
    # EnvironmentKeyframe sub-structs
    0x09E4015C: 'Camera', 0x3A39919D: 'Clouds', 0x6ADA1ED8: 'Fog', 0x1461D185: 'HeatHaze',
    0x86017919: 'LightRig', 0x2DEA5AB6: 'Mini_DOF', 0x7DFAB0CC: 'Sky', 0x1DEF1B53: 'Vfx', 0x0CE52897: 'Weather',
    0xF1A64644: 'SkyTexture', 0xA6795CCB: 'InScatteringColour (?)', 0x3460A645: 'FogColour (?)',
    0x848DC54E: 'FogDistance (?)', 0xE1760947: 'SunColour (?)', 0x24C70974: 'SkyGradient',
    0x0696CFDE: 'SunDiscColour (?)', 0x2EC8DF83: 'SunDiscSize (?)', 0x3BEC5831: 'ExposureBias (?)',
    0x9DD29928: 'Bloom (?)', 0x90CD6830: 'RoadWetness', 0xE8EB5D48: 'RainAmount',
    0xCCDBC945: 'Keys', 0xD65692B9: 'Keyframe', 0xDA6AC384: 'Hour', 0x33029C92: 'ColourCube',
}


def config_dir():
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    d = os.path.join(base, 'BNDLExplorer')
    os.makedirs(d, exist_ok=True)
    return d


class Labels:
    def __init__(self):
        self.path = os.path.join(config_dir(), 'labels.json')
        try:
            with open(self.path, encoding='utf-8') as f:
                self.user = {int(k, 16): v for k, v in json.load(f).items()}
        except (OSError, ValueError):
            self.user = {}

    def name(self, h):
        """Readable name of a field / enum value hash, or None."""
        return self.user.get(h) or KNOWN.get(h) or hash_name(h)

    def label(self, h):
        n = self.name(h)
        return f'{n}' if n else f'{h:08x}'

    def rename(self, h, text):
        text = text.strip()
        if text:
            self.user[h] = text
        else:
            self.user.pop(h, None)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({f'{k:08x}': v for k, v in sorted(self.user.items())}, f, indent=1)
