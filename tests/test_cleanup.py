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
    assert "not installed" in cleanup.warm_llm(settings.get)


def test_guard_accepts_self_correction_shrink():
    src = "Oh wait, I just want to let you know that I like football, oh wait, I like tennis."
    assert guard(src, "I just want to let you know that I like tennis.") is not None
    assert guard(src, "I like tennis.") is not None


def test_guard_still_rejects_summaries_without_correction():
    src = "We should ship the onboarding flow this week because the old one confuses people a lot"
    assert guard(src, "Ship it.") is None


def test_guard_accepts_quotes():
    src = "when I said can you send it today he told me not yet"
    assert guard(src, 'When I said, "Can you send it today?" he told me, "Not yet."') is not None


def test_prompt_has_quote_and_correction_rules(settings):
    _Fake.reply = "I like tennis."
    r = pipeline.process("Oh wait, I like football, oh wait, I like tennis.", settings.effective())
    assert r.text == "I like tennis." and r.info["llm"] == "ok"
    system = _Fake.seen[-1]["messages"][0]["content"]
    assert "quotation marks" in system and "oh wait" in system


def _m(name, gb, params=""):
    return {"name": name, "size_gb": gb, "params": params}


def test_installed_models_and_resolve(settings):
    models = cleanup.installed_models(settings.get)
    assert [m["name"] for m in models] == ["test-model:latest"]
    assert cleanup.resolve_model(settings.get, models) == ("test-model:latest", "")
    settings.set("cleanup.llm_model", "qwen3:4b-instruct")
    model, note = cleanup.resolve_model(settings.get, models)
    assert model == "test-model:latest" and "isn't installed" in note


def test_resolve_when_ollama_down_or_empty(settings):
    assert cleanup.resolve_model(settings.get, None)[0] is None
    assert "ollama pull" in cleanup.resolve_model(settings.get, [])[1]


def test_pick_model_prefers_known_good_then_smallest():
    assert cleanup.pick_model([_m("llama3.1:8b", 4.9), _m("deepseek-r1:70b", 40)]) == "llama3.1:8b"
    assert cleanup.pick_model([_m("gemma3:4b", 3.3), _m("llama3.1:8b", 4.9)]) == "gemma3:4b"
    assert cleanup.pick_model([_m("weird:20b", 12), _m("other:7b", 4.1), _m("tiny:0.5b", 0.4)]) == "other:7b"
    assert cleanup.pick_model([]) is None


def test_same_model():
    assert cleanup.same_model("llama3.1:8b", "llama3.1:8b")
    assert cleanup.same_model("mistral:latest", "mistral")
    assert not cleanup.same_model("qwen3:4b", "qwen3:4b-instruct")


@pytest.mark.parametrize("source, out", [
    ("Hmm, okay, yeah, sure.", "Nothing to fix here."),
    ("uh yeah so okay", "No changes needed."),
    ("Testing testing one two", "The text is already clean as is."),
    ("mm hmm right yeah", "There's nothing to clean up in this transcript."),
    ("so yeah okay cool", "I can't see any speech to clean."),
    ("um okay so yeah", "It looks like you were testing the microphone."),   # invented words
])
def test_guard_rejects_commentary_on_short_input(source, out):
    assert guard(source, out) is None


@pytest.mark.parametrize("source, out", [
    ("sounds good", "Sounds good."),
    ("okay yeah that works for me", "Okay, yeah, that works for me."),
    ("there's nothing to fix in the code", "There's nothing to fix in the code."),   # user actually said it
    ("first milk second eggs", "1. Milk\n2. Eggs"),
    ("we should ship on friday no wait monday", "We should ship on Monday."),
])
def test_guard_keeps_real_cleanups(source, out):
    assert guard(source, out) is not None


def test_noise_falls_back_to_what_was_said(settings):
    _Fake.reply = "Nothing to fix here."
    r = pipeline.process("Okay, yeah, sounds good then.", settings.effective())
    assert r.text == "Okay, yeah, sounds good then." and r.info["llm"] == "rejected"
    assert "NEVER reply with a remark" in _Fake.seen[-1]["messages"][0]["content"]


def test_pure_filler_produces_nothing(settings):
    r = pipeline.process("Um. Uh. Hmm.", settings.effective())
    assert r.text == ""


@pytest.mark.parametrize("source, out", [
    ("what's the capital of france", "The capital of France is Paris."),
    ("what's two plus two", "Two plus two is four."),
    ("um who wrote hamlet", "Shakespeare wrote Hamlet."),
])
def test_guard_rejects_answered_questions(source, out):
    assert guard(source, out) is None


@pytest.mark.parametrize("source, out", [
    ("what's the capital of france", "What's the capital of France?"),
    ("what I want to do is ship it", "What I want to do is ship it."),
    ("how many legs does a spider have", "How many legs does a spider have?"),
])
def test_guard_keeps_cleaned_questions(source, out):
    assert guard(source, out) is not None
