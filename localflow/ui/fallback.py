"""Shown when nothing has text focus: the dictation, pre-selected and already copied."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from .theme import QSS


class FallbackWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setStyleSheet(QSS + "#Fallback { background: #16161a; border: 1px solid #2c2c33; border-radius: 12px; }")
        self.setAttribute(Qt.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QWidget()
        frame.setObjectName("Fallback")
        outer.addWidget(frame)
        v = QVBoxLayout(frame)
        v.setContentsMargins(16, 12, 16, 14)
        top = QHBoxLayout()
        title = QLabel("No text field focused - copied to clipboard")
        title.setObjectName("Muted")
        title.setStyleSheet("background: transparent;")
        top.addWidget(title, 1)
        close = QPushButton("✕")
        close.setObjectName("Ghost")
        close.clicked.connect(self.hide)
        top.addWidget(close)
        v.addLayout(top)
        self.box = QPlainTextEdit()
        self.box.setMinimumHeight(90)
        v.addWidget(self.box)
        btns = QHBoxLayout()
        btns.addStretch(1)
        copy = QPushButton("Copy")
        copy.setObjectName("Primary")
        copy.clicked.connect(self._copy)
        btns.addWidget(copy)
        v.addLayout(btns)
        self.resize(460, 210)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_text(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.box.setPlainText(text)
        self.box.selectAll()
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        self.move(g.center().x() - self.width() // 2, g.bottom() - self.height() - 90)
        self.show()
        self.raise_()
        self.activateWindow()
        self.box.setFocus()
        self._hide_timer.start(30000)

    def _copy(self):
        QGuiApplication.clipboard().setText(self.box.toPlainText())
        self.hide()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)
