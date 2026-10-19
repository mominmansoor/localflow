import json
import os
import time

from localflow.config import Settings
from localflow.secrets import protect, unprotect


def test_defaults_and_set(tmp_path):
    s = Settings(tmp_path / "c.json")
    assert s.get("hotkeys.dictate") == "ctrl+win"
    s.set("hotkeys.dictate", "ctrl+alt")
    assert json.loads((tmp_path / "c.json").read_text())["hotkeys"]["dictate"] == "ctrl+alt"


def test_hot_reload(tmp_path):
    p = tmp_path / "c.json"
    s = Settings(p)
    s.set("cleanup.level", "high")
    data = json.loads(p.read_text())
    data["cleanup"]["level"] = "light"
    time.sleep(0.02)
    p.write_text(json.dumps(data))
    os.utime(p, (time.time() + 1, time.time() + 1))
    assert s.reload_if_changed() and s.get("cleanup.level") == "light"


def test_migrate_v01(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"hotkey": "f13", "llm_enabled": False, "vocabulary": ["GEA"]}))
    s = Settings(p)
    assert s.get("hotkeys.dictate") == "f13" and s.get("cleanup.level") == "light"
    assert s.get("vocabulary.hotwords") == ["GEA"]


def test_deleted_default_stays_deleted(tmp_path):
    s = Settings(tmp_path / "c.json")
    reps = s.get("formatting.inline_replacements")
    del reps["period"]
    s.set("formatting.inline_replacements", reps)
    assert "period" not in Settings(tmp_path / "c.json").get("formatting.inline_replacements")


def test_export_import_strips_secrets(tmp_path):
    s = Settings(tmp_path / "c.json")
    s.set("snippets", [{"trigger": "pw", "text": protect("x"), "secret": True},
                       {"trigger": "sig", "text": "S", "secret": False}])
    s.export(tmp_path / "out.json")
    s2 = Settings(tmp_path / "c2.json")
    s2.import_(tmp_path / "out.json")
    assert [x["trigger"] for x in s2.get("snippets")] == ["sig"]


def test_secret_roundtrip():
    assert unprotect(protect("hunter2")) == "hunter2"
