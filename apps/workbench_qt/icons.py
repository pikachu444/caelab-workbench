"""Purpose-drawn SVG icon set for the workbench.

Mature CAE programs ship a dedicated icon per tool and object type;
Qt's stock icons read as placeholders. These icons follow one visual
language (dark slate strokes, workbench blue accent, semantic colours
for loads/results) and are loaded from the package, no resources file.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon

_DIR = Path(__file__).parent / "icons"

GROUP_ICONS = {
    "재료·단면": "material",
    "메시": "mesh",
    "해석 단계": "step",
    "하중·구속": "force",
    "접촉·연결": "contact",
    "작업": "solver",
    "결과": "result",
}
LEAF_ICONS = {
    "materials": "material",
    "sections": "section",
    "mesh": "mesh",
    "steps": "step",
    "loads": "force",
    "bcs": "fixed",
    "interactions": "contact",
    "job": "solver",
    "result": "result",
    "model": "geometry",
}


def icon(name: str) -> QIcon:
    path = _DIR / f"{name}.svg"
    if path.exists():
        return QIcon(str(path))
    # A mistyped name must not leave a blank gap in the tree or the
    # toolbox: fall back to the folder icon so the spot stays visible.
    fallback = _DIR / "folder.svg"
    return QIcon(str(fallback)) if fallback.exists() else QIcon()
