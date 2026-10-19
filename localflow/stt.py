"""Speech-to-text with faster-whisper, kept resident on the GPU.

One lock serialises GPU use between dictations, live preview and the local API.
"""
from __future__ import annotations

import glob
import logging
import os
import sys
import threading
import time

import numpy as np

log = logging.getLogger(__name__)

MODELS = ["large-v3-turbo", "large-v3", "distil-large-v3", "medium", "small", "base", "tiny"]

_HALLUCINATIONS = {
    "thanks for watching", "thank you for watching", "subtitles by the amara.org community", "please subscribe",
}


def add_nvidia_dll_dirs() -> None:
    """Make pip-installed cuBLAS / cuDNN DLLs visible to CTranslate2 on Windows."""
    if sys.platform != "win32":
        return
    try:
        import nvidia
    except ImportError:
        return
    for base in nvidia.__path__:
        for d in glob.glob(os.path.join(base, "*", "bin")):
            try:
                os.add_dll_directory(d)
            except OSError:
                pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def predownload(model: str = "large-v3-turbo") -> None:
    from faster_whisper import download_model
    print(f"Downloading Whisper model '{model}' (one-time)...")
    download_model(model)
    print("Done.")


def model_cached(model: str) -> bool:
    try:
        from faster_whisper import download_model
        download_model(model, local_files_only=True)
        return True
    except Exception:
        return False


class Transcriber:
    def __init__(self, get):
        self.get = get                     # settings getter
        self.model = None
        self.device = None
        self.loaded_name = None
        self.fallback_reason: str | None = None
        self.ready = threading.Event()
        self.lock = threading.Lock()

    def load_async(self) -> None:
        threading.Thread(target=self.load, name="stt-load", daemon=True).start()

    def reload_if_needed(self) -> None:
        g = self.get
        want = (g("whisper.model"), g("whisper.device"), g("whisper.compute_type"))
        if self.loaded_name and want != self.loaded_name and self.ready.is_set():
            log.info("Whisper settings changed -> reloading")
            self.ready.clear()
            self.load_async()

    def load(self) -> None:
        g = self.get
        t0 = time.perf_counter()
        add_nvidia_dll_dirs()
        from faster_whisper import WhisperModel
        name, dev, ct = g("whisper.model"), g("whisper.device"), g("whisper.compute_type")
        self.fallback_reason = None
        try:
            m = WhisperModel(name, device=dev, compute_type=ct)
            self._warm(m)                  # CUDA errors show up on first inference
            self.device = f"{dev}/{ct} ({name})"
        except Exception as e:
            log.exception("GPU Whisper failed, falling back to CPU")
            self.fallback_reason = str(e).splitlines()[0][:200]
            m = WhisperModel(g("whisper.cpu_fallback_model"), device="cpu", compute_type="int8")
            self._warm(m)
            self.device = f"cpu/int8 ({g('whisper.cpu_fallback_model')})"
        with self.lock:
            self.model = m
        self.loaded_name = (name, dev, ct)
        log.info("Whisper ready on %s in %.1fs", self.device, time.perf_counter() - t0)
        self.ready.set()

    def _warm(self, m) -> None:
        noise = (np.random.default_rng(0).standard_normal(16000) * 0.01).astype(np.float32)
        segs, _ = m.transcribe(noise, language=self.get("whisper.language") or "en", beam_size=1,
                               vad_filter=False, without_timestamps=True)
        list(segs)

    def _prompt(self, hotwords) -> str | None:
        words = [w for w in (hotwords or []) if w]
        return ("Glossary: " + ", ".join(words) + ".") if words else None

    def transcribe(self, audio, *, language=None, translate=False, beam_size=None, vad=None,
                   hotwords=None, whisper_mode=False, timeout=180) -> str:
        """`audio` is a float32 16 kHz array or a file path."""
        if not self.ready.wait(timeout=timeout) or self.model is None:
            raise RuntimeError("Whisper model not loaded")
        g = self.get
        vad = g("whisper.vad_filter") if vad is None else vad
        with self.lock:
            segments, _ = self.model.transcribe(
                audio,
                task="translate" if translate else "transcribe",
                language=(language if language is not None else g("whisper.language")) or None,
                beam_size=beam_size or g("whisper.beam_size"),
                vad_filter=vad,
                vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 200,
                                "threshold": 0.3 if whisper_mode else 0.5},
                condition_on_previous_text=False,
                without_timestamps=True,
                initial_prompt=self._prompt(hotwords if hotwords is not None else g("vocabulary.hotwords")),
            )
            text = " ".join(s.text.strip() for s in segments).strip()
        if text.lower().strip(" .!") in _HALLUCINATIONS:
            return ""
        return text

    def try_preview(self, audio) -> str | None:
        """Fast greedy pass for the live preview; skipped if the GPU is busy."""
        if self.model is None or not self.lock.acquire(blocking=False):
            return None
        try:
            segs, _ = self.model.transcribe(audio, language=self.get("whisper.language") or None, beam_size=1,
                                            vad_filter=False, without_timestamps=True,
                                            condition_on_previous_text=False)
            return " ".join(s.text.strip() for s in segs).strip()
        except Exception:
            return None
        finally:
            self.lock.release()
