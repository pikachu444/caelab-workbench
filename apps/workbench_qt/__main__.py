"""Launch the workbench.

The entry point is the project window (the ANSYS Workbench-style
launcher/schematic). Per-system workspaces, such as the structural
analysis window, open from their schematic boxes.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from caelab.workbench import Workbench

from .project_window import ProjectWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = ProjectWindow(Workbench())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
