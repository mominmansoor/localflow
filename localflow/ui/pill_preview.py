"""A live, looping demo of the real pill, embedded in Settings.

It's the actual Overlay painter running as a child widget, driven by a little
script: idle -> listening (with fake voice + live preview text) -> processing ->
done -> idle. Toggling "show idle pill" or "live preview" changes it instantly.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..overlay import WIN_H, WIN_W, Overlay
from . import theme as T

DEMO = "Hey team, quick update on the launch plan for Friday".split()


class _Pill(Overlay):
    def __init__(self, parent):
        super().__init__(True, 0)
        self.setParent(parent)
        self.setWindowFlags(Qt.Widget)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def _place(self):          # stays where the layout puts it
        pass

    def _no_activate(self):
        pass

    def hide(self):            # "hidden" just paints nothing; keep the slot
        self.update()


class PillPreview(QWidget):
    def __init__(self, show_idle: bool, live_preview: bool, parent=None):
        super().__init__(parent)
        self.setFixedHeight(WIN_H + 34)
        self.setMinimumWidth(360)
        self.pill = _Pill(self)
        self.pill.show_idle = show_idle
        self.live_preview = live_preview
        self.phase = ""
        self.t_phase = time.monotonic()
        self.words = 0
        self.caption = ""
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._step)
        self._go("idle")

    def set_show_idle(self, on: bool):
        self.pill.show_idle = on
        if self.phase == "idle":
            self.pill.set_state("idle")

    def set_live_preview(self, on: bool):
        self.live_preview = on
        if not on:
            self.pill.preview = ""
            self.pill.update()

    def showEvent(self, e):
        super().showEvent(e)
        self._timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.pill.move((self.width() - WIN_W) // 2, 6)

    def _go(self, phase):
        self.phase, self.t_phase = phase, time.monotonic()
        self.caption = {"idle": "Idle" if self.pill.show_idle else "Idle (hidden)",
                        "listening": "Holding Ctrl+Win", "processing": "Transcribing", "done": "Pasted"}[phase]
        if phase == "listening":
            self.words = 0
        self.pill.set_state(phase if phase != "processing" else "processing")
        self.update()

    def _step(self):
        dt = time.monotonic() - self.t_phase
        if self.phase == "idle" and dt > 1.8:
            self._go("listening")
        elif self.phase == "listening":
            # fake voice: syllable-ish bursts
            v = 0.30 + 0.45 * abs(math.sin(dt * 5.3)) * (0.6 + 0.4 * math.sin(dt * 1.7))
            self.pill._target_level = max(self.pill._target_level, v)
            if self.live_preview:
                n = min(len(DEMO), int(dt / 0.32))
                if n != self.words:
                    self.words = n
                    self.pill.set_preview(" ".join(DEMO[:n]))
            if dt > 3.8:
                self._go("processing")
        elif self.phase == "processing" and dt > 0.7:
            self._go("done")
        elif self.phase == "done" and dt > 0.9:
            self._go("idle")

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # a soft "desktop" strip so the dark pill reads as floating
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QPen(QColor(T.LINE), 1))
        p.setBrush(QColor("#161616"))
        p.drawRoundedRect(r, 9, 9)
        p.setPen(QColor(T.TEXT_3))
        f = p.font()
        f.setPointSizeF(8.5)
        p.setFont(f)
        p.drawText(QRectF(12, self.height() - 24, self.width() - 24, 18), Qt.AlignLeft | Qt.AlignVCenter, self.caption)
        p.drawText(QRectF(12, self.height() - 24, self.width() - 24, 18), Qt.AlignRight | Qt.AlignVCenter, "Preview")
        p.end()
