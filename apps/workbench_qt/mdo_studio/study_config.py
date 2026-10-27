"""Explicit token domains and measured-response criteria for native INP studies."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QDialogButtonBox, QComboBox, QMessageBox,
)
from caelab.mdo.variables import ContinuousVariable, VariableSpace, parse_unit
from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.constraints import ConstraintSpec


class StudyConfigDialog(QDialog):
    def __init__(self, tokens, study, response_units, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configure Study — 변수와 응답 바인딩")
        self.resize(760, 580)
        layout = QVBoxLayout(self)
        label = QLabel("덱과 동일한 단위로 범위와 기준값을 입력하세요. 단위 변환은 수행하지 않습니다.\n"
                       "변수는 명시적 토큰만 치환합니다. CAD 재생성·재메시는 수행하지 않습니다.")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.variables = QTableWidget(len(tokens), 5)
        self.variables.setHorizontalHeaderLabels(["Token", "Lower", "Upper", "Initial", "Unit"])
        existing = {v.id: v for v in study.space.variables}
        for row, token in enumerate(tokens):
            v = existing.get(token.lower())
            for col, value in enumerate((token, v.lower if v else "", v.upper if v else "", v.initial if v else "", v.unit if v else "")):
                item = QTableWidgetItem(str(value))
                if col == 0:
                    from PySide6.QtCore import Qt
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.variables.setItem(row, col, item)
        self.variables.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.variables)
        label = QLabel("응답: disp = 최종 FRD 프레임 절점 변위 크기 최대, stress = 같은 프레임 절점 von Mises 최대.\n"
                       "mass_eall = 같은 시각 DAT의 EALL 집합 EMAS 총질량. 사용할 응답을 선택하세요. 빈 단위는 허용하지 않습니다(무차원은 1).")
        label.setWordWrap(True)
        layout.addWidget(label)
        keys = list(dict.fromkeys(["disp", "stress", "mass_eall", *response_units, *[o.response_id for o in study.objectives],
                                   *[c.response_id for c in study.constraints if c.response_id]]))
        self.responses = QTableWidget(len(keys), 5)
        self.responses.setHorizontalHeaderLabels(["Response", "Unit", "Objective", "Constraint", "Limit"])
        for row, key in enumerate(keys):
            self.responses.setItem(row, 0, QTableWidgetItem(key))
            self.responses.setItem(row, 1, QTableWidgetItem(response_units.get(key, "")))
            obj = QComboBox(); obj.addItems(["—", "MIN", "MAX"])
            criterion = next((o for o in study.objectives if o.response_id == key), None)
            if criterion:
                obj.setCurrentText(criterion.sense)
            self.responses.setCellWidget(row, 2, obj)
            con = QComboBox(); con.addItems(["—", "UPPER", "LOWER"])
            constraint = next((c for c in study.constraints if c.response_id == key), None)
            if constraint:
                con.setCurrentText(constraint.kind)
            self.responses.setCellWidget(row, 3, con)
            self.responses.setItem(row, 4, QTableWidgetItem(str(constraint.limit) if constraint else ""))
        self.responses.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.responses)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def configuration(self):
        variables = []
        for row in range(self.variables.rowCount()):
            token, lower, upper, initial, unit = [self.variables.item(row, c).text().strip() for c in range(5)]
            if not unit:
                raise ValueError(f"{token}: 단위를 선언하세요 (무차원은 1).")
            variables.append(ContinuousVariable(id=token.lower(), name=token, lower=float(lower),
                                               upper=float(upper), initial=float(initial), unit=unit))
        objectives, constraints, units = [], [], {}
        for row in range(self.responses.rowCount()):
            key = self.responses.item(row, 0).text().strip()
            sense = self.responses.cellWidget(row, 2).currentText()
            kind = self.responses.cellWidget(row, 3).currentText()
            if sense == kind == "—":
                continue
            if key in units or not key:
                raise ValueError("응답 이름은 비어 있거나 중복될 수 없습니다.")
            unit = self.responses.item(row, 1).text().strip()
            if not unit:
                raise ValueError(f"{key}: 응답 단위를 선언하세요.")
            parse_unit(unit)
            units[key] = unit
            if sense != "—":
                objectives.append(ObjectiveSpec(id=key, response_id=key, sense=sense))
            if kind != "—":
                constraints.append(ConstraintSpec(id=f"{key}_limit", response_id=key, kind=kind,
                                                  limit=float(self.responses.item(row, 4).text()), unit=unit))
        if not objectives:
            raise ValueError("목적 응답을 하나 이상 선택하세요.")
        return VariableSpace(variables=tuple(variables)), objectives, constraints, units

    def accept(self):
        try:
            self.configuration()
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "설정 확인", str(exc))
            return
        super().accept()
