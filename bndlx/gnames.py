"""Names of Genesys fields and enum values from their hashes.

The hash (found in the PS3 prototype's debug build, rw::core::stdc::CalculateCrc32): MSB-first CRC-32 with the
polynomial 0x04C11DB7 on the name's bytes, the register starting as the complement of the first four bytes and
the result complemented. Names of four characters or fewer therefore hash to themselves (big-endian ASCII).

The names are not stored anywhere in the game data, but the identifiers of the game's code and data contain them:
every identifier of NFS13.exe (and of the PS3 debug SELFs, when present), the type names and strings of the
bundles, cut into contiguous runs of their words (GetMaxDisplacement -> MaxDisplacement), are hashed and matched
against the hashes of the types' fields. A hash that several candidates match is given the one that shares the
most words with its type and its sibling fields; array count fields are tried as <array>Count; last, pairs of
words that occur in found names are combined. About 0.1 % of the single matches and ~2 % of the pairs can be
chance collisions.
"""
import collections
import re

import numpy as np

_T = []
for _i in range(256):
    _c = _i << 24
    for _ in range(8):
        _c = ((_c << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if _c & 0x80000000 else (_c << 1) & 0xFFFFFFFF
    _T.append(_c)
_TA = np.array(_T, np.uint32)
IDENT = re.compile(rb'[A-Za-z_][A-Za-z0-9_]{2,120}')
TOKEN = re.compile(r'[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+')


def name_hash(s):
    """The Genesys hash of a field / enum value / type name."""
    b = s.encode('latin1') if isinstance(s, str) else bytes(s)
    if len(b) <= 4:
        return int.from_bytes(b, 'big')
    r = ~int.from_bytes(b[:4], 'big') & 0xFFFFFFFF
    for c in b[4:]:
        r = (((r << 8) | c) & 0xFFFFFFFF) ^ _T[r >> 24]
    return ~r & 0xFFFFFFFF


def hash_many(names):
    """{name: hash} for many names at once (vectorised by length)."""
    out = {}
    by_len = collections.defaultdict(list)
    for n in names:
        by_len[len(n)].append(n)
    for ln, group in by_len.items():
        if ln <= 4:
            for n in group:
                out[n] = name_hash(n)
            continue
        a = np.frombuffer(''.join(group).encode('latin1'), np.uint8).reshape(len(group), ln)
        r = ~((a[:, 0].astype(np.uint32) << 24) | (a[:, 1].astype(np.uint32) << 16)
              | (a[:, 2].astype(np.uint32) << 8) | a[:, 3].astype(np.uint32))
        for k in range(4, ln):
            r = ((r << np.uint32(8)) | a[:, k].astype(np.uint32)) ^ _TA[r >> np.uint32(24)]
        for n, h in zip(group, (~r).tolist()):
            out[n] = h
    return out


def identifiers(data):
    """Identifier-like runs of a binary (executables, bundles)."""
    return {m.group(0).decode('latin1') for m in IDENT.finditer(data)}


def tokens(name):
    return [t[0].upper() + t[1:] for t in TOKEN.findall(name)]


def candidates(idents, max_words=6):
    """Every identifier and every contiguous run of its words (CamelCase joined, and with underscores)."""
    out = set()
    for w in idents:
        if len(w) > 4 and w.isascii():
            out.add(w)
        for piece in re.split(r'[._:]+|(?<=[a-z])(?=\d{2,})', w):
            toks = TOKEN.findall(piece)
            if not toks or len(toks) > 12:
                continue
            for i in range(len(toks)):
                for j in range(i + 1, min(len(toks), i + max_words) + 1):
                    s = ''.join(toks[i:j])
                    if len(s) > 4:
                        out.add(s)
                        out.add(s[0].upper() + s[1:])
                        if j - i > 1:
                            out.add('_'.join(toks[i:j]))
        for part in w.split('_'):
            if len(part) > 4:
                out.add(part)
    return {c for c in out if c.isascii()}


def _state(prefix):
    b = prefix.encode('latin1')
    r = ~int.from_bytes(b[:4], 'big') & 0xFFFFFFFF
    for c in b[4:]:
        r = (((r << 8) | c) & 0xFFFFFFFF) ^ _T[r >> 24]
    return r


def pairs(vocab, targets, known):
    """Names of two words of vocab (joined as is and with '_') whose hash is in targets and not in known."""
    tset = np.array(sorted(targets), np.uint32)
    out = {}
    for sep in ('', '_'):
        pre = [p + sep for p in vocab if len(p + sep) >= 4]
        if not pre:
            continue
        st = np.array([_state(p) for p in pre], np.uint32)
        for s in vocab:
            r = st.copy()
            for c in s.encode('latin1'):
                r = ((r << np.uint32(8)) | np.uint32(c)) ^ _TA[r >> np.uint32(24)]
            h = ~r
            for i in np.nonzero(np.isin(h, tset))[0]:
                hh = int(h[i])
                if hh not in known and hh not in out:
                    out[hh] = pre[i] + s
    return out


def solve(types, idents, progress=None):
    """{hash: name} for the fields and enum values of `types` ({type name: [(field hash, count field hash or
    None)]}) from the identifiers `idents`."""
    targets = {h for fl in types.values() for h, _ in fl}
    targets |= {c for fl in types.values() for _, c in fl if c}
    owners = collections.defaultdict(set)
    for tn, fl in types.items():
        for h, c in fl:
            owners[h].add(tn)
            if c:
                owners[c].add(tn)
    cands = candidates(idents)
    if progress:
        progress(1, 4)
    verbatim = set(idents)
    hits = collections.defaultdict(set)
    for n, h in hash_many(cands).items():
        if h in targets:
            hits[h].add(n)
    if progress:
        progress(2, 4)
    found = {h: next(iter(v)) for h, v in hits.items() if len(v) == 1}

    def context(h):
        words = set()
        for tn in owners.get(h, ()):
            words.update(tokens(tn.split('.')[-1]))
            for fh, _ in types.get(tn, ()):
                if fh != h and fh in found:
                    words.update(tokens(found[fh]))
        return words

    for h, v in hits.items():
        if len(v) > 1:
            ctx = context(h)
            found[h] = max(sorted(v), key=lambda n: (len(set(tokens(n)) & ctx), n in verbatim, '_' not in n, -len(n)))
    # array counts: <array>Count
    for tn, fl in types.items():
        for h, c in fl:
            if c and c not in found and h in found:
                for form in (found[h] + 'Count', 'Num' + found[h], found[h] + 'Size'):
                    if name_hash(form) == c:
                        found[c] = form
                        break
    if progress:
        progress(3, 4)
    vocab = collections.Counter(t for n in found.values() for t in tokens(n))
    words = [t for t, _ in vocab.most_common(2000)]
    found.update(pairs(words, {h for h in targets if h not in found and not ascii_name(h)}, found))
    return {h: n for h, n in found.items() if not ascii_name(h)}


def ascii_name(h):
    """Short names are their own hash: nothing to look up."""
    b = h.to_bytes(4, 'big').lstrip(b'\0')
    return len(b) >= 2 and re.fullmatch(rb'[A-Za-z][A-Za-z0-9_]*', b) is not None
