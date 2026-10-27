"""Main window for CAELab MDO Studio (native-deck studies).

Implements Section 15 of spec-clean-slate-mdo-design:
- Area 1: Left Study Tree (Workflow navigator)
- Area 2: Right Setup Dock (Contextual properties & Apply as Current Point)
- Area 3: Center-Top Table of Design Points (QTableView virtual model)
- Area 4: Center-Bottom Visualization Studio (Pareto Front, Sensitivity Tornado, Kriging ±1.96σ Surface)
- Full Dark/Light Engineering theme synchronization
- Live JobManager asynchronous batch execution and MDOExecutionBridge signal-slot integration
- Explicit native-deck studies; uncomputed benchmark target specification
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import math
import re
import shutil
import hashlib
import json
import uuid
from dataclasses import asdict
from typing import Sequence, Any
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt, QItemSelection, QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTableView, QTabWidget, QToolBar, QLabel, QDockWidget, QStatusBar,
    QMessageBox, QHeaderView, QFileDialog, QProgressBar, QInputDialog, QDialog
)
from PySide6.QtGui import QAction, QCloseEvent

from caelab.mdo.study_manager import StudyManager, DesignPointRecord
from caelab.mdo.inp_binder import TemplateDeck
from caelab.mdo.variables import ContinuousVariable, VariableSpace
from caelab.mdo.objectives import ObjectiveSpec, evaluate_criteria
from caelab.mdo.constraints import ConstraintSpec, assess_constraints
from .views.sensitivity_view import SensitivityTornadoWidget, TornadoItem
from caelab.mdo.surrogate import SurrogateDataset, SurrogateConfig, fit_surrogate
from .bridge import MDOExecutionBridge
from caelab.workbench import Workbench
from caelab.jobs import BatchItem, BatchProgress
from caelab.connections import ProgramSpec, ProgramStatus
from caelab.native.run_store import RunStore, atomic_json
from caelab.native.input_snapshot import snapshot_input, _includes
from caelab.mdo.doe import DOEConfig, generate_design
from .study_config import StudyConfigDialog

from apps.workbench_qt.theme import (
    get_palette, build_stylesheet, toggle_theme_mode, theme_toggle_label, current_theme_mode
)
from .design_points_model import DesignPointsTableModel
from .study_tree import StudyTreeWidget
from .setup_dock import SetupDockWidget
from .views.pareto_view import ParetoFrontWidget
from .views.response_surface_view import ResponseSurfaceWidget


def extract_calculix_responses(workdir: Path, run_result: dict | None) -> dict[str, float]:
    """Final FRD nodal fields and same-time DAT set totals, in declared deck units."""
    from caelab.native.results import _Reader
    obs = {}
    frd_path = workdir / "model.frd"
    final_time = None
    if frd_path.is_file():
        try:
            with frd_path.open(encoding="utf-8", errors="strict") as stream:
                reader = _Reader(stream, lambda: None)
                reader.read()
            if reader.frames:
                frame = reader.frames[-1]
                final_time = frame.metadata["value"]
                for result in frame.fields:
                    if result.warnings or not result.values or set(result.values) != set(reader.nodes):
                        continue
                    names = [c["name"].upper() for c in result.stored]
                    values = list(result.values.values())
                    if not all(math.isfinite(v) for row in values for v in row):
                        continue
                    if result.name == "DISP" and all(k in names for k in ("D1", "D2", "D3")):
                        obs["disp"] = max(math.hypot(*(row[names.index(k)] for k in ("D1", "D2", "D3"))) for row in values)
                    elif result.name == "STRESS" and all(k in names for k in ("SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX")):
                        vm = []
                        for row in values:
                            xx, yy, zz, xy, yz, zx = [row[names.index(k)] for k in ("SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX")]
                            vm.append(math.sqrt(.5 * ((xx-yy)**2 + (yy-zz)**2 + (zz-xx)**2) + 3 * (xy**2 + yz**2 + zx**2)))
                        obs["stress"] = max(vm)
        except (OSError, ValueError, OverflowError):
            return {}
    dat_path = workdir / "model.dat"
    if dat_path.is_file() and final_time is not None:
        text = dat_path.read_text(encoding="utf-8", errors="replace")
        # CalculiX printout.f EMAS TOTALS=ONLY: header, blank line, scalar.
        pattern = r"total mass for set\s+(\S+)\s+and time\s+([+\-\d.eEdD]+)\s*\n\s*([+\-\d.eEdD]+)"
        for match in re.finditer(pattern, text, re.IGNORECASE):
            name, stamp, raw = match.groups()
            try:
                stamp, mass = float(stamp.replace("D", "E")), float(raw.replace("D", "E"))
                if math.isclose(stamp, final_time, rel_tol=1e-6, abs_tol=1e-12) and math.isfinite(mass) and mass >= 0:
                    obs[f"mass_{name.lower()}"] = mass
            except ValueError:
                pass
    return {key: value for key, value in obs.items() if math.isfinite(value)}


class MDOStudioWindow(QMainWindow):
    def __init__(self, study: StudyManager | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CAELab MDO Studio")
        self.resize(1380, 880)

        self.workbench = Workbench()
        self.bridge = MDOExecutionBridge(self)
        self._active_batch_id: str | None = None
        self._active_store: RunStore | None = None
        self.current_deck_path: Path | None = None
        self.template_deck: TemplateDeck | None = None
        self._execution_template: TemplateDeck | None = None

        self._viz_timer = QTimer(self)
        self._viz_timer.setSingleShot(True)
        self._viz_timer.timeout.connect(self._deferred_update_visualizations)

        # Bridge signal connections (QueuedConnection ensures main thread handling)
        self.bridge.batchProgress.connect(self._on_batch_progress)
        self.bridge.batchFinished.connect(self._on_batch_finished)
        self.bridge.itemFinished.connect(self._on_item_finished)
        self.bridge.itemFailed.connect(self._on_item_failed)

        self._source_revisions: dict[str, str] = {}
        self._point_sources: dict[str, dict] = {}
        self.response_units: dict[str, str] = {}
        self._configured = False
        self._is_specification = False
        self._study_file: Path | None = None
        self.study = study or StudyManager()
        self.is_dark = current_theme_mode() == "dark"

        self._build_ui()
        self._populate_study()
        self.apply_theme()

    def _build_ui(self) -> None:
        # Toolbar
        tb = QToolBar("MDO Main Toolbar")
        tb.setMovable(False)
        self.addToolBar(tb)

        act_new = tb.addAction("📄 새 스터디")
        act_new.setToolTip("빈 MDO 스터디 시작 (Clean New Study)")
        act_new.triggered.connect(self._on_new_study)

        act_load_deck = tb.addAction("📂 해석 덱 열기")
        act_load_deck.setToolTip("CalculiX .inp 덱에서 <PARAM> 토큰 검색")
        act_load_deck.triggered.connect(lambda: self._on_load_deck())

        act_demo = tb.addAction("GE 목표 사양")
        act_demo.setToolTip("미계산 변수 사양 예제입니다. GE 모델이나 해석 결과를 포함하지 않습니다.")
        act_demo.triggered.connect(self._on_load_demo)

        self.act_configure = tb.addAction("Configure Study")
        self.act_configure.triggered.connect(self._on_configure)
        self.act_generate = tb.addAction("Generate DoE")
        self.act_generate.triggered.connect(lambda: self._on_generate_doe())
        self._study_actions = [act_new, act_load_deck, act_demo, self.act_configure, self.act_generate]
        file_menu = self.menuBar().addMenu("File")
        open_study = file_menu.addAction("Open Study…")
        open_study.triggered.connect(lambda: self._on_open_study())
        save_study = file_menu.addAction("Save Study…")
        save_study.triggered.connect(lambda: self._on_save_study())
        self._study_actions.extend([open_study, save_study])
        tb.addSeparator()
        self.act_run = tb.addAction("▶ Run Evaluations")
        self.act_run.setToolTip("Evaluate all pending design points using JobManager batch dispatch")
        self.act_run.triggered.connect(self._run_all_points)

        self.act_stop = tb.addAction("⏹ Stop Scheduling")
        self.act_stop.setToolTip("Halt pending design point dispatches and terminate active solver process tree")
        self.act_stop.setEnabled(False)
        self.act_stop.triggered.connect(self._stop_evaluations)

        tb.addSeparator()
        self.act_theme = tb.addAction(theme_toggle_label())
        self.act_theme.triggered.connect(self._on_toggle_theme)

        # Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(180)
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)
        self.status_bar.showMessage("Ready. MDO Studio initialized.")

        # Area 1: Left Study Tree Dock
        self.tree_dock = QDockWidget("Study Workflow")
        self.tree_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.study_tree = StudyTreeWidget()
        self.tree_dock.setWidget(self.study_tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.tree_dock)

        # Area 2: Right Setup Dock
        self.setup_dock = QDockWidget("Design Point Setup & Actions")
        self.setup_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.setup_dock.setMinimumWidth(280)
        self.setup_widget = SetupDockWidget(is_dark=self.is_dark)
        self.setup_widget.apply_as_current.connect(self._on_apply_as_current)
        self.setup_dock.setWidget(self.setup_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.setup_dock)

        # Center Splitter (Top: TDP, Bottom: Viz Studio)
        center_splitter = QSplitter(Qt.Orientation.Vertical)

        # Area 3: Center-Top Table of Design Points
        tdp_container = QWidget()
        tdp_layout = QVBoxLayout(tdp_container)
        tdp_layout.setContentsMargins(4, 4, 4, 4)

        self.study_label = QLabel()
        self.study_label.setWordWrap(True)
        tdp_layout.addWidget(self.study_label)
        self.tdp_lbl = QLabel("Design Points — declared deck units")
        title_color = "#38bdf8" if self.is_dark else "#0284c7"
        self.tdp_lbl.setStyleSheet(f"font-weight: bold; font-size: 10pt; color: {title_color};")
        tdp_layout.addWidget(self.tdp_lbl)

        self.tdp_table = QTableView()
        self.tdp_model = DesignPointsTableModel(is_dark=self.is_dark)
        self.tdp_table.setModel(self.tdp_model)
        self.tdp_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.tdp_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.tdp_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tdp_table.selectionModel().selectionChanged.connect(self._on_row_selected)
        tdp_layout.addWidget(self.tdp_table)
        center_splitter.addWidget(tdp_container)

        # Area 4: Center-Bottom Visualization Studio
        self.viz_tabs = QTabWidget()
        self.pareto_view = ParetoFrontWidget(is_dark=self.is_dark)
        self.tornado_view = SensitivityTornadoWidget(is_dark=self.is_dark)
        self.surface_view = ResponseSurfaceWidget(is_dark=self.is_dark)

        self.viz_tabs.addTab(self.pareto_view, "🎯 Multi-Objective Pareto Front")
        self.viz_tabs.addTab(self.tornado_view, "🌪️ Sensitivity Tornado Diagram")
        self.viz_tabs.addTab(self.surface_view, "📈 Kriging Response Slice (±1.96σ)")
        center_splitter.addWidget(self.viz_tabs)

        center_splitter.setStretchFactor(0, 4)
        center_splitter.setStretchFactor(1, 5)
        self.setCentralWidget(center_splitter)

    def _create_demo_ge_bracket_study(self) -> StudyManager:
        """Create uncomputed target-variable examples; no GE deck is supplied."""
        v_tb = ContinuousVariable(id="t_base", name="Base Thickness", unit="mm", lower=8.0, upper=22.0, initial=15.0)
        v_tr = ContinuousVariable(id="t_rib", name="Rib Thickness", unit="mm", lower=6.0, upper=18.0, initial=12.0)
        v_rf = ContinuousVariable(id="r_fillet", name="Fillet Radius", unit="mm", lower=3.0, upper=14.0, initial=6.0)
        v_db = ContinuousVariable(id="d_boss", name="Center Boss Outer Diameter", unit="mm", lower=48.0, upper=72.0, initial=60.0)
        v_tw = ContinuousVariable(id="t_wall", name="Boss Wall Thickness", unit="mm", lower=6.0, upper=15.0, initial=10.0)
        v_hs = ContinuousVariable(id="h_stiff", name="Stiffener Height", unit="mm", lower=20.0, upper=50.0, initial=35.0)
        space = VariableSpace(variables=(v_tb, v_tr, v_rf, v_db, v_tw, v_hs))

        # Objectives: Min Mass [kg], Min Pin Displacement [mm]
        obj_mass = ObjectiveSpec(id="mass", response_id="mass", sense="MIN", scale=2.0)
        obj_disp = ObjectiveSpec(id="disp", response_id="disp", sense="MIN", scale=1.0)

        # Constraint: Max von Mises Stress <= 650.0 MPa (Ti-6Al-4V Yield 830 MPa, Safety Factor 1.28)
        con_stress = ConstraintSpec(id="stress_con", response_id="stress", kind="UPPER", limit=650.0, scale=100.0)

        mgr = StudyManager("GE Jet Engine Bracket MDO Benchmark Specification (Synthetic Demo)", space=space, objectives=[obj_mass, obj_disp], constraints=[con_stress])

        # This is a design-space specification, never a measured campaign.
        design = generate_design(space, DOEConfig(method="lhs", n_samples=25, seed=42))
        for point in design.points:
            rec = mgr.add_point(point.values)
            rec.feasibility = "NOT_ASSESSED"
        return mgr

    def _populate_study(self) -> None:
        self.study_tree.populate(self.study)
        self.setup_widget.display_record(None)
        if self._is_specification:
            self.study_label.setText(self.study.name + "\n[미계산 목표 사양 / 비실측] GE 모델·결과 없음. 실제 .inp 덱을 여세요.")
        else:
            self.study_label.setText(self.study.name + (f"\nInput: {self.current_deck_path}" if self.current_deck_path else "\n해석 덱 열기 → Configure Study → Generate DoE → Run Evaluations"))

        param_keys = [v.id for v in self.study.space.variables]
        if self.study.objectives:
            response_keys = list(dict.fromkeys([o.response_id for o in self.study.objectives] + [c.response_id for c in self.study.constraints if c.response_id]))
        elif self.study.records and self.study.records[0].observations:
            response_keys = list(self.study.records[0].observations.keys())
        else:
            response_keys = ["response"]
        self.tdp_model.set_study_data(self.study.records, param_keys, response_keys)
        self._update_visualizations()

    @staticmethod
    def _hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _execution_text(template: str, path: Path, manifest: dict) -> str:
        data = template.encode("utf-8")
        layout = {entry["source_path"]: entry["execution_path"] for entry in manifest["files"]}
        for start, end, name, _ in reversed(list(_includes(data, path))):
            target = Path(name)
            target = (path.parent / target).resolve() if not target.is_absolute() else target.resolve()
            data = data[:start] + layout[str(target)].encode("ascii") + data[end:]
        return data.decode("utf-8")

    @staticmethod
    def _run_files(run) -> dict[Path, str]:
        expected = {}
        snapshot = getattr(run, "snapshot", {})
        for entry in snapshot.get("files", []):
            for key, digest in (("source_copy", "original_sha256"), ("execution_path", "execution_sha256")):
                relative = Path(entry[key])
                path = (run.workdir / relative).resolve()
                if relative.is_absolute() or not path.is_relative_to(run.workdir.resolve()):
                    raise ValueError("실행 입력이 실행 폴더 밖을 참조합니다.")
                expected[path] = entry[digest]
        for relative, digest in snapshot.get("artifacts", {}).items():
            path = (run.workdir / relative).resolve()
            if Path(relative).is_absolute() or not path.is_relative_to(run.workdir.resolve()):
                raise ValueError("실행 산출물이 실행 폴더 밖을 참조합니다.")
            expected[path] = digest
        return expected

    def _sources_unchanged(self) -> bool:
        return bool(self._source_revisions) and all(Path(p).is_file() and self._hash(Path(p)) == revision for p, revision in self._source_revisions.items())

    def _point_is_verified(self, rec, *, require_feasible=True) -> bool:
        evidence = self._point_sources.get(rec.point_id)
        if self._active_batch_id or self._is_specification or not evidence or rec.status != "COMPLETED" or (require_feasible and rec.feasibility != "FEASIBLE"):
            return False
        try:
            if not self._sources_unchanged() or evidence["values"] != rec.physical_values:
                return False
            if not evidence["files"] or not all(Path(p).is_file() and self._hash(Path(p)) == h for p, h in evidence["files"].items()):
                return False
            receipt = json.loads(Path(evidence["study_manifest"]).read_text(encoding="utf-8"))
            if (receipt["template"] != self.template_deck.original_text
                    or receipt["execution_template"] != self._execution_template.original_text
                    or receipt["points"][rec.point_id] != rec.physical_values
                    or self._execution_text(self.template_deck.original_text, self.current_deck_path, receipt["snapshot"]) != self._execution_template.original_text):
                return False
            run_path = Path(evidence["workdir"])
            run = RunStore(run_path.parent).read(run_path / "job.json")
            if run.state != "completed" or run.id != evidence["run_id"] or run.returncode != 0:
                return False
            native_files = self._run_files(run)
            if not native_files or any(not p.is_file() or self._hash(p) != h for p, h in native_files.items()):
                return False
            obs = extract_calculix_responses(run_path, run.result)
            assessment = evaluate_criteria(obs, self.study.objectives, self.study.constraints)
            expected = self._execution_template.bind(rec.physical_values).encode("utf-8")
            source_copy = run_path / "source" / "000000.inp"
            return (assessment.assessed and (not require_feasible or assessment.constraints.feasibility.value == "FEASIBLE")
                    and obs == rec.observations and source_copy.read_bytes() == expected)
        except (OSError, ValueError, TypeError, KeyError):
            return False

    def _update_visualizations(self) -> None:
        completed = [r for r in self.study.records if r.status == "COMPLETED" and r.observations]
        for view, text in ((self.surface_view, "No measured data for a response slice"), (self.pareto_view, "Two objective responses are required")):
            view.ax.clear()
            view._apply_theme()
            view.ax.text(.5, .5, text, ha="center", va="center", transform=view.ax.transAxes,
                         color="#e2e8f0" if self.is_dark else "#0f172a", gid="mdo_empty")
            view.canvas.draw_idle()
        if len(self.study.objectives) >= 2:
            ox, oy = self.study.objectives[:2]
            self.pareto_view.plot_study(self.study.records, ox.response_id, oy.response_id,
                                       obj_x_name=f"{ox.response_id} [{self.response_units.get(ox.response_id, '?')}] ({ox.sense})",
                                       obj_y_name=f"{oy.response_id} [{self.response_units.get(oy.response_id, '?')}] ({oy.sense})")
        target = self.study.objectives[0].response_id if self.study.objectives else None
        var_ids = [v.id for v in self.study.space.variables]
        valid = [r for r in completed if target in r.observations and all(k in r.physical_values for k in var_ids)]
        items = []
        if len(valid) >= 3 and var_ids:
            from caelab.mdo.sensitivity import spearman
            X = np.array([[r.physical_values[k] for k in var_ids] for r in valid])
            y = np.array([[r.observations[target]] for r in valid])
            try:
                for pair in spearman(X, y, variable_ids=var_ids, response_ids=(target,)):
                    if pair.rho is not None and math.isfinite(pair.rho):
                        items.append(TornadoItem(variable_id=pair.variable_id, metric="Spearman |rho|", magnitude=abs(pair.rho), sign=1 if pair.rho >= 0 else -1))
            except ValueError:
                pass
            try:
                ds = SurrogateDataset(x=X, y=y, point_ids=tuple(r.point_id for r in valid),
                                      run_ids=tuple(self._point_sources.get(r.point_id, {}).get("run_id", r.point_id) for r in valid),
                                      response_ids=(target,), units=(self.response_units.get(target, ""),), feature_order=tuple(var_ids))
                surrogate = fit_surrogate(ds, SurrogateConfig(method="gp", gp_kernel="matern52", seed=42))
                variable = self.study.space.variables[0]
                self.surface_view.plot_kriging_slice(surrogate, valid, variable.id, target, variable.lower, variable.upper,
                                                    fixed_values={v.id: v.initial for v in self.study.space.variables[1:]})
            except (ValueError, ImportError, ArithmeticError) as exc:
                self.surface_view.ax.clear()
                self.surface_view.ax.text(.5, .5, f"Surrogate unavailable: {exc}", ha="center", va="center", wrap=True, transform=self.surface_view.ax.transAxes,
                                          color="#e2e8f0" if self.is_dark else "#0f172a", gid="mdo_empty")
                self.surface_view.canvas.draw_idle()
        self.tornado_view.plot_tornado(items, target_name=f"Spearman |rho|: {target or 'no response'}")
        if self.study.records and not self.tdp_table.selectionModel().hasSelection():
            self.tdp_table.selectRow(0)
        self._on_row_selected(None, None)

    def _deferred_update_visualizations(self) -> None:
        self._update_visualizations()

    def _on_row_selected(self, selected, deselected) -> None:
        indexes = self.tdp_table.selectionModel().selectedRows()
        rec = self.study.records[indexes[0].row()] if indexes and indexes[0].row() < len(self.study.records) else None
        self.setup_widget.display_record(rec)
        self.setup_widget.btn_apply_current.setEnabled(bool(rec and self._point_is_verified(rec)))
        if rec:
            evidence = self._point_sources.get(rec.point_id)
            detail = f"Run: {evidence['run_id']}\n{evidence['workdir']}" if evidence else "No verified solver run"
            self.setup_widget.lbl_guide.setPlainText((rec.error_message + "\n" if rec.error_message else "") + detail)
            self.setup_widget.lbl_guide.setToolTip(detail)
            self.setup_widget.lbl_guide.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def _on_apply_as_current(self, point_id: str) -> None:
        rec = next((r for r in self.study.records if r.point_id == point_id), None)
        if not rec or not self.template_deck or not self.current_deck_path or not self._point_is_verified(rec):
            QMessageBox.warning(self, "적용할 수 없음", "완료·적합한 실제 실행과 변경되지 않은 입력/결과가 필요합니다. 파일을 확인하고 다시 평가하세요.")
            return
        temporary = None
        try:
            path = self.current_deck_path
            data = self.template_deck.bind(rec.physical_values).encode("utf-8")
            original = path.read_bytes()
            backup = path.with_name(path.name + ".bak")
            if backup.exists():
                backup = path.with_name(path.name + f".{uuid.uuid4().hex[:8]}.bak")
            # Exclusive creation preserves every previous backup.
            with backup.open("xb") as stream:
                stream.write(original)
                stream.flush()
                os.fsync(stream.fileno())
            with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if not self._sources_unchanged():
                raise ValueError("백업 중 원본 입력이 변경되었습니다.")
            os.replace(temporary, path)
            self._source_revisions[str(path)] = self._hash(path)
            QMessageBox.information(self, "Set as Current Design Point", f"Applied {point_id}\nINP: {path}\nBackup: {backup}\n원래 파라미터 템플릿은 스터디에 유지됩니다. CAD는 변경하지 않았습니다.")
            self.status_bar.showMessage(f"Applied {point_id}; backup: {backup}", 8000)
            self._on_row_selected(None, None)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "적용 실패", str(exc))
            self.status_bar.showMessage(f"Apply failed: {exc}", 8000)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def _set_running(self, active: bool) -> None:
        for action in self._study_actions:
            action.setEnabled(not active)
        self.act_run.setEnabled(not active)
        self.act_stop.setEnabled(active)
        self.progress_bar.setVisible(active)
        self.setup_widget.btn_apply_current.setEnabled(False)

    def _run_all_points(self) -> None:
        if self._active_batch_id:
            return
        if self._is_specification or not self.current_deck_path or not self.template_deck:
            QMessageBox.warning(self, "CalculiX 입력 필요", "No CalculiX .inp deck loaded. 실제 .inp 덱을 열고 변수를 설정하세요. 목표 사양은 해석 모델이 아닙니다.")
            return
        if not self._configured or not self.study.records:
            QMessageBox.warning(self, "평가 준비", "Configure Study와 Generate DoE를 먼저 실행하세요.")
            return
        solver = next((p for p in self.workbench.programs() if p.spec.id == "calculix"), None)
        if not solver or not solver.available:
            QMessageBox.warning(self, "CalculiX 없음", "CalculiX 연결을 확인하세요. CAELAB_CCX에 실제 ccx 실행 파일을 지정하세요.")
            return
        try:
            if not self._sources_unchanged():
                raise ValueError("원본 입력 또는 INCLUDE가 변경되었습니다. 덱을 다시 열고 설정하세요.")
            batch_id = uuid.uuid4().hex
            batch_dir = self.current_deck_path.parent / ".caelab_mdo" / batch_id
            snapshot_dir = batch_dir / "template"
            manifest = snapshot_input(self.current_deck_path, snapshot_dir)
            # The saved immutable template remains authoritative after Set as Current.
            effective = self._execution_template
            if self._execution_text(self.template_deck.original_text, self.current_deck_path, manifest) != effective.original_text:
                raise ValueError("원본 템플릿과 실행 템플릿이 일치하지 않습니다. 덱을 다시 여세요.")
            items = []
            for rec in self.study.records:
                self.study.space.encode(rec.physical_values)
                folder = batch_dir / "inputs" / rec.point_id
                shutil.copytree(snapshot_dir, folder)
                content = effective.bind(rec.physical_values)
                if TemplateDeck.from_text(content).token_names:
                    raise ValueError("미치환 파라미터가 있습니다.")
                (folder / "model.inp").write_text(content, encoding="utf-8", newline="")
                items.append(BatchItem(item_id=rec.point_id, input_path=folder / "model.inp", status=solver,
                                       node_id="mdo_batch", parameters=dict(rec.physical_values), timeout=300,
                                       units=dict(self.response_units), unit_source="MDO Configure Study (declared deck units)"))
            atomic_json(batch_dir / "study-input.json", {"source_revisions": self._source_revisions, "snapshot": manifest,
                                                       "template": self.template_deck.original_text, "execution_template": self._execution_template.original_text,
                                                       "variables": [asdict(v) for v in self.study.space.variables],
                                                       "objectives": [asdict(o) for o in self.study.objectives],
                                                       "constraints": [asdict(c) for c in self.study.constraints],
                                                       "points": {r.point_id: r.physical_values for r in self.study.records}})
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "배치 준비 실패", str(exc))
            return
        self._point_sources.clear()
        for rec in self.study.records:
            rec.status = "QUEUED"
            rec.observations.clear(); rec.objectives.clear()
            rec.feasibility = "NOT_ASSESSED"
            rec.is_pareto = False; rec.duration_sec = 0; rec.error_message = ""
        self._populate_study()
        self._active_batch_id = batch_id
        self._active_store = RunStore(batch_dir / "runs")
        self._set_running(True)
        self.progress_bar.setValue(0)
        self.status_bar.showMessage(f"Batch outputs: {batch_dir}")

        def finished(item, run):
            item.scalar_responses = extract_calculix_responses(run.workdir, run.result) if item.state == "completed" else {}
            self.bridge.itemFinished.emit(item.item_id, item, run)
        try:
            self.workbench.jobs.submit_batch(self._active_store, items, name=self.study.name, batch_id=batch_id,
                                            on_item_finished=finished, on_progress=self.bridge.batchProgress.emit,
                                            on_batch_finished=lambda b: self.bridge.batchFinished.emit(b.id, b.state, b.completed_count, b.failed_count))
        except Exception as exc:
            self._active_batch_id = None
            self._set_running(False)
            for rec in self.study.records:
                rec.status = "FAILED"; rec.error_message = str(exc)
            self._populate_study()
            QMessageBox.warning(self, "배치 제출 실패", str(exc))

    def _stop_evaluations(self) -> None:
        if self._active_batch_id:
            self.act_stop.setEnabled(False)
            self.workbench.jobs.cancel_batch(self._active_batch_id)
            self.status_bar.showMessage("배치 취소 요청 중…")

    def _on_batch_progress(self, prog: Any) -> None:
        if not prog or prog.batch_id != self._active_batch_id:
            return
        self.status_bar.showMessage(prog.heartbeat_message)
        if prog.total_count:
            self.progress_bar.setValue(int(prog.completed_count / prog.total_count * 100))
        if prog.current_item_id:
            rec = next((r for r in self.study.records if r.point_id == prog.current_item_id), None)
            if rec and rec.status == "QUEUED":
                self.tdp_model.update_point_result(rec.point_id, "RUNNING")

    def _on_item_finished(self, point_id: str, item: Any, run: Any) -> None:
        if self._active_batch_id is None or item.batch_id != self._active_batch_id:
            return
        rec = next((r for r in self.study.records if r.point_id == point_id), None)
        if rec is None:
            return
        rec.observations = dict(item.scalar_responses)
        rec.duration_sec = item.duration_sec
        rec.status = "COMPLETED" if item.state == "completed" else item.state.upper()
        rec.error_message = item.error
        assessment = evaluate_criteria(rec.observations, self.study.objectives, self.study.constraints,
                                       valid=item.state == "completed")
        rec.feasibility = assessment.constraints.feasibility.value if assessment.assessed else "NOT_ASSESSED"
        rec.total_violation = assessment.constraints.total_violation or 0.0
        rec.objectives = {o.id: float(v) for o, v in zip(self.study.objectives, assessment.objectives)} if assessment.assessed else {}
        if item.state == "completed" and not assessment.assessed:
            required = {o.response_id for o in self.study.objectives} | {c.response_id for c in self.study.constraints}
            missing = sorted(k for k in required if k not in rec.observations or not math.isfinite(rec.observations[k]))
            rec.status = "FAILED"
            rec.error_message = f"Solver completed; required responses missing/invalid: {', '.join(missing)}"
        if rec.status == "COMPLETED":
            paths = [run.workdir / name for name in ("job.json", "model.inp", "source/000000.inp", "model.frd", "model.dat", "job.log")]
            study_manifest = run.workdir.parent.parent / "study-input.json"
            paths.append(study_manifest)
            paths.extend(self._run_files(run))
            self._point_sources[point_id] = {"study_manifest": str(study_manifest), "run_id": run.id, "workdir": str(run.workdir), "values": dict(rec.physical_values),
                                              "files": {str(p): self._hash(p) for p in paths if p.is_file()}}
        self.study.update_pareto_front()
        self.tdp_model.update_point_result(point_id, rec.status, rec.observations, rec.feasibility, rec.total_violation, rec.duration_sec, rec.is_pareto)
        self._viz_timer.start(150)

    def _on_item_failed(self, point_id: str, error_msg: str) -> None:
        rec = next((r for r in self.study.records if r.point_id == point_id), None)
        if rec:
            rec.error_message = error_msg
            rec.observations.clear(); rec.objectives.clear()
            self.tdp_model.update_point_result(point_id, "FAILED", {}, "NOT_ASSESSED")

    def _on_batch_finished(self, batch_id: str, final_state: str, completed: int, failed: int) -> None:
        if batch_id != self._active_batch_id:
            return
        batch = self.workbench.jobs.batch_snapshot(batch_id)
        if batch:
            for item in batch.items:
                rec = next((r for r in self.study.records if r.point_id == item.item_id), None)
                if rec and rec.status in ("QUEUED", "RUNNING"):
                    rec.status = item.state.upper() if item.state in ("failed", "cancelled") else "FAILED"
                    rec.error_message = item.error or "No valid response receipt"
                    rec.feasibility = "NOT_ASSESSED"
        self._active_batch_id = None
        self._set_running(False)
        self._populate_study()
        valid = sum(r.status == "COMPLETED" for r in self.study.records)
        invalid = sum(r.status == "FAILED" for r in self.study.records)
        self.status_bar.showMessage(f"배치 완료 ({final_state}): 평가 완료 {valid}, 실패/응답 오류 {invalid}. 원자료: {self._active_store.root}")

    def closeEvent(self, event: QCloseEvent) -> None:
        """Safe shutdown: cancel active batch, kill solver process tree, cleanup resources."""
        if self._active_batch_id:
            self.workbench.jobs.cancel_batch(self._active_batch_id)
            self._active_batch_id = None
        if hasattr(self, "_viz_timer") and self._viz_timer.isActive():
            self._viz_timer.stop()
        for view in (getattr(self, "pareto_view", None), getattr(self, "tornado_view", None), getattr(self, "surface_view", None)):
            if view and hasattr(view, "canvas") and hasattr(view.canvas, "_draw_pending"):
                view.canvas._draw_pending = False
        if hasattr(self, "bridge"):
            self.bridge.blockSignals(True)
        if hasattr(self, "workbench"):
            if not self.workbench.close():
                event.ignore()
                return
        event.accept()

    def _reset_binding(self):
        self.current_deck_path = None
        self.template_deck = None
        self._execution_template = None
        self._source_revisions = {}
        self._point_sources = {}
        self.response_units = {}
        self._configured = False
        self._is_specification = False
        self._study_file = None

    def _on_new_study(self) -> None:
        if self._active_batch_id:
            return
        self._reset_binding()
        self.study = StudyManager()
        self._populate_study()

    def _on_load_demo(self) -> None:
        if self._active_batch_id:
            return
        self._reset_binding()
        self._is_specification = True
        self.study = self._create_demo_ge_bracket_study()
        self._populate_study()
        self.status_bar.showMessage("미계산 목표 사양입니다. GE 브래킷 모델이나 실측 결과를 포함하지 않습니다.")

    def _on_load_deck(self, file_path: str | Path | None = None) -> None:
        if self._active_batch_id:
            return
        if file_path is None:
            file_path, _ = QFileDialog.getOpenFileName(self, "CalculiX 해석 덱 열기", "", "CalculiX Input (*.inp)")
            if not file_path:
                return
        try:
            path = Path(file_path).resolve()
            if path.suffix.lower() != ".inp":
                raise ValueError("CalculiX .inp 파일을 선택하세요.")
            # Strict UTF-8 preserves invalid input as an error, not replacement bytes.
            original = path.read_bytes().decode("utf-8")
            deck = TemplateDeck.from_text(original, file_path=path)
            if not deck.token_names:
                raise ValueError("변수로 사용할 명시적 <TOKEN>이 없습니다. 원본 사본에 토큰을 추가하세요.")
            if len(set(t.lower() for t in deck.token_names)) != len(deck.token_names):
                raise ValueError("대소문자만 다른 토큰은 구별할 수 없습니다 (<T>/<t>).")
            with tempfile.TemporaryDirectory(prefix="caelab_mdo_check_") as temporary:
                folder = Path(temporary)
                manifest = snapshot_input(path, folder)
                for entry in manifest["files"][1:]:
                    if TemplateDeck.from_file(folder / entry["execution_path"]).token_names:
                        raise ValueError("INCLUDE 안의 파라미터 토큰은 아직 지원하지 않습니다. 토큰을 루트 덱에 두세요.")
                execution = TemplateDeck.from_text(self._execution_text(original, path, manifest))
            self._reset_binding()
            self.template_deck = deck
            self._execution_template = execution
            self.current_deck_path = path
            self._source_revisions = {f["source_path"]: f["original_sha256"] for f in manifest["files"]}
            self.study = StudyManager(name=f"Study: {path.name}")
            self._populate_study()
            self.status_bar.showMessage(f"{len(deck.token_names)} tokens: {', '.join(deck.token_names)}. Configure Study에서 범위·단위·응답을 지정하세요.")
        except (OSError, ValueError, UnicodeError) as exc:
            QMessageBox.warning(self, "덱 로드 오류", str(exc))

    def _on_configure(self):
        if self._active_batch_id:
            return
        if not self.template_deck or self._is_specification:
            QMessageBox.warning(self, "입력 필요", "먼저 실제 CalculiX .inp 템플릿을 여세요.")
            return
        dialog = StudyConfigDialog(self.template_deck.token_names, self.study, self.response_units, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._configure_study(*dialog.configuration())

    def _configure_study(self, space, objectives, constraints, response_units):
        if self._active_batch_id:
            raise ValueError("실행 중에는 스터디 설정을 변경할 수 없습니다.")
        if not self.template_deck or {v.id for v in space.variables} != {t.lower() for t in self.template_deck.token_names}:
            raise ValueError("변수와 덱 토큰이 일치하지 않습니다.")
        from caelab.mdo.variables import parse_unit
        for variable in space.variables:
            if not variable.unit:
                raise ValueError("변수 단위를 명시하세요.")
        required = {o.response_id for o in objectives} | {c.response_id for c in constraints}
        if not objectives or not required <= response_units.keys() or any(not response_units[k] for k in required):
            raise ValueError("모든 목적·제약 응답의 단위를 명시하세요.")
        for unit in response_units.values():
            parse_unit(unit)
        self.study = StudyManager(self.study.name, space=space, objectives=objectives, constraints=constraints)
        self.response_units = dict(response_units)
        self._point_sources.clear()
        self._configured = True
        self._populate_study()
        self.status_bar.showMessage("설정 저장됨. Generate DoE로 기준점과 LHS 설계점을 생성하세요.")

    def _on_generate_doe(self, n_samples=None, seed=None):
        if self._active_batch_id:
            return
        if not self._configured or not self.template_deck:
            QMessageBox.warning(self, "설정 필요", "실제 덱을 열고 Configure Study를 먼저 완료하세요.")
            return
        if n_samples is None:
            n_samples, ok = QInputDialog.getInt(self, "Generate DoE", "LHS samples (baseline is added separately)", 8, 1, 1000)
            if not ok:
                return
        if seed is None:
            seed, ok = QInputDialog.getInt(self, "Generate DoE", "Random seed", 42, 0, 2147483647)
            if not ok:
                return
        try:
            design = generate_design(self.study.space, DOEConfig(method="lhs", n_samples=n_samples, seed=seed))
            study = StudyManager(self.study.name, space=self.study.space, objectives=self.study.objectives, constraints=self.study.constraints)
            study.add_point({v.id: v.initial for v in study.space.variables}, name="Baseline")
            for point in design.points:
                study.add_point(point.values)
            for rec in study.records:
                rec.feasibility = "NOT_ASSESSED"
            self.study = study
            self._point_sources.clear()
            self._populate_study()
            self.status_bar.showMessage(f"Baseline + {n_samples} LHS points (seed {seed}); 아직 해석하지 않았습니다.")
        except (ValueError, ImportError) as exc:
            QMessageBox.warning(self, "DoE 생성 실패", str(exc))

    def _on_save_study(self, path=None):
        if self._active_batch_id:
            return
        if not self.template_deck or not self._configured:
            QMessageBox.warning(self, "저장 준비", "실제 덱과 스터디 설정이 필요합니다.")
            return
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "Save Study", str(self._study_file or self.current_deck_path.with_suffix(".mdo.json")), "MDO Study (*.json)")
            if not path:
                return
        try:
            atomic_json(Path(path), {
                "format": "caelab-mdo-study", "version": 1, "name": self.study.name,
                "deck_path": str(self.current_deck_path), "template": self.template_deck.original_text,
                "execution_template": self._execution_template.original_text,
                "source_revisions": self._source_revisions, "response_units": self.response_units,
                "variables": [asdict(v) for v in self.study.space.variables],
                "objectives": [asdict(o) for o in self.study.objectives], "constraints": [asdict(c) for c in self.study.constraints],
                "records": [asdict(r) for r in self.study.records], "point_sources": self._point_sources,
            })
            self._study_file = Path(path).resolve()
            self.status_bar.showMessage(f"Study saved: {self._study_file}")
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "저장 실패", str(exc))

    def _on_open_study(self, path=None):
        if self._active_batch_id:
            return
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Open Study", "", "MDO Study (*.json)")
            if not path:
                return
        state_fields = ("study", "current_deck_path", "template_deck", "_execution_template", "_source_revisions",
                        "_point_sources", "response_units", "_configured", "_is_specification", "_study_file")
        previous = {key: getattr(self, key) for key in state_fields}
        switched = False
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("format") != "caelab-mdo-study" or data.get("version") != 1:
                raise ValueError("지원하지 않는 스터디 형식입니다.")
            for key in ("name", "deck_path", "template", "execution_template"):
                if not isinstance(data[key], str) or not data[key]:
                    raise ValueError(f"{key}: 비어 있지 않은 문자열이 필요합니다.")
            for key in ("source_revisions", "response_units", "point_sources"):
                if not isinstance(data[key], dict):
                    raise ValueError(f"{key}: 사전이 필요합니다.")
            source_revisions = dict(data["source_revisions"])
            response_units = dict(data["response_units"])
            point_sources = dict(data["point_sources"])
            for name, digest in source_revisions.items():
                if not isinstance(name, str) or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
                    raise ValueError("입력 개정은 파일 경로와 SHA256이어야 합니다.")
            from caelab.mdo.variables import parse_unit
            for key, unit in response_units.items():
                if not isinstance(key, str) or not key or not unit:
                    raise ValueError("응답 이름과 단위를 명시하세요.")
                parse_unit(unit)
            space = VariableSpace(variables=tuple(ContinuousVariable(**v) for v in data["variables"]))
            objectives = [ObjectiveSpec(**o) for o in data["objectives"]]
            constraints = [ConstraintSpec(**c) for c in data["constraints"]]
            deck_path = Path(data["deck_path"])
            deck = TemplateDeck.from_text(data["template"], file_path=deck_path)
            execution_template = TemplateDeck.from_text(data["execution_template"])
            if any(not v.unit for v in space.variables):
                raise ValueError("변수 단위를 명시하세요.")
            required = {o.response_id for o in objectives} | {c.response_id for c in constraints}
            if not objectives or not required <= response_units.keys():
                raise ValueError("목적과 제약에 대응하는 응답 단위가 없습니다.")
            if set(v.id for v in space.variables) != {t.lower() for t in deck.token_names}:
                raise ValueError("저장된 변수와 템플릿이 일치하지 않습니다.")
            if not data["source_revisions"] or str(deck_path) not in data["source_revisions"]:
                raise ValueError("원본 입력 개정이 없습니다.")
            manager = StudyManager(data["name"], space=space, objectives=objectives, constraints=constraints)
            ids = set()
            for saved in data["records"]:
                rec = DesignPointRecord(**saved)
                space.encode(rec.physical_values)
                for values in (rec.observations, rec.objectives):
                    if not isinstance(values, dict) or any(not isinstance(k, str) or not isinstance(v, (int, float)) or not math.isfinite(v) for k, v in values.items()):
                        raise ValueError("응답과 목적 값은 유한한 숫자 사전이어야 합니다.")
                if rec.point_id in ids or rec.status not in ("PENDING", "COMPLETED", "FAILED", "CANCELLED", "STALE"):
                    raise ValueError("중복 또는 잘못된 설계점 기록입니다.")
                if not isinstance(rec.point_id, str) or not isinstance(rec.name, str) or not isinstance(rec.error_message, str) or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in (rec.duration_sec, rec.total_violation)):
                    raise ValueError("설계점 이름·시간·위반량이 잘못되었습니다.")
                ids.add(rec.point_id)
                manager.records.append(rec)
                manager._record_map[rec.point_id] = rec
            for point_id, evidence in point_sources.items():
                if point_id not in ids or not isinstance(evidence, dict):
                    raise ValueError("설계점 실행 근거가 잘못되었습니다.")
                if any(not isinstance(evidence.get(k), str) or not evidence[k] for k in ("study_manifest", "run_id", "workdir")):
                    raise ValueError("실행 근거 경로와 ID가 필요합니다.")
                if not isinstance(evidence.get("values"), dict) or not isinstance(evidence.get("files"), dict):
                    raise ValueError("실행 변수와 파일 해시 사전이 필요합니다.")
                space.encode(evidence["values"])
                if any(not isinstance(p, str) or not isinstance(h, str) or not re.fullmatch("[0-9a-f]{64}", h) for p, h in evidence["files"].items()):
                    raise ValueError("실행 근거의 파일 해시가 잘못되었습니다.")
            switched = True
            self._reset_binding()
            self.template_deck = deck
            self._execution_template = execution_template
            self.current_deck_path = deck_path
            self._source_revisions = source_revisions
            self._point_sources = point_sources
            self.response_units = response_units
            self.study = manager
            self._configured = True
            self._study_file = Path(path).resolve()
            for rec in self.study.records:
                if rec.status == "COMPLETED" and not self._point_is_verified(rec, require_feasible=False):
                    rec.status = "STALE"
                    rec.feasibility = "NOT_ASSESSED"
                    rec.objectives.clear(); rec.is_pareto = False
                    rec.error_message = "Saved input/result provenance is stale or unverified; re-evaluate before applying."
                elif rec.status == "COMPLETED":
                    assessment = evaluate_criteria(rec.observations, self.study.objectives, self.study.constraints)
                    rec.feasibility = assessment.constraints.feasibility.value
                    rec.total_violation = assessment.constraints.total_violation or 0.0
                    rec.objectives = {o.id: float(v) for o, v in zip(self.study.objectives, assessment.objectives)}
            self.study.update_pareto_front()
            self._populate_study()
            self.status_bar.showMessage("Study opened; 계산은 자동 실행하지 않습니다. 원본과 결과 파일은 저장 당시 경로를 참조합니다.")
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            if switched:
                for key, value in previous.items():
                    setattr(self, key, value)
                self._populate_study()
            QMessageBox.warning(self, "스터디 열기 실패", str(exc))

    def _on_toggle_theme(self) -> None:
        toggle_theme_mode()
        self.apply_theme()

    def apply_theme(self) -> None:
        mode = current_theme_mode()
        self.is_dark = mode == "dark"
        self.setStyleSheet(build_stylesheet(mode))
        self.act_theme.setText(theme_toggle_label())
        title_color = "#38bdf8" if self.is_dark else "#0284c7"
        self.tdp_lbl.setStyleSheet(f"font-weight: bold; font-size: 10pt; color: {title_color};")
        self.setup_widget.set_theme(self.is_dark)
        self.tdp_model.set_theme(self.is_dark)
        self.pareto_view.set_theme(self.is_dark)
        self.tornado_view.set_theme(self.is_dark)
        self.surface_view.set_theme(self.is_dark)
        for view in (self.pareto_view, self.surface_view):
            for text in view.ax.texts:
                if text.get_gid() == "mdo_empty":
                    text.set_color("#e2e8f0" if self.is_dark else "#0f172a")
