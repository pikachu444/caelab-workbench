"""Purpose-drawn SVG icon set for the workbench, with Dark/Light tinting.

Mature CAE programs ship a dedicated icon per tool and object type;
Qt's stock icons read as placeholders. These icons follow one visual
language and adapt stroke/fill contrast cleanly to Dark and Light modes.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon, QPixmap

from .theme import current_theme_mode

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

_DARK_SVG_REMAP = (
    ("#33414f", "#cbd5e1"),
    ("#10243a", "#e2e8f0"),
    ("#dcebf7", "#1e3a5f"),
    ("#bcd6ef", "#274c77"),
    ("#7fb2e5", "#60a5fa"),
    ("#1668b4", "#3b82f6"),
    ("#8a97a5", "#94a3b8"),
)

_CACHE: dict[tuple[str, str], QIcon] = {}


def _load_svg_icon(path: Path, mode: str) -> QIcon:
    if mode != "dark":
        return QIcon(str(path))
    try:
        svg_text = path.read_text(encoding="utf-8")
    except OSError:
        return QIcon(str(path))
    for src, dst in _DARK_SVG_REMAP:
        svg_text = svg_text.replace(src, dst).replace(src.upper(), dst)
    pixmap = QPixmap()
    if pixmap.loadFromData(svg_text.encode("utf-8"), "SVG") and not pixmap.isNull():
        return QIcon(pixmap)
    return QIcon(str(path))


def icon(name: str, mode: str | None = None) -> QIcon:
    resolved = mode if mode in ("dark", "light") else current_theme_mode()
    key = (name, resolved)
    cached = _CACHE.get(key)
    if cached is not None and not cached.isNull():
        return cached
    path = _DIR / f"{name}.svg"
    if not path.exists():
        # A mistyped name must not leave a blank gap in the tree or the
        # toolbox: fall back to the folder icon so the spot stays visible.
        path = _DIR / "folder.svg"
        if not path.exists():
            return QIcon()
    built = _load_svg_icon(path, resolved)
    _CACHE[key] = built
    return built
