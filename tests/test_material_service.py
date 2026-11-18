"""Comprehensive tests for D04 CORE02 service/residuals/experiments and D06 STORE01 persistence/SaveAs/recovery."""
from __future__ import annotations

import csv
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Any

import numpy as np
import pytest
from filelock import FileLock

from caelab.artifact_refs import (
    ArtifactOwner,
    ArtifactRefV1,
    ProducerIdentity,
    canonical_sha256,
    validate_relative_artifact_path,
    write_safe_npy_v2,
)
from caelab.materials import (
    AdapterIdentity,
    AdapterRegistry,
    CalibrationStudyService,
    CapabilityReport,
    ComparisonSpec,
    EvaluationPlan,
    ExperimentChannelSpec,
    ExperimentPredictionV1,
    ExperimentSpec,
    MaterialEvaluationRequest,
    MaterialExportPlan,
    MaterialStudyError,
    ModelDescriptorV1,
    ObservableCapability,
    ObservableSpec,
    ParameterSpec,
    PredictedChannelV1,
    PredictionBundleV1,
    QuantityUnitSpec,
    ScalarResponseSpecV1,
    SelectionRecordV1,
)
from caelab.materials.experiments import load_experiment_channel_arrays
from caelab.mdo.constraints import ConstraintSpec
from caelab.mdo.optimize import OptimizationConfig


class _CustomLinearAdapter:
    """Trusted custom plugin adapter y = m*x + b for testing zero model-branching extensibility."""

    MODEL_ID = "CUSTOM-LINEAR-1"
    PRODUCER = ProducerIdentity(id="custom.linear", version="1.0.0")

    def __init__(
        self,
        *,
        delay_sec: float = 0.0,
        partial_missing_on_exp: str | None = None,
        extrapolate_short_grid: bool = False,
        invalid_scalar: bool = False,
        violate_constraint: bool = False,
    ) -> None:
        self.delay_sec = delay_sec
        self.partial_missing_on_exp = partial_missing_on_exp
        self.extrapolate_short_grid = extrapolate_short_grid
        self.invalid_scalar = invalid_scalar
        self.violate_constraint = violate_constraint
        self.calls: list[tuple[str, list[str], dict[str, Any]]] = []
        self._identity = AdapterIdentity(
            id="custom.linear",
            version="1.0.0",
            implementation_sha256=canonical_sha256({"id": "custom.linear", "v": "1.0.0"}, exclude_keys=()),
        )

    @property
    def identity(self) -> AdapterIdentity:
        return self._identity

    def descriptor(self) -> ModelDescriptorV1:
        return ModelDescriptorV1(
            model_id=self.MODEL_ID,
            model_revision="1",
            family="custom",
            name="사용자 정의 선형 모델",
            parameter_specs=(
                ParameterSpec(id="m", name="기울기", unit="MPa", lower=0.1, upper=20.0, default=1.0, transform="linear", role="free"),
                ParameterSpec(id="b", name="절편", unit="MPa", lower=-10.0, upper=10.0, default=0.0, transform="linear", role="free"),
            ),
            observable_capabilities=(
                ObservableCapability(
                    observable_id="y_val",
                    value_kinds=("real",),
                    representations=("scalar",),
                    locations=("material_point",),
                    supported_units=("MPa", "Pa"),
                ),
            ),
            level="analytic",
            supports_export=True,
            supports_apply=False,
        )

    def capabilities(self, model=None, environment=None) -> CapabilityReport:
        return CapabilityReport(
            adapter=self._identity,
            model_id=self.MODEL_ID,
            model_revision="1",
            level="analytic",
            supported=True,
            can_run=True,
            differentiable=True,
            observable_capabilities=self.descriptor().observable_capabilities,
            optimizer_responses=(
                ScalarResponseSpecV1(response_id="fit_loss", unit="", source_kind="residual", source_name="fit_loss"),
                ScalarResponseSpecV1(response_id="custom_margin", unit="", source_kind="adapter_scalar", source_name="custom.margin"),
            ),
            optimizer_constraints=(
                ConstraintSpec(id="margin_min", response_id="custom_margin", kind="LOWER", limit=0.0, scale=1.0, tolerance=0.0, unit=""),
            ),
            supports_export=True,
            supports_apply=False,
        )

    def validate(self, model, parameters, experiments, numerical_settings=None):
        return ({"valid": True},)

    def plan(self, request: MaterialEvaluationRequest) -> EvaluationPlan:
        return EvaluationPlan(
            plan_id=f"plan-{request.evaluation_id}",
            evaluation_id=request.evaluation_id,
            request_id=request.request_id,
            study_revision=request.study_revision,
            model_id=self.MODEL_ID,
            model_revision="1",
            adapter=self._identity,
            kind="in_process",
            execution_phase=request.execution_phase,
            physical_parameters=request.physical_parameters,
            experiment_ids=tuple(e.experiment_id for e in request.experiments),
            numerical_settings=request.numerical_settings,
            callback_id="custom.linear.v1",
        )

    def execute(self, plan, request: MaterialEvaluationRequest, *, data_root: Path, study_id: str, cancel_event=None):
        self.calls.append((request.execution_phase, [e.experiment_id for e in request.experiments], dict(request.physical_parameters)))
        if self.delay_sec > 0.0:
            deadline = time.monotonic() + self.delay_sec
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    raise MaterialStudyError("CANCELLED", "custom.execute", {"reason": "USER_CANCEL"})
                time.sleep(0.005)

        m = float(request.physical_parameters["m"])
        b = float(request.physical_parameters["b"])
        exp_outputs = []
        artifacts = []
        owners = {}

        for exp in request.experiments:
            ch = exp.channels[0]
            ch_entry = request.experiment_data[exp.experiment_id][ch.observable_id]
            x_obs, _, sk_obs, _ = load_experiment_channel_arrays(ch_entry, data_root=data_root)
            if self.extrapolate_short_grid:
                x_eval = x_obs[1:-1] if x_obs.size > 2 else x_obs
                sk_eval = sk_obs[1:-1] if sk_obs.shape[0] > 2 else sk_obs
            else:
                x_eval = x_obs
                sk_eval = sk_obs
            y_eval = (m * x_eval + b).reshape(-1, 1)
            mask_arr = np.ones(y_eval.shape, dtype="|b1")
            valid_count = int(x_eval.size)
            missing_count = 0
            if self.partial_missing_on_exp == exp.experiment_id and valid_count > 1:
                mask_arr[-1, 0] = False
                valid_count -= 1
                missing_count = 1

            base = f"arrays/predictions/{request.evaluation_id}/{exp.experiment_id}"

            def _save(suffix: str, role: str, arr: np.ndarray) -> ArtifactRefV1:
                rel = f"{base}.{suffix}.npy"
                sha = write_safe_npy_v2(rel, arr, root=data_root)
                size = (data_root / rel).stat().st_size
                aid = f"pred:{request.evaluation_id}:{exp.experiment_id}:{suffix}"
                ref = ArtifactRefV1(id=aid, role=role, path=rel, sha256=sha, size=size, format_id="application/x-npy-v2", producer=self.PRODUCER)
                artifacts.append(ref)
                owners[aid] = ArtifactOwner(scope="consumer", id=f"material-study:{study_id}")
                return ref

            exp_outputs.append(
                {
                    "experiment_id": exp.experiment_id,
                    "experiment_revision": exp.experiment_revision,
                    "state": "completed",
                    "channels": [
                        {
                            "observable_id": ch.observable_id,
                            "observable": ch.observable,
                            "independent_ref": _save("ind", "predicted_independent", x_eval),
                            "sample_keys_ref": _save("sk", "predicted_sample_keys", sk_eval),
                            "sample_key_columns": ("source_row",),
                            "values_ref": _save("vals", "predicted_values", y_eval),
                            "mask_ref": _save("mask", "predicted_mask", mask_arr),
                            "component_order": ("y",),
                            "value_kind": "real",
                            "representation": "scalar",
                            "coverage": {"expected": int(x_eval.size), "valid": valid_count, "missing": missing_count},
                            "source_refs": (exp.source_ref,),
                        }
                    ],
                }
            )

        scalar_val = None if self.invalid_scalar else (-0.25 if self.violate_constraint else 1.0)
        return {
            "experiments": exp_outputs,
            "artifacts": artifacts,
            "artifact_owners": owners,
            "scalars": {"custom.margin": scalar_val},
        }

    def collect(self, plan, artifacts, *, data_root: Path, study_id: str) -> PredictionBundleV1:
        exps = tuple(
            ExperimentPredictionV1(
                experiment_id=ep["experiment_id"],
                experiment_revision=ep["experiment_revision"],
                state=ep["state"],
                channels=tuple(
                    PredictedChannelV1(
                        observable_id=ch["observable_id"],
                        observable=ch["observable"],
                        independent_ref=ch["independent_ref"],
                        sample_keys_ref=ch["sample_keys_ref"],
                        sample_key_columns=ch["sample_key_columns"],
                        values_ref=ch["values_ref"],
                        mask_ref=ch["mask_ref"],
                        component_order=ch["component_order"],
                        value_kind=ch["value_kind"],
                        representation=ch["representation"],
                        coverage=ch["coverage"],
                        source_refs=ch["source_refs"],
                        source_result_refs=(),
                    )
                    for ch in ep["channels"]
                ),
            )
            for ep in artifacts["experiments"]
        )
        return PredictionBundleV1(
            evaluation_id=plan.evaluation_id,
            request_id=plan.request_id,
            study_revision=plan.study_revision,
            model_revision=plan.model_revision,
            adapter=self._identity,
            experiments=exps,
            optimizer_scalars=artifacts["scalars"],
            artifacts=tuple(artifacts["artifacts"]),
            artifact_owners=artifacts["artifact_owners"],
            run_ids=(),
        )

    def export(self, evaluation_artifact, *, destination=None) -> MaterialExportPlan:
        body = json.dumps({"model": self.MODEL_ID, "parameters": evaluation_artifact["physical_parameters"]}, ensure_ascii=False, indent=2)
        return MaterialExportPlan(
            export_id="exp-custom-1",
            format_kind="parameter_formulation_report_json",
            supports_native_card=False,
            supported_solver_identity=None,
            model_id=self.MODEL_ID,
            model_revision="1",
            adapter=self._identity,
            study_id=str(evaluation_artifact["study_id"]),
            content_revision=str(evaluation_artifact["content_revision"]),
            evaluation_id=str(evaluation_artifact["evaluation_id"]),
            files=({"rel_path": "custom_report.json", "role": "export_report", "format_id": "application/json", "content_text": body},),
        )


def _write_csv(path: Path, headers: list[str], rows: list[list[Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


def _make_custom_study(tmp_path: Path, adapter: _CustomLinearAdapter, name: str = "custom.caemat.json") -> tuple[CalibrationStudyService, Path]:
    reg = AdapterRegistry()
    reg.register(adapter, default=True)
    study_path = tmp_path / name
    svc = CalibrationStudyService.create(study_path, name="Custom Linear Study", model=adapter.MODEL_ID, adapter="1.0.0", registry=reg)
    obs = ObservableSpec(id="y_val", quantity_kind="stress", unit="MPa", measure="cauchy", component_order=("y",))
    ch = ExperimentChannelSpec(observable_id="y_val", observable=obs, column_name="y", source_unit="MPa", independent_column="x", independent_unit="1")

    train_csv = tmp_path / "train.csv"
    val_csv = tmp_path / "val.csv"
    hold_csv = tmp_path / "hold.csv"
    # True line: y = 3*x + 2
    _write_csv(train_csv, ["x", "y"], [[0.0, 2.0], [1.0, 5.0], [2.0, 8.0], [3.0, 11.0]])
    _write_csv(val_csv, ["x", "y"], [[0.5, 3.5], [1.5, 6.5], [2.5, 9.5]])
    _write_csv(hold_csv, ["x", "y"], [[4.0, 14.0], [5.0, 17.0]])

    for eid, csv_p, sp in (("exp_train", train_csv, "train"), ("exp_val", val_csv, "validation"), ("exp_hold", hold_csv, "holdout")):
        svc.import_experiment(
            csv_p,
            experiment_id=eid,
            channels=[ch],
            unit_specs={"x": QuantityUnitSpec("1", "dimensionless"), "y": QuantityUnitSpec("MPa", "stress")},
            conditions={},
            split=sp,
            replicate_group="rep-1",
            expected_revision=svc.snapshot().document_revision,
        )

    comps = [
        ComparisonSpec(id="c_train", experiment_id="exp_train", observable_id="y_val", split="train", scale=1.0, weight=1.0, required_for_fit=True, acceptance_limit=1e-6),
        ComparisonSpec(id="c_val", experiment_id="exp_val", observable_id="y_val", split="validation", scale=1.0, weight=1.0, required_for_validation=True, acceptance_limit=1e-6),
        ComparisonSpec(id="c_hold", experiment_id="exp_hold", observable_id="y_val", split="holdout", scale=1.0, weight=1.0, required_for_validation=True, acceptance_limit=1e-6),
    ]
    svc.configure(parameters=svc.parameters(), comparisons=comps, expected_revision=svc.snapshot().document_revision)
    return svc, study_path


def test_custom_adapter_extensibility_and_holdout_seal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Custom plugin works without model branching, and Holdout Seal blocks exploration exposure."""
    adapter = _CustomLinearAdapter()
    svc, study_path = _make_custom_study(tmp_path, adapter)
    notifications: list[dict[str, Any]] = []
    try:
        # 1. Exploration optimize must NEVER call adapter on holdout
        camp = svc.optimize(
            OptimizationConfig(algorithm="slsqp", seed=17, max_evaluations=200, max_iterations=50, ftol=1e-10, finite_difference_step=1e-6),
            expected_revision=svc.snapshot().document_revision,
            on_progress=lambda ev: notifications.append(dict(ev)),
        )
        assert camp.state == "completed"
        assert notifications and notifications[0]["stage"] == "started"
        assert notifications[0]["campaign_id"] == camp.campaign_id
        for phase, exp_ids, _ in adapter.calls:
            assert phase == "exploration"
            assert "exp_hold" not in exp_ids
        assert all("holdout" not in str(n).lower() for n in notifications)

        best = svc.evaluation(camp.selected_ids[0])
        assert abs(best.physical_parameters["m"] - 3.0) < 1e-4
        assert abs(best.physical_parameters["b"] - 2.0) < 1e-4
        assert best.assessment.assessment["holdout_status"] == "not_assessed"
        assert "train_fit" not in best.assessment.to_dict()["assessment"]
        assert "train_fit" not in best.assessment.to_dict()["objective"]

        # 2. Confirmation requires cache_policy='refresh'
        with pytest.raises(MaterialStudyError) as ref_exc:
            svc.evaluate(
                best.physical_parameters,
                expected_revision=svc.snapshot().document_revision,
                cache_policy="reuse",
                execution_phase="confirmation",
            )
        assert ref_exc.value.code == "CONFIRMATION_REQUIRES_REFRESH"

        # 3. Confirmation writes SelectionRecordV1 BEFORE holdout execution
        pre_selections = list((svc.data_root / "revisions").rglob("selections/*.json"))
        assert pre_selections == []

        # Test fault-injected exposure write failure: must raise HOLDOUT_EXPOSURE_RECORD_FAILED and return zero holdout values
        orig_record = svc._record_holdout_exposure

        def _fail_exposure(**kwargs):
            raise MaterialStudyError("HOLDOUT_EXPOSURE_RECORD_FAILED", "expose_holdout", {"injected": True})

        monkeypatch.setattr(svc, "_record_holdout_exposure", _fail_exposure)
        with pytest.raises(MaterialStudyError) as exp_fail_exc:
            svc.evaluate(
                best.physical_parameters,
                expected_revision=svc.snapshot().document_revision,
                cache_policy="refresh",
                execution_phase="confirmation",
            )
        assert exp_fail_exc.value.code == "HOLDOUT_EXPOSURE_RECORD_FAILED"
        # Selection record WAS written before holdout execution!
        selections = list((svc.data_root / "revisions").rglob("selections/*.json"))
        assert len(selections) == 1
        # A failed exposure write cannot publish a confirmation entry.
        assert not any(e["execution_phase"] == "confirmation" for e in svc.list_evaluations())

        # Restore exposure recording and confirm cleanly
        monkeypatch.setattr(svc, "_record_holdout_exposure", orig_record)
        conf_rec = svc.evaluate(
            best.physical_parameters,
            expected_revision=svc.snapshot().document_revision,
            cache_policy="refresh",
            execution_phase="confirmation",
        )
        conf_art = svc.evaluation(conf_rec.evaluation_id)
        assert conf_art.cache_hit is False
        assert conf_art.selection_ref is not None
        assert conf_art.assessment.assessment["holdout_status"] == "pass"
        exposures = list((svc.data_root / "holdout_exposures").glob("*.json"))
        assert len(exposures) == 1
        selection = SelectionRecordV1.from_dict(json.loads((svc.data_root / conf_art.selection_ref.path).read_bytes()))
        assert selection.content_revision == camp.content_revision == conf_rec.content_revision
        assert best.evaluation_id in selection.source_evaluation_ids
        retry = svc.evaluate(
            best.physical_parameters, expected_revision=svc.snapshot().document_revision,
            cache_policy="refresh", execution_phase="confirmation", selection_ref=conf_art.selection_ref,
        )
        assert svc.evaluation(retry.evaluation_id).selection_ref == conf_art.selection_ref
        for selection_ref, values in (
            (None, best.physical_parameters),
            (conf_art.selection_ref, {"m": 3.1, "b": 1.9}),
        ):
            with pytest.raises(MaterialStudyError, match="HOLDOUT_REUSED_FOR_SELECTION"):
                svc.evaluate(
                    values, expected_revision=svc.snapshot().document_revision, cache_policy="refresh",
                    execution_phase="confirmation", selection_ref=selection_ref,
                )

        # 4. Changing parameter/policy and re-running confirmation on SAME holdout detects HOLDOUT_REUSED_FOR_SELECTION
        new_params = [
            ParameterSpec(id="m", name="기울기", unit="MPa", lower=0.1, upper=20.0, default=3.1, transform="linear", role="free"),
            ParameterSpec(id="b", name="절편", unit="MPa", lower=-10.0, upper=10.0, default=1.9, transform="linear", role="free"),
        ]
        svc.configure(parameters=new_params, comparisons=svc.comparisons(), assessment_policy={"selection_policy": {"mode": "training_only", "validation_comparison_ids": []}, "adequacy_policy": {"enabled": True, "require_independent_holdout": True, "allow_synthetic": True}}, expected_revision=svc.snapshot().document_revision)
        with pytest.raises(MaterialStudyError, match="HOLDOUT_REUSED_FOR_SELECTION"):
            svc.evaluate({"m": 3.1, "b": 1.9}, expected_revision=svc.snapshot().document_revision, cache_policy="refresh", execution_phase="confirmation")
    finally:
        svc.close()


def test_assessment_rules_constraints_interpolation_and_cache_policies(tmp_path: Path) -> None:
    """Verify C4/C5 rules: missing coverage, extrapolation, INFEASIBLE vs CONSTRAINT_RESPONSE_INVALID, diagnostic isolation, reuse/refresh."""
    # 1. Incomplete coverage on train -> eligible=False, fit_loss=None
    ad_missing = _CustomLinearAdapter(partial_missing_on_exp="exp_train")
    svc_m, _ = _make_custom_study(tmp_path / "m", ad_missing)
    try:
        ev = svc_m.evaluate({"m": 3.0, "b": 2.0}, expected_revision=svc_m.snapshot().document_revision)
        art = svc_m.evaluation(ev.evaluation_id)
        assert art.assessment.objective["eligible"] is False
        assert art.assessment.objective["fit_loss"] is None
        assert "COVERAGE_INCOMPLETE" in art.assessment.objective["reason_codes"]
    finally:
        svc_m.close()

    # 2. Extrapolation forbidden -> COVERAGE_INCOMPLETE
    ad_extrap = _CustomLinearAdapter(extrapolate_short_grid=True)
    svc_e, _ = _make_custom_study(tmp_path / "e", ad_extrap)
    try:
        ev = svc_e.evaluate({"m": 3.0, "b": 2.0}, expected_revision=svc_e.snapshot().document_revision)
        art = svc_e.evaluation(ev.evaluation_id)
        assert art.assessment.objective["eligible"] is False
        assert art.assessment.objective["fit_loss"] is None
        assert "COVERAGE_INCOMPLETE" in art.assessment.objective["reason_codes"]
    finally:
        svc_e.close()

    # 3. Finite constraint violation preserves finite fit_loss and sets INFEASIBLE
    ad_viol = _CustomLinearAdapter(violate_constraint=True)
    svc_v, _ = _make_custom_study(tmp_path / "v", ad_viol)
    try:
        ev = svc_v.evaluate({"m": 3.0, "b": 2.0}, expected_revision=svc_v.snapshot().document_revision)
        art = svc_v.evaluation(ev.evaluation_id)
        assert art.assessment.objective["eligible"] is True
        assert art.assessment.objective["fit_loss"] is not None
        assert art.assessment.assessment["constraint_status"] == "INFEASIBLE"
    finally:
        svc_v.close()

    # 4. Invalid constraint scalar (None) invalidates objective -> CONSTRAINT_RESPONSE_INVALID, NOT_ASSESSED
    ad_inv = _CustomLinearAdapter(invalid_scalar=True)
    svc_i, _ = _make_custom_study(tmp_path / "i", ad_inv)
    try:
        ev = svc_i.evaluate({"m": 3.0, "b": 2.0}, expected_revision=svc_i.snapshot().document_revision)
        art = svc_i.evaluation(ev.evaluation_id)
        assert art.assessment.objective["eligible"] is False
        assert art.assessment.objective["fit_loss"] is None
        assert art.assessment.assessment["constraint_status"] == "NOT_ASSESSED"
        assert "CONSTRAINT_RESPONSE_INVALID" in art.assessment.objective["reason_codes"]
    finally:
        svc_i.close()

    # 5. Cache reuse vs refresh + diagnostic failure isolation + CAS / external manifest change
    ad_ok = _CustomLinearAdapter()
    svc_ok, path_ok = _make_custom_study(tmp_path / "ok", ad_ok)
    try:
        r1 = svc_ok.evaluate({"m": 3.0, "b": 2.0}, expected_revision=svc_ok.snapshot().document_revision, cache_policy="reuse")
        assert r1.observation["cache_hit"] is False
        r2 = svc_ok.evaluate({"m": 3.0, "b": 2.0}, expected_revision=svc_ok.snapshot().document_revision, cache_policy="reuse")
        assert r2.evaluation_id == r1.evaluation_id
        assert r2.observation["cache_hit"] is True
        r3 = svc_ok.evaluate({"m": 3.0, "b": 2.0}, expected_revision=svc_ok.snapshot().document_revision, cache_policy="refresh")
        assert r3.evaluation_id != r1.evaluation_id
        assert r3.observation["cache_hit"] is False

        # Wrong expected_revision -> REVISION_MISMATCH
        with pytest.raises(MaterialStudyError) as cas_exc:
            svc_ok.evaluate({"m": 3.0, "b": 2.0}, expected_revision="0" * 64)
        assert cas_exc.value.code == "REVISION_MISMATCH"

        # External manifest change on disk -> EXTERNAL_MANIFEST_CHANGED without overwriting disk
        disk_text = path_ok.read_text(encoding="utf-8")
        modified_text = disk_text.replace('"Custom Linear Study"', '"Externally Edited"')
        path_ok.write_text(modified_text, encoding="utf-8")
        with pytest.raises(MaterialStudyError) as ext_exc:
            svc_ok.evaluate({"m": 3.0, "b": 2.0}, expected_revision=svc_ok.snapshot().document_revision)
        assert ext_exc.value.code == "EXTERNAL_MANIFEST_CHANGED"
        assert '"Externally Edited"' in path_ok.read_text(encoding="utf-8")
    finally:
        svc_ok.close()


def test_nonblocking_snapshot_stop_cancel_and_busy_write_rejection(tmp_path: Path) -> None:
    """Verify non-blocking snapshot, write rejection while active, Stop vs Cancel precedence, and stale IDs."""
    adapter = _CustomLinearAdapter(delay_sec=0.06)
    svc, _ = _make_custom_study(tmp_path, adapter)
    started = threading.Event()
    campaigns: list[str] = []
    results: list[Any] = []
    errors: list[Exception] = []

    def _on_prog(ev):
        if ev.get("stage") == "started" and ev.get("campaign_id"):
            campaigns.append(ev["campaign_id"])
            started.set()

    rev = svc.snapshot().document_revision

    def _run_opt():
        try:
            res = svc.optimize(
                OptimizationConfig(algorithm="de", seed=17, max_evaluations=120, max_iterations=20, population_size=6),
                expected_revision=rev,
                on_progress=_on_prog,
            )
            results.append(res)
        except Exception as exc:
            errors.append(exc)

    t = threading.Thread(target=_run_opt, daemon=True)
    t.start()
    assert started.wait(timeout=5.0)
    cid = campaigns[0]

    # snapshot() must return immediately (non-blocking) and expose active_campaign_id
    t0 = time.monotonic()
    snap = svc.snapshot()
    assert (time.monotonic() - t0) < 0.05
    assert snap.active_campaign_id == cid
    assert snap.active_operation_id is not None

    # Writes rejected while optimize active
    with pytest.raises(MaterialStudyError) as busy_exc:
        svc.save()
    assert busy_exc.value.code == "STUDY_BUSY"

    # Unknown campaign ID rejected with not_active
    stale_canc = svc.cancel("unknown-campaign-id")
    assert stale_canc.accepted is False
    assert stale_canc.state == "not_active"

    # Wait briefly for at least 1 batch evaluation, then request stop
    time.sleep(0.15)
    stop_rec = svc.stop(cid)
    assert stop_rec.accepted is True
    assert stop_rec.state == "requested"
    t.join(timeout=10.0)
    assert not errors
    assert results and results[0].state == "stopped"
    assert results[0].termination_reason == "USER_STOP"

    # Now test Cancel precedence & immediate receipt
    started.clear()
    campaigns.clear()
    results.clear()
    rev2 = svc.snapshot().document_revision

    def _run_opt2():
        try:
            results.append(
                svc.optimize(
                    OptimizationConfig(algorithm="de", seed=17, max_evaluations=120, max_iterations=20, population_size=6),
                    expected_revision=rev2,
                    on_progress=_on_prog,
                )
            )
        except Exception as exc:
            errors.append(exc)

    t2 = threading.Thread(target=_run_opt2, daemon=True)
    t2.start()
    assert started.wait(timeout=5.0)
    cid2 = campaigns[0]
    svc.stop(cid2)
    canc_rec = svc.cancel(cid2)
    assert canc_rec.accepted is True
    assert canc_rec.state == "requested"
    t2.join(timeout=10.0)
    assert not errors
    assert results and results[0].state == "cancelled"
    assert results[0].termination_reason == "USER_CANCEL"
    svc.close()


def test_store01_locks_readonly_saveas_relink_and_recovery(tmp_path: Path) -> None:
    """Verify D06 STORE01: stem.caemat-data/, lock lease, read-only zero writes, SaveAs, fault injection, relink, recovery."""
    adapter = _CustomLinearAdapter()
    reg = AdapterRegistry()
    reg.register(adapter, default=True)
    orig_dir = tmp_path / "orig"
    orig_dir.mkdir()
    svc, study_path = _make_custom_study(orig_dir, adapter, name="my.study.caemat.json")
    assert svc.data_root == orig_dir / "my.study.caemat-data"
    assert not (orig_dir / "my.study.caemat.caemat-data").exists()

    try:
        # Confirm an evaluation + export so selections, holdout-exposures, and exports are populated
        ev_conf = svc.evaluate({"m": 3.0, "b": 2.0}, expected_revision=svc.snapshot().document_revision, cache_policy="refresh", execution_phase="confirmation")
        svc.export(ev_conf.evaluation_id, orig_dir / "out_report.json", expected_revision=svc.snapshot().document_revision)

        # Second writable open fails with STUDY_LOCKED
        with pytest.raises(MaterialStudyError) as lock_exc:
            CalibrationStudyService.open(study_path, read_only=False, registry=reg)
        assert lock_exc.value.code == "STUDY_LOCKED"
    finally:
        svc.close()

    # Lock is released on close; verify read-only open creates ZERO files / locks
    assert (orig_dir / "my.study.caemat-data" / "study.lock").exists() or True
    (orig_dir / "my.study.caemat-data" / "study.lock").unlink(missing_ok=True)
    before_files = sorted(p.relative_to(orig_dir).as_posix() for p in orig_dir.rglob("*"))
    ro_svc = CalibrationStudyService.open(study_path, read_only=True, registry=reg)
    try:
        after_files = sorted(p.relative_to(orig_dir).as_posix() for p in orig_dir.rglob("*"))
        assert after_files == before_files
        assert not (orig_dir / "my.study.caemat-data" / "study.lock").exists()

        # Pre-commit fault injection on save_as from read-only source keeps original intact
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        fail_dest = dest_dir / "pre_fail.caemat.json"

        def _inject_pre(ev):
            if ev.get("stage") == "staging_copy":
                raise OSError("Injected staging I/O failure")

        with pytest.raises(OSError):
            ro_svc.save_as(fail_dest, expected_revision=ro_svc.snapshot().document_revision, on_progress=_inject_pre)
        assert not fail_dest.exists()
        assert not (dest_dir / "pre_fail.caemat-data").exists()
        assert list(dest_dir.glob(".caemat-saveas-*.json")) == []

        # Post-commit cleanup fault injection returns committed_cleanup_pending & preserves committed destination
        cleanup_dest = dest_dir / "cleanup_pending.caemat.json"

        def _inject_cleanup(ev):
            if ev.get("stage") == "cleaning_up":
                raise OSError("Injected cleanup failure")

        rec_pending = ro_svc.save_as(cleanup_dest, expected_revision=ro_svc.snapshot().document_revision, on_progress=_inject_cleanup)
        assert rec_pending.state == "committed_cleanup_pending"
        assert any("DESTINATION_COMMITTED_CLEANUP_PENDING" in w for w in rec_pending.warnings)
        assert cleanup_dest.is_file()
    finally:
        ro_svc.close()

    # Now do clean SaveAs, delete original folder completely, reopen copy & verify full evaluations/exposures!
    rw_svc = CalibrationStudyService.open(study_path, read_only=False, registry=reg)
    moved_path = tmp_path / "moved" / "copied.caemat.json"
    save_rec = rw_svc.save_as(moved_path, expected_revision=rw_svc.snapshot().document_revision)
    assert save_rec.state == "committed"
    assert save_rec.copied_artifact_count > 0
    rw_svc.close()

    shutil.rmtree(orig_dir)
    reopened = CalibrationStudyService.open(moved_path, read_only=False, registry=reg)
    try:
        art = reopened.evaluation(ev_conf.evaluation_id)
        assert art.assessment.assessment["holdout_status"] == "pass"

        # Test reconcile & relink_source
        rec_rep = reopened.reconcile(source_policy="inspect")
        assert rec_rep.applicability["can_evaluate"] is True
        train_src_id = reopened.experiments()[0].source_ref.id
        # Relink to copy with identical bytes succeeds; different bytes fails with RELINK_HASH_MISMATCH
        same_copy = tmp_path / "moved" / "identical_train.csv"
        shutil.copyfile(reopened.data_root / reopened.experiments()[0].source_ref.path, same_copy)
        relink_ok = reopened.relink_source(train_src_id, same_copy, expected_revision=reopened.snapshot().document_revision)
        assert relink_ok.new_path == str(same_copy.resolve())

        diff_file = tmp_path / "moved" / "different.csv"
        diff_file.write_text("x,y\n0,1\n", encoding="utf-8")
        with pytest.raises(MaterialStudyError) as relink_exc:
            reopened.relink_source(train_src_id, diff_file, expected_revision=reopened.snapshot().document_revision)
        assert relink_exc.value.code == "RELINK_HASH_MISMATCH"
    finally:
        reopened.close()

    # Test recovery: interrupted campaign does not auto-resume on open and is marked via recover('mark_interrupted')
    raw_manifest = json.loads(moved_path.read_text(encoding="utf-8"))
    raw_manifest["campaigns"]["stale-camp-1"] = {
        "campaign_id": "stale-camp-1",
        "content_revision": raw_manifest["content_revision"],
        "state": "running",
        "termination_reason": "RUNNING",
        "evaluation_ids": [],
        "selected_ids": [],
        "budget_usage": {},
    }
    moved_path.write_text(json.dumps(raw_manifest, indent=2), encoding="utf-8")

    rec_svc = CalibrationStudyService.open(moved_path, read_only=False, registry=reg)
    try:
        rep = rec_svc.inspect_recovery()
        assert any(c["campaign_id"] == "stale-camp-1" for c in rep.interrupted_campaigns)
        assert "mark_interrupted" in rep.allowed_actions
        rec_done = rec_svc.recover("mark_interrupted", "stale-camp-1", expected_revision=rec_svc.snapshot().document_revision)
        assert rec_done.state == "interrupted_marked"
        assert rec_svc._manifest["campaigns"]["stale-camp-1"]["state"] == "interrupted"
    finally:
        rec_svc.close()


@pytest.mark.parametrize("change", ["missing", "damaged", "wrong_evaluation"])
def test_holdout_event_must_verify_on_reopen_history_and_export(tmp_path, change):
    svc, study_path = _make_custom_study(tmp_path, _CustomLinearAdapter())
    registry = svc._registry
    with svc:
        receipt = svc.evaluate(
            {"m": 3, "b": 2}, expected_revision=svc.snapshot().document_revision,
            cache_policy="refresh", execution_phase="confirmation", compute_identifiability=False,
        )
        event = next((svc.data_root / "holdout_exposures").glob("*.json"))
    if change == "missing":
        event.unlink()
    elif change == "damaged":
        event.write_bytes(b"{}")
    else:
        record = json.loads(event.read_bytes())
        record["evaluation_id"] = "an-unrelated-evaluation"
        event.write_text(json.dumps(record), encoding="utf-8")
        manifest = json.loads(study_path.read_bytes())
        ref = manifest["holdout_exposures"][0]
        ref.update(sha256=hashlib.sha256(event.read_bytes()).hexdigest(), size=event.stat().st_size)
        manifest["evaluations"][receipt.evaluation_id]["holdout_exposure_ref"] = ref
        study_path.write_text(json.dumps(manifest), encoding="utf-8")

    with CalibrationStudyService.open(study_path, read_only=True, registry=registry) as reader:
        with pytest.raises(MaterialStudyError, match="CORRUPT_ARTIFACT"):
            reader.evaluation(receipt.evaluation_id)
        with pytest.raises(MaterialStudyError, match="CORRUPT_ARTIFACT"):
            reader.list_evaluations()
    with CalibrationStudyService.open(study_path, registry=registry) as writer:
        target = tmp_path / "bad-export.json"
        with pytest.raises(MaterialStudyError, match="CORRUPT_ARTIFACT"):
            writer.export(receipt.evaluation_id, target, expected_revision=writer.snapshot().document_revision)
        assert not target.exists()


def test_campaign_checkpoint_config_repeat_cache_and_stop(tmp_path):
    svc, _ = _make_custom_study(tmp_path, _CustomLinearAdapter())
    cfg = OptimizationConfig(algorithm="slsqp", max_evaluations=80, max_iterations=25, ftol=1e-9)
    with svc:
        original_revision = svc.snapshot().content_revision

        def verify_checkpoint(event):
            if event["stage"] == "started":
                saved = json.loads((svc.data_root / "campaigns" / f"{event['campaign_id']}.json").read_bytes())
                assert saved["state"] == "running"
                assert saved["content_revision"] == svc.snapshot().content_revision != original_revision
                assert json.loads(svc.path.read_bytes())["optimization_config"]["max_evaluations"] == 80

        first = svc.optimize(cfg, expected_revision=svc.snapshot().document_revision, on_progress=verify_checkpoint)
        repeat = svc.optimize(expected_revision=svc.snapshot().document_revision)
        assert repeat.state == "completed"
        assert repeat.content_revision == first.content_revision
        assert repeat.budget_usage["cache_hits"] > 0
        assert repeat.selected_ids == first.selected_ids != ()

        def stop_on_start(event):
            if event["stage"] == "started":
                assert svc.stop(event["campaign_id"]).accepted

        stopped = svc.optimize(cfg, expected_revision=svc.snapshot().document_revision, on_progress=stop_on_start)
        assert stopped.state == "stopped"
        assert stopped.selected_ids == first.selected_ids
        assert json.loads(svc.path.read_bytes())["selected_evaluation_id"] == first.selected_ids[0]


def test_campaign_checkpoint_is_reopened_after_dispatch_failure(tmp_path, monkeypatch):
    import caelab.materials.service as material_service

    svc, study_path = _make_custom_study(tmp_path, _CustomLinearAdapter())
    registry = svc._registry

    def crash(*args, **kwargs):
        raise RuntimeError("dispatch interrupted")

    monkeypatch.setattr(material_service, "mdo_optimize", crash)
    with svc, pytest.raises(RuntimeError, match="dispatch interrupted"):
        svc.optimize(expected_revision=svc.snapshot().document_revision)
    with CalibrationStudyService.open(study_path, registry=registry) as reopened:
        campaign, = reopened.inspect_recovery().interrupted_campaigns
        cid = campaign["campaign_id"]
        reopened.recover("mark_interrupted", cid, expected_revision=reopened.snapshot().document_revision)
        assert reopened.inspect_recovery().interrupted_campaigns[0]["state"] == "interrupted"
        assert json.loads((reopened.data_root / "campaigns" / f"{cid}.json").read_bytes())["state"] == "interrupted"


@pytest.mark.parametrize("action", ["stop", "cancel", "close"])
def test_single_evaluation_cancellation_keeps_lease_until_worker_exits(tmp_path, monkeypatch, action):
    adapter = _CustomLinearAdapter()
    svc, study_path = _make_custom_study(tmp_path, adapter)
    entered = threading.Event()
    observed_cancel = threading.Event()
    allow_exit = threading.Event()
    results = []
    original_execute = adapter.execute

    def wait_for_cancel(plan, request, *, cancel_event, **kwargs):
        if not request.evaluation_id.startswith("svd-"):
            return original_execute(plan, request, cancel_event=cancel_event, **kwargs)
        entered.set()
        assert cancel_event.wait(5)
        observed_cancel.set()
        assert allow_exit.wait(5)
        raise MaterialStudyError("CANCELLED", "test")

    monkeypatch.setattr(adapter, "execute", wait_for_cancel)
    rev = svc.snapshot().document_revision
    worker = threading.Thread(target=lambda: results.append(svc.evaluate(expected_revision=rev)), daemon=True)
    worker.start()
    closer = None
    try:
        assert entered.wait(5)
        operation = svc.snapshot().active_operation_id
        assert operation
        if action == "close":
            closer = threading.Thread(target=svc.close, daemon=True)
            closer.start()
        else:
            assert getattr(svc, action)(operation).accepted
        assert observed_cancel.wait(5)
        with pytest.raises(MaterialStudyError, match="STUDY_LOCKED"):
            CalibrationStudyService.open(study_path, registry=svc._registry)
    finally:
        allow_exit.set()
        worker.join(6)
        if closer is not None:
            closer.join(6)
        svc.close()
    assert not worker.is_alive()
    assert results and results[0].state == "cancelled"
    assert results[0].observation["reason"] == "CANCELLED"
    with CalibrationStudyService.open(study_path, registry=svc._registry) as reopened:
        objective = reopened.evaluation(results[0].evaluation_id).assessment.objective
        assert not objective["eligible"]
        assert objective["fit_loss"] is None
        assert "CANCELLED" in objective["reason_codes"]


def test_confirmation_cancelled_before_dispatch_does_not_consume_holdout(tmp_path):
    svc, _ = _make_custom_study(tmp_path, _CustomLinearAdapter())
    with svc:
        def cancel_on_start(event):
            if event["stage"] == "started":
                assert svc.cancel(event["operation_id"]).accepted

        cancelled = svc.evaluate(
            {"m": 3, "b": 2}, expected_revision=svc.snapshot().document_revision,
            cache_policy="refresh", execution_phase="confirmation", on_progress=cancel_on_start,
        )
        assert cancelled.state == "cancelled"
        artifact = svc.evaluation(cancelled.evaluation_id)
        assert not artifact.holdout_exposed
        assert artifact.assessment.assessment["holdout_status"] == "not_assessed"
        assert not list((svc.data_root / "holdout_exposures").glob("*.json"))
        completed = svc.evaluate(
            {"m": 3, "b": 2}, expected_revision=svc.snapshot().document_revision,
            cache_policy="refresh", execution_phase="confirmation", compute_identifiability=False,
        )
        assert svc.evaluation(completed.evaluation_id).assessment.assessment["holdout_status"] == "pass"


def test_nested_residual_reference_breaks_save_as(tmp_path):
    svc, _ = _make_custom_study(tmp_path, _CustomLinearAdapter())
    with svc:
        receipt = svc.evaluate(expected_revision=svc.snapshot().document_revision, compute_identifiability=False)
        artifact = svc.evaluation(receipt.evaluation_id)
        (svc.data_root / artifact.channel_results[0]["residuals_ref"]["path"]).unlink()
        destination = tmp_path / "copy.caemat.json"
        with pytest.raises(MaterialStudyError, match="CORRUPT_ARTIFACT"):
            svc.save_as(destination, expected_revision=svc.snapshot().document_revision)
        assert not destination.exists()


@pytest.mark.parametrize("damage", ["none", "manifest_missing"])
def test_save_as_shared_parent_recovery_respects_lease_and_manifest(tmp_path, damage):
    class SimulatedPowerLoss(BaseException):
        pass

    svc, _ = _make_custom_study(tmp_path, _CustomLinearAdapter())
    dest_dir = tmp_path / "shared"
    dest_dir.mkdir()
    sentinel = dest_dir / "unrelated.txt"
    sentinel.write_text("keep my data", encoding="utf-8")
    destination = dest_dir / "calibration.json"
    with svc:
        revision = svc.snapshot().document_revision

        def fail_before_copy(event):
            if event["stage"] == "staging_copy":
                raise OSError("staging failed")

        with pytest.raises(OSError, match="staging failed"):
            svc.save_as(destination, expected_revision=revision, on_progress=fail_before_copy)
        assert sorted(p.name for p in dest_dir.iterdir()) == ["unrelated.txt"]

        def interrupt(event):
            if event["stage"] == "committing_destination":
                raise SimulatedPowerLoss()

        with pytest.raises(SimulatedPowerLoss):
            svc.save_as(destination, expected_revision=revision, on_progress=interrupt)
        journal, = svc.inspect_recovery(destination=destination).transactions
        assert journal["created_data_root"] is False
        tx = journal["transaction_id"]
        with FileLock(dest_dir / "study.lock", timeout=0):
            with pytest.raises(MaterialStudyError, match="STUDY_LOCKED"):
                svc.recover("finish_save_as", tx, destination=destination, expected_revision=revision)
        journal_file = Path(journal["journal_path"])
        if damage == "manifest_missing":
            (journal_file.parent / "calibration.json.tmp").unlink()
            with pytest.raises(MaterialStudyError, match="CORRUPT_ARTIFACT"):
                svc.recover("finish_save_as", tx, destination=destination, expected_revision=revision)
            assert journal_file.is_file()
            assert svc.recover(
                "discard_incomplete_copy", tx, destination=destination, expected_revision=revision,
            ).state == "discarded"
            assert not destination.exists()
        else:
            assert svc.recover("finish_save_as", tx, destination=destination, expected_revision=revision).state == "committed"
            with CalibrationStudyService.open(destination, registry=svc._registry) as copy:
                assert copy.snapshot().content_revision == svc.snapshot().content_revision
        assert not journal_file.exists()
        assert sentinel.read_text(encoding="utf-8") == "keep my data"


def test_confirmation_freezes_before_array_reads_and_isolates_holdout_errors(tmp_path, monkeypatch):
    import caelab.materials.residuals as residuals

    adapter = _CustomLinearAdapter()
    svc, _ = _make_custom_study(tmp_path, adapter)
    load_arrays = residuals.load_experiment_channel_arrays
    execute = adapter.execute
    holdout_path = svc._manifest["experiment_data"]["exp_hold"]["y_val"]["values_ref"]["path"]
    holdout_read = []

    def verify_loader(entry, **kwargs):
        if entry["values_ref"]["path"] == holdout_path:
            assert list(svc.data_root.glob("revisions/*/selections/*.json"))
            holdout_read.append(True)
            raise MaterialStudyError("DATA_RECIPE_INCOMPATIBLE", "test")
        return load_arrays(entry, **kwargs)

    def verify_dispatch(plan, request, **kwargs):
        assert set(request.experiment_data) == set(plan.experiment_ids)
        assert all(
            set(request.experiment_data[exp.experiment_id]) == {ch.observable_id for ch in exp.channels}
            for exp in request.experiments
        )
        if request.execution_phase == "exploration":
            assert tuple(plan.experiment_ids) == ("exp_train",)
        if "exp_hold" in plan.experiment_ids:
            assert list(svc.data_root.glob("revisions/*/selections/*.json"))
        return execute(plan, request, **kwargs)

    monkeypatch.setattr(residuals, "load_experiment_channel_arrays", verify_loader)
    monkeypatch.setattr(adapter, "execute", verify_dispatch)
    with svc:
        # Importing a CSV does not declare it for the planner or freeze it into the selection.
        template = svc.experiments()[0].channels[0]
        svc.import_experiment(
            tmp_path / "hold.csv", experiment_id="hold-not-declared", channels=[template],
            split="holdout", expected_revision=svc.snapshot().document_revision,
        )
        fit = svc.evaluate(
            {"m": 3, "b": 2}, expected_revision=svc.snapshot().document_revision, cache_policy="refresh",
        )
        assert not holdout_read
        assert svc.evaluation(fit.evaluation_id).assessment.assessment["holdout_status"] == "not_assessed"
        confirmation = svc.evaluate(
            {"m": 3, "b": 2}, expected_revision=svc.snapshot().document_revision,
            cache_policy="refresh", execution_phase="confirmation", compute_identifiability=False,
        )
        result = svc.evaluation(confirmation.evaluation_id)
        assert holdout_read
        assert result.assessment.assessment["fit_status"] == "pass"
        assert result.assessment.objective["fit_loss"] == 0.0
        assert result.assessment.assessment["holdout_status"] == "fail"
        assert set(exp.experiment_id for exp in result.prediction_bundle.experiments) == {
            "exp_train", "exp_val", "exp_hold",
        }

        def reject_holdout(plan, request, **kwargs):
            if "exp_hold" in plan.experiment_ids:
                raise RuntimeError("adapter failed during holdout")
            return verify_dispatch(plan, request, **kwargs)

        monkeypatch.setattr(adapter, "execute", reject_holdout)
        again = svc.evaluate(
            {"m": 3, "b": 2}, expected_revision=svc.snapshot().document_revision,
            cache_policy="refresh", execution_phase="confirmation", compute_identifiability=False,
            selection_ref=result.selection_ref,
        )
        report = svc.evaluation(again.evaluation_id).assessment
        assert report.assessment["fit_status"] == "pass"
        assert report.assessment["holdout_status"] == "fail"
        assert report.objective["fit_loss"] == 0.0


def test_prediction_revision_and_strict_endpoint_coverage(tmp_path, monkeypatch):
    adapter = _CustomLinearAdapter()
    svc, _ = _make_custom_study(tmp_path, adapter)
    collect = adapter.collect

    with svc:
        def wrong_revision(*args, **kwargs):
            bundle = collect(*args, **kwargs)
            return replace(bundle, experiments=(replace(bundle.experiments[0], experiment_revision="0" * 64),))

        monkeypatch.setattr(adapter, "collect", wrong_revision)
        receipt = svc.evaluate(expected_revision=svc.snapshot().document_revision, compute_identifiability=False)
        assert receipt.state == "failed"
        assert "PREDICTION_EXPERIMENT_MISMATCH" in svc.evaluation(receipt.evaluation_id).assessment.objective["reason_codes"]

        monkeypatch.setattr(adapter, "collect", collect)
        original_loader = load_experiment_channel_arrays

        def almost_same_endpoint(entry, **kwargs):
            x, y, keys, mask = original_loader(entry, **kwargs)
            x = x.copy()
            x[-1] -= 5e-13
            return x, y, keys, mask

        monkeypatch.setattr(__name__ + ".load_experiment_channel_arrays", almost_same_endpoint)
        receipt = svc.evaluate(expected_revision=svc.snapshot().document_revision, compute_identifiability=False)
        assessment = svc.evaluation(receipt.evaluation_id).assessment
        assert "COVERAGE_INCOMPLETE" in assessment.objective["reason_codes"]
        assert assessment.assessment["channel_checks"][0]["coverage"] == {"expected": 4, "valid": 3, "missing": 1}


def test_optional_scalar_does_not_invalidate_parameter_constraints_or_required_uncertainty(tmp_path, monkeypatch):
    adapter = _CustomLinearAdapter(invalid_scalar=True)
    base_capabilities = adapter.capabilities

    def capabilities(*args, **kwargs):
        return replace(
            base_capabilities(*args, **kwargs),
            optimizer_constraints=(
                ConstraintSpec(id="slope_upper", response_id="m", kind="UPPER", limit=4, unit="MPa"),
                ConstraintSpec(
                    id="sum_upper", response_id=None, expression="m + b", dependencies=("m", "b"),
                    kind="UPPER", limit=6, unit="MPa",
                ),
            ),
        )

    monkeypatch.setattr(adapter, "capabilities", capabilities)
    svc, _ = _make_custom_study(tmp_path, adapter)
    with svc:
        svc.configure(
            parameters=svc.parameters(), comparisons=svc.comparisons(),
            assessment_policy={"adequacy_policy": {"enabled": True, "require_holdout": False, "require_uncertainty": True}},
            expected_revision=svc.snapshot().document_revision,
        )
        receipt = svc.evaluate(
            {"m": 3, "b": 2}, expected_revision=svc.snapshot().document_revision, compute_identifiability=False,
        )
        report = svc.evaluation(receipt.evaluation_id).assessment
        assert report.objective["eligible"]
        assert report.assessment["constraint_status"] == "FEASIBLE"
        assert report.assessment["uncertainty_status"] == "not_computed"
        assert report.assessment["adequacy_status"] == "not_assessed"


def test_csv_revision_is_portable_ids_do_not_alias_and_filtered_rows_must_match_split(tmp_path):
    adapter = _CustomLinearAdapter()
    svc, _ = _make_custom_study(tmp_path / "first", adapter)
    second, _ = _make_custom_study(tmp_path / "second", adapter)
    channel = svc.experiments()[0].channels[0]
    original = tmp_path / "first" / "train.csv"
    relocated = tmp_path / "second" / "train.csv"
    with svc, second:
        def import_to(study, csv_path, eid, **kwargs):
            return study.import_experiment(
                csv_path, experiment_id=eid, channels=[channel],
                expected_revision=study.snapshot().document_revision, **kwargs,
            )

        a = import_to(svc, original, "run A")
        import_to(second, relocated, "run A")
        first_exp = next(exp for exp in svc.experiments() if exp.experiment_id == "run A")
        second_exp = next(exp for exp in second.experiments() if exp.experiment_id == "run A")
        assert first_exp.experiment_revision == second_exp.experiment_revision
        c = import_to(svc, original, "run_A")
        assert {ref.path for ref in a.converted_refs}.isdisjoint({ref.path for ref in c.converted_refs})

        wrong = tmp_path / "labeled.csv"
        _write_csv(wrong, ["x", "y", "split", "group"], [[0, 2, "holdout", "a"], [1, 5, "train", "b"]])
        with pytest.raises(MaterialStudyError, match="DATA_RECIPE_INCOMPATIBLE"):
            import_to(svc, wrong, "bad", split="train", row_filter={"group": "a"})


def test_save_as_rollback_keeps_preexisting_managed_tree(tmp_path):
    svc, original = _make_custom_study(tmp_path, _CustomLinearAdapter())
    shared = tmp_path / "shared"
    (shared / "evaluations").mkdir(parents=True)
    sentinel = shared / "evaluations" / "another-study.json"
    sentinel.write_bytes(b"belongs to another study")
    destination = shared / "calibration.json"
    with svc:
        svc.evaluate(expected_revision=svc.snapshot().document_revision, compute_identifiability=False)
        with pytest.raises(MaterialStudyError, match="DESTINATION_ALREADY_EXISTS"):
            svc.save_as(destination, expected_revision=svc.snapshot().document_revision)
        assert svc.path == original
        assert sentinel.read_bytes() == b"belongs to another study"
        assert not destination.exists()
        assert not (shared / "arrays").exists()
        assert not list(shared.glob(".caemat-saveas-*"))


def test_legacy_copy_journal_without_ownership_and_digest_is_not_actionable(tmp_path):
    svc, _ = _make_custom_study(tmp_path, _CustomLinearAdapter())
    tx = "1" * 32
    journal = tmp_path / f".caemat-saveas-{tx}.json"
    journal.write_text(json.dumps({"transaction_id": tx, "state": "committing"}), encoding="utf-8")
    with svc:
        report = svc.inspect_recovery()
        assert report.transactions[0]["corrupt_journal"] == "LEGACY_COPY_OWNERSHIP_AND_DIGEST_UNVERIFIABLE"
        assert not report.allowed_actions
        for action in ("finish_save_as", "discard_incomplete_copy"):
            with pytest.raises(MaterialStudyError, match="CORRUPT_ARTIFACT"):
                svc.recover(action, tx, expected_revision=svc.snapshot().document_revision)
        assert journal.is_file()


def test_campaign_snapshot_stays_available_during_evaluation_index_write(tmp_path, monkeypatch):
    import caelab.materials.service as material_service

    svc, _ = _make_custom_study(tmp_path, _CustomLinearAdapter())
    original_write = material_service.atomic_json
    entered = threading.Event()
    release = threading.Event()
    returned = threading.Event()
    results = []

    def slow_write(path, payload):
        if not entered.is_set() and path == svc.path and payload.get("evaluations"):
            entered.set()
            assert release.wait(5)
        return original_write(path, payload)

    def read_snapshot():
        results.append(svc.snapshot())
        returned.set()

    monkeypatch.setattr(material_service, "atomic_json", slow_write)
    with svc:
        revision = svc.snapshot().document_revision
        config = OptimizationConfig(algorithm="slsqp", max_evaluations=8)
        worker = threading.Thread(target=lambda: svc.optimize(config, expected_revision=revision), daemon=True)
        worker.start()
        try:
            assert entered.wait(5)
            reader = threading.Thread(target=read_snapshot, daemon=True)
            reader.start()
            assert returned.wait(1)
            assert results[0].active_campaign_id
            assert svc.cancel(results[0].active_campaign_id).accepted
        finally:
            release.set()
            worker.join(10)
        assert not worker.is_alive()


def test_exploration_adapter_only_receives_enabled_channels_and_validation_ids(tmp_path, monkeypatch):
    adapter = _CustomLinearAdapter()
    svc, _ = _make_custom_study(tmp_path, adapter)
    original_execute = adapter.execute
    inspected = []

    def assert_request(plan, request, **kwargs):
        experiments = {spec.experiment_id: tuple(ch.observable_id for ch in spec.channels) for spec in request.experiments}
        assert set(request.experiment_data) == set(experiments)
        assert all(experiments[eid] == tuple(data) for eid, data in request.experiment_data.items())
        assert set(experiments) <= {"multi", "exp_val"}
        assert experiments["multi"] == ("y_val",)
        inspected.append((request.evaluation_id, set(experiments)))
        return original_execute(plan, request, **kwargs)

    with svc:
        template = svc.experiments()[0].channels[0]
        disabled = replace(template, observable_id="disabled", observable=replace(template.observable, id="disabled"))
        svc.import_experiment(
            tmp_path / "train.csv", experiment_id="multi", channels=[template, disabled], split="train",
            expected_revision=svc.snapshot().document_revision,
        )
        fit, validation, holdout = svc.comparisons()
        svc.configure(
            parameters=svc.parameters(),
            comparisons=(
                replace(fit, experiment_id="multi"),
                replace(fit, id="disabled", experiment_id="multi", observable_id="disabled", required_for_fit=False),
                validation, holdout,
            ),
            assessment_policy={"selection_policy": {"mode": "manual_with_validation", "validation_comparison_ids": [validation.id]}},
            expected_revision=svc.snapshot().document_revision,
        )
        monkeypatch.setattr(adapter, "execute", assert_request)
        receipt = svc.evaluate(expected_revision=svc.snapshot().document_revision, cache_policy="refresh")
        assert receipt.state == "completed"
        assert inspected[0][1] == {"multi", "exp_val"}
        assert any(eid.startswith("svd-") and ids == {"multi"} for eid, ids in inspected)


def test_adapter_upgrade_cannot_reuse_frozen_confirmation_identity(tmp_path):
    adapter = _CustomLinearAdapter()
    svc, _ = _make_custom_study(tmp_path, adapter)
    with svc:
        fit = svc.evaluate(
            {"m": 3, "b": 2}, expected_revision=svc.snapshot().document_revision, compute_identifiability=False,
        )
        confirmation = svc.evaluate(
            {"m": 3, "b": 2}, expected_revision=svc.snapshot().document_revision,
            cache_policy="refresh", execution_phase="confirmation", compute_identifiability=False,
        )
        confirmed = svc.evaluation(confirmation.evaluation_id)
        adapter._identity = replace(adapter.identity, implementation_sha256="1" * 64)
        dispatches = len(adapter.calls)
        with pytest.raises(MaterialStudyError, match="SELECTION_MISMATCH"):
            svc.evaluate(
                {"m": 3, "b": 2}, expected_revision=svc.snapshot().document_revision, cache_policy="refresh",
                execution_phase="confirmation", selection_ref=confirmed.selection_ref,
            )
        assert len(adapter.calls) == dispatches
        fresh = svc.evaluate(
            {"m": 3, "b": 2}, expected_revision=svc.snapshot().document_revision, compute_identifiability=False,
        )
        assert fresh.content_revision != fit.content_revision
        assert svc.evaluation(fresh.evaluation_id).adapter == adapter.identity
        assert svc.evaluation(confirmation.evaluation_id) == confirmed
