"""CORE02 single-point evaluator, SVD identifiability, and BatchEvaluator bridge to caelab.mdo.optimize."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import tempfile
import threading
import uuid
from typing import Any

import numpy as np

from caelab.artifact_refs import (
    ArtifactRefV1,
    ProducerIdentity,
    _to_plain_json,
    canonical_json_bytes,
    canonical_sha256,
    validate_relative_artifact_path,
    write_safe_npy_v2,
)
from caelab.materials.contracts import (
    AdapterIdentity,
    AssessmentResultV1,
    ComparisonSpec,
    ExperimentSpec,
    ModelDescriptorV1,
    ParameterSpec,
    PredictionBundleV1,
    ScalarResponseSpecV1,
)
from caelab.materials.registry import (
    CalibrationEvaluationArtifact,
    MaterialAdapter,
    MaterialEvaluationRequest,
    MaterialStudyError,
)
from caelab.materials.experiments import storage_segment
from caelab.materials.residuals import (
    ChannelResidualResult,
    ResidualEvaluationOutput,
    evaluate_bundle_residuals,
)
from caelab.mdo.constraints import ConstraintSpec
from caelab.mdo.optimize import BatchEvaluator, EvaluationObservation, EvaluationRequest
from caelab.mdo.variables import (
    ContinuousVariable,
    LinkedVariable,
    VariableSpace,
    evaluate_expression,
    parse_unit as _mdo_parse_unit,
)

__all__ = [
    "MaterialBatchEvaluator",
    "build_variable_space",
    "compute_candidate_cache_key",
    "compute_svd_identifiability",
    "execute_single_evaluation",
    "resolve_parameter_values",
]

_PRODUCER = ProducerIdentity(id="caelab.materials.evaluator", version="1.0.0")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=target.name + ".",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_json_artifact(
    data_root: Path,
    rel_path: str,
    payload: Any,
    *,
    artifact_id: str,
    role: str,
) -> ArtifactRefV1:
    validate_relative_artifact_path(rel_path, root=data_root)
    raw = canonical_json_bytes(payload)
    _atomic_write_bytes(data_root / rel_path, raw)
    return ArtifactRefV1(
        id=artifact_id,
        role=role,
        path=rel_path,
        sha256=hashlib.sha256(raw).hexdigest(),
        size=len(raw),
        format_id="application/json",
        producer=_PRODUCER,
    )


def resolve_parameter_values(
    parameter_specs: Sequence[ParameterSpec],
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    """Resolve free, fixed, and linked parameters deterministically with unit/bounds checks."""
    by_id = {p.id: p for p in parameter_specs}
    provided = dict(overrides or {})
    unknown = set(provided) - set(by_id)
    if unknown:
        raise MaterialStudyError(
            "INVALID_PARAMETER",
            "resolve_parameter_values",
            {"reason": "UNKNOWN_PARAMETER", "unknown": sorted(unknown)},
        )

    values: dict[str, float] = {}
    units = {p.id: p.unit for p in parameter_specs}

    for p in parameter_specs:
        if p.role == "fixed":
            val = p.default
            if val is None:
                raise MaterialStudyError(
                    "INVALID_CONFIGURATION",
                    "resolve_parameter_values",
                    {"reason": "FIXED_PARAMETER_NEEDS_DEFAULT", "parameter": p.id},
                )
            if p.id in provided:
                cand = float(provided[p.id])
                if not math.isclose(cand, float(val), rel_tol=1e-12, abs_tol=1e-12):
                    raise MaterialStudyError(
                        "INVALID_PARAMETER",
                        "resolve_parameter_values",
                        {"reason": "CANNOT_OVERRIDE_FIXED_PARAMETER", "parameter": p.id},
                    )
            values[p.id] = float(val)
        elif p.role == "free":
            raw_val = provided.get(p.id, p.default)
            if raw_val is None:
                raise MaterialStudyError(
                    "INVALID_PARAMETER",
                    "resolve_parameter_values",
                    {"reason": "MISSING_FREE_PARAMETER_VALUE", "parameter": p.id},
                )
            if isinstance(raw_val, bool) or not isinstance(raw_val, (int, float, np.integer, np.floating)):
                raise MaterialStudyError(
                    "INVALID_PARAMETER",
                    "resolve_parameter_values",
                    {"reason": "NON_NUMERIC_PARAMETER", "parameter": p.id},
                )
            fval = float(raw_val)
            if not math.isfinite(fval):
                raise MaterialStudyError(
                    "INVALID_PARAMETER",
                    "resolve_parameter_values",
                    {"reason": "NONFINITE_PARAMETER", "parameter": p.id},
                )
            if p.lower is not None and fval < p.lower - 1e-15:
                raise MaterialStudyError(
                    "INVALID_PARAMETER",
                    "resolve_parameter_values",
                    {"reason": "PARAMETER_BELOW_LOWER_BOUND", "parameter": p.id, "value": fval, "lower": p.lower},
                )
            if p.upper is not None and fval > p.upper + 1e-15:
                raise MaterialStudyError(
                    "INVALID_PARAMETER",
                    "resolve_parameter_values",
                    {"reason": "PARAMETER_ABOVE_UPPER_BOUND", "parameter": p.id, "value": fval, "upper": p.upper},
                )
            if p.lower is not None:
                fval = max(float(p.lower), fval)
            if p.upper is not None:
                fval = min(float(p.upper), fval)
            values[p.id] = fval

    # Resolve linked variables in topological order
    pending = [p for p in parameter_specs if p.role == "linked"]
    while pending:
        progressed = False
        remaining: list[ParameterSpec] = []
        for p in pending:
            if set(p.dependencies) <= set(values):
                try:
                    calc = float(
                        evaluate_expression(
                            str(p.expression),
                            {d: values[d] for d in p.dependencies},
                            units,
                            p.unit,
                            dependencies=p.dependencies,
                        )
                    )
                except Exception as exc:
                    raise MaterialStudyError(
                        "INVALID_PARAMETER",
                        "resolve_parameter_values",
                        {"reason": "LINKED_EXPRESSION_FAILED", "parameter": p.id, "error": str(exc)},
                    ) from exc
                if (p.lower is not None and calc < p.lower) or (p.upper is not None and calc > p.upper):
                    raise MaterialStudyError(
                        "INVALID_PARAMETER",
                        "resolve_parameter_values",
                        {"reason": "LINKED_VALUE_OUTSIDE_BOUNDS", "parameter": p.id, "value": calc},
                    )
                if p.id in provided and not math.isclose(float(provided[p.id]), calc, rel_tol=1e-12, abs_tol=1e-12):
                    raise MaterialStudyError(
                        "INVALID_PARAMETER",
                        "resolve_parameter_values",
                        {"reason": "LINKED_VALUE_DISAGREES", "parameter": p.id},
                    )
                values[p.id] = calc
                progressed = True
            else:
                remaining.append(p)
        if not progressed and remaining:
            raise MaterialStudyError(
                "INVALID_CONFIGURATION",
                "resolve_parameter_values",
                {"reason": "LINKED_PARAMETER_CYCLE_OR_MISSING_DEP", "parameters": [p.id for p in remaining]},
            )
        pending = remaining
    return values


def _safe_mdo_unit(unit: str) -> str:
    """Pass through units accepted by caelab.mdo.variables; fall back to '' for e.g. 'K'."""
    try:
        _mdo_parse_unit(unit)
        return unit
    except ValueError:
        return ""


def build_variable_space(parameter_specs: Sequence[ParameterSpec]) -> VariableSpace:
    """Construct caelab.mdo.VariableSpace for free parameters (and linked depending on free)."""
    free_specs = [p for p in parameter_specs if p.role == "free"]
    free_ids = {p.id for p in free_specs}
    variables: list[ContinuousVariable | LinkedVariable] = []
    for p in free_specs:
        if p.lower is None or p.upper is None:
            raise MaterialStudyError(
                "INVALID_CONFIGURATION",
                "build_variable_space",
                {"reason": "FREE_PARAMETER_REQUIRES_BOUNDS", "parameter": p.id},
            )
        initial = float(p.default) if p.default is not None else 0.5 * (float(p.lower) + float(p.upper))
        variables.append(
            ContinuousVariable(
                id=p.id,
                name=p.id,
                unit=_safe_mdo_unit(p.unit),
                lower=float(p.lower),
                upper=float(p.upper),
                initial=initial,
                transform=p.transform,
            )
        )
    # Include linked variables whose dependencies are strictly inside the VariableSpace
    known = set(free_ids)
    pending = [p for p in parameter_specs if p.role == "linked"]
    while pending:
        progressed = False
        rem: list[ParameterSpec] = []
        for p in pending:
            if set(p.dependencies) <= known:
                variables.append(
                    LinkedVariable(
                        id=p.id,
                        name=p.id,
                        unit=_safe_mdo_unit(p.unit),
                        expression=str(p.expression),
                        dependencies=p.dependencies,
                        lower=p.lower,
                        upper=p.upper,
                    )
                )
                known.add(p.id)
                progressed = True
            else:
                rem.append(p)
        if not progressed:
            break
        pending = rem
    return VariableSpace(variables=tuple(variables), id="material_space")


def compute_candidate_cache_key(
    *,
    content_revision: str,
    model_revision: str,
    adapter_id: str,
    adapter_version: str,
    implementation_sha256: str | None,
    physical_parameters: Mapping[str, Any],
    numerical_settings: Mapping[str, Any],
    execution_phase: str,
    planned_experiments: Sequence[ExperimentSpec],
) -> str:
    """Compute exact content-bound cache key including phase and planned experiment list (C4/C7)."""
    return canonical_sha256(
        {
            "content_revision": content_revision,
            "model_revision": model_revision,
            "adapter": {
                "id": adapter_id,
                "version": adapter_version,
                "implementation_sha256": implementation_sha256,
            },
            "physical_parameters": {k: float(v) if not isinstance(v, str) else v for k, v in sorted(physical_parameters.items())},
            "numerical_settings": _to_plain_json(numerical_settings),
            "execution_phase": execution_phase,
            "experiments": [
                {"experiment_id": exp.experiment_id, "experiment_revision": exp.experiment_revision}
                for exp in planned_experiments
            ],
        },
        exclude_keys=(),
    )


def _persist_channel_residual_arrays(
    *,
    data_root: Path,
    evaluation_id: str,
    channel_results: Sequence[ChannelResidualResult],
    experiments: Mapping[str, ExperimentSpec] | None = None,
    runtime_cache: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Save residual/observed/predicted arrays under arrays/residuals/<eval_id>/ and return summaries."""
    persisted: list[dict[str, Any]] = []
    shared_res_cache = runtime_cache.setdefault("shared_res_refs", {}) if runtime_cache is not None else None
    for item in channel_results:
        comp = item.comparison
        comp_slug = storage_segment(comp.id)
        base_rel = f"arrays/residuals/{evaluation_id}/{comp_slug}"

        def _save(rel: str, aid: str, role: str, arr: np.ndarray) -> ArtifactRefV1:
            sha = write_safe_npy_v2(rel, arr, root=data_root)
            size = (data_root / rel).stat().st_size
            return ArtifactRefV1(
                id=aid,
                role=role,
                path=rel,
                sha256=sha,
                size=size,
                format_id="application/x-npy-v2",
                producer=_PRODUCER,
            )

        pred_ref = _save(f"{base_rel}.pred.npy", f"res:{evaluation_id}:{comp_slug}:pred", "predicted_on_obs", item.predicted_on_obs)
        res_ref = _save(f"{base_rel}.residuals.npy", f"res:{evaluation_id}:{comp_slug}:residuals", "normalized_residuals", item.residuals)

        exp_rev = experiments[comp.experiment_id].experiment_revision if (experiments and comp.experiment_id in experiments) else ""
        skey = (comp.id, comp.sample_weights_policy, exp_rev)
        cached_triple = shared_res_cache.get(skey) if shared_res_cache is not None else None
        if cached_triple is not None and all((data_root / r.path).is_file() for r in cached_triple):
            coords_ref, obs_ref, wt_ref = cached_triple
        elif shared_res_cache is not None and exp_rev:
            rev_tag = exp_rev[:12]
            pol_tag = hashlib.sha256(str(comp.sample_weights_policy).encode("utf-8")).hexdigest()[:8]
            sbase = f"arrays/residuals/_shared/{comp_slug}_{rev_tag}_{pol_tag}"
            coords_ref = _save(f"{sbase}.coords.npy", f"res:_shared:{comp_slug}:{rev_tag}:{pol_tag}:coords", "observed_coordinates", item.observed_coords)
            obs_ref = _save(f"{sbase}.obs.npy", f"res:_shared:{comp_slug}:{rev_tag}:{pol_tag}:obs", "observed_values", item.observed_values)
            wt_ref = _save(f"{sbase}.weights.npy", f"res:_shared:{comp_slug}:{rev_tag}:{pol_tag}:weights", "quadrature_weights", item.weights)
            shared_res_cache[skey] = (coords_ref, obs_ref, wt_ref)
        else:
            coords_ref = _save(f"{base_rel}.coords.npy", f"res:{evaluation_id}:{comp_slug}:coords", "observed_coordinates", item.observed_coords)
            obs_ref = _save(f"{base_rel}.obs.npy", f"res:{evaluation_id}:{comp_slug}:obs", "observed_values", item.observed_values)
            wt_ref = _save(f"{base_rel}.weights.npy", f"res:{evaluation_id}:{comp_slug}:weights", "quadrature_weights", item.weights)

        persisted.append(
            {
                "comparison_id": comp.id,
                "experiment_id": comp.experiment_id,
                "observable_id": comp.observable_id,
                "split": comp.split,
                "scale": comp.scale,
                "loss": item.loss,
                "max_abs_error": item.max_abs_error,
                "rmse": item.rmse,
                "status": item.status,
                "observed_coords_ref": coords_ref.to_dict(),
                "observed_values_ref": obs_ref.to_dict(),
                "predicted_on_obs_ref": pred_ref.to_dict(),
                "residuals_ref": res_ref.to_dict(),
                "weights_ref": wt_ref.to_dict(),
            }
        )
    return tuple(persisted)


def _comparison_inputs(
    experiments: Sequence[ExperimentSpec],
    experiment_data: Mapping[str, Mapping[str, Any]],
    comparisons: Sequence[ComparisonSpec],
) -> tuple[tuple[ExperimentSpec, ...], dict[str, dict[str, Any]]]:
    channels: dict[str, set[str]] = {}
    for comp in comparisons:
        channels.setdefault(comp.experiment_id, set()).add(comp.observable_id)
    return (
        tuple(replace(exp, channels=tuple(ch for ch in exp.channels if ch.observable_id in channels[exp.experiment_id]))
              for exp in experiments if exp.experiment_id in channels),
        {eid: {cid: experiment_data[eid][cid] for cid in ids} for eid, ids in channels.items()},
    )


def _verify_prediction_experiments(
    bundle: PredictionBundleV1, request: MaterialEvaluationRequest, adapter_identity: AdapterIdentity,
) -> None:
    if (
        bundle.evaluation_id != request.evaluation_id or bundle.request_id != request.request_id
        or bundle.study_id != request.study_id or bundle.study_revision != request.study_revision
        or bundle.model_id != request.model_descriptor.model_id
        or bundle.model_revision != request.model_descriptor.model_revision
        or bundle.adapter != adapter_identity
        or bundle.parameter_set_sha256 != canonical_sha256(dict(request.physical_parameters), exclude_keys=())
    ):
        raise MaterialStudyError(
            "CORRUPT_ARTIFACT", "evaluate.collect", {"reason": "PREDICTION_PROVENANCE_MISMATCH"},
        )
    expected = {exp.experiment_id: exp for exp in request.experiments}
    for prediction in bundle.experiments:
        experiment = expected.get(prediction.experiment_id)
        if experiment is None or experiment.experiment_revision != prediction.experiment_revision:
            raise MaterialStudyError(
                "CORRUPT_ARTIFACT", "evaluate.collect",
                {"reason": "PREDICTION_EXPERIMENT_MISMATCH", "experiment_id": prediction.experiment_id,
                 "revision": prediction.experiment_revision},
            )
        if {ch.observable_id for ch in prediction.channels} - {ch.observable_id for ch in experiment.channels}:
            raise MaterialStudyError(
                "CORRUPT_ARTIFACT", "evaluate.collect",
                {"experiment_id": prediction.experiment_id, "reason": "UNDECLARED_PREDICTION_CHANNEL"},
            )


def compute_svd_identifiability(
    *,
    adapter: MaterialAdapter,
    model_descriptor: ModelDescriptorV1,
    parameter_specs: Sequence[ParameterSpec],
    base_physical: Mapping[str, float],
    experiments: Mapping[str, ExperimentSpec],
    experiment_data: Mapping[str, Mapping[str, Any]],
    comparisons: Sequence[ComparisonSpec],
    planned_experiments: Sequence[ExperimentSpec],
    numerical_settings: Mapping[str, Any],
    data_root: Path,
    study_id: str,
    content_revision: str,
    base_whitened: np.ndarray | None,
    space: VariableSpace,
    registered_responses: Sequence[ScalarResponseSpecV1],
    constraint_specs: Sequence[ConstraintSpec],
    assessment_policy: Mapping[str, Any],
    runtime_cache: dict[str, Any] | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[str, dict[str, Any]]:
    """Compute whitened residual Jacobian SVD identifiability in encoded [0,1] space (A2/B3)."""
    rcache = runtime_cache if runtime_cache is not None else {}
    free_vars = space.independent_variables
    free_ids = [v.id for v in free_vars]
    if not free_ids:
        return "not_computed", {"reason": "NO_FREE_PARAMETERS", "parameter_ids": []}
    if base_whitened is None or base_whitened.size == 0:
        return "not_computed", {"reason": "NO_WHITENED_RESIDUALS", "parameter_ids": free_ids}

    fit_experiments = tuple(exp for exp in planned_experiments if exp.split == "train")
    if not fit_experiments:
        return "not_computed", {"reason": "NO_TRAIN_EXPERIMENTS", "parameter_ids": free_ids}

    param_units = {p.id: p.unit for p in parameter_specs}
    planned_ids = [exp.experiment_id for exp in fit_experiments]
    fit_exp_id_set = set(planned_ids)
    fit_comparisons = tuple(c for c in comparisons if c.required_for_fit and c.experiment_id in fit_exp_id_set)
    fit_experiments, fit_data = _comparison_inputs(fit_experiments, experiment_data, fit_comparisons)

    def _eval_whitened(encoded_vec: np.ndarray) -> np.ndarray | None:
        if cancel_event is not None and cancel_event.is_set():
            return None
        clipped = np.clip(encoded_vec, 0.0, 1.0)
        phys_part = space.decode(clipped, purpose="optimizer", repair="nearest").values
        merged_overrides = {p.id: phys_part[p.id] for p in parameter_specs if p.role == "free" and p.id in phys_part}
        try:
            full_phys = resolve_parameter_values(parameter_specs, merged_overrides)
        except MaterialStudyError:
            return None
        tmp_eval_id = f"svd-{uuid.uuid4().hex[:12]}"
        req = MaterialEvaluationRequest(
            request_id=f"req-{tmp_eval_id}",
            evaluation_id=tmp_eval_id,
            study_id=study_id,
            study_revision=content_revision,
            model_descriptor=model_descriptor,
            parameter_specs=tuple(parameter_specs),
            physical_parameters=full_phys,
            experiments=fit_experiments,
            comparisons=fit_comparisons,
            numerical_settings=numerical_settings,
            execution_phase="exploration",
            selection_ref=None,
            experiment_data=fit_data,
            runtime_cache=rcache,
        )
        try:
            plan = adapter.plan(req)
            artifacts = adapter.execute(
                plan, req, data_root=data_root, study_id=study_id, cancel_event=cancel_event,
            )
            bundle = adapter.collect(plan, artifacts, data_root=data_root, study_id=study_id)
            _verify_prediction_experiments(bundle, req, adapter.identity)
            res_out = evaluate_bundle_residuals(
                bundle=bundle,
                experiments=experiments,
                experiment_data=experiment_data,
                comparisons=fit_comparisons,
                planned_experiment_ids=planned_ids,
                physical_parameters=full_phys,
                parameter_units=param_units,
                registered_responses=registered_responses,
                constraint_specs=(),
                assessment_policy={},
                policy_ref=None,
                data_root=data_root,
                execution_phase="exploration",
                runtime_cache=rcache,
            )
            return res_out.whitened_residual_vector
        except Exception:
            return None
        finally:
            import shutil

            shutil.rmtree(data_root / "arrays" / "predictions" / tmp_eval_id, ignore_errors=True)
            (data_root / "artifacts" / f"pyvisco-provenance-{tmp_eval_id}.json").unlink(missing_ok=True)

    space_inputs = {v.id: base_physical[v.id] for v in space.independent_variables}
    x0 = space.encode(space_inputs, purpose="optimizer").vector

    def _jacobian(step: float) -> tuple[np.ndarray, int, float, list[float]] | None:
        cols: list[np.ndarray] = []
        for i in range(len(free_ids)):
            xp = x0.copy()
            xm = x0.copy()
            xp[i] = min(1.0, float(x0[i]) + step)
            xm[i] = max(0.0, float(x0[i]) - step)
            dx = float(xp[i] - xm[i])
            if dx <= 0.0:
                return None
            rp = _eval_whitened(xp)
            rm = _eval_whitened(xm)
            if rp is None or rm is None or rp.shape != rm.shape:
                return None
            cols.append((rp - rm) / dx)
        jac = np.column_stack(cols)
        if not np.isfinite(jac).all():
            return None
        _, svals, vt = np.linalg.svd(jac, full_matrices=False)
        if svals.size == 0 or svals[0] <= 1e-14:
            return vt, 0, float("inf"), [float(v) for v in svals]
        rank = int(np.sum(svals >= 1e-6 * svals[0]))
        cond = float(svals[0] / svals[-1]) if svals[-1] > 0.0 else float("inf")
        return vt, rank, cond, [float(v) for v in svals]

    base_step = 1e-5
    res_mid = _jacobian(base_step)
    if res_mid is None:
        return "not_computed", {"reason": "JACOBIAN_EVALUATION_FAILED", "parameter_ids": free_ids}
    vt, rank_mid, cond_mid, svals_mid = res_mid

    # Step sensitivity stability check at 0.5h and 2h (A2)
    res_half = _jacobian(0.5 * base_step)
    res_double = _jacobian(2.0 * base_step)
    if res_half is not None and res_double is not None:
        if res_half[1] != rank_mid or res_double[1] != rank_mid:
            return "not_computed", {
                "reason": "NUMERICAL_SENSITIVITY_UNSTABLE",
                "parameter_ids": free_ids,
                "ranks_by_step": {"0.5h": res_half[1], "h": rank_mid, "2h": res_double[1]},
                "singular_values": svals_mid,
            }

    n_params = len(free_ids)
    nullspace = vt[rank_mid:].tolist() if rank_mid < n_params else []
    unidentifiable: list[str] = []
    if rank_mid < n_params and vt.shape[0] > 0:
        for idx, pid in enumerate(free_ids):
            if np.any(np.abs(vt[rank_mid:, idx]) > 0.1):
                unidentifiable.append(pid)

    if rank_mid < n_params or cond_mid > 1e6:
        status = "not_identifiable"
    else:
        status = "pass"

    return status, {
        "parameter_ids": free_ids,
        "parameter_count": n_params,
        "rank": rank_mid,
        "condition_number": None if not math.isfinite(cond_mid) else cond_mid,
        "singular_values": svals_mid,
        "nullspace": nullspace,
        "unidentifiable_parameters": unidentifiable,
    }


def execute_single_evaluation(
    *,
    evaluation_id: str,
    request_id: str,
    campaign_id: str | None,
    study_id: str,
    content_revision: str,
    adapter: MaterialAdapter,
    model_descriptor: ModelDescriptorV1,
    parameter_specs: Sequence[ParameterSpec],
    physical_parameters: Mapping[str, float],
    experiments: Mapping[str, ExperimentSpec],
    experiment_data: Mapping[str, Mapping[str, Any]],
    comparisons: Sequence[ComparisonSpec],
    planned_experiments: Sequence[ExperimentSpec],
    numerical_settings: Mapping[str, Any],
    execution_phase: str,
    selection_ref: ArtifactRefV1 | None,
    registered_responses: Sequence[ScalarResponseSpecV1],
    constraint_specs: Sequence[ConstraintSpec],
    assessment_policy: Mapping[str, Any],
    policy_ref: ArtifactRefV1 | Mapping[str, Any] | None,
    data_root: Path,
    cancel_event: threading.Event | None = None,
    stop_event: threading.Event | None = None,
    finalize: Callable[[], None] | None = None,
    compute_identifiability: bool = True,
    extra_split_checks: Sequence[Mapping[str, Any]] = (),
    extra_warnings: Sequence[str] = (),
    runtime_cache: dict[str, Any] | None = None,
) -> tuple[CalibrationEvaluationArtifact, ArtifactRefV1, ResidualEvaluationOutput]:
    """Execute validate -> plan -> execute -> collect -> residuals and persist CalibrationEvaluationArtifact."""
    rcache = runtime_cache if runtime_cache is not None else {}
    param_units = {p.id: p.unit for p in parameter_specs}
    planned_ids = [exp.experiment_id for exp in planned_experiments]
    planned_set = set(planned_ids)
    relevant_comparisons = [c for c in comparisons if c.experiment_id in planned_set]

    cap = adapter.capabilities(model_descriptor, None)
    bundle: PredictionBundleV1 | None = None
    failure_reasons: list[str] = []
    diagnostics: list[Any] = []
    eval_state = "completed"

    split_failures: dict[str, str] = {}
    executed_holdout_ids: list[str] = []
    training = tuple(exp for exp in planned_experiments if exp.split == "train")
    groups = [("train", training)] if training else []
    groups.extend(
        (split, (exp,))
        for split in ("validation", "holdout") for exp in planned_experiments if exp.split == split
    )
    for index, (split, group) in enumerate(groups):
        if stop_event is not None and stop_event.is_set():
            break
        group_ids = {exp.experiment_id for exp in group}
        comps = tuple(c for c in relevant_comparisons if c.experiment_id in group_ids)
        inputs, arrays = _comparison_inputs(group, experiment_data, comps)
        suffix = f"-{split}-{index}" if split != "train" else ""
        req = MaterialEvaluationRequest(
            request_id=request_id + suffix,
            evaluation_id=evaluation_id + suffix,
            study_id=study_id,
            study_revision=content_revision,
            model_descriptor=model_descriptor,
            parameter_specs=tuple(parameter_specs),
            physical_parameters=physical_parameters,
            experiments=inputs,
            comparisons=comps,
            numerical_settings=numerical_settings,
            execution_phase=execution_phase,
            selection_ref=selection_ref,
            experiment_data=arrays,
            runtime_cache=rcache,
        )
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise MaterialStudyError("CANCELLED", "evaluate", {"reason": "USER_CANCEL"})
            validate_request = getattr(adapter, "validate_request", None)
            if validate_request is not None:
                diagnostics.extend(validate_request(req))
            else:
                diagnostics.extend(adapter.validate(model_descriptor, physical_parameters, inputs, numerical_settings))
            plan = adapter.plan(req)
            if (
                plan.evaluation_id != req.evaluation_id or plan.request_id != req.request_id
                or plan.study_revision != req.study_revision or plan.model_id != req.model_descriptor.model_id
                or plan.model_revision != req.model_descriptor.model_revision or plan.adapter != adapter.identity
                or plan.execution_phase != req.execution_phase
                or dict(plan.physical_parameters) != dict(req.physical_parameters)
                or plan.experiment_ids != tuple(exp.experiment_id for exp in req.experiments)
            ):
                raise MaterialStudyError(
                    "CORRUPT_ARTIFACT", "evaluate.collect", {"reason": "PREDICTION_PROVENANCE_MISMATCH"},
                )
            if cancel_event is not None and cancel_event.is_set():
                raise MaterialStudyError("CANCELLED", "evaluate", {"reason": "USER_CANCEL"})
            artifacts = adapter.execute(plan, req, data_root=data_root, study_id=study_id, cancel_event=cancel_event)
            if split == "holdout":
                executed_holdout_ids.extend(exp.experiment_id for exp in inputs)
            if cancel_event is not None and cancel_event.is_set():
                raise MaterialStudyError("CANCELLED", "evaluate", {"reason": "USER_CANCEL"})
            collected = adapter.collect(plan, artifacts, data_root=data_root, study_id=study_id)
            collected.validate_level(cap.level)
            _verify_prediction_experiments(collected, req, adapter.identity)
        except Exception as exc:
            code = exc.code if isinstance(exc, MaterialStudyError) else "INTEGRATION_FAILED"
            diagnostic = exc.to_dict() if isinstance(exc, MaterialStudyError) else {"code": code, "error": str(exc)}
            diagnostics.append(diagnostic)
            if code == "CORRUPT_ARTIFACT" and exc.stage == "evaluate.collect":
                raise
            if split != "train":
                split_failures.update(dict.fromkeys(group_ids, code))
            else:
                failure_reasons.append(code)
            if code == "CANCELLED":
                eval_state = "cancelled"
                break
            if split == "train":
                eval_state = "failed"
            continue
        if bundle is None:
            # The stored bundle is a host aggregate. The producer IDs were verified above.
            bundle = replace(
                collected, evaluation_id=evaluation_id, request_id=request_id,
                diagnostics=(*collected.diagnostics, {"producer_request_id": collected.request_id}),
            )
        else:
            bundle = replace(
                bundle,
                experiments=(*bundle.experiments, *collected.experiments),
                artifacts=tuple({ref.id: ref for ref in (*bundle.artifacts, *collected.artifacts)}.values()),
                artifact_owners={**bundle.artifact_owners, **collected.artifact_owners},
                run_ids=tuple(dict.fromkeys((*bundle.run_ids, *collected.run_ids))),
                diagnostics=(*bundle.diagnostics, *collected.diagnostics, {"producer_request_id": collected.request_id}),
            )

    # A stop before dispatch has no holdout result or exposure.
    relevant_comparisons = [
        comp for comp in relevant_comparisons
        if comp.split != "holdout" or comp.experiment_id in executed_holdout_ids or comp.experiment_id in split_failures
    ]

    def assess(identifiability: str = "not_computed") -> ResidualEvaluationOutput:
        return evaluate_bundle_residuals(
            bundle=bundle,
            experiments=experiments,
            experiment_data=experiment_data,
            comparisons=relevant_comparisons,
            planned_experiment_ids=planned_ids,
            physical_parameters=physical_parameters,
            parameter_units=param_units,
            registered_responses=registered_responses,
            constraint_specs=constraint_specs,
            assessment_policy=assessment_policy,
            policy_ref=policy_ref,
            data_root=data_root,
            execution_phase=execution_phase,
            identifiability_status=identifiability,
            extra_split_checks=extra_split_checks,
            extra_warnings=extra_warnings,
            failure_reason_codes=failure_reasons,
            split_failures=split_failures,
            runtime_cache=rcache,
        )

    res_out = assess()
    ident_status = "not_computed"
    ident_info: dict[str, Any] = {}
    if (
        compute_identifiability and eval_state == "completed" and res_out.objective_eligible
        and (stop_event is None or not stop_event.is_set())
    ):
        space = build_variable_space(parameter_specs)
        ident_status, ident_info = compute_svd_identifiability(
            adapter=adapter,
            model_descriptor=model_descriptor,
            parameter_specs=parameter_specs,
            base_physical=physical_parameters,
            experiments=experiments,
            experiment_data=experiment_data,
            comparisons=relevant_comparisons,
            planned_experiments=training,
            numerical_settings=numerical_settings,
            data_root=data_root,
            study_id=study_id,
            content_revision=content_revision,
            base_whitened=res_out.whitened_residual_vector,
            space=space,
            registered_responses=registered_responses,
            constraint_specs=constraint_specs,
            assessment_policy=assessment_policy,
            runtime_cache=rcache,
            cancel_event=cancel_event,
        )
    if finalize is not None:
        finalize()
    if cancel_event is not None and cancel_event.is_set():
        eval_state = "cancelled"
        if "CANCELLED" not in failure_reasons:
            failure_reasons.append("CANCELLED")
            diagnostics.append({"code": "CANCELLED"})
        ident_status, ident_info = "not_computed", {}
        res_out = assess()
    elif ident_status != "not_computed":
        res_out = assess(ident_status)
    if eval_state != "cancelled" and stop_event is not None and stop_event.is_set():
        eval_state = "stopped"
    elif eval_state == "completed" and not res_out.objective_eligible:
        eval_state = "failed"

    persisted_channels = _persist_channel_residual_arrays(
        data_root=data_root,
        evaluation_id=evaluation_id,
        channel_results=res_out.channel_results,
        experiments=experiments,
        runtime_cache=rcache,
    )
    bundle_sha = canonical_sha256(bundle.to_dict(), exclude_keys=()) if bundle is not None else "0" * 64
    source_id = f"material-evaluation:{evaluation_id}:{bundle_sha[:16]}"
    run_ids = bundle.run_ids if bundle is not None else ()

    artifact = CalibrationEvaluationArtifact(
        evaluation_id=evaluation_id,
        campaign_id=campaign_id,
        study_id=study_id,
        content_revision=content_revision,
        model_id=model_descriptor.model_id,
        model_revision=model_descriptor.model_revision,
        adapter=adapter.identity,
        execution_phase=execution_phase,
        selection_ref=selection_ref,
        physical_parameters=physical_parameters,
        numerical_settings=numerical_settings,
        state=eval_state,
        cache_hit=False,
        holdout_exposed=bool(executed_holdout_ids),
        executed_holdout_experiment_ids=tuple(executed_holdout_ids),
        prediction_bundle=bundle,
        assessment=res_out.assessment,
        channel_results=persisted_channels,
        identifiability=ident_info,
        optimizer_scalars=res_out.all_scalars,
        source_id=source_id,
        run_ids=run_ids,
        diagnostics=tuple(diagnostics),
        created_at=_utc_now(),
    )
    eval_ref = _write_json_artifact(
        data_root,
        f"evaluations/{evaluation_id}.json",
        artifact.to_dict(),
        artifact_id=f"eval:{evaluation_id}",
        role="calibration_evaluation",
    )
    return artifact, eval_ref, res_out


class MaterialBatchEvaluator(BatchEvaluator):
    """caelab.mdo.optimize.BatchEvaluator executing actual MaterialAdapter evaluations during exploration."""

    def __init__(
        self,
        *,
        campaign_id: str,
        study_id: str,
        content_revision: str,
        adapter: MaterialAdapter,
        model_descriptor: ModelDescriptorV1,
        parameter_specs: Sequence[ParameterSpec],
        experiments: Mapping[str, ExperimentSpec],
        experiment_data: Mapping[str, Mapping[str, Any]],
        comparisons: Sequence[ComparisonSpec],
        planned_experiments: Sequence[ExperimentSpec],
        numerical_settings: Mapping[str, Any],
        registered_responses: Sequence[ScalarResponseSpecV1],
        constraint_specs: Sequence[ConstraintSpec],
        assessment_policy: Mapping[str, Any],
        policy_ref: ArtifactRefV1 | Mapping[str, Any] | None,
        data_root: Path,
        cancel_event: threading.Event,
        stop_event: threading.Event,
        cache_lookup: Callable[[str], CalibrationEvaluationArtifact | None],
        on_evaluation_recorded: Callable[[CalibrationEvaluationArtifact, ArtifactRefV1, str, str], None],
        on_progress: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self.campaign_id = campaign_id
        self.study_id = study_id
        self.content_revision = content_revision
        self.adapter = adapter
        self.model_descriptor = model_descriptor
        self.parameter_specs = tuple(parameter_specs)
        self.experiments = experiments
        self.experiment_data = experiment_data
        self.comparisons = tuple(comparisons)
        # Holdout MUST NEVER appear in exploration planned_experiments!
        self.planned_experiments = tuple(exp for exp in planned_experiments if exp.split != "holdout")
        self.numerical_settings = numerical_settings
        self.registered_responses = tuple(registered_responses)
        self.constraint_specs = tuple(constraint_specs)
        self.assessment_policy = assessment_policy
        self.policy_ref = policy_ref
        self.data_root = data_root
        self.cancel_event = cancel_event
        self.stop_event = stop_event
        self.cache_lookup = cache_lookup
        self.on_evaluation_recorded = on_evaluation_recorded
        self.on_progress = on_progress
        self.evaluation_ids: list[str] = []
        self.completed_count = 0
        self.failed_count = 0
        self.point_to_evaluation: dict[str, str] = {}
        self.runtime_cache: dict[str, Any] = {}

    def evaluate_batch(self, requests: tuple[EvaluationRequest, ...]) -> Mapping[str, EvaluationObservation]:
        observations: dict[str, EvaluationObservation] = {}
        for req in requests:
            # Stop lets all already-dispatched requests in this batch complete their required experiments;
            # only hard cancel aborts remaining requests in the current batch.
            if self.cancel_event.is_set():
                observations[req.id] = EvaluationObservation(
                    request_id=req.id,
                    point_id=req.point_id,
                    valid=False,
                    reason="CANCELLED",
                    mode="ACTUAL",
                    responses={},
                )
                continue

            eval_id = f"eval-{uuid.uuid4().hex[:16]}"
            overrides = {p.id: req.values[p.id] for p in self.parameter_specs if p.role == "free" and p.id in req.values}
            full_phys = resolve_parameter_values(self.parameter_specs, overrides)

            cache_key = compute_candidate_cache_key(
                content_revision=self.content_revision,
                model_revision=self.model_descriptor.model_revision,
                adapter_id=self.adapter.identity.id,
                adapter_version=self.adapter.identity.version,
                implementation_sha256=self.adapter.identity.implementation_sha256,
                physical_parameters=full_phys,
                numerical_settings=self.numerical_settings,
                execution_phase="exploration",
                planned_experiments=self.planned_experiments,
            )
            cached = self.cache_lookup(cache_key)
            if cached is not None and cached.state == "completed" and cached.assessment.objective["eligible"]:
                self.completed_count += 1
                observations[req.id] = EvaluationObservation(
                    request_id=req.id,
                    point_id=req.point_id,
                    responses=dict(cached.optimizer_scalars),
                    valid=True,
                    mode="ACTUAL",
                    run_ids=cached.run_ids,
                    source_id=cached.source_id,
                    native_attempts=0,
                    cache_hit=True,
                )
                self.evaluation_ids.append(cached.evaluation_id)
                self.point_to_evaluation[req.point_id] = cached.evaluation_id
                continue

            artifact, eval_ref, res_out = execute_single_evaluation(
                evaluation_id=eval_id,
                request_id=req.id,
                campaign_id=self.campaign_id,
                study_id=self.study_id,
                content_revision=self.content_revision,
                adapter=self.adapter,
                model_descriptor=self.model_descriptor,
                parameter_specs=self.parameter_specs,
                physical_parameters=full_phys,
                experiments=self.experiments,
                experiment_data=self.experiment_data,
                comparisons=self.comparisons,
                planned_experiments=self.planned_experiments,
                numerical_settings=self.numerical_settings,
                execution_phase="exploration",
                selection_ref=None,
                registered_responses=self.registered_responses,
                constraint_specs=self.constraint_specs,
                assessment_policy=self.assessment_policy,
                policy_ref=self.policy_ref,
                data_root=self.data_root,
                cancel_event=self.cancel_event,
                compute_identifiability=False,
                runtime_cache=self.runtime_cache,
            )
            self.on_evaluation_recorded(artifact, eval_ref, req.point_id, req.id)
            self.evaluation_ids.append(eval_id)
            self.point_to_evaluation[req.point_id] = eval_id

            if res_out.objective_eligible and artifact.state == "completed":
                self.completed_count += 1
                obs = EvaluationObservation(
                    request_id=req.id,
                    point_id=req.point_id,
                    responses=dict(res_out.all_scalars),
                    valid=True,
                    mode="ACTUAL",
                    run_ids=artifact.run_ids,
                    source_id=artifact.source_id,
                    native_attempts=len(artifact.run_ids),
                    cache_hit=False,
                )
            else:
                self.failed_count += 1
                reason = res_out.reason_codes[0] if res_out.reason_codes else "EVALUATION_FAILED"
                obs = EvaluationObservation(
                    request_id=req.id,
                    point_id=req.point_id,
                    responses={},
                    valid=False,
                    reason=reason,
                    mode="ACTUAL",
                    run_ids=artifact.run_ids,
                    source_id=artifact.source_id,
                    native_attempts=len(artifact.run_ids),
                    cache_hit=False,
                )
            observations[req.id] = obs

            if self.on_progress is not None:
                # Exploration progress notifications NEVER include holdout data!
                self.on_progress(
                    {
                        "stage": "candidate_evaluated",
                        "campaign_id": self.campaign_id,
                        "evaluation_id": eval_id,
                        "point_id": req.point_id,
                        "content_revision": self.content_revision,
                        "fit_loss": res_out.fit_loss,
                        "eligible": res_out.objective_eligible,
                        "completed": self.completed_count,
                        "failed": self.failed_count,
                    }
                )
        return observations
