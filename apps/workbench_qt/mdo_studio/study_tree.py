"""Study tree navigation dock (Area 1 of MDO Studio)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QWidget, QVBoxLayout, QLabel

from caelab.mdo.study_manager import StudyManager


class StudyTreeWidget(QWidget):
    node_selected = Signal(str, str)  # category, item_id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        header = QLabel("MDO Workflow Navigator")
        header.setStyleSheet("font-weight: bold; font-size: 11pt; padding: 4px;")
        layout.addWidget(header)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.tree)

    def populate(self, study: StudyManager) -> None:
        self.tree.clear()

        # 1. Inputs
        in_item = QTreeWidgetItem(self.tree, ["📁 Native Deck & Template"])
        QTreeWidgetItem(in_item, ["CalculiX / Abaqus .inp Template"])

        # 2. Variables
        var_item = QTreeWidgetItem(self.tree, [f"📐 Design Variables ({len(study.space.variables)})"])
        for v in study.space.variables:
            info = f"{v.id} [{v.unit or '—'}]"
            QTreeWidgetItem(var_item, [f"{v.name} ({info})"])
        var_item.setExpanded(True)

        # 3. Objectives & Constraints
        crit_item = QTreeWidgetItem(self.tree, [f"🎯 Objectives & Criteria ({len(study.objectives)} obj, {len(study.constraints)} con)"])
        for o in study.objectives:
            QTreeWidgetItem(crit_item, [f"Obj: {o.id} ({o.sense})"])
        for c in study.constraints:
            QTreeWidgetItem(crit_item, [f"Con: {c.id} ({c.kind} {c.limit})"])
        crit_item.setExpanded(True)

        doe_item = QTreeWidgetItem(self.tree, ["Design of Experiments"])
        QTreeWidgetItem(doe_item, ["Baseline + Latin Hypercube (Generate DoE)"])
        surr_item = QTreeWidgetItem(self.tree, ["Measured-data Analysis"])
        QTreeWidgetItem(surr_item, ["Sampled Pareto Front"])
        QTreeWidgetItem(surr_item, ["Gaussian Process: 1D response slice"])
        QTreeWidgetItem(surr_item, ["Spearman rank correlation"])

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        text = item.text(0)
        self.node_selected.emit("category", text)
