"""End-to-end event flow with fake mic / Whisper / keyboard."""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from localflow import app as appmod  # noqa: E402
from localflow import config, delivery, hotkey, winapi  # noqa: E402
from localflow.secrets import protect  # noqa: E402


class FakeRecorder:
    def __init__(self):
        self.recording = False
        self.started = 0

    def start(self):
        self.recording, self.started = True, self.started + 1

    def stop(self):
        self.recording = False
        return np.zeros(16000 * 2, np.float32)


class FakeSTT:
    def __init__(self, text):
        self.text = text

    def transcribe(self, audio, **kw):
        return self.text


@pytest.fixture()
def lf(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    s = config.Settings(tmp_path / "c.json")
    s.set("cleanup.level", "light")
    inst = appmod.LocalFlow(s)
    inst.recorder = FakeRecorder()
    sent, ran = [], []
    monkeypatch.setattr(delivery, "insert_text", lambda text, *a, **k: sent.append(text))
    monkeypatch.setattr(delivery, "run_command", lambda cmd, clip="": ran.append(cmd))
    monkeypatch.setattr(delivery, "clip_get", lambda: "")
    monkeypatch.setattr(winapi, "foreground_exe", lambda hwnd=None: "notepad.exe")
    monkeypatch.setattr(winapi, "no_text_target", lambda hwnd=None: False)
    monkeypatch.setattr(hotkey, "send_start_menu_mask", lambda: None)
    inst.sent, inst.ran = sent, ran
    return inst


def run(lf, events):
    for e in events:
        lf._handle(e)
    while not lf.jobs.empty():
        lf._process(*lf.jobs.get())


def test_hold_dictation(lf):
    lf.stt = FakeSTT("Um, so the the build failed.")
    run(lf, [("start", "dictate"), ("stop", "dictate")])
    assert lf.sent == ["So the build failed. "]
    assert lf.history.search()[0]["final"] == "So the build failed."
    assert lf.history.search()[0]["app"] == "notepad.exe"


def test_lock_then_stop(lf):
    lf.stt = FakeSTT("Hands free works.")
    run(lf, [("start", "dictate"), ("armed", "dictate"), ("lock", "dictate")])
    assert lf.recorder.recording and lf.session.locked
    run(lf, [("stop", "dictate")])
    assert lf.sent == ["Hands free works. "]


def test_secret_snippet_masked_in_history(lf):
    lf.s.set("snippets", [{"trigger": "my password", "text": protect("hunter2"), "secret": True}])
    lf.stt = FakeSTT("My password.")
    run(lf, [("start", "dictate"), ("stop", "dictate")])
    assert lf.sent == ["hunter2"]
    assert lf.history.search()[0]["final"] == "[my password]"
    assert lf.recent[0] == ("[my password]", None)


def test_voice_command(lf):
    lf.stt = FakeSTT("Select all.")
    run(lf, [("start", "dictate"), ("stop", "dictate")])
    assert lf.ran and lf.ran[0]["value"] == "ctrl+a" and not lf.sent


def test_suppressed_app(lf, monkeypatch):
    monkeypatch.setattr(winapi, "foreground_exe", lambda hwnd=None: "1password.exe")
    lf.stt = FakeSTT("secret stuff")
    run(lf, [("start", "dictate"), ("stop", "dictate")])
    assert lf.recorder.started == 0 and not lf.sent


def test_app_rule_style_applies(lf, monkeypatch):
    monkeypatch.setattr(winapi, "foreground_exe", lambda hwnd=None: "slack.exe")
    lf.stt = FakeSTT("x")
    run(lf, [("start", "dictate")])
    assert lf.session.eff.get("cleanup.style") == "casual"


def test_cancel(lf):
    lf.stt = FakeSTT("never sent")
    run(lf, [("start", "dictate"), ("cancel", "dictate")])
    assert not lf.sent and lf.jobs.empty()


def test_rephrase(lf, monkeypatch):
    from localflow import cleanup
    monkeypatch.setattr(delivery, "copy_selection", lambda: "hey guys release late")
    monkeypatch.setattr(cleanup, "rephrase", lambda sel, instr, get: f"REWRITTEN({sel}|{instr})")
    lf.stt = FakeSTT("make it formal")
    run(lf, [("start", "rephrase"), ("stop", "rephrase")])
    assert lf.sent == ["REWRITTEN(hey guys release late|make it formal)"]
    assert lf.history.search()[0]["kind"] == "rephrase"


def test_paste_last(lf):
    lf.stt = FakeSTT("First thing.")
    run(lf, [("start", "dictate"), ("stop", "dictate")])
    run(lf, [("fire", "paste_last")])
    assert lf.sent == ["First thing. ", "First thing. "]


def test_history_off(lf):
    lf.s.set("history.enabled", False)
    lf.stt = FakeSTT("Not stored.")
    run(lf, [("start", "dictate"), ("stop", "dictate")])
    assert lf.sent and lf.history.search() == []


def test_translate_flag_passed(lf):
    seen = {}

    class STT(FakeSTT):
        def transcribe(self, audio, **kw):
            seen.update(kw)
            return "Hello"
    lf.s.set("modes.translate", True)
    lf.stt = STT("")
    run(lf, [("start", "dictate"), ("stop", "dictate")])
    assert seen["translate"] is True
