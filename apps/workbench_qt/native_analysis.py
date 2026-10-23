"""Independent native CalculiX input, execution and recorded results."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel,
                              QMainWindow, QMessageBox, QPlainTextEdit, QSpinBox,
                              QSplitter, QTabWidget, QToolBar, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt, QTimer

from caelab.workbench import Workbench
from .theme import THEME

_STATE_LABELS = {"needs_input": "입력 필요", "ready": "입력 준비",
                 "changed": "원본 변경됨", "missing": "파일 누락", "stale": "오래됨",
                 "opened": "프로그램 열림", "failed": "실패"}
_UNIT_LABELS = {"length": "길이", "force": "힘", "time": "시간", "temperature": "온도", "stress": "응력"}
_RUN_LABELS = {"queued": "대기", "preparing": "입력 보존 중", "running": "계산 중",
               "completed": "프로세스 정상 종료", "failed": "실패", "cancelled": "취소됨",
               "interrupted": "중단됨"}
_PARSE_LABELS = {"pending": "대기", "unparsed": "미판독", "reading": "판독 중",
                 "readable": "판독 완료", "partial": "일부 판독",
                 "unsupported": "미지원 출력 형식", "missing": "출력 없음", "failed": "판독 실패"}
_ACTIVE_STATES = {"queued", "preparing", "running"}


def _busy(run) -> bool:
    return run.state in _ACTIVE_STATES or run.parse_state == "reading"


def _signature(path: Path):
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def _unit_text(units: dict) -> str:
    return ", ".join(f"{_UNIT_LABELS.get(key, key)}: {value}" for key, value in units.items()) or "미확인"


class NativeAnalysisWindow(QMainWindow):
    def __init__(self, project_path: str | Path, node_id: str,
                 workbench: Workbench | None = None) -> None:
        super().__init__()
        self.workbench = workbench or Workbench()
        self.project = self.workbench.open_native_project(project_path)
        self.node_id = node_id
        self._project_signature = _signature(self.project.path)
        self._runs = {}
        self._owned_run_ids = set()
        self._displayed_run_id = None
        self._displayed_result = None
        node = self.project.nodes[node_id]
        if node.kind != "analysis" or node.program_id != "calculix":
            raise ValueError("CalculiX 입력 확인 대상이 아닙니다.")
        self.setWindowTitle(f"{self.project.name} — CalculiX 해석")
        self.resize(1050, 760)
        self.setStyleSheet(THEME)
        toolbar = QToolBar("해석", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        refresh = toolbar.addAction("파일 상태 새로고침")
        refresh.setShortcut(QKeySequence.Refresh)
        refresh.triggered.connect(self.refresh_input)
        self.reload_action = toolbar.addAction("프로젝트 다시 읽기")
        self.reload_action.triggered.connect(self.reload_project)
        toolbar.addSeparator()
        self.run_action = toolbar.addAction("현재 입력 계산")
        self.run_action.triggered.connect(self.start_run)
        self.cancel_action = toolbar.addAction("진행 중 계산 취소")
        self.cancel_action.triggered.connect(self.cancel_run)
        self.cancel_action.setEnabled(False)
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 604800)
        self.timeout.setValue(3600)
        self.timeout.setSuffix(" 초")
        self.timeout.setToolTip("최대 실행 시간. 이 시간이 지나면 이 창에서 시작한 계산을 종료합니다.")
        toolbar.addWidget(QLabel("  제한 시간 "))
        toolbar.addWidget(self.timeout)
        container = QWidget()
        layout = QVBoxLayout(container)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.input_heading = QLabel()
        self.input_heading.setWordWrap(True)
        self.input_heading.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.input_heading)
        self.active_status = QLabel()
        self.active_status.setWordWrap(True)
        layout.addWidget(self.active_status)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.source = QPlainTextEdit()
        self.source.setReadOnly(True)
        self.source.setPlaceholderText("선택한 네이티브 입력 파일의 원문을 표시합니다.")
        input_tabs = QTabWidget()
        input_tabs.addTab(self.source, "입력 원문")
        input_tabs.addTab(self.details, "파일 정보")
        run_panel = QWidget()
        run_layout = QVBoxLayout(run_panel)
        run_layout.setContentsMargins(0, 0, 0, 0)
        selection = QHBoxLayout()
        selection.addWidget(QLabel("조회할 실행"))
        self.run_selector = QComboBox()
        self.run_selector.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.run_selector.setMinimumContentsLength(20)
        self.run_selector.currentIndexChanged.connect(self.select_run)
        selection.addWidget(self.run_selector, 1)
        run_layout.addLayout(selection)
        self.run_status = QLabel("아직 실행 기록이 없습니다.")
        self.run_status.setWordWrap(True)
        run_layout.addWidget(self.run_status)
        self.run_source = QLabel()
        self.run_source.setWordWrap(True)
        self.run_source.setTextInteractionFlags(Qt.TextSelectableByMouse)
        run_layout.addWidget(self.run_source)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("선택한 실행의 원본 로그를 표시합니다.")
        result_panel = QWidget()
        result_layout = QVBoxLayout(result_panel)
        result_layout.setContentsMargins(0, 0, 0, 0)
        self.frame_selector = QComboBox()
        self.frame_selector.currentIndexChanged.connect(self.show_result)
        result_layout.addWidget(self.frame_selector)
        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        result_layout.addWidget(self.result_text)
        output_tabs = QTabWidget()
        output_tabs.addTab(self.log, "로그")
        output_tabs.addTab(result_panel, "결과")
        run_layout.addWidget(output_tabs)
        split = QSplitter(Qt.Vertical)
        split.addWidget(input_tabs)
        split.addWidget(run_panel)
        split.setSizes([280, 340])
        layout.addWidget(split)
        note = QLabel("입력 편집은 전용 프로그램에서 합니다. 실행 기록을 선택해도 새 계산은 시작되지 않습니다.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.setCentralWidget(container)
        self.refresh_input()
        self.refresh_runs()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh_runs)
        self.timer.start()

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
            self.input_heading.setText("계산할 입력: 파일 없음")
        else:
            units = ", ".join(f"{_UNIT_LABELS.get(name, name)}: {unit}" for name, unit in reference.units.items())
            unit_source = {"user": "사용자 선언", "unknown": "미확인"}.get(reference.unit_source, reference.unit_source)
            lines += [f"입력 파일: {reference.path}",
                      f"파일 상태: {_STATE_LABELS.get(reference.status, reference.status)}",
                      f"단위: {units or '미확인 — 사용자 선언 필요'}",
                      f"단위 출처: {unit_source or '미확인'}"]
            self.input_heading.setText(f"계산할 입력: {reference.path}\n단위: {_unit_text(reference.units)} ({unit_source or '미확인'})")
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
        self._update_actions()

    def _update_actions(self) -> None:
        changed = _signature(self.project.path) != self._project_signature
        active = [run for run in self._runs.values() if _busy(run)]
        self.run_action.setEnabled(not changed and not active)
        self.cancel_action.setEnabled(any(run.id in self._owned_run_ids for run in active))
        self.reload_action.setEnabled(not active)
        self.active_status.setText("\n".join(
            f"{'이 창에서 진행 중' if run.id in self._owned_run_ids else '다른 창에서 진행 중'}: "
            f"{Path(run.input_path).name} — {'결과 판독 중' if run.parse_state == 'reading' else _RUN_LABELS.get(run.state, run.state)}"
            for run in active))
        self.active_status.setVisible(bool(active))
        if changed:
            self.status.setText("저장된 프로젝트가 변경되었습니다. ‘프로젝트 다시 읽기’로 계산할 입력을 확인하세요.")

    def reload_project(self) -> None:
        try:
            project = self.workbench.open_native_project(self.project.path)
            node = project.nodes.get(self.node_id)
            if node is None or node.kind != "analysis" or node.program_id != "calculix":
                raise ValueError("저장된 프로젝트에 이 CalculiX 해석 상자가 없습니다.")
            self.project = project
            self._project_signature = _signature(project.path)
            self._runs.clear()
            self._displayed_run_id = None
            self.refresh_input()
            self.refresh_runs()
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "프로젝트를 다시 읽지 못했습니다", str(error))

    def start_run(self) -> None:
        if _signature(self.project.path) != self._project_signature:
            self._update_actions()
            return
        try:
            # Refresh the visible input before the explicit run command. The service
            # owns snapshotting, process creation and all validation of its lineage.
            self.refresh_input()
            run = self.workbench.submit_native(self.project, self.node_id, timeout=self.timeout.value())
            self._owned_run_ids.add(run.id)
            self.refresh_runs()
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, "계산을 시작하지 못했습니다", str(error))

    def cancel_run(self) -> None:
        for run in self._runs.values():
            if run.id in self._owned_run_ids and _busy(run):
                self.workbench.cancel_native_run(run.id)
        self.refresh_runs()

    def refresh_runs(self) -> None:
        try:
            runs = self.workbench.native_runs(self.project, self.node_id)
            selected = self.workbench.selected_native_run(self.project, self.node_id)
        except (OSError, ValueError, RuntimeError) as error:
            self.run_status.setText(f"실행 기록을 읽지 못했습니다: {error}")
            self.log.clear()
            self.result_text.clear()
            self._update_actions()
            return
        self._runs = {run.id: run for run in runs}
        selected_id = selected.id if selected is not None else None
        self.run_selector.blockSignals(True)
        ids = [self.run_selector.itemData(index) for index in range(self.run_selector.count())]
        rebuild = ids != [run.id for run in runs]
        if rebuild:
            self.run_selector.clear()
        for index, run in enumerate(runs):
            label = f"{run.created_at} — {Path(run.input_path).name} — {_RUN_LABELS.get(run.state, run.state)}"
            if rebuild:
                self.run_selector.addItem(label, run.id)
            elif self.run_selector.itemText(index) != label:
                self.run_selector.setItemText(index, label)
        self.run_selector.setCurrentIndex(self.run_selector.findData(selected_id))
        self.run_selector.blockSignals(False)
        self._update_actions()
        if selected is None:
            self.run_status.setText("아직 선택한 실행이 없습니다.")
            self.run_source.clear()
            self.log.clear()
            self.result_text.clear()
            self.frame_selector.clear()
            self._displayed_run_id = None
            return
        self._show_run(selected)

    def select_run(self) -> None:
        run_id = self.run_selector.currentData()
        if run_id is None:
            return
        try:
            self.workbench.select_native_run(self.project, self.node_id, run_id)
            self.refresh_runs()
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.warning(self, "실행을 선택하지 못했습니다", str(error))

    def _show_run(self, run) -> None:
        changed = run.id != self._displayed_run_id
        self._displayed_run_id = run.id
        state = _RUN_LABELS.get(run.state, run.state)
        parsed = _PARSE_LABELS.get(run.parse_state, run.parse_state)
        stale = f" — 오래된 입력의 결과: {run.stale_reason}" if run.stale else ""
        self.run_status.setText(f"실행: {state} / 결과: {parsed}{stale}" + (f"\n{run.error}" if run.error else ""))
        self.run_source.setText(f"이 실행의 입력: {run.input_path}\n보존 위치: {run.workdir}")
        text = ""
        if run.log_path:
            try:
                with Path(run.log_path).open("rb") as stream:
                    stream.seek(0, 2)
                    size = stream.tell()
                    stream.seek(max(0, size - 32768))
                    text = stream.read().decode("utf-8", errors="replace")
                    if size > 32768:
                        text = "[최근 로그 32 KiB — 전체 로그는 실행 폴더에 보존됨]\n" + text
            except OSError as error:
                text = f"로그를 읽지 못했습니다 ({run.log_path}): {error}"
        if changed or text != self.log.toPlainText():
            scroll = self.log.verticalScrollBar()
            position, following = scroll.value(), scroll.value() == scroll.maximum()
            self.log.setPlainText(text)
            scroll.setValue(scroll.maximum() if changed or following else position)
        result = run.result or {}
        if changed or result != self._displayed_result:
            previous = self.frame_selector.currentData() if not changed else None
            self._displayed_result = result
            self.frame_selector.blockSignals(True)
            self.frame_selector.clear()
            for frame in result.get("frames", []):
                label = f"단계 {frame.get('step', '?')} / 증분 {frame.get('increment', '?')} / 값 {frame.get('value', '?')}"
                self.frame_selector.addItem(label, frame.get("index"))
            default = result.get("summary", {}).get("frame_index")
            index = self.frame_selector.findData(previous if previous is not None else default)
            self.frame_selector.setCurrentIndex(index if index >= 0 else self.frame_selector.count() - 1)
            self.frame_selector.blockSignals(False)
        self.show_result()

    def show_result(self) -> None:
        run = self._runs.get(self._displayed_run_id)
        if run is None:
            self.result_text.clear()
            return
        result = run.result or {}
        frame = next((frame for frame in result.get("frames", [])
                      if frame.get("index") == self.frame_selector.currentData()), None)
        lines = [f"선언 단위: {_unit_text(run.units)}", "수치 검증·공학적 적합성: 미실시"]
        if frame:
            summary = frame.get("summary", {})
            value = summary.get("max_displacement")
            label = "판독된 절점 중 최대 변위" if summary.get("displacement_node_count", 0) < summary.get("mesh_node_count", 0) else "최대 변위"
            lines.append("최대 변위: 미확인" if value is None else
                         f"{label}: {value:.8g} {run.units.get('length', '(단위 미확인)')}")
            lines.append(f"출력 종류: {frame.get('analysis_type', '미확인')}")
            for item in frame.get("fields", []):
                components = ", ".join(component["name"] for component in item.get("components", []))
                coverage = item.get("coverage", {})
                lines.append(f"{item['name']} ({components}) — 판독 절점 {coverage.get('record_count', '?')} / 메시 절점 {coverage.get('mesh_node_count', '?')}")
                for component in item.get("summary", {}).get("components", []):
                    low, high = component.get("minimum"), component.get("maximum")
                    if low is not None and high is not None:
                        lines.append(f"  {component['name']}: {low:.8g} ~ {high:.8g}")
            if frame.get("vtu_file"):
                lines.append(f"교환 파일: {Path(run.workdir) / frame['vtu_file']}")
        else:
            lines.append("판독한 결과 프레임이 없습니다. 원본 출력과 로그를 확인하세요.")
        lines.extend(str(message) for message in result.get("warnings", []))
        text = "\n".join(lines)
        if self.result_text.toPlainText() != text:
            self.result_text.setPlainText(text)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.workbench.close() is False:
            self.run_status.setText("소유한 계산을 종료하고 있습니다. 종료 후 다시 닫으세요.")
            event.ignore()
            return
        self.timer.stop()
        event.accept()


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
