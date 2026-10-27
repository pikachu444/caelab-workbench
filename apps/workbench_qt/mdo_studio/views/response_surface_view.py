"""Response surface slice with Kriging predictive variance (±1.96σ model uncertainty band)."""

from __future__ import annotations

from typing import Sequence, Mapping
import numpy as np
from PySide6.QtWidgets import QWidget, QVBoxLayout
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from caelab.mdo.surrogate import SurrogateArtifact, predict
from caelab.mdo.study_manager import DesignPointRecord


class ResponseSurfaceWidget(QWidget):
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
        legend = self.ax.get_legend()
        if legend is not None:
            legend.get_frame().set_facecolor(bg_color)
            legend.get_frame().set_edgecolor(grid_color)
            for label in legend.get_texts():
                label.set_color(text_color)

    def plot_kriging_slice(
        self,
        surrogate: SurrogateArtifact | None,
        records: Sequence[DesignPointRecord],
        var_id: str,
        resp_id: str,
        lower: float,
        upper: float,
        fixed_values: Mapping[str, float] | None = None,
    ) -> None:
        self.ax.clear()
        self._apply_theme()

        # Actual sample dots
        pts = [r for r in records if r.status == "COMPLETED" and var_id in r.physical_values and resp_id in r.observations]
        if pts:
            ax_vals = [r.physical_values[var_id] for r in pts]
            ay_vals = [r.observations[resp_id] for r in pts]
            self.ax.scatter(ax_vals, ay_vals, color="#f59e0b", marker="o", s=45, label="FE samples (all other-variable settings)", zorder=4)

        if surrogate is not None:
            # Predict grid along var_id
            grid_x = np.linspace(lower, upper, 100)
            fixed = dict(fixed_values or {})

            # Multi-dimensional feature layout
            feat_order = surrogate.dataset.feature_order
            cols = []
            for f in feat_order:
                if f == var_id:
                    cols.append(grid_x)
                else:
                    cols.append(np.full(len(grid_x), fixed[f]))
            X_pred = np.column_stack(cols)

            try:
                preds = predict(surrogate, X_pred)
                mean = preds.mean[:, 0]
                var = preds.variance[:, 0] if preds.variance is not None else np.zeros_like(mean)
                std = np.sqrt(np.maximum(0.0, var))

                # Kriging Mean Curve
                self.ax.plot(grid_x, mean, color="#38bdf8", linewidth=2.2, label="Kriging Mean Prediction ŷ(x)")
                # ±1.96σ Uncertainty Band
                self.ax.fill_between(
                    grid_x,
                    mean - 1.96 * std,
                    mean + 1.96 * std,
                    color="#0284c7",
                    alpha=0.25,
                    label="GP ±1.96σ (model uncertainty)",
                )
            except Exception:
                pass
        else:
            self.ax.text(0.5, 0.5, "Surrogate model not fitted yet", ha="center", va="center",
                         color="#94a3b8", transform=self.ax.transAxes)

        self.ax.set_xlabel(f"Design Variable: {var_id}", fontsize=9.5)
        self.ax.set_ylabel(f"Predicted Response: {resp_id}", fontsize=9.5)
        self.ax.set_title("Kriging 1D Slice (other variables fixed at initial values)", fontsize=11, fontweight="bold")
        legend = self.ax.legend(loc="best", framealpha=0.8, fontsize=8.5)
        if legend:
            legend.get_frame().set_facecolor(self.ax.get_facecolor())
            legend.get_frame().set_edgecolor("#1e293b" if self.is_dark else "#e2e8f0")
            for text in legend.get_texts():
                text.set_color("#e2e8f0" if self.is_dark else "#0f172a")

        self.fig.tight_layout()
        self.canvas.draw_idle()
