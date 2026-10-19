"""The pill at the bottom of the screen.

Frameless, always-on-top, click-through and never focused, so text still lands in
the app you're dictating into. Anti-aliased QPainter drawing; the animation timer
only runs while something moves (~0% CPU idle).

States: hidden, idle, listening, locked (hands-free, red dot), rephrase (violet
dot), processing, done, message, paused. Optional live-preview text sits next to
the waveform.
"""
from __future__ import annotations

import math
import sys
import time

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

WIN_W, WIN_H = 520, 64
BARS = 17
BARS_W = BARS * 3 + (BARS - 1) * 3
PREVIEW_MAX = 300

BG = QColor(18, 18, 18, 240)
BORDER = QColor(255, 255, 255, 34)
FG = QColor(237, 237, 237)
MUTED = QColor(237, 237, 237, 150)
RED = QColor("#e5674f")       # hands-free recording
BLUE = QColor("#7aa7e8")      # rephrase
AMBER = QColor("#e0a84b")     # paused
GREEN = QColor("#5bb98c")     # done


class Overlay(QWidget):
    def __init__(self, show_idle: bool = True, margin: int = 16):
        super().__init__(None)
        self.show_idle = show_idle
        self.margin = margin
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                            | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFixedSize(WIN_W, WIN_H)

        self.state = "hidden"
        self.message = ""
        self.msg_color = FG
        self.preview = ""
        self.paused = False
        self.w = self.h = 0.0
        self.alpha = 0.0
        self.level = 0.0
        self._target_level = 0.0
        self._flash = 0.0
        self._t0 = time.monotonic()
        self._font = QFont("Segoe UI", 9)
        self._font.setWeight(QFont.DemiBold)
        self._pfont = QFont("Segoe UI", 9)
        self._fm = QFontMetrics(self._font)
        self._pfm = QFontMetrics(self._pfont)

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._revert = QTimer(self)
        self._revert.setSingleShot(True)
        self._revert.timeout.connect(lambda: self.set_state("idle"))

    # -- public API (Qt thread) -------------------------------------------------
    def set_state(self, state: str) -> None:
        self._revert.stop()
        if state == "idle" and self.paused:
            state = "paused"
        if state == "done":
            self._flash = 1.0
        if state == "idle" and not self.show_idle:
            state = "hidden"
        if state in ("listening", "locked", "rephrase") and self.state not in ("listening", "locked", "rephrase"):
            self.level = self._target_level = 0.0
            self.preview = ""
        if state not in ("listening", "locked", "rephrase", "processing"):
            self.preview = ""
        self.state = state
        if state != "hidden":
            self._place()
            if not self.isVisible():
                self.show()
                self._no_activate()
            self.raise_()
        if state == "done":
            self._revert.start(450)
        self._timer.start()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        if self.state in ("idle", "paused", "hidden"):
            self.set_state("paused" if paused else "idle")

    def show_message(self, text: str, error: bool = True, ms: int = 3000) -> None:
        self.message = text
        self.msg_color = QColor("#f08a76") if error else FG
        self.set_state("message")
        self._revert.start(ms)

    def set_preview(self, text: str) -> None:
        if self.state in ("listening", "locked", "rephrase"):
            self.preview = text.strip()
            self._timer.start()

    def set_level(self, rms: float) -> None:
        db = 20 * math.log10(rms + 1e-9)
        self._target_level = max(0.0, min(1.0, (db + 55) / 40))

    # -- internals ---------------------------------------------------------------
    def _place(self) -> None:
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        self.move(g.center().x() - WIN_W // 2, g.bottom() - WIN_H - self.margin + 1)

    def _no_activate(self) -> None:
        if sys.platform != "win32":
            return
        import ctypes
        u = ctypes.windll.user32
        hwnd = int(self.winId())
        u.SetWindowLongW(hwnd, -20, u.GetWindowLongW(hwnd, -20) | 0x08000000 | 0x80 | 0x20)

    def _preview_w(self) -> float:
        if not self.preview:
            return 0.0
        return min(PREVIEW_MAX, self._pfm.horizontalAdvance(self.preview)) + 14

    def _target(self) -> tuple[float, float, float]:
        s = self.state
        dot = 18 if s in ("locked", "rephrase") else 0
        if s in ("listening", "locked", "rephrase"):
            w, h = BARS_W + 34 + dot + self._preview_w(), 34
        elif s == "processing":
            w, h = 64 + self._preview_w(), 34
        elif s == "message":
            w, h = min(WIN_W - 8, self._fm.horizontalAdvance(self.message) + 32), 34
        elif s == "paused":
            w, h = self._fm.horizontalAdvance("Paused") + 26, 22
        elif s in ("idle", "done"):
            w, h = 40, 8
        else:
            w, h = 0, 0
        alpha = {"hidden": 0.0, "idle": 0.6, "done": 0.6, "paused": 0.85}.get(s, 1.0)
        return float(w), float(h), alpha

    def _tick(self) -> None:
        tw, th, ta = self._target()
        k = 0.28
        self.w += (tw - self.w) * k
        self.h += (th - self.h) * k
        self.alpha += (ta - self.alpha) * k
        self.level += (self._target_level - self.level) * 0.35
        self._target_level *= 0.92
        self._flash *= 0.9
        settled = (abs(tw - self.w) < 0.3 and abs(th - self.h) < 0.3 and abs(ta - self.alpha) < 0.01
                   and self._flash < 0.01)
        animating = self.state in ("listening", "locked", "rephrase", "processing")
        if settled and not animating:
            self.w, self.h, self.alpha = tw, th, ta
            self._timer.stop()
            if self.state == "hidden":
                self.hide()
        self.update()

    def paintEvent(self, _):
        if self.alpha < 0.01 or self.w < 1:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self.alpha)
        w, h = self.w, self.h
        rect = QRectF((WIN_W - w) / 2, WIN_H - h - 4, w, h)
        path = QPainterPath()
        path.addRoundedRect(rect, h / 2, h / 2)
        p.fillPath(path, BG)
        border = QColor(GREEN) if self._flash > 0.05 else BORDER
        if self._flash > 0.05:
            border.setAlphaF(min(1.0, self._flash))
        p.setPen(QPen(border, 1.2))
        p.drawPath(path)

        t = time.monotonic() - self._t0
        p.setClipPath(path)
        if self.state in ("listening", "locked", "rephrase") and h > 20:
            self._paint_bars(p, rect, t)
        elif self.state == "processing" and h > 20:
            self._paint_dots(p, rect, t)
        elif self.state == "message" and h > 20:
            p.setPen(self.msg_color)
            p.setFont(self._font)
            p.drawText(rect, Qt.AlignCenter, self.message)
        elif self.state == "paused" and h > 14:
            p.setPen(AMBER)
            p.setFont(self._font)
            p.drawText(rect, Qt.AlignCenter, "Paused")
        p.end()

    def _paint_preview(self, p: QPainter, rect: QRectF, x: float) -> None:
        if not self.preview:
            return
        avail = rect.right() - x - 14
        if avail < 20:
            return
        text = self._pfm.elidedText(self.preview, Qt.ElideLeft, int(avail))
        p.setPen(MUTED)
        p.setFont(self._pfont)
        p.drawText(QRectF(x, rect.top(), avail, rect.height()), Qt.AlignVCenter | Qt.AlignLeft, text)

    def _paint_bars(self, p: QPainter, rect: QRectF, t: float) -> None:
        cy = rect.center().y()
        x = rect.left() + 17
        if self.state in ("locked", "rephrase"):
            r = 3.5 + 0.8 * math.sin(t * 5)
            p.setPen(Qt.NoPen)
            p.setBrush(RED if self.state == "locked" else BLUE)
            p.drawEllipse(QRectF(x + 1 - r, cy - r, 2 * r, 2 * r))
            x += 18
        maxh = rect.height() - 12
        p.setPen(Qt.NoPen)
        for i in range(BARS):
            d = abs(i - (BARS - 1) / 2) / ((BARS - 1) / 2)
            env = 1 - 0.65 * d * d
            wobble = 0.55 + 0.45 * math.sin(t * 9 + i * 0.85)
            bh = 3 + (maxh - 3) * min(1.0, self.level ** 0.7 * 1.15) * env * wobble
            col = QColor(FG)
            col.setAlphaF(0.55 + 0.45 * env)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(x + i * 6, cy - bh / 2, 3, bh), 1.5, 1.5)
        self._paint_preview(p, rect, x + BARS_W + 12)

    def _paint_dots(self, p: QPainter, rect: QRectF, t: float) -> None:
        cy = rect.center().y()
        cx = rect.left() + 32
        p.setPen(Qt.NoPen)
        for i in range(3):
            phase = (math.sin(t * 7 - i * 0.9) + 1) / 2
            col = QColor(FG)
            col.setAlphaF(0.35 + 0.65 * phase)
            p.setBrush(col)
            r = 2.6 + 0.9 * phase
            p.drawEllipse(QRectF(cx - 12 + i * 12 - r, cy - r - 2 * phase, 2 * r, 2 * r))
        self._paint_preview(p, rect, rect.left() + 58)
