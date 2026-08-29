"""Theme helpers — adapted from evid.gui.theme for visual consistency.

`apply_theme(widget)` auto-detects the OS colour scheme and applies a dark or
light QPalette + stylesheet. `token_colors()` returns the per-entity highlight
colours for the current scheme, used by the Typst preview highlighter.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QWidget

# Per-entity-prefix highlight colours (dark scheme, light scheme).
# Keys are the token prefixes did emits: P person, E email, A address/location,
# PH phone, DT date, ID id, CD code, GN general number, URL url.
_TOKEN_COLORS_DARK = {
    "P": "#6ab0f3",
    "O": "#ff9f6e",
    "E": "#5ed1c4",
    "A": "#e6c07b",
    "PH": "#c98bda",
    "DT": "#c98bda",
    "ID": "#e06c75",
    "CD": "#e06c75",
    "GN": "#e06c75",
    "URL": "#98c379",
    "DOC": "#8a93a5",
}
_TOKEN_COLORS_LIGHT = {
    "P": "#0050b3",
    "O": "#a33a00",
    "E": "#0a7d72",
    "A": "#8a6d00",
    "PH": "#7a2da0",
    "DT": "#7a2da0",
    "ID": "#b3261e",
    "CD": "#b3261e",
    "GN": "#b3261e",
    "URL": "#3a7d1e",
    # Titles are structure, not content — muted so they recede behind the
    # identifiers a reviewer is actually scanning for.
    "DOC": "#5a6472",
}


def is_dark_mode() -> bool:
    try:
        from PySide6.QtGui import QGuiApplication

        if hasattr(QGuiApplication.styleHints(), "colorScheme"):
            return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        pass
    return False


def token_colors() -> dict:
    """Highlight colours keyed by token prefix for the active colour scheme."""
    return _TOKEN_COLORS_DARK if is_dark_mode() else _TOKEN_COLORS_LIGHT


def apply_theme(widget: QWidget) -> None:
    if is_dark_mode():
        set_dark_theme(widget)
    else:
        set_light_theme(widget)


def set_dark_theme(widget: QWidget) -> None:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#2d2d2d"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#2d2d2d"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#333333"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#dcdcdc"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#dcdcdc"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#dcdcdc"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#4a4a4a"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#dcdcdc"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#0078d4"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#dcdcdc"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#888888"))
    palette.setColor(QPalette.ColorRole.Light, QColor("#555555"))
    palette.setColor(QPalette.ColorRole.Mid, QColor("#444444"))
    palette.setColor(QPalette.ColorRole.Dark, QColor("#333333"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2d2d2d"))
    widget.setPalette(palette)
    widget.setStyleSheet("""
        QToolTip { background-color: #35363a; color: #f0f0f0; border: 1px solid #555;
            padding: 5px 7px; }
        QComboBox, QLineEdit, QTextEdit, QPlainTextEdit, QTableWidget {
            background-color: #292a2d; color: #e4e4e4; border: 1px solid #484a4f;
            border-radius: 4px; }
        QTableWidget { alternate-background-color: #303136; }
        QComboBox { padding: 4px 8px; }
        QComboBox::drop-down { width: 18px; }
        QComboBox QAbstractItemView {
            background-color: #303136; color: #e4e4e4; border: 1px solid #51545a;
            selection-background-color: #1565a8; }
        QMenu { background-color: #303136; color: #ededed; border: 1px solid #51545a;
            padding: 5px; }
        QMenu::item { padding: 7px 28px 7px 10px; border-radius: 4px; }
        QMenu::item:selected { background-color: #1565a8; color: white; }
        QMenu::item:disabled { color: #777a80; }
        QMenu::separator { height: 1px; background: #4b4d52; margin: 5px 8px; }
        QMenuBar::item { padding: 5px 9px; border-radius: 4px; }
        QMenuBar { background-color: #2d2d2d; color: #ededed; }
        QMenuBar::item:selected { background-color: #3c3e43; }
        QTabWidget::pane { background-color: #2d2d2d; border: 1px solid #555; }
        QTabBar::tab {
            background-color: #3a3a3a; color: #cfcfcf; border: 1px solid #555;
            padding: 7px 12px; }
        QTabBar::tab:selected { background-color: #1565a8; color: #fff; }
        QHeaderView::section {
            background-color: #3a3a3a; color: #dcdcdc; border: 0;
            border-right: 1px solid #555; border-bottom: 1px solid #555;
            padding: 7px 6px; font-weight: 600; }
        QTableCornerButton::section {
            background-color: #3a3a3a; border: 1px solid #555; }
        QLineEdit { min-height: 28px; padding: 0 10px; font-size: 13px; }
        QToolBar { border: none; border-bottom: 1px solid #3c3e43;
            spacing: 7px; padding: 6px; }
        QPushButton, QToolButton {
            background-color: #3b3d42; color: #e8e8e8; border: 1px solid #51545a;
            padding: 6px 12px; border-radius: 5px; }
        QPushButton:hover, QToolButton:hover { background-color: #1565a8; border-color: #2580c5; }
        QPushButton:disabled, QToolButton:disabled { color: #777; background-color: #3a3a3a; }
        QStatusBar { color: #aaa; }
        QLabel { color: #dcdcdc; }
        QAbstractItemView { background-color: #292a2d; color: #dedede; }
        QListWidget, QTreeWidget { background-color: #292a2d; color: #dedede;
            border: 1px solid #484a4f; border-radius: 4px; outline: 0; }
        QTreeWidget::item, QTableWidget::item { padding: 4px 5px; }
        QTreeWidget::item:selected, QTableWidget::item:selected {
            background-color: #1565a8; color: white; }
        QSplitter::handle { background-color: #444; }
        QScrollBar:vertical, QScrollBar:horizontal {
            background: #292a2d; border: 0; margin: 0; }
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
            background: #55585e; border-radius: 4px; min-width: 24px; min-height: 24px; }
        QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
        QScrollBar::add-page, QScrollBar::sub-page { background: #292a2d; }
    """)


def set_light_theme(widget: QWidget) -> None:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#f0f0f0"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f7f7f7"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#000000"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#000000"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#e0e0e0"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#000000"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#0078d4"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    widget.setPalette(palette)
    widget.setStyleSheet("""
        QToolTip { padding: 5px 7px; }
        QLineEdit { min-height: 28px; padding: 0 10px; font-size: 13px;
            border: 1px solid #c7c9ce; border-radius: 4px; }
        QComboBox { padding: 4px 8px; border: 1px solid #c7c9ce; border-radius: 4px; }
        QToolBar { border: none; border-bottom: 1px solid #d8d9dc;
            spacing: 7px; padding: 6px; }
        QPushButton, QToolButton { padding: 6px 12px; border: 1px solid #c3c5ca;
            border-radius: 5px; background: #f7f7f8; }
        QPushButton:hover, QToolButton:hover { background-color: #e4f1fb; color: #064f86;
            border-color: #75add4; }
        QMenu { background: white; border: 1px solid #c7c9ce; padding: 5px; }
        QMenu::item { padding: 7px 28px 7px 10px; border-radius: 4px; }
        QMenu::item:selected { background: #dceefb; color: #064f86; }
        QMenu::separator { height: 1px; background: #dedfe2; margin: 5px 8px; }
        QMenuBar::item { padding: 5px 9px; border-radius: 4px; }
        QTreeWidget, QTableWidget { border: 1px solid #c7c9ce; border-radius: 4px;
            outline: 0; }
        QTableWidget { alternate-background-color: #f3f4f6; }
        QTreeWidget::item, QTableWidget::item { padding: 4px 5px; }
    """)
