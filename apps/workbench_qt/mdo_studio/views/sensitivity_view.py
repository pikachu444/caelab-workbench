"""Global Sensitivity Tornado Ranking chart."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence
import numpy as np
from PySide6.QtWidgets import QWidget, QVBoxLayout
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


@dataclass(frozen=True, slots=True)
class TornadoItem:
    variable_id: str
    metric: str
    magnitude: float
    interval: tuple[float, float] | None = None
    sign: int = 1


class SensitivityTornadoWidget(QWidget):
    def __init__(self, parent=None, is_dark: bool = True) -> None:
        super().__init__(parent)
        self.is_dark = is_dark
        self.fig = Figure(figsize=(6, 4), dpi=100)
        self.canvas = FigureCanvas(self.fig)
        self.ax = self.fig.add_subplot(111)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)
        self._apply_theme()

    def set_theme(self, is_dark: bool) -> None:
        self.is_dark = is_dark
        self._apply_theme()
        self.canvas.draw_idle()

    def _apply_theme(self) -> None:
        if self.is_dark:
            bg_color = "#13171e"
            text_color = "#e2e8f0"
            grid_color = "#1e293b"
        else:
            bg_color = "#f8fafc"
            text_color = "#0f172a"
            grid_color = "#e2e8f0"

        self.fig.patch.set_facecolor(bg_color)
        self.ax.set_facecolor(bg_color)
        self.ax.tick_params(colors=text_color, labelsize=9)
        self.ax.xaxis.label.set_color(text_color)
        self.ax.yaxis.label.set_color(text_color)
        self.ax.title.set_color(text_color)
        for spine in self.ax.spines.values():
            spine.set_color(grid_color)
        self.ax.grid(True, linestyle="--", alpha=0.5, color=grid_color)

    def plot_tornado(self, items: Sequence[TornadoItem], target_name: str = "Response") -> None:
        self.ax.clear()
        self._apply_theme()

        if not items:
            self.ax.text(0.5, 0.5, "No sensitivity items computed", ha="center", va="center",
                         color="#94a3b8", transform=self.ax.transAxes)
            self.canvas.draw_idle()
            return

        # Sort ascending for horizontal bar (largest at top)
        sorted_items = sorted(items, key=lambda x: x.magnitude)
        names = [it.variable_id for it in sorted_items]
        magnitudes = [it.magnitude for it in sorted_items]
        y_pos = np.arange(len(names))

        # Color gradient: highest impact gets vivid cyan, lower gets subtle steel blue
        colors = ["#38bdf8" if i == len(names) - 1 else "#0284c7" if i >= len(names) // 2 else "#64748b"
                  for i in range(len(names))]

        bars = self.ax.barh(y_pos, magnitudes, align="center", color=colors, height=0.55, edgecolor="none")
        self.ax.set_yticks(y_pos)
        self.ax.set_yticklabels(names, fontsize=9.5)

        # Annotate percentages
        for bar in bars:
            width = bar.get_width()
            self.ax.text(
                width + 0.015,
                bar.get_y() + bar.get_height() / 2,
                f"{width * 100:.1f}%",
                va="center",
                ha="left",
                fontsize=8.5,
                color="#e2e8f0" if self.is_dark else "#0f172a",
                fontweight="bold",
            )

        self.ax.set_xlabel("Sensitivity Impact / Relative Contribution", fontsize=9.5)
        self.ax.set_title(f"Global Sensitivity Tornado Diagram ({target_name})", fontsize=11, fontweight="bold")
        self.ax.set_xlim(0, max(magnitudes) * 1.25 if magnitudes else 1.0)
        self.fig.tight_layout()
        self.canvas.draw_idle()
