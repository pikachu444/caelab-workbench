"""Interactive 2D Pareto Front visualization with Knee Point recommendation."""

from __future__ import annotations

from typing import Sequence
import numpy as np
from PySide6.QtWidgets import QWidget, QVBoxLayout
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from caelab.mdo.study_manager import DesignPointRecord
from caelab.mdo.pareto import pareto_front, knee_point


class ParetoFrontWidget(QWidget):
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

    def plot_study(
        self,
        records: Sequence[DesignPointRecord],
        obj_x_id: str,
        obj_y_id: str,
        obj_x_name: str | None = None,
        obj_y_name: str | None = None,
    ) -> None:
        self.ax.clear()
        self._apply_theme()

        completed = [r for r in records if r.status == "COMPLETED" and obj_x_id in r.observations and obj_y_id in r.observations]
        if not completed:
            self.ax.text(0.5, 0.5, "No evaluated points available", ha="center", va="center",
                         color="#94a3b8", transform=self.ax.transAxes)
            self.canvas.draw_idle()
            return

        x_name = obj_x_name or obj_x_id
        y_name = obj_y_name or obj_y_id

        feasible = [r for r in completed if r.feasibility == "FEASIBLE"]
        infeasible = [r for r in completed if r.feasibility in ("MARGINAL", "INFEASIBLE")]

        # Infeasible points (muted crosses)
        if infeasible:
            ix = [r.observations[obj_x_id] for r in infeasible]
            iy = [r.observations[obj_y_id] for r in infeasible]
            self.ax.scatter(ix, iy, c="#ef4444", alpha=0.4, marker="x", s=35, label="Infeasible / Marginal")

        # Feasible points
        if feasible:
            fx = [r.observations[obj_x_id] for r in feasible]
            fy = [r.observations[obj_y_id] for r in feasible]
            self.ax.scatter(fx, fy, c="#22c55e", alpha=0.7, marker="o", s=40, label="Feasible Points")

            # Extract Pareto Front
            vecs = np.array([[r.observations[obj_x_id], r.observations[obj_y_id]] for r in feasible])
            p_ids = [r.point_id for r in feasible]
            p_indices = np.array([i for i, r in enumerate(feasible) if r.is_pareto], dtype=int)

            if len(p_indices) > 0:
                # Sort along front for smooth line
                p_vecs = vecs[list(p_indices)]
                order = np.argsort(p_vecs[:, 0])
                sorted_front = p_vecs[order]

                self.ax.plot(sorted_front[:, 0], sorted_front[:, 1], color="#38bdf8", linestyle="-", linewidth=2.2, label="Pareto Frontier")
                self.ax.scatter(sorted_front[:, 0], sorted_front[:, 1], c="#38bdf8", edgecolors="white", s=70, zorder=4)


        self.ax.set_xlabel(x_name, fontsize=9.5)
        self.ax.set_ylabel(y_name, fontsize=9.5)
        self.ax.set_title("Evaluated Design Points & Sampled Pareto Front", fontsize=11, fontweight="bold")
        legend = self.ax.legend(loc="upper right", framealpha=0.8, fontsize=8.5)
        if legend:
            for text in legend.get_texts():
                text.set_color("#e2e8f0" if self.is_dark else "#0f172a")

        self.fig.tight_layout()
        self.canvas.draw_idle()
