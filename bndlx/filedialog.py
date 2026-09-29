"""Native Windows dialogs (comdlg32 / shell32) owned by the editor window, so they open in front of it."""
import ctypes
import os
from ctypes import wintypes

OFN_OVERWRITEPROMPT = 0x00000002
OFN_NOCHANGEDIR = 0x00000008
OFN_ALLOWMULTISELECT = 0x00000200
OFN_PATHMUSTEXIST = 0x00000800
OFN_FILEMUSTEXIST = 0x00001000
OFN_EXPLORER = 0x00080000
OFN_ENABLESIZING = 0x00800000


class OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ('lStructSize', wintypes.DWORD), ('hwndOwner', wintypes.HWND), ('hInstance', wintypes.HINSTANCE),
        ('lpstrFilter', wintypes.LPCWSTR), ('lpstrCustomFilter', wintypes.LPWSTR), ('nMaxCustFilter', wintypes.DWORD),
        ('nFilterIndex', wintypes.DWORD), ('lpstrFile', wintypes.LPWSTR), ('nMaxFile', wintypes.DWORD),
        ('lpstrFileTitle', wintypes.LPWSTR), ('nMaxFileTitle', wintypes.DWORD), ('lpstrInitialDir', wintypes.LPCWSTR),
        ('lpstrTitle', wintypes.LPCWSTR), ('Flags', wintypes.DWORD), ('nFileOffset', wintypes.WORD),
        ('nFileExtension', wintypes.WORD), ('lpstrDefExt', wintypes.LPCWSTR), ('lCustData', wintypes.LPARAM),
        ('lpfnHook', ctypes.c_void_p), ('lpTemplateName', wintypes.LPCWSTR), ('pvReserved', ctypes.c_void_p),
        ('dwReserved', wintypes.DWORD), ('FlagsEx', wintypes.DWORD),
    ]


class BROWSEINFOW(ctypes.Structure):
    _fields_ = [('hwndOwner', wintypes.HWND), ('pidlRoot', ctypes.c_void_p), ('pszDisplayName', wintypes.LPWSTR),
                ('lpszTitle', wintypes.LPCWSTR), ('ulFlags', wintypes.UINT), ('lpfn', ctypes.c_void_p),
                ('lParam', wintypes.LPARAM), ('iImage', ctypes.c_int)]


BUNDLES = [('Bundles and sound streams (*.BNDL, *.BUNDLE, *.SPS)', '*.BNDL;*.bndl;*.BUNDLE;*.bundle;*.SPS;*.sps'),
           ('Bundles (*.BNDL, *.BUNDLE)', '*.BNDL;*.bndl;*.BUNDLE;*.bundle'), ('Sound streams (*.SPS)', '*.SPS;*.sps'),
           ('All files', '*.*')]
SPS = [('Sound streams (*.SPS)', '*.SPS;*.sps'), ('All files', '*.*')]
IMAGES = [('Images and DDS (*.png, *.dds, *.tga, *.jpg, *.bmp)', '*.png;*.dds;*.tga;*.jpg;*.jpeg;*.bmp'),
          ('All files', '*.*')]
DDS = [('DirectDraw Surface (*.dds)', '*.dds')]
PNG = [('PNG image (*.png)', '*.png')]
RES = [('BNDL Explorer resource (*.bres)', '*.bres'), ('All files', '*.*')]
BIN = [('Raw data (*.bin)', '*.bin'), ('All files', '*.*')]
TEXT = [('Text (*.txt, *.json, *.xml)', '*.txt;*.json;*.xml'), ('All files', '*.*')]
CSV = [('CSV (*.csv)', '*.csv'), ('All files', '*.*')]
AUDIO = [('Audio (*.wav, *.flac, *.ogg, *.mp3, *.aiff, *.sps)', '*.wav;*.flac;*.ogg;*.mp3;*.aif;*.aiff;*.sps;*.SPS'),
         ('All files', '*.*')]
WAV = [('WAV (*.wav)', '*.wav')]
ANY = [('All files', '*.*')]


def _start(path):
    """(initial folder, initial file name) from a remembered path; the nearest existing folder if it is gone."""
    if not path:
        return None, ''
    path = os.path.normpath(path)
    name = ''
    folder = path
    if not os.path.isdir(path):
        folder, name = os.path.split(path)
    while folder and not os.path.isdir(folder):
        parent = os.path.dirname(folder)
        if parent == folder:
            folder = ''
            break
        folder = parent
    return (folder or None), name


def _owner():
    user32 = ctypes.windll.user32
    return user32.GetActiveWindow() or user32.GetForegroundWindow()


def _dialog(title, start, filters, save, multi=False, default_ext=None):
    comdlg32 = ctypes.windll.comdlg32
    folder, name = _start(start)
    buf = ctypes.create_unicode_buffer(name, 65536)
    filt = ''.join(f'{d}\0{p}\0' for d, p in filters) + '\0'
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.hwndOwner = _owner()
    ofn.lpstrFilter = filt
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR)
    ofn.nMaxFile = len(buf)
    ofn.lpstrInitialDir = folder
    ofn.lpstrTitle = title
    ofn.Flags = OFN_EXPLORER | OFN_ENABLESIZING | OFN_NOCHANGEDIR | OFN_PATHMUSTEXIST
    if save:
        ofn.Flags |= OFN_OVERWRITEPROMPT
        ofn.lpstrDefExt = default_ext
        ok = comdlg32.GetSaveFileNameW(ctypes.byref(ofn))
        return buf.value if ok and buf.value else None
    ofn.Flags |= OFN_FILEMUSTEXIST | (OFN_ALLOWMULTISELECT if multi else 0)
    ok = comdlg32.GetOpenFileNameW(ctypes.byref(ofn))
    if not ok:
        return [] if multi else None
    if not multi:
        return buf.value or None
    raw = ctypes.wstring_at(ctypes.addressof(buf), len(buf))
    parts = raw.split('\0')
    parts = parts[:parts.index('')] if '' in parts else parts
    if len(parts) == 1:
        return parts
    return [os.path.join(parts[0], p) for p in parts[1:]]


def open_file(title, start='', filters=ANY):
    return _dialog(title, start, filters, save=False)


def open_files(title, start='', filters=ANY):
    return _dialog(title, start, filters, save=False, multi=True)


def save_file(title, start='', filters=ANY, default_ext=None):
    return _dialog(title, start, filters, save=True, default_ext=default_ext)


def pick_folder(title):
    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32
    BIF_RETURNONLYFSDIRS, BIF_EDITBOX, BIF_NEWDIALOGSTYLE = 0x1, 0x10, 0x40
    name = ctypes.create_unicode_buffer(260)
    bi = BROWSEINFOW()
    bi.hwndOwner = _owner()
    bi.pszDisplayName = ctypes.cast(name, wintypes.LPWSTR)
    bi.lpszTitle = title
    bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_EDITBOX | BIF_NEWDIALOGSTYLE
    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    shell32.SHBrowseForFolderW.argtypes = [ctypes.c_void_p]
    pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
    if not pidl:
        return None
    path = ctypes.create_unicode_buffer(1024)
    shell32.SHGetPathFromIDListW(ctypes.c_void_p(pidl), path)
    ole32.CoTaskMemFree(ctypes.c_void_p(pidl))
    return path.value or None
