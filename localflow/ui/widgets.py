"""Reusable widgets: toggle switch, hotkey recorder, cards, rows, override editor."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QPropertyAnimation, QRectF, Qt, Signal, Property
from PySide6.QtGui import QColor, QKeySequence, QPainter
from PySide6.QtWidgets import (QAbstractButton, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QSizePolicy,
                               QVBoxLayout, QWidget, QFormLayout)

from .theme import ACCENT


class Toggle(QAbstractButton):
    """iOS-style switch."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(40, 22)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(120)
        self.toggled.connect(self._animate)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def get_pos(self):
        return self._pos

    def set_pos(self, v):
        self._pos = v
        self.update()

    pos = Property(float, get_pos, set_pos)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = QColor(ACCENT)
        off = QColor("#33333b")
        track = QColor(int(off.red() + (on.red() - off.red()) * self._pos),
                       int(off.green() + (on.green() - off.green()) * self._pos),
                       int(off.blue() + (on.blue() - off.blue()) * self._pos))
        if not self.isEnabled():
            track.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 40, 22), 11, 11)
        p.setBrush(QColor("white"))
        x = 3 + self._pos * 18
        p.drawEllipse(QRectF(x, 3, 16, 16))
        p.end()

    def sizeHint(self):
        return self.size()


class HotkeyEdit(QLineEdit):
    """Click, then press the combination. Emits `changed(str)` like 'ctrl+win'."""
    changed = Signal(str)
    recording = Signal(bool)          # lets the app pause its global hotkeys meanwhile

    _MODS = {Qt.Key_Control: "ctrl", Qt.Key_Alt: "alt", Qt.Key_Shift: "shift", Qt.Key_Meta: "win",
             Qt.Key_Super_L: "win", Qt.Key_Super_R: "win", Qt.Key_AltGr: "alt"}
    _NAMES = {Qt.Key_Space: "space", Qt.Key_Return: "enter", Qt.Key_Enter: "enter", Qt.Key_Tab: "tab",
              Qt.Key_Escape: "esc", Qt.Key_Backspace: "backspace", Qt.Key_Delete: "delete",
              Qt.Key_CapsLock: "caps_lock", Qt.Key_Insert: "insert", Qt.Key_Home: "home", Qt.Key_End: "end",
              Qt.Key_PageUp: "page_up", Qt.Key_PageDown: "page_down"}

    def __init__(self, value="", allow_empty=True, parent=None):
        super().__init__(value, parent)
        self.setObjectName("HotkeyEdit")
        self.setReadOnly(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(210)
        self.allow_empty = allow_empty
        self._value = value
        self._held: list[str] = []
        self._best: list[str] = []
        self.setPlaceholderText("Not set")
        self.setToolTip("Click, then press the keys. Backspace clears.")

    def value(self) -> str:
        return self._value

    def set_value(self, v: str) -> None:
        self._value = v or ""
        self.setText(self._value)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self._held, self._best = [], []
        self.setProperty("recording", "true")
        self.style().polish(self)
        self.setText("Press keys…")
        self.recording.emit(True)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.setProperty("recording", "false")
        self.style().polish(self)
        self.setText(self._value)
        self.recording.emit(False)

    def _name(self, e) -> str | None:
        k = e.key()
        if k in self._MODS:
            return self._MODS[k]
        if k in self._NAMES:
            return self._NAMES[k]
        if Qt.Key_F1 <= k <= Qt.Key_F24:
            return f"f{k - Qt.Key_F1 + 1}"
        txt = QKeySequence(k).toString().lower()
        return txt if len(txt) == 1 else None

    def keyPressEvent(self, e):
        if e.isAutoRepeat():
            return
        n = self._name(e)
        if n == "backspace" and not self._held and self.allow_empty:
            self._value = ""
            self.changed.emit("")
            self.clearFocus()
            return
        if n and n not in self._held:
            self._held.append(n)
            if len(self._held) >= len(self._best):
                self._best = list(self._held)
            order = {"ctrl": 0, "alt": 1, "shift": 2, "win": 3}
            self.setText("+".join(sorted(self._held, key=lambda k: (order.get(k, 9), k))))

    def keyReleaseEvent(self, e):
        if e.isAutoRepeat():
            return
        n = self._name(e)
        if n in self._held:
            self._held.remove(n)
        if not self._held and self._best:
            order = {"ctrl": 0, "alt": 1, "shift": 2, "win": 3}
            combo = "+".join(sorted(self._best, key=lambda k: (order.get(k, 9), k)))
            self._value = combo
            self.changed.emit(combo)
            self.clearFocus()


def card(title: str | None = None, subtitle: str | None = None) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("Card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(10)
    if title:
        t = QLabel(title)
        t.setObjectName("CardTitle")
        lay.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("Muted")
        s.setWordWrap(True)
        lay.addWidget(s)
    return f, lay


def divider() -> QFrame:
    d = QFrame()
    d.setObjectName("Divider")
    return d


def row(title: str, desc: str | None, widget: QWidget) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 2, 0, 2)
    h.setSpacing(16)
    v = QVBoxLayout()
    v.setSpacing(1)
    t = QLabel(title)
    t.setObjectName("RowTitle")
    v.addWidget(t)
    if desc:
        d = QLabel(desc)
        d.setObjectName("Muted")
        d.setWordWrap(True)
        v.addWidget(d)
    h.addLayout(v, 1)
    h.addWidget(widget, 0, Qt.AlignRight | Qt.AlignVCenter)
    return w


def page_header(title: str, subtitle: str) -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 6)
    v.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("H1")
    s = QLabel(subtitle)
    s.setObjectName("Sub")
    s.setWordWrap(True)
    v.addWidget(t)
    v.addWidget(s)
    return w


class _Relay(QObject):
    done = Signal(object, object)


def run_bg(fn, on_done) -> None:
    """Run fn() in a thread, deliver (result, error) to on_done on the Qt thread."""
    relay = _Relay()
    relay.done.connect(on_done)
    run_bg._keep = getattr(run_bg, "_keep", set())
    run_bg._keep.add(relay)

    def work():
        try:
            res, err = fn(), None
        except Exception as e:  # noqa: BLE001
            res, err = None, e
        relay.done.emit(res, err)
        run_bg._keep.discard(relay)

    threading.Thread(target=work, daemon=True).start()


# ---------------------------------------------------------------------------
# Override editor (used by profiles and app rules)
# ---------------------------------------------------------------------------

INHERIT = "— inherit —"

OVERRIDE_FIELDS = [
    ("cleanup.level", "Cleanup level", ["none", "light", "medium", "high"]),
    ("cleanup.style", "Writing style", ["neutral", "formal", "casual", "very_casual", "verbatim"]),
    ("delivery.method", "Delivery", ["paste", "type", "copy"]),
    ("delivery.auto_send", "Press Enter after", bool),
    ("formatting.spoken_lists", "Spoken lists", bool),
    ("formatting.capitalize_first", "Capitalize first letter", bool),
    ("formatting.ensure_punctuation", "Always end with punctuation", bool),
    ("formatting.strip_trailing_period", "Drop final period", bool),
    ("modes.translate", "Translate to English", bool),
    ("modes.continuous", "Continuous dictation", bool),
    ("whisper.language", "Language", ["en", "ar", "ur", "hi", "fr", "de", "es", "auto"]),
]


class OverrideEditor(QWidget):
    changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setHorizontalSpacing(18)
        self.form.setVerticalSpacing(8)
        self.combos: dict[str, QComboBox] = {}
        for key, label, opts in OVERRIDE_FIELDS:
            c = QComboBox()
            c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            c.addItem(INHERIT, None)
            if opts is bool:
                c.addItem("On", True)
                c.addItem("Off", False)
            else:
                for o in opts:
                    c.addItem(o.replace("_", " "), None if o == "auto" and key == "whisper.language" else o)
                    if o == "auto" and key == "whisper.language":
                        c.setItemData(c.count() - 1, "__auto__")
            c.currentIndexChanged.connect(lambda *_: self.changed.emit(self.value()))
            self.combos[key] = c
            lab = QLabel(label)
            lab.setObjectName("RowTitle")
            self.form.addRow(lab, c)

    def set_value(self, overrides: dict) -> None:
        for key, c in self.combos.items():
            c.blockSignals(True)
            c.setCurrentIndex(0)
            if key in overrides:
                v = overrides[key]
                target = "__auto__" if (key == "whisper.language" and v is None) else v
                for i in range(1, c.count()):
                    if c.itemData(i) == target:
                        c.setCurrentIndex(i)
            c.blockSignals(False)

    def value(self) -> dict:
        out = {}
        for key, c in self.combos.items():
            if c.currentIndex() == 0:
                continue
            d = c.currentData()
            out[key] = None if d == "__auto__" else d
        return out


def summarize_overrides(ov: dict) -> str:
    if not ov:
        return "No changes"
    parts = []
    for key, label, opts in OVERRIDE_FIELDS:
        if key in ov:
            v = ov[key]
            if opts is bool:
                parts.append(f"{label}: {'on' if v else 'off'}")
            else:
                parts.append(f"{label}: {str(v or 'auto').replace('_', ' ')}")
    return " · ".join(parts)
