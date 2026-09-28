"""Windows clipboard: files (CF_HDROP) and text, so resources can be copied to / pasted from Explorer."""
import ctypes
import struct
from ctypes import wintypes

CF_UNICODETEXT, CF_HDROP = 13, 15
GMEM_MOVEABLE = 0x0002

_k32 = ctypes.windll.kernel32
_u32 = ctypes.windll.user32
_sh = ctypes.windll.shell32
_k32.GlobalAlloc.restype = ctypes.c_void_p
_k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_k32.GlobalLock.restype = ctypes.c_void_p
_k32.GlobalLock.argtypes = [ctypes.c_void_p]
_k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_u32.GetClipboardData.restype = ctypes.c_void_p
_u32.SetClipboardData.restype = ctypes.c_void_p
_u32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
_u32.OpenClipboard.argtypes = [ctypes.c_void_p]
_sh.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_wchar_p, wintypes.UINT]


def _global(data):
    h = _k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    p = _k32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    _k32.GlobalUnlock(h)
    return h


def _open(hwnd):
    for _ in range(10):
        if _u32.OpenClipboard(hwnd):
            return True
        _k32.Sleep(20)
    return False


def set_files(paths, hwnd=None, text=None, cut=False):
    """Put files on the clipboard (Explorer can paste them), optionally with text too."""
    if not _open(hwnd):
        return False
    try:
        _u32.EmptyClipboard()
        body = (''.join(p + '\0' for p in paths) + '\0').encode('utf-16-le')
        _u32.SetClipboardData(CF_HDROP, _global(struct.pack('<IiiII', 20, 0, 0, 0, 1) + body))
        effect = _u32.RegisterClipboardFormatW('Preferred DropEffect')
        _u32.SetClipboardData(effect, _global(struct.pack('<I', 2 if cut else 1)))
        if text:
            _u32.SetClipboardData(CF_UNICODETEXT, _global((text + '\0').encode('utf-16-le')))
        return True
    finally:
        _u32.CloseClipboard()


def get_files(hwnd=None):
    """Files on the clipboard (copied in Explorer), or []."""
    if not _u32.IsClipboardFormatAvailable(CF_HDROP) or not _open(hwnd):
        return []
    try:
        h = _u32.GetClipboardData(CF_HDROP)
        if not h:
            return []
        n = _sh.DragQueryFileW(h, 0xFFFFFFFF, None, 0)
        out = []
        for i in range(n):
            ln = _sh.DragQueryFileW(h, i, None, 0)
            buf = ctypes.create_unicode_buffer(ln + 1)
            _sh.DragQueryFileW(h, i, buf, ln + 1)
            out.append(buf.value)
        return out
    finally:
        _u32.CloseClipboard()
