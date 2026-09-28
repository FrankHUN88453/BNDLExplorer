"""Drag and drop with Windows Explorer.

In:  GLFW's drop callback (the imgui-bundle window is a GLFW window; glfw3.dll is already loaded by it).
Out: an OLE drag started with DoDragDrop: the shell builds the data object for files written to a temporary
     folder, and a minimal IDropSource (implemented here with ctypes) ends the drag when the button is released.
"""
import ctypes
import os
from ctypes import POINTER, WINFUNCTYPE, Structure, byref, c_long, c_ulong, c_void_p, wintypes

_keep = []                      # callbacks / COM objects that must stay alive


def _glfw():
    k32 = ctypes.windll.kernel32
    k32.GetModuleHandleW.restype = c_void_p
    h = k32.GetModuleHandleW('glfw3.dll')
    if h:
        return ctypes.CDLL('glfw3.dll', handle=h)
    path = os.environ.get('PYGLFW_LIBRARY')
    return ctypes.CDLL(path) if path else None


DROPFUN = ctypes.CFUNCTYPE(None, c_void_p, ctypes.c_int, POINTER(ctypes.c_char_p))


def install_drop(window_address, on_drop):
    """on_drop(list of paths) is called from the GUI thread while events are polled."""
    lib = _glfw()
    if lib is None or not window_address:
        return False

    def cb(win, count, paths):
        try:
            on_drop([paths[i].decode('utf-8', 'replace') for i in range(count)])
        except Exception:                                     # never let an exception cross into C
            import traceback
            traceback.print_exc()

    fn = DROPFUN(cb)
    _keep.append(fn)
    lib.glfwSetDropCallback.restype = c_void_p
    lib.glfwSetDropCallback.argtypes = [c_void_p, DROPFUN]
    lib.glfwSetDropCallback(c_void_p(window_address), fn)
    return True


def window_handle(window_address):
    lib = _glfw()
    if lib is None or not window_address:
        return None
    lib.glfwGetWin32Window.restype = c_void_p
    lib.glfwGetWin32Window.argtypes = [c_void_p]
    return lib.glfwGetWin32Window(c_void_p(window_address))


def cursor_outside(hwnd):
    """True when the mouse pointer is outside the client area of the window."""
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    pt = wintypes.POINT()
    user32.GetCursorPos(byref(pt))
    user32.ScreenToClient(c_void_p(hwnd), byref(pt))
    rc = wintypes.RECT()
    user32.GetClientRect(c_void_p(hwnd), byref(rc))
    return not (rc.left <= pt.x < rc.right and rc.top <= pt.y < rc.bottom)


# ---------------------------------------------------------------------------------------------------------------
# OLE drag source
# ---------------------------------------------------------------------------------------------------------------
class GUID(Structure):
    _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16), ('Data3', ctypes.c_uint16),
                ('Data4', ctypes.c_ubyte * 8)]


def _guid(s):
    g = GUID()
    ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(s), byref(g))
    return g


QI_T = WINFUNCTYPE(c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))
REF_T = WINFUNCTYPE(c_ulong, c_void_p)
QCD_T = WINFUNCTYPE(c_long, c_void_p, wintypes.BOOL, wintypes.DWORD)
GF_T = WINFUNCTYPE(c_long, c_void_p, wintypes.DWORD)


class _Vtbl(Structure):
    _fields_ = [('QueryInterface', QI_T), ('AddRef', REF_T), ('Release', REF_T),
                ('QueryContinueDrag', QCD_T), ('GiveFeedback', GF_T)]


class _DropSource(Structure):
    _fields_ = [('lpVtbl', POINTER(_Vtbl))]


S_OK = 0
E_NOINTERFACE = -2147467262                # 0x80004002
DRAGDROP_S_DROP, DRAGDROP_S_CANCEL, DRAGDROP_S_USEDEFAULTCURSORS = 0x40100, 0x40101, 0x40102
MK_LBUTTON = 1
DROPEFFECT_COPY = 1
_ole_ready = None


def _ole():
    global _ole_ready
    if _ole_ready is None:
        hr = ctypes.windll.ole32.OleInitialize(None)
        _ole_ready = hr in (0, 1)
    return _ole_ready


def _make_source():
    iids = [bytes(_guid('{00000000-0000-0000-C000-000000000046}')),
            bytes(_guid('{00000121-0000-0000-C000-000000000046}'))]
    src = _DropSource()

    def qi(this, riid, ppv):
        if bytes(riid.contents) in iids:
            ppv[0] = this
            return S_OK
        ppv[0] = None
        return E_NOINTERFACE

    def addref(this):
        return 1

    def release(this):
        return 1

    def qcd(this, escape, keys):
        if escape:
            return DRAGDROP_S_CANCEL
        if not keys & MK_LBUTTON:
            return DRAGDROP_S_DROP
        return S_OK

    def feedback(this, effect):
        return DRAGDROP_S_USEDEFAULTCURSORS

    vt = _Vtbl(QI_T(qi), REF_T(addref), REF_T(release), QCD_T(qcd), GF_T(feedback))
    src.lpVtbl = ctypes.pointer(vt)
    _keep.extend([vt, src])
    return src


def _data_object(paths):
    """Shell data object (CF_HDROP and friends) for existing files in one folder -> (obj, pidls to free)."""
    shell32 = ctypes.windll.shell32
    shell32.ILCreateFromPathW.restype = c_void_p
    shell32.ILCreateFromPathW.argtypes = [ctypes.c_wchar_p]
    shell32.ILFindLastID.restype = c_void_p
    shell32.ILFindLastID.argtypes = [c_void_p]
    shell32.SHCreateDataObject.argtypes = [c_void_p, ctypes.c_uint, POINTER(c_void_p), c_void_p,
                                           POINTER(GUID), POINTER(c_void_p)]
    folder = os.path.dirname(os.path.abspath(paths[0]))
    pfolder = shell32.ILCreateFromPathW(folder)
    items = [i for i in (shell32.ILCreateFromPathW(os.path.abspath(p)) for p in paths) if i]
    pidls = ([pfolder] if pfolder else []) + items
    if not pfolder or not items:
        return None, pidls
    arr = (c_void_p * len(items))(*[shell32.ILFindLastID(i) for i in items])
    obj = c_void_p()
    iid = _guid('{0000010E-0000-0000-C000-000000000046}')
    hr = shell32.SHCreateDataObject(pfolder, len(items), arr, None, byref(iid), byref(obj))
    return (obj if hr == 0 and obj else None), pidls


def _release(obj):
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
    REF_T(vtbl[2])(obj)                                       # IUnknown::Release


def _free(pidls):
    shell32 = ctypes.windll.shell32
    shell32.ILFree.argtypes = [c_void_p]
    for p in pidls:
        shell32.ILFree(p)


def data_object_ok(paths):
    """Self-test: can the shell build the data object for these files?"""
    if not _ole():
        return False
    obj, pidls = _data_object(paths)
    ok = obj is not None
    if ok:
        _release(obj)
    _free(pidls)
    return ok


def drag_files_out(paths):
    """Start a modal OLE drag of existing files (all in one folder). Returns True if they were dropped."""
    if not paths or not _ole():
        return False
    obj, pidls = _data_object(paths)
    dropped = False
    if obj is not None:
        src = _make_source()
        effect = wintypes.DWORD(0)
        ole32 = ctypes.windll.ole32
        ole32.DoDragDrop.argtypes = [c_void_p, c_void_p, wintypes.DWORD, POINTER(wintypes.DWORD)]
        hr = ole32.DoDragDrop(obj, byref(src), DROPEFFECT_COPY, byref(effect))
        dropped = hr == DRAGDROP_S_DROP and effect.value != 0
        _release(obj)
    _free(pidls)
    return dropped
