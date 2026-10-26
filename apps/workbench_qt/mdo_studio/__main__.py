"""Entry point for standalone CAELab MDO Studio."""

import sys
from PySide6.QtWidgets import QApplication
from apps.workbench_qt.mdo_studio.window import MDOStudioWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = MDOStudioWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
