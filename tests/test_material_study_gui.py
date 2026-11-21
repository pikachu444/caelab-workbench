"""Tests for D07 MaterialStudyWindow Qt GUI (C9 & 7 GUI Design Laws)."""
from __future__ import annotations

import csv
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import re
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import matplotlib
import numpy as np
import pytest

PySide6 = pytest.importorskip("PySide6")
from matplotlib.backend_bases import MouseEvent
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QStyleFactory,
    QTableWidget,
    QTreeWidget,
)

from caelab.materials import (
    CalibrationStudyService,
    ComparisonSpec,
    ExperimentChannelSpec,
    MaterialStudyError,
    ObservableSpec,
    QuantityUnitSpec,
)
from caelab.materials.adapters import prony_trs
from apps.workbench_qt import material_study
from apps.workbench_qt.material_study import (
    ExperimentImportDialog,
    MaterialStudyWindow,
    load_korean_font,
    main,
)


@pytest.fixture(scope="module", autouse=True)
def _restore_global_qt_and_matplotlib_state():
    """Preserve global QApplication style/font and matplotlib rcParams across modules."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(["pytest", "-platform", "offscreen"])
    prev_style = app.style().objectName() if app.style() is not None else ""
    prev_font = QFont(app.font())
    mpl_keys = ("font.family", "mathtext.fontset", "axes.unicode_minus")
    prev_rc = {k: list(matplotlib.rcParams[k]) if isinstance(matplotlib.rcParams[k], list) else matplotlib.rcParams[k] for k in mpl_keys}
    yield
    app = QApplication.instance()
    if app is not None:
        if prev_style and (restored := QStyleFactory.create(prev_style)) is not None:
            app.setStyle(restored)
        app.setFont(prev_font)
    matplotlib.rcParams.update(prev_rc)


def _get_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(["pytest", "-platform", "offscreen"])
    app.setStyle("Fusion")
    load_korean_font(app, 10)
    return app


def _wlf(T: float, Tref: float = 293.15, C1: float = 5.0, C2: float = 100.0) -> float:
    return 10.0 ** (-C1 * (T - Tref) / (C2 + T - Tref))


def _dma_exact(omega_r: np.ndarray, E0: float = 100.0, a: tuple[float, float] = (0.2, 0.3), tau: tuple[float, float] = (0.1, 10.0)):
    w = np.asarray(omega_r, dtype=float)
    e_inf = E0 * (1.0 - sum(a))
    stor = np.full_like(w, e_inf)
    loss = np.zeros_like(w)
    for ai, ti in zip(a, tau):
        wt2 = (w * ti) ** 2
        stor = stor + E0 * ai * wt2 / (1.0 + wt2)
        loss = loss + E0 * ai * (w * ti) / (1.0 + wt2)
    return stor, loss


def _relax_exact(t_r: np.ndarray, E0: float = 100.0, a: tuple[float, float] = (0.2, 0.3), tau: tuple[float, float] = (0.1, 10.0)):
    t = np.asarray(t_r, dtype=float)
    return E0 * (1.0 - sum(a) + a[0] * np.exp(-t / tau[0]) + a[1] * np.exp(-t / tau[1]))


def _write_csv(path: Path, headers: list[str], rows: list[list[float]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def _create_b3_study(root: Path, *, with_val_holdout: bool = True) -> Path:
    obs_stor = ObservableSpec(id="E_storage", quantity_kind="storage_modulus", unit="MPa", measure="viscoelastic", component_order=("E_storage",))
    obs_loss = ObservableSpec(id="E_loss", quantity_kind="loss_modulus", unit="MPa", measure="viscoelastic", component_order=("E_loss",))
    obs_rel = ObservableSpec(id="E_relax", quantity_kind="relaxation_modulus", unit="MPa", measure="viscoelastic", component_order=("E_relax",))

    freqs = np.logspace(-3, 3, 41)
    times = np.logspace(-3, 3, 41)
    study_path = root / "b3_gui.caemat.json"
    with CalibrationStudyService.create(study_path, name="점탄성 재료 보정 UI 테스트", model="B-VIS-TRS-1", adapter="1.0.0") as svc:
        comps: list[ComparisonSpec] = []
        temps = [(293.15, "train"), (303.15, "train"), (313.15, "train")]
        if with_val_holdout:
            temps.extend([(318.15, "validation"), (323.15, "holdout")])
        for temp_k, split in temps:
            csv_p = root / f"dma_{int(round(temp_k * 100))}.csv"
            stor, loss = _dma_exact(2.0 * math.pi * freqs * _wlf(temp_k))
            _write_csv(csv_p, ["frequency_Hz", "storage_MPa", "loss_MPa"], [[float(f), float(s), float(l)] for f, s, l in zip(freqs, stor, loss)])
            eid = f"dma_{int(round(temp_k * 100))}"
            svc.import_experiment(
                csv_p,
                experiment_id=eid,
                channels=[
                    ExperimentChannelSpec(observable_id="E_storage", observable=obs_stor, column_name="storage_MPa", source_unit="MPa", independent_column="frequency_Hz", independent_unit="Hz"),
                    ExperimentChannelSpec(observable_id="E_loss", observable=obs_loss, column_name="loss_MPa", source_unit="MPa", independent_column="frequency_Hz", independent_unit="Hz"),
                ],
                unit_specs={
                    "frequency_Hz": QuantityUnitSpec("Hz", "frequency"),
                    "storage_MPa": QuantityUnitSpec("MPa", "storage_modulus"),
                    "loss_MPa": QuantityUnitSpec("MPa", "loss_modulus"),
                },
                conditions={"temperature_K": temp_k},
                split=split,
                replicate_group=f"dma-{split}",
                expected_revision=svc.snapshot().document_revision,
            )
            comps.append(ComparisonSpec(id=f"{eid}_stor", experiment_id=eid, observable_id="E_storage", split=split, scale=100.0, weight=0.5, required_for_fit=(split == "train"), required_for_validation=(split != "train"), acceptance_limit=1e-4, sample_weights_policy="log10_trapezoidal"))
            comps.append(ComparisonSpec(id=f"{eid}_loss", experiment_id=eid, observable_id="E_loss", split=split, scale=100.0, weight=0.5, required_for_fit=(split == "train"), required_for_validation=(split != "train"), acceptance_limit=1e-4, sample_weights_policy="log10_trapezoidal"))

        rel_p = root / "rel_29315.csv"
        rel = _relax_exact(times)
        _write_csv(rel_p, ["time_s", "E_relax_MPa"], [[float(t), float(r)] for t, r in zip(times, rel)])
        svc.import_experiment(
            rel_p,
            experiment_id="rel_29315",
            channels=[ExperimentChannelSpec(observable_id="E_relax", observable=obs_rel, column_name="E_relax_MPa", source_unit="MPa", independent_column="time_s", independent_unit="s")],
            unit_specs={"time_s": QuantityUnitSpec("s", "time"), "E_relax_MPa": QuantityUnitSpec("MPa", "relaxation_modulus")},
            conditions={"temperature_K": 293.15},
            split="train",
            replicate_group="rel-train",
            expected_revision=svc.snapshot().document_revision,
        )
        comps.append(ComparisonSpec(id="rel_29315_c", experiment_id="rel_29315", observable_id="E_relax", split="train", scale=100.0, weight=1.0, required_for_fit=True, acceptance_limit=1e-4, sample_weights_policy="log10_trapezoidal"))
        svc.configure(
            parameters=svc.parameters(),
            comparisons=comps,
            assessment_policy={"selection_policy": {
                "mode": "manual_with_validation" if with_val_holdout else "training_only",
                "validation_comparison_ids": [c.id for c in comps if c.split == "validation"],
            }},
            numerical_settings={
                "wlf_provenance": {"source": "GUI test fixture generating inputs", "C1": 5.0, "C2": 100.0, "Tref": 293.15},
            },
            expected_revision=svc.snapshot().document_revision,
        )
    return study_path


def _collect_all_visible_texts(win: MaterialStudyWindow) -> list[str]:
    texts: list[str] = [win.windowTitle()]
    for w in win.findChildren(QLabel):
        texts.append(w.text())
    for w in win.findChildren(QPushButton):
        texts.append(w.text())
    for w in win.findChildren(QGroupBox):
        texts.append(w.title())
    for w in win.findChildren(QComboBox):
        for i in range(w.count()):
            texts.append(w.itemText(i))
    for w in win.findChildren(QTableWidget):
        for r in range(w.rowCount()):
            for c in range(w.columnCount()):
                item = w.item(r, c)
                if item is not None:
                    texts.append(item.text())
    for tree in win.findChildren(QTreeWidget):
        stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            texts.append(item.text(0))
            for k in range(item.childCount()):
                stack.append(item.child(k))
    return texts


def test_gui_layout_korean_font_design_laws_and_no_forbidden_strings(tmp_path: Path) -> None:
    """Verify 7 GUI design laws, Malgun Gothic font, 100-row CSV preview, and clean Korean strings."""
    app = _get_app()
    big_csv = tmp_path / "dma_150.csv"
    freqs = np.logspace(-3, 3, 150)
    stor, loss = _dma_exact(2.0 * math.pi * freqs)
    _write_csv(big_csv, ["frequency_Hz", "storage_MPa", "loss_MPa"], [[float(f), float(s), float(l)] for f, s, l in zip(freqs, stor, loss)])

    study_path = tmp_path / "layout_check.caemat.json"
    with CalibrationStudyService.create(study_path, name="레이아웃 점검 연구", model="B-VIS-TRS-1", adapter="1.0.0") as svc:
        svc.import_experiment(
            big_csv,
            experiment_id="dma_150",
            channels=[
                ExperimentChannelSpec(
                    observable_id="E_storage",
                    observable=ObservableSpec(id="E_storage", quantity_kind="storage_modulus", unit="MPa", measure="viscoelastic", component_order=("E_storage",)),
                    column_name="storage_MPa",
                    source_unit="MPa",
                    independent_column="frequency_Hz",
                    independent_unit="Hz",
                ),
            ],
            unit_specs={"frequency_Hz": QuantityUnitSpec("Hz", "frequency"), "storage_MPa": QuantityUnitSpec("MPa", "storage_modulus")},
            conditions={"temperature_K": 293.15},
            split="train",
            expected_revision=svc.snapshot().document_revision,
        )

    win = MaterialStudyWindow(study_path=study_path)
    try:
        win.resize(1440, 900)
        win.show()
        app.processEvents()

        # Malgun Gothic font loaded when present on Windows
        if Path("C:/Windows/Fonts/malgun.ttf").is_file():
            assert "malgun" in app.font().family().lower() or "맑은" in app.font().family()

        assert win.control_dock.width() <= int(win.width() * 0.30)
        assert win.central_workspace.width() >= int(win.width() * 0.70)
        # The plots and tools live in the same central work area.
        win_area = win.width() * win.height()
        central_area = win.central_workspace.width() * win.central_workspace.height()
        assert central_area >= 0.70 * win_area, (central_area, win_area)

        # Combo boxes use AdjustToContents.
        for combo in win.findChildren(QComboBox):
            assert combo.sizeAdjustPolicy() == QComboBox.SizeAdjustPolicy.AdjustToContents

        assert win.study_tree.textElideMode() == Qt.TextElideMode.ElideNone
        assert not win.study_tree.header().stretchLastSection()
        assert win.study_tree.header().sectionResizeMode(0) == QHeaderView.ResizeMode.ResizeToContents
        for root_index in range(win.study_tree.topLevelItemCount()):
            root = win.study_tree.topLevelItem(root_index)
            assert root.toolTip(0)
            for child_index in range(root.childCount()):
                child = root.child(child_index)
                assert child.toolTip(0) and not child.text(0).endswith("...")
        assert win.ax_curve.get_xlabel() == "주파수 (Hz)"
        assert win.ax_curve.get_ylabel() == "탄성계수 (MPa)"

        win.detail_tabs.setCurrentIndex(1)
        app.processEvents()
        for table in (win.param_table, win.constraint_table):
            metrics = QFontMetrics(table.font())
            for row in range(table.rowCount()):
                label = table.item(row, 0).text()
                assert metrics.horizontalAdvance(label) + 6 <= table.columnWidth(0), label

        # 150-row CSV previews at most 100 rows in the preview table
        assert win.exp_preview_table.rowCount() == 100

        # Zero forbidden disclaimers or raw hashes/UUIDs/internal IDs in default visible texts
        visible_texts = _collect_all_visible_texts(win)
        joined = "\n".join(visible_texts)
        for banned in ("공학 검증 미실시", "실물 재료 적합성 미검증", "참고용", "B-VIS-TRS-1", "not_assessed", "NOT_ASSESSED"):
            assert banned not in joined, f"Forbidden text found in UI: {banned}"
        assert not re.search(r"[0-9a-f]{64}", joined), "Raw SHA256 hash leaked to default UI"
        assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-", joined), "Raw UUID leaked to default UI"

        # ExperimentImportDialog unit preview check (100 rows max + AdjustToContents combos)
        dlg = ExperimentImportDialog(big_csv, parent=win)
        try:
            assert dlg.preview_table.rowCount() == 100
            assert any("101행 이상" in label.text() for label in dlg.findChildren(QLabel))
            for combo in dlg.findChildren(QComboBox):
                assert combo.sizeAdjustPolicy() == QComboBox.SizeAdjustPolicy.AdjustToContents
            payload = dlg.build_import_kwargs()
            assert payload["split"] in ("train", "validation", "holdout")
            assert payload["channels"]
        finally:
            dlg.close()
    finally:
        win.close()
        app.processEvents()


def test_gui_prony_workflow_plots_tables_holdout_seal_and_theme_toggle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run eval -> optimize -> theme toggle -> confirmation holdout through MaterialStudyWindow."""
    app = _get_app()
    study_path = _create_b3_study(tmp_path, with_val_holdout=True)

    win = MaterialStudyWindow(study_path=study_path)
    try:
        win.resize(1440, 900)
        win.show()
        app.processEvents()

        assert win.badge_hold.text() == "보류: 미확인"

        # 1) Single exploration evaluation
        win.on_evaluate()
        assert win.wait_for_idle(timeout=30.0)
        assert win.history_table.rowCount() == 1
        assert win.badge_hold.text() == "보류: 미확인"
        for r in range(win.channel_summary_table.rowCount()):
            assert win.channel_summary_table.item(r, 2).text() != "보류"

        # 2) Optimization campaign in worker thread
        win.on_optimize()
        assert win.wait_for_idle(timeout=240.0)
        assert win._active_eval_artifact is not None
        assert win._active_eval_artifact.assessment.objective["fit_loss"] <= 1e-8
        assert win.badge_fit.text() == "학습: 적합"
        assert win.badge_hold.text() == "보류: 미확인"
        assert len(win.ax_curve.lines) >= 4
        assert len(win.ax_resid.lines) >= 2

        # The calibrated point is shown separately from the initial specification.
        selected_exploration = win._selected_eval_id
        revision = win.service.snapshot().content_revision
        e0_default = win.service.parameters()[0].default
        e0_val = float(win.param_table.item(0, 7).text())
        assert abs(e0_val - 100.0) / 100.0 <= 1e-2
        assert float(win.param_table.item(0, 1).text()) == e0_default
        assert e0_default != e0_val
        assert not (win.param_table.item(0, 7).flags() & Qt.ItemFlag.ItemIsEditable)
        assert all(win.param_table.item(0, col).flags() & Qt.ItemFlag.ItemIsEditable for col in (1, 3, 4, 5, 6))
        assert not win._draft_dirty

        # Save and confirmation should never configure a clean draft.
        orig_configure = win.service.configure

        def unexpected_configure(**_kwargs):
            pytest.fail("clean draft configured again")

        win.service.configure = unexpected_configure
        win.on_save_study()
        assert win.service.snapshot().content_revision == revision

        # Toggle Light -> Dark theme cleanly
        win.apply_theme("light")
        app.processEvents()
        assert win._theme_mode == "light"
        light_icon = win.act_new.icon().pixmap(QSize(16, 16)).toImage()
        light_mpl = win._mpl_icons[0][0].icon().pixmap(QSize(24, 24)).toImage()
        assert "background: #f1f5f9; color: #0f172a" in win.styleSheet()
        win.apply_theme("dark")
        app.processEvents()
        assert win._theme_mode == "dark"
        assert win.act_new.icon().pixmap(QSize(16, 16)).toImage() != light_icon
        assert win._mpl_icons[0][0].icon().pixmap(QSize(24, 24)).toImage() != light_mpl
        assert "background: #1e293b; color: #e2e8f0" in win.styleSheet()

        # 3) Explicit Confirmation Holdout
        win.on_confirm_holdout()
        assert win.wait_for_idle(timeout=60.0)
        assert win.badge_hold.text() == "보류: 적합"
        assert win.service.snapshot().content_revision == revision
        confirmation = win._active_eval_artifact
        selection = json.loads((win.service.data_root / confirmation.selection_ref.path).read_text(encoding="utf-8"))
        assert selected_exploration in selection["source_evaluation_ids"]
        assert selection["content_revision"] == revision
        for axis in (win.ax_curve, win.ax_resid):
            assert len(axis.get_legend().get_texts()) == 10
        assert "점: 관측 / 선: 예측" in win.ax_curve.get_title()
        assert win.ax_curve.get_xlabel() == "주파수 (Hz)"
        training = [line for line in win.ax_resid.lines if line.get_label().startswith("학습")]
        assert len({line.get_color() for line in training}) == 3
        assert len({line.get_marker() for line in training}) >= 3
        assert len({line.get_linestyle() for line in training}) >= 3

        # Reselecting the exploration point reuses the same immutable selection.
        for row in range(win.history_table.rowCount()):
            if win.history_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == selected_exploration:
                win.history_table.selectRow(row)
                break
        assert win._active_eval_artifact.execution_phase == "exploration"
        win.on_confirm_holdout()
        assert win.wait_for_idle(timeout=60.0)
        assert win._active_eval_artifact.selection_ref == confirmation.selection_ref
        win.service.configure = orig_configure

        splits_in_summary = {
            win.channel_summary_table.item(r, 2).text()
            for r in range(win.channel_summary_table.rowCount())
        }
        assert "보류" in splits_in_summary

        # Switch plot selector across DMA & Relaxation views cleanly
        for idx in range(win.plot_selector.count()):
            win.plot_selector.setCurrentIndex(idx)
            app.processEvents()

        # Leave the confirmation selected, then revoke the proof on disk before
        # any history refresh. Plot redraw must not open either holdout array kind.
        holdout_id = "dma_32315"
        holdout_preview = win.preview_exp_combo.findData(holdout_id)
        win.preview_exp_combo.setCurrentIndex(holdout_preview)
        assert win.exp_preview_table.rowCount() > 0
        win.plot_selector.setCurrentIndex(0)
        assert any(line.get_label().startswith("보류") for line in win.ax_resid.lines)

        observed_entries = win.service._manifest["experiment_data"][holdout_id].values()
        holdout_observed_paths = {entry["values_ref"]["path"] for entry in observed_entries}
        protected = {
            ref["path"] for channel in win._active_eval_artifact.channel_results
            if channel["experiment_id"] == holdout_id
            for key, ref in channel.items() if key.endswith("_ref")
        }
        read_array = material_study._verify_and_read_channel_ref
        load_observed = material_study.load_experiment_channel_arrays

        def read_non_holdout(ref, *args, **kwargs):
            assert ref.path not in protected
            return read_array(ref, *args, **kwargs)

        def load_non_holdout(entry, **kwargs):
            assert entry["values_ref"]["path"] not in holdout_observed_paths
            return load_observed(entry, **kwargs)

        exposure_meta = win.service._manifest["evaluations"][win._selected_eval_id]
        exposure_ref = exposure_meta["holdout_exposure_ref"]
        (win.service.data_root / exposure_ref["path"]).unlink()
        with monkeypatch.context() as patch:
            patch.setattr(material_study, "_verify_and_read_channel_ref", read_non_holdout)
            patch.setattr(material_study, "load_experiment_channel_arrays", load_non_holdout)
            win._redraw_plots()
            assert not any(line.get_label().startswith("보류") for line in win.ax_curve.lines)
            assert not any(line.get_label().startswith("보류") for line in win.ax_resid.lines)
            assert win.exp_preview_table.rowCount() == 0
            assert "손상" in win.loss_summary_label.text()

        exposure_meta["holdout_exposed"] = False
        win._refresh_all()
        row = win.history_table.selectionModel().selectedRows()[0].row()
        assert win.history_table.item(row, 6).text() == "기록 손상"
        assert "손상" in win.loss_summary_label.text()
        assert win.badge_hold.text() == "보류: 미확인"
        assert win.channel_summary_table.rowCount() == 0
    finally:
        win.close()
        app.processEvents()


def test_gui_close_cancels_active_worker_and_readonly_without_pyvisco(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify read-only mode opens without pyvisco and closeEvent cancels active run + frees lock."""
    app = _get_app()
    study_path = _create_b3_study(tmp_path, with_val_holdout=False)

    # First evaluate and save once
    win = MaterialStudyWindow(study_path=study_path)
    try:
        win.on_evaluate()
        assert win.wait_for_idle(timeout=30.0)
        win.on_save_study()
    finally:
        win.close()
        app.processEvents()

    # Open read-only with missing pyvisco -> must still display saved artifacts & lock writes
    monkeypatch.setattr(
        prony_trs,
        "verify_pyvisco_sources",
        lambda: {
            "installed": False,
            "version": None,
            "source_match": False,
            "error_code": "BACKEND_UNAVAILABLE",
            "error": "no pyvisco",
            "loaded_source_checks": [],
        },
    )
    ro_win = MaterialStudyWindow(study_path=study_path, read_only=True)
    try:
        ro_win.resize(1440, 900)
        ro_win.show()
        app.processEvents()
        assert "(읽기 전용)" in ro_win.study_title_label.text()
        assert ro_win._active_eval_artifact is not None
        assert not ro_win.btn_import.isEnabled()
        assert not ro_win.btn_eval.isEnabled()
        assert not ro_win.btn_opt.isEnabled()
        assert not ro_win.btn_confirm.isEnabled()
        assert not ro_win.act_save.isEnabled()
    finally:
        ro_win.close()
        app.processEvents()

    # Active cancel on closeEvent using a delayed execute hook on B-VIS-TRS-1
    orig_execute = prony_trs.PronyTRSAdapter.execute
    cancelled = threading.Event()

    def _delayed_execute(self_adp, plan, request, *, data_root, study_id, cancel_event=None):
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                cancelled.set()
                raise MaterialStudyError("CANCELLED", "slow.execute", {"reason": "USER_CANCEL"})
            time.sleep(0.005)
        return orig_execute(self_adp, plan, request, data_root=data_root, study_id=study_id, cancel_event=cancel_event)

    monkeypatch.undo()
    monkeypatch.setattr(prony_trs.PronyTRSAdapter, "execute", _delayed_execute)

    close_win = MaterialStudyWindow(study_path=study_path)
    close_win.show()
    close_win.param_table.item(0, 1).setText("91")
    close_win.on_evaluate()
    assert close_win._busy_op is not None
    # The close can arrive before the standalone evaluation publishes its operation ID.
    close_win.close()
    assert not close_win._closed
    assert close_win.wait_for_idle(timeout=15.0)
    app.processEvents()
    assert close_win._closed
    # Lock must have been released so opening writable again succeeds immediately
    with CalibrationStudyService.open(study_path) as reopened:
        assert reopened.name == "점탄성 재료 보정 UI 테스트"
        assert cancelled.is_set() or any(ev["state"] == "cancelled" for ev in reopened.list_evaluations())


def test_gui_drafts_failed_history_and_selected_view_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _get_app()
    study_path = _create_b3_study(tmp_path, with_val_holdout=False)
    win = MaterialStudyWindow(study_path=study_path)
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, message: messages.append(message))
    try:
        win.on_evaluate()
        assert win.wait_for_idle(timeout=30)
        good_id = win._selected_eval_id
        assert win.ax_resid.get_legend() is not None

        def fail_execute(*_args, **_kwargs):
            raise MaterialStudyError("COVERAGE_INCOMPLETE", "test", {"reason": "failed input grid"})

        with monkeypatch.context() as patch:
            patch.setattr(prony_trs.PronyTRSAdapter, "execute", fail_execute)
            win.param_table.item(0, 1).setText("91")
            win.on_evaluate()
            assert win.wait_for_idle(timeout=30)
        assert win._selected_eval_id == good_id
        assert "실패" in win.status_bar.currentMessage() and "완료" not in win.status_bar.currentMessage()

        began = threading.Event()

        def wait_for_cancel(*args, cancel_event, **kwargs):
            began.set()
            if not cancel_event.wait(timeout=10):
                raise AssertionError("evaluation did not receive cancellation")
            raise MaterialStudyError("CANCELLED", "test")

        with monkeypatch.context() as patch:
            patch.setattr(prony_trs.PronyTRSAdapter, "execute", wait_for_cancel)
            win.param_table.item(0, 1).setText("92")
            win.on_evaluate()
            assert began.wait(timeout=10)
            win.on_cancel()
            assert win.wait_for_idle(timeout=30)
        assert win._selected_eval_id == good_id
        assert "취소" in win.status_bar.currentMessage() and "완료" not in win.status_bar.currentMessage()
        win.history_table.selectRow(1)
        failed_id = win._selected_eval_id
        assert failed_id != good_id
        failure = win.loss_summary_label.text()
        assert "관측 구간" in failure and "유효 0개" in failure and "누락" in failure
        assert all(win.channel_summary_table.item(row, 3).text() == "미확인"
                   for row in range(win.channel_summary_table.rowCount()))
        assert win.ax_resid.get_legend() is None

        revision = win.service.snapshot().content_revision
        default_cell = win.param_table.item(0, 1)
        default_cell.setText("1500")
        assert win._draft_dirty
        win.history_table.selectRow(0)
        win._refresh_all()
        assert win.param_table.item(0, 1).text() == "1500"
        win.on_save_study()
        assert "하한과 상한" in messages[-1]
        assert win.service.snapshot().content_revision == revision

        win.param_table.item(0, 1).setText("0")
        win.param_table.item(0, 4).setText("0")
        win.on_save_study()
        assert "0보다" in messages[-1]
        assert win.service.snapshot().content_revision == revision

        win.param_table.item(0, 1).setText("95")
        win.param_table.item(0, 4).setText("10")
        tau2_row = next(i for i, spec in enumerate(win.service.parameters()) if spec.id == "tau2")
        assert win.param_table.item(tau2_row, 8).flags() & Qt.ItemFlag.ItemIsEditable
        win.param_table.item(tau2_row, 3).setText("연동")
        win.param_table.item(tau2_row, 8).setText("50 * tau1")
        assert all(win.comparison_table.item(0, col).flags() & Qt.ItemFlag.ItemIsEditable for col in range(2, 8))
        win.comparison_table.item(0, 2).setText("120")
        win.comparison_table.item(0, 3).setText("0.25")
        win.comparison_table.item(0, 4).setText("사다리꼴")
        win.comparison_table.item(0, 5).setText("아니요")
        win.comparison_table.item(0, 7).setText("0.001")
        win.on_save_study()
        assert not win._draft_dirty
        assert win.service.parameters()[0].default == 95.0
        linked = win.service.parameters()[tau2_row]
        assert linked.role == "linked" and linked.expression == "50 * tau1" and linked.dependencies == ("tau1",)
        comparison = win.service.comparisons()[0]
        assert comparison.scale == 120.0 and comparison.weight == 0.25 and not comparison.required_for_fit
        assert comparison.sample_weights_policy == "trapezoidal" and comparison.acceptance_limit == 0.001

        # Save the view of a row other than the service's selected result.
        win.history_table.selectRow(1)
        assert win._selected_eval_id == failed_id
        win.on_save_study()
    finally:
        win.close()
        app.processEvents()

    reopened = MaterialStudyWindow(study_path=study_path, read_only=True)
    try:
        assert reopened._selected_eval_id == failed_id
        assert reopened._active_eval_artifact.evaluation_id == failed_id
    finally:
        reopened.close()


def test_gui_import_zero_time_weights_and_plot_units(tmp_path: Path) -> None:
    app = _get_app()
    csv_path = tmp_path / "relax_ms.csv"
    _write_csv(csv_path, ["time_ms", "E_relax_MPa"], [[0, 90], [1000, 65], [4000, 55]])
    dlg = ExperimentImportDialog(csv_path)
    try:
        dlg.ind_col_combo.setCurrentText("time_ms")
        dlg.val1_col_combo.setCurrentText("E_relax_MPa")
        dlg.ind_unit_combo.setCurrentIndex(1)
        dlg.dep_unit_combo.setCurrentText("kPa")
        default_payload = dlg.build_import_kwargs()
        assert default_payload["sample_weights_policy"] == "trapezoidal"
        assert default_payload["acceptance_limit"] is None
        dlg.weight_policy_combo.setCurrentIndex(dlg.weight_policy_combo.findData("uniform"))
        dlg.acceptance_limit_edit.setText("0.02")
        explicit_payload = dlg.build_import_kwargs()
        assert explicit_payload["sample_weights_policy"] == "uniform"
        assert explicit_payload["acceptance_limit"] == 0.02
    finally:
        dlg.close()

    study_path = _create_b3_study(tmp_path, with_val_holdout=False)
    with CalibrationStudyService.open(study_path) as service:
        service.import_experiment(
            default_payload["source"], experiment_id=default_payload["experiment_id"],
            channels=default_payload["channels"], unit_specs=default_payload["unit_specs"],
            column_mapping=default_payload["column_mapping"], conditions=default_payload["conditions"],
            split="train", expected_revision=service.snapshot().document_revision,
        )
    win = MaterialStudyWindow(study_path=study_path)
    try:
        assert win.ax_curve.get_xlabel() == "주파수 (Hz)"
        index = next(i for i in range(win.plot_selector.count()) if win.plot_selector.itemData(i) == ("exp", "relax_ms"))
        win.plot_selector.setCurrentIndex(index)
        app.processEvents()
        win._refresh_all()
        assert win.plot_selector.currentData() == ("exp", "relax_ms")
        assert win.ax_curve.get_xlabel() == "시간 (ms)"
        assert win.ax_curve.get_ylabel() == "탄성계수 (kPa)"
        assert len(win.ax_curve.lines) == 1
        assert win.ax_curve.get_xscale() == "linear"
    finally:
        win.close()


def test_gui_cli_opens_project_material_node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _get_app()
    path = tmp_path / "연구 폴더" / "calibration.json"
    with CalibrationStudyService.create(path, name="노드 재료 연구", model="B-VIS-TRS-1") as service:
        study_id = service.study_id
    project_file = tmp_path / "프로젝트.json"
    project_file.write_text(json.dumps({
        "schema_version": 4, "name": "프로젝트", "connections": [],
        "nodes": {"mat1": {
            "id": "mat1", "program_id": "caelab_material_study", "kind": "research",
            "files": {"material_study": {"path": path.relative_to(tmp_path).as_posix(), "study_id": study_id}},
        }},
    }), encoding="utf-8")

    def check_window():
        win = next(widget for widget in app.topLevelWidgets() if isinstance(widget, MaterialStudyWindow) and widget.isVisible())
        assert win.service.path == path
        assert win.service.read_only
        assert "노드 재료 연구" in win.study_title_label.text()
        win.close()
        return 0

    monkeypatch.setattr(app, "exec", check_window)
    assert main(["--project", str(project_file), "--node", "mat1", "--read-only"]) == 0
    with pytest.raises(SystemExit) as error:
        main(["--project", str(project_file)])
    assert error.value.code == 2


    replacement = tmp_path / "다른 연구" / "calibration.json"
    with CalibrationStudyService.create(replacement, name="다른 재료 연구", model="B-VIS-TRS-1") as service:
        replacement_id = service.study_id
    path.write_bytes(replacement.read_bytes())
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda _parent, _title, message: errors.append(message))
    assert main(["--project", str(project_file), "--node", "mat1"]) == 1
    assert "현재 파일의 연구가 일치하지 않습니다" in errors[-1]
    with CalibrationStudyService.open(path) as service:
        assert service.study_id == replacement_id


def test_gui_save_as_persists_draft_and_view_in_copy_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _get_app()
    original = _create_b3_study(tmp_path, with_val_holdout=False)
    copy_path = tmp_path / "다른 이름.caemat.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(copy_path), ""))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, message: warnings.append(message))

    win = MaterialStudyWindow(study_path=original)
    try:
        win.on_evaluate()
        assert win.wait_for_idle(timeout=30)
        first_id = win._selected_eval_id
        last = win.service.evaluate(
            {"E0": 95}, expected_revision=win.service.snapshot().document_revision, cache_policy="refresh"
        )
        assert first_id != last.evaluation_id
        win._refresh_all()
        win.history_table.selectRow(0)
        assert win._selected_eval_id == first_id
        original_document = original.read_bytes()

        win.param_table.item(0, 1).setText("10000")
        win.on_save_as_study()
        assert "하한과 상한" in warnings.pop()
        assert not copy_path.exists()

        win.param_table.item(0, 1).setText("91")
        win.comparison_table.item(0, 7).setText("0.001")
        win.on_save_as_study()
        assert win.wait_for_idle(timeout=30)
        assert not warnings
        assert not win._draft_dirty
        assert win.service.path == copy_path
        assert original.read_bytes() == original_document
        assert win.service.parameters()[0].default == 91
        assert win.service.comparisons()[0].acceptance_limit == 0.001
    finally:
        win.close()
        app.processEvents()

    reopened = MaterialStudyWindow(study_path=copy_path, read_only=True)
    try:
        assert reopened._selected_eval_id == first_id
        assert reopened.service.parameters()[0].default == 91
    finally:
        reopened.close()


def test_gui_save_as_reports_committed_copy_when_draft_write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _get_app()
    original = tmp_path / "original.caemat.json"
    copy_path = tmp_path / "copy.caemat.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(copy_path), ""))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, message: warnings.append(message))
    with CalibrationStudyService.create(original, name="원본", model="B-VIS-TRS-1"):
        pass
    original_document = original.read_bytes()
    win = MaterialStudyWindow(study_path=original)
    try:
        def fail_configure(**_kwargs):
            raise MaterialStudyError("EXTERNAL_MANIFEST_CHANGED", "configure")

        monkeypatch.setattr(win.service, "configure", fail_configure)
        win.param_table.item(0, 1).setText("91")
        win.on_save_as_study()
        assert win.wait_for_idle(timeout=30)
        assert win.service.path == copy_path
        assert win._draft_dirty
        assert win.param_table.item(0, 1).text() == "91"
        assert "복사본은 저장했습니다" in warnings[-1]
        assert original.read_bytes() == original_document
        assert win.service.parameters()[0].default == 90
    finally:
        win.close()
        app.processEvents()



def test_gui_curve_click_export_and_verified_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _get_app()
    study_path = _create_b3_study(tmp_path, with_val_holdout=False)
    win = MaterialStudyWindow(study_path=study_path)
    try:
        win.resize(1440, 900)
        win.show()
        app.processEvents()
        win.on_evaluate()
        assert win.wait_for_idle(timeout=30)
        active = win._active_eval_artifact
        exp_idx = next(i for i in range(win.plot_selector.count()) if win.plot_selector.itemData(i) == ("exp", "dma_29315"))
        win.plot_selector.setCurrentIndex(exp_idx)
        win.canvas.draw()
        for axis, suffix in ((win.ax_curve, "E'"), (win.ax_resid, "E''")):
            artist = next(line for line in axis.lines if line.get_label().endswith(suffix))
            x, y = artist.get_xydata()[20]
            sx, sy = axis.transData.transform((x, y))
            event = MouseEvent("button_press_event", win.canvas, sx, sy, button=1)
            win.canvas.callbacks.process("button_press_event", event)
            selected = win.channel_summary_table.selectionModel().selectedRows()
            assert len(selected) == 1
            assert win.channel_summary_table.item(selected[0].row(), 0).text() == "dma_29315"
            assert win.channel_summary_table.item(selected[0].row(), 1).text().endswith(f"({suffix})")
            assert "정규화 잔차" in win.loss_summary_label.text() and "채널 손실" in win.loss_summary_label.text()
            assert win.detail_tabs.currentIndex() == 3

        target = tmp_path / "export_report.json"
        messages = []
        monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_kw: (str(target), ""))
        monkeypatch.setattr(QMessageBox, "information", lambda _parent, _title, message: messages.append(message))
        win.on_export_report()
        assert win.wait_for_idle(timeout=30)
        report = json.loads(target.read_text(encoding="utf-8"))
        assert report["evaluation_id"] == active.evaluation_id
        assert report["parameters"]["E0"]["value"] == active.physical_parameters["E0"]
        assert messages and "보고서를 저장했습니다" in win.status_bar.currentMessage()

        entry = win.service._manifest["evaluations"][active.evaluation_id]
        artifact = win.service.data_root / entry["artifact_ref"]["path"]
        altered = json.loads(artifact.read_bytes())
        altered["assessment"]["assessment"]["fit_status"] = "pass"
        artifact.write_text(json.dumps(altered, ensure_ascii=False), encoding="utf-8")
        win._refresh_all()
        assert win.history_table.item(0, 2).text() == "기록 손상"
        assert win.history_table.item(0, 4).text() == "미확인"
        assert win.history_table.item(0, 3).text() == "미확인"
        assert "손상" in win.loss_summary_label.text()
    finally:
        win.close()
        app.processEvents()



def test_gui_validation_flag_and_unnamed_experiment_labels(tmp_path: Path) -> None:
    app = _get_app()
    path = _create_b3_study(tmp_path)
    with CalibrationStudyService.open(path) as service:
        template = service.experiments()[0].channels[0]
        unnamed = service.import_experiment(
            tmp_path / "dma_29315.csv",
            channels=[template],
            split="train",
            conditions={"temperature_K": 293.15},
            expected_revision=service.snapshot().document_revision,
        )
    win = MaterialStudyWindow(study_path=path)
    try:
        preview_idx = win.preview_exp_combo.findData(unnamed.experiment_id)
        assert win.preview_exp_combo.itemText(preview_idx).startswith("실험 7")
        assert unnamed.experiment_id not in win.preview_exp_combo.itemText(preview_idx)
        validation_row = next(i for i, spec in enumerate(win.service.comparisons()) if spec.split == "validation")
        validation_cell = win.comparison_table.item(validation_row, 6)
        assert validation_cell.flags() & Qt.ItemFlag.ItemIsEditable
        validation_cell.setText("아니요")
        win.on_save_study()
        assert not win.service.comparisons()[validation_row].required_for_validation
        assert not win._draft_dirty
        duplicate = MaterialStudyError("INVALID_CONFIGURATION", "import", {"reason": "DUPLICATE_EXPERIMENT_ID"})
        assert "같은 이름" in material_study._human_error(duplicate)
    finally:
        win.close()
        app.processEvents()


def test_gui_apply_keeps_unedited_float64_cells(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _get_app()
    study_path = _create_b3_study(tmp_path, with_val_holdout=False)
    with CalibrationStudyService.open(study_path) as svc:
        parameters = [
            replace(p, default=93.12345678901235, lower=10.123456789012346, upper=702.3456789012345)
            if p.id == "E0" else p
            for p in svc.parameters()
        ]
        comparisons = svc.comparisons()
        full_precision = replace(
            comparisons[0], scale=1.23456789, weight=1.0 / 3.0, acceptance_limit=None,
        )
        specified_limit = replace(comparisons[1], acceptance_limit=0.12345678901234567)
        svc.configure(
            parameters=parameters,
            comparisons=[full_precision, specified_limit, *comparisons[2:]],
            expected_revision=svc.snapshot().document_revision,
        )

    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: pytest.fail("apply rejected valid numeric edits"))
    win = MaterialStudyWindow(study_path=study_path)
    try:
        scale_cell = win.comparison_table.item(0, 2)
        weight_cell = win.comparison_table.item(0, 3)
        assert scale_cell.text() == "1.23457"
        assert weight_cell.text() == "0.333333"
        scale_cell.setText(f" {scale_cell.text()} ")
        row = next(
            i for i in range(win.param_table.rowCount())
            if win.param_table.item(i, 0).data(Qt.ItemDataRole.UserRole) == "E0"
        )
        win.param_table.item(row, 1).setText("98.76543219876543")
        win.comparison_table.item(1, 2).setText("123.45678901234567")
        win.on_apply_parameter_draft()
        e0 = next(p for p in win.service.parameters() if p.id == "E0")
        assert e0.default == 98.76543219876543
        assert e0.lower == 10.123456789012346
        assert e0.upper == 702.3456789012345
        assert win.service.comparisons()[0] == full_precision
        assert win.service.comparisons()[1] == replace(specified_limit, scale=123.45678901234567)

        win.comparison_table.item(1, 7).setText("")
        win.on_apply_parameter_draft()
        assert win.service.comparisons()[1].acceptance_limit is None
        assert next(p for p in win.service.parameters() if p.id == "E0") == e0
    finally:
        win.close()
        app.processEvents()


def test_gui_plot_preserves_duplicate_comparisons_and_withholds_corrupt_arrays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from caelab.materials import experiments

    app = _get_app()
    study_path = _create_b3_study(tmp_path, with_val_holdout=False)
    with CalibrationStudyService.open(study_path) as svc:
        comparisons = svc.comparisons()
        first = comparisons[0]
        second = replace(first, id="dma_29315_tight", scale=first.scale / 5)
        svc.configure(
            parameters=svc.parameters(), comparisons=[*comparisons, second],
            expected_revision=svc.snapshot().document_revision,
        )

    win = MaterialStudyWindow(study_path=study_path)
    try:
        win.resize(1440, 900)
        win.show()
        app.processEvents()
        win.on_evaluate()
        assert win.wait_for_idle(timeout=30)
        active = win._active_eval_artifact
        selector = win.plot_selector
        selector.setCurrentIndex(next(
            i for i in range(selector.count()) if selector.itemData(i) == ("exp", first.experiment_id)
        ))

        def select_pixel(x: float, y: float, expected_id: str) -> None:
            event = MouseEvent("button_press_event", win.canvas, x, y, button=1)
            win.canvas.callbacks.process("button_press_event", event)
            table = win.channel_summary_table
            selected = table.selectionModel().selectedRows()
            assert len(selected) == 1
            assert table.item(selected[0].row(), 0).data(Qt.ItemDataRole.UserRole) == expected_id
            assert expected_id in win.loss_summary_label.text()

        for comp in (first, second):
            win.canvas.draw()
            legend_text = next(t for t in win.ax_curve.get_legend().get_texts() if t.get_text().endswith(comp.id))
            bbox = legend_text.get_window_extent(win.canvas.get_renderer())
            select_pixel((bbox.x0 + bbox.x1) / 2, (bbox.y0 + bbox.y1) / 2, comp.id)

            curve = next(line for line in win.ax_curve.lines if line.get_label().endswith(comp.id))
            select_pixel(*win.ax_curve.transData.transform(curve.get_xydata()[20]), comp.id)

            artist = next(line for line in win.ax_resid.lines if line.get_label().endswith(comp.id))
            win.canvas.draw()
            select_pixel(*win.ax_resid.transData.transform(artist.get_xydata()[20]), comp.id)
            assert "정규화 잔차" in win.loss_summary_label.text()

        result = next(row for row in active.channel_results if row["comparison_id"] == second.id)
        original_reader = experiments.npy_format.read_array
        for field, change_bytes in (
            ("residuals_ref", lambda data: data[:-1] + bytes([data[-1] ^ 1])),
            ("predicted_on_obs_ref", lambda data: data + b"!"),
        ):
            rel = result[field]["path"]
            artifact = win.service.data_root / rel
            data = artifact.read_bytes()
            corrupted = change_bytes(data)

            def fail_on_unverified_load(stream, **kwargs):
                assert stream.getvalue() != corrupted, "corrupt array reached the NPY loader"
                return original_reader(stream, **kwargs)

            artifact.write_bytes(corrupted)
            with monkeypatch.context() as patch:
                patch.setattr(experiments.npy_format, "read_array", fail_on_unverified_load)
                win._redraw_plots()
            assert "손상" in win.status_bar.currentMessage()
            for axis in (win.ax_curve, win.ax_resid):
                labels = [line.get_label() for line in axis.lines]
                assert any(label.endswith(first.id) for label in labels)
                assert not any(label.endswith(second.id) for label in labels)

            artifact.write_bytes(data)
            win._redraw_plots()
            assert any(line.get_label().endswith(second.id) for line in win.ax_resid.lines)

        residual_path = win.service.data_root / result["residuals_ref"]["path"]
        expected_curve = next(line.get_xydata().copy() for line in win.ax_resid.lines
                              if line.get_label().endswith(second.id))
        original_read_bytes = Path.read_bytes
        reads = 0

        def replace_after_read(path):
            nonlocal reads
            raw = original_read_bytes(path)
            if path == residual_path:
                reads += 1
                path.write_bytes(b"replaced after verification read")
            return raw

        with monkeypatch.context() as patch:
            patch.setattr(Path, "read_bytes", replace_after_read)
            win._redraw_plots()
        assert reads == 1
        actual_curve = next(line.get_xydata() for line in win.ax_resid.lines
                            if line.get_label().endswith(second.id))
        np.testing.assert_array_equal(actual_curve, expected_curve)
    finally:
        win.close()
        app.processEvents()
