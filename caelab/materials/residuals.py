"""CORE02 deterministic grid interpolation, quadrature weights, normalized residuals, and C4 AssessmentResultV1."""
from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from dataclasses import replace
import hashlib
import math
from pathlib import Path
from typing import Any

import numpy as np

from caelab.artifact_refs import (
    ArtifactRefV1,
    _to_plain_json,
)
from caelab.materials.contracts import (
    AssessmentResultV1,
    ComparisonSpec,
    ExperimentSpec,
    ObservableSpec,
    PredictedChannelV1,
    PredictionBundleV1,
    ScalarResponseSpecV1,
)
from caelab.materials.experiments import _read_verified_array, load_experiment_channel_arrays
from caelab.materials.registry import MaterialStudyError
from caelab.materials.units import QuantityUnitSpec, convert_frequency_angular, convert_values
from caelab.mdo.constraints import ConstraintSpec, Feasibility, assess_constraints
from caelab.mdo.variables import evaluate_expression

__all__ = [
    "ChannelResidualResult",
    "ResidualEvaluationOutput",
    "build_normalization_payload",
    "compute_sample_weights",
    "evaluate_bundle_residuals",
    "interpolate_to_observed_grid",
]


class ChannelResidualResult:
    """Computed residual arrays and scalar summary for one ComparisonSpec."""

    def __init__(
        self,
        *,
        comparison: ComparisonSpec,
        observed_coords: np.ndarray,
        observed_values: np.ndarray,
        predicted_on_obs: np.ndarray,
        residuals: np.ndarray,
        weights: np.ndarray,
        loss: float,
        max_abs_error: float,
        rmse: float,
        status: str,
        reason_codes: tuple[str, ...] = (),
    ) -> None:
        self.comparison = comparison
        self.observed_coords = observed_coords
        self.observed_values = observed_values
        self.predicted_on_obs = predicted_on_obs
        self.residuals = residuals
        self.weights = weights
        self.loss = loss
        self.max_abs_error = max_abs_error
        self.rmse = rmse
        self.status = status
        self.reason_codes = reason_codes


class ResidualEvaluationOutput:
    """Host-evaluated residuals, scalar responses, and canonical AssessmentResultV1."""

    def __init__(
        self,
        *,
        assessment: AssessmentResultV1,
        channel_results: tuple[ChannelResidualResult, ...],
        whitened_residual_vector: np.ndarray | None,
        all_scalars: dict[str, float | None],
        objective_eligible: bool,
        fit_loss: float | None,
        reason_codes: tuple[str, ...],
    ) -> None:
        self.assessment = assessment
        self.channel_results = channel_results
        self.whitened_residual_vector = whitened_residual_vector
        self.all_scalars = all_scalars
        self.objective_eligible = objective_eligible
        self.fit_loss = fit_loss
        self.reason_codes = reason_codes


def compute_sample_weights(coords: np.ndarray, policy: str = "uniform") -> np.ndarray:
    """Compute normalized quadrature/sample weights q >= 0, sum(q) == 1 (C4/B3)."""
    arr = np.asarray(coords, dtype=np.float64)
    if arr.ndim != 1 or arr.size == 0:
        raise MaterialStudyError(
            "DATA_RECIPE_INCOMPATIBLE",
            "compute_sample_weights",
            {"reason": "EMPTY_OR_NON_1D_COORDS"},
        )
    if not np.isfinite(arr).all():
        raise MaterialStudyError(
            "NONFINITE_RESPONSE",
            "compute_sample_weights",
            {"reason": "NONFINITE_COORDS"},
        )
    n = arr.size
    if policy == "uniform":
        q = np.full(n, 1.0 / n, dtype=np.float64)
    elif policy == "trapezoidal":
        if n == 1:
            q = np.ones(1, dtype=np.float64)
        else:
            diffs = np.abs(np.diff(arr))
            span = float(np.sum(diffs))
            if span <= 0.0:
                raise MaterialStudyError(
                    "DATA_RECIPE_INCOMPATIBLE",
                    "compute_sample_weights",
                    {"reason": "ZERO_COORDINATE_SPAN"},
                )
            q = np.zeros(n, dtype=np.float64)
            q[0] = 0.5 * diffs[0]
            q[-1] = 0.5 * diffs[-1]
            if n > 2:
                q[1:-1] = 0.5 * (diffs[:-1] + diffs[1:])
            q /= float(np.sum(q))
    elif policy == "log10_trapezoidal":
        if not np.all(arr > 0.0):
            raise MaterialStudyError(
                "DATA_RECIPE_INCOMPATIBLE",
                "compute_sample_weights",
                {"reason": "LOG10_TRAPEZOIDAL_REQUIRES_POSITIVE_COORDS"},
            )
        if n == 1:
            q = np.ones(1, dtype=np.float64)
        else:
            z = np.log10(arr)
            diffs = np.abs(np.diff(z))
            span = float(np.sum(diffs))
            if span <= 0.0:
                raise MaterialStudyError(
                    "DATA_RECIPE_INCOMPATIBLE",
                    "compute_sample_weights",
                    {"reason": "ZERO_LOG_COORDINATE_SPAN"},
                )
            q = np.zeros(n, dtype=np.float64)
            q[0] = 0.5 * diffs[0]
            q[-1] = 0.5 * diffs[-1]
            if n > 2:
                q[1:-1] = 0.5 * (diffs[:-1] + diffs[1:])
            q /= float(np.sum(q))
    else:
        raise MaterialStudyError(
            "DATA_RECIPE_INCOMPATIBLE",
            "compute_sample_weights",
            {"reason": "UNKNOWN_SAMPLE_WEIGHTS_POLICY", "policy": policy},
        )
    if np.any(q < 0.0) or not np.isfinite(q).all() or not math.isclose(float(np.sum(q)), 1.0, rel_tol=1e-12, abs_tol=1e-12):
        raise MaterialStudyError(
            "DATA_RECIPE_INCOMPATIBLE",
            "compute_sample_weights",
            {"reason": "INVALID_NORMALIZED_WEIGHTS"},
        )
    return q


def _verify_and_read_channel_ref(
    ref: ArtifactRefV1,
    data_root: Path,
    runtime_cache: dict[str, Any] | None = None,
) -> np.ndarray:
    if runtime_cache is not None:
        vcache = runtime_cache.setdefault("verified_arrays", {})
        ckey = (ref.path, ref.sha256, ref.size)
        if ckey in vcache:
            return vcache[ckey]
    arr = _read_verified_array(ref, data_root, stage="residuals.read_channel")
    if runtime_cache is not None and "_shared" in ref.path:
        vcache[ckey] = arr
    return arr


def _check_observable_compatibility(obs_expected: ObservableSpec, obs_pred: ObservableSpec) -> None:
    for field_name in ("quantity_kind", "measure", "location", "basis", "shear_convention", "value_kind", "representation"):
        if getattr(obs_expected, field_name) != getattr(obs_pred, field_name):
            raise MaterialStudyError(
                "MEASURE_MISMATCH",
                "residuals.check_observable",
                {
                    "reason": "OBSERVABLE_CONTRACT_MISMATCH",
                    "field": field_name,
                    "expected": getattr(obs_expected, field_name),
                    "got": getattr(obs_pred, field_name),
                },
            )
    if obs_expected.component_order and obs_pred.component_order and obs_expected.component_order != obs_pred.component_order:
        raise MaterialStudyError(
            "MEASURE_MISMATCH",
            "residuals.check_observable",
            {
                "reason": "COMPONENT_ORDER_MISMATCH",
                "expected": list(obs_expected.component_order),
                "got": list(obs_pred.component_order),
            },
        )


def interpolate_to_observed_grid(
    x_obs: np.ndarray,
    obs_mask: np.ndarray,
    sample_keys_obs: np.ndarray,
    pred_channel: PredictedChannelV1,
    *,
    data_root: Path,
    expected_obs_spec: ObservableSpec,
    runtime_cache: dict[str, Any] | None = None,
) -> np.ndarray:
    """Deterministically evaluate or linearly interpolate pred_channel onto fixed x_obs (no extrapolation)."""
    _check_observable_compatibility(expected_obs_spec, pred_channel.observable)

    cov = pred_channel.coverage
    expected_n = cov.get("expected")
    valid_n = cov.get("valid")
    missing_n = cov.get("missing", 0)
    if expected_n is None or valid_n is None or int(valid_n) < int(expected_n) or int(missing_n) > 0:
        raise MaterialStudyError(
            "COVERAGE_INCOMPLETE",
            "residuals.interpolate",
            {
                "reason": "COVERAGE_INCOMPLETE",
                "observable_id": pred_channel.observable_id,
                "coverage": dict(cov),
            },
        )

    x_pred = _verify_and_read_channel_ref(pred_channel.independent_ref, data_root, runtime_cache)
    y_pred = _verify_and_read_channel_ref(pred_channel.values_ref, data_root, runtime_cache)
    sk_pred = _verify_and_read_channel_ref(pred_channel.sample_keys_ref, data_root, runtime_cache)
    pred_mask = _verify_and_read_channel_ref(pred_channel.mask_ref, data_root, runtime_cache)

    if x_pred.ndim != 1 or y_pred.ndim != 2 or pred_mask.ndim != 2:
        raise MaterialStudyError(
            "COVERAGE_INCOMPLETE",
            "residuals.interpolate",
            {"reason": "INVALID_PREDICTION_ARRAY_SHAPE", "observable_id": pred_channel.observable_id},
        )
    if x_pred.shape[0] == 0 or y_pred.shape[0] != x_pred.shape[0] or pred_mask.shape != y_pred.shape:
        raise MaterialStudyError(
            "COVERAGE_INCOMPLETE",
            "residuals.interpolate",
            {"reason": "PREDICTION_SHAPE_MISMATCH", "observable_id": pred_channel.observable_id},
        )
    if not np.isfinite(x_pred).all() or not np.isfinite(y_pred).all():
        raise MaterialStudyError(
            "NONFINITE_RESPONSE",
            "residuals.interpolate",
            {"reason": "NONFINITE_PREDICTION_VALUES", "observable_id": pred_channel.observable_id},
        )
    if not bool(np.all(pred_mask)):
        raise MaterialStudyError(
            "COVERAGE_INCOMPLETE",
            "residuals.interpolate",
            {"reason": "MASKED_PREDICTION_SAMPLES", "observable_id": pred_channel.observable_id},
        )

    if pred_channel.observable.unit != expected_obs_spec.unit:
        try:
            y_pred = np.asarray(
                convert_values(
                    y_pred,
                    QuantityUnitSpec(pred_channel.observable.unit, pred_channel.observable.quantity_kind),
                    QuantityUnitSpec(expected_obs_spec.unit, expected_obs_spec.quantity_kind),
                ),
                dtype=np.float64,
            )
        except ValueError as exc:
            raise MaterialStudyError(
                "UNSUPPORTED_UNIT",
                "residuals.interpolate",
                {"reason": "PREDICTION_UNIT_CONVERSION_FAILED", "error": str(exc)},
            ) from exc

    if y_pred.shape[1] != obs_mask.shape[1]:
        raise MaterialStudyError(
            "MEASURE_MISMATCH",
            "residuals.interpolate",
            {
                "reason": "CHANNEL_COMPONENT_COUNT_MISMATCH",
                "expected": int(obs_mask.shape[1]),
                "got": int(y_pred.shape[1]),
            },
        )

    if x_obs.size == 0:
        raise MaterialStudyError("COVERAGE_INCOMPLETE", "residuals.interpolate", {"reason": "EMPTY_OBSERVED_GRID"})
    x_min, x_max = float(np.min(x_pred)), float(np.max(x_pred))
    if float(np.min(x_obs)) < x_min or float(np.max(x_obs)) > x_max:
        valid = int(np.count_nonzero((x_obs >= x_min) & (x_obs <= x_max)))
        raise MaterialStudyError(
            "COVERAGE_INCOMPLETE",
            "residuals.interpolate",
            {
                "reason": "EXTRAPOLATION_FORBIDDEN",
                "observable_id": pred_channel.observable_id,
                "obs_range": [float(np.min(x_obs)), float(np.max(x_obs))],
                "pred_range": [x_min, x_max],
                "coverage": {"expected": int(x_obs.size), "valid": valid, "missing": int(x_obs.size) - valid},
            },
        )

    # Grid and sample key match handles repeated cyclic coordinates without interpolation ambiguity.
    if (
        x_pred.shape == x_obs.shape
        and sk_pred.shape == sample_keys_obs.shape
        and np.array_equal(sk_pred, sample_keys_obs)
        and np.array_equal(x_pred, x_obs)
    ):
        return y_pred.astype(np.float64, copy=True)

    # Otherwise perform strict monotonic linear interpolation without extrapolation.
    if x_pred.size == 1:
        if x_obs.size == 1 and float(x_obs[0]) == float(x_pred[0]):
            return y_pred.astype(np.float64, copy=True)
        raise MaterialStudyError(
            "COVERAGE_INCOMPLETE",
            "residuals.interpolate",
            {"reason": "EXTRAPOLATION_FORBIDDEN", "observable_id": pred_channel.observable_id},
        )

    diffs = np.diff(x_pred)
    if np.all(diffs > 0.0):
        xs, ys = x_pred, y_pred
    elif np.all(diffs < 0.0):
        xs, ys = x_pred[::-1], y_pred[::-1]
    else:
        raise MaterialStudyError(
            "COVERAGE_INCOMPLETE",
            "residuals.interpolate",
            {"reason": "NON_MONOTONIC_PREDICTION_GRID", "observable_id": pred_channel.observable_id},
        )

    out = np.empty((x_obs.shape[0], ys.shape[1]), dtype=np.float64)
    for idx, xi in enumerate(x_obs):
        x_val = float(xi)
        # Exact coordinate hit uses the exact predicted value without interpolation roundoff.
        exact_matches = np.flatnonzero(xs == x_val)
        if exact_matches.size > 0:
            out[idx] = ys[int(exact_matches[0])]
            continue
        right = int(np.searchsorted(xs, x_val, side="right"))
        if right <= 0 or right >= xs.size:
            raise MaterialStudyError(
                "COVERAGE_INCOMPLETE",
                "residuals.interpolate",
                {"reason": "EXTRAPOLATION_FORBIDDEN", "x": x_val},
            )
        left = right - 1
        span = float(xs[right] - xs[left])
        t = (x_val - float(xs[left])) / span
        out[idx] = (1.0 - t) * ys[left] + t * ys[right]

    if not np.isfinite(out).all():
        raise MaterialStudyError(
            "NONFINITE_RESPONSE",
            "residuals.interpolate",
            {"reason": "NONFINITE_INTERPOLATED_VALUE", "observable_id": pred_channel.observable_id},
        )
    return out


def observed_rows_overlap(
    fit: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    other: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    *,
    fit_grid: QuantityUnitSpec,
    other_grid: QuantityUnitSpec,
    fit_observable: ObservableSpec,
    other_observable: ObservableSpec,
) -> bool:
    """Compare complete observed rows in the same units, allowing conversion roundoff."""
    if fit_observable != replace(other_observable, unit=fit_observable.unit):
        return False
    fit_x, fit_y, _, fit_mask = fit
    other_x, other_y, _, other_mask = other
    converted_grid = other_grid != fit_grid
    converted_values = other_observable.unit != fit_observable.unit
    try:
        if {fit_grid.quantity_kind, other_grid.quantity_kind} == {"frequency", "angular_frequency"}:
            other_x = convert_frequency_angular(other_x, other_grid, fit_grid)
        elif converted_grid:
            other_x = convert_values(other_x, other_grid, fit_grid)
        if converted_values:
            other_y = convert_values(
                other_y,
                QuantityUnitSpec(other_observable.unit, other_observable.quantity_kind),
                QuantityUnitSpec(fit_observable.unit, fit_observable.quantity_kind),
            )
    except ValueError:
        return False  # Distinct physical quantities do not share sample rows.

    other_valid = np.flatnonzero(np.all(other_mask, axis=1))
    order = other_valid[np.argsort(other_x[other_valid])]
    sorted_x = other_x[order]
    fit_valid = np.all(fit_mask, axis=1)
    coords = fit_x[fit_valid]
    tolerance = 8 * np.finfo(np.float64).eps
    radius = (tolerance if converted_grid else 0.0) * np.abs(coords)
    value_tolerance = tolerance if converted_values else 0.0
    left = np.searchsorted(sorted_x, coords - radius)
    right = np.searchsorted(sorted_x, coords + radius, side="right")
    for row, start, end in zip(fit_y[fit_valid], left, right):
        if end > start and np.any(np.all(np.isclose(other_y[order[start:end]], row, rtol=value_tolerance, atol=0), axis=1)):
            return True
    return False


def build_normalization_payload(
    *,
    experiments: Mapping[str, ExperimentSpec],
    experiment_data: Mapping[str, Mapping[str, Any]],
    comparisons: Sequence[ComparisonSpec],
    data_root: Path,
    schema_version: int = 2,
) -> dict[str, Any]:
    """Build deterministic frozen normalization payload (C4) from train comparisons and observed grids."""
    fit_comps = [c for c in comparisons if c.required_for_fit]
    total_fit_weight = math.fsum(c.weight for c in fit_comps)
    if fit_comps and total_fit_weight <= 0.0:
        raise MaterialStudyError(
            "INVALID_CONFIGURATION",
            "normalization",
            {"reason": "FIT_WEIGHT_SUM_MUST_BE_POSITIVE"},
        )

    entries: list[dict[str, Any]] = []
    for comp in comparisons:
        if comp.split == "holdout":
            continue
        exp = experiments[comp.experiment_id]
        ch_entry = experiment_data[comp.experiment_id][comp.observable_id]
        x_obs, _, _, _ = load_experiment_channel_arrays(ch_entry, data_root=data_root)
        q = compute_sample_weights(x_obs, comp.sample_weights_policy)
        norm_weight = (comp.weight / total_fit_weight) if comp.required_for_fit else 0.0
        entries.append(
            {
                "comparison_id": comp.id,
                "experiment_id": comp.experiment_id,
                "observable_id": comp.observable_id,
                "split": comp.split,
                "scale": comp.scale,
                "raw_weight": comp.weight,
                "normalized_fit_weight": norm_weight,
                "required_for_fit": comp.required_for_fit,
                "required_for_validation": comp.required_for_validation,
                "sample_weights_policy": comp.sample_weights_policy,
                "sample_count": int(x_obs.size),
                "sample_weights_sha256": hashlib.sha256(q.tobytes(order="C")).hexdigest(),
                "source_sha256": exp.source_ref.sha256,
            }
        )
        if schema_version == 2:
            observable = next(ch.observable for ch in exp.channels if ch.observable_id == comp.observable_id)
            entries[-1].update(
                experiment_revision=exp.experiment_revision,
                independent_unit=ch_entry["independent_unit"],
                independent_quantity_kind=ch_entry["independent_quantity_kind"],
                observable_unit=observable.unit,
                observable_quantity_kind=observable.quantity_kind,
                grid_sha256=ch_entry["independent_ref"]["sha256"],
                observation_sha256=ch_entry["values_ref"]["sha256"],
                sample_keys_sha256=ch_entry["sample_keys_ref"]["sha256"],
                mask_sha256=ch_entry["mask_ref"]["sha256"],
            )
    return {
        "schema_version": schema_version,
        "fit_comparison_ids": [c.id for c in fit_comps],
        "total_fit_weight": total_fit_weight,
        "comparisons": entries,
    }


def _aggregate_split_status(required_statuses: list[str]) -> str:
    """C4 rule 3: empty -> not_assessed; fail takes priority over not_assessed; all pass -> pass."""
    if not required_statuses:
        return "not_assessed"
    if any(s == "fail" for s in required_statuses):
        return "fail"
    if any(s == "not_assessed" for s in required_statuses):
        return "not_assessed"
    return "pass"


def _compute_constraints_and_scalars(
    *,
    physical_parameters: Mapping[str, float | str],
    parameter_units: Mapping[str, str],
    bundle: PredictionBundleV1 | None,
    registered_responses: Sequence[ScalarResponseSpecV1],
    constraint_specs: Sequence[ConstraintSpec],
    fit_loss_candidate: float | None,
    objective_valid_so_far: bool,
) -> tuple[dict[str, float | None], str, list[dict[str, Any]], bool, list[str]]:
    """Compute registered scalar responses and assess all ConstraintSpecs (C5)."""
    all_scalars: dict[str, float | None] = {}
    if fit_loss_candidate is not None and objective_valid_so_far:
        all_scalars["fit_loss"] = float(fit_loss_candidate)

    bundle_scalars = dict(bundle.optimizer_scalars) if bundle is not None else {}
    scalar_units: dict[str, str] = {"fit_loss": ""}
    reason_codes: list[str] = []

    for spec in registered_responses:
        scalar_units[spec.response_id] = spec.unit
        if spec.response_id == "fit_loss" or spec.source_kind == "residual":
            continue
        if spec.source_kind == "adapter_scalar":
            val = bundle_scalars.get(spec.source_name, bundle_scalars.get(spec.response_id))
            if val is None or isinstance(val, bool) or not math.isfinite(float(val)):
                all_scalars[spec.response_id] = None
            else:
                all_scalars[spec.response_id] = float(val)
        elif spec.source_kind == "parameter_expression":
            try:
                numeric_vals = {
                    k: float(v)
                    for k, v in physical_parameters.items()
                    if not isinstance(v, (bool, str)) and math.isfinite(float(v))
                }
                tree = ast.parse(spec.source_name, mode="eval")
                fn_names = {"abs", "sqrt", "exp", "log", "sin", "cos", "min", "max"}
                deps = tuple(sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - fn_names))
                val = evaluate_expression(
                    spec.source_name,
                    numeric_vals,
                    parameter_units,
                    spec.unit,
                    dependencies=deps,
                )
                all_scalars[spec.response_id] = float(val)
            except Exception:
                all_scalars[spec.response_id] = None

    constraint_checks: list[dict[str, Any]] = []
    if not constraint_specs:
        return all_scalars, "NOT_ASSESSED", constraint_checks, False, reason_codes

    numeric_parameters = {
        key: float(value) for key, value in physical_parameters.items() if not isinstance(value, (bool, str))
    }
    statuses: set[str] = set()
    invalid = False
    for spec in constraint_specs:
        response = spec.response_id
        values, units = numeric_parameters, parameter_units
        missing_output = False
        if response in scalar_units:
            value = all_scalars.get(response)
            missing_output = value is None and any(
                output.response_id == response and output.source_kind == "adapter_scalar"
                for output in registered_responses
            )
            values = {response: value} if value is not None else {}
            units = {response: scalar_units[response]}
        elif response in numeric_parameters:
            all_scalars[response] = numeric_parameters[response]

        result = assess_constraints(values, (spec,), valid=True, units=units)
        check = {
            "constraint_id": spec.id, "response_id": response, "kind": spec.kind,
            "limit": spec.limit, "value": values.get(response),
        }
        if result.feasibility is None:
            status = "INFEASIBLE" if missing_output else "NOT_ASSESSED"
            invalid = True
        else:
            norm_res = float(result.normalized_residuals[0])
            status = (
                "FEASIBLE" if norm_res <= 0.0
                else "MARGINAL" if norm_res <= spec.marginal_band / spec.scale
                else "INFEASIBLE"
            )
            check.update(raw_residual=float(result.raw_residuals[0]), normalized_residual=norm_res)
        check["status"] = status
        constraint_checks.append(check)
        statuses.add(status)

    if invalid:
        reason_codes.append("CONSTRAINT_RESPONSE_INVALID")
    status = next(s for s in ("INFEASIBLE", "NOT_ASSESSED", "MARGINAL", "FEASIBLE") if s in statuses)
    return all_scalars, status, constraint_checks, invalid, reason_codes


def evaluate_bundle_residuals(
    *,
    bundle: PredictionBundleV1 | None,
    experiments: Mapping[str, ExperimentSpec],
    experiment_data: Mapping[str, Mapping[str, Any]],
    comparisons: Sequence[ComparisonSpec],
    planned_experiment_ids: Sequence[str],
    physical_parameters: Mapping[str, float | str],
    parameter_units: Mapping[str, str],
    registered_responses: Sequence[ScalarResponseSpecV1],
    constraint_specs: Sequence[ConstraintSpec],
    assessment_policy: Mapping[str, Any],
    policy_ref: ArtifactRefV1 | Mapping[str, Any] | None,
    data_root: Path,
    execution_phase: str,
    identifiability_status: str = "not_computed",
    extra_split_checks: Sequence[Mapping[str, Any]] = (),
    extra_warnings: Sequence[str] = (),
    failure_reason_codes: Sequence[str] = (),
    split_failures: Mapping[str, str] | None = None,
    runtime_cache: dict[str, Any] | None = None,
) -> ResidualEvaluationOutput:
    """Compute host residuals, split/adequacy verdicts, and strict AssessmentResultV1 (C4/C5)."""
    planned_set = set(planned_experiment_ids)
    bundle_exps = {ep.experiment_id: ep for ep in bundle.experiments} if bundle is not None else {}

    fit_comps = [c for c in comparisons if c.required_for_fit]
    total_fit_weight = math.fsum(c.weight for c in fit_comps)
    if fit_comps and total_fit_weight <= 0.0:
        raise MaterialStudyError(
            "INVALID_CONFIGURATION",
            "evaluate_bundle_residuals",
            {"reason": "FIT_WEIGHT_SUM_MUST_BE_POSITIVE"},
        )

    channel_results: list[ChannelResidualResult] = []
    channel_checks: list[dict[str, Any]] = []
    reason_codes: list[str] = list(failure_reason_codes)
    warnings: list[str] = list(extra_warnings)

    fit_channel_losses: list[float] = []
    fit_channel_statuses: list[str] = []
    validation_channel_statuses: list[str] = []
    holdout_channel_statuses: list[str] = []
    whitened_chunks: list[np.ndarray] = []
    fit_invalid = bool(failure_reason_codes)

    for comp in comparisons:
        # Holdout must NEVER be inspected or scored during exploration (C4).
        if execution_phase == "exploration" and comp.split == "holdout":
            if comp.required_for_validation:
                holdout_channel_statuses.append("not_assessed")
            channel_checks.append(
                {
                    "comparison_id": comp.id,
                    "experiment_id": comp.experiment_id,
                    "observable_id": comp.observable_id,
                    "split": comp.split,
                    "required_for_fit": comp.required_for_fit,
                    "required_for_validation": comp.required_for_validation,
                    "status": "not_assessed",
                    "loss": None,
                    "max_abs_error": None,
                    "rmse": None,
                    "acceptance_limit": comp.acceptance_limit,
                    "reason_codes": ["HOLDOUT_SEALED_IN_EXPLORATION"],
                }
            )
            continue

        holdout_confirmation = comp.split == "holdout" and execution_phase == "confirmation"
        missing_status = "fail" if holdout_confirmation or comp.split == "validation" else "not_assessed"
        if comp.experiment_id not in planned_set or comp.experiment_id not in bundle_exps:
            ch_status = missing_status
            ch_reasons = [(split_failures or {}).get(comp.experiment_id, "EXPERIMENT_NOT_EXECUTED")]
            if comp.required_for_fit:
                fit_invalid = True
                fit_channel_statuses.append("not_assessed")
                reason_codes.append("REQUIRED_FIT_EXPERIMENT_MISSING")
            elif comp.required_for_validation:
                if comp.split == "validation":
                    validation_channel_statuses.append(missing_status)
                elif comp.split == "holdout":
                    holdout_channel_statuses.append(missing_status)
            channel_checks.append(
                {
                    "comparison_id": comp.id,
                    "experiment_id": comp.experiment_id,
                    "observable_id": comp.observable_id,
                    "split": comp.split,
                    "required_for_fit": comp.required_for_fit,
                    "required_for_validation": comp.required_for_validation,
                    "status": ch_status,
                    "loss": None,
                    "max_abs_error": None,
                    "rmse": None,
                    "acceptance_limit": comp.acceptance_limit,
                    "reason_codes": ch_reasons,
                }
            )
            continue

        ep = bundle_exps[comp.experiment_id]
        if ep.state != "completed":
            ch_reasons = [f"EXPERIMENT_STATE_{ep.state.upper()}"]
            if comp.required_for_fit:
                fit_invalid = True
                fit_channel_statuses.append("not_assessed")
                reason_codes.extend(ch_reasons)
            elif comp.required_for_validation:
                if comp.split == "validation":
                    validation_channel_statuses.append(missing_status)
                elif comp.split == "holdout":
                    holdout_channel_statuses.append(missing_status)
            channel_checks.append(
                {
                    "comparison_id": comp.id,
                    "experiment_id": comp.experiment_id,
                    "observable_id": comp.observable_id,
                    "split": comp.split,
                    "required_for_fit": comp.required_for_fit,
                    "required_for_validation": comp.required_for_validation,
                    "status": missing_status,
                    "loss": None,
                    "max_abs_error": None,
                    "rmse": None,
                    "acceptance_limit": comp.acceptance_limit,
                    "reason_codes": ch_reasons,
                }
            )
            continue

        pred_channels = {ch.observable_id: ch for ch in ep.channels}
        if comp.observable_id not in pred_channels:
            ch_reasons = ["MISSING_PREDICTED_CHANNEL"]
            if comp.required_for_fit:
                fit_invalid = True
                fit_channel_statuses.append("not_assessed")
                reason_codes.append("COVERAGE_INCOMPLETE")
            elif comp.required_for_validation:
                if comp.split == "validation":
                    validation_channel_statuses.append(missing_status)
                elif comp.split == "holdout":
                    holdout_channel_statuses.append(missing_status)
            channel_checks.append(
                {
                    "comparison_id": comp.id,
                    "experiment_id": comp.experiment_id,
                    "observable_id": comp.observable_id,
                    "split": comp.split,
                    "required_for_fit": comp.required_for_fit,
                    "required_for_validation": comp.required_for_validation,
                    "status": missing_status,
                    "loss": None,
                    "max_abs_error": None,
                    "rmse": None,
                    "acceptance_limit": comp.acceptance_limit,
                    "reason_codes": ch_reasons,
                }
            )
            continue

        exp_spec = experiments[comp.experiment_id]
        exp_ch_spec = next(c for c in exp_spec.channels if c.observable_id == comp.observable_id)
        ch_entry = experiment_data[comp.experiment_id][comp.observable_id]
        try:
            exp_cache = runtime_cache.setdefault("exp_arrays", {}) if runtime_cache is not None else {}
            exp_key = (comp.experiment_id, comp.observable_id)
            if exp_key not in exp_cache:
                exp_cache[exp_key] = load_experiment_channel_arrays(ch_entry, data_root=data_root)
            x_obs, y_obs, sk_obs, obs_mask = exp_cache[exp_key]
            pred_on_obs = interpolate_to_observed_grid(
                x_obs,
                obs_mask,
                sk_obs,
                pred_channels[comp.observable_id],
                data_root=data_root,
                expected_obs_spec=exp_ch_spec.observable,
                runtime_cache=runtime_cache,
            )
            q = compute_sample_weights(x_obs, comp.sample_weights_policy)
            n_comp = y_obs.shape[1]
            q_nc = np.repeat((q / n_comp)[:, None], n_comp, axis=1)
            r = (pred_on_obs - y_obs) / comp.scale
            if not np.isfinite(r).all():
                raise MaterialStudyError(
                    "NONFINITE_RESPONSE",
                    "residuals.compute",
                    {"reason": "NONFINITE_RESIDUAL", "comparison_id": comp.id},
                )
            loss = float(np.sum(q_nc * (r ** 2)))
            max_abs = float(np.max(np.abs(pred_on_obs - y_obs)))
            rmse = float(np.sqrt(np.mean((pred_on_obs - y_obs) ** 2)))
            if not (math.isfinite(loss) and math.isfinite(max_abs) and math.isfinite(rmse)):
                raise MaterialStudyError(
                    "NONFINITE_RESPONSE",
                    "residuals.compute",
                    {"reason": "NONFINITE_CHANNEL_METRIC", "comparison_id": comp.id},
                )
        except Exception as exc:
            if comp.split == "train" and not isinstance(exc, MaterialStudyError):
                raise
            code = exc.code if isinstance(exc, MaterialStudyError) else "INTEGRATION_FAILED"
            if comp.required_for_fit:
                fit_invalid = True
                fit_channel_statuses.append("not_assessed")
                reason_codes.append(code)
            elif comp.required_for_validation:
                if comp.split == "validation":
                    validation_channel_statuses.append(missing_status)
                elif comp.split == "holdout":
                    holdout_channel_statuses.append(missing_status)
            channel_checks.append(
                {
                    "comparison_id": comp.id,
                    "experiment_id": comp.experiment_id,
                    "observable_id": comp.observable_id,
                    "split": comp.split,
                    "required_for_fit": comp.required_for_fit,
                    "required_for_validation": comp.required_for_validation,
                    "status": missing_status,
                    "loss": None,
                    "max_abs_error": None,
                    "rmse": None,
                    "acceptance_limit": comp.acceptance_limit,
                    "reason_codes": [code],
                    "coverage": exc.details.get("coverage") if isinstance(exc, MaterialStudyError) else None,
                }
            )
            continue

        if comp.acceptance_limit is None:
            ch_status = "not_assessed"
        elif loss <= comp.acceptance_limit:
            ch_status = "pass"
        else:
            ch_status = "fail"

        if comp.required_for_fit:
            norm_w = comp.weight / total_fit_weight
            fit_channel_losses.append(norm_w * loss)
            fit_channel_statuses.append(ch_status)
            whitened_chunks.append((np.sqrt(norm_w * q_nc) * r).ravel())
        elif comp.required_for_validation:
            if comp.split == "validation":
                validation_channel_statuses.append(ch_status)
            elif comp.split == "holdout":
                holdout_channel_statuses.append(ch_status)

        channel_results.append(
            ChannelResidualResult(
                comparison=comp,
                observed_coords=x_obs,
                observed_values=y_obs,
                predicted_on_obs=pred_on_obs,
                residuals=r,
                weights=q_nc,
                loss=loss,
                max_abs_error=max_abs,
                rmse=rmse,
                status=ch_status,
            )
        )
        channel_checks.append(
            {
                "comparison_id": comp.id,
                "experiment_id": comp.experiment_id,
                "observable_id": comp.observable_id,
                "split": comp.split,
                "required_for_fit": comp.required_for_fit,
                "required_for_validation": comp.required_for_validation,
                "status": ch_status,
                "loss": loss,
                "max_abs_error": max_abs,
                "rmse": rmse,
                "acceptance_limit": comp.acceptance_limit,
                "reason_codes": [],
            }
        )

    for check in channel_checks:
        if check.get("coverage") is not None:
            continue
        entry = experiment_data.get(check["experiment_id"], {}).get(check["observable_id"], {})
        expected = int(entry.get("row_count", 0))
        valid = expected if check["loss"] is not None else 0
        check["coverage"] = {"expected": expected, "valid": valid, "missing": expected - valid}

    if not fit_comps:
        fit_invalid = True
        if "NO_REQUIRED_FIT_COMPARISONS" not in reason_codes:
            reason_codes.append("NO_REQUIRED_FIT_COMPARISONS")

    fit_loss_candidate = None if fit_invalid else float(math.fsum(fit_channel_losses))
    if fit_loss_candidate is not None and not math.isfinite(fit_loss_candidate):
        fit_loss_candidate = None
        fit_invalid = True
        reason_codes.append("NONFINITE_RESPONSE")

    all_scalars, constraint_status, constraint_checks, constraint_invalid, constraint_reasons = (
        _compute_constraints_and_scalars(
            physical_parameters=physical_parameters,
            parameter_units=parameter_units,
            bundle=bundle,
            registered_responses=registered_responses,
            constraint_specs=constraint_specs,
            fit_loss_candidate=fit_loss_candidate,
            objective_valid_so_far=not fit_invalid,
        )
    )
    for rc in constraint_reasons:
        if rc not in reason_codes:
            reason_codes.append(rc)

    objective_eligible = bool((not fit_invalid) and (not constraint_invalid) and (fit_loss_candidate is not None))
    final_fit_loss = fit_loss_candidate if objective_eligible else None
    if not objective_eligible and "fit_loss" in all_scalars:
        all_scalars["fit_loss"] = None

    fit_status = _aggregate_split_status(fit_channel_statuses)
    validation_status = _aggregate_split_status(validation_channel_statuses)
    holdout_status = "not_assessed" if execution_phase == "exploration" else _aggregate_split_status(holdout_channel_statuses)

    # Adequacy assessment (C4 rule 5, 6, 7)
    adequacy_policy = assessment_policy.get("adequacy_policy") if isinstance(assessment_policy, Mapping) else None
    holdout_reused = any(
        isinstance(item, Mapping) and item.get("code") == "HOLDOUT_REUSED_FOR_SELECTION"
        for item in extra_split_checks
    )
    has_synthetic = any(exp.source_kind in ("synthetic_arithmetic", "numerical_fixture") for exp in experiments.values())
    if not isinstance(adequacy_policy, Mapping) or not adequacy_policy.get("enabled", False):
        adequacy_status = "not_assessed"
    elif holdout_reused and adequacy_policy.get("require_independent_holdout", True):
        adequacy_status = "not_assessed"
    elif has_synthetic and not adequacy_policy.get("allow_synthetic", False):
        adequacy_status = "not_assessed"
        if "SYNTHETIC_FIXTURE_NOT_MATERIAL_VALIDATION" not in warnings:
            warnings.append("SYNTHETIC_FIXTURE_NOT_MATERIAL_VALIDATION")
    else:
        req_checks: list[str] = []
        if adequacy_policy.get("require_fit", True):
            req_checks.append(fit_status)
        if adequacy_policy.get("require_validation", False):
            req_checks.append(validation_status)
        if adequacy_policy.get("require_holdout", True):
            req_checks.append(holdout_status)
        if adequacy_policy.get("require_constraints", True) and constraint_specs:
            req_checks.append("pass" if constraint_status == "FEASIBLE" else ("not_assessed" if constraint_status == "NOT_ASSESSED" else "fail"))
        if adequacy_policy.get("require_identifiability", False):
            if identifiability_status == "not_identifiable":
                req_checks.append("fail")
            elif identifiability_status == "not_computed":
                req_checks.append("not_assessed")
            else:
                req_checks.append(identifiability_status)
        if adequacy_policy.get("require_uncertainty", False):
            req_checks.append("not_assessed")
        if not req_checks:
            adequacy_status = "not_assessed"
        elif any(s == "fail" for s in req_checks):
            adequacy_status = "fail"
        elif any(s == "not_assessed" for s in req_checks):
            adequacy_status = "not_assessed"
        else:
            adequacy_status = "pass"

    split_checks: list[dict[str, Any]] = [
        {"split": "train", "status": fit_status, "required_count": len(fit_channel_statuses)},
        {"split": "validation", "status": validation_status, "required_count": len(validation_channel_statuses)},
        {
            "split": "holdout",
            "status": holdout_status,
            "required_count": len(holdout_channel_statuses),
            "independent_validation_satisfied": bool(holdout_status == "pass" and not holdout_reused),
        },
    ]
    for extra in extra_split_checks:
        split_checks.append(dict(_to_plain_json(extra)))

    unique_reasons = tuple(dict.fromkeys(str(r) for r in reason_codes if r))
    unique_warnings = tuple(dict.fromkeys(str(w) for w in warnings if w))

    assessment = AssessmentResultV1(
        objective={
            "eligible": objective_eligible,
            "fit_loss": final_fit_loss,
            "reason_codes": unique_reasons,
        },
        assessment={
            "fit_status": fit_status,
            "validation_status": validation_status,
            "holdout_status": holdout_status,
            "adequacy_status": adequacy_status,
            "uncertainty_status": "not_computed",
            "identifiability_status": identifiability_status,
            "constraint_status": constraint_status,
            "constraint_checks": constraint_checks,
            "policy_ref": policy_ref,
            "channel_checks": channel_checks,
            "split_checks": split_checks,
            "warnings": unique_warnings,
        },
    )

    whitened = np.concatenate(whitened_chunks) if objective_eligible and whitened_chunks else None
    return ResidualEvaluationOutput(
        assessment=assessment,
        channel_results=tuple(channel_results),
        whitened_residual_vector=whitened,
        all_scalars=all_scalars,
        objective_eligible=objective_eligible,
        fit_loss=final_fit_loss,
        reason_codes=unique_reasons,
    )
