"""Reusable, animated controls for the settings app.

Every control paints itself (QPainter + SVG icons) so motion can be eased:
toggles slide, dropdown chevrons rotate and menus fade, the nav highlight
glides between items, choice cards and buttons ease on hover.
"""
from __future__ import annotations

import math
import threading
import time

from PySide6.QtCore import (Property, QEasingCurve, QObject, QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt,
                            QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QComboBox, QFormLayout, QFrame, QGraphicsOpacityEffect, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QStyledItemDelegate,
                               QToolButton, QVBoxLayout, QWidget)

from . import icons
from . import theme as T


def qc(hex_: str, alpha: float = 1.0) -> QColor:
    c = QColor(hex_)
    c.setAlphaF(alpha)
    return c


def mix(a: str | QColor, b: str | QColor, t: float) -> QColor:
    a, b = QColor(a), QColor(b)
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t), int(a.alpha() + (b.alpha() - a.alpha()) * t))


def animate(obj: QObject, prop: bytes, end, ms: int = 160, curve=QEasingCurve.OutCubic) -> QPropertyAnimation:
    """Start (or retarget) an animation on obj.prop. Keeps a reference on obj."""
    store = obj.__dict__.setdefault("_anims", {})
    a = store.get(prop)
    if a is None:
        a = QPropertyAnimation(obj, prop, obj)
        store[prop] = a
    a.stop()
    a.setDuration(ms)
    a.setEasingCurve(curve)
    a.setStartValue(obj.property(prop.decode()))
    a.setEndValue(end)
    a.start()
    return a


class _HoverMixin:
    """Adds an animated 0..1 `hover` property."""

    def _init_hover(self):
        self._hover = 0.0
        self.setAttribute(Qt.WA_Hover)

    def get_hover(self):
        return self._hover

    def set_hover(self, v):
        self._hover = v
        self.update()

    def enterEvent(self, e):
        animate(self, b"hover", 1.0, 120)
        super().enterEvent(e)

    def leaveEvent(self, e):
        animate(self, b"hover", 0.0, 200)
        super().leaveEvent(e)


# ===========================================================================
# Toggle
# ===========================================================================

class Toggle(_HoverMixin, QAbstractButton):
    """Switch with an eased knob, colour cross-fade and a slight stretch while pressed."""
    W, H = 38, 22

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self._init_hover()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.W, self.H)
        self._knob = 1.0 if checked else 0.0
        self._press = 0.0
        # NB: never name an animated property "pos" - QWidget already has a "pos"
        # property (its screen position), and animating it slides the whole widget away.
        self.toggled.connect(lambda on: animate(self, b"knob", 1.0 if on else 0.0, 200, QEasingCurve.OutCubic))

    def get_knob(self):
        return self._knob

    def set_knob(self, v):
        self._knob = v
        self.update()

    def get_press(self):
        return self._press

    def set_press(self, v):
        self._press = v
        self.update()

    knob = Property(float, get_knob, set_knob)
    press = Property(float, get_press, set_press)
    hover = Property(float, _HoverMixin.get_hover, _HoverMixin.set_hover)

    def setChecked(self, on):  # programmatic changes jump without animating
        super().setChecked(on)
        if not self.signalsBlocked():
            return
        self._knob = 1.0 if on else 0.0
        self.update()

    def mousePressEvent(self, e):
        animate(self, b"press", 1.0, 100)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        animate(self, b"press", 0.0, 160)
        super().mouseReleaseEvent(e)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def sizeHint(self):
        return QSize(self.W, self.H)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        off = mix("#2f2f2f", "#3a3a3a", self._hover)
        on = mix(T.ACCENT, "#ffffff", self._hover)
        track = mix(off, on, self._knob)
        if not self.isEnabled():
            track.setAlphaF(0.4)
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, self.W, self.H), self.H / 2, self.H / 2)
        # knob: stretches toward its direction of travel while pressed
        d = self.H - 6
        stretch = 5 * self._press
        x = 3 + self._knob * (self.W - 6 - d)
        if self._knob > 0.5:
            x -= stretch
        p.setBrush(mix("#d6d6d6", T.ON_ACCENT, self._knob))
        p.drawRoundedRect(QRectF(x, 3, d + stretch, d), d / 2, d / 2)
        p.end()


# ===========================================================================
# Select (dropdown)
# ===========================================================================

class Select(QComboBox):
    """QComboBox with a chevron that rotates open and a menu that fades in."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._angle = 0.0
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(32)
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        # Dark, rounded menu: the popup is its own top-level window, so it needs a
        # frameless translucent container and a styled list (the delegate makes the
        # per-item styles apply).
        self.setItemDelegate(QStyledItemDelegate(self))
        view = self.view()
        view.setStyleSheet(
            f"QListView {{ background: {T.SURFACE_2}; color: {T.TEXT}; border: 1px solid #343434; border-radius: 9px;"
            f" padding: 4px; outline: 0; }}"
            f"QListView::item {{ min-height: 30px; padding: 0 10px; border-radius: 6px; color: {T.TEXT}; }}"
            f"QListView::item:hover {{ background: {T.SURFACE_3}; }}"
            f"QListView::item:selected {{ background: {T.SURFACE_3}; color: {T.TEXT}; }}")
        container = view.parentWidget()
        if container is not None:
            container.setWindowFlags(container.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
            container.setAttribute(Qt.WA_TranslucentBackground)
            container.setStyleSheet("background: transparent; border: none;")

    def get_angle(self):
        return self._angle

    def set_angle(self, v):
        self._angle = v
        self.update()

    angle = Property(float, get_angle, set_angle)

    def showPopup(self):
        super().showPopup()
        animate(self, b"angle", 180.0, 220)
        win = self.view().window()
        win.setWindowOpacity(0.0)
        a = QPropertyAnimation(win, b"windowOpacity", win)
        a.setDuration(140)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.setEasingCurve(QEasingCurve.OutCubic)
        a.start(QPropertyAnimation.DeleteWhenStopped)

    def hidePopup(self):
        super().hidePopup()
        animate(self, b"angle", 0.0, 220)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = 14
        cx, cy = self.width() - 17, self.height() / 2
        p.translate(cx, cy)
        p.rotate(self._angle)
        icons.paint(p, "chevron-down", T.TEXT_2 if self.isEnabled() else T.TEXT_3, QRectF(-s / 2, -s / 2, s, s))
        p.end()


# ===========================================================================
# Stepper (replaces spin boxes)
# ===========================================================================

class _StepButton(_HoverMixin, QToolButton):
    def __init__(self, name, parent=None):
        super().__init__(parent)
        self._init_hover()
        self.name = name
        self.setAutoRepeat(True)
        self.setAutoRepeatDelay(350)
        self.setAutoRepeatInterval(60)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(28, 30)

    hover = Property(float, _HoverMixin.get_hover, _HoverMixin.set_hover)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._hover > 0.01:
            p.setPen(Qt.NoPen)
            p.setBrush(qc("#ffffff", 0.06 * self._hover))
            p.drawRoundedRect(QRectF(2, 3, 24, 24), 5, 5)
        col = T.TEXT if self.isEnabled() else T.TEXT_3
        icons.paint(p, self.name, col, QRectF(7, 8, 14, 14))
        p.end()


class Stepper(QFrame):
    """[ − ] value [ + ]. Hold a button to repeat. Click the number to type one."""
    valueChanged = Signal(int)
    committed = Signal(int)            # fires once the value settles (for saving)

    def __init__(self, lo=0, hi=100, step=1, suffix="", parent=None):
        super().__init__(parent)
        self.setObjectName("Stepper")
        self.setStyleSheet(f"QFrame#Stepper {{ background: {T.SURFACE_2}; border: 1px solid {T.LINE}; border-radius: 7px; }}"
                           f"QFrame#Stepper QLineEdit {{ background: transparent; border: none; padding: 0; }}")
        self.lo, self.hi, self.step, self.suffix = lo, hi, step, suffix
        self._value = lo
        h = QHBoxLayout(self)
        h.setContentsMargins(1, 0, 1, 0)
        h.setSpacing(0)
        self.minus, self.plus = _StepButton("minus"), _StepButton("plus")
        self.edit = QLineEdit()
        self.edit.setAlignment(Qt.AlignCenter)
        self.edit.setFixedWidth(64)
        h.addWidget(self.minus)
        h.addWidget(self.edit)
        h.addWidget(self.plus)
        self.setFixedHeight(32)
        self.minus.clicked.connect(lambda: self.setValue(self._value - self.step, user=True))
        self.plus.clicked.connect(lambda: self.setValue(self._value + self.step, user=True))
        self.edit.editingFinished.connect(self._typed)
        self._commit = QTimer(self)
        self._commit.setSingleShot(True)
        self._commit.setInterval(500)
        self._commit.timeout.connect(lambda: self.committed.emit(self._value))
        self._render()

    def value(self) -> int:
        return self._value

    def setValue(self, v, user=False):
        v = max(self.lo, min(self.hi, int(v)))
        changed = v != self._value
        self._value = v
        self._render()
        if changed:
            self.valueChanged.emit(v)
            if user:
                self._commit.start()

    def _typed(self):
        digits = "".join(ch for ch in self.edit.text() if ch.isdigit())
        if digits:
            self.setValue(int(digits), user=True)
        self._render()

    def _render(self):
        self.edit.setText(f"{self._value}{self.suffix}")
        self.minus.setEnabled(self._value > self.lo)
        self.plus.setEnabled(self._value < self.hi)


# ===========================================================================
# Hotkey field (keycaps)
# ===========================================================================

_KEY_LABEL = {"ctrl": "Ctrl", "win": "Win", "alt": "Alt", "shift": "Shift", "esc": "Esc", "space": "Space",
              "enter": "Enter", "tab": "Tab", "backspace": "Backspace", "delete": "Del", "caps_lock": "Caps",
              "page_up": "PgUp", "page_down": "PgDn", "home": "Home", "end": "End", "insert": "Ins"}
_ORDER = {"ctrl": 0, "alt": 1, "shift": 2, "win": 3}


def _sorted_keys(keys):
    return sorted(keys, key=lambda k: (_ORDER.get(k, 9), k))


def key_label(k: str) -> str:
    return _KEY_LABEL.get(k, k.upper() if len(k) <= 3 else k.capitalize())


class HotkeyEdit(_HoverMixin, QWidget):
    """Shows a shortcut as keycaps. Click, then press the combination; Backspace clears."""
    changed = Signal(str)
    recording = Signal(bool)          # lets the app pause its global hotkeys meanwhile

    _MODS = {Qt.Key_Control: "ctrl", Qt.Key_Alt: "alt", Qt.Key_Shift: "shift", Qt.Key_Meta: "win",
             Qt.Key_Super_L: "win", Qt.Key_Super_R: "win", Qt.Key_AltGr: "alt"}
    _NAMES = {Qt.Key_Space: "space", Qt.Key_Return: "enter", Qt.Key_Enter: "enter", Qt.Key_Tab: "tab",
              Qt.Key_Escape: "esc", Qt.Key_Backspace: "backspace", Qt.Key_Delete: "delete",
              Qt.Key_CapsLock: "caps_lock", Qt.Key_Insert: "insert", Qt.Key_Home: "home", Qt.Key_End: "end",
              Qt.Key_PageUp: "page_up", Qt.Key_PageDown: "page_down"}

    def __init__(self, value="", allow_empty=True, parent=None):
        super().__init__(parent)
        self._init_hover()
        self.allow_empty = allow_empty
        self._value = value or ""
        self._held: list[str] = []
        self._best: list[str] = []
        self._rec = False
        self._pulse = 0.0
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(210, 32)
        self.setToolTip("Click, then press the keys" + (". Backspace clears." if allow_empty else "."))
        self._font = QFont(self.font())
        self._font.setPointSizeF(8.8)
        self._font.setWeight(QFont.DemiBold)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    hover = Property(float, _HoverMixin.get_hover, _HoverMixin.set_hover)

    def value(self) -> str:
        return self._value

    def set_value(self, v: str) -> None:
        self._value = v or ""
        self.update()

    def setFixedWidth(self, w):
        super().setFixedSize(w, 32)

    def _tick(self):
        self._pulse = (self._pulse + 0.05) % (2 * math.pi)
        self.update()

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self._held, self._best, self._rec = [], [], True
        self._timer.start()
        self.recording.emit(True)
        self.update()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._rec = False
        self._timer.stop()
        self.recording.emit(False)
        self.update()

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
        if n == "backspace" and not self._held:
            if self.allow_empty:
                self._value = ""
                self.changed.emit("")
            self.clearFocus()
            return
        if n == "esc" and not self._held and self._value:
            self.clearFocus()            # Esc alone = keep the old value
            return
        if n and n not in self._held:
            self._held.append(n)
            if len(self._held) >= len(self._best):
                self._best = list(self._held)
            self.update()

    def keyReleaseEvent(self, e):
        if e.isAutoRepeat():
            return
        n = self._name(e)
        if n in self._held:
            self._held.remove(n)
        if not self._held and self._best:
            self._value = "+".join(_sorted_keys(self._best))
            self.changed.emit(self._value)
            self.clearFocus()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        border = mix(T.LINE, "#363636", self._hover)
        if self._rec:
            border = mix("#5a5a5a", T.ACCENT, 0.35 + 0.35 * math.sin(self._pulse * 2))
        p.setPen(QPen(border, 1))
        p.setBrush(QColor(T.SURFACE_2))
        p.drawRoundedRect(r, 7, 7)

        keys = _sorted_keys(self._held) if self._rec and self._held else (
            [] if self._rec else [k for k in self._value.split("+") if k])
        x = 6.0
        p.setFont(self._font)
        fm = QFontMetrics(self._font)
        for k in keys:
            label = key_label(k)
            w = max(22, fm.horizontalAdvance(label) + 14)
            cap = QRectF(x, 5, w, self.height() - 10)
            p.setPen(QPen(QColor("#3a3a3a"), 1))
            p.setBrush(QColor("#2b2b2b"))
            p.drawRoundedRect(cap, 5, 5)
            p.setPen(QPen(QColor("#1c1c1c"), 1))            # bottom lip
            p.drawLine(cap.bottomLeft() + QPoint(4, 0), cap.bottomRight() - QPoint(4, 0))
            p.setPen(QColor(T.TEXT))
            p.drawText(cap, Qt.AlignCenter, label)
            x += w + 4
        if not keys:
            p.setFont(self.font())
            if self._rec:
                p.setPen(QColor(T.TEXT_2))
                p.setBrush(mix(T.RED, "#ffb4a6", 0.5 + 0.5 * math.sin(self._pulse * 2)))
                p.setPen(Qt.NoPen)
                p.drawEllipse(QRectF(12, self.height() / 2 - 3.5, 7, 7))
                p.setPen(QColor(T.TEXT_2))
                p.drawText(QRectF(26, 0, self.width() - 30, self.height()), Qt.AlignVCenter, "Press keys…")
            else:
                p.setPen(QColor(T.TEXT_3))
                p.drawText(QRectF(12, 0, self.width() - 16, self.height()), Qt.AlignVCenter, "Not set")
        p.end()


# ===========================================================================
# Buttons
# ===========================================================================

class Button(_HoverMixin, QPushButton):
    """variant: primary | secondary | ghost | danger. Optional leading icon."""

    def __init__(self, text="", variant="secondary", icon_name=None, parent=None):
        super().__init__(text, parent)
        self._init_hover()
        self.variant = variant
        self.icon_name = icon_name
        self._down = 0.0
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(32)
        self.setFlat(True)

    hover = Property(float, _HoverMixin.get_hover, _HoverMixin.set_hover)

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(self.text()) + (32 if self.variant != "ghost" else 18)
        if self.icon_name:
            w += 20
        return QSize(w, 32)

    def _colors(self):
        v, h = self.variant, self._hover
        if v == "primary":
            return mix(T.ACCENT, "#ffffff", h), QColor(T.ON_ACCENT), None
        if v == "danger":
            return mix(T.SURFACE_2, "#2c1d1a", h), QColor(T.RED), QColor(T.LINE)
        if v == "ghost":
            return qc("#ffffff", 0.05 * h), mix(T.TEXT_2, T.TEXT, h), None
        return mix(T.SURFACE_2, T.SURFACE_3, h), QColor(T.TEXT), QColor(T.LINE)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg, fg, border = self._colors()
        if not self.isEnabled():
            fg = QColor(T.TEXT_3)
            bg.setAlphaF(bg.alphaF() * 0.5)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        if self.isDown():
            r.adjust(0, 1, 0, 1)
        p.setPen(QPen(border, 1) if border else Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 7, 7)
        fm = QFontMetrics(self.font())
        tw = fm.horizontalAdvance(self.text())
        iw = 20 if self.icon_name else 0
        x = (self.width() - tw - iw) / 2
        if self.icon_name:
            icons.paint(p, self.icon_name, fg.name(), QRectF(x, (self.height() - 15) / 2 + (1 if self.isDown() else 0), 15, 15))
        font = QFont(self.font())
        if self.variant == "primary":
            font.setWeight(QFont.DemiBold)
        p.setFont(font)
        p.setPen(fg)
        p.drawText(QRectF(x + iw, 1 if self.isDown() else 0, tw + 4, self.height()), Qt.AlignVCenter | Qt.AlignLeft,
                   self.text())
        p.end()


# ===========================================================================
# Groups and rows
# ===========================================================================

class GroupFrame(QFrame):
    """Rounded surface; draws hairlines between consecutive rows."""

    def __init__(self):
        super().__init__()
        self.setObjectName("Group")

    def paintEvent(self, e):
        super().paintEvent(e)
        lay = self.layout()
        if not lay:
            return
        rows = [lay.itemAt(i).widget() for i in range(lay.count())]
        rows = [w for w in rows if w is not None and w.isVisible()]
        p = QPainter(self)
        p.setPen(QPen(QColor(T.HAIRLINE), 1))
        for a, b in zip(rows, rows[1:]):
            if a.objectName() == "Row" and b.objectName() == "Row":
                y = (a.geometry().bottom() + b.geometry().top()) // 2 + 1
                p.drawLine(16, y, self.width() - 16, y)
        p.end()


def card(title: str | None = None, subtitle: str | None = None) -> tuple[QWidget, QVBoxLayout]:
    """A titled group. Returns (container to add to the page, layout to add rows to)."""
    outer = QWidget()
    ov = QVBoxLayout(outer)
    ov.setContentsMargins(0, 6, 0, 0)
    ov.setSpacing(6)
    if title:
        t = QLabel(title.upper())
        t.setObjectName("GroupTitle")
        t.setContentsMargins(4, 0, 0, 0)
        ov.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("GroupSub")
        s.setWordWrap(True)
        s.setContentsMargins(4, 0, 0, 2)
        ov.addWidget(s)
    frame = GroupFrame()
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 6, 16, 6)
    lay.setSpacing(8)
    ov.addWidget(frame)
    outer.group = frame
    return outer, lay


def divider() -> QWidget:
    """Kept for compatibility: rows already get separators."""
    w = QWidget()
    w.setFixedHeight(0)
    return w


def row(title: str, desc: str | None, widget: QWidget) -> QWidget:
    w = QWidget()
    w.setObjectName("Row")
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 8, 0, 8)
    h.setSpacing(20)
    v = QVBoxLayout()
    v.setSpacing(2)
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
    v.setContentsMargins(4, 0, 0, 4)
    v.setSpacing(4)
    t = QLabel(title)
    t.setObjectName("H1")
    s = QLabel(subtitle)
    s.setObjectName("Sub")
    s.setWordWrap(True)
    v.addWidget(t)
    v.addWidget(s)
    return w


# ===========================================================================
# Choice cards
# ===========================================================================

class ChoiceCard(_HoverMixin, QAbstractButton):
    def __init__(self, title, desc, parent=None):
        super().__init__(parent)
        self._init_hover()
        self._sel = 0.0
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumHeight(68)
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 11, 34, 11)
        v.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("font-weight: 600; background: transparent;")
        d = QLabel(desc)
        d.setObjectName("Muted")
        d.setWordWrap(True)
        for lab in (t, d):
            lab.setAttribute(Qt.WA_TransparentForMouseEvents)
            v.addWidget(lab)
        v.addStretch(1)
        self.toggled.connect(lambda on: animate(self, b"sel", 1.0 if on else 0.0, 200))

    hover = Property(float, _HoverMixin.get_hover, _HoverMixin.set_hover)

    def get_sel(self):
        return self._sel

    def set_sel(self, v):
        self._sel = v
        self.update()

    sel = Property(float, get_sel, set_sel)

    def sizeHint(self):
        return QSize(160, max(68, self.layout().sizeHint().height()))

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        bg = mix(T.SURFACE_2, "#262626", max(self._hover * 0.8, self._sel * 0.6))
        border = mix(mix(T.LINE, "#363636", self._hover), "#8a8a8a", self._sel)
        p.setPen(QPen(border, 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, 9, 9)
        # radio: ring -> filled disc with a check that scales in
        c = QRectF(self.width() - 26, 12, 14, 14)
        p.setPen(QPen(mix("#4a4a4a", T.ACCENT, self._sel), 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c)
        if self._sel > 0.01:
            s = 14 * self._sel
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.ACCENT))
            p.drawEllipse(QRectF(c.center().x() - s / 2, c.center().y() - s / 2, s, s))
            if self._sel > 0.5:
                k = (self._sel - 0.5) * 2
                icons.paint(p, "check", T.ON_ACCENT, QRectF(c.center().x() - 5 * k, c.center().y() - 5 * k, 10 * k, 10 * k))
        p.end()


class ChoiceGrid(QWidget):
    picked = Signal(object)

    def __init__(self, options: dict, current, cols: int):
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 4, 0, 4)
        grid.setSpacing(8)
        self.cards = {}
        for i, (val, (title, desc)) in enumerate(options.items()):
            c = ChoiceCard(title, desc)
            c.setChecked(val == current)
            c._sel = 1.0 if val == current else 0.0
            c.clicked.connect(lambda _=False, v=val: self.select(v, emit=True))
            grid.addWidget(c, i // cols, i % cols)
            self.cards[val] = c
        for col in range(cols):
            grid.setColumnStretch(col, 1)

    def select(self, val, emit=False):
        for v, c in self.cards.items():
            c.setChecked(v == val)
        if emit:
            self.picked.emit(val)


# ===========================================================================
# Sidebar with a gliding highlight
# ===========================================================================

class _NavItem(_HoverMixin, QAbstractButton):
    def __init__(self, text, icon_name, parent=None):
        super().__init__(parent)
        self._init_hover()
        self.text_ = text
        self.icon_name = icon_name
        self._sel = 0.0
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(32)

    hover = Property(float, _HoverMixin.get_hover, _HoverMixin.set_hover)

    def get_sel(self):
        return self._sel

    def set_sel(self, v):
        self._sel = v
        self.update()

    sel = Property(float, get_sel, set_sel)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._hover > 0.01 and self._sel < 0.99:
            p.setPen(Qt.NoPen)
            p.setBrush(qc("#ffffff", 0.035 * self._hover * (1 - self._sel)))
            p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 7, 7)
        col = mix(mix(T.TEXT_3, T.TEXT_2, self._hover), T.TEXT, self._sel)
        icons.paint(p, self.icon_name, col.name(), QRectF(10, 7, 18, 18))
        f = QFont(self.font())
        if self._sel > 0.5:
            f.setWeight(QFont.Medium)
        p.setFont(f)
        p.setPen(col)
        p.drawText(QRectF(38, 0, self.width() - 40, self.height()), Qt.AlignVCenter, self.text_)
        p.end()


class Sidebar(QWidget):
    currentChanged = Signal(int)

    def __init__(self, sections: list[tuple[str | None, list[tuple[str, str]]]], parent=None):
        super().__init__(parent)
        self.items: list[_NavItem] = []
        self._current = -1
        self.indicator = QFrame(self)
        self.indicator.setStyleSheet(f"background: {T.SURFACE_3}; border-radius: 7px;")
        self.indicator.hide()
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 0, 10, 0)
        v.setSpacing(1)
        for title, entries in sections:
            if title:
                lab = QLabel(title.upper())
                lab.setObjectName("NavSection")
                v.addWidget(lab)
            else:
                v.addSpacing(10)
            for text, icon_name in entries:
                it = _NavItem(text, icon_name)
                idx = len(self.items)
                it.clicked.connect(lambda _=False, i=idx: self.setCurrent(i))
                self.items.append(it)
                v.addWidget(it)
        v.addStretch(1)
        self._anim = QPropertyAnimation(self.indicator, b"geometry", self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def current(self) -> int:
        return self._current

    def setCurrent(self, i: int, animated: bool = True):
        if not 0 <= i < len(self.items):
            return
        changed = i != self._current
        for j, it in enumerate(self.items):
            it.setChecked(j == i)
            animate(it, b"sel", 1.0 if j == i else 0.0, 180)
        self._current = i
        self._move_indicator(animated and self.indicator.isVisible())
        if changed:
            self.currentChanged.emit(i)

    def _move_indicator(self, animated: bool):
        if self._current < 0:
            return
        target = self.items[self._current].geometry()
        if target.height() <= 0:
            return
        self.indicator.show()
        self.indicator.lower()
        if animated:
            self._anim.stop()
            self._anim.setStartValue(self.indicator.geometry())
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self.indicator.setGeometry(target)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, lambda: self._move_indicator(False))

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, lambda: self._move_indicator(False))


def fade_in(w: QWidget, ms: int = 180) -> None:
    """Cross-fade a page in; the effect is removed afterwards so text stays crisp."""
    eff = QGraphicsOpacityEffect(w)
    eff.setOpacity(0.0)
    w.setGraphicsEffect(eff)
    a = QPropertyAnimation(eff, b"opacity", w)
    a.setDuration(ms)
    a.setStartValue(0.0)
    a.setEndValue(1.0)
    a.setEasingCurve(QEasingCurve.OutCubic)
    a.finished.connect(lambda: w.setGraphicsEffect(None))
    a.start(QPropertyAnimation.DeleteWhenStopped)


class SavedBadge(QWidget):
    """'Saved' with a check, fades in on each change and fades out again."""

    def __init__(self, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(5)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("check", T.GREEN, 13))
        lab = QLabel("Saved")
        lab.setObjectName("Saved")
        h.addWidget(ic)
        h.addWidget(lab)
        h.addStretch(1)
        self.eff = QGraphicsOpacityEffect(self)
        self.eff.setOpacity(0.0)
        self.setGraphicsEffect(self.eff)
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(lambda: self._fade(0.0, 500))

    def _fade(self, to, ms):
        animate(self.eff, b"opacity", to, ms)

    def flash(self):
        self._fade(1.0, 120)
        self._hold.start(1400)


# ===========================================================================
# Background work
# ===========================================================================

class _Relay(QObject):
    done = Signal(object, object)


_keep: set = set()


def run_bg(fn, on_done) -> None:
    """Run fn() in a thread, deliver (result, error) to on_done on the Qt thread."""
    relay = _Relay()
    relay.done.connect(on_done)
    _keep.add(relay)

    def work():
        try:
            res, err = fn(), None
        except Exception as e:  # noqa: BLE001
            res, err = None, e
        relay.done.emit(res, err)
        _keep.discard(relay)

    threading.Thread(target=work, daemon=True).start()


# ===========================================================================
# Override editor (profiles and app rules)
# ===========================================================================

INHERIT = "Inherit"

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
            c = Select()
            c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            c.addItem(INHERIT, None)
            if opts is bool:
                c.addItem("On", True)
                c.addItem("Off", False)
            else:
                for o in opts:
                    if o == "auto" and key == "whisper.language":
                        c.addItem("auto", "__auto__")
                    else:
                        c.addItem(o.replace("_", " "), o)
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
