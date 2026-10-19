"""Design tokens + stylesheet.

Neutral graphite, one off-white accent. No hue for decoration: colour only
carries meaning (green = good, amber = attention, red = destructive/error).
Hierarchy comes from type weight, spacing and three surface levels.
"""

# Surfaces (darkest -> lightest)
BG = "#121212"          # page
SIDEBAR = "#0e0e0e"     # navigation
SURFACE = "#1a1a1a"     # grouped rows
SURFACE_2 = "#222222"   # inputs, hover
SURFACE_3 = "#2a2a2a"   # pressed, selected
LINE = "#262626"        # borders
HAIRLINE = "#202020"    # separators inside groups

# Text
TEXT = "#ededed"
TEXT_2 = "#a3a3a3"
TEXT_3 = "#6f6f6f"

# The one accent: off-white. Used for "on", primary actions and focus.
ACCENT = "#ededed"
ON_ACCENT = "#121212"

# Meaning-only colours, softened so they sit in the palette
GREEN = "#5bb98c"
AMBER = "#e0a84b"
RED = "#e5674f"
BLUE = "#7aa7e8"

FONT = '"Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif'
FONT_DISPLAY = '"Segoe UI Variable Display", "Segoe UI", "Inter Display", "Inter", sans-serif'
MONO = '"Cascadia Mono", "Consolas", "JetBrains Mono", monospace'

QSS = f"""
* {{ font-family: {FONT}; font-size: 10pt; }}
QWidget {{ background: {BG}; color: {TEXT}; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {LINE}; padding: 5px 8px; border-radius: 6px; }}

/* ---- navigation ---- */
#SideWrap {{ background: {SIDEBAR}; border-right: 1px solid {HAIRLINE}; }}
#SideWrap QWidget {{ background: transparent; }}
#Brand {{ font-family: {FONT_DISPLAY}; font-size: 12.5pt; font-weight: 600; color: {TEXT}; }}
#BrandSub {{ color: {TEXT_3}; font-size: 8.5pt; }}
#NavSection {{ color: {TEXT_3}; font-size: 8pt; font-weight: 600; letter-spacing: 0.6px; padding: 14px 0 4px 12px; }}
#SideFooter {{ color: {TEXT_3}; font-size: 8.5pt; }}
#Saved {{ color: {TEXT_2}; font-size: 8.5pt; }}

/* ---- page ---- */
QLabel#H1 {{ font-family: {FONT_DISPLAY}; font-size: 18pt; font-weight: 600; background: transparent; }}
QLabel#Sub {{ color: {TEXT_2}; background: transparent; }}
QLabel#Muted {{ color: {TEXT_2}; font-size: 9pt; background: transparent; }}
QLabel#Faint {{ color: {TEXT_3}; font-size: 9pt; background: transparent; }}
QLabel#Warn {{ color: {AMBER}; font-size: 9pt; background: transparent; }}
QLabel#RowTitle {{ background: transparent; }}
QLabel#GroupTitle {{ color: {TEXT_2}; font-size: 8.5pt; font-weight: 600; letter-spacing: 0.5px; background: transparent; }}
QLabel#GroupSub {{ color: {TEXT_3}; font-size: 9pt; background: transparent; }}
QLabel#CardTitle {{ font-weight: 600; color: {TEXT}; background: transparent; }}
QFrame#Group {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px; }}
QFrame#Group QWidget {{ background: transparent; }}
QFrame#Divider {{ background: {HAIRLINE}; max-height: 1px; min-height: 1px; border: none; }}

/* ---- inputs ---- */
QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {{
  background: {SURFACE_2}; border: 1px solid {LINE}; border-radius: 7px; padding: 6px 10px;
  selection-background-color: #3a3a3a; selection-color: {TEXT}; }}
QFrame#Group QLineEdit, QFrame#Group QComboBox, QFrame#Group QPlainTextEdit {{ background: {SURFACE_2}; }}
QLineEdit:hover, QComboBox:hover, QPlainTextEdit:hover {{ border-color: #333333; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: #5a5a5a; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {TEXT_3}; }}
QComboBox {{ padding-right: 30px; }}
QComboBox::drop-down {{ border: none; width: 0px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid #333333; border-radius: 8px; padding: 4px;
  outline: 0; selection-background-color: {SURFACE_3}; selection-color: {TEXT}; }}
QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 0 8px; border-radius: 5px; }}
QComboBox QAbstractItemView::item:hover {{ background: {SURFACE_3}; }}
QCheckBox {{ background: transparent; spacing: 8px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 4px; border: 1px solid #444; background: {SURFACE_2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

/* ---- plain buttons (dialogs); app buttons are painted by widgets.Button ---- */
QPushButton {{ background: {SURFACE_2}; border: 1px solid {LINE}; border-radius: 7px; padding: 6px 14px; color: {TEXT}; }}
QPushButton:hover {{ background: {SURFACE_3}; }}
QPushButton:default, QPushButton#Primary {{ background: {ACCENT}; color: {ON_ACCENT}; border-color: {ACCENT}; font-weight: 600; }}

/* ---- tables & lists ---- */
QTableWidget, QListWidget {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px;
  gridline-color: transparent; outline: 0; alternate-background-color: {SURFACE}; }}
QTableWidget::item, QListWidget::item {{ padding: 6px 10px; border: none; border-bottom: 1px solid {HAIRLINE}; }}
QTableWidget::item:hover, QListWidget::item:hover {{ background: #1e1e1e; }}
QTableWidget::item:selected, QListWidget::item:selected {{ background: {SURFACE_3}; color: {TEXT}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {SURFACE}; color: {TEXT_3}; border: none; border-bottom: 1px solid {LINE};
  padding: 8px 10px; font-weight: 600; font-size: 8.5pt; }}
QTableCornerButton::section {{ background: {SURFACE}; border: none; }}

/* ---- scrolling ---- */
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: #2c2c2c; border-radius: 3px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: #3a3a3a; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
QScrollBar:horizontal {{ height: 0; }}

/* ---- dialogs & menus ---- */
QDialog, QMessageBox, QInputDialog {{ background: {BG}; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid #333333; padding: 5px; border-radius: 8px; }}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
QMenu::item:disabled {{ color: {TEXT_3}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 6px; }}
QMenu::indicator {{ width: 12px; height: 12px; margin-left: 4px; }}
"""
