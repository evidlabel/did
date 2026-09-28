"""Entry point for the gdid GUI."""

import sys

from .display import NO_DISPLAY_MESSAGE, configure_display


def main():
    # Before Qt loads: a missing display must be a message, not an abort.
    if not configure_display():
        print(NO_DISPLAY_MESSAGE, file=sys.stderr)
        sys.exit(1)

    from PySide6.QtWidgets import QApplication

    from .gui.theme import apply_theme
    from .gui.window import MainWindow

    # No model check here: browsing needs none, and the first detection in a
    # language downloads the model it needs (did.core.model_install).
    app = QApplication(sys.argv)
    apply_theme(app)
    window = MainWindow()
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
