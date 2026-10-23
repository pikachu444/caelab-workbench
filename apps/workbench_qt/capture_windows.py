"""Windows evidence capture for the GUI check (WINDOWS_CHECK.md §3).

Run on the Windows PC after installing the GUI packages:

    .venv\\Scripts\\python apps\\workbench_qt\\capture_windows.py

Writes PNG screenshots of the project window and the analysis window
plus a plain-text state dump into ``windows_check_out/`` next to the
repo root, so the result can be sent back and compared against the
Linux Xvfb captures. Note: the VTK 3D area can come out black in a
grab() screenshot even when it renders fine on the real screen —
check that item by eye, not from these files.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from caelab.connections import program_statuses
    from caelab.workbench import Workbench

    from apps.workbench_qt.main_window import MainWindow
    from apps.workbench_qt.project_window import ProjectWindow

    out = REPO_ROOT / "windows_check_out"
    out.mkdir(exist_ok=True)

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    workbench = Workbench()

    project = ProjectWindow(workbench)
    project.resize(1280, 820)
    project.show()

    lines = [
        f"platform: {platform.platform()}",
        f"python: {sys.version.split()[0]}",
        f"screen: {app.primaryScreen().size().width()}x"
        f"{app.primaryScreen().size().height()} "
        f"dpr={app.primaryScreen().devicePixelRatio()}",
        "",
        "programs:",
    ]
    for status in program_statuses():
        lines.append(
            f"  {status.spec.id}: available={status.available} path={status.path}")
    lines.append("")
    lines.append("project boxes:")
    for kind, box in project.boxes.items():
        lines.append(f"  {kind}: state={box.state} detail={box.detail!r}")

    def finish() -> None:
        app.processEvents()
        project.grab().save(str(out / "01_project_window.png"))
        main = MainWindow(workbench)
        main.resize(1440, 900)
        main.show()
        app.processEvents()
        main.grab().save(str(out / "02_main_window.png"))
        lines.append(f"viewport.available: {main.viewport.available}")
        lines.append(f"props width: {main.props_panel.width()}")
        lines.append(f"tree width: {main.tree.width()}")
        (out / "state.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"saved to {out}")
        app.quit()

    QTimer.singleShot(800, finish)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
