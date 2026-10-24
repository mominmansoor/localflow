"""Microphone capture.

- Stream stays open (by default) with a pre-roll ring buffer: instant start, first
  syllable never clipped.
- Watchdog reconnects if the mic is unplugged or the stream stalls.
- Whisper mode boosts quiet speech; optional noise suppression (noisereduce).
- Silence tracking for continuous mode (auto-split long dictations at pauses).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

RATE = 16000
BLOCK = 512  # 32 ms


def _hostapi_index(name: str) -> int | None:
    import sounddevice as sd
    try:
        for i, h in enumerate(sd.query_hostapis()):
            if h.get("name") == name:
                return i
    except Exception:
        pass
    return None


def list_input_devices() -> list[tuple[int, str]]:
    """One entry per physical mic, with its full name.

    Windows' old MME list cuts names to 31 characters ("Microphone (HyperX Cloud Stinge"),
    so the full names come from WASAPI when it's available."""
    import sounddevice as sd
    api = _hostapi_index("Windows WASAPI")
    if api is None:
        api = 0
    out, seen = [], set()
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and d["hostapi"] == api and d["name"] not in seen:
            seen.add(d["name"])
            out.append((i, d["name"]))
    return out


def list_devices() -> str:
    import sounddevice as sd
    return str(sd.query_devices())


def names_match(spec: str | None, name: str | None) -> bool:
    """Same mic? Handles MME's 31-character truncation in either direction."""
    if not spec or not name:
        return False
    a, b = spec.strip().lower(), name.strip().lower()
    return a == b or a in b or b in a or (min(len(a), len(b)) >= 24 and (a.startswith(b) or b.startswith(a)))


def full_name(spec: str | None) -> str | None:
    """The full device name for a saved (possibly truncated) one, if it's plugged in."""
    if not spec or isinstance(spec, int):
        return None
    try:
        for _i, name in list_input_devices():
            if names_match(spec, name):
                return name
    except Exception:
        pass
    return None


def live_input_names() -> list[str] | None:
    """Mics Windows can see right now. A direct WinMM query: microseconds, no
    allocation to speak of, and unlike PortAudio's list it is never stale.
    Returns None where that isn't available (not Windows)."""
    import sys
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class WAVEINCAPSW(ctypes.Structure):
        _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD), ("vDriverVersion", wintypes.UINT),
                    ("szPname", wintypes.WCHAR * 32), ("dwFormats", wintypes.DWORD),
                    ("wChannels", wintypes.WORD), ("wReserved1", wintypes.WORD)]

    winmm = ctypes.windll.winmm
    names = []
    caps = WAVEINCAPSW()
    for i in range(winmm.waveInGetNumDevs()):
        if winmm.waveInGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
            names.append(caps.szPname)
    return names


def _resolve_device(dev):
    """Device index for a saved mic name, or None if it isn't there (= system default)."""
    if dev is None or isinstance(dev, int):
        return dev
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and names_match(str(dev), d["name"]):
            return i
    return None


def rms_db(x: np.ndarray) -> float:
    return 20 * np.log10(float(np.sqrt(np.mean(x * x))) + 1e-9)


def whisper_boost(audio: np.ndarray, target_db: float = -22.0, max_gain: float = 20.0) -> np.ndarray:
    """Normalise quiet speech up to a speaking level (gain from the loud parts)."""
    if len(audio) < BLOCK:
        return audio
    n = len(audio) // BLOCK * BLOCK
    blocks = audio[:n].reshape(-1, BLOCK)
    levels = np.sqrt(np.mean(blocks * blocks, axis=1))
    speech = float(np.percentile(levels, 90))
    if speech <= 1e-6:
        return audio
    gain = min(max_gain, max(1.0, 10 ** (target_db / 20) / speech))
    return np.clip(audio * gain, -1.0, 1.0).astype(np.float32)


_nr_warned = False


def denoise(audio: np.ndarray) -> np.ndarray:
    global _nr_warned
    try:
        import noisereduce as nr
    except ImportError:
        if not _nr_warned:
            log.warning("Noise suppression on but 'noisereduce' isn't installed (pip install noisereduce)")
            _nr_warned = True
        return audio
    return nr.reduce_noise(y=audio, sr=RATE, stationary=True, prop_decrease=0.85).astype(np.float32)


class Recorder:
    """Keeps the mic stream healthy without costing anything while it is.

    - Chosen mic missing (headset asleep, PC just booted): record from the system
      default, and every RECHECK_S seconds ask Windows if the mic is back. That check
      only runs while we're on the fallback.
    - Pressing the hotkey while on the fallback does the same check first.
    - A recording that's exact digital zeros for DEAD_S seconds means the device went
      away under us: reopen it once, then tell the user if it's still silent (muted).
    """
    RECHECK_S = 5.0
    DEAD_S = 1.0

    def __init__(self, device=None, keep_open=True, preroll_ms=500,
                 on_level: Callable[[float], None] | None = None,
                 on_status: Callable[[str, bool], None] | None = None):
        self.on_level = on_level
        self.on_status = on_status
        self.keep_open = keep_open
        self.preroll_ms = preroll_ms
        self._pre: deque = deque(maxlen=max(1, int(preroll_ms / 1000 * RATE / BLOCK)))
        self._chunks: list[np.ndarray] = []
        self._rec = False
        self._lock = threading.Lock()
        self._dev_lock = threading.RLock()
        self._stream = None
        self._rate = RATE
        self._last_cb = 0.0
        # silence tracking (continuous mode)
        self.voice_db = -45.0
        self.last_voice_t = 0.0
        self.voiced_since_cut = 0.0
        # health
        self.fallback = False
        self._last_check = 0.0
        self._zero_s = 0.0
        self._dead_handled = False
        self._watchdog_started = False
        self.device_spec = device
        self.device = None
        self._pick_device(announce=True)

    # -- device choice ---------------------------------------------------------
    def _status(self, text: str, error: bool = False) -> None:
        log.info("mic: %s", text)
        if self.on_status:
            try:
                self.on_status(text, error)
            except Exception:
                pass

    def _pick_device(self, announce: bool) -> None:
        self.device = _resolve_device(self.device_spec)
        was = self.fallback
        self.fallback = bool(self.device_spec) and self.device is None
        if self.fallback and (announce or not was):
            log.warning("Mic %r not found, using default", self.device_spec)
            self._status("Your mic isn't connected, using the default for now", True)

    def _chosen_mic_visible(self) -> bool:
        try:
            names = live_input_names()
        except Exception:
            log.exception("live mic check failed")
            return True            # let the full rescan decide
        if names is None:          # can't ask cheaply on this OS: assume it might be back
            return True
        return any(names_match(self.device_spec, n) for n in names)

    # -- config ------------------------------------------------------------------
    def configure(self, device=None, keep_open=True, preroll_ms=500) -> None:
        changed = device != self.device_spec
        self.keep_open = keep_open
        if preroll_ms != self.preroll_ms:
            self.preroll_ms = preroll_ms
            self._pre = deque(maxlen=max(1, int(preroll_ms / 1000 * RATE / BLOCK)))
        if changed:
            with self._dev_lock:
                self.device_spec = device
                self.close()
                self._rescan()
                self._pick_device(announce=True)
        if keep_open and not self._rec:
            self._safe_open()
        elif not keep_open and not self._rec:
            self.close()

    # -- stream management ---------------------------------------------------
    def open(self) -> None:
        import sounddevice as sd
        with self._dev_lock:
            if self._stream is not None and self._stream.active:
                return
            self.close()
            try:
                self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                                              blocksize=BLOCK, device=self.device, callback=self._cb)
                self._rate = RATE
            except Exception:
                info = sd.query_devices(self.device, "input")
                self._rate = int(info["default_samplerate"])
                self._stream = sd.InputStream(samplerate=self._rate, channels=1, dtype="float32",
                                              blocksize=int(BLOCK * self._rate / RATE), device=self.device,
                                              callback=self._cb)
                log.info("Mic doesn't do 16 kHz; capturing at %d Hz and resampling", self._rate)
            self._stream.start()
            self._last_cb = time.monotonic()
        self._start_watchdog()

    def _safe_open(self) -> bool:
        try:
            self.open()
            return True
        except Exception as e:
            log.warning("Mic open failed: %s", e)
            return False

    def close(self) -> None:
        with self._dev_lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

    @staticmethod
    def _rescan() -> None:
        """PortAudio reads the device list once; re-initialise it to see new devices."""
        import sounddevice as sd
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass

    def _reconnect(self, reason: str = "") -> None:
        with self._dev_lock:
            log.info("Reconnecting microphone%s", f" ({reason})" if reason else "")
            was_fallback = self.fallback
            self.close()
            self._rescan()
            self._pick_device(announce=False)
            if not self._safe_open() and self.device is not None:
                self.device = None          # chosen mic is listed but won't open: use the default
                self.fallback = bool(self.device_spec)
                self._safe_open()
            if was_fallback and not self.fallback:
                self._status("Mic reconnected")

    # -- health checks (cheap; called by the watchdog every 1.5 s) ----------------
    def watch_once(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        want_open = self.keep_open or self._rec
        if not want_open:
            return
        s = self._stream
        if s is None or not s.active or (now - self._last_cb > 2.0):
            self._reconnect("stream stopped")
            return
        if self._rec and self._zero_s >= self.DEAD_S and not self._dead_handled:
            self._dead_handled = True
            self._reconnect("no signal")
            return
        if self.fallback and not self._rec and now - self._last_check >= self.RECHECK_S:
            self._last_check = now
            if self._chosen_mic_visible():
                self._reconnect("chosen mic is back")

    def _start_watchdog(self) -> None:
        if self._watchdog_started:
            return
        self._watchdog_started = True

        def loop():
            while True:
                time.sleep(1.5)
                try:
                    self.watch_once()
                except Exception:
                    log.exception("mic watchdog")

        threading.Thread(target=loop, name="mic-watchdog", daemon=True).start()

    def _cb(self, indata, frames, time_info, status):
        self._last_cb = now = time.monotonic()
        mono = indata[:, 0].copy()
        with self._lock:
            rec = self._rec
            if rec:
                self._chunks.append(mono)
            elif self.keep_open:
                self._pre.append(mono)
        if rec:
            peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
            # exact zeros = no device behind the stream (a real mic always has some noise)
            self._zero_s = self._zero_s + len(mono) / self._rate if peak == 0.0 else 0.0
            lvl = float(np.sqrt(np.mean(mono * mono)))
            if 20 * np.log10(lvl + 1e-9) > self.voice_db:
                self.last_voice_t = now
                self.voiced_since_cut += len(mono) / self._rate
            if self.on_level:
                self.on_level(lvl)

    # -- recording -------------------------------------------------------------
    def start(self) -> None:
        if self.fallback and self._chosen_mic_visible():
            self._reconnect("chosen mic is back")      # costs ~0.1 s, only when it matters
        self.open()
        with self._lock:
            self._chunks = list(self._pre)
            self._pre.clear()
            self._rec = True
        self._zero_s = 0.0
        self._dead_handled = False
        self.last_voice_t = time.monotonic()
        self.voiced_since_cut = 0.0

    def _to16k(self, chunks) -> np.ndarray:
        if not chunks:
            return np.zeros(0, np.float32)
        audio = np.concatenate(chunks)
        if self._rate != RATE:
            n = int(len(audio) * RATE / self._rate)
            audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
        return audio

    def cut(self) -> np.ndarray:
        """Take everything recorded so far but keep recording (continuous mode)."""
        with self._lock:
            chunks, self._chunks = self._chunks, []
        self.voiced_since_cut = 0.0
        return self._to16k(chunks)

    def snapshot(self, seconds: float) -> np.ndarray:
        """Last N seconds of the current recording, without consuming it (live preview)."""
        with self._lock:
            chunks = list(self._chunks)
        need = int(seconds * self._rate / BLOCK) + 1
        return self._to16k(chunks[-need:])

    def stop(self) -> np.ndarray:
        with self._lock:
            self._rec = False
            chunks, self._chunks = self._chunks, []
        if self._dead_handled and self._zero_s >= self.DEAD_S:
            self._status("No sound from your mic. Is it muted?", True)
        if not self.keep_open:
            self.close()
        return self._to16k(chunks)

    @property
    def recording(self) -> bool:
        return self._rec
