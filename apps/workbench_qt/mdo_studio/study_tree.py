"""Study artifact navigation; selecting a node never applies or evaluates."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QWidget, QVBoxLayout

_VIEW_ITEMS = (
    ("Pareto", "Pareto 프런트"),
    ("Parallel", "평행 좌표"),
    ("Surface", "반응표면"),
    ("Sensitivity", "민감도"),
    ("Convergence", "수렴 이력"),
)

_SENSE_KO = {"MIN": "최소화", "MAX": "최대화"}
_CONSTRAINT_KO = {"UPPER": "상한", "LOWER": "하한", "RANGE": "범위", "BAND": "범위", "EQUAL": "등식"}
_STATE_KO = {"RUNNING": "실행 중", "FAILED": "실패", "CANCELLED": "취소됨"}


class StudyTreeWidget(QWidget):
    node_selected = Signal(str, str)

    def __init__(self, parent=None, *, language: str = "en"):
        super().__init__(parent)
        self.language = "ko" if str(language).strip().lower() == "ko" else "en"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setAccessibleName("연구 항목 탐색" if self.language == "ko" else "Study artifacts")
        self.tree.currentItemChanged.connect(self._selected)
        layout.addWidget(self.tree)

    def set_language(self, language: str) -> None:
        self.language = "ko" if str(language).strip().lower() == "ko" else "en"
        self.tree.setAccessibleName("연구 항목 탐색" if self.language == "ko" else "Study artifacts")

    def populate(self, study, *, surrogate=None, current_point_id=None, language: str | None = None):
        if language is not None:
            self.set_language(language)
        ko = self.language == "ko"
        selected = self.tree.currentItem().data(0, Qt.ItemDataRole.UserRole) if self.tree.currentItem() else None
        self.tree.blockSignals(True)
        self.tree.clear()
        root = QTreeWidgetItem(self.tree, [study.name])
        root.setExpanded(True)

        def item(parent, text, kind, key=""):
            result = QTreeWidgetItem(parent, [text])
            result.setToolTip(0, text)
            result.setData(0, Qt.ItemDataRole.UserRole, (kind, key))
            return result

        item(root, "입력 덱 / 스칼라 토큰" if ko else "Inputs / scalar tokens", "input")
        variables = item(
            root,
            f"설계 변수 ({len(study.space.variables)})" if ko else f"Variables ({len(study.space.variables)})",
            "variables",
        )
        for v in study.space.variables:
            item(variables, f"{v.name} [{getattr(v, 'unit', '—')}]", "variables", v.id)
        criteria = item(root, "목적 · 제약 응답" if ko else "Criteria / response selectors", "criteria")
        for o in study.objectives:
            sense_txt = _SENSE_KO.get(str(o.sense), str(o.sense)) if ko else str(o.sense)
            item(criteria, f"{o.response_id} — {sense_txt}", "criteria", o.id)
        for c in study.constraints:
            kind_txt = _CONSTRAINT_KO.get(str(c.kind), str(c.kind)) if ko else str(c.kind)
            item(criteria, f"{c.response_id or c.id} — {kind_txt}", "criteria", c.id)
        item(root, "실험계획 (DOE Plans)" if ko else "DOE plans", "doe")
        actual = [r for r in study.records if getattr(r, "mode", "ACTUAL") == "ACTUAL"]
        predicted = [r for r in study.records if getattr(r, "mode", "ACTUAL") == "SURROGATE"]
        completed_n = sum(r.status == "COMPLETED" for r in actual)
        campaign = item(
            root,
            f"네이티브 해석 평가 ({completed_n}개 완료)" if ko else f"Native evaluations ({completed_n} completed)",
            "evaluations",
        )
        for state in ("RUNNING", "FAILED", "CANCELLED"):
            count = sum(r.status == state for r in actual)
            if count:
                state_lbl = f"{_STATE_KO.get(state, state)}: {count}" if ko else f"{state}: {count}"
                item(campaign, state_lbl, "evaluations", state)
        if ko:
            item(root, "대리모델 — " + ("검증 가능" if surrogate else "학습 필요"), "surrogates")
            item(root, f"최적화 실행 ({len(predicted)}개 대리모델 예측)", "optimization")
            item(root, "민감도 분석", "sensitivity")
            views = item(root, "시각화 보기", "views")
            for key, ko_label in _VIEW_ITEMS:
                item(views, ko_label, "view", key)
            item(
                root,
                "저장된 선택 / 현재 설계" + (f" — {current_point_id}" if current_point_id else ""),
                "selection",
            )
        else:
            item(root, "Surrogates — " + ("validation available" if surrogate else "needs training"), "surrogates")
            item(root, f"Optimization runs ({len(predicted)} surrogate predictions)", "optimization")
            item(root, "Sensitivity analyses", "sensitivity")
            views = item(root, "Views", "views")
            for key, _ko_label in _VIEW_ITEMS:
                item(views, key, "view", key)
            item(
                root,
                "Saved selections / current design" + (f" — {current_point_id}" if current_point_id else ""),
                "selection",
            )
        root.setExpanded(True)
        views.setExpanded(True)
        if selected:
            def restore(parent):
                for i in range(parent.childCount()):
                    child = parent.child(i)
                    if child.data(0, Qt.ItemDataRole.UserRole) == selected:
                        self.tree.setCurrentItem(child)
                    restore(child)
            restore(root)
        self.tree.blockSignals(False)

    def _selected(self, item, previous=None):
        value = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if value:
            self.node_selected.emit(*value)
