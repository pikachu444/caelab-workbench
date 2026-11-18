"""D05 B-VIS-TRS-1 2-term Prony + WLF viscoelastic adapter using actual pyvisco==2.1.3 (B3)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import importlib.metadata
import math
import os
from pathlib import Path
import tempfile
import threading
import uuid
from typing import Any

import numpy as np

from caelab.artifact_refs import (
    ArtifactOwner,
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
    CapabilityReport,
    ExperimentPredictionV1,
    ExperimentSpec,
    ModelDescriptorV1,
    ObservableCapability,
    ParameterSpec,
    PredictedChannelV1,
    PredictionBundleV1,
    ScalarResponseSpecV1,
)
from caelab.materials.experiments import load_experiment_channel_arrays, storage_segment
from caelab.materials.registry import (
    EvaluationPlan,
    MaterialEvaluationRequest,
    MaterialExportPlan,
    MaterialStudyError,
)
from caelab.materials.units import (
    QuantityUnitSpec,
    convert_frequency_angular,
    convert_values,
)
from caelab.mdo.constraints import ConstraintSpec

__all__ = [
    "PYVISCO_PRONY_SHA256",
    "PYVISCO_SHIFT_SHA256",
    "PronyTRSAdapter",
    "verify_pyvisco_sources",
]

PYVISCO_PRONY_SHA256 = "641490da54f1d62d5f631c899f3f0ade44d1f9debc98a9e9068fe4f0577795bb"
PYVISCO_SHIFT_SHA256 = "93f143f9789e31aaffebc229fe76b0c3b37ffa0b4937d5b2980b1d524e2503a2"
PYVISCO_REPOSITORY = "NatLabRockies/pyvisco"
PYVISCO_REVISION = "b61b5a0c6a2939927c87189972ea5619503bd813"
PYVISCO_LICENSE = "BSD-3-Clause"
_PYVISCO_VERSION = "2.1.3"

_PRODUCER = ProducerIdentity(id="caelab.prony_trs", version="1.0.0")
_STRESS_UNITS = ("MPa", "Pa", "kPa", "GPa")


def verify_pyvisco_sources() -> dict[str, Any]:
    """Import pyvisco==2.1.3 and verify loaded prony.py and shift.py byte SHA256 digests."""
    try:
        import pyvisco
        from pyvisco import prony, shift
    except ImportError as exc:
        return {
            "installed": False,
            "version": None,
            "source_match": False,
            "error_code": "BACKEND_UNAVAILABLE",
            "error": str(exc),
            "loaded_source_checks": [],
        }

    prony_path = Path(prony.__file__).resolve()
    shift_path = Path(shift.__file__).resolve()
    prony_sha = hashlib.sha256(prony_path.read_bytes()).hexdigest()
    shift_sha = hashlib.sha256(shift_path.read_bytes()).hexdigest()

    try:
        pkg_version = importlib.metadata.version("pyvisco")
    except Exception:
        pkg_version = getattr(pyvisco, "__version__", "unknown")

    checks = [
        {
            "source_id": "B-SRC-VIS",
            "repository": PYVISCO_REPOSITORY,
            "revision": PYVISCO_REVISION,
            "path": "pyvisco/prony.py",
            "url": f"https://github.com/{PYVISCO_REPOSITORY}/blob/{PYVISCO_REVISION}/pyvisco/prony.py",
            "license": PYVISCO_LICENSE,
            "expected_sha256": PYVISCO_PRONY_SHA256,
            "loaded_file_sha256": prony_sha,
            "loaded_file_path": str(prony_path),
            "source_match": prony_sha == PYVISCO_PRONY_SHA256,
        },
        {
            "source_id": "B-SRC-WLF",
            "repository": PYVISCO_REPOSITORY,
            "revision": PYVISCO_REVISION,
            "path": "pyvisco/shift.py",
            "url": f"https://github.com/{PYVISCO_REPOSITORY}/blob/{PYVISCO_REVISION}/pyvisco/shift.py",
            "license": PYVISCO_LICENSE,
            "expected_sha256": PYVISCO_SHIFT_SHA256,
            "loaded_file_sha256": shift_sha,
            "loaded_file_path": str(shift_path),
            "source_match": shift_sha == PYVISCO_SHIFT_SHA256,
        },
    ]
    source_match = all(item["source_match"] for item in checks)
    version_match = pkg_version == _PYVISCO_VERSION
    return {
        "installed": True,
        "version": str(pkg_version),
        "source_match": source_match and version_match,
        "error_code": (
            "PYVISCO_VERSION_MISMATCH" if not version_match
            else None if source_match else "PYVISCO_SOURCE_HASH_MISMATCH"
        ),
        "prony_sha256": prony_sha,
        "shift_sha256": shift_sha,
        "license": PYVISCO_LICENSE,
        "repository": PYVISCO_REPOSITORY,
        "revision": PYVISCO_REVISION,
        "loaded_source_checks": checks,
    }


# Verify pinned pyvisco==2.1.3 hashes at module load time (D05).
_MODULE_LOAD_PYVISCO_STATUS = verify_pyvisco_sources()


def _adapter_implementation_sha256() -> str:
    self_bytes = Path(__file__).read_bytes()
    return canonical_sha256(
        {
            "adapter_file_sha256": hashlib.sha256(self_bytes).hexdigest(),
            "pinned_prony_sha256": PYVISCO_PRONY_SHA256,
            "pinned_shift_sha256": PYVISCO_SHIFT_SHA256,
        },
        exclude_keys=(),
    )


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


def _default_b3_parameters() -> tuple[ParameterSpec, ...]:
    """B3 default 2-term Prony + fixed WLF parameter specification."""
    return (
        ParameterSpec(
            id="E0",
            name="순간 탄성계수 E0",
            unit="MPa",
            lower=10.0,
            upper=1000.0,
            default=90.0,
            transform="log",
            role="free",
        ),
        ParameterSpec(
            id="a1",
            name="1차 Prony 가중 계수 a1",
            unit="",
            lower=1e-8,
            upper=0.475,
            default=0.18,
            transform="linear",
            role="free",
        ),
        ParameterSpec(
            id="a2",
            name="2차 Prony 가중 계수 a2",
            unit="",
            lower=1e-8,
            upper=0.475,
            default=0.27,
            transform="linear",
            role="free",
        ),
        ParameterSpec(
            id="tau1",
            name="1차 완화 시간상수 τ1",
            unit="s",
            lower=0.001,
            upper=0.3,
            default=0.2,
            transform="log",
            role="free",
        ),
        ParameterSpec(
            id="tau2",
            name="2차 완화 시간상수 τ2",
            unit="s",
            lower=1.0,
            upper=1000.0,
            default=8.0,
            transform="log",
            role="free",
        ),
        ParameterSpec(
            id="C1",
            name="WLF 상수 C1",
            unit="",
            lower=0.1,
            upper=20.0,
            default=5.0,
            transform="linear",
            role="fixed",
        ),
        ParameterSpec(
            id="C2",
            name="WLF 상수 C2",
            unit="K",
            lower=50.0,
            upper=300.0,
            default=100.0,
            transform="linear",
            role="fixed",
        ),
        ParameterSpec(
            id="Tref",
            name="기준 온도 Tref",
            unit="K",
            lower=200.0,
            upper=500.0,
            default=293.15,
            transform="linear",
            role="fixed",
        ),
    )


def _observable_capabilities() -> tuple[ObservableCapability, ...]:
    return (
        ObservableCapability(
            observable_id="E_storage",
            value_kinds=("real",),
            representations=("scalar",),
            locations=("material_point",),
            supported_units=_STRESS_UNITS,
        ),
        ObservableCapability(
            observable_id="E_loss",
            value_kinds=("real",),
            representations=("scalar",),
            locations=("material_point",),
            supported_units=_STRESS_UNITS,
        ),
        ObservableCapability(
            observable_id="E_star",
            value_kinds=("complex",),
            representations=("storage_loss",),
            locations=("material_point",),
            supported_units=_STRESS_UNITS,
        ),
        ObservableCapability(
            observable_id="E_relax",
            value_kinds=("real",),
            representations=("scalar",),
            locations=("material_point",),
            supported_units=_STRESS_UNITS,
        ),
        ObservableCapability(
            observable_id="stress_xx",
            value_kinds=("real",),
            representations=("scalar",),
            locations=("material_point",),
            supported_units=_STRESS_UNITS,
        ),
        ObservableCapability(
            observable_id="axial_stress",
            value_kinds=("real",),
            representations=("scalar",),
            locations=("material_point",),
            supported_units=_STRESS_UNITS,
        ),
    )


def _optimizer_responses() -> tuple[ScalarResponseSpecV1, ...]:
    return (
        ScalarResponseSpecV1(
            response_id="fit_loss",
            unit="",
            source_kind="residual",
            source_name="fit_loss",
        ),
        ScalarResponseSpecV1(
            response_id="prony_fraction_sum",
            unit="",
            source_kind="parameter_expression",
            source_name="a1 + a2",
        ),
        ScalarResponseSpecV1(
            response_id="prony_tau_ratio",
            unit="",
            source_kind="parameter_expression",
            source_name="tau2 / tau1",
        ),
        ScalarResponseSpecV1(
            response_id="prony_e_inf",
            unit="MPa",
            source_kind="parameter_expression",
            source_name="E0 * (1 - a1 - a2)",
        ),
    )


def _optimizer_constraints() -> tuple[ConstraintSpec, ...]:
    return (
        ConstraintSpec(
            id="prony_fraction_sum_max",
            response_id="prony_fraction_sum",
            kind="UPPER",
            limit=0.95,
            scale=1.0,
            tolerance=0.0,
            unit="",
        ),
        ConstraintSpec(
            id="prony_tau_order_ratio",
            response_id="prony_tau_ratio",
            kind="LOWER",
            limit=2.0,
            scale=1.0,
            tolerance=0.0,
            unit="",
        ),
        ConstraintSpec(
            id="prony_e_inf_positive",
            response_id="prony_e_inf",
            kind="LOWER",
            limit=1e-9,
            scale=10.0,
            tolerance=0.0,
            unit="MPa",
        ),
    )


def _extract_param_values(parameters: Mapping[str, Any] | Sequence[ParameterSpec]) -> dict[str, float]:
    if isinstance(parameters, Mapping):
        out: dict[str, float] = {}
        for k, v in parameters.items():
            if isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating)):
                raise MaterialStudyError(
                    "INVALID_PARAMETER",
                    "prony_trs.parameters",
                    {"reason": "NON_NUMERIC_PARAMETER", "parameter": k},
                )
            fv = float(v)
            if not math.isfinite(fv):
                raise MaterialStudyError(
                    "INVALID_PARAMETER",
                    "prony_trs.parameters",
                    {"reason": "NONFINITE_PARAMETER", "parameter": k},
                )
            out[str(k)] = fv
        return out
    out = {}
    for p in parameters:
        if not isinstance(p, ParameterSpec):
            raise MaterialStudyError(
                "INVALID_PARAMETER",
                "prony_trs.parameters",
                {"reason": "INVALID_PARAMETER_SPEC"},
            )
        if p.default is not None:
            out[p.id] = float(p.default)
        elif p.lower is not None and p.upper is not None:
            out[p.id] = 0.5 * (float(p.lower) + float(p.upper))
        else:
            raise MaterialStudyError(
                "INVALID_PARAMETER",
                "prony_trs.parameters",
                {"reason": "MISSING_PARAMETER_DEFAULT", "parameter": p.id},
            )
    return out


def _extract_temperature_K(exp: ExperimentSpec, stage: str) -> float:
    cond = exp.conditions
    if "temperature_K" in cond:
        val = cond["temperature_K"]
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(float(val)) or float(val) <= 0.0:
            raise MaterialStudyError(
                "INVALID_PARAMETER",
                stage,
                {"reason": "INVALID_TEMPERATURE_K", "experiment_id": exp.experiment_id},
            )
        return float(val)
    if "temperature" in cond:
        t_obj = cond["temperature"]
        if isinstance(t_obj, Mapping) and "value" in t_obj and "unit" in t_obj:
            return float(
                convert_values(
                    float(t_obj["value"]),
                    QuantityUnitSpec(str(t_obj["unit"]), "absolute_temperature"),
                    QuantityUnitSpec("K", "absolute_temperature"),
                )
            )
    raise MaterialStudyError(
        "EXPERIMENT_METADATA_MISSING",
        stage,
        {"reason": "MISSING_TEMPERATURE", "experiment_id": exp.experiment_id},
    )


def _validate_wlf_temperature(
    temp_k: float,
    *,
    tref_k: float,
    c1: float,
    c2: float,
    allow_extended: bool,
    stage: str,
    experiment_id: str,
) -> None:
    denom = c2 + (temp_k - tref_k)
    if not math.isfinite(denom) or denom <= 1e-12:
        raise MaterialStudyError(
            "INVALID_PARAMETER",
            stage,
            {
                "reason": "WLF_SINGULAR_DENOMINATOR",
                "experiment_id": experiment_id,
                "temperature_K": temp_k,
                "Tref_K": tref_k,
                "C2_K": c2,
                "denominator_K": denom,
            },
        )
    if denom < 20.0:
        raise MaterialStudyError(
            "INVALID_PARAMETER",
            stage,
            {
                "reason": "SHIFT_RANGE_EXCEEDED",
                "experiment_id": experiment_id,
                "temperature_K": temp_k,
                "denominator_K": denom,
            },
        )
    if not allow_extended and not (293.15 <= temp_k <= 323.15):
        raise MaterialStudyError(
            "INVALID_PARAMETER",
            stage,
            {
                "reason": "SHIFT_RANGE_EXCEEDED",
                "experiment_id": experiment_id,
                "temperature_K": temp_k,
                "allowed_range_K": [293.15, 323.15],
            },
        )


def _validate_physical_domain(
    param_vals: Mapping[str, float],
    *,
    stage: str,
) -> tuple[float, np.ndarray, np.ndarray, float, float, float]:
    required = ("E0", "a1", "a2", "tau1", "tau2", "C1", "C2", "Tref")
    for key in required:
        if key not in param_vals:
            raise MaterialStudyError(
                "INVALID_PARAMETER",
                stage,
                {"reason": "MISSING_PARAMETER", "parameter": key},
            )
    e0 = float(param_vals["E0"])
    a1 = float(param_vals["a1"])
    a2 = float(param_vals["a2"])
    tau1 = float(param_vals["tau1"])
    tau2 = float(param_vals["tau2"])
    c1 = float(param_vals["C1"])
    c2 = float(param_vals["C2"])
    tref = float(param_vals["Tref"])

    if e0 <= 0.0:
        raise MaterialStudyError("INVALID_PARAMETER", stage, {"reason": "NONPOSITIVE_E0", "E0": e0})
    if a1 <= 0.0 or a2 <= 0.0:
        raise MaterialStudyError("INVALID_PARAMETER", stage, {"reason": "NONPOSITIVE_PRONY_FRACTION", "a": [a1, a2]})
    if a1 + a2 > 0.95 + 1e-12:
        raise MaterialStudyError(
            "INVALID_PARAMETER",
            stage,
            {"reason": "PRONY_FRACTION_SUM", "a": [a1, a2], "sum_a": a1 + a2},
        )
    if e0 * (1.0 - a1 - a2) <= 0.0:
        raise MaterialStudyError(
            "INVALID_PARAMETER",
            stage,
            {"reason": "NONPOSITIVE_EQUILIBRIUM_MODULUS", "E0": e0, "sum_a": a1 + a2},
        )
    if tau1 <= 0.0 or tau2 <= 0.0:
        raise MaterialStudyError("INVALID_PARAMETER", stage, {"reason": "NONPOSITIVE_TAU", "tau_s": [tau1, tau2]})
    if tau2 < 2.0 * tau1 - 1e-12:
        raise MaterialStudyError(
            "INVALID_PARAMETER",
            stage,
            {"reason": "PRONY_TERM_ORDER", "tau_s": [tau1, tau2]},
        )
    if c1 <= 0.0 or c2 <= 0.0 or tref <= 0.0:
        raise MaterialStudyError(
            "INVALID_PARAMETER",
            stage,
            {"reason": "INVALID_WLF_CONSTANTS", "C1": c1, "C2": c2, "Tref": tref},
        )
    return e0, np.array([a1, a2], dtype=np.float64), np.array([tau1, tau2], dtype=np.float64), c1, c2, tref


def _require_verified_pyvisco(stage: str) -> tuple[Any, Any, dict[str, Any]]:
    status = verify_pyvisco_sources()
    if not status["installed"]:
        raise MaterialStudyError(
            "BACKEND_UNAVAILABLE",
            stage,
            {"reason": "PYVISCO_NOT_INSTALLED", "message": status.get("error", "pyvisco is not installed")},
        )
    if not status["source_match"]:
        raise MaterialStudyError(
            status["error_code"] or "PYVISCO_SOURCE_HASH_MISMATCH",
            stage,
            {
                "version": status["version"],
                "checks": status["loaded_source_checks"],
            },
        )
    from pyvisco import prony, shift

    return prony, shift, status


def _maxwell_ramp_step(
    deps: float,
    dt: float,
    temp_k: float,
    q: np.ndarray,
    *,
    e0: float,
    a_arr: np.ndarray,
    tau_arr: np.ndarray,
    tref_k: float,
    c1: float,
    c2: float,
    shift_mod: Any,
) -> np.ndarray:
    """Exact piecewise-linear strain Prony step using expm1 and r->0 limit (B3)."""
    if dt == 0.0:
        return q + e0 * a_arr * deps
    log_a_t = float(shift_mod.WLF(temp_k, tref_k, c1, c2))
    a_t = 10.0 ** log_a_t
    r = dt / (a_t * tau_arr)
    exp_neg_r = np.exp(-r)
    # Limit (1 - exp(-r))/r -> 1 as r -> 0 without cancellation
    gain = np.where(np.abs(r) < 1e-12, 1.0 - 0.5 * r + (r ** 2) / 6.0, -np.expm1(-r) / r)
    return q * exp_neg_r + e0 * a_arr * deps * gain


def _integrate_strain_history(
    time_s: np.ndarray,
    strain: np.ndarray,
    temp_k: np.ndarray,
    *,
    e0: float,
    a_arr: np.ndarray,
    tau_arr: np.ndarray,
    tref_k: float,
    c1: float,
    c2: float,
    shift_mod: Any,
) -> np.ndarray:
    """Integrate 1D viscoelastic Prony stress over piecewise-linear strain/temperature path (B3)."""
    n = time_s.size
    stress = np.empty(n, dtype=np.float64)
    e_inf = e0 * (1.0 - float(np.sum(a_arr)))
    q = np.zeros_like(a_arr, dtype=np.float64)
    stress[0] = e_inf * float(strain[0]) + float(np.sum(q))
    for i in range(1, n):
        t0, t1 = float(time_s[i - 1]), float(time_s[i])
        dt_total = t1 - t0
        eps0, eps1 = float(strain[i - 1]), float(strain[i])
        temp0, temp1 = float(temp_k[i - 1]), float(temp_k[i])
        if dt_total == 0.0 or abs(temp1 - temp0) <= 1e-12:
            q = _maxwell_ramp_step(
                eps1 - eps0,
                dt_total,
                0.5 * (temp0 + temp1),
                q,
                e0=e0,
                a_arr=a_arr,
                tau_arr=tau_arr,
                tref_k=tref_k,
                c1=c1,
                c2=c2,
                shift_mod=shift_mod,
            )
        else:
            # Subdivide temperature ramp using midpoint rule until convergence <= 1e-7 + 1e-5*|sigma| (max 16 halvings)
            converged = False
            prev_sigma = None
            accepted_q = q
            for k in range(17):
                m = 1 << k
                q_sub = q.copy()
                dt_sub = dt_total / m
                deps_sub = (eps1 - eps0) / m
                for s in range(m):
                    t_mid = temp0 + (temp1 - temp0) * ((s + 0.5) / m)
                    q_sub = _maxwell_ramp_step(
                        deps_sub,
                        dt_sub,
                        t_mid,
                        q_sub,
                        e0=e0,
                        a_arr=a_arr,
                        tau_arr=tau_arr,
                        tref_k=tref_k,
                        c1=c1,
                        c2=c2,
                        shift_mod=shift_mod,
                    )
                cur_sigma = e_inf * eps1 + float(np.sum(q_sub))
                if prev_sigma is not None and abs(cur_sigma - prev_sigma) <= 1e-7 + 1e-5 * abs(cur_sigma):
                    accepted_q = q_sub
                    converged = True
                    break
                prev_sigma = cur_sigma
            if not converged:
                raise MaterialStudyError(
                    "INTEGRATION_FAILED",
                    "prony_trs.integrate_strain_history",
                    {"reason": "TEMPERATURE_QUADRATURE_FAILED", "step_index": i},
                )
            q = accepted_q
        stress[i] = e_inf * eps1 + float(np.sum(q))
    return stress


class PronyTRSAdapter:
    """B-VIS-TRS-1 2-term Maxwell Prony + fixed WLF viscoelasticity adapter using pyvisco==2.1.3."""

    MODEL_ID = "B-VIS-TRS-1"
    MODEL_REVISION = "1"
    ADAPTER_ID = "caelab.prony_trs"
    ADAPTER_VERSION = "1.0.0"

    def __init__(self) -> None:
        self._impl_sha = _adapter_implementation_sha256()
        self._identity = AdapterIdentity(
            id=self.ADAPTER_ID,
            version=self.ADAPTER_VERSION,
            implementation_sha256=self._impl_sha,
        )

    @property
    def identity(self) -> AdapterIdentity:
        return self._identity

    def descriptor(self) -> ModelDescriptorV1:
        return ModelDescriptorV1(
            model_id=self.MODEL_ID,
            model_revision=self.MODEL_REVISION,
            family="viscoelastic",
            name="점탄성 2항 Prony + WLF (시간-온도 중첩)",
            parameter_specs=_default_b3_parameters(),
            observable_capabilities=_observable_capabilities(),
            level="material_library",
            supports_export=True,
            supports_apply=False,
        )

    def capabilities(
        self,
        model: ModelDescriptorV1 | None = None,
        environment: Mapping[str, Any] | None = None,
    ) -> CapabilityReport:
        status = verify_pyvisco_sources()
        can_run = bool(status["installed"] and status["source_match"])
        reasons: list[str] = []
        if not can_run and status.get("error_code"):
            reasons.append(str(status["error_code"]))
        return CapabilityReport(
            adapter=self._identity,
            model_id=model.model_id if model is not None else self.MODEL_ID,
            model_revision=model.model_revision if model is not None else self.MODEL_REVISION,
            level="material_library",
            supported=True,
            can_run=can_run,
            differentiable=True,
            observable_capabilities=_observable_capabilities(),
            optimizer_responses=_optimizer_responses(),
            optimizer_constraints=_optimizer_constraints(),
            supports_export=True,
            supports_apply=False,
            reason_codes=tuple(reasons),
        )

    def validate(
        self,
        model: ModelDescriptorV1,
        parameters: Mapping[str, Any] | Sequence[ParameterSpec],
        experiments: Sequence[ExperimentSpec],
        numerical_settings: Mapping[str, Any] | None = None,
    ) -> tuple[Mapping[str, Any], ...]:
        _require_verified_pyvisco("prony_trs.validate")
        if model.model_id != self.MODEL_ID:
            raise MaterialStudyError(
                "UNSUPPORTED_MODEL",
                "prony_trs.validate",
                {"expected": self.MODEL_ID, "got": model.model_id},
            )
        param_vals = _extract_param_values(parameters)
        _, _, _, c1, c2, tref = _validate_physical_domain(param_vals, stage="prony_trs.validate")

        settings = numerical_settings or {}
        allow_extended = bool(settings.get("allow_extended_temperature_range", False))
        supported_obs = {cap.observable_id for cap in _observable_capabilities()}
        diagnostics: list[dict[str, Any]] = []

        for exp in experiments:
            if exp.conditions.get("phase_convention") == "unknown":
                raise MaterialStudyError(
                    "MEASURE_MISMATCH",
                    "prony_trs.validate",
                    {"reason": "PHASE_CONVENTION_UNKNOWN", "experiment_id": exp.experiment_id},
                )
            temp_k = _extract_temperature_K(exp, "prony_trs.validate")
            _validate_wlf_temperature(
                temp_k,
                tref_k=tref,
                c1=c1,
                c2=c2,
                allow_extended=allow_extended,
                stage="prony_trs.validate",
                experiment_id=exp.experiment_id,
            )
            for ch in exp.channels:
                if ch.observable_id not in supported_obs:
                    raise MaterialStudyError(
                        "UNSUPPORTED_OBSERVABLE",
                        "prony_trs.validate",
                        {"observable_id": ch.observable_id, "experiment_id": exp.experiment_id},
                    )
                if ch.observable.unit not in _STRESS_UNITS:
                    raise MaterialStudyError(
                        "UNSUPPORTED_UNIT",
                        "prony_trs.validate",
                        {"unit": ch.observable.unit, "observable_id": ch.observable_id},
                    )
            diagnostics.append({"experiment_id": exp.experiment_id, "temperature_K": temp_k, "valid": True})
        return tuple(diagnostics)

    def validate_study_configuration(
        self,
        parameters: Sequence[ParameterSpec],
        experiments: Sequence[ExperimentSpec],
        settings: Mapping[str, Any],
    ) -> None:
        stage = "prony_trs.wlf_provenance"
        measured = any(
            exp.source_kind == "experiment" and exp.conditions.get("provenance") != "synthetic_demo"
            for exp in experiments
        )
        fixed = {p.id: p.default for p in parameters if p.role == "fixed" and p.id in ("C1", "C2", "Tref")}
        if not measured or not fixed or len({_extract_temperature_K(exp, stage) for exp in experiments}) < 2:
            return
        provenance = settings.get("wlf_provenance")
        if (
            not isinstance(provenance, Mapping)
            or not isinstance(provenance.get("source"), str)
            or not provenance["source"].strip()
            or any(
                not isinstance(provenance.get(key), (int, float))
                or isinstance(provenance[key], bool)
                or provenance[key] != value
                for key, value in fixed.items()
            )
        ):
            raise MaterialStudyError("WLF_PROVENANCE_REQUIRED", stage, {"fixed_parameters": fixed})

    def plan(self, request: MaterialEvaluationRequest) -> EvaluationPlan:
        self.validate_study_configuration(request.parameter_specs, request.experiments, request.numerical_settings)
        self.validate(
            request.model_descriptor,
            request.physical_parameters,
            request.experiments,
            request.numerical_settings,
        )
        exp_plans: list[dict[str, Any]] = []
        expected_artifacts: list[str] = [f"artifacts/pyvisco-provenance-{request.evaluation_id}.json"]
        for exp in request.experiments:
            exp_slug = storage_segment(exp.experiment_id)
            exp_plans.append(
                {
                    "experiment_id": exp.experiment_id,
                    "experiment_revision": exp.experiment_revision,
                    "split": exp.split,
                    "temperature_K": _extract_temperature_K(exp, "prony_trs.plan"),
                    "channels": [ch.observable_id for ch in exp.channels],
                }
            )
            for ch in exp.channels:
                ch_slug = storage_segment(ch.observable_id)
                prefix = f"arrays/predictions/{request.evaluation_id}/{exp_slug}/{ch_slug}"
                expected_artifacts.extend(
                    [f"{prefix}.independent.npy", f"{prefix}.values.npy", f"{prefix}.sample_keys.npy", f"{prefix}.mask.npy"]
                )
        plan_id = f"plan-{uuid.uuid4().hex[:16]}"
        return EvaluationPlan(
            plan_id=plan_id,
            evaluation_id=request.evaluation_id,
            request_id=request.request_id,
            study_revision=request.study_revision,
            model_id=self.MODEL_ID,
            model_revision=self.MODEL_REVISION,
            adapter=self._identity,
            kind="in_process",
            execution_phase=request.execution_phase,
            physical_parameters=request.physical_parameters,
            experiment_ids=tuple(exp.experiment_id for exp in request.experiments),
            experiment_plans=tuple(exp_plans),
            expected_artifacts=tuple(expected_artifacts),
            numerical_settings=request.numerical_settings,
            callback_id="caelab.prony_trs.evaluate_v1",
        )

    def execute(
        self,
        plan: EvaluationPlan,
        request: MaterialEvaluationRequest,
        *,
        data_root: Path,
        study_id: str,
        cancel_event: threading.Event | None = None,
    ) -> Mapping[str, Any]:
        prony_mod, shift_mod, pyvisco_status = _require_verified_pyvisco("prony_trs.execute")
        param_vals = _extract_param_values(request.physical_parameters)
        e0, a_arr, tau_arr, c1, c2, tref = _validate_physical_domain(param_vals, stage="prony_trs.execute")
        allow_extended = bool(request.numerical_settings.get("allow_extended_temperature_range", False))

        # Write provenance artifact inside <data_root> (share during ephemeral SVD perturbations)
        rcache = request.runtime_cache if isinstance(request.runtime_cache, dict) else None
        is_ephemeral = request.evaluation_id.startswith("svd-")
        prov_payload = {
            "schema_version": 1,
            "adapter": self._identity.to_dict(),
            "model_id": self.MODEL_ID,
            "model_revision": self.MODEL_REVISION,
            "pyvisco_version": pyvisco_status["version"],
            "wlf_provenance": _to_plain_json(request.numerical_settings.get("wlf_provenance")),
            "loaded_source_checks": pyvisco_status["loaded_source_checks"],
            "license": PYVISCO_LICENSE,
            "repository": PYVISCO_REPOSITORY,
            "revision": PYVISCO_REVISION,
        }
        prov_ref = rcache.get("shared_prov_ref") if (rcache is not None and is_ephemeral) else None
        if prov_ref is None or not (data_root / prov_ref.path).is_file():
            prov_rel = (
                "artifacts/pyvisco-provenance-_shared.json"
                if (rcache is not None and is_ephemeral)
                else f"artifacts/pyvisco-provenance-{request.evaluation_id}.json"
            )
            validate_relative_artifact_path(prov_rel, root=data_root)
            prov_bytes = canonical_json_bytes(prov_payload)
            _atomic_write_bytes(data_root / prov_rel, prov_bytes)
            prov_ref = ArtifactRefV1(
                id="prov:_shared" if (rcache is not None and is_ephemeral) else f"prov:{request.evaluation_id}",
                role="adapter_provenance",
                path=prov_rel,
                sha256=hashlib.sha256(prov_bytes).hexdigest(),
                size=len(prov_bytes),
                format_id="application/json",
                producer=_PRODUCER,
            )
            if rcache is not None and is_ephemeral:
                rcache["shared_prov_ref"] = prov_ref

        exp_outputs: list[dict[str, Any]] = []
        all_artifacts: list[ArtifactRefV1] = [prov_ref]
        artifact_owners: dict[str, ArtifactOwner] = {
            prov_ref.id: ArtifactOwner(scope="consumer", id=f"material-study:{study_id}")
        }

        for exp in request.experiments:
            if cancel_event is not None and cancel_event.is_set():
                raise MaterialStudyError("CANCELLED", "prony_trs.execute", {"reason": "USER_CANCEL"})

            temp_k = _extract_temperature_K(exp, "prony_trs.execute")
            _validate_wlf_temperature(
                temp_k,
                tref_k=tref,
                c1=c1,
                c2=c2,
                allow_extended=allow_extended,
                stage="prony_trs.execute",
                experiment_id=exp.experiment_id,
            )
            log10_a_t = float(shift_mod.WLF(temp_k, tref, c1, c2))
            if not math.isfinite(log10_a_t):
                raise MaterialStudyError(
                    "NONFINITE_RESPONSE",
                    "prony_trs.execute",
                    {"reason": "NONFINITE_WLF_SHIFT", "temperature_K": temp_k},
                )
            a_t = 10.0 ** log10_a_t
            if not math.isfinite(a_t) or a_t <= 0.0:
                raise MaterialStudyError(
                    "NONFINITE_RESPONSE",
                    "prony_trs.execute",
                    {"reason": "INVALID_WLF_SHIFT_FACTOR", "aT": a_t},
                )

            exp_data_entry = request.experiment_data.get(exp.experiment_id, {})
            exp_slug = storage_segment(exp.experiment_id)
            ch_outputs: list[dict[str, Any]] = []

            for ch in exp.channels:
                ch_entry = exp_data_entry.get(ch.observable_id)
                if not isinstance(ch_entry, Mapping):
                    raise MaterialStudyError(
                        "EXPERIMENT_METADATA_MISSING",
                        "prony_trs.execute",
                        {"experiment_id": exp.experiment_id, "observable_id": ch.observable_id},
                    )
                if rcache is not None:
                    exp_cache = rcache.setdefault("exp_arrays", {})
                    exp_key = (exp.experiment_id, ch.observable_id)
                    if exp_key in exp_cache:
                        x_obs, _, sk_obs, _ = exp_cache[exp_key]
                    else:
                        loaded_exp = load_experiment_channel_arrays(ch_entry, data_root=data_root)
                        exp_cache[exp_key] = loaded_exp
                        x_obs, _, sk_obs, _ = loaded_exp
                else:
                    x_obs, _, sk_obs, _ = load_experiment_channel_arrays(ch_entry, data_root=data_root)
                ind_kind = str(ch_entry["independent_quantity_kind"])
                ind_unit = str(ch_entry["independent_unit"])
                obs = ch.observable
                n = x_obs.size

                if ch.observable_id in ("E_storage", "E_loss", "E_star"):
                    if ind_kind == "frequency":
                        omega = np.asarray(
                            convert_frequency_angular(
                                x_obs,
                                QuantityUnitSpec(ind_unit, "frequency"),
                                QuantityUnitSpec("rad/s", "angular_frequency"),
                            ),
                            dtype=np.float64,
                        )
                    elif ind_kind == "angular_frequency":
                        omega = np.asarray(
                            convert_values(
                                x_obs,
                                QuantityUnitSpec(ind_unit, "angular_frequency"),
                                QuantityUnitSpec("rad/s", "angular_frequency"),
                            ),
                            dtype=np.float64,
                        )
                    else:
                        raise MaterialStudyError(
                            "DATA_RECIPE_INCOMPATIBLE",
                            "prony_trs.execute",
                            {"reason": "DMA_REQUIRES_FREQUENCY_AXIS", "quantity_kind": ind_kind},
                        )
                    omega_r = omega * a_t
                    freq_norm = np.asarray(prony_mod.E_freq_norm(omega_r, a_arr, tau_arr), dtype=np.float64) * e0
                    if freq_norm.shape != (2 * n,):
                        raise MaterialStudyError(
                            "INTEGRATION_FAILED",
                            "prony_trs.execute",
                            {"reason": "UNEXPECTED_PYVISCO_FREQ_SHAPE", "shape": list(freq_norm.shape), "n": n},
                        )
                    e_stor_mpa = freq_norm[:n]
                    e_loss_mpa = freq_norm[n:]
                    if ch.observable_id == "E_storage":
                        y_mpa = e_stor_mpa.reshape(n, 1)
                        comp_order = obs.component_order or ("E_storage",)
                    elif ch.observable_id == "E_loss":
                        y_mpa = e_loss_mpa.reshape(n, 1)
                        comp_order = obs.component_order or ("E_loss",)
                    else:
                        y_mpa = np.column_stack((e_stor_mpa, e_loss_mpa))
                        comp_order = obs.component_order or ("E_storage", "E_loss")
                    y_out = np.asarray(
                        convert_values(
                            y_mpa,
                            QuantityUnitSpec("MPa", obs.quantity_kind),
                            QuantityUnitSpec(obs.unit, obs.quantity_kind),
                        ),
                        dtype=np.float64,
                    )
                elif ch.observable_id == "E_relax":
                    if ind_kind != "time":
                        raise MaterialStudyError(
                            "DATA_RECIPE_INCOMPATIBLE",
                            "prony_trs.execute",
                            {"reason": "RELAXATION_REQUIRES_TIME_AXIS", "quantity_kind": ind_kind},
                        )
                    t_s = np.asarray(
                        convert_values(
                            x_obs,
                            QuantityUnitSpec(ind_unit, "time"),
                            QuantityUnitSpec("s", "time"),
                        ),
                        dtype=np.float64,
                    )
                    t_r = t_s / a_t
                    y_mpa = (np.asarray(prony_mod.E_relax_norm(t_r, a_arr, tau_arr), dtype=np.float64) * e0).reshape(n, 1)
                    comp_order = obs.component_order or ("E_relax",)
                    y_out = np.asarray(
                        convert_values(
                            y_mpa,
                            QuantityUnitSpec("MPa", obs.quantity_kind),
                            QuantityUnitSpec(obs.unit, obs.quantity_kind),
                        ),
                        dtype=np.float64,
                    )
                elif ch.observable_id in ("stress_xx", "axial_stress"):
                    if ind_kind != "time":
                        raise MaterialStudyError(
                            "DATA_RECIPE_INCOMPATIBLE",
                            "prony_trs.execute",
                            {"reason": "PATH_HISTORY_REQUIRES_TIME_AXIS", "quantity_kind": ind_kind},
                        )
                    strain_series = exp.conditions.get("strain_history")
                    if strain_series is None or len(strain_series) != n:
                        raise MaterialStudyError(
                            "EXPERIMENT_METADATA_MISSING",
                            "prony_trs.execute",
                            {"reason": "MISSING_STRAIN_HISTORY", "experiment_id": exp.experiment_id},
                        )
                    t_s = np.asarray(
                        convert_values(x_obs, QuantityUnitSpec(ind_unit, "time"), QuantityUnitSpec("s", "time")),
                        dtype=np.float64,
                    )
                    temp_series = np.asarray(
                        exp.conditions.get("temperature_history_K", np.full(n, temp_k, dtype=np.float64)),
                        dtype=np.float64,
                    )
                    stress_mpa = _integrate_strain_history(
                        t_s,
                        np.asarray(strain_series, dtype=np.float64),
                        temp_series,
                        e0=e0,
                        a_arr=a_arr,
                        tau_arr=tau_arr,
                        tref_k=tref,
                        c1=c1,
                        c2=c2,
                        shift_mod=shift_mod,
                    ).reshape(n, 1)
                    comp_order = obs.component_order or (ch.observable_id,)
                    y_out = np.asarray(
                        convert_values(
                            stress_mpa,
                            QuantityUnitSpec("MPa", obs.quantity_kind),
                            QuantityUnitSpec(obs.unit, obs.quantity_kind),
                        ),
                        dtype=np.float64,
                    )
                else:
                    raise MaterialStudyError(
                        "UNSUPPORTED_OBSERVABLE",
                        "prony_trs.execute",
                        {"observable_id": ch.observable_id},
                    )

                if not np.isfinite(y_out).all():
                    raise MaterialStudyError(
                        "NONFINITE_RESPONSE",
                        "prony_trs.execute",
                        {"experiment_id": exp.experiment_id, "observable_id": ch.observable_id},
                    )

                mask_arr = np.ones(y_out.shape, dtype="|b1")
                ch_slug = storage_segment(ch.observable_id)
                base_rel = f"arrays/predictions/{request.evaluation_id}/{exp_slug}/{ch_slug}"

                def _save_pred_npy(rel: str, aid: str, role: str, arr: np.ndarray) -> ArtifactRefV1:
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

                def _track(ref: ArtifactRefV1) -> ArtifactRefV1:
                    all_artifacts.append(ref)
                    artifact_owners[ref.id] = ArtifactOwner(scope="consumer", id=f"material-study:{study_id}")
                    return ref

                vals_ref = _track(
                    _save_pred_npy(
                        f"{base_rel}.values.npy",
                        f"pred:{request.evaluation_id}:{exp_slug}:{ch_slug}:values",
                        "predicted_values",
                        y_out,
                    )
                )

                shared_key = (exp.experiment_id, exp.experiment_revision, ch.observable_id)
                shared_refs_cache = rcache.setdefault("shared_pred_refs", {}) if rcache is not None else None
                cached_triplet = shared_refs_cache.get(shared_key) if shared_refs_cache is not None else None
                if cached_triplet is not None and all((data_root / r.path).is_file() for r in cached_triplet):
                    ind_ref, sk_ref, mask_ref = (_track(r) for r in cached_triplet)
                elif shared_refs_cache is not None:
                    rev_tag = exp.experiment_revision[:16]
                    sbase = f"arrays/predictions/_shared/{exp_slug}_{rev_tag}/{ch_slug}"
                    ind_ref = _track(_save_pred_npy(f"{sbase}.independent.npy", f"pred:_shared:{exp_slug}:{rev_tag}:{ch_slug}:independent", "predicted_independent", x_obs))
                    sk_ref = _track(_save_pred_npy(f"{sbase}.sample_keys.npy", f"pred:_shared:{exp_slug}:{rev_tag}:{ch_slug}:sample_keys", "predicted_sample_keys", sk_obs))
                    mask_ref = _track(_save_pred_npy(f"{sbase}.mask.npy", f"pred:_shared:{exp_slug}:{rev_tag}:{ch_slug}:mask", "predicted_mask", mask_arr))
                    shared_refs_cache[shared_key] = (ind_ref, sk_ref, mask_ref)
                    verified_cache = rcache.setdefault("verified_arrays", {})
                    verified_cache[(ind_ref.path, ind_ref.sha256)] = np.asarray(x_obs, dtype=np.float64)
                    verified_cache[(sk_ref.path, sk_ref.sha256)] = np.asarray(sk_obs)
                    verified_cache[(mask_ref.path, mask_ref.sha256)] = np.asarray(mask_arr, dtype="|b1")
                else:
                    ind_ref = _track(_save_pred_npy(f"{base_rel}.independent.npy", f"pred:{request.evaluation_id}:{exp_slug}:{ch_slug}:independent", "predicted_independent", x_obs))
                    sk_ref = _track(_save_pred_npy(f"{base_rel}.sample_keys.npy", f"pred:{request.evaluation_id}:{exp_slug}:{ch_slug}:sample_keys", "predicted_sample_keys", sk_obs))
                    mask_ref = _track(_save_pred_npy(f"{base_rel}.mask.npy", f"pred:{request.evaluation_id}:{exp_slug}:{ch_slug}:mask", "predicted_mask", mask_arr))

                ch_outputs.append(
                    {
                        "observable_id": ch.observable_id,
                        "observable": obs,
                        "independent_ref": ind_ref,
                        "sample_keys_ref": sk_ref,
                        "sample_key_columns": tuple(ch_entry.get("sample_key_columns", ("source_row",))),
                        "values_ref": vals_ref,
                        "mask_ref": mask_ref,
                        "component_order": comp_order,
                        "value_kind": obs.value_kind,
                        "representation": obs.representation,
                        "coverage": {"expected": int(n), "valid": int(n), "missing": 0},
                        "source_refs": (exp.source_ref, prov_ref),
                    }
                )

            exp_outputs.append(
                {
                    "experiment_id": exp.experiment_id,
                    "experiment_revision": exp.experiment_revision,
                    "state": "completed",
                    "channels": ch_outputs,
                    "diagnostics": [{"temperature_K": temp_k, "log10_aT": log10_a_t, "aT": a_t}],
                }
            )

        return {
            "request_id": request.request_id,
            "evaluation_id": request.evaluation_id,
            "study_revision": request.study_revision,
            "experiments": exp_outputs,
            "artifacts": all_artifacts,
            "artifact_owners": artifact_owners,
            "provenance": prov_payload,
        }

    def collect(
        self,
        plan: EvaluationPlan,
        artifacts: Mapping[str, Any],
        *,
        data_root: Path,
        study_id: str,
    ) -> PredictionBundleV1:
        exp_preds: list[ExperimentPredictionV1] = []
        for exp_entry in artifacts.get("experiments", ()):
            channels = tuple(
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
                for ch in exp_entry["channels"]
            )
            exp_preds.append(
                ExperimentPredictionV1(
                    experiment_id=exp_entry["experiment_id"],
                    experiment_revision=exp_entry["experiment_revision"],
                    state=exp_entry["state"],
                    initial_state_ref=None,
                    channels=channels,
                    execution_refs=(),
                    diagnostics=exp_entry.get("diagnostics", ()),
                )
            )

        prov = artifacts.get("provenance", {})
        bundle = PredictionBundleV1(
            evaluation_id=plan.evaluation_id,
            request_id=plan.request_id,
            study_revision=plan.study_revision,
            model_revision=plan.model_revision,
            adapter=self._identity,
            experiments=tuple(exp_preds),
            optimizer_scalars={},
            artifacts=tuple(artifacts.get("artifacts", ())),
            artifact_owners=artifacts.get("artifact_owners", {}),
            run_ids=(),
            diagnostics=({"pyvisco_provenance": prov},) if prov else (),
        )
        bundle.validate_level("material_library")
        return bundle

    def export(
        self,
        evaluation_artifact: Mapping[str, Any],
        *,
        destination: Path | None = None,
    ) -> MaterialExportPlan:
        export_id = f"export-{uuid.uuid4().hex[:12]}"
        pyvisco_status = verify_pyvisco_sources()
        params = dict(evaluation_artifact.get("physical_parameters", {}))
        e0 = float(params.get("E0", 0.0))
        a1 = float(params.get("a1", 0.0))
        a2 = float(params.get("a2", 0.0))
        assessment = evaluation_artifact.get("assessment", {})
        assess_block = assessment.get("assessment", {}) if isinstance(assessment, Mapping) else {}
        split_checks = assess_block.get("split_checks", [])
        channel_checks = assess_block.get("channel_checks", [])

        report = {
            "schema_version": 1,
            "export_id": export_id,
            "format_kind": "parameter_formulation_report_json",
            "supports_native_card": False,
            "supports_apply": False,
            "model_id": self.MODEL_ID,
            "model_revision": self.MODEL_REVISION,
            "adapter": self._identity.to_dict(),
            "study_id": str(evaluation_artifact.get("study_id", "")),
            "content_revision": str(evaluation_artifact.get("content_revision", "")),
            "evaluation_id": str(evaluation_artifact.get("evaluation_id", "")),
            "execution_phase": str(evaluation_artifact.get("execution_phase", "exploration")),
            "formulation": {
                "constitutive_law": "Generalized Maxwell 2-term Prony series with WLF horizontal shift (TRS)",
                "relaxation_modulus": "E(t, T) = E0 * (1 - a1 - a2) + E0 * sum_{i=1..2} a_i * exp(-t / (aT(T) * tau_i))",
                "frequency_modulus": "E*(omega, T) = E'(aT*omega) + i E''(aT*omega)",
                "wlf_shift": "log10(aT) = -C1 * (T - Tref) / (C2 + T - Tref)",
                "equilibrium_modulus_MPa": e0 * (1.0 - a1 - a2),
            },
            "parameters": {
                "E0": {"value": params.get("E0"), "unit": "MPa"},
                "a1": {"value": params.get("a1"), "unit": ""},
                "a2": {"value": params.get("a2"), "unit": ""},
                "tau1": {"value": params.get("tau1"), "unit": "s"},
                "tau2": {"value": params.get("tau2"), "unit": "s"},
                "C1": {"value": params.get("C1"), "unit": ""},
                "C2": {"value": params.get("C2"), "unit": "K"},
                "Tref": {"value": params.get("Tref"), "unit": "K"},
            },
            "provenance": {
                "pyvisco_version": pyvisco_status.get("version"),
                "loaded_source_checks": pyvisco_status.get("loaded_source_checks", []),
                "license": PYVISCO_LICENSE,
                "repository": PYVISCO_REPOSITORY,
                "revision": PYVISCO_REVISION,
            },
            "sources": list(evaluation_artifact.get("sources", ())),
            "assessment_summary": {
                "objective": evaluation_artifact.get("assessment", {}).get("objective", {}),
                "fit_status": assess_block.get("fit_status", "not_assessed"),
                "validation_status": assess_block.get("validation_status", "not_assessed"),
                "holdout_status": assess_block.get("holdout_status", "not_assessed"),
                "constraint_status": assess_block.get("constraint_status", "NOT_ASSESSED"),
                "identifiability_status": assess_block.get("identifiability_status", "not_computed"),
                "split_checks": split_checks,
                "channel_checks": channel_checks,
            },
            "limitations": [
                "Small-strain linear viscoelastic scalar library evaluation only (not finite-strain rubber viscoelasticity).",
                "Native solver card export is not supported for B-VIS-TRS-1; this artifact is a parameter/formulation report JSON.",
                "Open Prep document Apply is not supported (supports_apply=false).",
            ],
        }

        report_bytes = canonical_json_bytes(report).decode("utf-8")
        return MaterialExportPlan(
            export_id=export_id,
            format_kind="parameter_formulation_report_json",
            supports_native_card=False,
            supported_solver_identity=None,
            model_id=self.MODEL_ID,
            model_revision=self.MODEL_REVISION,
            adapter=self._identity,
            study_id=str(evaluation_artifact.get("study_id", "")),
            content_revision=str(evaluation_artifact.get("content_revision", "")),
            evaluation_id=str(evaluation_artifact.get("evaluation_id", "")),
            formulation=report["formulation"],
            parameters=report["parameters"],
            provenance=report["provenance"],
            sources=tuple(report["sources"]),
            assessment_summary=report["assessment_summary"],
            limitations=tuple(report["limitations"]),
            files=(
                {
                    "rel_path": "prony_trs_report.json",
                    "role": "export_report",
                    "format_id": "application/json",
                    "content_text": report_bytes,
                },
            ),
        )
