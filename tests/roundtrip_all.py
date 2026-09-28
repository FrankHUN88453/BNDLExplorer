"""Open and re-save (in memory) every bundle under the given folders; the bytes must be identical.

usage: python tests/roundtrip_all.py FOLDER [FOLDER ...]
"""
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bndlx.bundle import Bundle  # noqa: E402


def check(path):
    try:
        with open(path, 'rb') as f:
            d = f.read()
        if d[:4] != b'bnd2':
            return path, 'skip'
        b = Bundle.from_bytes(d)
        if b.truncated:
            return path, 'truncated (read only)'
        out = b.to_bytes()
        if out == d:
            return path, 'ok'
        n = next((i for i in range(min(len(out), len(d))) if out[i] != d[i]), min(len(out), len(d)))
        return path, f'DIFF at {n:#x} (len {len(out)} vs {len(d)})'
    except Exception as e:
        return path, f'ERROR {type(e).__name__}: {e}'


def main():
    files = []
    for root in sys.argv[1:]:
        for dp, _, fn in os.walk(root):
            files += [os.path.join(dp, f) for f in fn if f.upper().endswith('.BNDL')]
    t = time.time()
    bad = 0
    counts = {}
    with ProcessPoolExecutor() as ex:
        for path, res in ex.map(check, files, chunksize=8):
            key = res.split(' ')[0]
            counts[key] = counts.get(key, 0) + 1
            if key not in ('ok', 'skip', 'truncated'):
                bad += 1
                if bad <= 30:
                    print(path, res)
    print(counts, f'{time.time() - t:.0f} s')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
