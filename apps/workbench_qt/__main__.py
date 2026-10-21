import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from caelab.workbench import Workbench
from .main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow(Workbench())
    if len(sys.argv) > 1:
        window.set_model(window.workbench.open_project(sys.argv[1]), Path(sys.argv[1]))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
