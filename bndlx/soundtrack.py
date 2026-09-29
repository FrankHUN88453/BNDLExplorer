"""Soundtrack editor back end.

UI\\SONGS\\SONGS.BNDL (PC retail) holds
  * Song objects (Genesys.Gen.Song, 48 bytes): 0x00 type import, 0x08 u32 schema hash, 0x0C handle (unused),
    0x14 handle -> the Wave (import), 0x1C u32 0, 0x20 u32 artist string id, 0x24 u32 own id (= low half of the
    resource id), 0x28 u32 title string id, 0x2C f32 (-0.9 .. 0.9 on the licensed songs, 0 on the others;
    probably a loudness trim);
  * SongList objects (Genesys.Gen.SongList, 36 bytes + data): 0x0C string {i32 offset relative to 0x0C, u32 length
    with the NUL} (a Spotify link on one list), 0x14 u32 (30567 on the two big lists), 0x18 u32 own id, 0x1C u32
    offset of the Song handle array (8 bytes per song, an import per slot), 0x20 u16 count, 0x22 / 0x23 bool8;
    the string follows at 0x24, the array at the next 4-byte boundary;
  * Wave resources (stream references, SOUND stream): the path `UI\\SONGS\\<wave id>.SPS` at 0x28.
The artist / title strings are LocalisedText entries (sorted by id) in every UI\\LANGUAGE\\<nnnn>.BNDL.
The two 42-song lists (1622455, referenced by GAMEMODES, and 1845669) hold the whole licensed soundtrack."""
import os
import shutil
import struct
from dataclasses import dataclass, field

from . import eal3
from .bundle import Bundle
from .localised import StringTable

GC = 0x01000000 << 32
SONG_TYPE = 0x0100000000002A36
LIST_TYPE = 0x0100000000002A37
T_GOBJECT, T_WAVE, T_STRINGS = 0x15, 0x81, 0x201
NEW_ID_BASE = 0x0FA00000          # ids for new songs / strings / waves (far above the game's own, ~2.4 million)
MAIN_LISTS = (1622455, 1845669)   # the two lists with the whole licensed soundtrack
LIST_NAMES = {1622455: 'Soundtrack A (used by GAMEMODES)', 1845669: 'Soundtrack B',
              1836280: 'Selection (used by GAMEMODES)', 1084858: 'Steve Hillage (Spotify link)',
              116490: 'Untitled 3', 116491: 'Untitled 6', 1836664: 'Sequence 3', 1870156: 'Sequence 1',
              1870247: 'Sequence 1 (Dead Sara)', 1870362: 'Sequence 1 (Crosses)', 2026345: 'Sequence 1 (The Who)',
              30437: 'Empty', 759015: 'Empty 2'}


class SoundtrackError(Exception):
    pass


def _imports(res, e):
    return {i.offset: i for i in res.imports()}


# ---------------------------------------------------------------------------------------------------------------
# objects
# ---------------------------------------------------------------------------------------------------------------
def parse_song(res, e):
    c = res.data(0)
    imps = _imports(res, e)
    return {'hash08': struct.unpack_from(e + 'I', c, 8)[0], 'wave': imps[0x14].id if 0x14 in imps else None,
            'u1c': struct.unpack_from(e + 'I', c, 0x1C)[0],
            'artist': struct.unpack_from(e + 'I', c, 0x20)[0], 'own': struct.unpack_from(e + 'I', c, 0x24)[0],
            'title': struct.unpack_from(e + 'I', c, 0x28)[0], 'trim': struct.unpack_from(e + 'f', c, 0x2C)[0]}


def build_song(s, e):
    """(chunk 0 body, imports) of a Song."""
    body = bytearray(0x30)
    struct.pack_into(e + 'I', body, 8, s['hash08'])
    struct.pack_into(e + '4I', body, 0x1C, s['u1c'], s['artist'], s['own'], s['title'])
    struct.pack_into(e + 'f', body, 0x2C, s['trim'])
    return bytes(body), [(SONG_TYPE, 0x80000000), (s['wave'], 0x80000014)]


def parse_list(res, e):
    c = res.data(0)
    imps = _imports(res, e)
    rel, ln = struct.unpack_from(e + 'iI', c, 0x0C)
    text = bytes(c[0x0C + rel:0x0C + rel + max(ln - 1, 0)]) if ln else b''
    arr, n = struct.unpack_from(e + 'I', c, 0x1C)[0], struct.unpack_from(e + 'H', c, 0x20)[0]
    return {'hash08': struct.unpack_from(e + 'I', c, 8)[0], 'text': text, 'f14': struct.unpack_from(e + 'I', c, 0x14)[0],
            'own': struct.unpack_from(e + 'I', c, 0x18)[0], 'flags': (c[0x22], c[0x23]),
            'songs': [imps[arr + 8 * k].id for k in range(n) if arr + 8 * k in imps]}


def build_list(li, e):
    body = bytearray(0x24)
    struct.pack_into(e + 'I', body, 8, li['hash08'])
    struct.pack_into(e + 'iI', body, 0x0C, 0x18, len(li['text']) + 1)
    struct.pack_into(e + 'II', body, 0x14, li['f14'], li['own'])
    body[0x22], body[0x23] = li['flags']
    body += li['text'] + b'\0'
    n = len(li['songs'])
    arr = 0
    if n:
        body += bytes(-len(body) % 4)
        arr = len(body)
        body += bytes(8 * n)
    struct.pack_into(e + 'I', body, 0x1C, arr)
    struct.pack_into(e + 'H', body, 0x20, n)
    imports = [(LIST_TYPE, 0x80000000)] + [(sid, 0x80000000 | (arr + 8 * k)) for k, sid in enumerate(li['songs'])]
    return bytes(body), imports


def build_stream_wave(template, path_text, head, e):
    """A stream-reference Wave (64+ bytes) like the game's song waves: `template` supplies the unknown bytes."""
    hdr = bytearray(template[:0x28])
    struct.pack_into(e + 'f', hdr, 0x14, head['samples'] * 1000.0 / head['rate'])
    hdr[0x24] = head['channels']
    data = bytes(hdr) + path_text.encode('latin1') + b'\0'
    return data + bytes(-len(data) % 16)


# ---------------------------------------------------------------------------------------------------------------
# the editor model
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class Song:
    rid: int
    fields: dict
    artist: str
    title: str
    file: str = ''                 # the .SPS file (absolute)
    duration: float = 0.0          # seconds
    channels: int = 2
    new_sps: bytes = None          # encoded audio not written yet
    added: bool = False


@dataclass
class Playlist:
    rid: int
    fields: dict
    name: str = ''
    songs: list = field(default_factory=list)


class Soundtrack:
    def __init__(self, root):
        self.root = root
        self.path = os.path.join(root, 'UI', 'SONGS', 'SONGS.BNDL')
        if not os.path.isfile(self.path):
            raise SoundtrackError(f'{self.path} not found')
        self.b = Bundle.open(self.path)
        if self.b.platform != 'PC':
            raise SoundtrackError('the soundtrack editor works on the PC game')
        self.e = self.b.e
        self.lang_paths = sorted(os.path.join(root, 'UI', 'LANGUAGE', f) for f in os.listdir(os.path.join(root, 'UI', 'LANGUAGE'))
                                 if f.lower().endswith('.bndl'))
        self.strings = {}
        lb = Bundle.open(self.lang_paths[0]) if self.lang_paths else None
        for r in (lb.resources if lb else []):
            if r.type == T_STRINGS:
                self.strings.update(StringTable.read(r, lb.e).entries)
        self.new_strings = {}        # string id -> text (added or changed)
        self.removed = set()         # Song resource ids to delete
        self.songs, self.lists = [], []
        waves = {r.id: r for r in self.b.resources if r.type == T_WAVE}
        self.wave_template = None
        for r in self.b.resources:
            if r.type != T_GOBJECT or not r.imports():
                continue
            t = r.imports()[0].id
            if t == SONG_TYPE:
                f = parse_song(r, self.e)
                s = Song(r.id, f, self.strings.get(f['artist'], ''), self.strings.get(f['title'], ''))
                w = waves.get(f['wave'])
                if w is not None:
                    wf = eal3.wave_fields(w.data(0), self.e)
                    s.duration, s.channels = wf['duration'] / 1000.0, wf['channels']
                    if wf['kind'] == 'stream':
                        s.file = os.path.join(root, wf['stream_ref'].replace('/', os.sep).replace('\\', os.sep))
                        if self.wave_template is None:
                            self.wave_template = bytes(w.data(0))
                            self.wave_res_template = w
                self.songs.append(s)
            elif t == LIST_TYPE:
                f = parse_list(r, self.e)
                self.lists.append(Playlist(r.id, f, LIST_NAMES.get(f['own'], f'List {f["own"]}'), list(f['songs'])))
        self.lists.sort(key=lambda li: (li.fields['own'] not in MAIN_LISTS, -len(li.songs), li.fields['own']))
        self.song_template = next((r for r in self.b.resources if r.type == T_GOBJECT and r.imports()
                                   and r.imports()[0].id == SONG_TYPE), None)
        self.dirty = False

    # -- queries ---------------------------------------------------------------------------------------------
    def song(self, rid):
        return next((s for s in self.songs if s.rid == rid), None)

    def lists_of(self, s):
        return [li for li in self.lists if s.rid in li.songs]

    def _used_ids(self):
        ids = {r.id & 0xFFFFFFFF for r in self.b.resources} | set(self.strings) | set(self.new_strings)
        return ids

    def new_id(self, count=1):
        """`count` consecutive unused ids at or above NEW_ID_BASE."""
        used = self._used_ids() | {s.fields['own'] for s in self.songs} | {s.fields['wave'] & 0xFFFFFFFF for s in self.songs}
        start = NEW_ID_BASE
        while any((start + k) in used for k in range(count)):
            start += count
        return start

    def _string_users(self, sid):
        return [s for s in self.songs if s.fields['artist'] == sid or s.fields['title'] == sid]

    # -- edits -----------------------------------------------------------------------------------------------
    def set_text(self, s, artist=None, title=None):
        """Change a song's artist / title; a string shared with other songs gets a new id for this song."""
        for key, text in (('artist', artist), ('title', title)):
            if text is None or text == getattr(s, key):
                continue
            sid = s.fields[key]
            if not sid or len(self._string_users(sid)) > 1:
                sid = self.new_id()
                s.fields[key] = sid
            self.new_strings[sid] = text
            setattr(s, key, text)
            self.dirty = True

    def encode(self, audio_path, quality=0.2):
        """An audio file -> (SPS bytes, header): stereo, the file's rate made an MPEG rate (as the game's songs)."""
        audio, rate = eal3.read_audio(audio_path)
        audio, rate = eal3.prepare_audio(audio, rate, 2, rate)
        return eal3.encode_sps(audio, rate, loop=False, loop_start=0, quality=quality)

    def add_song(self, audio_path, artist, title, lists=MAIN_LISTS, quality=0.2):
        if self.song_template is None or self.wave_template is None:
            raise SoundtrackError('no song to copy the layout from')
        sps, head = self.encode(audio_path, quality)
        own = self.new_id(4)
        artist_id, title_id, wave_own = own + 1, own + 2, own + 3
        f = dict(parse_song(self.song_template, self.e), u1c=0, artist=artist_id, own=own, title=title_id,
                 trim=0.0, wave=GC | wave_own)
        s = Song(GC | own, f, artist, title, os.path.join(self.root, 'UI', 'SONGS', f'{wave_own}.SPS'),
                 head['samples'] / head['rate'], head['channels'], sps, True)
        s.head = head
        self.new_strings[artist_id] = artist
        self.new_strings[title_id] = title
        self.songs.append(s)
        for li in self.lists:
            if li.fields['own'] in lists:
                li.songs.append(s.rid)
        self.dirty = True
        return s

    def replace_audio(self, s, audio_path, quality=0.2):
        sps, head = self.encode(audio_path, quality)
        s.new_sps, s.head = sps, head
        s.duration, s.channels = head['samples'] / head['rate'], head['channels']
        self.dirty = True

    def remove_song(self, s):
        for li in self.lists:
            li.songs = [x for x in li.songs if x != s.rid]
        self.songs.remove(s)
        if not s.added:
            self.removed.add(s.rid)
        self.dirty = True

    def set_member(self, s, li, on):
        if on and s.rid not in li.songs:
            li.songs.append(s.rid)
        elif not on and s.rid in li.songs:
            li.songs = [x for x in li.songs if x != s.rid]
        else:
            return
        self.dirty = True

    def move(self, li, rid, delta):
        i = li.songs.index(rid)
        j = i + delta
        if 0 <= j < len(li.songs):
            li.songs[i], li.songs[j] = li.songs[j], li.songs[i]
            self.dirty = True

    # -- saving ----------------------------------------------------------------------------------------------
    def apply(self):
        """Write the model into the bundle (in memory). Returns {file path: SPS bytes} still to be written."""
        b, e = self.b, self.e
        files = {}
        for rid in self.removed:
            r = b.find(rid)
            if r is not None:
                wave = parse_song(r, e)['wave']
                b.resources.remove(r)
                w = b.find(wave)
                if w is not None and not any(s.fields['wave'] == wave for s in self.songs):
                    b.resources.remove(w)
        self.removed.clear()
        for s in self.songs:
            body, imps = build_song(s.fields, e)
            r = b.find(s.rid)
            if r is None:
                r = self.song_template.copy()
                r.id = s.rid
                r.name = ''
                b.add(r)
            r.set_body(body, imps, e)
            if s.new_sps is not None:
                files[s.file] = s.new_sps
                w = b.find(s.fields['wave'])
                rel = os.path.relpath(s.file, self.root).replace(os.sep, '\\')
                if w is None:
                    w = self.wave_res_template.copy()
                    w.id = s.fields['wave']
                    w.name = ''
                    b.add(w)
                w.set_data(0, build_stream_wave(self.wave_template, rel, s.head, e))
        for li in self.lists:
            li.fields['songs'] = list(li.songs)
            body, imps = build_list(li.fields, e)
            b.find(li.rid).set_body(body, imps, e)
        return files

    def save(self, progress=None):
        """Write SONGS.BNDL, the language bundles (new / changed strings) and the new .SPS files; every existing
        file is copied to <name>.orig first (once). Returns the list of files written."""
        files = self.apply()
        written = []

        def backup(p):
            if os.path.exists(p) and not os.path.exists(p + '.orig'):
                shutil.copy2(p, p + '.orig')

        steps = 1 + len(self.lang_paths) + len(files)
        k = 0
        for p, data in files.items():
            backup(p)
            with open(p + '.tmp', 'wb') as f:
                f.write(data)
            os.replace(p + '.tmp', p)
            written.append(p)
            k += 1
            if progress:
                progress(k, steps)
        if self.new_strings:
            for lp in self.lang_paths:
                lb = Bundle.open(lp)
                changed = False
                for r in lb.resources:
                    if r.type != T_STRINGS:
                        continue
                    t = StringTable.read(r, lb.e)
                    for sid, text in self.new_strings.items():
                        t.set(sid, text)
                    r.set_data(0, t.build(lb.e))
                    changed = True
                if changed:
                    backup(lp)
                    lb.save(lp)
                    written.append(lp)
                k += 1
                if progress:
                    progress(k, steps)
            self.strings.update(self.new_strings)
            self.new_strings.clear()
        backup(self.path)
        self.b.save(self.path)
        written.append(self.path)
        for s in self.songs:
            s.new_sps = None
            s.added = False
        self.dirty = False
        return written
