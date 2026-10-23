"""Independent, read-only inspection of a project's native CalculiX input."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QApplication, QLabel, QMainWindow, QMessageBox,
                              QPlainTextEdit, QSplitter, QToolBar, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt

from caelab.workbench import Workbench
from .theme import THEME

_STATE_LABELS = {"needs_input": "입력 필요", "ready": "입력 준비",
                 "changed": "원본 변경됨", "missing": "파일 누락", "stale": "오래됨",
                 "opened": "프로그램 열림", "failed": "실패"}
_UNIT_LABELS = {"length": "길이", "force": "힘", "time": "시간", "temperature": "온도", "stress": "응력"}


class NativeAnalysisWindow(QMainWindow):
    def __init__(self, project_path: str | Path, node_id: str,
                 workbench: Workbench | None = None) -> None:
        super().__init__()
        self.workbench = workbench or Workbench()
        self.project = self.workbench.open_native_project(project_path)
        self.node_id = node_id
        node = self.project.nodes[node_id]
        if node.kind != "analysis" or node.program_id != "calculix":
            raise ValueError("CalculiX 입력 확인 대상이 아닙니다.")
        self.setWindowTitle(f"{self.project.name} — CalculiX 입력 확인")
        self.resize(1050, 760)
        self.setStyleSheet(THEME)
        toolbar = QToolBar("입력 확인", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        refresh = toolbar.addAction("파일 상태 새로고침")
        refresh.setShortcut(QKeySequence.Refresh)
        refresh.triggered.connect(self.refresh_input)
        container = QWidget()
        layout = QVBoxLayout(container)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.source = QPlainTextEdit()
        self.source.setReadOnly(True)
        self.source.setPlaceholderText("선택한 네이티브 입력 파일의 원문을 표시합니다.")
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.details)
        split.addWidget(self.source)
        split.setSizes([230, 430])
        layout.addWidget(split)
        note = QLabel("네이티브 덱 계산은 아직 구현되지 않았습니다. 입력 원문은 읽기 전용입니다.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.setCentralWidget(container)
        self.refresh_input()

    def refresh_input(self) -> None:
        self.workbench.refresh_native_project(self.project)
        node = self.project.nodes[self.node_id]
        reference = self.workbench.input_file(self.project, self.node_id)
        program = next((p for p in self.workbench.programs() if p.spec.id == node.program_id), None)
        state = _STATE_LABELS.get(node.state, node.state)
        reason = node.reason
        for node_id, owner in self.project.nodes.items():
            source = self.workbench.input_file(self.project, node_id) or owner.files.get("output")
            reason = reason.replace(node_id, Path(source.path).name if source else "프로그램")
        for source_state in ("changed", "missing", "stale"):
            reason = reason.replace(f": {source_state} —", f": {_STATE_LABELS[source_state]} —")
        reason = reason.replace(".files.input", " 입력 파일").replace(".files.output", " 출력 파일")
        self.status.setText(f"입력 상태: {state}" + (f" — {reason}" if reason else ""))
        lines = [f"프로젝트: {self.project.path}"]
        self.source.clear()
        if reference is None:
            lines.append("선택한 입력 파일이 없습니다.")
        else:
            units = ", ".join(f"{_UNIT_LABELS.get(name, name)}: {unit}" for name, unit in reference.units.items())
            unit_source = {"user": "사용자 선언", "unknown": "미확인"}.get(reference.unit_source, reference.unit_source)
            lines += [f"입력 파일: {reference.path}",
                      f"파일 상태: {_STATE_LABELS.get(reference.status, reference.status)}",
                      f"단위: {units or '미확인 — 사용자 선언 필요'}",
                      f"단위 출처: {unit_source or '미확인'}"]
            try:
                # Keep very large decks from exhausting the inspection window.
                with Path(reference.path).open("rb") as stream:
                    preview = stream.read(2 * 1024 * 1024 + 1)
                self.source.setPlainText(preview[:2 * 1024 * 1024].decode("utf-8", errors="replace"))
                if len(preview) > 2 * 1024 * 1024:
                    lines.append("원문 표시는 처음 2 MiB로 제한합니다. 전체 파일은 전용 프로그램에서 확인하세요.")
            except OSError as error:
                lines.append(f"입력 파일을 읽지 못했습니다: {error}")
        if program:
            lines += [f"CalculiX: {'발견됨' if program.available else '미설치'}",
                      f"실행 파일: {program.path or '없음'}", f"발견 경로: {program.source}",
                      f"추가 환경 변수: {', '.join(program.runtime_env) or '없음'}", f"설명: {program.reason}"]
        self.details.setPlainText("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="선택한 네이티브 CalculiX 입력 확인")
    parser.add_argument("project", help="프로젝트 JSON 파일")
    parser.add_argument("--node", required=True, help="프로젝트의 해석 상자 식별자")
    args = parser.parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    try:
        window = NativeAnalysisWindow(args.project, args.node)
    except (OSError, ValueError, KeyError) as error:
        QMessageBox.critical(None, "입력 확인창을 열지 못했습니다", str(error))
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
