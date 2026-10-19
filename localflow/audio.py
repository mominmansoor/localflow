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


def list_input_devices() -> list[tuple[int, str]]:
    import sounddevice as sd
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and d["hostapi"] == 0:   # MME list = one entry per device
            out.append((i, d["name"]))
    return out


def list_devices() -> str:
    import sounddevice as sd
    return str(sd.query_devices())


def _resolve_device(dev):
    if dev is None or isinstance(dev, int):
        return dev
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and str(dev).lower() in d["name"].lower():
            return i
    log.warning("Mic %r not found, using default", dev)
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
    def __init__(self, device=None, keep_open=True, preroll_ms=500,
                 on_level: Callable[[float], None] | None = None):
        self.device_spec = device
        self.device = _resolve_device(device)
        self.keep_open = keep_open
        self.on_level = on_level
        self.preroll_ms = preroll_ms
        self._pre: deque = deque(maxlen=max(1, int(preroll_ms / 1000 * RATE / BLOCK)))
        self._chunks: list[np.ndarray] = []
        self._rec = False
        self._lock = threading.Lock()
        self._stream = None
        self._rate = RATE
        self._last_cb = 0.0
        # silence tracking (continuous mode)
        self.voice_db = -45.0
        self.last_voice_t = 0.0
        self.voiced_since_cut = 0.0
        self._watchdog_started = False

    # -- config ------------------------------------------------------------------
    def configure(self, device=None, keep_open=True, preroll_ms=500) -> None:
        changed = device != self.device_spec
        self.keep_open = keep_open
        if preroll_ms != self.preroll_ms:
            self.preroll_ms = preroll_ms
            self._pre = deque(maxlen=max(1, int(preroll_ms / 1000 * RATE / BLOCK)))
        if changed:
            self.device_spec, self.device = device, _resolve_device(device)
            self.close()
        if keep_open and not self._rec:
            self._safe_open()
        elif not keep_open and not self._rec:
            self.close()

    # -- stream management ---------------------------------------------------
    def open(self) -> None:
        import sounddevice as sd
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
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _reconnect(self) -> None:
        """Re-scan devices (PortAudio caches the list) and reopen."""
        import sounddevice as sd
        log.info("Reconnecting microphone...")
        self.close()
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
        self.device = _resolve_device(self.device_spec)
        if not self._safe_open() and self.device is not None:
            self.device = None  # fall back to the system default mic
            self._safe_open()

    def _start_watchdog(self) -> None:
        if self._watchdog_started:
            return
        self._watchdog_started = True

        def loop():
            while True:
                time.sleep(1.5)
                want_open = self.keep_open or self._rec
                if not want_open:
                    continue
                s = self._stream
                dead = s is None or not s.active or (time.monotonic() - self._last_cb > 2.0)
                if dead:
                    self._reconnect()

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
            lvl = float(np.sqrt(np.mean(mono * mono)))
            if 20 * np.log10(lvl + 1e-9) > self.voice_db:
                self.last_voice_t = now
                self.voiced_since_cut += len(mono) / self._rate
            if self.on_level:
                self.on_level(lvl)

    # -- recording -------------------------------------------------------------
    def start(self) -> None:
        self.open()
        with self._lock:
            self._chunks = list(self._pre)
            self._pre.clear()
            self._rec = True
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
        if not self.keep_open:
            self.close()
        return self._to16k(chunks)

    @property
    def recording(self) -> bool:
        return self._rec
