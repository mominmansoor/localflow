"""Mute other apps' microphone input while you dictate (so a call doesn't hear it).

Windows gives every app that records from a mic its own capture "audio session",
with its own mute switch. On dictation start we mute the sessions that are actively
recording (Discord, Teams, Zoom, a Meet tab...) - never our own - and on stop we
unmute exactly the ones we muted. The mic device itself stays on, so LocalFlow still
hears you.

All COM work happens on one small background thread (raw ctypes, no extra
packages), so the hotkey never waits on it. The muted apps are written to disk
while muted; if LocalFlow dies mid-dictation they're unmuted on the next start.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)
WIN = sys.platform == "win32"

CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_IAudioSessionManager2 = "{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}"
IID_IAudioSessionControl2 = "{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}"
IID_ISimpleAudioVolume = "{87CE5498-68D6-44E5-9215-6DA47EF883D8}"
E_CAPTURE, DEVICE_STATE_ACTIVE, CLSCTX_ALL, SESSION_ACTIVE = 1, 1, 23, 1


# -- tiny raw-COM helpers ---------------------------------------------------------
def _guid(s: str):
    import ctypes
    g = (ctypes.c_byte * 16)()
    ctypes.windll.ole32.CLSIDFromString(s, g)
    return g


def _call(obj: int, index: int, *args) -> int:
    """Method #index of a COM object's vtable. Out-params: pass ctypes.pointer(x)."""
    import ctypes
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *[type(a) for a in args])(vtbl[index])
    return fn(obj, *args)


def _release(obj) -> None:
    if obj:
        try:
            _call(obj, 2)
        except Exception:
            pass


def _qi(obj: int, iid: str) -> int | None:
    import ctypes
    out = ctypes.c_void_p()
    if _call(obj, 0, ctypes.pointer(_guid(iid)), ctypes.pointer(out)) == 0 and out.value:
        return out.value
    return None


def _capture_sessions():
    """Yield (pid, ISimpleAudioVolume*) for every *active* recording session on every
    mic. The caller must release the pointers it keeps."""
    import ctypes
    from ctypes import wintypes
    ole32 = ctypes.windll.ole32
    enum = ctypes.c_void_p()
    if ole32.CoCreateInstance(_guid(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                              _guid(IID_IMMDeviceEnumerator), ctypes.byref(enum)) != 0 or not enum.value:
        return
    coll = ctypes.c_void_p()
    try:
        if _call(enum.value, 3, ctypes.c_int(E_CAPTURE), wintypes.DWORD(DEVICE_STATE_ACTIVE),
                 ctypes.pointer(coll)) != 0 or not coll.value:                      # EnumAudioEndpoints
            return
        n = wintypes.UINT()
        _call(coll.value, 3, ctypes.pointer(n))                                     # GetCount
        for i in range(n.value):
            dev = ctypes.c_void_p()
            if _call(coll.value, 4, wintypes.UINT(i), ctypes.pointer(dev)) != 0 or not dev.value:   # Item
                continue
            mgr = ctypes.c_void_p()
            try:
                if _call(dev.value, 3, ctypes.pointer(_guid(IID_IAudioSessionManager2)), wintypes.DWORD(CLSCTX_ALL),
                         ctypes.c_void_p(), ctypes.pointer(mgr)) != 0 or not mgr.value:   # Activate
                    continue
                senum = ctypes.c_void_p()
                if _call(mgr.value, 5, ctypes.pointer(senum)) != 0 or not senum.value:   # GetSessionEnumerator
                    continue
                try:
                    count = ctypes.c_int()
                    _call(senum.value, 3, ctypes.pointer(count))
                    for j in range(count.value):
                        ctl = ctypes.c_void_p()
                        if _call(senum.value, 4, ctypes.c_int(j), ctypes.pointer(ctl)) != 0 or not ctl.value:
                            continue
                        try:
                            state = ctypes.c_int()
                            _call(ctl.value, 3, ctypes.pointer(state))                  # GetState
                            if state.value != SESSION_ACTIVE:
                                continue
                            ctl2 = _qi(ctl.value, IID_IAudioSessionControl2)
                            if not ctl2:
                                continue
                            pid = wintypes.DWORD()
                            _call(ctl2, 14, ctypes.pointer(pid))                        # GetProcessId
                            _release(ctl2)
                            vol = _qi(ctl.value, IID_ISimpleAudioVolume)
                            if vol:
                                yield pid.value, vol
                        finally:
                            _release(ctl.value)
                finally:
                    _release(senum.value)
            finally:
                _release(mgr.value)
                _release(dev.value)
    finally:
        _release(coll.value)
        _release(enum.value)


def _get_mute(vol: int) -> bool:
    import ctypes
    m = ctypes.c_int()
    _call(vol, 6, ctypes.pointer(m))                                                # GetMute
    return bool(m.value)


def _set_mute(vol: int, on: bool) -> bool:
    import ctypes
    return _call(vol, 5, ctypes.c_int(1 if on else 0), ctypes.c_void_p()) == 0     # SetMute


def _co_init() -> None:
    import ctypes
    ctypes.windll.ole32.CoInitializeEx(None, 0)


def _exe(pid: int) -> str:
    try:
        from .winapi import exe_of_pid
        return exe_of_pid(pid) or str(pid)
    except Exception:
        return str(pid)


# -- the muter ----------------------------------------------------------------------
class CallMuter:
    def __init__(self, state_file: Path):
        self.state_file = Path(state_file)
        self._q: queue.Queue = queue.Queue()
        self._held: list[tuple[int, int]] = []        # (pid, ISimpleAudioVolume*) we muted
        self._thread = None
        if WIN:
            self._thread = threading.Thread(target=self._run, name="callmute", daemon=True)
            self._thread.start()
            self._q.put("restore")

    def mute(self) -> None:
        if self._thread:
            self._q.put("mute")

    def unmute(self) -> None:
        if self._thread:
            self._q.put("unmute")

    def shutdown(self, timeout: float = 1.0) -> None:
        """Unmute anything still muted, then stop (used on quit)."""
        if self._thread:
            done = threading.Event()
            self._q.put("unmute")
            self._q.put(("stop", done))
            done.wait(timeout)
            self._thread = None

    # worker thread ----------------------------------------------------------------
    def _run(self) -> None:
        _co_init()
        while True:
            cmd = self._q.get()
            try:
                if cmd == "mute":
                    self._do_mute()
                elif cmd == "unmute":
                    self._do_unmute()
                elif cmd == "restore":
                    self._do_restore()
                elif isinstance(cmd, tuple) and cmd[0] == "stop":
                    cmd[1].set()
                    return
            except Exception:
                log.exception("call mute: %s failed", cmd)

    def _do_mute(self) -> None:
        if self._held:
            return
        me = os.getpid()
        seen = []
        for pid, vol in _capture_sessions():
            seen.append(_exe(pid))
            if pid in (0, me) or _get_mute(vol):
                _release(vol)                          # ours, system, or already muted by you
                continue
            if _set_mute(vol, True):
                self._held.append((pid, vol))
            else:
                _release(vol)
        if self._held:
            pids = sorted({p for p, _ in self._held})
            self._save(pids)
            log.info("call mute: muted mic for %s", ", ".join(_exe(p) for p in pids))
        else:
            log.info("call mute: nothing to mute (recording now: %s)", ", ".join(seen) or "none")

    def _do_unmute(self) -> None:
        held, self._held = self._held, []
        for _pid, vol in held:
            try:
                _set_mute(vol, False)
            finally:
                _release(vol)
        if held:
            log.info("call mute: unmuted %d", len(held))
        self._save([])

    def _do_restore(self) -> None:
        """After a crash mid-dictation: unmute the apps we'd muted."""
        try:
            pids = set(json.loads(self.state_file.read_text())) if self.state_file.exists() else set()
        except Exception:
            pids = set()
        if not pids:
            live = []
            for pid, vol in _capture_sessions():          # startup self-check: just look
                live.append(_exe(pid))
                _release(vol)
            log.info("call mute: ready (apps using a mic now: %s)", ", ".join(live) or "none")
            return
        n = 0
        for pid, vol in _capture_sessions():
            if pid in pids and _get_mute(vol):
                n += _set_mute(vol, False)
            _release(vol)
        log.info("call mute: restored %d app(s) left muted by a previous run", n)
        self._save([])

    def _save(self, pids: list[int]) -> None:
        try:
            if pids:
                self.state_file.parent.mkdir(parents=True, exist_ok=True)
                self.state_file.write_text(json.dumps(pids))
            elif self.state_file.exists():
                self.state_file.unlink()
        except Exception:
            pass
