"""python -m bndlx [FILES ...] [--select ID] [--screenshot out.png]"""
import argparse
import sys


def main():
    ap = argparse.ArgumentParser(prog='BNDLExplorer', description='NFS Most Wanted (2012) bundle explorer (PC / PS3)')
    ap.add_argument('files', nargs='*', help='.BNDL files to open')
    ap.add_argument('--select', help='resource id (hex) to select after opening')
    ap.add_argument('--screenshot', help='render the window, save it to this PNG and exit (for tests)')
    ap.add_argument('--tab', type=int, help=argparse.SUPPRESS)
    ap.add_argument('--expand', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('--view', choices=['details', 'icons'], help=argparse.SUPPRESS)
    ap.add_argument('--folder', help=argparse.SUPPRESS)
    ap.add_argument('--type', help=argparse.SUPPRESS)
    a = ap.parse_args()
    from .app import run
    sel = int(a.select.lower().replace('0x', ''), 16) if a.select else None
    run(a.files, a.screenshot, select=sel, tab=a.tab, expand=a.expand, view=a.view, folder=a.folder,
        type_filter=int(a.type, 16) if a.type else None)


if __name__ == '__main__':
    sys.exit(main())
