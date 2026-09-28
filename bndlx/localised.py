r"""LocalisedText (resource type 0x201, UI\LANGUAGE bundles): the game's strings.

  u32 1, u32 count, u32 ids offset (0x10), u32 entries offset
  u32 ids[count] (sorted; a few ids appear more than once, with the same text)
  {u32 offset, u32 length in characters including the NUL}[count]
  strings: UTF-16 in the platform's byte order, NUL-terminated, back to back in entry order; padded to 16.
Rebuilding an unchanged table gives the same bytes (checked on PC and PS3).
"""
import csv
import io
import struct


class StringTable:
    def __init__(self, entries=None, version=1):
        self.version = version
        self.entries = list(entries or [])        # [[id, text without NUL]]

    @classmethod
    def read(cls, res, e):
        c = res.data(0)
        ver, n, io_, eo = struct.unpack_from(e + '4I', c, 0)
        ids = struct.unpack_from(e + f'{n}I', c, io_)
        enc = 'utf-16-be' if e == '>' else 'utf-16-le'
        out = []
        for i, sid in enumerate(ids):
            off, ln = struct.unpack_from(e + 'II', c, eo + 8 * i)
            s = c[off:off + 2 * ln].decode(enc, 'replace')
            out.append([sid, s[:-1] if s.endswith('\0') else s])
        return cls(out, ver)

    def build(self, e):
        ents = sorted(self.entries, key=lambda x: x[0])
        n = len(ents)
        enc = 'utf-16-be' if e == '>' else 'utf-16-le'
        io_ = 0x10
        eo = io_ + 4 * n
        pos = eo + 8 * n
        table, blobs = [], []
        for sid, text in ents:
            s = (text + '\0').encode(enc)
            table.append(struct.pack(e + 'II', pos, len(s) // 2))
            blobs.append(s)
            pos += len(s)
        out = struct.pack(e + '4I', self.version, n, io_, eo) + struct.pack(e + f'{n}I', *[x[0] for x in ents])
        out += b''.join(table) + b''.join(blobs)
        return out + b'\0' * ((-len(out)) % 16)

    def set(self, sid, text):
        found = False
        for ent in self.entries:
            if ent[0] == sid:
                ent[1] = text
                found = True
        if not found:
            self.entries.append([sid, text])

    # -- text exchange (translations) ------------------------------------------------------------------------
    def to_csv(self):
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator='\n')
        w.writerow(['id', 'text'])
        done = set()
        for sid, text in self.entries:
            if sid not in done:
                done.add(sid)
                w.writerow([f'{sid:08X}', text])
        return buf.getvalue()

    def update_from_csv(self, text, add_new=False):
        """Apply `id,text` rows. Returns (changed, added, unknown ids)."""
        current = {}
        for sid, t in self.entries:
            current.setdefault(sid, t)
        changed = added = 0
        unknown = []
        for row in csv.reader(io.StringIO(text)):
            if len(row) < 2 or row[0].strip().lower() == 'id':
                continue
            try:
                sid = int(row[0].strip(), 16)
            except ValueError:
                continue
            if sid not in current:
                if not add_new:
                    unknown.append(sid)
                    continue
                added += 1
            elif current[sid] == row[1]:
                continue
            else:
                changed += 1
            current[sid] = row[1]
            self.set(sid, row[1])
        return changed, added, unknown
