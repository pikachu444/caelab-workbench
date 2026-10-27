"""Entry point for standalone CAELab MDO Studio."""

import sys
import argparse
from pathlib import Path
from PySide6.QtWidgets import QApplication,QMessageBox
from apps.workbench_qt.mdo_studio.window import MDOStudioWindow


def main() -> int:
    parser=argparse.ArgumentParser(description='Independent native-input MDO Studio')
    parser.add_argument('--study',type=Path)
    parser.add_argument('--project',type=Path)
    parser.add_argument('--node')
    parser.add_argument('--read-only',action='store_true')
    options=parser.parse_args()
    app = QApplication(sys.argv)
    project=None
    if options.project:
        from caelab.project import load_native_project
        project=load_native_project(options.project)
    try:
        window = MDOStudioWindow(study_path=options.study,project=project,node_id=options.node,read_only=options.read_only)
    except (ValueError,OSError,KeyError) as exc:
        QMessageBox.warning(None,'Open MDO Studio',str(exc))
        return 2
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
