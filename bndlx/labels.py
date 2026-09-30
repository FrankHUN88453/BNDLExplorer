"""Display names for hashed Genesys field and enum value names.

The hash is known (gnames.name_hash); the names come from Find names (gnames.solve over the game's identifiers).
Names of four characters or fewer are their own hash (ASCII). A few environment fields are named here so that
they read well before a scan; names marked '(?)' describe what the field holds (its real name was not found).
Users can name any field (right click in the object view); those names are saved in the user config folder.
"""
import json
import os

from .genesys import hash_name
from .gnames import name_hash

KNOWN = {
    # EnvironmentKeyframe sub-structs and fields (checked against the hash, except '(?)')
    0x09E4015C: 'Camera', 0x3A39919D: 'Clouds', 0x6ADA1ED8: 'Fog (?)', 0x1461D185: 'Heat_Haze',
    0x86017919: 'LightRig', 0x2DEA5AB6: 'Mini_DOF', 0x7DFAB0CC: 'Sky (?)', 0x1DEF1B53: 'Vfx (?)',
    0x0CE52897: 'Weather', 0xF1A64644: 'BaseCloudTexture', 0xA6795CCB: 'ColourSun', 0x3460A645: 'Colour',
    0x848DC54E: 'FarDistance', 0xE1760947: 'KeyLightColour', 0x24C70974: 'SkyColorGradient',
    0x0696CFDE: 'SunColour', 0x2EC8DF83: 'SunScaleX', 0x3BEC5831: 'ExposureBracketMin',
    0x9DD29928: 'BloomWeightSmall', 0x90CD6830: 'Thunderstorm_Intensity', 0xE8EB5D48: 'Wet_Road_Puddlemap_Scale',
    0xCCDBC945: 'KeyFrames', 0xD65692B9: 'Keyframe', 0xDA6AC384: 'TimeOfDay', 0x33029C92: 'ColourCube',
}


def config_dir():
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    d = os.path.join(base, 'BNDLExplorer')
    os.makedirs(d, exist_ok=True)
    return d


class Labels:
    def __init__(self):
        self.found = {}          # names found by Find names (NameDB.gnames)
        self.path = os.path.join(config_dir(), 'labels.json')
        try:
            with open(self.path, encoding='utf-8') as f:
                self.user = {int(k, 16): v for k, v in json.load(f).items()}
        except (OSError, ValueError):
            self.user = {}

    def name(self, h):
        """Readable name of a field / enum value hash, or None."""
        return self.user.get(h) or self.found.get(h) or KNOWN.get(h) or hash_name(h)

    def source(self, h):
        """Where the name comes from, for the tooltip."""
        if h in self.user:
            ok = name_hash(self.user[h]) == h
            return 'your name' + (' (it is the real name: its hash matches)' if ok else '')
        if h in self.found:
            return 'the real name: found in the game\'s identifiers, its hash matches'
        if h in KNOWN:
            return 'named from the data (not the real name)' if KNOWN[h].endswith('(?)') else 'the real name'
        if hash_name(h):
            return 'short names are stored as they are'
        return 'unknown name (run Find names; right click to name it)'

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
