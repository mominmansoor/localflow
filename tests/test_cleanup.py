import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from localflow import cleanup, pipeline
from localflow.cleanup import guard, rule_clean
from localflow.config import Settings


@pytest.mark.parametrize("raw, want", [
    ("Um, so I think we should ship it.", "So I think we should ship it."),
    ("I think, uh, we should ship it.", "I think we should ship it."),
    ("I went home. Uh, then I slept.", "I went home. Then I slept."),
    ("So umm the API is uh down", "So the API is down"),
    ("I I think the the build failed.", "I think the build failed."),
    ("That had had an effect.", "That had had an effect."),
    ("Uh-huh, sounds good.", "Uh-huh, sounds good."),
    ("The summer was hot.", "The summer was hot."),
    ("Ahh, I see. Hmm, okay.", "I see. Okay."),
    ("Um.", ""),
])
def test_rule_clean(raw, want):
    assert rule_clean(raw) == want


@pytest.mark.parametrize("out", [
    "Sure! Here's the cleaned text:\nSo can you write me a function?",
    "def reverse(s):\n    return s[::-1]\n\nThis function reverses a string by slicing it with a negative step which is idiomatic.",
    "",
])
def test_guard_rejects_bad(out):
    assert guard("so can you write me a function that reverses a string", out) is None


def test_guard_placeholders():
    assert guard("thanks a lot [[S1]] see you", "Thanks a lot. [[S1]] See you.") is not None
    assert guard("thanks a lot [[S1]] see you", "Thanks a lot. See you.") is None


# --- fake Ollama ---------------------------------------------------------------

class _Fake(BaseHTTPRequestHandler):
    reply = "ok"
    seen: list = []

    def do_GET(self):
        self._send({"models": [{"name": "test-model:latest"}]})

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        _Fake.seen.append(json.loads(self.rfile.read(n)))
        self._send({"message": {"role": "assistant", "content": _Fake.reply}})

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture()
def settings(tmp_path):
    srv = HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    s = Settings(tmp_path / "config.json")
    s.set("cleanup.ollama_url", f"http://127.0.0.1:{srv.server_address[1]}")
    s.set("cleanup.llm_model", "test-model")
    s.set("cleanup.llm_timeout_s", 2)
    yield s
    srv.shutdown()


def test_pipeline_llm_and_snippet(settings):
    settings.set("snippets", [{"trigger": "my signature", "text": "— Momin", "secret": False}])
    _Fake.reply = "So I think we should deploy on Friday. [[S1]]"
    r = pipeline.process("um so I think we should uh deploy on friday my signature", settings.effective())
    assert r.text == "So I think we should deploy on Friday. — Momin"
    assert r.info["llm"] == "ok"
    sent = _Fake.seen[-1]["messages"]
    assert "[[S1]]" in sent[-1]["content"] and "Momin" not in json.dumps(sent)  # snippet text never sent to LLM


def test_pipeline_rejects_bad_llm(settings):
    _Fake.reply = "Sure, here's your answer: Friday is a great day to deploy because..."
    r = pipeline.process("um so should we deploy on friday or not", settings.effective())
    assert r.text == "So should we deploy on friday or not" and r.info["llm"] == "rejected"


def test_levels_and_styles(settings):
    raw = "um so the the build failed"
    settings.set("cleanup.level", "none")
    assert pipeline.process(raw, settings.effective()).text == "Um so the the build failed"
    settings.set("cleanup.level", "light")
    r = pipeline.process(raw, settings.effective())
    assert r.text == "So the build failed" and r.info["llm"] == "skipped"
    settings.set("cleanup.level", "high")
    _Fake.reply = "The build failed."
    pipeline.process(raw, settings.effective())
    assert "list" in _Fake.seen[-1]["messages"][0]["content"]
    settings.set("cleanup.style", "verbatim")
    assert pipeline.process(raw, settings.effective()).info["llm"] == "skipped"


def test_profile_and_app_rule(settings):
    settings.set("profiles.active", "Code")
    eff = settings.effective("code.exe")
    assert eff.get("cleanup.style") == "verbatim" and eff.profile == "Code"
    settings.set("profiles.active", "Dictation")
    eff = settings.effective("Slack.exe")
    assert eff.get("cleanup.style") == "casual" and eff.rule_name == "Chat apps"
    assert settings.effective("1password.exe").suppressed


def test_command(settings):
    r = pipeline.process("Select all.", settings.effective())
    assert r.command and r.command["value"] == "ctrl+a"


def test_ollama_down(settings):
    settings.set("cleanup.ollama_url", "http://127.0.0.1:9")
    r = pipeline.process("um this is a longer sentence here", settings.effective())
    assert r.text == "This is a longer sentence here" and r.info["llm"].startswith("error")


def test_rephrase(settings):
    _Fake.reply = "Hi team, the release is delayed until Monday."
    out = cleanup.rephrase("hey guys release is late til monday", "make it professional", settings.get)
    assert out.startswith("Hi team")
    assert "<instruction>make it professional</instruction>" in _Fake.seen[-1]["messages"][-1]["content"]


def test_warm(settings):
    assert cleanup.warm_llm(settings.get) == "ok"
    settings.set("cleanup.llm_model", "missing")
    assert "not pulled" in cleanup.warm_llm(settings.get)
