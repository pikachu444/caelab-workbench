"""Launch the workbench.

The entry point is the project window (the ANSYS Workbench-style
launcher/schematic). Per-system workspaces, such as the structural
analysis window, open from their schematic boxes.
"""

from __future__ import annotations

import sys
import argparse

from PySide6.QtWidgets import QApplication

from caelab.workbench import Workbench

from .project_window import ProjectWindow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CAELAB 네이티브 프로젝트 열기")
    parser.add_argument("project", nargs="?", help="저장한 프로젝트 JSON 파일")
    args = parser.parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    window = ProjectWindow(Workbench(), project_path=args.project)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
