import json
import time
import urllib.request
import uuid

import numpy as np

from localflow.audio import whisper_boost
from localflow.history import History
from localflow.server import ApiServer


def test_history(tmp_path):
    h = History(tmp_path / "h.db")
    h.add(raw="um deploy to vercel", final="Deploy to Vercel.", app="code.exe")
    h.add(raw="ping supabase", final="Ping Supabase and Vercel.", app="slack.exe")
    h.add(raw="x", final="Ask GitHub about the PR.", app=None)
    assert [r["app"] for r in h.search("vercel")] == ["slack.exe", "code.exe"]
    assert h.stats()["count"] == 3
    words = dict(h.suggest_hotwords())
    assert words.get("Vercel") == 2 and "Ask" not in words
    assert h.suggest_hotwords(existing=["vercel"]) == []
    h._db.execute("UPDATE dictations SET ts = ? WHERE app = 'code.exe'", (time.time() - 40 * 86400,))
    assert h.prune(30) == 1 and h.stats()["count"] == 2
    assert h.prune(0) == 2


def test_whisper_boost():
    quiet = (np.sin(np.linspace(0, 400, 16000)) * 0.005).astype(np.float32)
    boosted = whisper_boost(quiet)
    assert boosted.max() > quiet.max() * 5 and boosted.max() <= 1.0


def test_api_server():
    calls = []

    def fake(path, language, prompt, translate):
        calls.append((open(path, "rb").read(), language, translate))
        return "hello world"

    srv = ApiServer(fake, port=0)
    port = srv._srv.server_address[1]
    srv.start()
    try:
        b = uuid.uuid4().hex
        body = (f"--{b}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-1\r\n"
                f"--{b}\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\nen\r\n"
                f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
                f"Content-Type: audio/wav\r\n\r\n").encode() + b"RIFFDATA" + f"\r\n--{b}--\r\n".encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/audio/transcriptions", data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        assert json.loads(urllib.request.urlopen(req).read()) == {"text": "hello world"}
        assert calls == [(b"RIFFDATA", "en", False)]
        models = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models").read())
        assert models["data"][0]["id"] == "whisper-1"
    finally:
        srv.stop()
