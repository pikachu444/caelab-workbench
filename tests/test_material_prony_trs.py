"""Tests for D05 B-VIS-TRS-1 2-term Prony + WLF adapter using actual pyvisco==2.1.3 (B3)."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np
import pytest

from caelab.materials import (
    CalibrationStudyService,
    ComparisonSpec,
    ExperimentChannelSpec,
    ObservableSpec,
    ParameterSpec,
    QuantityUnitSpec,
)
from caelab.materials.adapters import prony_trs
from caelab.materials.adapters.prony_trs import (
    PYVISCO_PRONY_SHA256,
    PYVISCO_SHIFT_SHA256,
    PronyTRSAdapter,
    verify_pyvisco_sources,
)
from caelab.materials.registry import MaterialStudyError
from caelab.mdo.optimize import OptimizationConfig


def _wlf(T: float, Tref: float = 293.15, C1: float = 5.0, C2: float = 100.0) -> float:
    return 10.0 ** (-C1 * (T - Tref) / (C2 + T - Tref))


def _relax_exact(t: np.ndarray | float, E0: float = 100.0, a: tuple[float, float] = (0.2, 0.3), tau: tuple[float, float] = (0.1, 10.0)):
    return E0 * (1.0 - sum(a) + a[0] * np.exp(-np.asarray(t) / tau[0]) + a[1] * np.exp(-np.asarray(t) / tau[1]))


def _dma_exact(omega: np.ndarray | float, E0: float = 100.0, a: tuple[float, float] = (0.2, 0.3), tau: tuple[float, float] = (0.1, 10.0)):
    w = np.asarray(omega, dtype=np.float64)
    z1 = w * tau[0]
    z2 = w * tau[1]
    stor = E0 * (1.0 - sum(a) + a[0] * z1 * z1 / (1.0 + z1 * z1) + a[1] * z2 * z2 / (1.0 + z2 * z2))
    loss = E0 * (a[0] * z1 / (1.0 + z1 * z1) + a[1] * z2 / (1.0 + z2 * z2))
    return stor, loss


def _write_csv(path: Path, headers: list[str], rows: list[list[Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


def test_pyvisco_pinned_hashes_and_b3_reference_values(tmp_path: Path) -> None:
    status = verify_pyvisco_sources()
    assert status["installed"] is True
    assert status["version"] == "2.1.3"
    assert status["source_match"] is True
    assert status["prony_sha256"] == PYVISCO_PRONY_SHA256
    assert status["shift_sha256"] == PYVISCO_SHIFT_SHA256

    adapter = PronyTRSAdapter()
    cap = adapter.capabilities()
    assert cap.level == "material_library"
    assert cap.supported is True
    assert cap.can_run is True
    assert cap.differentiable is True
    assert cap.supports_export is True
    assert cap.supports_apply is False

    # Independent reference values from B3
    from pyvisco import prony, shift

    er_1 = float(prony.E_relax_norm(np.array([1.0]), np.array([0.2, 0.3]), np.array([0.1, 10.0]))[0]) * 100.0
    freq_1 = prony.E_freq_norm(np.array([1.0]), np.array([0.2, 0.3]), np.array([0.1, 10.0])) * 100.0
    log_a = float(shift.WLF(313.15, 293.15, 5.0, 100.0))
    assert abs(er_1 - 77.14603053967404) < 1e-10
    assert np.allclose(freq_1, [79.9009900990099, 4.9504950495049505], atol=1e-10, rtol=0.0)
    assert abs((10.0 ** log_a) - 0.14677992676220694) < 1e-13

    # Test 1s strain ramp = 0.01 at Tref=293.15 K against B3 reference sigma=0.805486837893526 MPa
    ramp_csv = tmp_path / "ramp.csv"
    _write_csv(ramp_csv, ["time_s", "stress_MPa"], [[0.0, 0.0], [1.0, 0.805486837893526]])
    with CalibrationStudyService.create(tmp_path / "ramp.caemat.json", name="Ramp Check", model="B-VIS-TRS-1") as svc:
        obs_sig = ObservableSpec(id="stress_xx", quantity_kind="stress", unit="MPa", measure="cauchy", component_order=("stress_xx",))
        snap = svc.snapshot()
        svc.import_experiment(
            ramp_csv,
            experiment_id="ramp_1s",
            channels=[ExperimentChannelSpec(observable_id="stress_xx", observable=obs_sig, column_name="stress_MPa", source_unit="MPa", independent_column="time_s", independent_unit="s")],
            unit_specs={"time_s": QuantityUnitSpec("s", "time"), "stress_MPa": QuantityUnitSpec("MPa", "stress")},
            conditions={"temperature_K": 293.15, "strain_history": [0.0, 0.01]},
            split="train",
            expected_revision=snap.document_revision,
        )
        snap = svc.snapshot()
        svc.configure(
            parameters=svc.parameters(),
            comparisons=[ComparisonSpec(id="c_ramp", experiment_id="ramp_1s", observable_id="stress_xx", split="train", scale=1.0, required_for_fit=True)],
            expected_revision=snap.document_revision,
        )
        ev = svc.evaluate({"E0": 100.0, "a1": 0.2, "a2": 0.3, "tau1": 0.1, "tau2": 10.0}, expected_revision=svc.snapshot().document_revision)
        art = svc.evaluation(ev.evaluation_id)
        assert art.channel_results[0]["max_abs_error"] < 1e-10
        assert art.prediction_bundle is not None
        art.prediction_bundle.validate_level("material_library")
        assert art.prediction_bundle.run_ids == ()
        assert all(ch.source_result_refs == () for ep in art.prediction_bundle.experiments for ch in ep.channels)


def test_b3_synthetic_regression_dma_and_relaxation_slsqp(tmp_path: Path) -> None:
    """Full B3 synthetic calibration (DMA + Relaxation, SLSQP seed=17) + Confirmation Holdout Seal."""
    obs_stor = ObservableSpec(id="E_storage", quantity_kind="storage_modulus", unit="MPa", measure="viscoelastic", component_order=("E_storage",))
    obs_loss = ObservableSpec(id="E_loss", quantity_kind="loss_modulus", unit="MPa", measure="viscoelastic", component_order=("E_loss",))
    obs_rel = ObservableSpec(id="E_relax", quantity_kind="relaxation_modulus", unit="MPa", measure="viscoelastic", component_order=("E_relax",))

    freqs = np.logspace(-3, 3, 61)
    times = np.logspace(-3, 3, 61)
    truth = {"E0": 100.0, "a1": 0.2, "a2": 0.3, "tau1": 0.1, "tau2": 10.0}

    with CalibrationStudyService.create(
        tmp_path / "b3_prony.caemat.json",
        name="B3 Prony+WLF Regression",
        model="B-VIS-TRS-1",
        adapter="1.0.0",
    ) as svc:
        comps: list[ComparisonSpec] = []
        for temp_k, split in ((293.15, "train"), (303.15, "train"), (313.15, "train"), (318.15, "validation"), (323.15, "holdout")):
            p = tmp_path / f"dma_{int(round(temp_k * 100))}.csv"
            stor, loss = _dma_exact(2.0 * math.pi * freqs * _wlf(temp_k))
            _write_csv(
                p,
                ["frequency_Hz", "storage_MPa", "loss_MPa"],
                [[float(f), float(s), float(l)] for f, s, l in zip(freqs, stor, loss)],
            )
            eid = f"dma_{int(round(temp_k * 100))}"
            svc.import_experiment(
                p,
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
                source_kind="synthetic_arithmetic",
                expected_revision=svc.snapshot().document_revision,
            )
            comps.append(
                ComparisonSpec(
                    id=f"{eid}_stor",
                    experiment_id=eid,
                    observable_id="E_storage",
                    split=split,
                    scale=100.0,
                    weight=0.5,
                    required_for_fit=(split == "train"),
                    required_for_validation=(split != "train"),
                    acceptance_limit=1e-6,
                    sample_weights_policy="log10_trapezoidal",
                )
            )
            comps.append(
                ComparisonSpec(
                    id=f"{eid}_loss",
                    experiment_id=eid,
                    observable_id="E_loss",
                    split=split,
                    scale=100.0,
                    weight=0.5,
                    required_for_fit=(split == "train"),
                    required_for_validation=(split != "train"),
                    acceptance_limit=1e-6,
                    sample_weights_policy="log10_trapezoidal",
                )
            )

        for temp_k, split in ((293.15, "train"), (313.15, "train"), (323.15, "holdout")):
            p = tmp_path / f"rel_{int(round(temp_k * 100))}.csv"
            rel = _relax_exact(times / _wlf(temp_k))
            _write_csv(
                p,
                ["time_s", "E_relax_MPa"],
                [[float(t), float(r)] for t, r in zip(times, rel)],
            )
            eid = f"rel_{int(round(temp_k * 100))}"
            svc.import_experiment(
                p,
                experiment_id=eid,
                channels=[
                    ExperimentChannelSpec(observable_id="E_relax", observable=obs_rel, column_name="E_relax_MPa", source_unit="MPa", independent_column="time_s", independent_unit="s"),
                ],
                unit_specs={
                    "time_s": QuantityUnitSpec("s", "time"),
                    "E_relax_MPa": QuantityUnitSpec("MPa", "relaxation_modulus"),
                },
                conditions={"temperature_K": temp_k},
                split=split,
                replicate_group=f"rel-{split}",
                source_kind="synthetic_arithmetic",
                expected_revision=svc.snapshot().document_revision,
            )
            comps.append(
                ComparisonSpec(
                    id=f"{eid}_rel",
                    experiment_id=eid,
                    observable_id="E_relax",
                    split=split,
                    scale=100.0,
                    weight=1.0,
                    required_for_fit=(split == "train"),
                    required_for_validation=(split != "train"),
                    acceptance_limit=1e-6,
                    sample_weights_policy="log10_trapezoidal",
                )
            )

        svc.configure(
            parameters=svc.parameters(),
            comparisons=comps,
            assessment_policy={"selection_policy": {"mode": "training_only", "validation_comparison_ids": []}},
            user_constraints=(),
            expected_revision=svc.snapshot().document_revision,
        )

        camp = svc.optimize(
            OptimizationConfig(
                algorithm="slsqp",
                seed=17,
                max_evaluations=1500,
                max_iterations=200,
                ftol=1e-10,
                finite_difference_step=1e-6,
            ),
            expected_revision=svc.snapshot().document_revision,
        )
        assert camp.state == "completed"
        assert camp.termination_reason == "CONVERGED"
        assert camp.selected_ids

        best_explore = svc.evaluation(camp.selected_ids[0])
        assert best_explore.assessment.assessment["holdout_status"] == "not_assessed"
        assert best_explore.assessment.objective["fit_loss"] <= 1e-10
        for pname, expected_val in truth.items():
            rel_err = abs(float(best_explore.physical_parameters[pname]) - expected_val) / expected_val
            assert rel_err <= 1e-3, (pname, best_explore.physical_parameters[pname], expected_val)

        # Confirmation evaluation with refresh
        conf_rec = svc.evaluate(
            best_explore.physical_parameters,
            expected_revision=svc.snapshot().document_revision,
            cache_policy="refresh",
            execution_phase="confirmation",
        )
        conf_art = svc.evaluation(conf_rec.evaluation_id)
        assert conf_art.cache_hit is False
        assert conf_art.selection_ref is not None
        assert conf_art.assessment.objective["eligible"] is True
        assert conf_art.assessment.objective["fit_loss"] <= 1e-10
        assert conf_art.assessment.assessment["fit_status"] == "pass"
        assert conf_art.assessment.assessment["validation_status"] == "pass"
        assert conf_art.assessment.assessment["holdout_status"] == "pass"
        assert conf_art.assessment.assessment["constraint_status"] == "FEASIBLE"
        assert conf_art.assessment.assessment["identifiability_status"] == "pass"

        val_losses = [c["loss"] for c in conf_art.channel_results if c["split"] == "validation"]
        hold_losses = [c["loss"] for c in conf_art.channel_results if c["split"] == "holdout"]
        assert val_losses and max(val_losses) <= 1e-10
        assert hold_losses and max(hold_losses) <= 1e-10
        assert max(c["max_abs_error"] for c in conf_art.channel_results) <= 1e-3

        # Export report JSON (never claiming native solver card / Prep apply)
        report_dest = tmp_path / "prony_export.json"
        exp_rec = svc.export(conf_rec.evaluation_id, report_dest, expected_revision=svc.snapshot().document_revision)
        assert exp_rec.supported_solver_identity is None
        exported_json = json.loads(report_dest.read_text(encoding="utf-8"))
        assert exported_json["supports_native_card"] is False
        assert exported_json["supports_apply"] is False
        assert exported_json["provenance"]["loaded_source_checks"][0]["loaded_file_sha256"] == PYVISCO_PRONY_SHA256

        with pytest.raises(MaterialStudyError) as apply_exc:
            svc.prepare_apply(conf_rec.evaluation_id, {"target": "prep"}, expected_revision=svc.snapshot().document_revision)
        assert apply_exc.value.code == "UNSUPPORTED_ATOMIC_APPLY"


def test_prony_preserves_arbitrary_user_csv_grid_and_wider_bounds(tmp_path: Path) -> None:
    """Arbitrary non-61-point grid is preserved without resampling; wider E0 bounds work via configure()."""
    irregular_freqs = [0.05, 0.25, 1.2, 5.0, 18.0, 75.0, 320.0]  # 7 points, non-61
    stor, loss = _dma_exact(2.0 * math.pi * np.array(irregular_freqs), E0=3500.0)
    csv_path = tmp_path / "high_mod.csv"
    _write_csv(
        csv_path,
        ["frequency_Hz", "storage_MPa", "loss_MPa"],
        [[f, s, l] for f, s, l in zip(irregular_freqs, stor, loss)],
    )

    obs_stor = ObservableSpec(id="E_storage", quantity_kind="storage_modulus", unit="MPa", measure="viscoelastic", component_order=("E_storage",))
    with CalibrationStudyService.create(tmp_path / "wide.caemat.json", name="Wide Bounds", model="B-VIS-TRS-1") as svc:
        svc.import_experiment(
            csv_path,
            experiment_id="dma_7pt",
            channels=[ExperimentChannelSpec(observable_id="E_storage", observable=obs_stor, column_name="storage_MPa", source_unit="MPa", independent_column="frequency_Hz", independent_unit="Hz")],
            unit_specs={"frequency_Hz": QuantityUnitSpec("Hz", "frequency"), "storage_MPa": QuantityUnitSpec("MPa", "storage_modulus")},
            conditions={"temperature_K": 293.15},
            split="train",
            expected_revision=svc.snapshot().document_revision,
        )
        wider_params = [
            ParameterSpec(id="E0", name="E0", unit="MPa", lower=100.0, upper=10000.0, default=3500.0, transform="log", role="free")
            if p.id == "E0"
            else p
            for p in svc.parameters()
        ]
        svc.configure(
            parameters=wider_params,
            comparisons=[ComparisonSpec(id="c_7", experiment_id="dma_7pt", observable_id="E_storage", split="train", scale=1000.0, required_for_fit=True)],
            expected_revision=svc.snapshot().document_revision,
        )
        ev = svc.evaluate({"E0": 3500.0, "a1": 0.2, "a2": 0.3, "tau1": 0.1, "tau2": 10.0}, expected_revision=svc.snapshot().document_revision)
        art = svc.evaluation(ev.evaluation_id)
        assert art.prediction_bundle is not None
        ch = art.prediction_bundle.experiments[0].channels[0]
        assert ch.coverage["expected"] == 7
        assert ch.coverage["valid"] == 7
        assert art.assessment.objective["fit_loss"] < 1e-20


def test_prony_domain_failures_and_rank_counterexamples(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify failure_cases.json B-VIS-TRS-1 domain checks and VIS_TREF_RANK."""
    obs_rel = ObservableSpec(id="E_relax", quantity_kind="relaxation_modulus", unit="MPa", measure="viscoelastic", component_order=("E_relax",))
    times = np.logspace(-2, 2, 11)
    rel_vals = _relax_exact(times)
    rel_csv = tmp_path / "tref_only.csv"
    _write_csv(rel_csv, ["time_s", "E_relax_MPa"], [[float(t), float(r)] for t, r in zip(times, rel_vals)])

    with CalibrationStudyService.create(tmp_path / "fail.caemat.json", name="Failure Modes", model="B-VIS-TRS-1") as svc:
        svc.import_experiment(
            rel_csv,
            experiment_id="rel_tref",
            channels=[ExperimentChannelSpec(observable_id="E_relax", observable=obs_rel, column_name="E_relax_MPa", source_unit="MPa", independent_column="time_s", independent_unit="s")],
            unit_specs={"time_s": QuantityUnitSpec("s", "time"), "E_relax_MPa": QuantityUnitSpec("MPa", "relaxation_modulus")},
            conditions={"temperature_K": 293.15},
            split="train",
            expected_revision=svc.snapshot().document_revision,
        )
        # Configure wider bounds so invalid Prony sum / order / WLF pole reach evaluate()
        wide_params = []
        for p in svc.parameters():
            if p.id in ("a1", "a2"):
                wide_params.append(ParameterSpec(id=p.id, name=p.name, unit=p.unit, lower=1e-8, upper=0.9, default=0.2 if p.id == "a1" else 0.3, transform="linear", role="free"))
            elif p.id in ("tau1", "tau2"):
                wide_params.append(ParameterSpec(id=p.id, name=p.name, unit=p.unit, lower=0.001, upper=1000.0, default=0.1 if p.id == "tau1" else 10.0, transform="log", role="free"))
            elif p.id in ("C1", "C2"):
                wide_params.append(ParameterSpec(id=p.id, name=p.name, unit=p.unit, lower=p.lower, upper=p.upper, default=p.default, transform="linear", role="free"))
            else:
                wide_params.append(p)
        svc.configure(
            parameters=wide_params,
            comparisons=[ComparisonSpec(id="c_rel", experiment_id="rel_tref", observable_id="E_relax", split="train", scale=100.0, required_for_fit=True)],
            expected_revision=svc.snapshot().document_revision,
        )

        # VIS_TREF_RANK: Tref-only dataset with C1, C2 free -> not_identifiable!
        ev_rank = svc.evaluate({"E0": 100.0, "a1": 0.2, "a2": 0.3, "tau1": 0.1, "tau2": 10.0, "C1": 5.0, "C2": 100.0}, expected_revision=svc.snapshot().document_revision)
        art_rank = svc.evaluation(ev_rank.evaluation_id)
        assert art_rank.assessment.objective["eligible"] is True
        assert art_rank.assessment.assessment["identifiability_status"] == "not_identifiable"
        assert art_rank.identifiability["rank"] < art_rank.identifiability["parameter_count"]
        assert "C1" in art_rank.identifiability["unidentifiable_parameters"]

        # VIS_SUM: a1 + a2 > 0.95 -> failed with INVALID_PARAMETER / PRONY_FRACTION_SUM and fit_loss=None
        ev_sum = svc.evaluate({"a1": 0.8, "a2": 0.3}, expected_revision=svc.snapshot().document_revision)
        assert ev_sum.state == "failed"
        art_sum = svc.evaluation(ev_sum.evaluation_id)
        assert art_sum.assessment.objective["eligible"] is False
        assert art_sum.assessment.objective["fit_loss"] is None
        assert "INVALID_PARAMETER" in art_sum.assessment.objective["reason_codes"]
        assert art_sum.diagnostics[0]["details"]["reason"] == "PRONY_FRACTION_SUM"

        # VIS_ORDER: tau2 < 2*tau1 -> failed with INVALID_PARAMETER / PRONY_TERM_ORDER
        ev_ord = svc.evaluate({"tau1": 10.0, "tau2": 0.1}, expected_revision=svc.snapshot().document_revision)
        assert ev_ord.state == "failed"
        art_ord = svc.evaluation(ev_ord.evaluation_id)
        assert art_ord.assessment.objective["fit_loss"] is None
        assert art_ord.diagnostics[0]["details"]["reason"] == "PRONY_TERM_ORDER"

    # VIS_WLF_POLE & SHIFT_RANGE_EXCEEDED
    adapter = PronyTRSAdapter()
    from caelab.materials.contracts import ExperimentSpec
    from caelab.artifact_refs import ArtifactRefV1, ProducerIdentity

    dummy_ref = ArtifactRefV1(id="s", role="source_csv", path="sources/s.csv", sha256="a" * 64, size=10, format_id="text/csv", producer=ProducerIdentity(id="t", version="1"))
    exp_pole = ExperimentSpec(experiment_id="e_pole", experiment_revision="r1", split="train", source_ref=dummy_ref, channels=(), conditions={"temperature_K": 193.15})
    with pytest.raises(MaterialStudyError) as pole_exc:
        adapter.validate(adapter.descriptor(), {"E0": 100.0, "a1": 0.2, "a2": 0.3, "tau1": 0.1, "tau2": 10.0, "C1": 5.0, "C2": 100.0, "Tref": 293.15}, [exp_pole])
    assert pole_exc.value.code == "INVALID_PARAMETER"
    assert pole_exc.value.details["reason"] == "WLF_SINGULAR_DENOMINATOR"

    exp_oob = ExperimentSpec(experiment_id="e_oob", experiment_revision="r2", split="train", source_ref=dummy_ref, channels=(), conditions={"temperature_K": 360.0})
    with pytest.raises(MaterialStudyError) as oob_exc:
        adapter.validate(adapter.descriptor(), {"E0": 100.0, "a1": 0.2, "a2": 0.3, "tau1": 0.1, "tau2": 10.0, "C1": 5.0, "C2": 100.0, "Tref": 293.15}, [exp_oob])
    assert oob_exc.value.code == "INVALID_PARAMETER"
    assert oob_exc.value.details["reason"] == "SHIFT_RANGE_EXCEEDED"

    # VIS_PHASE_UNKNOWN & VIS_TIME_DUPLICATE
    bad_dup_csv = tmp_path / "dup_time.csv"
    _write_csv(bad_dup_csv, ["time_s", "E_relax_MPa"], [[0.0, 100.0], [1.0, 80.0], [1.0, 79.0], [2.0, 60.0]])
    with CalibrationStudyService.create(tmp_path / "csv_fail.caemat.json", name="CSV Rejects", model="B-VIS-TRS-1") as svc2:
        with pytest.raises(MaterialStudyError) as dup_exc:
            svc2.import_experiment(
                bad_dup_csv,
                experiment_id="dup_exp",
                channels=[ExperimentChannelSpec(observable_id="E_relax", observable=obs_rel, column_name="E_relax_MPa", source_unit="MPa", independent_column="time_s", independent_unit="s")],
                unit_specs={"time_s": QuantityUnitSpec("s", "time"), "E_relax_MPa": QuantityUnitSpec("MPa", "relaxation_modulus")},
                conditions={"temperature_K": 293.15},
                split="train",
                expected_revision=svc2.snapshot().document_revision,
            )
        assert dup_exc.value.code == "DATA_RECIPE_INCOMPATIBLE"
        assert dup_exc.value.details["reason"] == "UNDECLARED_DUPLICATE_TIME"

        amp_csv = tmp_path / "amp.csv"
        _write_csv(amp_csv, ["frequency_Hz", "amp_MPa", "phase_rad"], [[1.0, 80.0, 0.06]])
        obs_stor = ObservableSpec(id="E_storage", quantity_kind="storage_modulus", unit="MPa", measure="viscoelastic", component_order=("E_storage",))
        with pytest.raises(MaterialStudyError) as phase_exc:
            svc2.import_experiment(
                amp_csv,
                experiment_id="amp_exp",
                channels=[ExperimentChannelSpec(observable_id="E_storage", observable=obs_stor, column_name="amp_MPa", source_unit="MPa", independent_column="frequency_Hz", independent_unit="Hz")],
                unit_specs={"frequency_Hz": QuantityUnitSpec("Hz", "frequency"), "amp_MPa": QuantityUnitSpec("MPa", "storage_modulus")},
                conditions={"temperature_K": 293.15},
                split="train",
                conversion_recipe={"representation": "amplitude_phase", "phase_convention": "unknown", "amplitude_column": "amp_MPa", "phase_column": "phase_rad"},
                expected_revision=svc2.snapshot().document_revision,
            )
        assert phase_exc.value.code == "MEASURE_MISMATCH"
        assert phase_exc.value.details["reason"] == "PHASE_CONVENTION_UNKNOWN"

    # Hash mismatch detection
    monkeypatch.setattr(
        prony_trs,
        "verify_pyvisco_sources",
        lambda: {"installed": True, "version": "2.1.3", "source_match": False, "error_code": "PYVISCO_SOURCE_HASH_MISMATCH", "loaded_source_checks": []},
    )
    bad_cap = adapter.capabilities()
    assert bad_cap.can_run is False
    assert "PYVISCO_SOURCE_HASH_MISMATCH" in bad_cap.reason_codes


def test_pyvisco_rejects_wrong_version_even_when_both_hashes_match(monkeypatch):
    monkeypatch.setattr(prony_trs.importlib.metadata, "version", lambda _: "2.1.4")
    status = verify_pyvisco_sources()
    assert all(check["source_match"] for check in status["loaded_source_checks"])
    assert status["error_code"] == "PYVISCO_VERSION_MISMATCH"
    assert not PronyTRSAdapter().capabilities().can_run


def test_measured_multitemperature_wlf_fixed_values_require_bound_provenance(tmp_path):
    csv_file = tmp_path / "dma.csv"
    _write_csv(csv_file, ["frequency_Hz", "storage_MPa"], [[1, 70], [2, 80]])
    obs = ObservableSpec(
        id="E_storage", quantity_kind="storage_modulus", unit="MPa", measure="viscoelastic",
        component_order=("E_storage",),
    )
    ch = ExperimentChannelSpec(
        observable_id=obs.id, observable=obs, column_name="storage_MPa", source_unit="MPa",
        independent_column="frequency_Hz", independent_unit="Hz",
    )
    with CalibrationStudyService.create(tmp_path / "measured.caemat.json", name="DMA", model="B-VIS-TRS-1") as svc:
        comps = []
        for eid, split, temperature in (("run A", "train", 293.15), ("run_A", "holdout", 303.15)):
            svc.import_experiment(
                csv_file, experiment_id=eid, channels=[ch], split=split, conditions={"temperature_K": temperature},
                expected_revision=svc.snapshot().document_revision,
            )
            comps.append(ComparisonSpec(
                id=eid, experiment_id=eid, observable_id=obs.id, split=split, scale=10,
                required_for_fit=split == "train", required_for_validation=split == "holdout", acceptance_limit=10,
            ))

        def configure(settings):
            return svc.configure(
                parameters=svc.parameters(), comparisons=comps, numerical_settings=settings,
                expected_revision=svc.snapshot().document_revision,
            )

        rev = configure({})
        with pytest.raises(MaterialStudyError, match="WLF_PROVENANCE_REQUIRED"):
            svc.evaluate(expected_revision=rev.document_revision, compute_identifiability=False)
        with pytest.raises(MaterialStudyError, match="WLF_PROVENANCE_REQUIRED"):
            svc.optimize(expected_revision=rev.document_revision)
        fixed = {p.id: p.default for p in svc.parameters() if p.id in ("C1", "C2", "Tref")}
        rev = configure({"wlf_provenance": {**fixed, "C1": 6, "source": "lab data report 12"}})
        with pytest.raises(MaterialStudyError, match="WLF_PROVENANCE_REQUIRED"):
            svc.evaluate(expected_revision=rev.document_revision, compute_identifiability=False)

        provenance = {**fixed, "source": "lab data report 12"}
        rev = configure({"wlf_provenance": provenance})
        receipt = svc.evaluate(
            expected_revision=rev.document_revision, cache_policy="refresh",
            execution_phase="confirmation", compute_identifiability=False,
        )
        result = svc.evaluation(receipt.evaluation_id)
        assert result.state == "completed"
        assert dict(result.numerical_settings["wlf_provenance"]) == provenance
        refs = [exp.channels[0].values_ref.path for exp in result.prediction_bundle.experiments]
        assert len(set(refs)) == 2
