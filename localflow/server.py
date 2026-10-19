"""Local OpenAI-compatible transcription API (localhost only).

POST /v1/audio/transcriptions  (multipart: file, [language], [prompt], [response_format])
POST /v1/audio/translations
GET  /v1/models
Lets Cursor, Open WebUI etc. use your GPU Whisper instead of a cloud API.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)


def parse_multipart(content_type: str, body: bytes) -> dict:
    msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
    fields = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        fn = part.get_filename()
        fields[name] = {"filename": fn, "data": payload} if fn else payload.decode("utf-8", "replace")
    return fields


def make_handler(transcribe):
    """transcribe(path, language, prompt, translate) -> text"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "LocalFlow/1"

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.rstrip("/") == "/v1/models":
                return self._json(200, {"object": "list", "data": [{"id": "whisper-1", "object": "model"}]})
            self._json(404, {"error": {"message": "not found"}})

        def do_POST(self):
            route = self.path.split("?")[0].rstrip("/")
            if route not in ("/v1/audio/transcriptions", "/v1/audio/translations"):
                return self._json(404, {"error": {"message": "not found"}})
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ctype:
                return self._json(400, {"error": {"message": "expected multipart/form-data"}})
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            fields = parse_multipart(ctype, body)
            f = fields.get("file")
            if not isinstance(f, dict):
                return self._json(400, {"error": {"message": "missing 'file'"}})
            suffix = os.path.splitext(f["filename"] or "")[1] or ".wav"
            fd, path = tempfile.mkstemp(suffix=suffix)
            try:
                with os.fdopen(fd, "wb") as out:
                    out.write(f["data"])
                text = transcribe(path, fields.get("language") or None, fields.get("prompt") or None,
                                  route.endswith("translations"))
            except Exception as e:
                log.exception("API transcription failed")
                return self._json(500, {"error": {"message": str(e)}})
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            if fields.get("response_format") == "text":
                body = text.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._json(200, {"text": text})

        def log_message(self, fmt, *args):
            log.debug("api: " + fmt, *args)

    return Handler


class ApiServer:
    def __init__(self, transcribe, port: int = 7777):
        self.port = port
        self._srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(transcribe))

    def start(self) -> None:
        threading.Thread(target=self._srv.serve_forever, name="api", daemon=True).start()
        log.info("OpenAI-compatible API on http://127.0.0.1:%d/v1", self.port)

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()
