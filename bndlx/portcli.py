"""python -m bndlx port SOURCE TARGET OUT [options]: port a PS3 bundle into the same bundle of the PC game.

The game folder of TARGET is scanned first (cached afterwards); the PS3 folder of SOURCE too when it can be found, so
the schemas of both builds are known. Nothing is written with --dry-run."""
import argparse
import sys

from . import port


def main(argv):
    ap = argparse.ArgumentParser(prog='bndlx port', description=__doc__.split('\n\n')[0])
    ap.add_argument('source', help='the bundle to take data from (e.g. the PS3 prototype ENVIRONMENT.BNDL)')
    ap.add_argument('target', help='the game\'s bundle of the same name (PC); it decides which resources exist')
    ap.add_argument('out', help='where to write the ported bundle (never the target itself)')
    ap.add_argument('--game', help='PC game folder (default: found from the target)')
    ap.add_argument('--source-game', help='folder of the source build (default: found from the source)')
    ap.add_argument('--lenient', action='store_true',
                    help='also convert Genesys objects whose schema leaves data unexplained when that data is clearly '
                         'float32 (a guess; the report counts them)')
    ap.add_argument('--dry-run', action='store_true', help='scan and report, write nothing')
    ap.add_argument('--no-cache', action='store_true', help='ignore the cached scan of the game folders')
    a = ap.parse_args(argv)

    si, di = port.inspect(a.source), port.inspect(a.target)
    todo = port.problems(a.source, si, a.target, di, a.out)
    if todo:
        for t in todo:
            print(t.split(': ', 1)[-1] if t.startswith('Step') else t, file=sys.stderr)
        return 2
    print(f'source: {a.source} ({si[0]}, {si[1]} resources)')
    print(f'target: {a.target} ({di[0]}, {di[1]} resources)', flush=True)
    last = [-1]

    def progress(i, n):
        pct = int(100 * i / max(n, 1))
        if pct != last[0] and pct % 10 == 0:
            last[0] = pct
            print(f'  {pct}%', flush=True)

    res, notes = port.plan(a.source, a.target, lenient=a.lenient, progress=progress, status=print,
                           game_dir=a.game, source_game_dir=a.source_game, use_cache=not a.no_cache)
    print('\n'.join(notes))
    print()
    print(res.text())
    if a.dry_run:
        print('\n(dry run: nothing written)')
        return 0
    rep = port.write(res, a.out)
    print(f'\nWritten: {a.out}\nReport:  {rep}')
    return 0
