"""python -m bndlx [FILES ...] [--select ID] [--screenshot out.png]"""
import argparse
import sys


def main():
    import multiprocessing
    multiprocessing.freeze_support()          # the name scan uses worker processes (also in the exe)
    ap = argparse.ArgumentParser(prog='BNDLExplorer', description='NFS Most Wanted (2012) bundle explorer (PC / PS3)')
    ap.add_argument('files', nargs='*', help='.BNDL files to open')
    ap.add_argument('--select', help='resource id (hex) to select after opening')
    ap.add_argument('--screenshot', help='render the window, save it to this PNG and exit (for tests)')
    ap.add_argument('--tab', type=int, help=argparse.SUPPRESS)
    ap.add_argument('--expand', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('--view', choices=['details', 'icons'], help=argparse.SUPPRESS)
    ap.add_argument('--folder', help=argparse.SUPPRESS)
    ap.add_argument('--type', help=argparse.SUPPRESS)
    ap.add_argument('--scan-names', nargs='+', metavar='FOLDER',
                    help='find resource names in these folders without opening the window, then exit')
    a = ap.parse_args()
    if a.scan_names:
        import glob
        import json
        import os
        from .names import NameDB
        db = NameDB.load()
        exes = [p for f in a.scan_names for p in glob.glob(os.path.join(f, 'NFS13.exe'))]
        st = db.scan(a.scan_names, exe_paths=exes)
        if sys.stdout is not None:
            print(json.dumps(st))
        return 0
    from .app import run
    sel = int(a.select.lower().replace('0x', ''), 16) if a.select else None
    run(a.files, a.screenshot, select=sel, tab=a.tab, expand=a.expand, view=a.view, folder=a.folder,
        type_filter=int(a.type, 16) if a.type else None)


if __name__ == '__main__':
    sys.exit(main())
