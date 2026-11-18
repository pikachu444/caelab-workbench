"""Tests for D07 MaterialStudyWindow Qt GUI (C9 & 7 GUI Design Laws)."""
from __future__ import annotations

import csv
import math
import os
from pathlib import Path
import re
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGroupBox,
    QLabel,
    QPushButton,
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
from apps.workbench_qt.material_study import (
    ExperimentImportDialog,
    MaterialStudyWindow,
    load_korean_font,
)


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

        # Central workspace >= 70% window area and left dock <= 30% width
        win_area = win.width() * win.height()
        central_area = win.central_workspace.width() * win.central_workspace.height()
        assert central_area >= 0.70 * win_area, (central_area, win_area)
        assert win.control_dock.width() <= 0.30 * win.width(), (win.control_dock.width(), win.width())

        # Combo boxes use AdjustToContents and status badges have no narrow max width
        for combo in win.findChildren(QComboBox):
            assert combo.sizeAdjustPolicy() == QComboBox.SizeAdjustPolicy.AdjustToContents
        for lbl in (win.badge_fit, win.badge_val, win.badge_hold, win.badge_const, win.loss_summary_label):
            assert lbl.maximumWidth() >= 320

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


def test_gui_prony_workflow_plots_tables_holdout_seal_and_theme_toggle(tmp_path: Path) -> None:
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

        # Parameter table updated to calibrated E0 ~ 100 MPa
        e0_val = float(win.param_table.item(0, 1).text())
        assert abs(e0_val - 100.0) / 100.0 <= 1e-2

        # Toggle Light -> Dark theme cleanly
        win.apply_theme("light")
        app.processEvents()
        assert win._theme_mode == "light"
        win.apply_theme("dark")
        app.processEvents()
        assert win._theme_mode == "dark"

        # 3) Explicit Confirmation Holdout
        win.on_confirm_holdout()
        assert win.wait_for_idle(timeout=60.0)
        assert win.badge_hold.text() == "보류: 적합"
        splits_in_summary = {
            win.channel_summary_table.item(r, 2).text()
            for r in range(win.channel_summary_table.rowCount())
        }
        assert "보류" in splits_in_summary

        # Switch plot selector across DMA & Relaxation views cleanly
        for idx in range(win.plot_selector.count()):
            win.plot_selector.setCurrentIndex(idx)
            app.processEvents()
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

    def _delayed_execute(self_adp, plan, request, *, data_root, study_id, cancel_event=None):
        deadline = time.monotonic() + 0.08
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise MaterialStudyError("CANCELLED", "slow.execute", {"reason": "USER_CANCEL"})
            time.sleep(0.005)
        return orig_execute(self_adp, plan, request, data_root=data_root, study_id=study_id, cancel_event=cancel_event)

    monkeypatch.undo()
    monkeypatch.setattr(prony_trs.PronyTRSAdapter, "execute", _delayed_execute)

    close_win = MaterialStudyWindow(study_path=study_path)
    close_win.show()
    close_win.on_optimize()
    deadline = time.monotonic() + 5.0
    while close_win._active_campaign_id is None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert close_win._busy_op is not None
    # Request close while optimization is active -> must ignore, trigger cancel, and close after finish
    close_win.close()
    assert not close_win._closed
    assert close_win.wait_for_idle(timeout=15.0)
    app.processEvents()
    assert close_win._closed
    # Lock must have been released so opening writable again succeeds immediately
    with CalibrationStudyService.open(study_path) as reopened:
        assert reopened.name == "점탄성 재료 보정 UI 테스트"
