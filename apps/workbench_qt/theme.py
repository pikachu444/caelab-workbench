"""Shared Dark/Light engineering theme tokens and Qt stylesheet builder.

Default mode is 'dark' (Engineering Dark Slate), persisted in QSettings
('CAELAB', 'WorkbenchUI') so all CAELAB windows share a consistent look
and can switch between Dark and Light modes immediately.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import weakref
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSettings


def _arrow_svg_url(direction: str, color: str) -> str:
    """Return a forward-slash filesystem path to a cached chevron SVG for QSS url(...)."""
    cache_dir = Path(tempfile.gettempdir()) / "caelab-ui-theme-assets"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.md5(f"{direction}:{color}".encode("utf-8")).hexdigest()[:10]
        svg_path = cache_dir / f"arrow-{direction}-{digest}.svg"
        if not svg_path.exists():
            pts = "2,6 5,3 8,6" if direction == "up" else "2,3 5,6 8,3"
            svg = (
                '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="9" viewBox="0 0 10 9">'
                f'<polyline points="{pts}" fill="none" stroke="{color}" '
                'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>'
                "</svg>"
            )
            svg_path.write_text(svg, encoding="utf-8")
        return svg_path.as_posix()
    except OSError:
        return ""

UI_FONT_STACK = '"Segoe UI", "Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif'
MONO_FONT_STACK = '"Cascadia Mono", "Consolas", "D2Coding", "Malgun Gothic", monospace'

DARK_PALETTE: dict[str, object] = {
    "mode": "dark",
    # Base surfaces
    "window_bg": "#13171e",
    "canvas_bg": "#0f131a",
    "canvas_grid_minor": "#1d2532",
    "canvas_grid_major": "#283446",
    "surface_bg": "#191f29",
    "panel_bg": "#1d2430",
    "card_bg": "#232c3a",
    "card_alt_bg": "#171d26",
    "header_bg": "#283344",
    "input_bg": "#131821",
    "code_bg": "#10151d",
    # Borders & interactive accents
    "border": "#313d4f",
    "border_subtle": "#252f3e",
    "border_strong": "#495970",
    "accent": "#3b82f6",
    "accent_hover": "#60a5fa",
    # Primary action button keeps deep engineering blue vs lighter slate disabled
    # so enabled vs waiting/disabled state is high-contrast in both themes.
    "primary_btn_bg": "#1668b4",
    "primary_btn_hover": "#1d76c9",
    "primary_btn_fg": "#ffffff",
    "primary_btn_disabled_bg": "#94a3b8",
    "primary_btn_disabled_border": "#7c8ba1",
    "primary_btn_disabled_fg": "#1e293b",
    "selection_bg": "#1d3554",
    "selection_border": "#60a5fa",
    "hover_bg": "#263244",
    # Typography
    "text_primary": "#e6edf5",
    "text_secondary": "#a9b7c9",
    "text_muted": "#7b8ba1",
    "text_accent": "#60a5fa",
    # Semantic statuses
    "status_ready_fg": "#4ade80",
    "status_ready_bg": "#132e21",
    "status_ready_border": "#22643d",
    "status_running_fg": "#60a5fa",
    "status_running_bg": "#152a47",
    "status_running_border": "#2563eb",
    "status_warning_fg": "#fbbf24",
    "status_warning_bg": "#36260f",
    "status_warning_border": "#9a5b13",
    "status_error_fg": "#f87171",
    "status_error_bg": "#3a181c",
    "status_error_border": "#991b1b",
    "status_neutral_fg": "#94a3b8",
    "status_neutral_bg": "#222b38",
    "status_neutral_border": "#384659",
    # Schematic SystemBox & Connector
    "box_bg": "#1e2633",
    "box_selected_bg": "#21334d",
    "box_header_bg": "#283345",
    "box_selected_header_bg": "#1e406b",
    "box_border": "#3d4d63",
    "box_selected_border": "#60a5fa",
    "connector": "#6d8db0",
    "connector_hover": "#60a5fa",
    "port_in": "#38bdf8",
    "port_out": "#4ade80",
    "stale_stripe": "#523914",
    # Code / deck viewer & syntax highlighting
    "gutter_bg": "#151b24",
    "gutter_fg": "#64748b",
    "syn_keyword": "#60a5fa",
    "syn_section": "#c084fc",
    "syn_param": "#38bdf8",
    "syn_comment": "#6b7c93",
    "syn_number": "#fbbf24",
    "syn_error": "#f87171",
    "syn_warning": "#fbbf24",
    # Expert discussion HTML cards
    "chat_root_bg": "#141922",
    "chat_user_bg": "#1e2b3c",
    "chat_user_border": "#2e4666",
    "chat_assistant_bg": "#1b222d",
    "chat_proposal_bg": "#1f2735",
    "chat_proposal_border": "#3b5275",
    "chat_before_bg": "#2d1d22",
    "chat_after_bg": "#162d24",
    "chat_citation_bg": "#1c3352",
    "chat_citation_fg": "#93c5fd",
    "chat_table_header_bg": "#253041",
    # Auxiliary 3D viewport & optimization table
    # Note: best_row_bg keeps green > 200 in both themes (test_optimization_panel.py:84).
    "vtk_bg_bottom": (0.08, 0.10, 0.14),
    "vtk_bg_top": (0.15, 0.19, 0.25),
    "vtk_text_rgb": (0.88, 0.91, 0.95),
    "vtk_edge_rgb": (0.58, 0.66, 0.78),
    "best_row_bg": "#22d37e",
    "best_row_fg": "#0b1f15",
}

LIGHT_PALETTE: dict[str, object] = {
    "mode": "light",
    # Base surfaces
    "window_bg": "#eef1f5",
    "canvas_bg": "#f4f6f9",
    "canvas_grid_minor": "#e1e7ef",
    "canvas_grid_major": "#cfd8e3",
    "surface_bg": "#f7f9fc",
    "panel_bg": "#ffffff",
    "card_bg": "#ffffff",
    "card_alt_bg": "#f2f5f9",
    "header_bg": "#e7edf4",
    "input_bg": "#ffffff",
    "code_bg": "#f6f8fb",
    # Borders & interactive accents
    "border": "#cbd5e1",
    "border_subtle": "#e2e8f0",
    "border_strong": "#94a3b8",
    "accent": "#1668b4",
    "accent_hover": "#1c7ad1",
    "primary_btn_bg": "#1668b4",
    "primary_btn_hover": "#1c7ad1",
    "primary_btn_fg": "#ffffff",
    "primary_btn_disabled_bg": "#dde3ea",
    "primary_btn_disabled_border": "#cfd7e0",
    "primary_btn_disabled_fg": "#5d6b78",
    "selection_bg": "#dcebf7",
    "selection_border": "#1668b4",
    "hover_bg": "#e6f0fa",
    # Typography
    "text_primary": "#102033",
    "text_secondary": "#425265",
    "text_muted": "#64748b",
    "text_accent": "#1668b4",
    # Semantic statuses
    "status_ready_fg": "#156f3d",
    "status_ready_bg": "#e6f6ed",
    "status_ready_border": "#86efac",
    "status_running_fg": "#155799",
    "status_running_bg": "#e3effb",
    "status_running_border": "#93c5fd",
    "status_warning_fg": "#8a460d",
    "status_warning_bg": "#fef3c7",
    "status_warning_border": "#f59e0b",
    "status_error_fg": "#991b1b",
    "status_error_bg": "#fee2e2",
    "status_error_border": "#ef4444",
    "status_neutral_fg": "#475569",
    "status_neutral_bg": "#eaeff5",
    "status_neutral_border": "#cbd5e1",
    # Schematic SystemBox & Connector
    "box_bg": "#ffffff",
    "box_selected_bg": "#e6f1fb",
    "box_header_bg": "#e8eef5",
    "box_selected_header_bg": "#d2e5f8",
    "box_border": "#8a99a8",
    "box_selected_border": "#1668b4",
    "connector": "#557494",
    "connector_hover": "#1668b4",
    "port_in": "#0284c7",
    "port_out": "#16a34a",
    "stale_stripe": "#fde68a",
    # Code / deck viewer & syntax highlighting
    "gutter_bg": "#edf1f6",
    "gutter_fg": "#64748b",
    "syn_keyword": "#1668b4",
    "syn_section": "#6d28d9",
    "syn_param": "#0369a1",
    "syn_comment": "#64748b",
    "syn_number": "#b45309",
    "syn_error": "#dc2626",
    "syn_warning": "#d97706",
    # Expert discussion HTML cards
    "chat_root_bg": "#f6f8fa",
    "chat_user_bg": "#e8f0f8",
    "chat_user_border": "#bfd5ec",
    "chat_assistant_bg": "#ffffff",
    "chat_proposal_bg": "#f2f6fb",
    "chat_proposal_border": "#9ec0e6",
    "chat_before_bg": "#fef2f2",
    "chat_after_bg": "#ecfdf5",
    "chat_citation_bg": "#e0eefb",
    "chat_citation_fg": "#155799",
    "chat_table_header_bg": "#e6ecf3",
    # Auxiliary 3D viewport & optimization table
    "vtk_bg_bottom": (0.87, 0.90, 0.93),
    "vtk_bg_top": (0.97, 0.98, 0.99),
    "vtk_text_rgb": (0.14, 0.19, 0.25),
    "vtk_edge_rgb": (0.15, 0.20, 0.28),
    "best_row_bg": "#dcfce7",
    "best_row_fg": "#102033",
}

_LISTENERS: list[object] = []


def _settings() -> QSettings:
    override = os.environ.get("CAELAB_UI_SETTINGS_PATH", "").strip()
    if override:
        return QSettings(override, QSettings.IniFormat)
    return QSettings(QSettings.defaultFormat(), QSettings.UserScope, "CAELAB", "WorkbenchUI")


def current_theme_mode() -> str:
    try:
        raw = str(_settings().value("theme/mode", "dark") or "dark").strip().lower()
    except Exception:
        return "dark"
    return raw if raw in ("dark", "light") else "dark"


def get_palette(mode: str | None = None) -> dict[str, object]:
    resolved = mode if mode in ("dark", "light") else current_theme_mode()
    return DARK_PALETTE if resolved == "dark" else LIGHT_PALETTE


def set_theme_mode(mode: str) -> str:
    resolved = "light" if str(mode).strip().lower() == "light" else "dark"
    try:
        settings = _settings()
        settings.setValue("theme/mode", resolved)
        settings.sync()
    except Exception:
        pass
    _notify_listeners(resolved)
    return resolved


def toggle_theme_mode() -> str:
    next_mode = "light" if current_theme_mode() == "dark" else "dark"
    return set_theme_mode(next_mode)


def theme_toggle_label(mode: str | None = None) -> str:
    resolved = mode if mode in ("dark", "light") else current_theme_mode()
    return "라이트 모드" if resolved == "dark" else "다크 모드"


def register_theme_listener(callback: Callable[[str], None]) -> None:
    ref: object
    if hasattr(callback, "__self__") and callback.__self__ is not None:
        ref = weakref.WeakMethod(callback)
    else:
        ref = weakref.ref(callback)
    _LISTENERS.append(ref)


def _notify_listeners(mode: str) -> None:
    alive: list[object] = []
    for ref in list(_LISTENERS):
        target = ref() if callable(ref) else None
        if target is None:
            continue
        alive.append(ref)
        try:
            target(mode)
        except RuntimeError:
            # Underlying C++ QObject was deleted.
            alive.pop()
    _LISTENERS[:] = alive


def status_badge_style(tone: str, mode: str | None = None) -> str:
    p = get_palette(mode)
    key = tone if tone in ("ready", "running", "warning", "error", "neutral") else "neutral"
    fg = p[f"status_{key}_fg"]
    bg = p[f"status_{key}_bg"]
    border = p[f"status_{key}_border"]
    return (
        f"color: {fg}; background: {bg}; border: 1px solid {border}; "
        f"border-radius: 10px; padding: 2px 9px; font-weight: 600;"
    )


def build_stylesheet(mode: str | None = None) -> str:
    p = get_palette(mode)
    up_arrow = _arrow_svg_url("up", str(p["text_secondary"]))
    down_arrow = _arrow_svg_url("down", str(p["text_secondary"]))
    return f"""
QWidget {{
    color: {p['text_primary']};
    font-family: {UI_FONT_STACK};
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background: {p['window_bg']};
}}
QMenuBar {{
    background: {p['surface_bg']};
    color: {p['text_primary']};
    border-bottom: 1px solid {p['border_subtle']};
    padding: 2px 6px;
}}
QMenuBar::item {{
    padding: 4px 10px;
    border-radius: 4px;
    background: transparent;
}}
QMenuBar::item:selected {{
    background: {p['hover_bg']};
}}
QMenu {{
    background: {p['panel_bg']};
    color: {p['text_primary']};
    border: 1px solid {p['border']};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 22px 6px 12px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background: {p['selection_bg']};
    color: {p['text_primary']};
}}
QMenu::item:disabled {{
    color: {p['text_muted']};
}}
QToolBar {{
    background: {p['surface_bg']};
    border-bottom: 1px solid {p['border']};
    padding: 4px 6px;
    spacing: 4px;
}}
QToolBar::separator {{
    background: {p['border']};
    width: 1px;
    margin: 4px 4px;
}}
QToolButton {{
    color: {p['text_primary']};
    background: transparent;
    padding: 5px 9px;
    border: 1px solid transparent;
    border-radius: 5px;
}}
QToolButton:hover {{
    background: {p['hover_bg']};
    border-color: {p['border']};
}}
QToolButton:disabled {{
    color: {p['text_muted']};
}}
QToolButton:checked {{
    background: {p['primary_btn_bg']};
    color: {p['primary_btn_fg']};
    font-weight: 600;
}}
QDockWidget {{
    color: {p['text_primary']};
    border: 1px solid {p['border']};
}}
QDockWidget::title {{
    background: {p['header_bg']};
    color: {p['text_primary']};
    padding: 6px 10px;
    font-weight: 600;
    border-bottom: 1px solid {p['border']};
}}
QTreeWidget, QListWidget, QTableWidget, QTableView {{
    background: {p['panel_bg']};
    alternate-background-color: {p['card_alt_bg']};
    color: {p['text_primary']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    gridline-color: {p['border_subtle']};
    outline: 0;
}}
QTreeWidget::item, QListWidget::item {{
    padding: 5px 6px;
    border-radius: 4px;
}}
QTreeWidget::item:hover, QListWidget::item:hover {{
    background: {p['hover_bg']};
}}
QTreeWidget::item:selected, QListWidget::item:selected, QTableWidget::item:selected, QTableView::item:selected {{
    background: {p['selection_bg']};
    color: {p['text_primary']};
}}
QHeaderView::section {{
    background: {p['header_bg']};
    color: {p['text_primary']};
    border: none;
    border-right: 1px solid {p['border']};
    border-bottom: 1px solid {p['border']};
    padding: 5px 8px;
    font-weight: 600;
}}
QTabWidget::pane {{
    border: 1px solid {p['border']};
    background: {p['panel_bg']};
    border-radius: 6px;
    top: -1px;
}}
QTabBar::tab {{
    background: {p['surface_bg']};
    color: {p['text_secondary']};
    border: 1px solid {p['border']};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 6px 14px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background: {p['panel_bg']};
    color: {p['text_primary']};
    font-weight: 600;
}}
QTabBar::tab:hover:!selected {{
    background: {p['hover_bg']};
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QFrame#sectionCard, QWidget#sectionCard {{
    background: {p['card_bg']};
    border: 1px solid {p['border']};
    border-radius: 8px;
}}
QGroupBox {{
    background: {p['card_bg']};
    color: {p['text_primary']};
    border: 1px solid {p['border']};
    border-radius: 7px;
    margin-top: 10px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    color: {p['text_primary']};
    background: {p['card_bg']};
}}
QFrame#kpiCard, QWidget#kpiCard {{
    background: {p['card_bg']};
    border: 1px solid {p['border']};
    border-radius: 7px;
}}
QWidget#propsPanel, QWidget#selectionSettings {{
    background: {p['surface_bg']};
}}
QWidget#canvasControls {{
    background: {p['card_bg']};
    border: 1px solid {p['border']};
    border-radius: 7px;
}}
QLabel {{
    background: transparent;
}}
QLabel#panelHint, QLabel#mutedLabel {{
    color: {p['text_secondary']};
}}
QLabel#cardTitle {{
    color: {p['text_primary']};
    font-weight: 600;
    font-size: 13px;
}}
QPushButton {{
    background: {p['card_bg']};
    color: {p['text_primary']};
    border: 1px solid {p['border_strong']};
    border-radius: 5px;
    padding: 5px 12px;
}}
QPushButton:hover {{
    background: {p['hover_bg']};
    border-color: {p['accent']};
}}
QPushButton:disabled {{
    color: {p['text_muted']};
    background: {p['surface_bg']};
    border-color: {p['border_subtle']};
}}
QPushButton#primary, QToolButton#primary {{
    background: {p['primary_btn_bg']};
    border: 1px solid {p['primary_btn_bg']};
    color: {p['primary_btn_fg']};
    font-weight: 600;
    border-radius: 5px;
    padding: 5px 16px;
}}
QPushButton#primary:hover, QToolButton#primary:hover {{
    background: {p['primary_btn_hover']};
    border-color: {p['primary_btn_hover']};
}}
QPushButton#primary:disabled, QPushButton#primary:disabled:hover {{
    background: {p['primary_btn_disabled_bg']};
    border-color: {p['primary_btn_disabled_border']};
    color: {p['primary_btn_disabled_fg']};
}}
QPushButton:focus, QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1.5px solid {p['accent']};
}}
QComboBox, QLineEdit {{
    background: {p['input_bg']};
    color: {p['text_primary']};
    border: 1px solid {p['border_strong']};
    border-radius: 5px;
    padding: 4px 8px;
}}
QSpinBox, QDoubleSpinBox {{
    background: {p['input_bg']};
    color: {p['text_primary']};
    border: 1px solid {p['border_strong']};
    border-radius: 5px;
    padding: 4px 24px 4px 8px;
    min-height: 20px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 19px;
    background: {p['header_bg']};
    border-left: 1px solid {p['border']};
    border-bottom: 1px solid {p['border']};
    border-top-right-radius: 4px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 19px;
    background: {p['header_bg']};
    border-left: 1px solid {p['border']};
    border-bottom-right-radius: 4px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {p['hover_bg']};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url("{up_arrow}");
    width: 9px;
    height: 7px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QComboBox::down-arrow {{
    image: url("{down_arrow}");
    width: 9px;
    height: 7px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    border-left: 1px solid {p['border']};
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: {p['panel_bg']};
    color: {p['text_primary']};
    border: 1px solid {p['border']};
    selection-background-color: {p['selection_bg']};
    selection-color: {p['text_primary']};
}}
QPlainTextEdit, QTextEdit {{
    background: {p['code_bg']};
    color: {p['text_primary']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    selection-background-color: {p['selection_bg']};
    selection-color: {p['text_primary']};
}}
QTextEdit#logView, QPlainTextEdit#monoView {{
    background: {p['code_bg']};
    color: {p['text_primary']};
    border: none;
    font-family: {MONO_FONT_STACK};
    font-size: 12px;
}}
QLabel.chip {{
    background: {p['status_neutral_bg']};
    color: {p['status_neutral_fg']};
    border: 1px solid {p['status_neutral_border']};
    border-radius: 10px;
    padding: 3px 10px;
}}
QLabel.chip[active="true"] {{
    background: {p['primary_btn_bg']};
    border-color: {p['primary_btn_bg']};
    color: {p['primary_btn_fg']};
}}
QSplitter::handle {{
    background: {p['border_subtle']};
}}
QSplitter::handle:vertical {{
    height: 5px;
}}
QSplitter::handle:horizontal {{
    width: 5px;
}}
QStatusBar {{
    background: {p['surface_bg']};
    color: {p['text_secondary']};
    border-top: 1px solid {p['border']};
}}
QToolTip {{
    background: {p['card_bg']};
    color: {p['text_primary']};
    border: 1px solid {p['border_strong']};
    padding: 5px 8px;
}}
"""


# Backwards-compatible default constant for callers importing THEME directly.
THEME = build_stylesheet("dark")
