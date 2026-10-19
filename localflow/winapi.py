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


def set_launch_at_login(enabled: bool, root) -> None:
    if not WIN:
        return
    import subprocess
    startup = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup")
    lnk = os.path.join(startup, "LocalFlow.lnk")
    if not enabled:
        if os.path.exists(lnk):
            os.remove(lnk)
        return
    pyw = os.path.join(root, ".venv", "Scripts", "pythonw.exe")
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
          f"$s.TargetPath='{pyw}';$s.Arguments='-m localflow';$s.WorkingDirectory='{root}';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=0x08000000, timeout=15)
