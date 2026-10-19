"""Line icons as inline SVG (24x24 grid, 1.6px strokes, round caps), drawn for LocalFlow.

icon(name, color, size) -> QIcon, pixmap(...) -> QPixmap. Colour is applied at render
time so the same icon works for normal / hover / selected states.
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_PATHS = {
    # navigation
    "general": '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M9 21h6"/>',
    "cleanup": '<path d="M4 6h16M4 10h11M4 14h16M4 18h8"/><path d="M17.5 16.5l1.5 1.5 3-3"/>',
    "snippets": '<path d="M7 3h7l4 4v14H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v4h4M9.5 12h5M9.5 15.5h5"/>',
    "commands": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M12.5 15H17"/>',
    "vocabulary": '<path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v15H6.5A1.5 1.5 0 0 0 5 19.5z"/><path d="M5 19.5A1.5 1.5 0 0 0 6.5 21H19v-3M9 7.5h6"/>',
    "punctuation": '<path d="M7 7.5a2 2 0 1 1 2 2c0 2-1 3.5-3 4.5M15 7.5a2 2 0 1 1 2 2c0 2-1 3.5-3 4.5"/><circle cx="12" cy="19" r="0.6" fill="currentColor"/>',
    "apps": '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><path d="M17 13.5v7M13.5 17h7"/>',
    "profiles": '<path d="M12 3l8.5 4.5L12 12 3.5 7.5z"/><path d="M3.5 12L12 16.5 20.5 12M3.5 16.5L12 21l8.5-4.5"/>',
    "history": '<path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3.5 8.5"/><path d="M3.5 4v4.5H8M12 7.5V12l3 2"/>',
    "advanced": '<path d="M14.5 4.5a4 4 0 0 0-5 5L4 15v5h5l5.5-5.5a4 4 0 0 0 5-5l-2.5 2.5-3-.5-.5-3z"/>',
    # controls
    "chevron-down": '<path d="M6 9.5l6 6 6-6"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "minus": '<path d="M6 12h12"/>',
    "plus": '<path d="M12 6v12M6 12h12"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.3-5.7L20 8.5"/><path d="M20 4v4.5h-4.5"/>',
    "keyboard": '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M9.5 10h.01M13 10h.01M16.5 10h.01M7.5 14h9"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7.5a4 4 0 0 1 8 0V11"/>',
}


def _svg(name: str, color: str) -> bytes:
    body = _PATHS[name].replace("currentColor", color)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()


@lru_cache(maxsize=256)
def renderer(name: str, color: str) -> QSvgRenderer:
    return QSvgRenderer(QByteArray(_svg(name, color)))


def paint(p: QPainter, name: str, color: str, rect: QRectF) -> None:
    renderer(name, color).render(p, rect)


@lru_cache(maxsize=256)
def pixmap(name: str, color: str, size: int = 18, dpr: float = 2.0) -> QPixmap:
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    renderer(name, color).render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


def icon(name: str, color: str, size: int = 18) -> QIcon:
    return QIcon(pixmap(name, color, size))
