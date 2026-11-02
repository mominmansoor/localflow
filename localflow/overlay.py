"""The pill at the bottom of the screen.

Frameless, always-on-top, click-through and never focused, so text still lands in
the app you're dictating into. Anti-aliased QPainter drawing; the animation timer
only runs while something moves (~0% CPU idle).

States: hidden, idle, listening, locked (hands-free, red dot), rephrase (violet
dot), processing, done, message, paused, copy. Optional live-preview text sits
next to the waveform.

"copy": when a dictation had no text box to land in, the pill grows into a small
card showing what you said (capped at COPY_LINES lines, "..." after that; the full
text is on the clipboard) with a Copy button. Only in that state does the window
take clicks - and even then it never takes focus from your app.
"""
from __future__ import annotations

import math
import sys
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

WIN_W, WIN_H = 520, 200
COPY_W = 400
COPY_LINES = 4            # strict cap: longer dictations end in "..." (the clipboard has all of it)
COPY_HIDE_MS = 12000
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
SURFACE = QColor(34, 34, 34)
FAINT = QColor(237, 237, 237, 105)
ON_ACCENT = QColor(18, 18, 18)


def wrap_lines(text: str, fm: QFontMetrics, width: int, max_lines: int) -> list[str]:
    """Word-wrap to at most max_lines; the last line ends in "..." if anything was cut."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    i = 0
    while i < len(words):
        w = words[i]
        trial = f"{cur} {w}" if cur else w
        if fm.horizontalAdvance(trial) <= width:
            cur, i = trial, i + 1
            continue
        if not cur:                                  # one word wider than the line: hard-cut it
            cur, i = fm.elidedText(w, Qt.ElideRight, width), i + 1
        lines.append(cur)
        cur = ""
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
        cur = ""
    if i < len(words) and lines:            # cut short: finish the last line with "..."
        lines[-1] = fm.elidedText(lines[-1] + " " + " ".join(words[i:]), Qt.ElideRight, width)
    return lines


class Overlay(QWidget):
    ctrl_c = Signal()          # emitted from the keyboard hook thread; queued to the Qt thread

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

        # copy card
        self.copy_text = ""
        self._copy_lines: list[str] = []
        self._copied_again = False
        self._hover = None                   # "copy" | "close" | "card" | None
        self._clickable = False
        self._copy_btn = self._close_btn = QRectF()
        self._tfont = QFont("Segoe UI", 10)
        self._tfm = QFontMetrics(self._tfont)
        self._sfont = QFont("Segoe UI", 8)
        self._sfm = QFontMetrics(self._sfont)
        self._copy_timer = QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.timeout.connect(self.close_copy)
        self.ctrl_c.connect(self._copy)
        self.setMouseTracking(True)

    # -- public API (Qt thread) -------------------------------------------------
    def set_state(self, state: str) -> None:
        if self.state == "copy":
            if state in ("done", "idle", "paused", "message"):
                return                       # the card stays until dismissed or a new dictation starts
            self._leave_copy()
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

    def show_copy(self, text: str) -> None:
        """Grow into the copy card. The text is put on the clipboard right away."""
        self.copy_text = text.strip()
        QGuiApplication.clipboard().setText(self.copy_text)
        self._copy_lines = wrap_lines(self.copy_text, self._tfm, COPY_W - 32, COPY_LINES)
        self._copied_again = False
        self._hover = None
        self.set_state("copy")
        self._set_clickable(True)
        self._copy_timer.start(COPY_HIDE_MS)

    @property
    def copy_open(self) -> bool:
        return self.state == "copy"

    def close_copy(self) -> None:
        if self.state == "copy":
            self._leave_copy()
            self.state = "copy-closing"
            self.set_state("idle")

    def _leave_copy(self) -> None:
        self._copy_timer.stop()
        self._set_clickable(False)
        self._hover = None
        self.unsetCursor()

    def _copy(self) -> None:
        if self.state != "copy":
            return
        QGuiApplication.clipboard().setText(self.copy_text)
        self._copied_again = True
        self.update()
        self._copy_timer.start(1100)

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
        """WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW, plus WS_EX_TRANSPARENT (clicks pass
        through) except while the copy card is open."""
        if sys.platform != "win32":
            return
        import ctypes
        u = ctypes.windll.user32
        hwnd = int(self.winId())
        style = u.GetWindowLongW(hwnd, -20) | 0x08000000 | 0x80
        style = style & ~0x20 if self._clickable else style | 0x20
        u.SetWindowLongW(hwnd, -20, style)

    def _set_clickable(self, on: bool) -> None:
        self._clickable = on
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not on)
        if self.isVisible():
            self._no_activate()

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
        elif s == "copy":
            w = COPY_W
            h = 44 + len(self._copy_lines) * self._tfm.lineSpacing() + 46
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
        if self.state == "copy" and settled:
            self.w, self.h, self.alpha = tw, th, ta
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
        radius = min(h / 2, 16.0)            # pill -> rounded card as it grows
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
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
        elif self.state == "copy":
            tw, th, _ = self._target()
            grow = max(0.0, min(1.0, (min(w / tw, h / th) - 0.86) / 0.13))  # content fades in as it finishes opening
            if grow > 0.01:
                p.setOpacity(self.alpha * grow)
                self._paint_copy(p, QRectF(rect.center().x() - tw / 2, rect.bottom() - th, tw, th))
        p.end()

    def _paint_copy(self, p: QPainter, r: QRectF) -> None:
        pad = 16
        # header: status + close
        p.setPen(Qt.NoPen)
        p.setBrush(GREEN)
        p.drawEllipse(QRectF(r.left() + pad, r.top() + 19, 7, 7))
        p.setFont(self._font)
        p.setPen(FG)
        title = "Copied again" if self._copied_again else "Copied to clipboard"
        p.drawText(QRectF(r.left() + pad + 15, r.top() + 10, 220, 24), Qt.AlignVCenter | Qt.AlignLeft, title)
        self._close_btn = QRectF(r.right() - pad - 22, r.top() + 11, 22, 22)
        if self._hover == "close":
            p.setPen(Qt.NoPen)
            p.setBrush(SURFACE)
            p.drawRoundedRect(self._close_btn, 6, 6)
        p.setPen(QPen(MUTED if self._hover != "close" else FG, 1.4, Qt.SolidLine, Qt.RoundCap))
        c, d = self._close_btn.center(), 4.0
        p.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
        p.drawLine(QPointF(c.x() + d, c.y() - d), QPointF(c.x() - d, c.y() + d))
        # the text
        p.setFont(self._tfont)
        p.setPen(FG)
        y = r.top() + 42
        ls = self._tfm.lineSpacing()
        for line in self._copy_lines:
            p.drawText(QRectF(r.left() + pad, y, r.width() - 2 * pad, ls), Qt.AlignVCenter | Qt.AlignLeft, line)
            y += ls
        # footer: hint + Copy button
        by = r.bottom() - 40
        p.setFont(self._sfont)
        p.setPen(FAINT)
        p.drawText(QRectF(r.left() + pad, by, 250, 28), Qt.AlignVCenter | Qt.AlignLeft,
                   "Not in a text box \u00b7 Ctrl+C copies it again")
        label = "Copied" if self._copied_again else "Copy"
        bw = self._fm.horizontalAdvance(label) + 30
        self._copy_btn = QRectF(r.right() - pad - bw, by, bw, 28)
        btn = QColor(FG) if self._hover != "copy" else QColor(255, 255, 255)
        p.setPen(Qt.NoPen)
        p.setBrush(GREEN if self._copied_again else btn)
        p.drawRoundedRect(self._copy_btn, 8, 8)
        p.setPen(ON_ACCENT)
        p.setFont(self._font)
        p.drawText(self._copy_btn, Qt.AlignCenter, label)

    # -- mouse (copy card only) ------------------------------------------------
    def _hit(self, pos) -> str | None:
        if self.state != "copy":
            return None
        if self._copy_btn.contains(pos):
            return "copy"
        if self._close_btn.contains(pos):
            return "close"
        tw, th, _ = self._target()
        card = QRectF((WIN_W - tw) / 2, WIN_H - th - 4, tw, th)
        return "card" if card.contains(pos) else None

    def mouseMoveEvent(self, e):
        hit = self._hit(e.position())
        if hit != self._hover:
            self._hover = hit
            self.setCursor(Qt.PointingHandCursor if hit in ("copy", "close") else Qt.ArrowCursor)
            if hit:
                self._copy_timer.stop()            # reading it: stay open
            elif self.state == "copy":
                self._copy_timer.start(4000)
            self.update()

    def leaveEvent(self, e):
        if self.state == "copy" and self._hover:
            self._hover = None
            self._copy_timer.start(4000)
            self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        hit = self._hit(e.position())
        if hit == "copy":
            self._copy()
        elif hit == "close":
            self.close_copy()

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
