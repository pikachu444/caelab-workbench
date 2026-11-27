"""Point-ID linked Pareto projections; dominance comes only from the full study."""
from __future__ import annotations
import numpy as np
from PySide6.QtWidgets import QHBoxLayout, QGridLayout, QComboBox, QLabel, QWidget, QSizePolicy
from .plot_base import PlotWidget, Native3D

NATIVE_SELECTED_COLOR = "#ffffff"
PARETO_LAYERS = (
    ("Actual feasible", "#22c55e", "o", lambda r: getattr(r, "mode", "ACTUAL") != "SURROGATE" and r.feasibility == "FEASIBLE"),
    ("Actual marginal", "#f59e0b", "^", lambda r: getattr(r, "mode", "ACTUAL") != "SURROGATE" and r.feasibility == "MARGINAL"),
    ("Actual infeasible / unassessed", "#ef4444", "x", lambda r: getattr(r, "mode", "ACTUAL") != "SURROGATE" and r.feasibility not in ("FEASIBLE", "MARGINAL")),
    ("Surrogate prediction", "#a78bfa", "D", lambda r: getattr(r, "mode", "ACTUAL") == "SURROGATE"),
)
PARETO_LAYERS_KO = (
    ("실제 실행 (허용)", "#22c55e", "o", lambda r: getattr(r, "mode", "ACTUAL") != "SURROGATE" and r.feasibility == "FEASIBLE"),
    ("실제 실행 (경계)", "#f59e0b", "^", lambda r: getattr(r, "mode", "ACTUAL") != "SURROGATE" and r.feasibility == "MARGINAL"),
    ("제약 위반 / 미평가", "#ef4444", "x", lambda r: getattr(r, "mode", "ACTUAL") != "SURROGATE" and r.feasibility not in ("FEASIBLE", "MARGINAL")),
    ("대리모델 예측", "#a78bfa", "D", lambda r: getattr(r, "mode", "ACTUAL") == "SURROGATE"),
)
PARETO_LEGEND = tuple((label, color) for label, color, _, _ in PARETO_LAYERS) + (
    ("Full-objective feasible front", "#38bdf8"),
    ("Selected", NATIVE_SELECTED_COLOR),
)
PARETO_LEGEND_KO = tuple((label, color) for label, color, _, _ in PARETO_LAYERS_KO) + (
    ("전체 목적 기준 Pareto 프런트", "#38bdf8"),
    ("선택됨", NATIVE_SELECTED_COLOR),
)
PARETO_LEGEND_TEXT = "   ".join(f'<span style="color:{color}">●</span> {name}' for name, color in PARETO_LEGEND)
PARETO_LEGEND_TEXT_KO = "   ".join(f'<span style="color:{color}">●</span> {name}' for name, color in PARETO_LEGEND_KO)

_SENSE_KO = {"MIN": "최소화", "MAX": "최대화"}


def pareto_result_note(rendered, total, *, fallback=False, language: str = "en"):
    if str(language).strip().lower() == "ko":
        return (
            f"표시 설계점 {rendered} / {total}개 렌더링됨. 전체 목적 기준 Pareto 프런트를 유지하며 현재 투영에서 지배 관계를 재계산하지 않습니다."
            + (" 2D 대체 표시 — 네이티브 VTK 미사용." if fallback else "")
        )
    return (
        f"Rendered {rendered} / {total} points. Full-objective front retained; this projection does not recompute dominance."
        + (" 2D fallback; native VTK unavailable." if fallback else "")
    )


class ParetoFrontWidget(PlotWidget):
    def __init__(self, parent=None, is_dark=True, *, language: str = "en"):
        super().__init__(parent, is_dark, language=language)
        row = QGridLayout()
        self.dimension = QComboBox()
        self.dimension.addItems(["2D", "3D"])
        row.addWidget(self.dimension, 0, 0)
        self.axes = []
        self.axis_groups = []
        for i, label in enumerate(("X", "Y", "Z")):
            group = QWidget()
            line = QHBoxLayout(group)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(QLabel(label))
            combo = QComboBox()
            combo.setMinimumContentsLength(4)
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            combo.setMinimumWidth(60)
            line.addWidget(combo, 1)
            self.axes.append(combo)
            self.axis_groups.append(group)
            row.addWidget(group, 0, i + 1) if i < 2 else row.addWidget(group, 1, 1, 1, 2)
            combo.currentIndexChanged.connect(self.refresh)
        self.controls.addLayout(row)
        self.native = Native3D(self, language=self.language)
        self.layout.insertWidget(self.layout.indexOf(self.canvas) + 1, self.native, 1)
        self.native.hide()
        self.native.point_selected.connect(self.point_selected)
        self.dimension.currentIndexChanged.connect(self.refresh)
        self.records = []
        self.objectives = []
        self.units = {}

    def _populate_axis_combos(self) -> None:
        ko = self.language == "ko"
        keys = [o.response_id for o in self.objectives]
        for i, combo in enumerate(self.axes):
            old = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for o in self.objectives:
                sense = _SENSE_KO.get(str(o.sense), str(o.sense)) if ko else str(o.sense)
                combo.addItem(f"{o.response_id} [{self.units.get(o.response_id, '—')}] ({sense})", o.response_id)
            if keys:
                combo.setCurrentIndex(keys.index(old) if old in keys else min(i, len(keys) - 1))
            combo.blockSignals(False)

    def set_theme(self, is_dark: bool):
        super().set_theme(is_dark)
        if getattr(self, "records", None):
            self.refresh()

    def set_language(self, language: str):
        super().set_language(language)
        if hasattr(self, "objectives") and self.objectives:
            self._populate_axis_combos()
        self.refresh()

    def set_study(self, records, objectives, units):
        self.records = list(records)
        self.objectives = list(objectives)
        self.units = dict(units)
        self._populate_axis_combos()
        self.refresh()

    def plot_study(self, records, obj_x_id, obj_y_id, obj_x_name=None, obj_y_name=None):
        from types import SimpleNamespace
        self.set_study(records, [SimpleNamespace(response_id=obj_x_id, sense="MIN"), SimpleNamespace(response_id=obj_y_id, sense="MIN")], {})
        if obj_x_name:
            self.ax.set_xlabel(obj_x_name)
        if obj_y_name:
            self.ax.set_ylabel(obj_y_name)

    def set_selected(self, point_ids):
        updated = set(point_ids)
        changed = updated != self._selected
        super().set_selected(updated)
        if changed and getattr(self, "records", None) and self.dimension.currentText() != "3D":
            self.refresh()

    def refresh(self, *args):
        if not hasattr(self, "records"):
            return
        ko = self.language == "ko"
        use3 = self.dimension.currentText() == "3D"
        self.axis_groups[2].setVisible(use3)
        for combo in self.axes:
            combo.setToolTip(combo.currentText())
        self.set_native_mode(use3 and self.native.available)
        keys = [c.currentData() for c in self.axes[:3 if use3 else 2]]
        if len(set(keys)) != len(keys) or any(k is None for k in keys):
            self.empty("서로 다른 2개 또는 3개의 목적 응답을 선택하세요." if ko else "Choose two / three different objective responses.")
            return
        rows = [r for r in self.records if r.status in ("COMPLETED", "PREDICTED") and all(k in r.observations for k in keys)]
        self.ax.clear()
        self._picks.clear()
        self.native.clear()
        if not rows:
            self.empty("네이티브 해석을 실행하거나 검증된 대리모델 탐색을 수행하세요." if ko else "Run native evaluations or search a validated surrogate.")
            return
        # Keep the entire full-objective front and selected points when decimating.
        keep = [r for r in rows if r.is_pareto or r.point_id in self._selected]
        rest = [r for r in rows if not r.is_pareto and r.point_id not in self._selected]
        stride = max(1, int(np.ceil(len(rest) / 3000)))
        rendered = keep + rest[::stride]
        layers = PARETO_LAYERS_KO if ko else PARETO_LAYERS
        legend_items = PARETO_LEGEND_KO if ko else PARETO_LEGEND
        front_label = "전체 목적 기준 Pareto 프런트" if ko else "Full-objective feasible front"
        selected_label = "선택됨" if ko else "Selected"
        self.native.set_legend(legend_items)
        from matplotlib.colors import to_rgb
        for label, color, marker, predicate in layers:
            group = [r for r in rendered if predicate(r)]
            if not group:
                continue
            values = np.array([[r.observations[k] for k in keys] for r in group])
            artist = self.ax.scatter(values[:, 0], values[:, 1], c=color, marker=marker, s=35, alpha=.7, label=label, picker=6)
            self._picks[artist] = [r.point_id for r in group]
            if use3:
                self.native.points(values, [r.point_id for r in group], to_rgb(color))
        for label, is_sel, group, color, size in [
            (front_label, False, [r for r in rendered if r.is_pareto and getattr(r, "mode", "ACTUAL") != "SURROGATE"], "#38bdf8", 65),
            (selected_label, True, [r for r in rendered if r.point_id in self._selected], "#ffffff" if self.is_dark else "#0f172a", 130),
        ]:
            if group:
                values = np.array([[r.observations[k] for k in keys] for r in group])
                artist = self.ax.scatter(values[:, 0], values[:, 1], facecolors="none", edgecolors=color, s=size, linewidths=2, label=label, picker=6)
                self._picks[artist] = [r.point_id for r in group]
                if use3:
                    self.native.points(values, [r.point_id for r in group], to_rgb(NATIVE_SELECTED_COLOR if is_sel else color), 12)
        labels = [self.axes[i].currentText() for i in range(len(keys))]
        self.ax.set_xlabel(labels[0])
        self.ax.set_ylabel(labels[1])
        self.ax.set_title("표본 Pareto 투영" if ko else "Sampled Pareto projection")
        self.ax.legend(fontsize=8)
        self._apply_theme()
        self.fig.tight_layout()
        self.canvas.draw_idle()
        if use3:
            self.native.finish(labels)
        self.note.setText(pareto_result_note(len(rendered), len(rows), fallback=use3 and not self.native.available, language=self.language))
