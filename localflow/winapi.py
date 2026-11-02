"""Small Windows helpers (no-ops elsewhere so tests run anywhere)."""
from __future__ import annotations

import os
import sys

WIN = sys.platform == "win32"
_NO_FOCUS_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def foreground_hwnd() -> int:
    if not WIN:
        return 0
    import ctypes
    return ctypes.windll.user32.GetForegroundWindow() or 0


def window_class(hwnd: int) -> str:
    if not WIN or not hwnd:
        return ""
    import ctypes
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def foreground_exe(hwnd: int | None = None) -> str | None:
    """e.g. 'slack.exe' for the app you're dictating into."""
    if not WIN:
        return None
    import ctypes
    from ctypes import wintypes
    hwnd = hwnd or foreground_hwnd()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return exe_of_pid(pid.value)


def exe_of_pid(pid: int) -> str | None:
    """'discord.exe' for a process id."""
    if not WIN or not pid:
        return None
    import ctypes
    from ctypes import wintypes
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
    finally:
        ctypes.windll.kernel32.CloseHandle(h)
    return None


def no_text_target(hwnd: int | None = None) -> bool:
    """True when nothing sensible has focus (desktop, taskbar, nothing)."""
    if not WIN:
        return False
    hwnd = hwnd or foreground_hwnd()
    return not hwnd or window_class(hwnd) in _NO_FOCUS_CLASSES


# --- what has keyboard focus? ---------------------------------------------------
# UI Automation control types (UIAutomationClient.h)
UIA_EDIT, UIA_COMBOBOX, UIA_DOCUMENT, UIA_PANE = 50004, 50003, 50030, 50033
_TERMINAL_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "mintty", "PuTTY",
                     "VirtualConsoleClass"}
_uia_local = None


def _caret_hwnd(hwnd: int) -> int:
    """The window showing a blinking text caret in that app's UI thread, if any."""
    import ctypes
    from ctypes import wintypes

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                    ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND),
                    ("hwndMenuOwner", wintypes.HWND), ("hwndMoveSize", wintypes.HWND),
                    ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]
    tid = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, None)
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if not ctypes.windll.user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
        return 0
    return info.hwndCaret or 0


def _uia():
    """One IUIAutomation per thread (COM objects are per-apartment), via raw ctypes."""
    import ctypes
    import threading
    global _uia_local
    if _uia_local is None:
        _uia_local = threading.local()
    obj = getattr(_uia_local, "obj", None)
    if obj:
        return obj
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx(None, 0)                       # MTA; harmless if already initialised
    clsid, iid = (ctypes.c_byte * 16)(), (ctypes.c_byte * 16)()
    ole32.CLSIDFromString("{ff48dba4-60ef-4201-aa87-54103eef594e}", clsid)   # CUIAutomation
    ole32.CLSIDFromString("{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}", iid)     # IUIAutomation
    ptr = ctypes.c_void_p()
    if ole32.CoCreateInstance(clsid, None, 1, iid, ctypes.byref(ptr)) != 0 or not ptr.value:
        return None
    _uia_local.obj = ptr.value
    return ptr.value


def _vcall(obj: int, index: int, *args):
    """Call method #index of a COM object's vtable; returns the HRESULT.
    Pass out-parameters as ctypes.pointer(x) (byref() objects carry no type)."""
    import ctypes
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *[type(a) for a in args])(vtbl[index])
    return fn(obj, *args)


def _focused_control_type() -> int | None:
    import ctypes
    uia = _uia()
    if not uia:
        return None
    el = ctypes.c_void_p()
    if _vcall(uia, 8, ctypes.pointer(el)) != 0 or not el.value:        # IUIAutomation::GetFocusedElement
        return None
    try:
        ct = ctypes.c_int()
        if _vcall(el.value, 21, ctypes.pointer(ct)) != 0:              # IUIAutomationElement::get_CurrentControlType
            return None
        return ct.value if 50000 <= ct.value <= 50050 else None        # sanity: a real control type id
    finally:
        _vcall(el.value, 2)                                            # Release


def text_focus(hwnd: int | None = None) -> tuple[str, str]:
    """('text' | 'none' | 'unknown', detail). 'none' = we're confident there's no text
    field to type into (desktop, a web page clicked outside its boxes, a button...)."""
    if not WIN:
        return "unknown", ""
    hwnd = hwnd or foreground_hwnd()
    if not hwnd:
        return "none", "no window"
    cls = window_class(hwnd)
    if cls in _NO_FOCUS_CLASSES:
        return "none", cls
    if cls in _TERMINAL_CLASSES:
        return "text", cls
    try:
        if _caret_hwnd(hwnd):
            return "text", f"{cls} caret"
    except Exception:
        pass
    try:
        ct = _focused_control_type()
    except Exception as e:               # ctypes turns access violations into OSError
        return "unknown", f"{cls} uia error {e}"
    if ct is None:
        return "unknown", f"{cls} uia n/a"
    if ct in (UIA_EDIT, UIA_COMBOBOX):
        return "text", f"{cls} uia {ct}"
    if ct == UIA_PANE:          # the window itself: Chromium/Electron without its accessibility tree
        return "unknown", f"{cls} uia pane"
    return "none", f"{cls} uia {ct}"


def set_console_title(text: str) -> None:
    if WIN:
        import ctypes
        if ctypes.windll.kernel32.GetConsoleWindow():
            ctypes.windll.kernel32.SetConsoleTitleW(text)


def running_exes() -> list[str]:
    if not WIN:
        return []
    import csv
    import io
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                             creationflags=0x08000000, timeout=5).stdout
    except Exception:
        return []
    return sorted({row[0].lower() for row in csv.reader(io.StringIO(out)) if row})


def _make_shortcut(lnk: str, root, icon: str | None = None, description: str = "LocalFlow dictation") -> None:
    import subprocess
    pyw = os.path.join(str(root), ".venv", "Scripts", "pythonw.exe")
    q = lambda v: str(v).replace("'", "''")  # noqa: E731 - PowerShell single-quote escape
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(lnk)}');"
          f"$s.TargetPath='{q(pyw)}';$s.Arguments='-m localflow';$s.WorkingDirectory='{q(root)}';"
          f"$s.Description='{q(description)}';"
          + (f"$s.IconLocation='{q(icon)},0';" if icon else "") + "$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=0x08000000, timeout=20, check=True)


def set_launch_at_login(enabled: bool, root) -> None:
    if not WIN:
        return
    startup = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup")
    lnk = os.path.join(startup, "LocalFlow.lnk")
    if not enabled:
        if os.path.exists(lnk):
            os.remove(lnk)
        return
    _make_shortcut(lnk, root, write_icon(root))


def write_icon(root) -> str | None:
    """Save the app icon as data/localflow.ico (for shortcuts)."""
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # noqa: F841 - QPixmap needs an app
        from .app import _tray_icon
        path = os.path.join(str(root), "data", "localflow.ico")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if _tray_icon().pixmap(256, 256).save(path, "ICO"):
            return path
    except Exception:
        pass
    return None


def create_shortcuts(root, desktop: bool = True) -> list[str]:
    """Start Menu (searchable as 'LocalFlow') and Desktop shortcuts."""
    if not WIN:
        return []
    icon = write_icon(root)
    made = []
    start_menu = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\LocalFlow.lnk")
    _make_shortcut(start_menu, root, icon)
    made.append(start_menu)
    if desktop:
        desk = os.path.join(os.path.expanduser("~"), "Desktop", "LocalFlow.lnk")
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0:  # CSIDL_DESKTOPDIRECTORY (OneDrive-aware)
                desk = os.path.join(buf.value, "LocalFlow.lnk")
        except Exception:
            pass
        _make_shortcut(desk, root, icon)
        made.append(desk)
    return made
