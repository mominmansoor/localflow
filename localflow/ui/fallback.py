"""The copy box: shown when a dictation had no text box to land in.

The text is already on the clipboard; the box lets you see it and copy it again.
It never takes focus from what you're doing, and fades away on its own (hovering
keeps it open)."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from . import theme as T
from .icons import pixmap
from .widgets import Button

HIDE_AFTER_MS = 12000


class FallbackWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setStyleSheet(T.QSS + f"""
            QFrame#CopyBox {{ background: {T.SURFACE}; border: 1px solid {T.LINE}; border-radius: 12px; }}
            QFrame#CopyBox QLabel {{ background: transparent; }}
            QPlainTextEdit#CopyText {{ background: {T.BG}; border: 1px solid {T.HAIRLINE}; border-radius: 8px;
                                      padding: 6px 8px; color: {T.TEXT}; selection-background-color: {T.SURFACE_3}; }}
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setObjectName("CopyBox")
        outer.addWidget(frame)
        v = QVBoxLayout(frame)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.badge = QLabel()
        self.badge.setPixmap(pixmap("check", T.GREEN, 16))
        top.addWidget(self.badge)
        self.title = QLabel()
        self.title.setObjectName("CardTitle")
        top.addWidget(self.title, 1)
        close = Button("", "ghost", "close")
        close.setFixedSize(28, 28)
        close.setToolTip("Dismiss (Esc)")
        close.clicked.connect(self.dismiss)
        top.addWidget(close)
        v.addLayout(top)

        self.box = QPlainTextEdit()
        self.box.setObjectName("CopyText")
        self.box.setReadOnly(True)
        self.box.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        v.addWidget(self.box)

        bottom = QHBoxLayout()
        hint = QLabel("You weren't in a text box. Click where it should go and press Ctrl+V.")
        hint.setObjectName("Faint")
        hint.setWordWrap(True)
        bottom.addWidget(hint, 1)
        self.copy_btn = Button("Copy", "primary", "copy")
        self.copy_btn.clicked.connect(self._copy)
        bottom.addWidget(self.copy_btn)
        v.addLayout(bottom)

        self.setFixedWidth(440)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._then = None
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(lambda: self._then and self._then())

    # -- showing ---------------------------------------------------------------
    def show_text(self, text: str) -> None:
        text = text.rstrip()
        QGuiApplication.clipboard().setText(text)
        self.title.setText("Copied to clipboard")
        self.copy_btn.setText("Copy")
        self.box.setPlainText(text)
        line_h = self.box.fontMetrics().lineSpacing()
        n = self._wrapped_lines(text)
        self.box.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded if n > 6 else Qt.ScrollBarAlwaysOff)
        self.box.setFixedHeight(int(min(6, n) * line_h + 26))
        self.adjustSize()
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        self.move(g.center().x() - self.width() // 2, g.bottom() - self.height() - 96)   # just above the pill
        self._anim.stop()
        if not self.isVisible():
            self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._fade(1.0, 160)
        self._timer.start(HIDE_AFTER_MS)

    def _wrapped_lines(self, text: str) -> int:
        fm = self.box.fontMetrics()
        width = max(1, 440 - 28 - 34)       # frame margins, box padding and border
        return sum(max(1, -(-fm.horizontalAdvance(line) // width)) for line in text.split("\n"))

    def dismiss(self) -> None:
        self._timer.stop()
        if self.isVisible():
            self._fade(0.0, 200, then=self.hide)

    def _fade(self, to: float, ms: int, then=None) -> None:
        self._anim.stop()
        self._then = then
        self._anim.setDuration(ms)
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(to)
        self._anim.start()

    # -- actions ---------------------------------------------------------------
    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.box.toPlainText())
        self.title.setText("Copied again - paste it with Ctrl+V")
        self.copy_btn.setText("Copied")
        self._timer.start(1800)

    def enterEvent(self, e):
        self._timer.stop()                 # reading it: stay
        if self._anim.endValue() == 0.0:
            self._fade(1.0, 120)
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self.isVisible():
            self._timer.start(4000)
        super().leaveEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.dismiss()
        else:
            super().keyPressEvent(e)
