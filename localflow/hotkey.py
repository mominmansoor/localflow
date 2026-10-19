"""Global hotkeys.

Bindings:
  dictate     hold-to-talk. Double-tap = hands-free lock (tap again to stop).
  rephrase    hold while text is selected, speak an instruction, release.
  paste_last  tap: re-insert the last dictation.
  pause       tap: disable/enable every other LocalFlow hotkey.

HotkeyMachine is pure logic (unit-tested). When combos overlap (Ctrl+Win vs
Ctrl+Shift+Win) the biggest fully-held combo wins; if you add the extra key a
moment after starting, the smaller action is cancelled and the bigger one starts.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

START, STOP, CANCEL, LOCK, ARMED, FIRE = "start", "stop", "cancel", "lock", "armed", "fire"
HOLD_KIND, TAP_KIND = "hold", "tap"
KINDS = {"dictate": HOLD_KIND, "rephrase": HOLD_KIND, "paste_last": TAP_KIND, "pause": TAP_KIND}

ALIASES = {
    "control": "ctrl", "ctl": "ctrl",
    "cmd": "win", "super": "win", "meta": "win", "windows": "win", "command": "win",
    "option": "alt", "escape": "esc", "return": "enter", "spacebar": "space",
}


def parse_hotkey(spec: str) -> frozenset[str]:
    parts = [p.strip().lower() for p in (spec or "").split("+") if p.strip()]
    if not parts:
        raise ValueError(f"Empty hotkey: {spec!r}")
    return frozenset(ALIASES.get(p, p) for p in parts)


def format_hotkey(combo) -> str:
    order = {"ctrl": 0, "alt": 1, "shift": 2, "win": 3}
    return "+".join(sorted(combo, key=lambda k: (order.get(k, 9), k)))


class HotkeyMachine:
    IDLE, HOLD, ARMED_S, LOCKED = "idle", "hold", "armed", "locked"

    def __init__(self, bindings: dict[str, str], lock_mode="double_tap", tap_ms=300,
                 double_tap_ms=400, chord_guard_ms=400, cancel_key="esc",
                 is_down: Callable[[str], bool | None] | None = None):
        self._is_down = is_down
        self._lock = threading.RLock()
        self.pressed: set[str] = set()
        self.state = self.IDLE
        self.active: str | None = None
        self.t_start = self.t_release = 0.0
        self.paused = False
        self.suspended = False
        self.configure(bindings, lock_mode, tap_ms, double_tap_ms, chord_guard_ms, cancel_key)

    def configure(self, bindings, lock_mode="double_tap", tap_ms=300, double_tap_ms=400,
                  chord_guard_ms=400, cancel_key="esc") -> None:
        with self._lock:
            self.bindings: dict[str, frozenset] = {}
            for name, spec in bindings.items():
                if spec:
                    try:
                        self.bindings[name] = parse_hotkey(spec)
                    except ValueError:
                        log.warning("Bad hotkey for %s: %r", name, spec)
            self.lock_mode = lock_mode
            self.tap_s, self.dtap_s, self.guard_s = tap_ms / 1000, double_tap_ms / 1000, chord_guard_ms / 1000
            self.cancel_key = cancel_key

    # -- helpers ------------------------------------------------------------------
    def _best(self, k: str) -> str | None:
        best, size = None, 0
        for name, combo in self.bindings.items():
            if self.paused and name != "pause":
                continue
            if k in combo and combo <= self.pressed and len(combo) > size:
                best, size = name, len(combo)
        return best

    def _resync(self, current: str) -> None:
        if not self._is_down:
            return
        for k in list(self.pressed):
            if k != current and self._is_down(k) is False:
                self.pressed.discard(k)

    def _begin(self, name: str, t: float) -> list:
        if KINDS.get(name) == TAP_KIND:
            if name == "pause":
                self.paused = not self.paused
            return [(FIRE, name)]
        self.state, self.active, self.t_start = self.HOLD, name, t
        return [(START, name)]

    def _end(self, ev: str) -> list:
        name = self.active
        self.state, self.active = self.IDLE, None
        return [(ev, name)]

    # -- public -------------------------------------------------------------------
    def press(self, k: str | None, t: float) -> list:
        with self._lock:
            if k is None or k in self.pressed:   # unknown key / auto-repeat
                return []
            self._resync(k)
            self.pressed.add(k)
            if self.suspended:        # e.g. while recording a new hotkey in Settings
                return []
            best = self._best(k)

            if self.state == self.IDLE:
                return self._begin(best, t) if best else []

            if k == self.cancel_key:
                return self._end(CANCEL)

            if self.state == self.HOLD:
                if best and best != self.active and self.bindings[self.active] < self.bindings[best] \
                        and t - self.t_start < self.guard_s:
                    return self._end(CANCEL) + self._begin(best, t)
                if k not in self.bindings[self.active] and t - self.t_start < self.guard_s:
                    return self._end(CANCEL)      # was a system shortcut, e.g. Ctrl+Win+D
                return []

            if self.state == self.ARMED_S:
                if best == self.active:
                    self.state = self.LOCKED
                    return [(LOCK, self.active)]
                if k in self.bindings[self.active]:
                    return []                     # first key of the second tap
                return self._end(CANCEL)

            # LOCKED
            if best == self.active:
                return self._end(STOP)
            if best and KINDS.get(best) == TAP_KIND:
                return self._begin(best, t)
            return []

    def release(self, k: str | None, t: float) -> list:
        with self._lock:
            if k is None:
                return []
            self.pressed.discard(k)
            if self.state != self.HOLD or k not in self.bindings.get(self.active, ()):
                return []
            if t - self.t_start >= self.tap_s or self.active != "dictate":
                return self._end(STOP)
            # quick tap on the dictate hotkey
            if self.lock_mode == "double_tap":
                self.state, self.t_release = self.ARMED_S, t
                return [(ARMED, self.active)]
            if self.lock_mode == "single_tap":
                self.state = self.LOCKED
                return [(LOCK, self.active)]
            return self._end(STOP)

    def poll(self, t: float) -> list:
        """Call ~double_tap_ms after an ARMED event: a lone tap is discarded."""
        with self._lock:
            if self.state == self.ARMED_S and t - self.t_release >= self.dtap_s:
                return self._end(CANCEL)
            return []

    def force_stop(self) -> list:
        with self._lock:
            if self.state in (self.HOLD, self.LOCKED, self.ARMED_S):
                return self._end(STOP)
            return []


# ---------------------------------------------------------------------------
# OS glue
# ---------------------------------------------------------------------------

def normalize_key(key) -> str | None:
    from pynput.keyboard import Key, KeyCode

    if isinstance(key, Key):
        name = key.name
        if name == "alt_gr":
            return "alt"
        for suf in ("_l", "_r"):
            if name.endswith(suf):
                name = name[: -len(suf)]
        return "win" if name == "cmd" else name
    if isinstance(key, KeyCode):
        vk = key.vk
        if vk == 0xE8:  # our Start-menu mask key
            return None
        if vk is not None and (0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39):
            return chr(vk).lower()
        if key.char and key.char.isprintable():
            return key.char.lower()
        return f"vk{vk}" if vk is not None else None
    return None


_VK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10}


def win_is_down(name: str) -> bool | None:
    import ctypes
    gaks = ctypes.windll.user32.GetAsyncKeyState
    if name == "win":
        return bool((gaks(0x5B) | gaks(0x5C)) & 0x8000)
    vk = _VK.get(name)
    if vk is None and len(name) == 1 and name.isalnum():
        vk = ord(name.upper())
    if vk is None:
        return None
    return bool(gaks(vk) & 0x8000)


class HotkeyListener:
    def __init__(self, machine: HotkeyMachine, on_events: Callable[[list], None]):
        self.m = machine
        self.on_events = on_events
        self._listener = None

    def start(self) -> None:
        from pynput import keyboard
        kwargs = {}
        if sys.platform == "win32":
            kwargs["win32_event_filter"] = lambda msg, data: not (data.flags & 0x10)  # skip injected input
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release, **kwargs)
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()

    # Runs inside the OS hook: must return instantly.
    def _on_press(self, key):
        ev = self.m.press(normalize_key(key), time.monotonic())
        if ev:
            self.on_events(ev)

    def _on_release(self, key):
        ev = self.m.release(normalize_key(key), time.monotonic())
        if ev:
            self.on_events(ev)


def send_start_menu_mask() -> None:
    """Tap an unassigned key while Win is held so releasing Win doesn't open Start."""
    if sys.platform != "win32":
        return
    from pynput.keyboard import Controller, KeyCode
    c, k = Controller(), KeyCode.from_vk(0xE8)
    c.press(k)
    c.release(k)


def send_combo(spec: str) -> None:
    """Press a key combo like 'ctrl+shift+t' or 'enter'."""
    from pynput.keyboard import Controller, Key, KeyCode
    c = Controller()
    names = {"ctrl": Key.ctrl, "alt": Key.alt, "shift": Key.shift, "win": Key.cmd, "enter": Key.enter,
             "esc": Key.esc, "tab": Key.tab, "space": Key.space, "backspace": Key.backspace,
             "delete": Key.delete, "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right,
             "home": Key.home, "end": Key.end, "pageup": Key.page_up, "pagedown": Key.page_down}
    keys = []
    for p in [ALIASES.get(x.strip().lower(), x.strip().lower()) for x in spec.split("+") if x.strip()]:
        if p in names:
            keys.append(names[p])
        elif p.startswith("f") and p[1:].isdigit():
            keys.append(getattr(Key, p))
        else:
            keys.append(KeyCode.from_char(p))
    for k in keys:
        c.press(k)
    for k in reversed(keys):
        c.release(k)
