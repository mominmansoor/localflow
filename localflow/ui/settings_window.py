"""LocalFlow settings app. Every change saves immediately; the running app hot-reloads it."""
from __future__ import annotations

import copy
import os
import sys
import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget, QInputDialog, QCheckBox, QSizePolicy)

from .. import __version__, cleanup, config, diagnostics, winapi
from ..config import CLEANUP_LEVELS, DEFAULT_INLINE, STYLES
from ..vault import protect, snippet_text
from . import icons
from . import theme as T
from .pill_preview import PillPreview
from .theme import QSS
from .widgets import (Button, ChoiceGrid, HotkeyEdit, OverrideEditor, SavedBadge, Select, Sidebar, Stepper, Toggle,
                      card, divider, fade_in, page_header, row, run_bg, summarize_overrides)

LEVEL_INFO = {
    "none": ("None", "Exactly what Whisper heard."),
    "light": ("Light", "Removes um, uh and stutters. Instant, no AI."),
    "medium": ("Medium", "Also fixes self-corrections (\"5, no wait, 6\") and punctuation with your local model."),
    "high": ("High", "Also turns spoken lists into lists and smooths phrasing into clean writing."),
}
STYLE_INFO = {
    "neutral": ("Neutral", "Keeps your tone."),
    "formal": ("Formal", "Professional, complete sentences."),
    "casual": ("Casual", "Conversational, contractions are fine."),
    "very_casual": ("Very casual", "Chat style, lowercase, no final period."),
    "verbatim": ("Verbatim", "No AI rewriting at all."),
}
LANGS = [("auto", "Auto-detect"), ("en", "English"), ("ar", "Arabic"), ("ur", "Urdu"), ("hi", "Hindi"),
         ("fr", "French"), ("de", "German"), ("es", "Spanish"), ("zh", "Chinese"), ("ja", "Japanese")]

NAV = [
    ("Dictation", [("General", "general"), ("Speech & mic", "mic"), ("Cleanup & style", "cleanup")]),
    ("Your words", [("Snippets", "snippets"), ("Voice commands", "commands"), ("Vocabulary", "vocabulary"),
                    ("Spoken punctuation", "punctuation")]),
    ("Context", [("App rules", "apps"), ("Profiles", "profiles")]),
    (None, [("History", "history"), ("Advanced", "advanced")]),
]


def _table(headers: list[str], stretch_col: int | tuple = 1) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setShowGrid(False)
    t.setWordWrap(False)
    t.setFocusPolicy(Qt.NoFocus)
    h = t.horizontalHeader()
    stretch = stretch_col if isinstance(stretch_col, tuple) else (stretch_col,)
    for i in range(len(headers)):
        h.setSectionResizeMode(i, QHeaderView.Stretch if i in stretch else QHeaderView.ResizeToContents)
    h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    h.setHighlightSections(False)
    t.setTextElideMode(Qt.ElideRight)
    t.verticalHeader().setDefaultSectionSize(38)
    return t


_KINDS = {"Primary": "primary", "Danger": "danger", "Ghost": "ghost", "Chip": "ghost"}


def _btn(text, slot=None, kind=None, icon_name=None) -> Button:
    b = Button(text, _KINDS.get(kind, "secondary"), icon_name)
    if kind == "Chip":
        b.setMinimumHeight(26)
    if slot:
        b.clicked.connect(slot)
    return b


def _hbox(*widgets, stretch_first=False) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setSpacing(8)
    if stretch_first:
        h.addStretch(1)
    for w in widgets:
        h.addWidget(w)
    if not stretch_first:
        h.addStretch(1)
    return h


def _mono(widget) -> None:
    f = QFont("Cascadia Mono")
    f.setStyleHint(QFont.Monospace)
    f.setPointSize(9)
    widget.setFont(f)


class StatusDot(QWidget):
    """Small coloured dot + text: Ready / Loading / Offline."""

    def __init__(self):
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(7)
        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        self.text = QLabel("")
        self.text.setObjectName("Muted")
        h.addWidget(self.dot)
        h.addWidget(self.text)
        h.addStretch(1)
        self.set("", T.TEXT_3)

    def set(self, text: str, color: str):
        self.text.setText(text)
        self.dot.setStyleSheet(f"background: {color}; border-radius: 4px;")


# ===========================================================================
# Dialogs
# ===========================================================================

class SnippetDialog(QDialog):
    def __init__(self, parent, snip: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Snippet")
        self.setMinimumWidth(520)
        snip = snip or {"trigger": "", "text": "", "secret": False}
        v = QVBoxLayout(self)
        v.setSpacing(10)
        form = QFormLayout()
        self.trigger = QLineEdit(snip["trigger"])
        self.trigger.setPlaceholderText("e.g. my signature")
        form.addRow("When I say", self.trigger)
        v.addLayout(form)

        self.secret = Toggle(bool(snip.get("secret")))
        v.addWidget(row("Secret", "Encrypted with your Windows account, masked in history, never sent to the AI.",
                        self.secret))

        self.stack = QStackedWidget()
        self.text = QPlainTextEdit(snippet_text(snip) if not snip.get("secret") else "")
        self.text.setPlaceholderText("Best regards,\nMomin")
        self.text.setMinimumHeight(140)
        self.pw = QLineEdit(snippet_text(snip) if snip.get("secret") else "")
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setPlaceholderText("Secret text")
        pw_wrap = QWidget()
        pv = QVBoxLayout(pw_wrap)
        pv.setContentsMargins(0, 0, 0, 0)
        show = QCheckBox("Show")
        show.toggled.connect(lambda on: self.pw.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        pv.addWidget(self.pw)
        pv.addWidget(show)
        pv.addStretch(1)
        self.stack.addWidget(self.text)
        self.stack.addWidget(pw_wrap)
        self.stack.setCurrentIndex(1 if snip.get("secret") else 0)
        self.secret.toggled.connect(self._swap)
        v.addWidget(QLabel("Types out"))
        v.addWidget(self.stack)

        self.vars = QWidget()
        hv = QHBoxLayout(self.vars)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.addWidget(QLabel("Insert:"))
        for var in ("${date}", "${time}", "${day}", "${clipboard}"):
            b = _btn(var, lambda _=False, x=var: self.text.insertPlainText(x), "Chip")
            hv.addWidget(b)
        hv.addStretch(1)
        self.vars.setVisible(not snip.get("secret"))
        v.addWidget(self.vars)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setObjectName("Primary")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _swap(self, secret):
        if secret and self.text.toPlainText() and not self.pw.text():
            self.pw.setText(self.text.toPlainText())
        if not secret and self.pw.text() and not self.text.toPlainText():
            self.text.setPlainText(self.pw.text())
        self.stack.setCurrentIndex(1 if secret else 0)
        self.vars.setVisible(not secret)

    def _accept(self):
        if not self.trigger.text().strip():
            QMessageBox.warning(self, "Snippet", "Give it a trigger phrase.")
            return
        self.accept()

    def value(self) -> dict:
        secret = self.secret.isChecked()
        text = self.pw.text() if secret else self.text.toPlainText()
        return {"trigger": self.trigger.text().strip(), "text": protect(text) if secret else text, "secret": secret}


class CommandDialog(QDialog):
    ACTIONS = [("hotkey", "Press keys"), ("type", "Type text"), ("run", "Run a program / shell command")]

    def __init__(self, parent, cmd: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Voice command")
        self.setMinimumWidth(520)
        cmd = cmd or {"trigger": "", "action": "hotkey", "value": ""}
        v = QVBoxLayout(self)
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.trigger = QLineEdit(cmd["trigger"])
        self.trigger.setPlaceholderText("e.g. new tab")
        form.addRow("When I say only", self.trigger)
        self.action = Select()
        for k, label in self.ACTIONS:
            self.action.addItem(label, k)
        self.action.setCurrentIndex([a for a, _ in self.ACTIONS].index(cmd.get("action", "hotkey")))
        form.addRow("Do", self.action)
        v.addLayout(form)

        self.stack = QStackedWidget()
        self.hk = HotkeyEdit(cmd["value"] if cmd.get("action") == "hotkey" else "", allow_empty=False)
        self.hk.setFixedWidth(260)
        self.hk.recording.connect(lambda on: getattr(parent, "suspend_hotkeys", lambda _x: None)(on))
        w0 = QWidget()
        l0 = QVBoxLayout(w0)
        l0.setContentsMargins(0, 0, 0, 0)
        l0.addWidget(self.hk)
        l0.addStretch(1)
        self.typed = QPlainTextEdit(cmd["value"] if cmd.get("action") == "type" else "")
        self.typed.setPlaceholderText("Text to type. ${date} ${time} ${clipboard} work here too.")
        self.run = QLineEdit(cmd["value"] if cmd.get("action") == "run" else "")
        self.run.setPlaceholderText('e.g. start "" "C:\\Program Files\\Obsidian\\Obsidian.exe"')
        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        l2.setContentsMargins(0, 0, 0, 0)
        l2.addWidget(self.run)
        warn = QLabel("Runs through the Windows shell with your permissions. Only add commands you trust.")
        warn.setObjectName("Warn")
        warn.setWordWrap(True)
        l2.addWidget(warn)
        l2.addStretch(1)
        for w in (w0, self.typed, w2):
            self.stack.addWidget(w)
        self.stack.setCurrentIndex(self.action.currentIndex())
        self.action.currentIndexChanged.connect(self.stack.setCurrentIndex)
        self.stack.setMinimumHeight(110)
        v.addWidget(self.stack)
        tip = QLabel("Commands fire only when the whole dictation is the trigger phrase, so normal sentences "
                     "containing those words are typed as usual.")
        tip.setObjectName("Muted")
        tip.setWordWrap(True)
        v.addWidget(tip)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setObjectName("Primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def value(self) -> dict:
        a = self.action.currentData()
        val = {"hotkey": self.hk.value(), "type": self.typed.toPlainText(), "run": self.run.text()}[a]
        return {"trigger": self.trigger.text().strip(), "action": a, "value": val}


class RuleDialog(QDialog):
    def __init__(self, parent, rule: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("App rule")
        self.setMinimumWidth(560)
        rule = rule or {"name": "", "enabled": True, "match": [], "suppress": False, "overrides": {}}
        v = QVBoxLayout(self)
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.name = QLineEdit(rule["name"])
        self.name.setPlaceholderText("e.g. Slack")
        form.addRow("Name", self.name)
        self.apps = QLineEdit(", ".join(rule.get("match", [])))
        self.apps.setPlaceholderText("slack.exe, discord.exe")
        self.pick = Select()
        self.pick.setEditable(True)
        self.pick.lineEdit().setPlaceholderText("Pick a running app…")
        self.pick.addItems(winapi.running_exes())
        self.pick.setCurrentIndex(-1)
        add = _btn("Add", self._add_app)
        apps_row = QHBoxLayout()
        apps_row.addWidget(self.apps, 2)
        apps_row.addWidget(self.pick, 1)
        apps_row.addWidget(add)
        form.addRow("Apps (.exe)", apps_row)
        v.addLayout(form)
        self.suppress = Toggle(bool(rule.get("suppress")))
        v.addWidget(row("Turn dictation off in these apps", "e.g. password managers", self.suppress))
        v.addWidget(divider())
        lab = QLabel("Overrides in these apps")
        lab.setObjectName("CardTitle")
        v.addWidget(lab)
        self.ov = OverrideEditor()
        self.ov.set_value(rule.get("overrides", {}))
        v.addWidget(self.ov)
        self._enabled = rule.get("enabled", True)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setObjectName("Primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _add_app(self):
        exe = self.pick.currentText().strip().lower()
        if not exe:
            return
        cur = [a.strip() for a in self.apps.text().split(",") if a.strip()]
        if exe not in cur:
            cur.append(exe)
        self.apps.setText(", ".join(cur))
        self.pick.setCurrentIndex(-1)
        self.pick.clearEditText()

    def value(self) -> dict:
        return {"name": self.name.text().strip() or "Rule", "enabled": self._enabled,
                "match": [a.strip().lower() for a in self.apps.text().split(",") if a.strip()],
                "suppress": self.suppress.isChecked(), "overrides": self.ov.value()}


class FixDialog(QDialog):
    def __init__(self, parent, heard: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Fix this everywhere")
        self.setMinimumWidth(440)
        v = QVBoxLayout(self)
        info = QLabel("Adds a vocabulary correction. It applies to every future dictation, no restart needed.")
        info.setObjectName("Muted")
        info.setWordWrap(True)
        v.addWidget(info)
        form = QFormLayout()
        self.heard = QLineEdit(heard)
        self.heard.setPlaceholderText("what Whisper wrote, e.g. cloud code")
        self.right = QLineEdit()
        self.right.setPlaceholderText("what you meant, e.g. Claude Code")
        form.addRow("Whisper heard", self.heard)
        form.addRow("Should be", self.right)
        v.addLayout(form)
        self.hot = QCheckBox("Also add it to hotwords (helps Whisper hear it right in the first place)")
        self.hot.setChecked(True)
        v.addWidget(self.hot)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setObjectName("Primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)


class TextDialog(QDialog):
    def __init__(self, parent, title: str, text: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 460)
        v = QVBoxLayout(self)
        box = QPlainTextEdit(text)
        box.setReadOnly(True)
        _mono(box)
        v.addWidget(box)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        copy_b = bb.addButton("Copy", QDialogButtonBox.ActionRole)
        copy_b.clicked.connect(lambda: QGuiApplication.clipboard().setText(text))
        bb.rejected.connect(self.reject)
        v.addWidget(bb)


# ===========================================================================
# Window
# ===========================================================================

class SettingsWindow(QWidget):
    PAGES = [name for _, items in NAV for name, _icon in items]

    def __init__(self, settings, app=None):
        super().__init__()
        self.s = settings
        self.app = app
        if app is not None:
            self.history = app.history
        else:
            from ..history import History
            self.history = History(config.DATA_DIR / "history.db")
        self.setWindowTitle("LocalFlow")
        from ..app import _tray_icon
        self.setWindowIcon(_tray_icon())
        self.setStyleSheet(QSS)
        self.resize(1060, 740)
        self.setMinimumSize(900, 600)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        side = QWidget()
        side.setObjectName("SideWrap")
        side.setAttribute(Qt.WA_StyledBackground)
        side.setFixedWidth(232)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 20, 0, 16)
        sv.setSpacing(0)
        brand = QLabel("LocalFlow")
        brand.setObjectName("Brand")
        brand.setContentsMargins(22, 0, 0, 0)
        sub = QLabel("Local dictation, unlimited")
        sub.setObjectName("BrandSub")
        sub.setContentsMargins(22, 2, 0, 6)
        sv.addWidget(brand)
        sv.addWidget(sub)
        self.nav = Sidebar(NAV)
        sv.addWidget(self.nav, 1)
        foot = QWidget()
        fv = QVBoxLayout(foot)
        fv.setContentsMargins(22, 0, 22, 0)
        fv.setSpacing(4)
        self.saved = SavedBadge()
        fv.addWidget(self.saved)
        self.status = QLabel(f"Version {__version__} · changes save automatically")
        self.status.setObjectName("SideFooter")
        self.status.setWordWrap(True)
        fv.addWidget(self.status)
        sv.addWidget(foot)
        root.addWidget(side)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        builders = [self._p_general, self._p_speech, self._p_cleanup, self._p_snippets, self._p_commands,
                    self._p_vocab, self._p_inline, self._p_rules, self._p_profiles, self._p_history, self._p_advanced]
        for b in builders:
            self.stack.addWidget(self._scroll(b()))
        self.nav.currentChanged.connect(self._on_page)
        self.nav.setCurrent(0, animated=False)

    # -- plumbing ---------------------------------------------------------------
    def _scroll(self, inner: QWidget) -> QScrollArea:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QScrollArea.NoFrame)
        # cap the reading width so rows don't stretch across wide windows
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(36, 30, 36, 36)
        h.addStretch(1)
        inner.setMaximumWidth(760)
        h.addWidget(inner, 100)
        h.addStretch(1)
        sa.setWidget(wrap)
        return sa

    def _page(self, title, subtitle) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(18)
        v.addWidget(page_header(title, subtitle))
        return w, v

    def _on_page(self, i):
        self.stack.setCurrentIndex(i)
        fade_in(self.stack.currentWidget())
        name = self.PAGES[i]
        if name == "History":
            self._history_refresh()
        elif name == "Cleanup & style":
            self._warm_for_try()

    def open_page(self, name: str) -> None:
        if name in self.PAGES:
            self.nav.setCurrent(self.PAGES.index(name), animated=self.isVisible())

    def _set(self, key, value):
        self.s.set(key, value)
        self.saved.flash()
        if self.app is not None:
            self.app.on_settings_changed()

    def suspend_hotkeys(self, on: bool):
        if self.app is not None:
            self.app.machine_suspend(on)

    # binders
    def _toggle(self, key, also=None) -> Toggle:
        t = Toggle(bool(self.s.get(key)))

        def changed(v):
            self._set(key, bool(v))
            if also:
                also(bool(v))
        t.toggled.connect(changed)
        return t

    def _combo(self, key, options: list[tuple], editable=False, width=220) -> QComboBox:
        c = Select()
        c.setFixedWidth(width)
        c.setEditable(editable)
        cur = self.s.get(key)
        for val, label in options:
            c.addItem(label, val)
        idx = next((i for i, (val, _) in enumerate(options) if val == cur), -1)
        if idx >= 0:
            c.setCurrentIndex(idx)
        elif editable and cur is not None:
            c.setEditText(str(cur))
        elif cur is not None:          # a custom value from config.json: keep it selectable
            c.addItem(str(cur), cur)
            c.setCurrentIndex(c.count() - 1)

        def commit(*_):
            i = c.currentIndex()
            if editable and (i < 0 or c.currentText() != c.itemText(i)):
                v = c.currentText().strip()
            else:
                v = c.itemData(i)
            if v != self.s.get(key):
                self._set(key, v)

        if editable:
            c.lineEdit().editingFinished.connect(commit)
            c.activated.connect(commit)
        else:
            c.currentIndexChanged.connect(commit)
        return c

    def _spin(self, key, lo, hi, step=1, suffix="", also=None) -> Stepper:
        s = Stepper(lo, hi, step, suffix)
        s.setValue(int(self.s.get(key) or 0))

        def commit(v):
            if v != self.s.get(key):
                self._set(key, v)
                if also:
                    also(v)
        s.committed.connect(commit)
        return s

    def _line(self, key, width=260, placeholder="") -> QLineEdit:
        e = QLineEdit(str(self.s.get(key) or ""))
        e.setFixedWidth(width)
        e.setPlaceholderText(placeholder)
        e.editingFinished.connect(lambda: self._set(key, e.text().strip()) if e.text().strip() != self.s.get(key) else None)
        return e

    def _hotkey(self, key, allow_empty=True) -> HotkeyEdit:
        h = HotkeyEdit(self.s.get(key) or "", allow_empty)
        h.changed.connect(lambda v: self._set(key, v))
        h.recording.connect(self.suspend_hotkeys)
        return h

    # =======================================================================
    # Pages
    # =======================================================================

    def _p_general(self):
        w, v = self._page("General", "Hotkeys, the pill, and where your text goes.")

        c, l = card("Pill", "The small indicator at the bottom of your screen.")
        self.pill_preview = PillPreview(bool(self.s.get("overlay.show_idle_pill")), bool(self.s.get("modes.live_preview")))
        l.addWidget(self.pill_preview)
        l.addWidget(row("Live preview", "Shows your words in the pill as you speak. Uses the GPU a little while you talk.",
                        self._toggle("modes.live_preview", also=self.pill_preview.set_live_preview)))
        l.addWidget(row("Show when idle", "Keep a small pill on screen while you're not dictating, so you know LocalFlow is on.",
                        self._toggle("overlay.show_idle_pill", also=self.pill_preview.set_show_idle)))
        l.addWidget(row("Distance from bottom", "Raise it if the pill covers your taskbar or a chat box.",
                        self._spin("overlay.margin_px", 0, 400, 4, " px")))
        v.addWidget(c)

        c, l = card("Hotkeys", "Click a field, then press the keys. Backspace clears an optional one.")
        l.addWidget(row("Dictate", "Hold to talk, release to insert.", self._hotkey("hotkeys.dictate", False)))
        l.addWidget(row("Hands-free lock", "Lock recording on so you can talk without holding the keys.",
                        self._combo("hotkeys.lock_mode", [("double_tap", "Double-tap the hotkey"),
                                                          ("single_tap", "Single quick tap"), ("off", "Off")])))
        l.addWidget(row("Double-tap speed", "Longest gap allowed between the two taps.",
                        self._spin("hotkeys.double_tap_ms", 200, 900, 25, " ms")))
        l.addWidget(row("AI rephrase", "Select text, hold, say how to rewrite it (\"make it formal\"), release.",
                        self._hotkey("hotkeys.rephrase")))
        l.addWidget(row("Paste last dictation", "If it landed in the wrong window.", self._hotkey("hotkeys.paste_last")))
        l.addWidget(row("Pause all hotkeys", "Press again to resume.", self._hotkey("hotkeys.pause")))
        l.addWidget(row("Cancel recording", None, self._hotkey("hotkeys.cancel", False)))
        v.addWidget(c)

        c, l = card("Output")
        l.addWidget(row("Delivery", "Paste is fastest. Type works in apps that block paste. Copy only puts it on the clipboard.",
                        self._combo("delivery.method", [("paste", "Paste at cursor"), ("type", "Type it out"),
                                                        ("copy", "Copy only")])))
        l.addWidget(row("Press Enter after inserting", "Auto-send. Usually better set per app in App rules.",
                        self._toggle("delivery.auto_send")))
        l.addWidget(row("Restore my clipboard", "Puts back what you had copied after pasting.",
                        self._toggle("delivery.restore_clipboard")))
        l.addWidget(row("Add a space after sentences", "So back-to-back dictations flow.",
                        self._toggle("delivery.add_trailing_space")))
        l.addWidget(row("Fallback window", "If no text field has focus, show the text in a small window instead.",
                        self._toggle("delivery.fallback_window")))
        v.addWidget(c)

        c, l = card("App")
        profiles = list((self.s.get("profiles.items") or {}).keys())
        self.general_profile = self._combo("profiles.active", [(p, p) for p in profiles])
        l.addWidget(row("Active profile", "Presets for different kinds of writing. Edit them under Profiles.",
                        self.general_profile))
        login = Toggle(bool(self.s.get("startup.launch_at_login")))

        def set_login(on):
            try:
                winapi.set_launch_at_login(on, str(config.ROOT))
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "Launch at login", str(e))
            self._set("startup.launch_at_login", bool(on))
        login.toggled.connect(set_login)
        l.addWidget(row("Launch at login", None, login))
        v.addWidget(c)
        v.addStretch(1)
        return w

    def _p_speech(self):
        from ..stt import MODELS
        w, v = self._page("Speech & mic", "Whisper runs on your GPU. Model changes reload in the background.")
        c, l = card("Speech recognition")
        l.addWidget(row("Model", "large-v3-turbo is the best balance of speed and accuracy on a modern GPU.",
                        self._combo("whisper.model", [(m, m) for m in MODELS])))
        l.addWidget(row("Language", "Pinning a language is faster and more reliable than auto-detect.",
                        self._combo("whisper.language", [(None if k == "auto" else k, n) for k, n in LANGS])))
        l.addWidget(row("Translate to English", "Speak any language, get English text.", self._toggle("modes.translate")))
        l.addWidget(row("Device", None, self._combo("whisper.device", [("cuda", "GPU (CUDA)"), ("cpu", "CPU")])))
        l.addWidget(row("Precision", "float16 is the safe choice on RTX 50-series.",
                        self._combo("whisper.compute_type", [("float16", "float16"), ("int8_float16", "int8_float16"),
                                                             ("int8", "int8"), ("float32", "float32")])))
        l.addWidget(row("Beam size", "1 is fastest, 5 is more accurate.", self._spin("whisper.beam_size", 1, 10)))
        l.addWidget(row("Skip silence", "Prevents made-up words when nothing was said.", self._toggle("whisper.vad_filter")))
        v.addWidget(c)

        c, l = card("Microphone")
        mics = [(None, "System default")]
        try:
            from ..audio import list_input_devices
            mics += [(name, name) for _, name in list_input_devices()]
        except Exception:  # noqa: BLE001
            pass
        l.addWidget(row("Input device", None, self._combo("audio.mic_device", mics, width=300)))
        l.addWidget(row("Keep mic open", "Instant start, and catches the moment before you press. Windows shows the mic icon while LocalFlow runs.",
                        self._toggle("audio.keep_mic_open")))
        l.addWidget(row("Pre-roll", "Audio kept from just before the hotkey.", self._spin("audio.preroll_ms", 0, 1500, 50, " ms")))
        l.addWidget(row("Whisper mode", "Boosts quiet speech so you can dictate softly in a shared room.",
                        self._toggle("audio.whisper_mode")))
        try:
            import noisereduce  # noqa: F401
            nr_desc = "Filters steady background noise. Adds a little latency."
        except ImportError:
            nr_desc = "Needs one extra package: pip install noisereduce"
        l.addWidget(row("Noise suppression", nr_desc, self._toggle("audio.noise_suppression")))
        v.addWidget(c)

        c, l = card("Long dictation")
        l.addWidget(row("Continuous dictation", "While hands-free, text is inserted at every pause and recording keeps going.",
                        self._toggle("modes.continuous")))
        l.addWidget(row("Pause length", "Silence that ends a chunk in continuous mode.",
                        self._spin("modes.continuous_silence_ms", 500, 4000, 100, " ms")))
        l.addWidget(row("Max recording", None, self._spin("audio.max_recording_seconds", 30, 3600, 30, " s")))
        v.addWidget(c)
        v.addStretch(1)
        return w

    def _choice_cards(self, key, info: dict, cols: int) -> QWidget:
        grid = ChoiceGrid(info, self.s.get(key), cols)
        grid.picked.connect(lambda val: self._set(key, val))
        return grid

    def _p_cleanup(self):
        w, v = self._page("Cleanup & style", "How much LocalFlow tidies what you say. The AI runs on your own GPU through Ollama.")
        c, l = card("Cleanup level")
        l.addWidget(self._choice_cards("cleanup.level", {k: LEVEL_INFO[k] for k in CLEANUP_LEVELS}, 2))
        v.addWidget(c)
        c, l = card("Writing style", "Applies everywhere. Profiles and App rules can override it, like formal in Outlook.")
        l.addWidget(self._choice_cards("cleanup.style", {k: STYLE_INFO[k] for k in STYLES}, 3))
        v.addWidget(c)

        c, l = card("Try it", "Type something you might say and run it through your current settings.")
        self.try_in = QPlainTextEdit("um so I I think we should uh ship it on friday no wait actually monday. "
                                     "email me at momin at example dot com")
        self.try_in.setFixedHeight(76)
        self.try_out = QPlainTextEdit()
        self.try_out.setReadOnly(True)
        self.try_out.setFixedHeight(84)
        self.try_out.setPlaceholderText("The cleaned text shows up here.")
        self.try_info = QLabel("")
        self.try_info.setObjectName("Muted")
        self.try_btn = _btn("Run cleanup", self._try_run, "Primary")
        self.model_state = StatusDot()
        l.addWidget(self.try_in)
        bar = QHBoxLayout()
        bar.setSpacing(12)
        bar.addWidget(self.try_btn)
        bar.addWidget(self.try_info)
        bar.addStretch(1)
        bar.addWidget(self.model_state)
        l.addLayout(bar)
        l.addWidget(self.try_out)
        v.addWidget(c)

        c, l = card("Local AI model")
        self.model_combo = Select()
        self.model_combo.setFixedWidth(300)
        self.model_combo.addItem("Looking for models…", None)
        self.model_combo.setEnabled(False)
        self.model_combo.currentIndexChanged.connect(self._model_chosen)
        refresh = _btn("Refresh", self._refresh_models, "Ghost", "refresh")
        refresh.setToolTip("Check Ollama again after installing a model")
        holder = QWidget()
        hh = QHBoxLayout(holder)
        hh.setContentsMargins(0, 0, 0, 0)
        hh.setSpacing(6)
        hh.addWidget(self.model_combo)
        hh.addWidget(refresh)
        l.addWidget(row("Model", "Models installed in Ollama on this PC. 3B to 8B is the sweet spot for speed. "
                                 "Add more with: ollama pull <name>", holder))
        self.model_status = QLabel("")
        self.model_status.setObjectName("Faint")
        self.model_status.setWordWrap(True)
        l.addWidget(self.model_status)
        l.addWidget(row("Ollama address", None, self._line("cleanup.ollama_url")))
        l.addWidget(row("Timeout", "Uses the rule-cleaned text if the model takes longer than this.",
                        self._spin("cleanup.llm_timeout_s", 2, 30, 1, " s")))
        l.addWidget(row("Skip AI below", "Short dictations only get the instant rule cleanup.",
                        self._spin("cleanup.llm_min_words", 1, 30, 1, " words")))
        v.addWidget(c)
        QTimer.singleShot(300, self._refresh_models)

        c, l = card("Formatting")
        l.addWidget(row("Spoken lists", "\"First milk, second eggs\" becomes a numbered list. High does this too.",
                        self._toggle("formatting.spoken_lists")))
        l.addWidget(row("Times", "\"3 p.m.\" becomes 3 PM", self._toggle("formatting.smart_times")))
        l.addWidget(row("Emails", "\"john at example dot com\" becomes john@example.com", self._toggle("formatting.smart_emails")))
        l.addWidget(row("Web addresses", "\"github dot com\" becomes github.com", self._toggle("formatting.smart_urls")))
        l.addWidget(row("Percent", "\"50 percent\" becomes 50%", self._toggle("formatting.smart_percent")))
        l.addWidget(row("Capitalize first letter", None, self._toggle("formatting.capitalize_first")))
        l.addWidget(row("Always end with punctuation", None, self._toggle("formatting.ensure_punctuation")))
        l.addWidget(row("Drop the final period", "Feels more natural in chat.", self._toggle("formatting.strip_trailing_period")))
        v.addWidget(c)
        v.addStretch(1)
        return w

    # -- model + Try it -----------------------------------------------------------
    def _refresh_models(self):
        self.model_status.setText("Checking Ollama…")
        run_bg(lambda: cleanup.installed_models(self.s.get), self._fill_models)

    def _fill_models(self, models, err):
        c = self.model_combo
        cur = self.s.get("cleanup.llm_model")
        c.blockSignals(True)
        c.clear()
        if not models:
            c.addItem(cur or "No model", None)
            c.setEnabled(False)
            self.model_status.setText(
                "Ollama isn't running. Start Ollama from the Start Menu, then click Refresh."
                if models is None else
                "No AI models installed yet. In a terminal run:  ollama pull qwen3:4b-instruct  then click Refresh.")
            self.model_state.set("Offline" if models is None else "No model", T.RED)
            c.blockSignals(False)
            return
        c.setEnabled(True)
        for m in models:
            extra = " · ".join(x for x in (m["params"], f"{m['size_gb']:g} GB" if m["size_gb"] else "") if x)
            c.addItem(f"{m['name']}    {extra}" if extra else m["name"], m["name"])
        model, note = cleanup.resolve_model(self.s.get, models)
        c.setCurrentIndex(max(0, c.findData(model)))
        c.blockSignals(False)
        n = len(models)
        found = f"{n} model{'s' if n != 1 else ''} found in Ollama."
        if note:  # configured model isn't installed: switch to one that is
            self._set("cleanup.llm_model", model)
            self.model_status.setText(f"{note}. {found}")
        else:
            self.model_status.setText(found)

    def _model_chosen(self, i):
        name = self.model_combo.itemData(i)
        if name and not cleanup.same_model(name, self.s.get("cleanup.llm_model")):
            self._set("cleanup.llm_model", name)
            self.model_status.setText(f"Switched to {name}.")
            self._warm_for_try(force=True)

    def _warm_for_try(self, force=False):
        """Load the model into the GPU ahead of time so Try it (and dictation) is instant."""
        if self.s.get("cleanup.level") not in ("medium", "high"):
            self.model_state.set("AI off at this level", T.TEXT_3)
            return
        if getattr(self, "_warming", False) and not force:
            return
        self._warming = True
        name = self.s.get("cleanup.llm_model")
        self.model_state.set(f"Loading {name} into GPU…", T.AMBER)
        t0 = time.perf_counter()

        def done(status, err):
            self._warming = False
            if err or status != "ok":
                self.model_state.set("Model unavailable", T.RED)
                if status:
                    self.model_status.setText(str(status))
                return
            ms = (time.perf_counter() - t0) * 1000
            self.model_state.set(f"{self.s.get('cleanup.llm_model')} ready" + (f" · loaded in {ms / 1000:.1f}s" if ms > 1500 else ""),
                                 T.GREEN)
        run_bg(lambda: cleanup.warm_llm(self.s.get), done)

    def _try_run(self):
        from .. import pipeline
        text = self.try_in.toPlainText()
        if not text.strip():
            return
        self.try_info.setText("Running…")
        self.try_btn.setEnabled(False)

        def work():
            t0 = time.perf_counter()
            r = pipeline.process(text, self.s.effective())
            return r, (time.perf_counter() - t0) * 1000

        def done(res, err):
            self.try_btn.setEnabled(True)
            if err:
                self.try_info.setText(f"Error: {err}")
                return
            r, ms = res
            out = r.display if not r.command else f"(would run voice command: {r.command['trigger']})"
            self.try_out.setPlainText(out)
            llm = str(r.info.get("llm", ""))
            if llm == "ok":
                self.try_info.setText(f"{ms:.0f} ms total · AI {r.info.get('llm_ms', 0)} ms")
                self.model_state.set(f"{self.s.get('cleanup.llm_model')} ready", T.GREEN)
            elif llm == "skipped":
                self.try_info.setText(f"{ms:.0f} ms · rules only (AI not used at this level or for short text)")
            elif llm == "rejected":
                self.try_info.setText(f"{ms:.0f} ms · AI answer rejected, used rule cleanup")
            else:
                self.try_info.setText(f"{ms:.0f} ms · AI unavailable, used rule cleanup")
                self.model_state.set("Model unavailable", T.RED)
        run_bg(work, done)

    # -- snippets ---------------------------------------------------------------
    def _p_snippets(self):
        w, v = self._page("Snippets", "Say a trigger phrase anywhere in a dictation and it's replaced with the full text. "
                                      "Secret snippets are encrypted and never shown in history or sent to the AI.")
        self.snip_table = _table(["Say", "Types out", ""], 1)
        self.snip_table.doubleClicked.connect(lambda *_: self._snip_edit())
        v.addLayout(_hbox(_btn("New snippet", self._snip_add, "Primary"), _btn("Edit", self._snip_edit),
                          _btn("Delete", self._snip_del, "Danger")))
        v.addWidget(self.snip_table, 1)
        tip = QLabel("Variables: ${date}  ${time}  ${day}  ${clipboard}")
        tip.setObjectName("Muted")
        v.addWidget(tip)
        self._snip_fill()
        return w

    def _snip_fill(self):
        snips = self.s.get("snippets") or []
        t = self.snip_table
        t.setRowCount(len(snips))
        for i, sn in enumerate(snips):
            t.setItem(i, 0, QTableWidgetItem(f"\u201c{sn['trigger']}\u201d"))
            preview = "••••••••" if sn.get("secret") else sn.get("text", "").replace("\n", " ⏎ ")
            t.setItem(i, 1, QTableWidgetItem(preview[:120]))
            t.setItem(i, 2, QTableWidgetItem("Secret" if sn.get("secret") else ""))

    def _snip_add(self):
        d = SnippetDialog(self)
        if d.exec():
            snips = self.s.get("snippets") or []
            snips.append(d.value())
            self._set("snippets", snips)
            self._snip_fill()

    def _snip_edit(self):
        i = self.snip_table.currentRow()
        snips = self.s.get("snippets") or []
        if not 0 <= i < len(snips):
            return
        d = SnippetDialog(self, snips[i])
        if d.exec():
            snips[i] = d.value()
            self._set("snippets", snips)
            self._snip_fill()

    def _snip_del(self):
        i = self.snip_table.currentRow()
        snips = self.s.get("snippets") or []
        if 0 <= i < len(snips):
            snips.pop(i)
            self._set("snippets", snips)
            self._snip_fill()

    # -- commands ---------------------------------------------------------------
    def _p_commands(self):
        w, v = self._page("Voice commands", "Say a phrase on its own to press keys, type text or launch something.")
        c, l = card()
        l.addWidget(row("Voice commands enabled", None, self._toggle("commands.enabled")))
        v.addWidget(c)
        self.cmd_table = _table(["Say", "Action", "Value"], 2)
        self.cmd_table.doubleClicked.connect(lambda *_: self._cmd_edit())
        v.addLayout(_hbox(_btn("New command", self._cmd_add, "Primary"), _btn("Edit", self._cmd_edit),
                          _btn("Delete", self._cmd_del, "Danger")))
        v.addWidget(self.cmd_table, 1)
        self._cmd_fill()
        return w

    def _cmd_fill(self):
        items = self.s.get("commands.items") or []
        labels = dict(CommandDialog.ACTIONS)
        t = self.cmd_table
        t.setRowCount(len(items))
        for i, c in enumerate(items):
            t.setItem(i, 0, QTableWidgetItem(f"\u201c{c['trigger']}\u201d"))
            t.setItem(i, 1, QTableWidgetItem(labels.get(c["action"], c["action"])))
            t.setItem(i, 2, QTableWidgetItem(str(c.get("value", "")).replace("\n", " ⏎ ")[:120]))

    def _cmd_add(self):
        d = CommandDialog(self)
        if d.exec() and d.value()["trigger"]:
            items = self.s.get("commands.items") or []
            items.append(d.value())
            self._set("commands.items", items)
            self._cmd_fill()

    def _cmd_edit(self):
        i = self.cmd_table.currentRow()
        items = self.s.get("commands.items") or []
        if not 0 <= i < len(items):
            return
        d = CommandDialog(self, items[i])
        if d.exec():
            items[i] = d.value()
            self._set("commands.items", items)
            self._cmd_fill()

    def _cmd_del(self):
        i = self.cmd_table.currentRow()
        items = self.s.get("commands.items") or []
        if 0 <= i < len(items):
            items.pop(i)
            self._set("commands.items", items)
            self._cmd_fill()

    # -- vocabulary ---------------------------------------------------------------
    def _p_vocab(self):
        w, v = self._page("Vocabulary", "Teach LocalFlow your names and jargon.")
        c, l = card("Hotwords", "Names and terms Whisper should expect. Also given to the AI so it spells them right.")
        self.hot_list = QListWidget()
        self.hot_list.setFixedHeight(150)
        self.hot_in = QLineEdit()
        self.hot_in.setPlaceholderText("Add a word or name, e.g. Supabase")
        self.hot_in.returnPressed.connect(self._hot_add)
        l.addLayout(_hbox(self.hot_in, _btn("Add", self._hot_add), _btn("Remove", self._hot_del, "Danger"),
                          _btn("Suggest from my history", self._hot_suggest)))
        l.addWidget(self.hot_list)
        v.addWidget(c)

        c, l = card("Corrections", "When Whisper mishears something the same way, map it to the right spelling. "
                                    "Longest match wins. Tip: use \"Fix this everywhere\" from History.")
        self.corr_table = _table(["Correct spelling", "Whisper hears it as (comma-separated)"], 1)
        self.corr_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.corr_table.itemChanged.connect(self._corr_save)
        self.corr_table.setMinimumHeight(200)
        l.addLayout(_hbox(_btn("Add", self._corr_add), _btn("Remove", self._corr_del, "Danger")))
        l.addWidget(self.corr_table)
        v.addWidget(c)
        v.addStretch(1)
        self._vocab_fill()
        return w

    def _vocab_fill(self):
        self.hot_list.clear()
        self.hot_list.addItems(self.s.get("vocabulary.hotwords") or [])
        corr = self.s.get("vocabulary.corrections") or {}
        self.corr_table.blockSignals(True)
        self.corr_table.setRowCount(len(corr))
        for i, (right, wrongs) in enumerate(corr.items()):
            self.corr_table.setItem(i, 0, QTableWidgetItem(right))
            self.corr_table.setItem(i, 1, QTableWidgetItem(", ".join(wrongs)))
        self.corr_table.blockSignals(False)

    def _hot_add(self):
        word = self.hot_in.text().strip()
        hw = self.s.get("vocabulary.hotwords") or []
        if word and word not in hw:
            hw.append(word)
            self._set("vocabulary.hotwords", hw)
        self.hot_in.clear()
        self._vocab_fill()

    def _hot_del(self):
        it = self.hot_list.currentItem()
        if it:
            hw = [h for h in self.s.get("vocabulary.hotwords") or [] if h != it.text()]
            self._set("vocabulary.hotwords", hw)
            self._vocab_fill()

    def _hot_suggest(self):
        sugg = self.history.suggest_hotwords(self.s.get("vocabulary.hotwords"))
        if not sugg:
            QMessageBox.information(self, "Suggestions", "Nothing yet. Dictate a bit more and try again.")
            return
        d = QDialog(self)
        d.setWindowTitle("Suggested hotwords")
        d.resize(380, 440)
        dv = QVBoxLayout(d)
        lab = QLabel("Words you say often that aren't common English. Tick the ones to add.")
        lab.setObjectName("Muted")
        lab.setWordWrap(True)
        dv.addWidget(lab)
        lw = QListWidget()
        for word, n in sugg:
            it = QListWidgetItem(f"{word}   ×{n}")
            it.setData(Qt.UserRole, word)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            lw.addItem(it)
        dv.addWidget(lw)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        dv.addWidget(bb)
        if d.exec():
            hw = self.s.get("vocabulary.hotwords") or []
            for i in range(lw.count()):
                if lw.item(i).checkState() == Qt.Checked:
                    word = lw.item(i).data(Qt.UserRole)
                    if word not in hw:
                        hw.append(word)
            self._set("vocabulary.hotwords", hw)
            self._vocab_fill()

    def _corr_save(self, *_):
        corr = {}
        for i in range(self.corr_table.rowCount()):
            r, w_ = self.corr_table.item(i, 0), self.corr_table.item(i, 1)
            right = (r.text() if r else "").strip()
            if right:
                corr[right] = [x.strip() for x in (w_.text() if w_ else "").split(",") if x.strip()]
        self._set("vocabulary.corrections", corr)

    def _corr_add(self):
        self.corr_table.blockSignals(True)
        n = self.corr_table.rowCount()
        self.corr_table.insertRow(n)
        self.corr_table.setItem(n, 0, QTableWidgetItem(""))
        self.corr_table.setItem(n, 1, QTableWidgetItem(""))
        self.corr_table.blockSignals(False)
        self.corr_table.setCurrentCell(n, 0)
        self.corr_table.editItem(self.corr_table.item(n, 0))

    def _corr_del(self):
        i = self.corr_table.currentRow()
        if i >= 0:
            self.corr_table.removeRow(i)
            self._corr_save()

    # -- inline formatting ------------------------------------------------------------
    def _p_inline(self):
        w, v = self._page("Spoken punctuation", "Say these mid-sentence: \"hello comma world period\". "
                                                "Add your own (any language) or map words to symbols.")
        c, l = card()
        l.addWidget(row("Spoken punctuation enabled", "\"period\" only counts at the end of a sentence, so \"trial period\" is safe.",
                        self._toggle("formatting.inline_commands")))
        v.addWidget(c)
        self.inl_table = _table(["When I say", "Insert"], 0)
        self.inl_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.inl_table.itemChanged.connect(self._inl_save)
        v.addLayout(_hbox(_btn("Add", self._inl_add), _btn("Remove", self._inl_del, "Danger"),
                          _btn("Reset to defaults", self._inl_reset, "Ghost")))
        v.addWidget(self.inl_table, 1)
        tip = QLabel("Use \\n for a line break.")
        tip.setObjectName("Muted")
        v.addWidget(tip)
        self._inl_fill()
        return w

    def _inl_fill(self):
        reps = self.s.get("formatting.inline_replacements") or {}
        self.inl_table.blockSignals(True)
        self.inl_table.setRowCount(len(reps))
        for i, (k, val) in enumerate(sorted(reps.items())):
            self.inl_table.setItem(i, 0, QTableWidgetItem(k))
            self.inl_table.setItem(i, 1, QTableWidgetItem(val.replace("\n", "\\n")))
        self.inl_table.blockSignals(False)

    def _inl_save(self, *_):
        reps = {}
        for i in range(self.inl_table.rowCount()):
            k, val = self.inl_table.item(i, 0), self.inl_table.item(i, 1)
            if k and k.text().strip():
                reps[k.text().strip().lower()] = (val.text() if val else "").replace("\\n", "\n")
        self._set("formatting.inline_replacements", reps)

    def _inl_add(self):
        self.inl_table.blockSignals(True)
        n = self.inl_table.rowCount()
        self.inl_table.insertRow(n)
        self.inl_table.setItem(n, 0, QTableWidgetItem(""))
        self.inl_table.setItem(n, 1, QTableWidgetItem(""))
        self.inl_table.blockSignals(False)
        self.inl_table.setCurrentCell(n, 0)
        self.inl_table.editItem(self.inl_table.item(n, 0))

    def _inl_del(self):
        i = self.inl_table.currentRow()
        if i >= 0:
            self.inl_table.removeRow(i)
            self._inl_save()

    def _inl_reset(self):
        self._set("formatting.inline_replacements", copy.deepcopy(DEFAULT_INLINE))
        self._inl_fill()

    # -- app rules --------------------------------------------------------------------
    def _p_rules(self):
        w, v = self._page("App rules", "Different behaviour per app: formal in email, casual in chat, off in password managers. "
                                       "Rules win over the active profile.")
        self.rule_table = _table(["On", "Rule", "Apps", "Behaviour"], (2, 3))
        self.rule_table.doubleClicked.connect(lambda *_: self._rule_edit())
        self.rule_table.cellClicked.connect(self._rule_toggle_click)
        v.addLayout(_hbox(_btn("New rule", self._rule_add, "Primary"), _btn("Edit", self._rule_edit),
                          _btn("Delete", self._rule_del, "Danger")))
        v.addWidget(self.rule_table, 1)
        tip = QLabel("Click the On column to enable or disable a rule.")
        tip.setObjectName("Muted")
        v.addWidget(tip)
        self._rule_fill()
        return w

    def _rule_fill(self):
        rules = self.s.get("app_rules") or []
        t = self.rule_table
        t.setRowCount(len(rules))
        for i, r in enumerate(rules):
            t.setItem(i, 0, QTableWidgetItem("●" if r.get("enabled", True) else "○"))
            t.setItem(i, 1, QTableWidgetItem(r.get("name", "")))
            t.setItem(i, 2, QTableWidgetItem(", ".join(r.get("match", []))))
            beh = "Dictation off" if r.get("suppress") else summarize_overrides(r.get("overrides", {}))
            t.setItem(i, 3, QTableWidgetItem(beh))

    def _rule_toggle_click(self, rown, col):
        if col != 0:
            return
        rules = self.s.get("app_rules") or []
        rules[rown]["enabled"] = not rules[rown].get("enabled", True)
        self._set("app_rules", rules)
        self._rule_fill()

    def _rule_add(self):
        d = RuleDialog(self)
        if d.exec():
            rules = self.s.get("app_rules") or []
            rules.insert(0, d.value())
            self._set("app_rules", rules)
            self._rule_fill()

    def _rule_edit(self):
        i = self.rule_table.currentRow()
        rules = self.s.get("app_rules") or []
        if not 0 <= i < len(rules):
            return
        d = RuleDialog(self, rules[i])
        if d.exec():
            rules[i] = d.value()
            self._set("app_rules", rules)
            self._rule_fill()

    def _rule_del(self):
        i = self.rule_table.currentRow()
        rules = self.s.get("app_rules") or []
        if 0 <= i < len(rules):
            rules.pop(i)
            self._set("app_rules", rules)
            self._rule_fill()

    # -- profiles ---------------------------------------------------------------------
    def _p_profiles(self):
        w, v = self._page("Profiles", "Presets you switch between from the tray. A profile only stores what it changes.")
        body = QHBoxLayout()
        body.setSpacing(14)
        left = QVBoxLayout()
        self.prof_list = QListWidget()
        self.prof_list.setFixedWidth(220)
        self.prof_list.currentRowChanged.connect(self._prof_select)
        left.addWidget(self.prof_list, 1)
        left.addLayout(_hbox(_btn("New", self._prof_new), _btn("Rename", self._prof_rename),
                             _btn("Delete", self._prof_del, "Danger")))
        body.addLayout(left)
        c, l = card()
        self.prof_title = QLabel("")
        self.prof_title.setObjectName("CardTitle")
        self.prof_active_btn = _btn("Make active", self._prof_activate, "Primary")
        l.addLayout(_hbox(self.prof_title, self.prof_active_btn))
        self.prof_ov = OverrideEditor()
        self.prof_ov.changed.connect(self._prof_save)
        l.addWidget(self.prof_ov)
        l.addStretch(1)
        body.addWidget(c, 1)
        v.addLayout(body, 1)
        self._prof_fill()
        return w

    def _prof_fill(self, select: str | None = None):
        items = self.s.get("profiles.items") or {}
        active = self.s.get("profiles.active")
        self.prof_list.blockSignals(True)
        self.prof_list.clear()
        for name in items:
            it = QListWidgetItem(("●  " if name == active else "    ") + name)
            it.setData(Qt.UserRole, name)
            self.prof_list.addItem(it)
        self.prof_list.blockSignals(False)
        names = list(items)
        target = select or (self._prof_current() if hasattr(self, "_prof_name") else active)
        self.prof_list.setCurrentRow(names.index(target) if target in names else 0)
        self._prof_select(self.prof_list.currentRow())
        if hasattr(self, "general_profile"):
            g = self.general_profile
            g.blockSignals(True)
            g.clear()
            for n in names:
                g.addItem(n, n)
            g.setCurrentIndex(names.index(active) if active in names else 0)
            g.blockSignals(False)

    def _prof_current(self) -> str | None:
        return getattr(self, "_prof_name", None)

    def _prof_select(self, i):
        it = self.prof_list.item(i)
        if not it:
            return
        name = it.data(Qt.UserRole)
        self._prof_name = name
        active = self.s.get("profiles.active")
        self.prof_title.setText(name + ("  (active)" if name == active else ""))
        self.prof_active_btn.setEnabled(name != active)
        self.prof_ov.blockSignals(True)
        self.prof_ov.set_value((self.s.get("profiles.items") or {}).get(name, {}))
        self.prof_ov.blockSignals(False)

    def _prof_save(self, ov):
        name = self._prof_current()
        items = self.s.get("profiles.items") or {}
        if name in items:
            items[name] = ov
            self._set("profiles.items", items)

    def _prof_activate(self):
        name = self._prof_current()
        if name:
            self._set("profiles.active", name)
            self._prof_fill(name)

    def _prof_new(self):
        name, ok = QInputDialog.getText(self, "New profile", "Name:")
        name = name.strip()
        items = self.s.get("profiles.items") or {}
        if ok and name and name not in items:
            items[name] = {}
            self._set("profiles.items", items)
            self._prof_fill(name)

    def _prof_rename(self):
        old = self._prof_current()
        if not old:
            return
        name, ok = QInputDialog.getText(self, "Rename profile", "Name:", text=old)
        name = name.strip()
        items = self.s.get("profiles.items") or {}
        if ok and name and name not in items:
            items = {(name if k == old else k): val for k, val in items.items()}
            self.s.set("profiles.items", items, save=False)
            if self.s.get("profiles.active") == old:
                self.s.set("profiles.active", name, save=False)
            self._set("profiles.items", items)
            self._prof_fill(name)

    def _prof_del(self):
        name = self._prof_current()
        items = self.s.get("profiles.items") or {}
        if not name or len(items) <= 1:
            return
        items.pop(name, None)
        if self.s.get("profiles.active") == name:
            self.s.set("profiles.active", next(iter(items)), save=False)
        self._set("profiles.items", items)
        self._prof_name = None
        self._prof_fill()

    # -- history ------------------------------------------------------------------------
    def _p_history(self):
        w, v = self._page("History", "Everything you've dictated, searchable. Stored only on this PC.")
        c, l = card()
        keep = self._toggle("history.enabled")
        l.addWidget(row("Keep history", "Off = nothing is stored.", keep))
        l.addWidget(row("Delete entries older than", None, self._spin("history.retention_days", 1, 3650, 1, " days")))
        v.addWidget(c)
        self.hist_search = QLineEdit()
        self.hist_search.setPlaceholderText("Search…")
        self.hist_search.textChanged.connect(lambda *_: self._hist_timer.start(200))
        self._hist_timer = QTimer(self)
        self._hist_timer.setSingleShot(True)
        self._hist_timer.timeout.connect(self._history_refresh)
        self.hist_stats = QLabel("")
        self.hist_stats.setObjectName("Muted")
        srow = QHBoxLayout()
        srow.addWidget(self.hist_search, 1)
        srow.addSpacing(12)
        srow.addWidget(self.hist_stats)
        v.addLayout(srow)
        self.hist_table = _table(["When", "App", "Text"], 2)
        self.hist_table.setMinimumHeight(260)
        self.hist_table.currentCellChanged.connect(lambda *_: self._hist_show())
        v.addWidget(self.hist_table, 1)
        self.hist_detail = QPlainTextEdit()
        self.hist_detail.setReadOnly(True)
        self.hist_detail.setFixedHeight(130)
        v.addWidget(self.hist_detail)
        v.addLayout(_hbox(_btn("Copy", self._hist_copy), _btn("Fix this everywhere…", self._hist_fix, "Primary"),
                          _btn("Delete", self._hist_del, "Danger"), _btn("Clear all…", self._hist_clear, "Ghost")))
        self._hist_rows: list[dict] = []
        return w

    def _history_refresh(self):
        self._hist_rows = self.history.search(self.hist_search.text().strip())
        st = self.history.stats()
        self.hist_stats.setText(f"{st['count']} dictations · {st['words']:,} words · {st['audio_minutes']} min spoken")
        t = self.hist_table
        t.setRowCount(len(self._hist_rows))
        for i, r in enumerate(self._hist_rows):
            t.setItem(i, 0, QTableWidgetItem(datetime.fromtimestamp(r["ts"]).strftime("%d %b %H:%M")))
            t.setItem(i, 1, QTableWidgetItem((r["app"] or "—").replace(".exe", "")))
            t.setItem(i, 2, QTableWidgetItem((r["final"] or "").replace("\n", " ⏎ ")))
        if self._hist_rows:
            t.setCurrentCell(0, 0)

    def _hist_cur(self) -> dict | None:
        i = self.hist_table.currentRow()
        return self._hist_rows[i] if 0 <= i < len(self._hist_rows) else None

    def _hist_show(self):
        r = self._hist_cur()
        if not r:
            self.hist_detail.clear()
            return
        meta = f"{r['kind'] or 'dictation'} · {r['profile'] or ''} · {r['audio_s'] or 0:.1f}s audio · " \
               f"whisper {r['stt_ms'] or 0} ms · AI {r['llm'] or 'skipped'} {r['llm_ms'] or 0} ms"
        self.hist_detail.setPlainText(f"{r['final']}\n\n— Whisper heard —\n{r['raw']}\n\n{meta}")

    def _hist_copy(self):
        r = self._hist_cur()
        if r:
            QGuiApplication.clipboard().setText(r["final"] or "")

    def _hist_fix(self):
        heard = self.hist_detail.textCursor().selectedText().strip()
        d = FixDialog(self, heard)
        if d.exec():
            wrong, right = d.heard.text().strip(), d.right.text().strip()
            if not (wrong and right):
                return
            corr = self.s.get("vocabulary.corrections") or {}
            lst = corr.setdefault(right, [])
            if wrong.lower() not in [x.lower() for x in lst]:
                lst.append(wrong)
            self._set("vocabulary.corrections", corr)
            if d.hot.isChecked():
                hw = self.s.get("vocabulary.hotwords") or []
                if right not in hw:
                    hw.append(right)
                    self._set("vocabulary.hotwords", hw)
            self._vocab_fill()

    def _hist_del(self):
        r = self._hist_cur()
        if r:
            self.history.delete(r["id"])
            self._history_refresh()

    def _hist_clear(self):
        if QMessageBox.question(self, "Clear history", "Delete all dictation history?") == QMessageBox.Yes:
            self.history.clear()
            self._history_refresh()

    # -- advanced -------------------------------------------------------------------------
    def _p_advanced(self):
        w, v = self._page("Advanced", "Diagnostics, backups and integrations.")
        c, l = card("Tools")
        self.adv_out = QPlainTextEdit()
        self.adv_out.setReadOnly(True)
        self.adv_out.setMinimumHeight(220)
        _mono(self.adv_out)
        l.addLayout(_hbox(_btn("Run diagnostics", self._adv_doctor, "Primary"), _btn("Self-test", self._adv_selftest),
                          _btn("Hotkey cheat sheet", self._adv_cheat), _btn("Bundle logs", self._adv_bundle)))
        l.addLayout(_hbox(_btn("Export settings…", self._adv_export), _btn("Import settings…", self._adv_import),
                          _btn("Open data folder", self._adv_open)))
        l.addWidget(self.adv_out)
        v.addWidget(c)

        c, l = card("Local transcription API",
                    "An OpenAI-compatible endpoint on this PC only (127.0.0.1). Point Cursor, Open WebUI or your own "
                    "scripts at it instead of a cloud Whisper API.")
        l.addWidget(row("Enable API", None, self._toggle("api_server.enabled")))
        l.addWidget(row("Port", None, self._spin("api_server.port", 1024, 65535)))
        url = QLabel(f"Base URL: http://127.0.0.1:{self.s.get('api_server.port')}/v1   ·   any API key works")
        url.setObjectName("Muted")
        url.setTextInteractionFlags(Qt.TextSelectableByMouse)
        l.addWidget(url)
        v.addWidget(c)

        c, l = card("Updates", "Off by default (fully offline). When on, checks GitHub releases once a day.")
        l.addWidget(row("Check for updates", None, self._toggle("update_check.enabled")))
        l.addWidget(row("Repository", None, self._line("update_check.repo", placeholder="owner/repo")))
        v.addWidget(c)
        v.addStretch(1)
        return w

    def _adv_doctor(self):
        self.adv_out.setPlainText("Running diagnostics…")
        run_bg(lambda: diagnostics.format_report(diagnostics.doctor(self.s)),
               lambda res, err: self.adv_out.setPlainText(res if not err else f"Failed: {err}"))

    def _adv_selftest(self):
        self.adv_out.setPlainText("Self-test: speak for 1.5 seconds…")
        run_bg(lambda: diagnostics.format_report(diagnostics.selftest(self.s)),
               lambda res, err: self.adv_out.setPlainText(res if not err else f"Failed: {err}"))

    def _adv_cheat(self):
        TextDialog(self, "Hotkey cheat sheet", diagnostics.cheat_sheet(self.s)).exec()

    def _adv_bundle(self):
        self.adv_out.setPlainText("Bundling logs…")
        run_bg(lambda: diagnostics.bundle_logs(self.s),
               lambda res, err: self.adv_out.setPlainText(f"Saved {res}\nSecrets and shell commands are redacted."
                                                          if not err else f"Failed: {err}"))

    def _adv_export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export settings", "localflow-settings.json", "JSON (*.json)")
        if not path:
            return
        incl = QMessageBox.question(
            self, "Secrets", "Include secret snippets?\n\nThey stay encrypted and only decrypt on this Windows "
                             "account, so they're useless on another PC.") == QMessageBox.Yes
        self.s.export(path, include_secrets=incl)
        self.adv_out.setPlainText(f"Exported to {path}")

    def _adv_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import settings", "", "JSON (*.json)")
        if not path:
            return
        try:
            self.s.import_(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Import", f"Couldn't import: {e}")
            return
        if self.app is not None:
            self.app.on_settings_changed()
        QMessageBox.information(self, "Import", "Settings imported. Reopen this window to see them.")

    def _adv_open(self):
        config.DATA_DIR.mkdir(exist_ok=True)
        if sys.platform == "win32":
            os.startfile(config.DATA_DIR)  # noqa: S606
        else:
            self.adv_out.setPlainText(str(config.DATA_DIR))

    def closeEvent(self, e):
        self.suspend_hotkeys(False)
        super().closeEvent(e)
