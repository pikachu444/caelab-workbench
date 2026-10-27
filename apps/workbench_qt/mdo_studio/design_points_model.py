"""High-performance virtual QAbstractTableModel for Table of Design Points (TDP).

Avoids QTableWidgetItem widget allocation overhead; queries data dynamically
allowing 10,000+ design points to be browsed without UI lag.
"""

from __future__ import annotations

from typing import Any, Sequence
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QBrush

from caelab.mdo.study_manager import DesignPointRecord


class DesignPointsTableModel(QAbstractTableModel):
    def __init__(
        self,
        records: list[DesignPointRecord] | None = None,
        parameter_keys: Sequence[str] = (),
        response_keys: Sequence[str] = (),
        is_dark: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.records: list[DesignPointRecord] = records or []
        self.parameter_keys: list[str] = list(parameter_keys)
        self.response_keys: list[str] = list(response_keys)
        self.is_dark: bool = is_dark
        self._row_map: dict[str, int] = {r.point_id: i for i, r in enumerate(self.records)}
        self._build_headers()

    def set_theme(self, is_dark: bool) -> None:
        self.is_dark = is_dark
        self.layoutChanged.emit()

    def _build_headers(self) -> None:
        self.headers = ["ID", "Name", "Status", "Feasibility", "Pareto?"]
        self.headers.extend([f"P: {k}" for k in self.parameter_keys])
        self.headers.extend([f"R: {k}" for k in self.response_keys])
        self.headers.extend(["Violation", "Time (s)"])

    def set_study_data(
        self,
        records: list[DesignPointRecord],
        parameter_keys: Sequence[str],
        response_keys: Sequence[str],
    ) -> None:
        self.beginResetModel()
        self.records = records
        self.parameter_keys = list(parameter_keys)
        self.response_keys = list(response_keys)
        self._row_map = {r.point_id: i for i, r in enumerate(self.records)}
        self._build_headers()
        self.endResetModel()

    def update_point_result(
        self,
        point_id: str,
        status: str,
        observations: dict[str, float] | None = None,
        feasibility: str | None = None,
        total_violation: float = 0.0,
        duration_sec: float = 0.0,
        is_pareto: bool = False,
    ) -> None:
        row = self._row_map.get(point_id)
        if row is None or row >= len(self.records):
            return
        rec = self.records[row]
        rec.status = status
        if observations is not None:
            rec.observations.update(observations)
        if feasibility is not None:
            rec.feasibility = feasibility
        rec.total_violation = total_violation
        rec.duration_sec = duration_sec
        rec.is_pareto = is_pareto

        top_left = self.index(row, 0)
        bottom_right = self.index(row, self.columnCount() - 1)
        self.dataChanged.emit(top_left, bottom_right, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ForegroundRole])

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self.headers)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if 0 <= section < len(self.headers):
                return self.headers[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self.records):
            return None

        rec = self.records[index.row()]
        col = index.column()

        # Fixed columns
        num_fixed = 5
        num_params = len(self.parameter_keys)
        num_responses = len(self.response_keys)

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return rec.point_id
            elif col == 1:
                return rec.name
            elif col == 2:
                return rec.status
            elif col == 3:
                return rec.feasibility or "—"
            elif col == 4:
                return "★ Pareto" if rec.is_pareto else "—"
            elif num_fixed <= col < num_fixed + num_params:
                pkey = self.parameter_keys[col - num_fixed]
                val = rec.physical_values.get(pkey)
                return f"{val:.4g}" if isinstance(val, (int, float)) else str(val)
            elif num_fixed + num_params <= col < num_fixed + num_params + num_responses:
                rkey = self.response_keys[col - (num_fixed + num_params)]
                val = rec.observations.get(rkey)
                return f"{val:.4g}" if val is not None else "—"
            elif col == num_fixed + num_params + num_responses:
                return f"{rec.total_violation:.4g}" if rec.total_violation > 0 else "0.0"
            elif col == num_fixed + num_params + num_responses + 1:
                return f"{rec.duration_sec:.2f}"

        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (0, 2, 3, 4):
                return int(Qt.AlignmentFlag.AlignCenter)
            elif col >= num_fixed:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        elif role == Qt.ItemDataRole.ForegroundRole:
            if col == 3:  # Feasibility
                if rec.feasibility == "FEASIBLE":
                    return QBrush(QColor("#22c55e"))  # Emerald green
                elif rec.feasibility == "MARGINAL":
                    return QBrush(QColor("#f59e0b"))  # Amber
                elif rec.feasibility == "INFEASIBLE":
                    return QBrush(QColor("#ef4444"))  # Red
            elif col == 4 and rec.is_pareto:
                return QBrush(QColor("#38bdf8"))  # Sky blue
            return QBrush(QColor("#f1f5f9" if self.is_dark else "#0f172a"))
