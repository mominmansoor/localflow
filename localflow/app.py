"""Wires everything together: hotkeys -> mic -> Whisper -> pipeline -> delivery, plus tray & windows."""
from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import cleanup, config, delivery, diagnostics, hotkey, pipeline, winapi
from .audio import RATE, Recorder, denoise, list_input_devices, whisper_boost
from .history import History
from .overlay import Overlay
from .stt import MODELS, Transcriber

log = logging.getLogger("localflow")
SOCKET_NAME = "LocalFlowDictation"


class Bridge(QObject):
    """Thread-safe hop onto the Qt thread."""
    state = Signal(str)
    level = Signal(float)
    preview = Signal(str)
    message = Signal(str, bool)
    tooltip = Signal(str)
    fallback = Signal(str)
    paused = Signal(bool)
    settings_changed = Signal()
    notify = Signal(str, str)


@dataclass
class Session:
    kind: str                       # "dictate" | "rephrase"
    eff: object
    exe: str | None
    hwnd: int
    suppressed: bool = False
    locked: bool = False
    t0: float = field(default_factory=time.monotonic)
    active: bool = True


def _tray_icon(paused=False) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(20, 20, 22))
    p.setPen(QColor(255, 255, 255, 60))
    p.drawRoundedRect(4, 14, 56, 36, 18, 18)
    p.setPen(QColor(0, 0, 0, 0))
    p.setBrush(QColor(255, 196, 77) if paused else QColor(255, 255, 255))
    for i, h in enumerate((8, 16, 24, 16, 8)):
        p.drawRoundedRect(17 + i * 7, 32 - h // 2, 4, h, 2, 2)
    p.end()
    return QIcon(pm)


class LocalFlow:
    def __init__(self, settings: config.Settings):
        self.s = settings
        self.bridge = Bridge()
        config.DATA_DIR.mkdir(exist_ok=True)
        self.history = History(config.DATA_DIR / "history.db")
        g = self.s.get
        self.recorder = Recorder(g("audio.mic_device"), g("audio.keep_mic_open"), g("audio.preroll_ms"),
                                 on_level=self.bridge.level.emit)
        self.stt = Transcriber(self.s.get)
        self.machine = hotkey.HotkeyMachine({}, is_down=hotkey.win_is_down if sys.platform == "win32" else None)
        self._configure_hotkeys()
        self.listener = hotkey.HotkeyListener(self.machine, self._on_events)
        self.events: queue.Queue = queue.Queue()
        self.jobs: queue.Queue = queue.Queue()
        self.session: Session | None = None
        self.last_text = ""
        self.recent: deque = deque(maxlen=10)       # (display, actual)
        self._llm_warned = -1e9
        self.llm_models: list[str] = []
        self._warmed_sig = None
        self._max_timer: threading.Timer | None = None
        self.api = None
        self.settings_window = None
        self.bridge.settings_changed.connect(self._apply_settings_qt)

    # =======================================================================
    # settings
    # =======================================================================
    def _configure_hotkeys(self):
        g = self.s.get
        self.machine.configure(
            {k: g(f"hotkeys.{k}") for k in ("dictate", "rephrase", "paste_last", "pause")},
            lock_mode=g("hotkeys.lock_mode"), tap_ms=g("hotkeys.tap_ms"),
            double_tap_ms=g("hotkeys.double_tap_ms"), cancel_key=g("hotkeys.cancel"))

    def on_settings_changed(self):
        """Callable from any thread."""
        self.bridge.settings_changed.emit()

    def _apply_settings_qt(self):
        g = self.s.get
        self._configure_hotkeys()
        try:
            self.recorder.configure(g("audio.mic_device"), g("audio.keep_mic_open"), g("audio.preroll_ms"))
        except Exception:
            log.exception("mic reconfigure failed")
        self.stt.reload_if_needed()
        self._apply_overlay()
        self._sync_api()
        self.history.prune(g("history.retention_days") if g("history.enabled") else 0)
        # Only (re)load the AI model when something it depends on changed. Warming on
        # every toggle queued GPU work in front of real requests.
        sig = self._llm_signature()
        if g("cleanup.level") in ("medium", "high") and sig != self._warmed_sig:
            threading.Thread(target=self._warm_llm, daemon=True).start()

    def _llm_signature(self):
        g = self.s.get
        return (g("cleanup.llm_model"), g("cleanup.ollama_url"), g("cleanup.level"), g("cleanup.style"),
                tuple(g("vocabulary.hotwords") or []))

    def _apply_overlay(self):
        g = self.s.get
        ov = self.overlay
        ov.show_idle = bool(g("overlay.show_idle_pill"))
        margin = int(g("overlay.margin_px") or 0)
        moved = margin != ov.margin
        ov.margin = margin
        if ov.state in ("idle", "hidden", "paused", "done"):
            ov.set_state("idle")            # re-evaluates show_idle -> idle or hidden
        elif moved and ov.isVisible():
            ov._place()

    def _poll_settings_file(self):
        if self.s.reload_if_changed():
            log.info("config.json changed on disk - reloaded")
            self._apply_settings_qt()

    def machine_suspend(self, on: bool):
        self.machine.suspended = on

    def _sync_api(self):
        want = self.s.get("api_server.enabled")
        port = self.s.get("api_server.port")
        if self.api and (not want or self.api.port != port):
            self.api.stop()
            self.api = None
        if want and not self.api:
            from .server import ApiServer
            try:
                self.api = ApiServer(lambda path, lang, prompt, tr: self.stt.transcribe(
                    path, language=lang, translate=tr, hotwords=[prompt] if prompt else None), port)
                self.api.start()
            except OSError as e:
                self.bridge.message.emit(f"API port {port} busy: {e}"[:60], True)

    # =======================================================================
    # hotkey thread -> event thread
    # =======================================================================
    def _on_events(self, evs):  # inside the OS keyboard hook: must be instant
        for e in evs:
            self.events.put(e)

    def _event_loop(self):
        while True:
            ev = self.events.get()
            try:
                if ev == "_poll":
                    for e in self.machine.poll(time.monotonic()):
                        self._handle(e)
                else:
                    self._handle(ev)
            except Exception as e:
                log.exception("event %s failed", ev)
                self.bridge.message.emit(f"Error: {e}"[:60], True)

    def _set_state(self, st: str):
        self.bridge.state.emit(st)
        winapi.set_console_title(f"LocalFlow - {st}")

    def _handle(self, ev):
        kind, name = ev
        if kind == hotkey.FIRE:
            if name == "pause":
                self.bridge.paused.emit(self.machine.paused)
                self.bridge.message.emit("Hotkeys paused" if self.machine.paused else "Hotkeys on", False)
            elif name == "paste_last":
                if "win" in self.machine.bindings.get(name, ()):
                    hotkey.send_start_menu_mask()
                if self.last_text:
                    delivery.insert_text(self.last_text, self.s.get("delivery.method") if self.s.get("delivery.method") != "copy" else "paste",
                                         self.s.get("delivery.restore_clipboard"))
                else:
                    self.bridge.message.emit("Nothing dictated yet", False)
            return

        if kind == hotkey.START:
            if "win" in self.machine.bindings.get(name, ()):
                hotkey.send_start_menu_mask()
            hwnd = winapi.foreground_hwnd()
            exe = winapi.foreground_exe(hwnd)
            eff = self.s.effective(exe)
            self.session = Session(kind=name, eff=eff, exe=exe, hwnd=hwnd, suppressed=eff.suppressed)
            if eff.suppressed:
                self.bridge.message.emit(f"Dictation is off in {exe or 'this app'}", False)
                return
            self.recorder.start()
            self._set_state("rephrase" if name == "rephrase" else "listening")
            self._arm_max_timer()
            if eff.get("modes.live_preview"):
                threading.Thread(target=self._preview_loop, args=(self.session,), daemon=True).start()
            return

        sess = self.session
        if sess is None or sess.suppressed:
            if kind in (hotkey.STOP, hotkey.CANCEL) and sess:
                self.session = None
            return

        if kind == hotkey.ARMED:
            threading.Timer(self.machine.dtap_s + 0.03, lambda: self.events.put("_poll")).start()
        elif kind == hotkey.LOCK:
            sess.locked = True
            self._set_state("locked")
            if sess.eff.get("modes.continuous"):
                threading.Thread(target=self._continuous_loop, args=(sess,), daemon=True).start()
        elif kind == hotkey.CANCEL:
            self._end_session()
            self.recorder.stop()
            self._set_state("idle")
        elif kind == hotkey.STOP:
            self._end_session()
            audio = self.recorder.stop()
            if len(audio) < RATE * self.s.get("audio.min_recording_ms") / 1000:
                self._set_state("idle")
                return
            self._set_state("processing")
            self.jobs.put((audio, sess, False))

    def _end_session(self):
        self._disarm_max_timer()
        if self.session:
            self.session.active = False

    def _arm_max_timer(self):
        self._disarm_max_timer()

        def fire():
            for e in self.machine.force_stop():
                self.events.put(e)
        self._max_timer = threading.Timer(self.s.get("audio.max_recording_seconds"), fire)
        self._max_timer.daemon = True
        self._max_timer.start()

    def _disarm_max_timer(self):
        if self._max_timer:
            self._max_timer.cancel()
            self._max_timer = None

    # -- background loops while recording -----------------------------------------
    def _continuous_loop(self, sess: Session):
        silence = sess.eff.get("modes.continuous_silence_ms") / 1000
        self.recorder.voice_db = -52.0 if sess.eff.get("audio.whisper_mode") else -45.0
        while sess.active:
            time.sleep(0.1)
            r = self.recorder
            if r.voiced_since_cut >= 0.6 and time.monotonic() - r.last_voice_t >= silence:
                audio = r.cut()
                if sess.active and len(audio):
                    self.jobs.put((audio, sess, True))

    def _preview_loop(self, sess: Session):
        while sess.active:
            time.sleep(0.7)
            if not sess.active:
                break
            audio = self.recorder.snapshot(8.0)
            if len(audio) < RATE * 0.6:
                continue
            text = self.stt.try_preview(audio)
            if text and sess.active:
                self.bridge.preview.emit(text[-120:])

    # =======================================================================
    # worker: STT + pipeline + delivery (one job at a time on the GPU)
    # =======================================================================
    def _worker(self):
        while True:
            audio, sess, partial = self.jobs.get()
            try:
                self._process(audio, sess, partial)
            except Exception as e:
                log.exception("processing failed")
                self.bridge.message.emit(f"Error: {e}"[:60], True)

    def _process(self, audio: np.ndarray, sess: Session, partial: bool):
        g = sess.eff.get
        secs = len(audio) / RATE
        t0 = time.perf_counter()
        if g("audio.whisper_mode"):
            audio = whisper_boost(audio)
        if g("audio.noise_suppression"):
            audio = denoise(audio)
        raw = self.stt.transcribe(audio, translate=g("modes.translate"), language=g("whisper.language"),
                                  whisper_mode=g("audio.whisper_mode"))
        stt_ms = (time.perf_counter() - t0) * 1000
        still_recording = self.recorder.recording
        if not raw:
            if not still_recording:
                self._set_state("idle")
            return

        if sess.kind == "rephrase":
            self._do_rephrase(raw, sess, secs, stt_ms)
            return

        clip = ""
        if any("${clipboard}" in str(x.get("text", "")) + str(x.get("value", ""))
               for x in (g("snippets") or []) + (g("commands.items") or [])):
            clip = delivery.clip_get() or ""
        r = pipeline.process(raw, sess.eff, clip)
        if str(r.info.get("llm", "")).startswith("error") and time.monotonic() - self._llm_warned > 300:
            self._llm_warned = time.monotonic()
            self.bridge.message.emit("AI cleanup offline - is Ollama running?", True)
        hist_on = self.s.get("history.enabled")
        if r.command:
            delivery.run_command(r.command, clip)
            if hist_on:
                self.history.add(raw=raw, final=r.display, app=sess.exe, profile=sess.eff.profile, kind="command",
                                 audio_s=secs, stt_ms=round(stt_ms))
            if not still_recording:
                self._set_state("done")
            return

        text = r.text
        if g("delivery.add_trailing_space") and text and text[-1] in ".!?,:;":
            text += " "
        method = g("delivery.method")
        if method == "copy" or (g("delivery.fallback_window") and winapi.no_text_target()):
            delivery.clip_set(text)
            self.bridge.fallback.emit(text)
        else:
            delivery.insert_text(text, method, g("delivery.restore_clipboard"), g("delivery.auto_send") and not partial)
        self.last_text = text
        self.recent.appendleft((r.display, text if r.display == r.text else None))
        total_ms = (time.perf_counter() - t0) * 1000
        log.info("%.1fs audio | stt %dms | llm %s %dms | total %dms | %s | %s",
                 secs, stt_ms, r.info["llm"], r.info["llm_ms"], total_ms, sess.exe, sess.eff.profile)
        if hist_on:
            self.history.add(raw=raw, final=r.display, app=sess.exe, profile=sess.eff.profile, audio_s=secs,
                             stt_ms=round(stt_ms), llm_ms=r.info["llm_ms"], llm=r.info["llm"])
        self.bridge.tooltip.emit(f"LocalFlow - last: {total_ms:.0f} ms ({secs:.1f}s audio)")
        if not self.recorder.recording:
            self._set_state("done")

    def _do_rephrase(self, instruction: str, sess: Session, secs: float, stt_ms: float):
        selection = delivery.copy_selection()
        if not selection:
            self.bridge.message.emit("Select some text first, then hold the rephrase keys", True)
            return
        try:
            out = cleanup.rephrase(selection, instruction, sess.eff.get)
        except Exception as e:
            self.bridge.message.emit(f"Rephrase failed: {e}"[:60], True)
            return
        delivery.insert_text(out, "paste", sess.eff.get("delivery.restore_clipboard"))
        self.last_text = out
        if self.s.get("history.enabled"):
            self.history.add(raw=f"[{instruction}] {selection}", final=out, app=sess.exe, profile=sess.eff.profile,
                             kind="rephrase", audio_s=secs, stt_ms=round(stt_ms))
        self._set_state("done")

    # =======================================================================
    # startup helpers
    # =======================================================================
    def _warm_llm(self):
        models = cleanup.installed_models(self.s.get)
        self.llm_models = [m["name"] for m in models or []]
        model, note = cleanup.resolve_model(self.s.get, models)
        if model and note:
            # configured model isn't installed: switch to one that is, and say so
            log.info("LLM: %s", note)
            self.s.set("cleanup.llm_model", model)
            self.bridge.message.emit(note[:60], False)
        elif model is None:
            log.info("LLM: %s", note)
            self.bridge.message.emit(note[:60], True)
            return
        status = cleanup.warm_llm(self.s.get)
        log.info("LLM: %s", status)
        if status == "ok":
            self._warmed_sig = self._llm_signature()
        else:
            self.bridge.message.emit(status[:58], True)

    def _after_stt_load(self):
        self.stt.ready.wait()
        if self.stt.fallback_reason:
            self.bridge.message.emit("GPU failed - using CPU Whisper (see log)", True)
        self.bridge.tooltip.emit(f"LocalFlow - ready on {self.stt.device}")

    def _update_loop(self):
        while True:
            if self.s.get("update_check.enabled"):
                tag = diagnostics.check_update(self.s.get("update_check.repo"))
                if tag:
                    self.bridge.notify.emit("LocalFlow update", f"{tag} is available on GitHub.")
            time.sleep(86400)

    # =======================================================================
    # Qt side
    # =======================================================================
    def open_settings(self, page: str | None = None):
        from .ui.settings_window import SettingsWindow
        if self.settings_window is None:
            self.settings_window = SettingsWindow(self.s, self)
        if page:
            self.settings_window.open_page(page)
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def _show_fallback(self, text: str):
        from .ui.fallback import FallbackWindow
        if not hasattr(self, "_fallback"):
            self._fallback = FallbackWindow()
        self._fallback.show_text(text)

    def _set_paused(self, paused: bool):
        self.overlay.set_paused(paused)
        self.tray.setIcon(_tray_icon(paused))

    def _toggle_pause(self, on):
        self.machine.paused = bool(on)
        self._set_paused(bool(on))

    def _paste_last_from_tray(self):
        if self.last_text:
            QTimer.singleShot(250, lambda: threading.Thread(
                target=delivery.insert_text, args=(self.last_text, "paste", True), daemon=True).start())

    def _radio_menu(self, parent: QMenu, title: str, key: str, options: list[tuple]):
        m = parent.addMenu(title)
        grp = QActionGroup(m)
        grp.setExclusive(True)
        cur = self.s.get(key)
        for val, label in options:
            a = QAction(label, m, checkable=True, checked=(val == cur))
            a.triggered.connect(lambda _=False, v=val: (self.s.set(key, v), self.on_settings_changed()))
            grp.addAction(a)
            m.addAction(a)
        return m

    def _check_action(self, menu: QMenu, label: str, key: str):
        a = QAction(label, menu, checkable=True, checked=bool(self.s.get(key)))
        a.toggled.connect(lambda on: (self.s.set(key, bool(on)), self.on_settings_changed()))
        menu.addAction(a)

    def _rebuild_menu(self):
        m = self.menu
        m.clear()
        g = self.s.get
        lock = {"double_tap": "double-tap to lock", "single_tap": "tap to lock", "off": ""}[g("hotkeys.lock_mode")]
        hint = QAction(f"Hold {g('hotkeys.dictate')} to dictate" + (f" · {lock}" if lock else ""), m)
        hint.setEnabled(False)
        m.addAction(hint)
        m.addAction("Settings…", self.open_settings)
        m.addAction("History…", lambda: self.open_settings("History"))
        m.addSeparator()
        self._radio_menu(m, "Profile", "profiles.active", [(p, p) for p in (g("profiles.items") or {})])
        self._radio_menu(m, "Cleanup", "cleanup.level",
                         [("none", "None"), ("light", "Light (no AI)"), ("medium", "Medium"), ("high", "High")])
        self._radio_menu(m, "Writing style", "cleanup.style",
                         [("neutral", "Neutral"), ("formal", "Formal"), ("casual", "Casual"),
                          ("very_casual", "Very casual"), ("verbatim", "Verbatim")])
        if self.llm_models:
            self._radio_menu(m, "AI model", "cleanup.llm_model", [(n, n) for n in self.llm_models])
        self._check_action(m, "Translate to English", "modes.translate")
        self._check_action(m, "Continuous dictation", "modes.continuous")
        try:
            mics = [(None, "System default")] + [(n, n) for _, n in list_input_devices()]
        except Exception:
            mics = [(None, "System default")]
        self._radio_menu(m, "Microphone", "audio.mic_device", mics)
        self._radio_menu(m, "Whisper model", "whisper.model", [(x, x) for x in MODELS])
        m.addSeparator()
        rec = m.addMenu("Recent")
        if not self.recent:
            a = rec.addAction("Nothing yet")
            a.setEnabled(False)
        for display, actual in self.recent:
            label = display.replace("\n", " ")
            label = label[:60] + ("…" if len(label) > 60 else "")
            rec.addAction(label, lambda d=display, a=actual: QApplication.clipboard().setText(a or d))
        m.addAction("Paste last dictation", self._paste_last_from_tray)
        pause = QAction("Pause hotkeys", m, checkable=True, checked=self.machine.paused)
        pause.toggled.connect(self._toggle_pause)
        m.addAction(pause)
        m.addSeparator()
        m.addAction("Hotkey cheat sheet", self._show_cheat)
        m.addAction("Diagnostics…", lambda: self.open_settings("Advanced"))
        m.addSeparator()
        m.addAction("Restart LocalFlow", self.restart)
        m.addAction("Quit LocalFlow", QApplication.instance().quit)

    # -- restart (also used automatically when LocalFlow's own files are updated) ----
    def _code_stamp(self) -> float:
        pkg = os.path.dirname(os.path.abspath(__file__))
        newest = 0.0
        for d, _dirs, files in os.walk(pkg):
            if "__pycache__" in d:
                continue
            for f in files:
                if f.endswith(".py"):
                    try:
                        newest = max(newest, os.path.getmtime(os.path.join(d, f)))
                    except OSError:
                        pass
        return newest

    def _check_code_updated(self):
        stamp = self._code_stamp()
        if stamp <= self._code_loaded:
            self._code_pending = 0.0
            return
        busy = self.session is not None and self.session.active or not self.jobs.empty()
        if stamp != self._code_pending or busy:   # wait until files stop changing and we're idle
            self._code_pending = stamp
            return
        log.info("LocalFlow files were updated - restarting to load them")
        self.restart()

    def restart(self):
        import subprocess
        try:
            self.listener.stop()
            self.recorder.close()
            if self.api:
                self.api.stop()
            self._ipc.close()
            _release_single_instance()
            flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0  # DETACHED | NEW_PROCESS_GROUP
            subprocess.Popen([sys.executable, "-m", "localflow", "--restarted"], cwd=str(config.ROOT),
                             creationflags=flags, close_fds=True)
        except Exception:
            log.exception("restart failed")
        QApplication.instance().quit()

    def _show_cheat(self):
        from .ui.settings_window import TextDialog
        TextDialog(None, "Hotkey cheat sheet", diagnostics.cheat_sheet(self.s)).exec()

    def _start_ipc(self):
        QLocalServer.removeServer(SOCKET_NAME)
        self._ipc = QLocalServer()
        self._ipc.listen(SOCKET_NAME)

        def on_conn():
            c = self._ipc.nextPendingConnection()
            c.waitForReadyRead(500)
            msg = bytes(c.readAll()).decode(errors="ignore").strip()
            c.disconnectFromServer()
            self.open_settings("History" if msg == "history" else None)
        self._ipc.newConnection.connect(on_conn)

    def run(self) -> int:
        app = QApplication.instance() or QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        g = self.s.get

        self.overlay = Overlay(g("overlay.show_idle_pill"), g("overlay.margin_px"))
        b = self.bridge
        b.state.connect(self.overlay.set_state)
        b.level.connect(self.overlay.set_level)
        b.preview.connect(self.overlay.set_preview)
        b.message.connect(lambda t, err: self.overlay.show_message(t, err))
        b.paused.connect(self._set_paused)
        b.fallback.connect(self._show_fallback)

        self.tray = QSystemTrayIcon(_tray_icon())
        self.tray.setToolTip("LocalFlow - loading Whisper…")
        b.tooltip.connect(self.tray.setToolTip)
        b.notify.connect(lambda title, msg: self.tray.showMessage(title, msg))
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self._rebuild_menu)
        self._rebuild_menu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(lambda r: self.open_settings() if r == QSystemTrayIcon.DoubleClick else None)
        self.tray.show()

        self.stt.load_async()
        for target in (self._after_stt_load, self._event_loop, self._worker, self._update_loop):
            threading.Thread(target=target, name=target.__name__, daemon=True).start()
        if g("cleanup.level") in ("medium", "high"):
            threading.Thread(target=self._warm_llm, daemon=True).start()
        if g("audio.keep_mic_open"):
            try:
                self.recorder.open()
            except Exception as e:
                log.exception("mic open failed")
                self.overlay.show_message(f"Mic error: {e}"[:60])
        self.history.prune(g("history.retention_days") if g("history.enabled") else 0)
        self._sync_api()
        self._start_ipc()
        self._file_timer = QTimer()
        self._file_timer.timeout.connect(self._poll_settings_file)
        self._file_timer.start(1000)
        self._code_loaded, self._code_pending = self._code_stamp(), 0.0
        self._code_timer = QTimer()
        self._code_timer.timeout.connect(self._check_code_updated)
        self._code_timer.start(2000)
        self.listener.start()
        self.overlay.set_state("idle")
        self.overlay.show_message(f"LocalFlow is on · hold {g('hotkeys.dictate')} to dictate", error=False, ms=3500)
        log.info("LocalFlow running. Dictate: %s", g("hotkeys.dictate"))
        code = app.exec()
        self.listener.stop()
        self.recorder.close()
        if self.api:
            self.api.stop()
        return code


def send_to_running(msg: str) -> bool:
    """If LocalFlow is already running, ask it to open a window. Returns True if it was."""
    sock = QLocalSocket()
    sock.connectToServer(SOCKET_NAME)
    if not sock.waitForConnected(300):
        return False
    sock.write(msg.encode())
    sock.flush()
    sock.waitForBytesWritten(300)
    sock.disconnectFromServer()
    return True


_MUTEX = None


def _single_instance() -> bool:
    global _MUTEX
    if sys.platform != "win32":
        return True
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    h = k32.CreateMutexW(None, False, "Global\\LocalFlowDictation")
    if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        if h:
            k32.CloseHandle(ctypes.c_void_p(h))   # don't keep the other instance's mutex alive
        return False
    _MUTEX = h
    return True


def _release_single_instance() -> None:
    global _MUTEX
    if _MUTEX and sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(_MUTEX))
    _MUTEX = None


def main(settings: config.Settings | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841 - needed for QLocalSocket
    got = _single_instance()
    if not got and "--restarted" in sys.argv:
        # the old instance is shutting down; wait for it to let go
        import time as _t
        end = _t.monotonic() + 15
        while not got and _t.monotonic() < end:
            _t.sleep(0.3)
            got = _single_instance()
    if not got:
        send_to_running("settings")   # second launch = open Settings in the running app
        return 0
    diagnostics.install_crash_handler()
    return LocalFlow(settings or config.Settings()).run()
