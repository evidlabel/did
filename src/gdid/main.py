"""Entry point for the gdid GUI."""

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from did.core.anonymizer import MODELS_INSTALL_HINT, missing_spacy_models

from .gui.theme import apply_theme
from .gui.window import MainWindow


def main():
    app = QApplication(sys.argv)
    apply_theme(app)
    missing = missing_spacy_models()
    if missing:
        QMessageBox.critical(
            None,
            "Missing language models",
            "DID needs spaCy models to detect entities:\n\n"
            + "\n".join(f"• {name}" for name in missing)
            + f"\n\nInstall them with:\n{MODELS_INSTALL_HINT}",
        )
        return 1
    window = MainWindow()
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
