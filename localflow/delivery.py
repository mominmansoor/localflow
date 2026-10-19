"""Getting text into apps, and running voice commands."""
from __future__ import annotations

import logging
import subprocess
import sys
import time

log = logging.getLogger(__name__)


def _modifiers_down() -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    g = ctypes.windll.user32.GetAsyncKeyState
    return any(g(vk) & 0x8000 for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C))


def wait_modifiers_released(timeout: float = 1.5) -> None:
    end = time.monotonic() + timeout
    while _modifiers_down() and time.monotonic() < end:
        time.sleep(0.01)


def clip_get() -> str | None:
    import pyperclip
    for _ in range(5):
        try:
            return pyperclip.paste()
        except Exception:
            time.sleep(0.02)
    return None


def clip_set(text: str) -> bool:
    import pyperclip
    for _ in range(5):
        try:
            pyperclip.copy(text)
            return True
        except Exception:
            time.sleep(0.02)
    return False


def _ctrl(key: str) -> None:
    from pynput.keyboard import Controller, Key
    kb = Controller()
    with kb.pressed(Key.ctrl):
        kb.press(key)
        kb.release(key)


def press_enter() -> None:
    from pynput.keyboard import Controller, Key
    kb = Controller()
    kb.press(Key.enter)
    kb.release(Key.enter)


def insert_text(text: str, method: str = "paste", restore_clipboard: bool = True, auto_send: bool = False) -> None:
    from pynput.keyboard import Controller
    wait_modifiers_released()
    if method == "type":
        Controller().type(text)
    else:
        old = clip_get() if restore_clipboard else None
        if not clip_set(text):
            log.warning("Clipboard busy; typing instead")
            Controller().type(text)
        else:
            _ctrl("v")
            if restore_clipboard and old is not None:
                time.sleep(0.3)               # let the target app read the clipboard
                if clip_get() == text:        # don't clobber something copied meanwhile
                    clip_set(old)
    if auto_send:
        time.sleep(0.05)
        press_enter()


def copy_selection(timeout: float = 0.6) -> str | None:
    """Ctrl+C the current selection; returns None if nothing was selected."""
    wait_modifiers_released()
    sentinel = f"__localflow_{time.time_ns()}__"
    old = clip_get()
    clip_set(sentinel)
    _ctrl("c")
    end = time.monotonic() + timeout
    got = sentinel
    while time.monotonic() < end:
        time.sleep(0.03)
        got = clip_get()
        if got != sentinel:
            break
    if old is not None:
        clip_set(old)
    return None if got in (sentinel, None, "") else got


def run_command(cmd: dict, clipboard: str = "") -> None:
    from .hotkey import send_combo
    from .textproc import expand_vars
    action, value = cmd.get("action"), cmd.get("value", "")
    wait_modifiers_released()
    if action == "hotkey":
        send_combo(value)
    elif action == "type":
        insert_text(expand_vars(value, clipboard), "paste")
    elif action == "run":
        flags = 0x08000000 if sys.platform == "win32" else 0  # no console window
        subprocess.Popen(value, shell=True, creationflags=flags)  # noqa: S602 - user-defined command
    else:
        raise ValueError(f"Unknown command action: {action}")
