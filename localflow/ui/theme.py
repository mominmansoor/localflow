"""Dark theme shared by the settings app and dialogs (matches the pill)."""

ACCENT = "#7c6cff"

QSS = f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif; font-size: 10pt; }}
QWidget {{ background: #0f0f11; color: #ececf0; }}
QToolTip {{ background: #1d1d22; color: #ececf0; border: 1px solid #33333b; padding: 4px 6px; }}

#SideWrap {{ background: #131316; border-right: 1px solid #222228; }}
#Sidebar {{ background: #131316; border: none; outline: 0; padding-top: 6px; }}
#Sidebar::item {{ padding: 9px 14px; border-radius: 8px; margin: 1px 10px; color: #a9a9b3; }}
#Sidebar::item:hover {{ background: #1b1b20; color: #ececf0; }}
#Sidebar::item:selected {{ background: #24242c; color: #ffffff; }}
#Brand {{ background: #131316; font-size: 13pt; font-weight: 600; padding: 18px 22px 10px 22px; }}
#BrandSub {{ background: #131316; color: #77777f; font-size: 8.5pt; padding: 0 22px 12px 22px; }}
#SideFooter {{ background: #131316; color: #77777f; font-size: 8.5pt; padding: 12px 22px; }}

QLabel#H1 {{ font-size: 17pt; font-weight: 600; background: transparent; }}
QLabel#Sub {{ color: #8e8e99; background: transparent; }}
QLabel#Muted {{ color: #8e8e99; font-size: 9pt; background: transparent; }}
QLabel#Warn {{ color: #ffc44d; font-size: 9pt; background: transparent; }}
QLabel#RowTitle {{ background: transparent; }}
QFrame#Card {{ background: #16161a; border: 1px solid #24242a; border-radius: 12px; }}
QFrame#Card QWidget {{ background: transparent; }}
QLabel#CardTitle {{ font-weight: 600; font-size: 10.5pt; color: #ffffff; }}
QFrame#Divider {{ background: #222228; max-height: 1px; min-height: 1px; border: none; }}

QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
  background: #1d1d22; border: 1px solid #2c2c33; border-radius: 8px; padding: 6px 9px;
  selection-background-color: {ACCENT}; selection-color: white; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit#HotkeyEdit {{ font-family: "Cascadia Mono", "Consolas", monospace; }}
QLineEdit#HotkeyEdit[recording="true"] {{ border: 1px solid {ACCENT}; color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: #1d1d22; border: 1px solid #2c2c33; selection-background-color: #2c2c38; outline: 0; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}

QPushButton {{ background: #23232a; border: 1px solid #31313a; border-radius: 8px; padding: 7px 14px; }}
QPushButton:hover {{ background: #2b2b34; }}
QPushButton:pressed {{ background: #1c1c22; }}
QPushButton:disabled {{ color: #66666e; }}
QPushButton#Primary {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; font-weight: 600; }}
QPushButton#Primary:hover {{ background: #8b7dff; }}
QPushButton#Danger {{ color: #ff7a7a; }}
QPushButton#Ghost {{ background: transparent; border: none; color: #a9a9b3; padding: 4px 8px; }}
QPushButton#Ghost:hover {{ color: #ffffff; }}
QPushButton#Choice {{ text-align: left; padding: 10px 12px; background: #1a1a1f; border: 1px solid #2a2a31; border-radius: 10px; }}
QPushButton#Choice:checked {{ border: 1px solid {ACCENT}; background: #1f1d30; }}
QPushButton#Chip {{ padding: 3px 10px; border-radius: 11px; font-size: 9pt; }}

QTableWidget, QListWidget {{ background: #141417; border: 1px solid #24242a; border-radius: 10px;
  gridline-color: transparent; outline: 0; alternate-background-color: #17171b; }}
QTableWidget::item, QListWidget::item {{ padding: 6px 8px; border: none; }}
QTableWidget::item:selected, QListWidget::item:selected {{ background: #27273a; color: white; }}
QHeaderView::section {{ background: #141417; color: #8e8e99; border: none; border-bottom: 1px solid #24242a;
  padding: 7px 8px; font-weight: 600; font-size: 9pt; }}
QTableCornerButton::section {{ background: #141417; border: none; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2c2c33; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3a3a44; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ height: 0; }}

QDialog {{ background: #121215; }}
QMenu {{ background: #1a1a1f; border: 1px solid #2c2c33; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: #2a2a34; }}
QMenu::item:disabled {{ color: #77777f; }}
QMenu::separator {{ height: 1px; background: #2a2a31; margin: 4px 6px; }}
QMenu::indicator {{ width: 12px; height: 12px; margin-left: 4px; }}
QMessageBox {{ background: #121215; }}
"""
