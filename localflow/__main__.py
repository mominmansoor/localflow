"""python -m localflow [options]"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from logging.handlers import RotatingFileHandler

from . import config


def _setup_logging(debug: bool) -> None:
    config.DATA_DIR.mkdir(exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(config.DATA_DIR / "localflow.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")]
    if sys.stderr is not None:  # pythonw has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    for noisy in ("faster_whisper", "urllib3", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> int:
    ap = argparse.ArgumentParser(prog="localflow", description="Local, unlimited push-to-talk dictation")
    ap.add_argument("--debug", action="store_true", help="verbose console logging")
    ap.add_argument("--settings", action="store_true", help="open the settings app")
    ap.add_argument("--history", action="store_true", help="open the dictation history")
    ap.add_argument("--doctor", action="store_true", help="check GPU, mic, model cache, Ollama, hotkeys")
    ap.add_argument("--selftest", action="store_true", help="mic -> Whisper -> cleanup -> clipboard end-to-end test")
    ap.add_argument("--cheat-sheet", action="store_true", help="print your hotkeys, snippets and commands")
    ap.add_argument("--bundle-logs", action="store_true", help="zip redacted logs + diagnostics for bug reports")
    ap.add_argument("--export-settings", metavar="FILE")
    ap.add_argument("--import-settings", metavar="FILE")
    ap.add_argument("--list-mics", action="store_true")
    ap.add_argument("--download-model", action="store_true", help="pre-download the Whisper model")
    ap.add_argument("--test-cleanup", metavar="TEXT", help="run the text pipeline on TEXT")
    ap.add_argument("--serve", action="store_true", help="run only the local OpenAI-compatible API (no hotkeys)")
    args = ap.parse_args()
    _setup_logging(args.debug)
    s = config.Settings()

    if args.list_mics:
        from .audio import list_devices
        print(list_devices())
        return 0
    if args.download_model:
        from .stt import predownload
        predownload(s.get("whisper.model"))
        return 0
    if args.doctor or args.selftest:
        from . import diagnostics
        rows = diagnostics.doctor(s) if args.doctor else diagnostics.selftest(s)
        print(diagnostics.format_report(rows))
        return 1 if any(r[0] == "fail" for r in rows) else 0
    if args.cheat_sheet:
        from . import diagnostics
        print(diagnostics.cheat_sheet(s))
        return 0
    if args.bundle_logs:
        from . import diagnostics
        print(f"Saved {diagnostics.bundle_logs(s)}")
        return 0
    if args.export_settings:
        s.export(args.export_settings)
        print(f"Exported to {args.export_settings} (secret snippets left out)")
        return 0
    if args.import_settings:
        s.import_(args.import_settings)
        print("Imported. A running LocalFlow picks this up within a second.")
        return 0
    if args.test_cleanup:
        from . import cleanup, pipeline
        print("warm:", cleanup.warm_llm(s.get))
        t0 = time.perf_counter()
        r = pipeline.process(args.test_cleanup, s.effective())
        print(f"--- {r.info} | {(time.perf_counter() - t0) * 1000:.0f} ms ---")
        print(r.display if not r.command else f"[voice command] {r.command}")
        return 0
    if args.serve:
        from .server import ApiServer
        from .stt import Transcriber
        t = Transcriber(s.get)
        t.load()
        srv = ApiServer(lambda p, lang, prompt, tr: t.transcribe(p, language=lang, translate=tr,
                                                                 hotwords=[prompt] if prompt else None),
                        s.get("api_server.port"))
        srv.start()
        print(f"Serving on http://127.0.0.1:{srv.port}/v1 - Ctrl+C to stop")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0
    if args.settings or args.history:
        from PySide6.QtWidgets import QApplication
        from .app import send_to_running
        app = QApplication(sys.argv)
        if send_to_running("history" if args.history else "settings"):
            return 0
        from .ui.settings_window import SettingsWindow
        w = SettingsWindow(s)
        if args.history:
            w.open_page("History")
        w.show()
        return app.exec()

    from .app import main as run
    return run(s)


if __name__ == "__main__":
    sys.exit(main())
