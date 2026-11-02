"""Settings: nested JSON with defaults, profiles, per-app rules and hot-reload.

Everything the settings window edits lives in one file (config.json). Profiles and
app rules are stored as *overrides* keyed by dotted paths ("cleanup.style"), and
`Settings.effective(app)` layers them: defaults -> your settings -> active profile
-> matching app rule.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("LOCALFLOW_DATA", ROOT / "data"))

CLEANUP_LEVELS = ["none", "light", "medium", "high"]
STYLES = ["neutral", "formal", "casual", "very_casual", "verbatim"]
LOCK_MODES = ["double_tap", "single_tap", "off"]

DEFAULT_INLINE = {
    "comma": ",", "period": ".", "full stop": ".", "question mark": "?",
    "exclamation mark": "!", "exclamation point": "!", "colon": ":", "semicolon": ";",
    "new line": "\n", "new paragraph": "\n\n", "open quote": "\"", "close quote": "\"",
    "end quote": "\"", "open bracket": "(", "close bracket": ")", "open paren": "(",
    "close paren": ")", "ellipsis": "…", "hyphen": "-", "arrow": "→",
}

DEFAULT_PROFILES = {
    "Dictation": {},
    "Chat": {"cleanup.style": "casual", "formatting.strip_trailing_period": True},
    "Email": {"cleanup.style": "formal", "cleanup.level": "high"},
    "Code": {"cleanup.style": "verbatim", "cleanup.level": "light",
             "formatting.capitalize_first": False, "formatting.strip_trailing_period": True},
    "Notes": {"cleanup.level": "high", "formatting.spoken_lists": True, "modes.continuous": True},
}

DEFAULT_APP_RULES = [
    {"name": "Email", "enabled": True, "match": ["outlook.exe", "olk.exe", "thunderbird.exe"],
     "suppress": False, "overrides": {"cleanup.style": "formal"}},
    {"name": "Chat apps", "enabled": True,
     "match": ["slack.exe", "discord.exe", "whatsapp.exe", "telegram.exe", "ms-teams.exe"],
     "suppress": False, "overrides": {"cleanup.style": "casual"}},
    {"name": "Terminals (verbatim)", "enabled": False,
     "match": ["windowsterminal.exe", "wt.exe", "cmd.exe", "powershell.exe"],
     "suppress": False, "overrides": {"cleanup.style": "verbatim", "formatting.strip_trailing_period": True}},
    {"name": "Password managers", "enabled": True,
     "match": ["1password.exe", "bitwarden.exe", "keepassxc.exe"], "suppress": True, "overrides": {}},
]

DEFAULTS: dict = {
    "hotkeys": {
        "dictate": "ctrl+win",
        "lock_mode": "double_tap",       # double-tap = hands-free; tap again to stop
        "tap_ms": 300,                    # presses shorter than this count as taps
        "double_tap_ms": 400,             # max gap between the two taps
        "rephrase": "ctrl+shift+win",
        "paste_last": "alt+shift+z",
        "pause": "ctrl+alt+win",
        "cancel": "esc",
    },
    "audio": {
        "mic_device": None,
        "keep_mic_open": True,
        "preroll_ms": 500,
        "min_recording_ms": 250,
        "max_recording_seconds": 600,
        "whisper_mode": False,
        "noise_suppression": False,
    },
    "whisper": {
        "model": "large-v3-turbo",
        "device": "cuda",
        "compute_type": "float16",
        "beam_size": 5,
        "language": "en",
        "vad_filter": True,
        "cpu_fallback_model": "small",
    },
    "cleanup": {
        "level": "medium",               # none | light | medium | high
        "style": "neutral",              # neutral | formal | casual | very_casual | verbatim
        "ollama_url": "http://127.0.0.1:11434",
        "llm_model": "qwen3:4b-instruct",
        "llm_keep_alive": "2h",
        "llm_timeout_s": 6,
        "llm_min_words": 4,
    },
    "formatting": {
        "inline_commands": True,
        "inline_replacements": DEFAULT_INLINE,
        "spoken_lists": False,
        "smart_times": True,
        "smart_emails": True,
        "smart_urls": True,
        "smart_percent": True,
        "capitalize_first": True,
        "ensure_punctuation": False,
        "strip_trailing_period": False,
    },
    "vocabulary": {
        "hotwords": [],
        "corrections": {},               # {"CAPEX": ["cap x", "copics"]}
    },
    "snippets": [],                      # [{"trigger", "text", "secret"}]
    "commands": {"enabled": True, "items": [
        {"trigger": "press enter", "action": "hotkey", "value": "enter"},
        {"trigger": "select all", "action": "hotkey", "value": "ctrl+a"},
        {"trigger": "undo that", "action": "hotkey", "value": "ctrl+z"},
    ]},
    "delivery": {
        "method": "paste",               # paste | type | copy
        "restore_clipboard": True,
        "add_trailing_space": True,
        "auto_send": False,              # press Enter after delivering
        "fallback_window": True,
    },
    "modes": {
        "translate": False,
        "continuous": False,
        "continuous_silence_ms": 1200,
        "live_preview": False,
    },
    "profiles": {"active": "Dictation", "items": DEFAULT_PROFILES},
    "app_rules": DEFAULT_APP_RULES,
    "history": {"enabled": True, "retention_days": 30},
    "overlay": {"show_idle_pill": True, "margin_px": 16},
    "api_server": {"enabled": False, "port": 7777},
    "update_check": {"enabled": False, "repo": "mominmansoor/localflow"},
    "startup": {"launch_at_login": False},
}

# User-owned collections: if present in the user's file they replace the default
# wholesale (so deleting a default entry sticks).
REPLACE_KEYS = {"formatting.inline_replacements", "vocabulary.corrections", "profiles.items"}

# v0.1 flat keys -> new nested keys
_MIGRATE = {
    "hotkey": "hotkeys.dictate", "cancel_key": "hotkeys.cancel", "max_recording_seconds": "audio.max_recording_seconds",
    "language": "whisper.language", "whisper_model": "whisper.model", "whisper_device": "whisper.device",
    "whisper_compute_type": "whisper.compute_type", "beam_size": "whisper.beam_size", "vad_filter": "whisper.vad_filter",
    "cpu_fallback_model": "whisper.cpu_fallback_model", "mic_device": "audio.mic_device",
    "keep_mic_open": "audio.keep_mic_open", "preroll_ms": "audio.preroll_ms", "min_recording_ms": "audio.min_recording_ms",
    "ollama_url": "cleanup.ollama_url", "llm_model": "cleanup.llm_model", "llm_keep_alive": "cleanup.llm_keep_alive",
    "llm_timeout_s": "cleanup.llm_timeout_s", "llm_min_words": "cleanup.llm_min_words",
    "paste_method": "delivery.method", "restore_clipboard": "delivery.restore_clipboard",
    "add_trailing_space": "delivery.add_trailing_space", "show_idle_pill": "overlay.show_idle_pill",
    "overlay_margin_px": "overlay.margin_px", "vocabulary": "vocabulary.hotwords",
}


# ---------------------------------------------------------------------------
def get_path(d: dict, key: str, default=None):
    cur = d
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set_path(d: dict, key: str, value) -> None:
    parts = key.split(".")
    cur = d
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def _merge(base: dict, over: dict, prefix: str = "") -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        path = f"{prefix}{k}"
        if isinstance(v, dict) and isinstance(out.get(k), dict) and path not in REPLACE_KEYS:
            out[k] = _merge(out[k], v, path + ".")
        else:
            out[k] = copy.deepcopy(v)
    return out


def _is_v01(key: str, value) -> bool:
    """True if this top-level key is in the old flat v0.1 format.

    "vocabulary" exists in both formats: a list in v0.1, a dict now.
    """
    if key == "vocabulary":
        return isinstance(value, list)
    return key in _MIGRATE or key == "llm_enabled"


def _repair(user: dict) -> bool:
    """Undo damage from an old migration bug that nested vocabulary inside itself
    on every launch. Returns True if anything changed."""
    vocab = user.get("vocabulary")
    if not isinstance(vocab, dict):
        return False
    hot, changed = vocab.get("hotwords"), False
    while isinstance(hot, dict):          # {"hotwords": {"hotwords": [...]}} -> [...]
        hot = hot.get("hotwords", [])
        changed = True
    if changed:
        vocab["hotwords"] = hot if isinstance(hot, list) else []
        log.info("Repaired nested vocabulary in config.json")
    return changed


def migrate(user: dict) -> dict:
    old_keys = [k for k, v in user.items() if _is_v01(k, v)]
    if not old_keys:
        return user
    new: dict = {k: v for k, v in user.items() if k not in old_keys and not k.startswith("_")}
    for old, path in _MIGRATE.items():
        if old in old_keys:
            set_path(new, path, user[old])
    if user.get("llm_enabled") is False:
        set_path(new, "cleanup.level", "light")
    log.info("Migrated v0.1 config to the new format")
    return new


class Effective:
    """Read-only resolved settings for one dictation."""

    def __init__(self, data: dict, profile: str, rule: dict | None):
        self.data = data
        self.profile = profile
        self.rule_name = rule.get("name") if rule else None
        self.suppressed = bool(rule and rule.get("suppress"))

    def get(self, key, default=None):
        return get_path(self.data, key, default)


class Settings:
    def __init__(self, path: Path | None = None):
        env = os.environ.get("LOCALFLOW_CONFIG")
        self.path = Path(path or env or ROOT / "config.json")
        self._lock = threading.RLock()
        self._mtime = 0.0
        self.user: dict = {}
        self.data: dict = copy.deepcopy(DEFAULTS)
        self.load()

    # -- file I/O --------------------------------------------------------------
    def load(self) -> None:
        with self._lock:
            user = {}
            if self.path.exists():
                try:
                    user = json.loads(self.path.read_text(encoding="utf-8"))
                    self._mtime = self.path.stat().st_mtime
                except (json.JSONDecodeError, OSError) as e:
                    log.error("config.json unreadable (%s) - keeping previous settings", e)
                    return
            migrated = migrate(user)
            repaired = _repair(migrated)
            self.user = migrated
            self.data = _merge(DEFAULTS, migrated)
            if migrated is not user or repaired:
                self.save()

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
            self._mtime = self.path.stat().st_mtime

    def reload_if_changed(self) -> bool:
        try:
            m = self.path.stat().st_mtime
        except OSError:
            return False
        if m != self._mtime:
            self.load()
            return True
        return False

    # -- access ------------------------------------------------------------------
    def get(self, key, default=None):
        with self._lock:
            return copy.deepcopy(get_path(self.data, key, default))

    def set(self, key, value, save: bool = True) -> None:
        with self._lock:
            set_path(self.data, key, copy.deepcopy(value))
            if save:
                self.save()

    def effective(self, app_exe: str | None = None) -> Effective:
        with self._lock:
            data = copy.deepcopy(self.data)
        profile = data["profiles"].get("active", "Dictation")
        for k, v in (data["profiles"]["items"].get(profile) or {}).items():
            set_path(data, k, v)
        rule = match_rule(data.get("app_rules", []), app_exe)
        if rule:
            for k, v in (rule.get("overrides") or {}).items():
                set_path(data, k, v)
        return Effective(data, profile, rule)

    # -- portability -------------------------------------------------------------
    def export(self, dest: Path, include_secrets: bool = False) -> None:
        data = copy.deepcopy(self.data)
        if not include_secrets:
            data["snippets"] = [s for s in data.get("snippets", []) if not s.get("secret")]
        Path(dest).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def import_(self, src: Path) -> None:
        user = json.loads(Path(src).read_text(encoding="utf-8"))
        if not isinstance(user, dict):
            raise ValueError("Not a LocalFlow settings file")
        with self._lock:
            self.data = _merge(DEFAULTS, migrate(user))
            self.save()


def match_rule(rules: list, app_exe: str | None) -> dict | None:
    if not app_exe:
        return None
    exe = app_exe.lower()
    for r in rules:
        if r.get("enabled", True) and exe in [m.lower() for m in r.get("match", [])]:
            return r
    return None
