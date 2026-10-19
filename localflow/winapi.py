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
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
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
