"""--doctor, --selftest, --bundle-logs, cheat sheet, crash reports, update check."""
from __future__ import annotations

import json
import logging
import platform
import sys
import threading
import time
import traceback
import urllib.request
import zipfile
from datetime import datetime
from importlib import metadata
from pathlib import Path

from . import __version__, config

log = logging.getLogger(__name__)
OK, WARN, FAIL = "ok", "warn", "fail"


def _ver(pkg: str) -> str | None:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None


def doctor(settings) -> list[tuple[str, str, str]]:
    g = settings.get
    out: list[tuple[str, str, str]] = []
    add = lambda s, n, d: out.append((s, n, d))  # noqa: E731

    add(OK, "LocalFlow", f"{__version__} on Python {platform.python_version()} / {platform.platform()}")
    for pkg in ("faster-whisper", "ctranslate2", "PySide6-Essentials", "sounddevice", "pynput", "pyperclip"):
        v = _ver(pkg)
        add(OK if v else FAIL, pkg, v or "not installed")

    # GPU
    try:
        from .stt import add_nvidia_dll_dirs
        add_nvidia_dll_dirs()
        import ctranslate2
        n = ctranslate2.get_cuda_device_count()
        add(OK if n else WARN, "CUDA", f"{n} GPU(s) visible to CTranslate2" if n else "No CUDA GPU - Whisper will use CPU")
    except Exception as e:
        add(FAIL, "CUDA", str(e)[:160])

    # model cache
    from .stt import model_cached
    m = g("whisper.model")
    add(OK if model_cached(m) else WARN, "Whisper model", f"{m} {'cached' if model_cached(m) else 'not downloaded yet (first launch will download it)'}")

    # audio
    try:
        import numpy as np
        import sounddevice as sd
        dev = sd.query_devices(kind="input")
        add(OK, "Default mic", dev["name"])
        rec = sd.rec(int(0.5 * 16000), samplerate=16000, channels=1, dtype="float32")
        sd.wait()
        lvl = 20 * np.log10(float(np.sqrt(np.mean(rec * rec))) + 1e-9)
        add(OK if lvl > -80 else WARN, "Mic signal", f"{lvl:.0f} dBFS over 0.5 s" + ("" if lvl > -80 else " (silent - muted or wrong mic?)"))
    except Exception as e:
        add(FAIL, "Microphone", str(e)[:160])

    # Ollama
    from . import cleanup
    status = cleanup.warm_llm(g) if g("cleanup.level") in ("medium", "high") else "AI cleanup disabled by level"
    add(OK if status == "ok" else WARN, "Ollama", status if status != "ok" else f"{g('cleanup.llm_model')} loaded")

    # hotkeys
    from .hotkey import parse_hotkey
    seen = {}
    for name in ("dictate", "rephrase", "paste_last", "pause"):
        spec = g(f"hotkeys.{name}")
        if not spec:
            continue
        try:
            combo = parse_hotkey(spec)
        except ValueError:
            add(FAIL, f"Hotkey {name}", f"invalid: {spec!r}")
            continue
        if combo in seen:
            add(FAIL, f"Hotkey {name}", f"same as {seen[combo]} ({spec})")
        else:
            seen[combo] = name
            add(OK, f"Hotkey {name}", spec)

    # recent errors
    logf = config.DATA_DIR / "localflow.log"
    errs = []
    if logf.exists():
        errs = [ln.strip() for ln in logf.read_text(encoding="utf-8", errors="replace").splitlines() if " ERROR " in ln][-5:]
    add(OK if not errs else WARN, "Recent errors", "none" if not errs else "\n".join(errs))
    crashes = sorted((config.DATA_DIR / "crash").glob("*.txt")) if (config.DATA_DIR / "crash").exists() else []
    if crashes:
        add(WARN, "Crash reports", f"{len(crashes)} in data/crash (latest {crashes[-1].name})")
    return out


def format_report(rows) -> str:
    icon = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}
    return "\n".join(f"{icon[s]} {n}: {d}" for s, n, d in rows)


def selftest(settings) -> list[tuple[str, str, str]]:
    """End-to-end: mic -> Whisper -> cleanup -> clipboard."""
    import numpy as np
    out = []
    g = settings.get
    try:
        import sounddevice as sd
        audio = sd.rec(int(1.5 * 16000), samplerate=16000, channels=1, dtype="float32")[:, 0]
        sd.wait()
        out.append((OK, "Mic capture", f"{len(audio) / 16000:.1f} s captured"))
    except Exception as e:
        audio = np.zeros(16000, np.float32)
        out.append((FAIL, "Mic capture", str(e)[:160]))
    try:
        from .stt import Transcriber
        t = Transcriber(g)
        t0 = time.perf_counter()
        t.load()
        load_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        txt = t.transcribe(audio)
        out.append((OK if not t.fallback_reason else WARN, "Whisper",
                    f"{t.device}: loaded in {load_s:.1f}s, 1.5 s clip in {(time.perf_counter() - t0) * 1000:.0f} ms -> {txt!r}"))
    except Exception as e:
        out.append((FAIL, "Whisper", str(e)[:160]))
    try:
        from . import pipeline
        t0 = time.perf_counter()
        r = pipeline.process("um so I I think the the build uh failed on friday", settings.effective())
        out.append((OK, "Cleanup", f"{(time.perf_counter() - t0) * 1000:.0f} ms ({r.info['llm']}) -> {r.text!r}"))
    except Exception as e:
        out.append((FAIL, "Cleanup", str(e)[:160]))
    try:
        from .delivery import clip_get, clip_set
        old = clip_get()
        clip_set("localflow-selftest")
        ok = clip_get() == "localflow-selftest"
        if old is not None:
            clip_set(old)
        out.append((OK if ok else FAIL, "Clipboard", "read/write ok" if ok else "could not round-trip"))
    except Exception as e:
        out.append((FAIL, "Clipboard", str(e)[:160]))
    return out


def cheat_sheet(settings) -> str:
    g = settings.get
    lock = {"double_tap": "double-tap for hands-free, tap again to stop",
            "single_tap": "quick tap for hands-free, tap again to stop", "off": "no hands-free"}[g("hotkeys.lock_mode")]
    lines = [
        "HOTKEYS",
        f"  {g('hotkeys.dictate'):<18} hold to dictate ({lock})",
        f"  {g('hotkeys.rephrase') or '-':<18} hold with text selected, say how to rewrite it",
        f"  {g('hotkeys.paste_last') or '-':<18} paste last dictation again",
        f"  {g('hotkeys.pause') or '-':<18} pause / resume all LocalFlow hotkeys",
        f"  {g('hotkeys.cancel'):<18} cancel current recording",
        "",
        f"PROFILE: {g('profiles.active')}    CLEANUP: {g('cleanup.level')}    STYLE: {g('cleanup.style')}",
    ]
    snips = g("snippets") or []
    if snips:
        lines += ["", "SNIPPETS (say anywhere in a dictation)"] + [f"  \"{s['trigger']}\"" for s in snips]
    cmds = g("commands.items") or []
    if cmds and g("commands.enabled"):
        lines += ["", "VOICE COMMANDS (say on their own)"] + [f"  \"{c['trigger']}\" -> {c['action']}: {c['value'] if c['action'] != 'run' else '(shell command)'}" for c in cmds]
    if g("formatting.inline_commands"):
        lines += ["", "SPOKEN PUNCTUATION", "  " + ", ".join(sorted(g("formatting.inline_replacements")))]
    return "\n".join(lines)


def bundle_logs(settings) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = config.DATA_DIR / f"localflow-logs-{stamp}.zip"
    data = json.loads(json.dumps(settings.data))
    for s in data.get("snippets", []):
        s["text"] = "***" if s.get("secret") else f"<{len(s.get('text', ''))} chars>"
    for c in data.get("commands", {}).get("items", []):
        if c.get("action") == "run":
            c["value"] = "<redacted>"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in config.DATA_DIR.glob("localflow.log*"):
            z.write(f, f.name)
        for f in (config.DATA_DIR / "crash").glob("*.txt") if (config.DATA_DIR / "crash").exists() else []:
            z.write(f, f"crash/{f.name}")
        z.writestr("settings-redacted.json", json.dumps(data, indent=2))
        try:
            z.writestr("doctor.txt", format_report(doctor(settings)))
        except Exception as e:
            z.writestr("doctor.txt", f"doctor failed: {e}")
    return dest


def install_crash_handler() -> None:
    crash_dir = config.DATA_DIR / "crash"

    def dump(kind, exc_type, exc, tb):
        try:
            crash_dir.mkdir(parents=True, exist_ok=True)
            p = crash_dir / f"crash-{datetime.now():%Y%m%d-%H%M%S}.txt"
            p.write_text(
                f"LocalFlow {__version__} | Python {sys.version} | {platform.platform()}\n{kind}\n\n"
                + "".join(traceback.format_exception(exc_type, exc, tb)), encoding="utf-8")
            log.error("Crash report written to %s", p)
        except Exception:
            pass

    prev = sys.excepthook

    def hook(t, e, tb):
        dump("main thread", t, e, tb)
        prev(t, e, tb)

    sys.excepthook = hook
    threading.excepthook = lambda a: dump(f"thread {a.thread.name if a.thread else '?'}", a.exc_type, a.exc_value, a.exc_traceback)


def check_update(repo: str) -> str | None:
    """Returns the newer tag name if a GitHub release is newer than this version."""
    try:
        with urllib.request.urlopen(f"https://api.github.com/repos/{repo}/releases/latest", timeout=5) as r:
            tag = json.loads(r.read()).get("tag_name", "")
    except Exception:
        return None
    def parse(v):
        return tuple(int(x) for x in v.lstrip("v").split(".") if x.isdigit())
    try:
        return tag if parse(tag) > parse(__version__) else None
    except ValueError:
        return None
