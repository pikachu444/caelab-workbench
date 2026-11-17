"""Pure immutable v1 material study, adapter, prediction, assessment, and receipt contracts (C2/C3/C4/C5)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
import math
from types import MappingProxyType
from typing import Any

import numpy as np

from caelab.artifact_refs import (
    ArtifactOwner,
    ArtifactRefV1,
    ResultRefV1,
    _coerce_artifact_ref,
    _freeze_json_value,
    _freeze_mapping,
    _require_bool,
    _require_exact_keys,
    _require_finite_float,
    _require_nonempty_str,
    _require_optional_nonempty_str,
    _require_schema_version_v1,
    _require_sha256,
    _require_str_tuple,
    _to_plain_json,
)
from caelab.materials.units import validate_unit_string
from caelab.mdo.constraints import ConstraintSpec

__all__ = [
    "AdapterIdentity",
    "AssessmentResultV1",
    "CampaignReceipt",
    "CancelReceipt",
    "CapabilityReport",
    "ComparisonSpec",
    "EvaluationReceipt",
    "ExperimentChannelSpec",
    "ExperimentPredictionV1",
    "ExperimentSpec",
    "ImportReceipt",
    "ModelDescriptorV1",
    "ObservableCapability",
    "ObservableSpec",
    "ParameterSpec",
    "PredictedChannelV1",
    "PredictionBundle",
    "PredictionBundleV1",
    "ScalarResponseSpecV1",
    "SelectionRecordV1",
    "StudySnapshot",
]

_LEVELS = frozenset({"analytic", "material_library", "native_fe"})
_SCALAR_SOURCE_KINDS = frozenset({"residual", "parameter_expression", "adapter_scalar"})
_PARAM_ROLES = frozenset({"free", "fixed", "linked"})
_PARAM_TRANSFORMS = frozenset({"linear", "log"})
_VALUE_KINDS = frozenset({"real", "complex"})
_REAL_REPRESENTATIONS = frozenset({"scalar", "components"})
_COMPLEX_REPRESENTATIONS = frozenset({"real_imag", "storage_loss"})
_REPRESENTATIONS = _REAL_REPRESENTATIONS | _COMPLEX_REPRESENTATIONS
_CAPABILITY_REAL_REPS = frozenset({"scalar", "components", "vector", "tensor", "symmetric_tensor"})
_CAPABILITY_COMPLEX_REPS = _COMPLEX_REPRESENTATIONS
_CAPABILITY_REPRESENTATIONS = _CAPABILITY_REAL_REPS | _CAPABILITY_COMPLEX_REPS
_SPLITS = frozenset({"train", "validation", "holdout"})
_PASS_FAIL_NA = frozenset({"pass", "fail", "not_assessed"})
_UNCERTAINTY_STATUSES = frozenset({"pass", "fail", "not_computed"})
_IDENTIFIABILITY_STATUSES = frozenset({"pass", "fail", "not_computed", "not_identifiable"})
_CONSTRAINT_STATUSES = frozenset({"FEASIBLE", "MARGINAL", "INFEASIBLE", "NOT_ASSESSED"})
_CONSTRAINT_KEYS = frozenset({
    "id",
    "response_id",
    "kind",
    "limit",
    "scale",
    "tolerance",
    "marginal_band",
    "weight",
    "expression",
    "dependencies",
    "unit",
})


def _coerce_typed_tuple(values: Any, expected_type: type, name: str) -> tuple[Any, ...]:
    if isinstance(values, (str, bytes, Mapping)) or not isinstance(values, Sequence):
        raise ValueError(f"{name} must be a sequence")
    items = tuple(values)
    for idx, item in enumerate(items):
        if not isinstance(item, expected_type):
            raise ValueError(f"{name}[{idx}] must be a {expected_type.__name__}")
    return items


def _coerce_diagnostics(values: Any, name: str) -> tuple[MappingProxyType[str, Any] | str, ...]:
    if isinstance(values, (str, bytes, Mapping)) or not isinstance(values, Sequence):
        raise ValueError(f"{name} must be a sequence")
    result: list[MappingProxyType[str, Any] | str] = []
    for idx, item in enumerate(values):
        if isinstance(item, str):
            result.append(_require_nonempty_str(item, f"{name}[{idx}]"))
        elif isinstance(item, Mapping):
            result.append(_freeze_mapping(item, context=f"{name}[{idx}]"))
        else:
            raise ValueError(f"{name}[{idx}] must be a nonempty string or mapping")
    return tuple(result)


def _coerce_constraint_spec(value: Any, name: str) -> ConstraintSpec:
    if isinstance(value, ConstraintSpec):
        return value
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a ConstraintSpec or mapping")
    _require_exact_keys(value, {"id", "kind", "limit"}, optional=_CONSTRAINT_KEYS - {"id", "kind", "limit"}, context=name)
    kwargs: dict[str, Any] = dict(value)
    if "response_id" not in kwargs:
        kwargs["response_id"] = None
    if "dependencies" in kwargs:
        kwargs["dependencies"] = _require_str_tuple(kwargs["dependencies"], f"{name}.dependencies")
    if "unit" in kwargs and isinstance(kwargs["unit"], str) and kwargs["unit"] != "":
        validate_unit_string(kwargs["unit"], allow_empty=True)
    return ConstraintSpec(**kwargs)


def _constraint_to_dict(spec: ConstraintSpec) -> dict[str, Any]:
    return {
        "id": spec.id,
        "response_id": spec.response_id,
        "kind": spec.kind,
        "limit": spec.limit,
        "scale": spec.scale,
        "tolerance": spec.tolerance,
        "marginal_band": spec.marginal_band,
        "weight": spec.weight,
        "expression": spec.expression,
        "dependencies": list(spec.dependencies),
        "unit": spec.unit,
    }


def _dataclass_to_plain(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _dataclass_to_plain(value.to_dict())
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _dataclass_to_plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Enum):
        return _dataclass_to_plain(value.value)
    if isinstance(value, Mapping):
        return {k: _dataclass_to_plain(v) for k, v in value.items()}
    if isinstance(value, np.ndarray):
        return [_dataclass_to_plain(x) for x in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [_dataclass_to_plain(item) for item in value]
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


def _coerce_mapping_or_dataclass(value: Any, name: str) -> MappingProxyType[str, Any]:
    if isinstance(value, Mapping):
        return _freeze_mapping(value, context=name)
    if is_dataclass(value) and not isinstance(value, type):
        return _freeze_mapping(_dataclass_to_plain(value), context=name)
    raise ValueError(f"{name} must be a mapping")


def _validate_value_kind_representation(
    value_kind: str,
    representation: str,
    component_order: tuple[str, ...],
    *,
    context: str,
    require_nonempty_components: bool = False,
) -> None:
    if value_kind not in _VALUE_KINDS:
        raise ValueError(f"{context}.value_kind must be one of {sorted(_VALUE_KINDS)}")
    if representation not in _REPRESENTATIONS:
        raise ValueError(f"{context}.representation must be one of {sorted(_REPRESENTATIONS)}")
    if value_kind == "complex":
        if representation not in _COMPLEX_REPRESENTATIONS:
            raise ValueError(
                f"{context} with value_kind='complex' requires representation in {sorted(_COMPLEX_REPRESENTATIONS)}"
            )
        if len(component_order) != 2:
            raise ValueError(f"{context} with value_kind='complex' requires len(component_order) == 2")
    else:
        if representation in _COMPLEX_REPRESENTATIONS:
            raise ValueError(
                f"{context} with value_kind='real' cannot use complex representation {representation!r}"
            )
        if require_nonempty_components:
            if not component_order:
                raise ValueError(f"{context}.component_order must be non-empty")
            if representation == "scalar" and len(component_order) != 1:
                raise ValueError(
                    f"{context} with value_kind='real' and representation='scalar' requires len(component_order) == 1"
                )


@dataclass(frozen=True)
class AdapterIdentity:
    """Identity and optional provenance digest of a registered material adapter."""

    id: str
    version: str
    implementation_sha256: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.id, "AdapterIdentity.id")
        _require_nonempty_str(self.version, "AdapterIdentity.version")
        if self.implementation_sha256 is not None:
            _require_sha256(self.implementation_sha256, "AdapterIdentity.implementation_sha256")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "version": self.version,
            "implementation_sha256": self.implementation_sha256,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AdapterIdentity:
        m = _require_exact_keys(
            data,
            {"schema_version", "id", "version", "implementation_sha256"},
            context="AdapterIdentity",
        )
        _require_schema_version_v1(m["schema_version"])
        return cls(
            schema_version=1,
            id=m["id"],
            version=m["version"],
            implementation_sha256=m["implementation_sha256"],
        )


@dataclass(frozen=True)
class ObservableCapability:
    """Supported observable representations, locations, and units declared by a model or adapter."""

    observable_id: str
    value_kinds: tuple[str, ...]
    representations: tuple[str, ...]
    locations: tuple[str, ...]
    supported_units: tuple[str, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.observable_id, "ObservableCapability.observable_id")
        value_kinds = _require_str_tuple(self.value_kinds, "ObservableCapability.value_kinds")
        if not value_kinds or any(vk not in _VALUE_KINDS for vk in value_kinds):
            raise ValueError(f"ObservableCapability.value_kinds must be nonempty subset of {sorted(_VALUE_KINDS)}")
        reps = _require_str_tuple(self.representations, "ObservableCapability.representations")
        if not reps or any(r not in _CAPABILITY_REPRESENTATIONS for r in reps):
            raise ValueError(
                f"ObservableCapability.representations must be nonempty subset of {sorted(_CAPABILITY_REPRESENTATIONS)}"
            )
        vk_set = set(value_kinds)
        rep_set = set(reps)
        if "complex" in vk_set and not (rep_set & _CAPABILITY_COMPLEX_REPS):
            raise ValueError(
                "ObservableCapability with 'complex' value_kind requires 'real_imag' or 'storage_loss' representation"
            )
        if "real" in vk_set and not (rep_set & _CAPABILITY_REAL_REPS):
            raise ValueError(
                "ObservableCapability with 'real' value_kind requires at least one real representation"
            )
        if vk_set == {"complex"} and (rep_set & _CAPABILITY_REAL_REPS):
            raise ValueError(
                "ObservableCapability with only 'complex' value_kind cannot include real representations"
            )
        if vk_set == {"real"} and (rep_set & _CAPABILITY_COMPLEX_REPS):
            raise ValueError(
                "ObservableCapability with only 'real' value_kind cannot include complex representations"
            )
        locs = _require_str_tuple(self.locations, "ObservableCapability.locations")
        if not locs:
            raise ValueError("ObservableCapability.locations must be nonempty")
        units = _require_str_tuple(self.supported_units, "ObservableCapability.supported_units", allow_empty_item=True)
        if not units:
            raise ValueError("ObservableCapability.supported_units must be nonempty")
        for u in units:
            validate_unit_string(u, allow_empty=True)

        object.__setattr__(self, "value_kinds", value_kinds)
        object.__setattr__(self, "representations", reps)
        object.__setattr__(self, "locations", locs)
        object.__setattr__(self, "supported_units", units)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "observable_id": self.observable_id,
            "value_kinds": list(self.value_kinds),
            "representations": list(self.representations),
            "locations": list(self.locations),
            "supported_units": list(self.supported_units),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ObservableCapability:
        m = _require_exact_keys(
            data,
            {"schema_version", "observable_id", "value_kinds", "representations", "locations", "supported_units"},
            context="ObservableCapability",
        )
        _require_schema_version_v1(m["schema_version"])
        return cls(
            schema_version=1,
            observable_id=m["observable_id"],
            value_kinds=m["value_kinds"],
            representations=m["representations"],
            locations=m["locations"],
            supported_units=m["supported_units"],
        )


@dataclass(frozen=True)
class ScalarResponseSpecV1:
    """Registered scalar response contract (C5)."""

    response_id: str
    unit: str
    source_kind: str
    source_name: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.response_id, "ScalarResponseSpecV1.response_id")
        validate_unit_string(self.unit, allow_empty=True)
        if self.source_kind not in _SCALAR_SOURCE_KINDS:
            raise ValueError(
                f"ScalarResponseSpecV1.source_kind must be one of {sorted(_SCALAR_SOURCE_KINDS)}, got {self.source_kind!r}"
            )
        _require_nonempty_str(self.source_name, "ScalarResponseSpecV1.source_name")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "response_id": self.response_id,
            "unit": self.unit,
            "source_kind": self.source_kind,
            "source_name": self.source_name,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ScalarResponseSpecV1:
        m = _require_exact_keys(
            data,
            {"schema_version", "response_id", "unit", "source_kind", "source_name"},
            context="ScalarResponseSpecV1",
        )
        _require_schema_version_v1(m["schema_version"])
        return cls(
            schema_version=1,
            response_id=m["response_id"],
            unit=m["unit"],
            source_kind=m["source_kind"],
            source_name=m["source_name"],
        )


@dataclass(frozen=True)
class CapabilityReport:
    """Host/environment capability report returned by MaterialAdapter.capabilities."""

    adapter: AdapterIdentity
    model_id: str
    model_revision: str
    level: str
    supported: bool
    can_run: bool
    differentiable: bool
    observable_capabilities: tuple[ObservableCapability, ...] = ()
    optimizer_responses: tuple[ScalarResponseSpecV1, ...] = ()
    optimizer_constraints: tuple[ConstraintSpec | Mapping[str, Any], ...] = ()
    supports_export: bool = False
    supports_apply: bool = False
    reason_codes: tuple[str, ...] = ()
    evidence_refs: tuple[ArtifactRefV1, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        if not isinstance(self.adapter, AdapterIdentity):
            raise ValueError("CapabilityReport.adapter must be an AdapterIdentity")
        _require_nonempty_str(self.model_id, "CapabilityReport.model_id")
        _require_nonempty_str(self.model_revision, "CapabilityReport.model_revision")
        if self.level not in _LEVELS:
            raise ValueError(f"CapabilityReport.level must be one of {sorted(_LEVELS)}, got {self.level!r}")
        for flag in ("supported", "can_run", "differentiable", "supports_export", "supports_apply"):
            _require_bool(getattr(self, flag), f"CapabilityReport.{flag}")
        if self.can_run and not self.supported:
            raise ValueError("CapabilityReport.can_run=True requires supported=True")

        obs_caps = _coerce_typed_tuple(
            self.observable_capabilities, ObservableCapability, "CapabilityReport.observable_capabilities"
        )
        if len({o.observable_id for o in obs_caps}) != len(obs_caps):
            raise ValueError("CapabilityReport.observable_capabilities contains duplicate observable_id")
        object.__setattr__(self, "observable_capabilities", obs_caps)

        responses = _coerce_typed_tuple(
            self.optimizer_responses, ScalarResponseSpecV1, "CapabilityReport.optimizer_responses"
        )
        if len({r.response_id for r in responses}) != len(responses):
            raise ValueError("CapabilityReport.optimizer_responses contains duplicate response_id")
        object.__setattr__(self, "optimizer_responses", responses)

        if isinstance(self.optimizer_constraints, (str, bytes, Mapping)) or not isinstance(
            self.optimizer_constraints, Sequence
        ):
            raise ValueError("CapabilityReport.optimizer_constraints must be a sequence")
        constraints = tuple(
            _coerce_constraint_spec(c, f"CapabilityReport.optimizer_constraints[{idx}]")
            for idx, c in enumerate(self.optimizer_constraints)
        )
        if len({c.id for c in constraints}) != len(constraints):
            raise ValueError("CapabilityReport.optimizer_constraints contains duplicate id")
        object.__setattr__(self, "optimizer_constraints", constraints)

        object.__setattr__(self, "reason_codes", _require_str_tuple(self.reason_codes, "CapabilityReport.reason_codes"))
        ev_refs = _coerce_typed_tuple(self.evidence_refs, ArtifactRefV1, "CapabilityReport.evidence_refs")
        object.__setattr__(self, "evidence_refs", ev_refs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "adapter": self.adapter.to_dict(),
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "level": self.level,
            "supported": self.supported,
            "can_run": self.can_run,
            "differentiable": self.differentiable,
            "observable_capabilities": [item.to_dict() for item in self.observable_capabilities],
            "optimizer_responses": [item.to_dict() for item in self.optimizer_responses],
            "optimizer_constraints": [_constraint_to_dict(c) for c in self.optimizer_constraints],
            "supports_export": self.supports_export,
            "supports_apply": self.supports_apply,
            "reason_codes": list(self.reason_codes),
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CapabilityReport:
        required = {
            "schema_version",
            "adapter",
            "model_id",
            "model_revision",
            "level",
            "supported",
            "can_run",
            "differentiable",
            "observable_capabilities",
            "optimizer_responses",
            "optimizer_constraints",
            "supports_export",
            "supports_apply",
            "reason_codes",
            "evidence_refs",
        }
        m = _require_exact_keys(data, required, context="CapabilityReport")
        _require_schema_version_v1(m["schema_version"])
        for seq_key in ("observable_capabilities", "optimizer_responses", "optimizer_constraints", "evidence_refs"):
            raw_seq = m[seq_key]
            if isinstance(raw_seq, (str, bytes, Mapping)) or not isinstance(raw_seq, Sequence):
                raise ValueError(f"CapabilityReport.{seq_key} must be a list")
        return cls(
            schema_version=1,
            adapter=AdapterIdentity.from_dict(m["adapter"]),
            model_id=m["model_id"],
            model_revision=m["model_revision"],
            level=m["level"],
            supported=m["supported"],
            can_run=m["can_run"],
            differentiable=m["differentiable"],
            observable_capabilities=tuple(
                ObservableCapability.from_dict(item) for item in m["observable_capabilities"]
            ),
            optimizer_responses=tuple(ScalarResponseSpecV1.from_dict(item) for item in m["optimizer_responses"]),
            optimizer_constraints=tuple(m["optimizer_constraints"]),
            supports_export=m["supports_export"],
            supports_apply=m["supports_apply"],
            reason_codes=m["reason_codes"],
            evidence_refs=tuple(ArtifactRefV1.from_dict(item) for item in m["evidence_refs"]),
        )


@dataclass(frozen=True)
class ParameterSpec:
    """Material model parameter contract connecting roles, units, bounds, and linked expressions."""

    id: str
    name: str
    unit: str
    lower: float | None = None
    upper: float | None = None
    default: float | None = None
    transform: str = "linear"
    role: str = "free"
    expression: str | None = None
    dependencies: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.id, "ParameterSpec.id")
        _require_nonempty_str(self.name, "ParameterSpec.name")
        validate_unit_string(self.unit, allow_empty=True)
        if self.transform not in _PARAM_TRANSFORMS:
            raise ValueError(f"ParameterSpec.transform must be one of {sorted(_PARAM_TRANSFORMS)}")
        if self.role not in _PARAM_ROLES:
            raise ValueError(f"ParameterSpec.role must be one of {sorted(_PARAM_ROLES)}")

        lo = None if self.lower is None else _require_finite_float(self.lower, "ParameterSpec.lower")
        hi = None if self.upper is None else _require_finite_float(self.upper, "ParameterSpec.upper")
        default = None if self.default is None else _require_finite_float(self.default, "ParameterSpec.default")
        object.__setattr__(self, "lower", lo)
        object.__setattr__(self, "upper", hi)
        object.__setattr__(self, "default", default)

        if lo is not None and hi is not None:
            if self.role == "free" and lo >= hi:
                raise ValueError("ParameterSpec with role='free' requires lower < upper")
            if lo > hi:
                raise ValueError("ParameterSpec requires lower <= upper")
        if self.transform == "log":
            if lo is not None and lo <= 0.0:
                raise ValueError("ParameterSpec with transform='log' requires lower > 0")
            if hi is not None and hi <= 0.0:
                raise ValueError("ParameterSpec with transform='log' requires upper > 0")
            if default is not None and default <= 0.0:
                raise ValueError("ParameterSpec with transform='log' requires default > 0")
        if default is not None:
            if lo is not None and default < lo:
                raise ValueError("ParameterSpec default must be >= lower")
            if hi is not None and default > hi:
                raise ValueError("ParameterSpec default must be <= upper")

        deps = _require_str_tuple(self.dependencies, "ParameterSpec.dependencies")
        if len(set(deps)) != len(deps):
            raise ValueError("ParameterSpec.dependencies must not contain duplicates")
        object.__setattr__(self, "dependencies", deps)
        if self.role == "linked":
            _require_nonempty_str(self.expression, "ParameterSpec.expression")
        else:
            if self.expression is not None:
                raise ValueError(f"ParameterSpec with role={self.role!r} must not set expression")
            if deps:
                raise ValueError(f"ParameterSpec with role={self.role!r} must not set dependencies")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "unit": self.unit,
            "lower": self.lower,
            "upper": self.upper,
            "default": self.default,
            "transform": self.transform,
            "role": self.role,
            "expression": self.expression,
            "dependencies": list(self.dependencies),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ParameterSpec:
        required = {
            "schema_version",
            "id",
            "name",
            "unit",
            "lower",
            "upper",
            "default",
            "transform",
            "role",
            "expression",
            "dependencies",
        }
        m = _require_exact_keys(data, required, context="ParameterSpec")
        _require_schema_version_v1(m["schema_version"])
        return cls(
            schema_version=1,
            id=m["id"],
            name=m["name"],
            unit=m["unit"],
            lower=m["lower"],
            upper=m["upper"],
            default=m["default"],
            transform=m["transform"],
            role=m["role"],
            expression=m["expression"],
            dependencies=m["dependencies"],
        )


@dataclass(frozen=True)
class ModelDescriptorV1:
    """Registered constitutive/material model profile descriptor."""

    model_id: str
    model_revision: str
    family: str
    name: str
    parameter_specs: tuple[ParameterSpec, ...]
    observable_capabilities: tuple[ObservableCapability, ...] = ()
    conditions_schema_ref: ArtifactRefV1 | None = None
    level: str = "analytic"
    supports_export: bool = False
    supports_apply: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.model_id, "ModelDescriptorV1.model_id")
        _require_nonempty_str(self.model_revision, "ModelDescriptorV1.model_revision")
        _require_nonempty_str(self.family, "ModelDescriptorV1.family")
        _require_nonempty_str(self.name, "ModelDescriptorV1.name")
        params = _coerce_typed_tuple(self.parameter_specs, ParameterSpec, "ModelDescriptorV1.parameter_specs")
        if len({p.id for p in params}) != len(params):
            raise ValueError("ModelDescriptorV1.parameter_specs contains duplicate id")
        object.__setattr__(self, "parameter_specs", params)

        obs_caps = _coerce_typed_tuple(
            self.observable_capabilities, ObservableCapability, "ModelDescriptorV1.observable_capabilities"
        )
        if len({o.observable_id for o in obs_caps}) != len(obs_caps):
            raise ValueError("ModelDescriptorV1.observable_capabilities contains duplicate observable_id")
        object.__setattr__(self, "observable_capabilities", obs_caps)

        if self.conditions_schema_ref is not None and not isinstance(self.conditions_schema_ref, ArtifactRefV1):
            raise ValueError("ModelDescriptorV1.conditions_schema_ref must be None or ArtifactRefV1")
        if self.level not in _LEVELS:
            raise ValueError(f"ModelDescriptorV1.level must be one of {sorted(_LEVELS)}, got {self.level!r}")
        _require_bool(self.supports_export, "ModelDescriptorV1.supports_export")
        _require_bool(self.supports_apply, "ModelDescriptorV1.supports_apply")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "family": self.family,
            "name": self.name,
            "parameter_specs": [p.to_dict() for p in self.parameter_specs],
            "observable_capabilities": [o.to_dict() for o in self.observable_capabilities],
            "conditions_schema_ref": None if self.conditions_schema_ref is None else self.conditions_schema_ref.to_dict(),
            "level": self.level,
            "supports_export": self.supports_export,
            "supports_apply": self.supports_apply,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ModelDescriptorV1:
        required = {
            "schema_version",
            "model_id",
            "model_revision",
            "family",
            "name",
            "parameter_specs",
            "observable_capabilities",
            "conditions_schema_ref",
            "level",
            "supports_export",
            "supports_apply",
        }
        m = _require_exact_keys(data, required, context="ModelDescriptorV1")
        _require_schema_version_v1(m["schema_version"])
        for seq_key in ("parameter_specs", "observable_capabilities"):
            if isinstance(m[seq_key], (str, bytes, Mapping)) or not isinstance(m[seq_key], Sequence):
                raise ValueError(f"ModelDescriptorV1.{seq_key} must be a list")
        return cls(
            schema_version=1,
            model_id=m["model_id"],
            model_revision=m["model_revision"],
            family=m["family"],
            name=m["name"],
            parameter_specs=tuple(ParameterSpec.from_dict(p) for p in m["parameter_specs"]),
            observable_capabilities=tuple(ObservableCapability.from_dict(o) for o in m["observable_capabilities"]),
            conditions_schema_ref=(
                None if m["conditions_schema_ref"] is None else ArtifactRefV1.from_dict(m["conditions_schema_ref"])
            ),
            level=m["level"],
            supports_export=m["supports_export"],
            supports_apply=m["supports_apply"],
        )


@dataclass(frozen=True)
class ObservableSpec:
    """Physical observable definition (quantity, unit, measure, location, basis, representation)."""

    id: str
    quantity_kind: str
    unit: str
    measure: str
    location: str = "material_point"
    basis: str = "global"
    shear_convention: str = "unknown"
    component_order: tuple[str, ...] = ()
    value_kind: str = "real"
    representation: str = "scalar"
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.id, "ObservableSpec.id")
        _require_nonempty_str(self.quantity_kind, "ObservableSpec.quantity_kind")
        validate_unit_string(self.unit, allow_empty=True)
        _require_nonempty_str(self.measure, "ObservableSpec.measure")
        _require_nonempty_str(self.location, "ObservableSpec.location")
        _require_nonempty_str(self.basis, "ObservableSpec.basis")
        _require_nonempty_str(self.shear_convention, "ObservableSpec.shear_convention")
        comp_order = _require_str_tuple(self.component_order, "ObservableSpec.component_order")
        if len(set(comp_order)) != len(comp_order):
            raise ValueError("ObservableSpec.component_order must not contain duplicates")
        object.__setattr__(self, "component_order", comp_order)
        _validate_value_kind_representation(
            self.value_kind,
            self.representation,
            comp_order,
            context="ObservableSpec",
            require_nonempty_components=False,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "quantity_kind": self.quantity_kind,
            "unit": self.unit,
            "measure": self.measure,
            "location": self.location,
            "basis": self.basis,
            "shear_convention": self.shear_convention,
            "component_order": list(self.component_order),
            "value_kind": self.value_kind,
            "representation": self.representation,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ObservableSpec:
        required = {
            "schema_version",
            "id",
            "quantity_kind",
            "unit",
            "measure",
            "location",
            "basis",
            "shear_convention",
            "component_order",
            "value_kind",
            "representation",
        }
        m = _require_exact_keys(data, required, context="ObservableSpec")
        _require_schema_version_v1(m["schema_version"])
        return cls(
            schema_version=1,
            id=m["id"],
            quantity_kind=m["quantity_kind"],
            unit=m["unit"],
            measure=m["measure"],
            location=m["location"],
            basis=m["basis"],
            shear_convention=m["shear_convention"],
            component_order=m["component_order"],
            value_kind=m["value_kind"],
            representation=m["representation"],
        )


@dataclass(frozen=True)
class ExperimentChannelSpec:
    """Binding between a source experiment CSV column/axis and a physical ObservableSpec."""

    observable_id: str
    observable: ObservableSpec
    column_name: str
    source_unit: str
    independent_column: str
    independent_unit: str
    control_mode: str = ""
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.observable_id, "ExperimentChannelSpec.observable_id")
        if not isinstance(self.observable, ObservableSpec):
            raise ValueError("ExperimentChannelSpec.observable must be an ObservableSpec")
        if self.observable_id != self.observable.id:
            raise ValueError("ExperimentChannelSpec.observable_id must match observable.id")
        _require_nonempty_str(self.column_name, "ExperimentChannelSpec.column_name")
        validate_unit_string(self.source_unit, allow_empty=True)
        _require_nonempty_str(self.independent_column, "ExperimentChannelSpec.independent_column")
        validate_unit_string(self.independent_unit, allow_empty=True)
        if not isinstance(self.control_mode, str) or "\x00" in self.control_mode:
            raise ValueError("ExperimentChannelSpec.control_mode must be a valid string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "observable_id": self.observable_id,
            "observable": self.observable.to_dict(),
            "column_name": self.column_name,
            "source_unit": self.source_unit,
            "independent_column": self.independent_column,
            "independent_unit": self.independent_unit,
            "control_mode": self.control_mode,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExperimentChannelSpec:
        required = {
            "schema_version",
            "observable_id",
            "observable",
            "column_name",
            "source_unit",
            "independent_column",
            "independent_unit",
            "control_mode",
        }
        m = _require_exact_keys(data, required, context="ExperimentChannelSpec")
        _require_schema_version_v1(m["schema_version"])
        return cls(
            schema_version=1,
            observable_id=m["observable_id"],
            observable=ObservableSpec.from_dict(m["observable"]),
            column_name=m["column_name"],
            source_unit=m["source_unit"],
            independent_column=m["independent_column"],
            independent_unit=m["independent_unit"],
            control_mode=m["control_mode"],
        )


@dataclass(frozen=True)
class ExperimentSpec:
    """Immutable experiment specification including split, source artifact, channels, and conditions."""

    experiment_id: str
    experiment_revision: str
    split: str
    source_ref: ArtifactRefV1
    channels: tuple[ExperimentChannelSpec, ...]
    replicate_group: str = ""
    conditions: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    sequence: tuple[Mapping[str, Any], ...] = ()
    source_kind: str = "experiment"
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.experiment_id, "ExperimentSpec.experiment_id")
        _require_nonempty_str(self.experiment_revision, "ExperimentSpec.experiment_revision")
        if self.split not in _SPLITS:
            raise ValueError(f"ExperimentSpec.split must be one of {sorted(_SPLITS)}, got {self.split!r}")
        if not isinstance(self.source_ref, ArtifactRefV1):
            raise ValueError("ExperimentSpec.source_ref must be an ArtifactRefV1")

        channels = _coerce_typed_tuple(self.channels, ExperimentChannelSpec, "ExperimentSpec.channels")
        if len({ch.observable_id for ch in channels}) != len(channels):
            raise ValueError("ExperimentSpec.channels contains duplicate observable_id")
        object.__setattr__(self, "channels", channels)

        if not isinstance(self.replicate_group, str) or "\x00" in self.replicate_group:
            raise ValueError("ExperimentSpec.replicate_group must be a valid string")
        object.__setattr__(self, "conditions", _freeze_mapping(self.conditions, context="ExperimentSpec.conditions"))

        if isinstance(self.sequence, (str, bytes, Mapping)) or not isinstance(self.sequence, Sequence):
            raise ValueError("ExperimentSpec.sequence must be a sequence of mappings")
        object.__setattr__(
            self,
            "sequence",
            tuple(_freeze_mapping(seg, context=f"ExperimentSpec.sequence[{idx}]") for idx, seg in enumerate(self.sequence)),
        )
        _require_nonempty_str(self.source_kind, "ExperimentSpec.source_kind")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "experiment_revision": self.experiment_revision,
            "split": self.split,
            "source_ref": self.source_ref.to_dict(),
            "channels": [ch.to_dict() for ch in self.channels],
            "replicate_group": self.replicate_group,
            "conditions": _to_plain_json(self.conditions),
            "sequence": [_to_plain_json(seg) for seg in self.sequence],
            "source_kind": self.source_kind,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExperimentSpec:
        required = {
            "schema_version",
            "experiment_id",
            "experiment_revision",
            "split",
            "source_ref",
            "channels",
            "replicate_group",
            "conditions",
            "sequence",
            "source_kind",
        }
        m = _require_exact_keys(data, required, context="ExperimentSpec")
        _require_schema_version_v1(m["schema_version"])
        if isinstance(m["channels"], (str, bytes, Mapping)) or not isinstance(m["channels"], Sequence):
            raise ValueError("ExperimentSpec.channels must be a list")
        return cls(
            schema_version=1,
            experiment_id=m["experiment_id"],
            experiment_revision=m["experiment_revision"],
            split=m["split"],
            source_ref=ArtifactRefV1.from_dict(m["source_ref"]),
            channels=tuple(ExperimentChannelSpec.from_dict(ch) for ch in m["channels"]),
            replicate_group=m["replicate_group"],
            conditions=m["conditions"],
            sequence=m["sequence"],
            source_kind=m["source_kind"],
        )


@dataclass(frozen=True)
class ComparisonSpec:
    """Comparison channel specification enforcing train/validation/holdout participation rules (C4)."""

    id: str
    experiment_id: str
    observable_id: str
    split: str
    scale: float
    weight: float = 1.0
    required_for_fit: bool = False
    required_for_validation: bool = False
    acceptance_limit: float | None = None
    sample_weights_policy: str = "uniform"
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.id, "ComparisonSpec.id")
        _require_nonempty_str(self.experiment_id, "ComparisonSpec.experiment_id")
        _require_nonempty_str(self.observable_id, "ComparisonSpec.observable_id")
        if self.split not in _SPLITS:
            raise ValueError(f"ComparisonSpec.split must be one of {sorted(_SPLITS)}, got {self.split!r}")

        scale = _require_finite_float(self.scale, "ComparisonSpec.scale")
        if scale <= 0.0:
            raise ValueError("ComparisonSpec.scale must be > 0")
        object.__setattr__(self, "scale", scale)

        weight = _require_finite_float(self.weight, "ComparisonSpec.weight")
        if weight < 0.0:
            raise ValueError("ComparisonSpec.weight must be >= 0")
        object.__setattr__(self, "weight", weight)

        _require_bool(self.required_for_fit, "ComparisonSpec.required_for_fit")
        _require_bool(self.required_for_validation, "ComparisonSpec.required_for_validation")
        if self.required_for_fit and self.required_for_validation:
            raise ValueError("ComparisonSpec cannot set both required_for_fit=True and required_for_validation=True")
        if self.required_for_fit and self.split != "train":
            raise ValueError("ComparisonSpec with required_for_fit=True is only allowed on split='train'")
        if self.required_for_validation and self.split not in ("validation", "holdout"):
            raise ValueError(
                "ComparisonSpec with required_for_validation=True is only allowed on split in ('validation', 'holdout')"
            )

        if self.acceptance_limit is not None:
            limit = _require_finite_float(self.acceptance_limit, "ComparisonSpec.acceptance_limit")
            if limit < 0.0:
                raise ValueError("ComparisonSpec.acceptance_limit must be >= 0")
            object.__setattr__(self, "acceptance_limit", limit)

        _require_nonempty_str(self.sample_weights_policy, "ComparisonSpec.sample_weights_policy")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "experiment_id": self.experiment_id,
            "observable_id": self.observable_id,
            "split": self.split,
            "scale": self.scale,
            "weight": self.weight,
            "required_for_fit": self.required_for_fit,
            "required_for_validation": self.required_for_validation,
            "acceptance_limit": self.acceptance_limit,
            "sample_weights_policy": self.sample_weights_policy,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ComparisonSpec:
        required = {
            "schema_version",
            "id",
            "experiment_id",
            "observable_id",
            "split",
            "scale",
            "weight",
            "required_for_fit",
            "required_for_validation",
            "acceptance_limit",
            "sample_weights_policy",
        }
        m = _require_exact_keys(data, required, context="ComparisonSpec")
        _require_schema_version_v1(m["schema_version"])
        return cls(
            schema_version=1,
            id=m["id"],
            experiment_id=m["experiment_id"],
            observable_id=m["observable_id"],
            split=m["split"],
            scale=m["scale"],
            weight=m["weight"],
            required_for_fit=m["required_for_fit"],
            required_for_validation=m["required_for_validation"],
            acceptance_limit=m["acceptance_limit"],
            sample_weights_policy=m["sample_weights_policy"],
        )


@dataclass(frozen=True)
class PredictedChannelV1:
    """Predicted observable channel inside an ExperimentPredictionV1 (C3)."""

    observable_id: str
    observable: ObservableSpec
    independent_ref: ArtifactRefV1
    sample_keys_ref: ArtifactRefV1
    sample_key_columns: tuple[str, ...]
    values_ref: ArtifactRefV1
    mask_ref: ArtifactRefV1
    component_order: tuple[str, ...]
    value_kind: str
    representation: str
    coverage: Mapping[str, Any]
    source_refs: tuple[ArtifactRefV1, ...] = ()
    source_result_refs: tuple[ResultRefV1, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.observable_id, "PredictedChannelV1.observable_id")
        if not isinstance(self.observable, ObservableSpec):
            raise ValueError("PredictedChannelV1.observable must be an ObservableSpec")
        if self.observable_id != self.observable.id:
            raise ValueError("PredictedChannelV1.observable_id must match observable.id")

        for ref_name in ("independent_ref", "sample_keys_ref", "values_ref", "mask_ref"):
            if not isinstance(getattr(self, ref_name), ArtifactRefV1):
                raise ValueError(f"PredictedChannelV1.{ref_name} must be an ArtifactRefV1")

        sample_key_cols = _require_str_tuple(self.sample_key_columns, "PredictedChannelV1.sample_key_columns")
        if not sample_key_cols:
            raise ValueError("PredictedChannelV1.sample_key_columns must be non-empty")
        object.__setattr__(self, "sample_key_columns", sample_key_cols)

        comp_order = _require_str_tuple(self.component_order, "PredictedChannelV1.component_order")
        if len(set(comp_order)) != len(comp_order):
            raise ValueError("PredictedChannelV1.component_order must not contain duplicates")
        if self.observable.component_order and comp_order != self.observable.component_order:
            raise ValueError("PredictedChannelV1.component_order must match observable.component_order")
        object.__setattr__(self, "component_order", comp_order)

        _validate_value_kind_representation(
            self.value_kind,
            self.representation,
            comp_order,
            context="PredictedChannelV1",
            require_nonempty_components=True,
        )
        if self.value_kind != self.observable.value_kind:
            raise ValueError("PredictedChannelV1.value_kind must match observable.value_kind")
        if self.representation != self.observable.representation:
            raise ValueError("PredictedChannelV1.representation must match observable.representation")

        object.__setattr__(self, "coverage", _freeze_mapping(self.coverage, context="PredictedChannelV1.coverage"))
        object.__setattr__(
            self, "source_refs", _coerce_typed_tuple(self.source_refs, ArtifactRefV1, "PredictedChannelV1.source_refs")
        )
        object.__setattr__(
            self,
            "source_result_refs",
            _coerce_typed_tuple(self.source_result_refs, ResultRefV1, "PredictedChannelV1.source_result_refs"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "observable_id": self.observable_id,
            "observable": self.observable.to_dict(),
            "independent_ref": self.independent_ref.to_dict(),
            "sample_keys_ref": self.sample_keys_ref.to_dict(),
            "sample_key_columns": list(self.sample_key_columns),
            "values_ref": self.values_ref.to_dict(),
            "mask_ref": self.mask_ref.to_dict(),
            "component_order": list(self.component_order),
            "value_kind": self.value_kind,
            "representation": self.representation,
            "coverage": _to_plain_json(self.coverage),
            "source_refs": [item.to_dict() for item in self.source_refs],
            "source_result_refs": [item.to_dict() for item in self.source_result_refs],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PredictedChannelV1:
        required = {
            "schema_version",
            "observable_id",
            "observable",
            "independent_ref",
            "sample_keys_ref",
            "sample_key_columns",
            "values_ref",
            "mask_ref",
            "component_order",
            "value_kind",
            "representation",
            "coverage",
            "source_refs",
            "source_result_refs",
        }
        m = _require_exact_keys(data, required, context="PredictedChannelV1")
        _require_schema_version_v1(m["schema_version"])
        for seq_key in ("source_refs", "source_result_refs"):
            if isinstance(m[seq_key], (str, bytes, Mapping)) or not isinstance(m[seq_key], Sequence):
                raise ValueError(f"PredictedChannelV1.{seq_key} must be a list")
        return cls(
            schema_version=1,
            observable_id=m["observable_id"],
            observable=ObservableSpec.from_dict(m["observable"]),
            independent_ref=ArtifactRefV1.from_dict(m["independent_ref"]),
            sample_keys_ref=ArtifactRefV1.from_dict(m["sample_keys_ref"]),
            sample_key_columns=m["sample_key_columns"],
            values_ref=ArtifactRefV1.from_dict(m["values_ref"]),
            mask_ref=ArtifactRefV1.from_dict(m["mask_ref"]),
            component_order=m["component_order"],
            value_kind=m["value_kind"],
            representation=m["representation"],
            coverage=m["coverage"],
            source_refs=tuple(ArtifactRefV1.from_dict(item) for item in m["source_refs"]),
            source_result_refs=tuple(ResultRefV1.from_dict(item) for item in m["source_result_refs"]),
        )


@dataclass(frozen=True)
class ExperimentPredictionV1:
    """Adapter prediction channels and provenance for a single experiment (C3)."""

    experiment_id: str
    experiment_revision: str
    state: str
    initial_state_ref: ArtifactRefV1 | None = None
    channels: tuple[PredictedChannelV1, ...] = ()
    execution_refs: tuple[ArtifactRefV1, ...] = ()
    diagnostics: tuple[Mapping[str, Any] | str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.experiment_id, "ExperimentPredictionV1.experiment_id")
        _require_nonempty_str(self.experiment_revision, "ExperimentPredictionV1.experiment_revision")
        _require_nonempty_str(self.state, "ExperimentPredictionV1.state")
        if self.initial_state_ref is not None and not isinstance(self.initial_state_ref, ArtifactRefV1):
            raise ValueError("ExperimentPredictionV1.initial_state_ref must be None or ArtifactRefV1")

        channels = _coerce_typed_tuple(self.channels, PredictedChannelV1, "ExperimentPredictionV1.channels")
        if len({ch.observable_id for ch in channels}) != len(channels):
            raise ValueError("ExperimentPredictionV1.channels contains duplicate observable_id")
        object.__setattr__(self, "channels", channels)

        object.__setattr__(
            self,
            "execution_refs",
            _coerce_typed_tuple(self.execution_refs, ArtifactRefV1, "ExperimentPredictionV1.execution_refs"),
        )
        object.__setattr__(self, "diagnostics", _coerce_diagnostics(self.diagnostics, "ExperimentPredictionV1.diagnostics"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "experiment_revision": self.experiment_revision,
            "state": self.state,
            "initial_state_ref": None if self.initial_state_ref is None else self.initial_state_ref.to_dict(),
            "channels": [ch.to_dict() for ch in self.channels],
            "execution_refs": [item.to_dict() for item in self.execution_refs],
            "diagnostics": _to_plain_json(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExperimentPredictionV1:
        required = {
            "schema_version",
            "experiment_id",
            "experiment_revision",
            "state",
            "initial_state_ref",
            "channels",
            "execution_refs",
            "diagnostics",
        }
        m = _require_exact_keys(data, required, context="ExperimentPredictionV1")
        _require_schema_version_v1(m["schema_version"])
        for seq_key in ("channels", "execution_refs"):
            if isinstance(m[seq_key], (str, bytes, Mapping)) or not isinstance(m[seq_key], Sequence):
                raise ValueError(f"ExperimentPredictionV1.{seq_key} must be a list")
        return cls(
            schema_version=1,
            experiment_id=m["experiment_id"],
            experiment_revision=m["experiment_revision"],
            state=m["state"],
            initial_state_ref=(
                None if m["initial_state_ref"] is None else ArtifactRefV1.from_dict(m["initial_state_ref"])
            ),
            channels=tuple(PredictedChannelV1.from_dict(ch) for ch in m["channels"]),
            execution_refs=tuple(ArtifactRefV1.from_dict(item) for item in m["execution_refs"]),
            diagnostics=m["diagnostics"],
        )


@dataclass(frozen=True)
class PredictionBundleV1:
    """Complete immutable PredictionBundle collected from MaterialAdapter.collect (C3)."""

    evaluation_id: str
    request_id: str
    study_revision: str
    model_revision: str
    adapter: AdapterIdentity
    experiments: tuple[ExperimentPredictionV1, ...] = ()
    optimizer_scalars: Mapping[str, float | None] = field(default_factory=lambda: MappingProxyType({}))
    artifacts: tuple[ArtifactRefV1, ...] = ()
    artifact_owners: Mapping[str, ArtifactOwner] = field(default_factory=lambda: MappingProxyType({}))
    run_ids: tuple[str, ...] = ()
    diagnostics: tuple[Mapping[str, Any] | str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.evaluation_id, "PredictionBundleV1.evaluation_id")
        _require_nonempty_str(self.request_id, "PredictionBundleV1.request_id")
        _require_nonempty_str(self.study_revision, "PredictionBundleV1.study_revision")
        _require_nonempty_str(self.model_revision, "PredictionBundleV1.model_revision")
        if not isinstance(self.adapter, AdapterIdentity):
            raise ValueError("PredictionBundleV1.adapter must be an AdapterIdentity")

        experiments = _coerce_typed_tuple(self.experiments, ExperimentPredictionV1, "PredictionBundleV1.experiments")
        if len({exp.experiment_id for exp in experiments}) != len(experiments):
            raise ValueError("PredictionBundleV1.experiments contains duplicate experiment_id")
        object.__setattr__(self, "experiments", experiments)

        if not isinstance(self.optimizer_scalars, Mapping):
            raise ValueError("PredictionBundleV1.optimizer_scalars must be a mapping")
        scalars: dict[str, float | None] = {}
        for key, value in self.optimizer_scalars.items():
            _require_nonempty_str(key, "PredictionBundleV1.optimizer_scalars key")
            if key == "fit_loss":
                raise ValueError("Adapters must not supply reserved host key 'fit_loss' in optimizer_scalars")
            scalars[key] = None if value is None else _require_finite_float(value, f"optimizer_scalars[{key!r}]")
        object.__setattr__(self, "optimizer_scalars", MappingProxyType(scalars))

        object.__setattr__(
            self, "artifacts", _coerce_typed_tuple(self.artifacts, ArtifactRefV1, "PredictionBundleV1.artifacts")
        )

        if not isinstance(self.artifact_owners, Mapping):
            raise ValueError("PredictionBundleV1.artifact_owners must be a mapping")
        owners: dict[str, ArtifactOwner] = {}
        for aid, owner in self.artifact_owners.items():
            _require_nonempty_str(aid, "PredictionBundleV1.artifact_owners key")
            owners[aid] = owner if isinstance(owner, ArtifactOwner) else ArtifactOwner.from_dict(owner)
        object.__setattr__(self, "artifact_owners", MappingProxyType(owners))

        object.__setattr__(self, "run_ids", _require_str_tuple(self.run_ids, "PredictionBundleV1.run_ids"))
        object.__setattr__(self, "diagnostics", _coerce_diagnostics(self.diagnostics, "PredictionBundleV1.diagnostics"))

    def validate_level(self, level: str) -> None:
        """Enforce C3 level invariants: no source_result_refs for analytic/material_library, no run_ids for analytic."""
        if level not in _LEVELS:
            raise ValueError(f"Unknown adapter level {level!r}; expected one of {sorted(_LEVELS)}")
        if level in ("analytic", "material_library"):
            for exp in self.experiments:
                for ch in exp.channels:
                    if ch.source_result_refs:
                        raise ValueError(
                            f"PredictionBundleV1 at level={level!r} must have empty source_result_refs "
                            f"(found on {exp.experiment_id}/{ch.observable_id})"
                        )
        if level == "analytic" and self.run_ids:
            raise ValueError("PredictionBundleV1 at level='analytic' must have empty run_ids")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evaluation_id": self.evaluation_id,
            "request_id": self.request_id,
            "study_revision": self.study_revision,
            "model_revision": self.model_revision,
            "adapter": self.adapter.to_dict(),
            "experiments": [exp.to_dict() for exp in self.experiments],
            "optimizer_scalars": dict(self.optimizer_scalars),
            "artifacts": [item.to_dict() for item in self.artifacts],
            "artifact_owners": {aid: owner.to_dict() for aid, owner in self.artifact_owners.items()},
            "run_ids": list(self.run_ids),
            "diagnostics": _to_plain_json(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PredictionBundleV1:
        required = {
            "schema_version",
            "evaluation_id",
            "request_id",
            "study_revision",
            "model_revision",
            "adapter",
            "experiments",
            "optimizer_scalars",
            "artifacts",
            "artifact_owners",
            "run_ids",
            "diagnostics",
        }
        m = _require_exact_keys(data, required, context="PredictionBundleV1")
        _require_schema_version_v1(m["schema_version"])
        for seq_key in ("experiments", "artifacts"):
            if isinstance(m[seq_key], (str, bytes, Mapping)) or not isinstance(m[seq_key], Sequence):
                raise ValueError(f"PredictionBundleV1.{seq_key} must be a list")
        return cls(
            schema_version=1,
            evaluation_id=m["evaluation_id"],
            request_id=m["request_id"],
            study_revision=m["study_revision"],
            model_revision=m["model_revision"],
            adapter=AdapterIdentity.from_dict(m["adapter"]),
            experiments=tuple(ExperimentPredictionV1.from_dict(exp) for exp in m["experiments"]),
            optimizer_scalars=m["optimizer_scalars"],
            artifacts=tuple(ArtifactRefV1.from_dict(item) for item in m["artifacts"]),
            artifact_owners=m["artifact_owners"],
            run_ids=m["run_ids"],
            diagnostics=m["diagnostics"],
        )


PredictionBundle = PredictionBundleV1


def _validate_objective_block(objective: Any) -> MappingProxyType[str, Any]:
    if isinstance(objective, Mapping) and "train_fit" in objective:
        raise ValueError("Forbidden legacy alias 'train_fit' in AssessmentResultV1.objective")
    m = _require_exact_keys(
        objective,
        {"eligible", "fit_loss", "reason_codes"},
        context="AssessmentResultV1.objective",
    )
    eligible = _require_bool(m["eligible"], "objective.eligible")
    fit_loss = None if m["fit_loss"] is None else _require_finite_float(m["fit_loss"], "objective.fit_loss")
    if not eligible and fit_loss is not None:
        raise ValueError("AssessmentResultV1.objective with eligible=False must have fit_loss=None")
    if eligible and fit_loss is None:
        raise ValueError("AssessmentResultV1.objective with eligible=True must have finite fit_loss")
    reason_codes = _require_str_tuple(m["reason_codes"], "objective.reason_codes")
    return MappingProxyType(
        {
            "eligible": eligible,
            "fit_loss": fit_loss,
            "reason_codes": reason_codes,
        }
    )


def _validate_assessment_block(assessment: Any) -> MappingProxyType[str, Any]:
    if isinstance(assessment, Mapping) and "train_fit" in assessment:
        raise ValueError("Forbidden legacy alias 'train_fit' in AssessmentResultV1.assessment")
    required = {
        "fit_status",
        "validation_status",
        "holdout_status",
        "adequacy_status",
        "uncertainty_status",
        "identifiability_status",
        "constraint_status",
        "constraint_checks",
        "policy_ref",
        "channel_checks",
        "split_checks",
        "warnings",
    }
    m = _require_exact_keys(assessment, required, context="AssessmentResultV1.assessment")
    for status_key in ("fit_status", "validation_status", "holdout_status", "adequacy_status"):
        if m[status_key] not in _PASS_FAIL_NA:
            raise ValueError(
                f"assessment.{status_key} must be one of {sorted(_PASS_FAIL_NA)}, got {m[status_key]!r}"
            )
    if m["uncertainty_status"] not in _UNCERTAINTY_STATUSES:
        raise ValueError(
            f"assessment.uncertainty_status must be one of {sorted(_UNCERTAINTY_STATUSES)}, got {m['uncertainty_status']!r}"
        )
    if m["identifiability_status"] not in _IDENTIFIABILITY_STATUSES:
        raise ValueError(
            f"assessment.identifiability_status must be one of {sorted(_IDENTIFIABILITY_STATUSES)}, got {m['identifiability_status']!r}"
        )
    if m["constraint_status"] not in _CONSTRAINT_STATUSES:
        raise ValueError(
            f"assessment.constraint_status must be one of {sorted(_CONSTRAINT_STATUSES)}, got {m['constraint_status']!r}"
        )

    raw_policy = m["policy_ref"]
    if raw_policy is None:
        policy_ref: Any = None
    elif isinstance(raw_policy, ArtifactRefV1):
        policy_ref = raw_policy
    elif isinstance(raw_policy, str):
        policy_ref = _require_nonempty_str(raw_policy, "assessment.policy_ref")
    elif isinstance(raw_policy, Mapping):
        if {"id", "role", "path", "sha256", "size", "format_id", "producer"}.issubset(raw_policy.keys()):
            policy_ref = ArtifactRefV1.from_dict(raw_policy)
        else:
            policy_ref = _freeze_mapping(raw_policy, context="assessment.policy_ref")
    else:
        raise ValueError("assessment.policy_ref must be None, ArtifactRefV1, nonempty str, or mapping")

    frozen_checks: dict[str, tuple[Any, ...]] = {}
    for check_key in ("constraint_checks", "channel_checks", "split_checks"):
        raw_list = m[check_key]
        if isinstance(raw_list, (str, bytes, Mapping)) or not isinstance(raw_list, Sequence):
            raise ValueError(f"assessment.{check_key} must be a sequence")
        frozen_checks[check_key] = _freeze_json_value(list(raw_list), context=f"assessment.{check_key}")

    warnings = _require_str_tuple(m["warnings"], "assessment.warnings")
    return MappingProxyType(
        {
            "fit_status": m["fit_status"],
            "validation_status": m["validation_status"],
            "holdout_status": m["holdout_status"],
            "adequacy_status": m["adequacy_status"],
            "uncertainty_status": m["uncertainty_status"],
            "identifiability_status": m["identifiability_status"],
            "constraint_status": m["constraint_status"],
            "constraint_checks": frozen_checks["constraint_checks"],
            "policy_ref": policy_ref,
            "channel_checks": frozen_checks["channel_checks"],
            "split_checks": frozen_checks["split_checks"],
            "warnings": warnings,
        }
    )


@dataclass(frozen=True)
class AssessmentResultV1:
    """Canonical C4 assessment serialization uniting objective eligibility and split/adequacy status."""

    objective: Mapping[str, Any]
    assessment: Mapping[str, Any]
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        object.__setattr__(self, "objective", _validate_objective_block(self.objective))
        object.__setattr__(self, "assessment", _validate_assessment_block(self.assessment))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "objective": _to_plain_json(self.objective),
            "assessment": _to_plain_json(self.assessment),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AssessmentResultV1:
        if isinstance(data, Mapping) and "train_fit" in data:
            raise ValueError("Forbidden legacy alias 'train_fit' in AssessmentResultV1")
        m = _require_exact_keys(data, {"schema_version", "objective", "assessment"}, context="AssessmentResultV1")
        _require_schema_version_v1(m["schema_version"])
        return cls(schema_version=1, objective=m["objective"], assessment=m["assessment"])


def _freeze_experiment_revisions(values: Any, name: str) -> tuple[MappingProxyType[str, str], ...]:
    if isinstance(values, (str, bytes, Mapping)) or not isinstance(values, Sequence):
        raise ValueError(f"{name} must be a sequence of {{experiment_id, experiment_revision}} mappings")
    out: list[MappingProxyType[str, str]] = []
    for idx, item in enumerate(values):
        m = _require_exact_keys(item, {"experiment_id", "experiment_revision"}, context=f"{name}[{idx}]")
        out.append(
            MappingProxyType(
                {
                    "experiment_id": _require_nonempty_str(m["experiment_id"], f"{name}[{idx}].experiment_id"),
                    "experiment_revision": _require_nonempty_str(
                        m["experiment_revision"], f"{name}[{idx}].experiment_revision"
                    ),
                }
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class SelectionRecordV1:
    """Frozen candidate selection record bound before confirmation/holdout exposure (C4)."""

    selection_id: str
    study_id: str
    content_revision: str
    model_revision: str
    adapter: AdapterIdentity
    physical_parameters: Mapping[str, float | str]
    parameter_values_sha256: str
    numerical_settings_sha256: str
    normalization_ref: ArtifactRefV1
    assessment_policy_ref: ArtifactRefV1
    selection_policy_ref: ArtifactRefV1
    source_evaluation_ids: tuple[str, ...] = ()
    training_experiment_revisions: tuple[Mapping[str, str], ...] = ()
    validation_experiment_revisions: tuple[Mapping[str, str], ...] = ()
    holdout_experiment_revisions: tuple[Mapping[str, str], ...] = ()
    created_at: str = ""
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.selection_id, "SelectionRecordV1.selection_id")
        _require_nonempty_str(self.study_id, "SelectionRecordV1.study_id")
        _require_nonempty_str(self.content_revision, "SelectionRecordV1.content_revision")
        _require_nonempty_str(self.model_revision, "SelectionRecordV1.model_revision")
        if not isinstance(self.adapter, AdapterIdentity):
            raise ValueError("SelectionRecordV1.adapter must be an AdapterIdentity")

        if not isinstance(self.physical_parameters, Mapping):
            raise ValueError("SelectionRecordV1.physical_parameters must be a mapping")
        params: dict[str, float | str] = {}
        for k, v in self.physical_parameters.items():
            _require_nonempty_str(k, "SelectionRecordV1.physical_parameters key")
            if isinstance(v, str):
                params[k] = _require_nonempty_str(v, f"physical_parameters[{k!r}]")
            else:
                params[k] = _require_finite_float(v, f"physical_parameters[{k!r}]")
        object.__setattr__(self, "physical_parameters", MappingProxyType(params))

        _require_sha256(self.parameter_values_sha256, "SelectionRecordV1.parameter_values_sha256")
        _require_sha256(self.numerical_settings_sha256, "SelectionRecordV1.numerical_settings_sha256")
        for ref_field in ("normalization_ref", "assessment_policy_ref", "selection_policy_ref"):
            if not isinstance(getattr(self, ref_field), ArtifactRefV1):
                raise ValueError(f"SelectionRecordV1.{ref_field} must be an ArtifactRefV1")

        object.__setattr__(
            self,
            "source_evaluation_ids",
            _require_str_tuple(self.source_evaluation_ids, "SelectionRecordV1.source_evaluation_ids"),
        )
        object.__setattr__(
            self,
            "training_experiment_revisions",
            _freeze_experiment_revisions(
                self.training_experiment_revisions, "SelectionRecordV1.training_experiment_revisions"
            ),
        )
        object.__setattr__(
            self,
            "validation_experiment_revisions",
            _freeze_experiment_revisions(
                self.validation_experiment_revisions, "SelectionRecordV1.validation_experiment_revisions"
            ),
        )
        object.__setattr__(
            self,
            "holdout_experiment_revisions",
            _freeze_experiment_revisions(
                self.holdout_experiment_revisions, "SelectionRecordV1.holdout_experiment_revisions"
            ),
        )
        _require_nonempty_str(self.created_at, "SelectionRecordV1.created_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "selection_id": self.selection_id,
            "study_id": self.study_id,
            "content_revision": self.content_revision,
            "model_revision": self.model_revision,
            "adapter": self.adapter.to_dict(),
            "physical_parameters": dict(self.physical_parameters),
            "parameter_values_sha256": self.parameter_values_sha256,
            "numerical_settings_sha256": self.numerical_settings_sha256,
            "normalization_ref": self.normalization_ref.to_dict(),
            "assessment_policy_ref": self.assessment_policy_ref.to_dict(),
            "selection_policy_ref": self.selection_policy_ref.to_dict(),
            "source_evaluation_ids": list(self.source_evaluation_ids),
            "training_experiment_revisions": _to_plain_json(self.training_experiment_revisions),
            "validation_experiment_revisions": _to_plain_json(self.validation_experiment_revisions),
            "holdout_experiment_revisions": _to_plain_json(self.holdout_experiment_revisions),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SelectionRecordV1:
        required = {
            "schema_version",
            "selection_id",
            "study_id",
            "content_revision",
            "model_revision",
            "adapter",
            "physical_parameters",
            "parameter_values_sha256",
            "numerical_settings_sha256",
            "normalization_ref",
            "assessment_policy_ref",
            "selection_policy_ref",
            "source_evaluation_ids",
            "training_experiment_revisions",
            "validation_experiment_revisions",
            "holdout_experiment_revisions",
            "created_at",
        }
        m = _require_exact_keys(data, required, context="SelectionRecordV1")
        _require_schema_version_v1(m["schema_version"])
        return cls(
            schema_version=1,
            selection_id=m["selection_id"],
            study_id=m["study_id"],
            content_revision=m["content_revision"],
            model_revision=m["model_revision"],
            adapter=AdapterIdentity.from_dict(m["adapter"]),
            physical_parameters=m["physical_parameters"],
            parameter_values_sha256=m["parameter_values_sha256"],
            numerical_settings_sha256=m["numerical_settings_sha256"],
            normalization_ref=ArtifactRefV1.from_dict(m["normalization_ref"]),
            assessment_policy_ref=ArtifactRefV1.from_dict(m["assessment_policy_ref"]),
            selection_policy_ref=ArtifactRefV1.from_dict(m["selection_policy_ref"]),
            source_evaluation_ids=m["source_evaluation_ids"],
            training_experiment_revisions=m["training_experiment_revisions"],
            validation_experiment_revisions=m["validation_experiment_revisions"],
            holdout_experiment_revisions=m["holdout_experiment_revisions"],
            created_at=m["created_at"],
        )


@dataclass(frozen=True)
class StudySnapshot:
    """Immutable snapshot of current CalibrationStudyService state with explicit document/content revisions (C2/C7)."""

    study_id: str
    document_revision: str
    content_revision: str
    path: str
    data_root: str
    read_only: bool
    busy_operation: str | None = None
    active_campaign_id: str | None = None
    active_operation_id: str | None = None
    experiment_ids: tuple[str, ...] = ()
    point_ids: tuple[str, ...] = ()
    diagnostics: tuple[Mapping[str, Any] | str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.study_id, "StudySnapshot.study_id")
        _require_nonempty_str(self.document_revision, "StudySnapshot.document_revision")
        _require_nonempty_str(self.content_revision, "StudySnapshot.content_revision")
        _require_nonempty_str(self.path, "StudySnapshot.path")
        _require_nonempty_str(self.data_root, "StudySnapshot.data_root")
        _require_bool(self.read_only, "StudySnapshot.read_only")
        _require_optional_nonempty_str(self.busy_operation, "StudySnapshot.busy_operation")
        _require_optional_nonempty_str(self.active_campaign_id, "StudySnapshot.active_campaign_id")
        _require_optional_nonempty_str(self.active_operation_id, "StudySnapshot.active_operation_id")
        object.__setattr__(self, "experiment_ids", _require_str_tuple(self.experiment_ids, "StudySnapshot.experiment_ids"))
        object.__setattr__(self, "point_ids", _require_str_tuple(self.point_ids, "StudySnapshot.point_ids"))
        object.__setattr__(self, "diagnostics", _coerce_diagnostics(self.diagnostics, "StudySnapshot.diagnostics"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "study_id": self.study_id,
            "document_revision": self.document_revision,
            "content_revision": self.content_revision,
            "path": self.path,
            "data_root": self.data_root,
            "read_only": self.read_only,
            "busy_operation": self.busy_operation,
            "active_campaign_id": self.active_campaign_id,
            "active_operation_id": self.active_operation_id,
            "experiment_ids": list(self.experiment_ids),
            "point_ids": list(self.point_ids),
            "diagnostics": _to_plain_json(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StudySnapshot:
        required = {
            "schema_version",
            "study_id",
            "document_revision",
            "content_revision",
            "path",
            "data_root",
            "read_only",
            "busy_operation",
            "active_campaign_id",
            "active_operation_id",
            "experiment_ids",
            "point_ids",
            "diagnostics",
        }
        m = _require_exact_keys(data, required, context="StudySnapshot")
        _require_schema_version_v1(m["schema_version"])
        return cls(
            schema_version=1,
            study_id=m["study_id"],
            document_revision=m["document_revision"],
            content_revision=m["content_revision"],
            path=m["path"],
            data_root=m["data_root"],
            read_only=m["read_only"],
            busy_operation=m["busy_operation"],
            active_campaign_id=m["active_campaign_id"],
            active_operation_id=m["active_operation_id"],
            experiment_ids=m["experiment_ids"],
            point_ids=m["point_ids"],
            diagnostics=m["diagnostics"],
        )


@dataclass(frozen=True)
class ImportReceipt:
    """Receipt returned by CalibrationStudyService.import_experiment (C2/C7)."""

    study_id: str
    previous_document_revision: str | None
    document_revision: str
    previous_content_revision: str | None
    content_revision: str
    experiment_id: str
    source_ref: ArtifactRefV1
    converted_refs: tuple[ArtifactRefV1, ...] = ()
    warnings: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.study_id, "ImportReceipt.study_id")
        _require_optional_nonempty_str(self.previous_document_revision, "ImportReceipt.previous_document_revision")
        _require_nonempty_str(self.document_revision, "ImportReceipt.document_revision")
        _require_optional_nonempty_str(self.previous_content_revision, "ImportReceipt.previous_content_revision")
        _require_nonempty_str(self.content_revision, "ImportReceipt.content_revision")
        _require_nonempty_str(self.experiment_id, "ImportReceipt.experiment_id")
        if not isinstance(self.source_ref, ArtifactRefV1):
            raise ValueError("ImportReceipt.source_ref must be an ArtifactRefV1")
        object.__setattr__(
            self,
            "converted_refs",
            _coerce_typed_tuple(self.converted_refs, ArtifactRefV1, "ImportReceipt.converted_refs"),
        )
        object.__setattr__(self, "warnings", _require_str_tuple(self.warnings, "ImportReceipt.warnings"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "study_id": self.study_id,
            "previous_document_revision": self.previous_document_revision,
            "document_revision": self.document_revision,
            "previous_content_revision": self.previous_content_revision,
            "content_revision": self.content_revision,
            "experiment_id": self.experiment_id,
            "source_ref": self.source_ref.to_dict(),
            "converted_refs": [item.to_dict() for item in self.converted_refs],
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ImportReceipt:
        required = {
            "schema_version",
            "study_id",
            "previous_document_revision",
            "document_revision",
            "previous_content_revision",
            "content_revision",
            "experiment_id",
            "source_ref",
            "converted_refs",
            "warnings",
        }
        m = _require_exact_keys(data, required, context="ImportReceipt")
        _require_schema_version_v1(m["schema_version"])
        raw_converted = m["converted_refs"]
        if isinstance(raw_converted, (str, bytes, Mapping)) or not isinstance(raw_converted, Sequence):
            raise ValueError("ImportReceipt.converted_refs must be a list")
        return cls(
            schema_version=1,
            study_id=m["study_id"],
            previous_document_revision=m["previous_document_revision"],
            document_revision=m["document_revision"],
            previous_content_revision=m["previous_content_revision"],
            content_revision=m["content_revision"],
            experiment_id=m["experiment_id"],
            source_ref=ArtifactRefV1.from_dict(m["source_ref"]),
            converted_refs=tuple(ArtifactRefV1.from_dict(item) for item in raw_converted),
            warnings=m["warnings"],
        )


@dataclass(frozen=True)
class EvaluationReceipt:
    """Receipt returned by CalibrationStudyService.evaluate (C2/C7)."""

    campaign_id: str | None
    evaluation_id: str
    document_revision: str
    content_revision: str
    state: str
    artifact_ref: ArtifactRefV1 | None = None
    observation: Mapping[str, Any] | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_optional_nonempty_str(self.campaign_id, "EvaluationReceipt.campaign_id")
        _require_nonempty_str(self.evaluation_id, "EvaluationReceipt.evaluation_id")
        _require_nonempty_str(self.document_revision, "EvaluationReceipt.document_revision")
        _require_nonempty_str(self.content_revision, "EvaluationReceipt.content_revision")
        _require_nonempty_str(self.state, "EvaluationReceipt.state")
        if self.artifact_ref is not None and not isinstance(self.artifact_ref, ArtifactRefV1):
            raise ValueError("EvaluationReceipt.artifact_ref must be None or ArtifactRefV1")
        if self.observation is not None:
            object.__setattr__(
                self,
                "observation",
                _coerce_mapping_or_dataclass(self.observation, "EvaluationReceipt.observation"),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "campaign_id": self.campaign_id,
            "evaluation_id": self.evaluation_id,
            "document_revision": self.document_revision,
            "content_revision": self.content_revision,
            "state": self.state,
            "artifact_ref": None if self.artifact_ref is None else self.artifact_ref.to_dict(),
            "observation": None if self.observation is None else _to_plain_json(self.observation),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvaluationReceipt:
        required = {
            "schema_version",
            "campaign_id",
            "evaluation_id",
            "document_revision",
            "content_revision",
            "state",
            "artifact_ref",
            "observation",
        }
        m = _require_exact_keys(data, required, context="EvaluationReceipt")
        _require_schema_version_v1(m["schema_version"])
        return cls(
            schema_version=1,
            campaign_id=m["campaign_id"],
            evaluation_id=m["evaluation_id"],
            document_revision=m["document_revision"],
            content_revision=m["content_revision"],
            state=m["state"],
            artifact_ref=None if m["artifact_ref"] is None else ArtifactRefV1.from_dict(m["artifact_ref"]),
            observation=m["observation"],
        )


@dataclass(frozen=True)
class CampaignReceipt:
    """Receipt returned by CalibrationStudyService.optimize (C2/C7)."""

    campaign_id: str
    content_revision: str
    state: str
    termination_reason: str
    evaluation_ids: tuple[str, ...] = ()
    selected_ids: tuple[str, ...] = ()
    budget_usage: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.campaign_id, "CampaignReceipt.campaign_id")
        _require_nonempty_str(self.content_revision, "CampaignReceipt.content_revision")
        _require_nonempty_str(self.state, "CampaignReceipt.state")
        _require_nonempty_str(self.termination_reason, "CampaignReceipt.termination_reason")
        object.__setattr__(self, "evaluation_ids", _require_str_tuple(self.evaluation_ids, "CampaignReceipt.evaluation_ids"))
        object.__setattr__(self, "selected_ids", _require_str_tuple(self.selected_ids, "CampaignReceipt.selected_ids"))
        object.__setattr__(
            self,
            "budget_usage",
            _coerce_mapping_or_dataclass(self.budget_usage, "CampaignReceipt.budget_usage"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "campaign_id": self.campaign_id,
            "content_revision": self.content_revision,
            "state": self.state,
            "termination_reason": self.termination_reason,
            "evaluation_ids": list(self.evaluation_ids),
            "selected_ids": list(self.selected_ids),
            "budget_usage": _to_plain_json(self.budget_usage),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CampaignReceipt:
        required = {
            "schema_version",
            "campaign_id",
            "content_revision",
            "state",
            "termination_reason",
            "evaluation_ids",
            "selected_ids",
            "budget_usage",
        }
        m = _require_exact_keys(data, required, context="CampaignReceipt")
        _require_schema_version_v1(m["schema_version"])
        return cls(
            schema_version=1,
            campaign_id=m["campaign_id"],
            content_revision=m["content_revision"],
            state=m["state"],
            termination_reason=m["termination_reason"],
            evaluation_ids=m["evaluation_ids"],
            selected_ids=m["selected_ids"],
            budget_usage=m["budget_usage"],
        )


@dataclass(frozen=True)
class CancelReceipt:
    """Receipt returned by CalibrationStudyService.cancel / stop (C2/C7)."""

    campaign_id: str
    accepted: bool
    state: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.campaign_id, "CancelReceipt.campaign_id")
        _require_bool(self.accepted, "CancelReceipt.accepted")
        _require_nonempty_str(self.state, "CancelReceipt.state")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "campaign_id": self.campaign_id,
            "accepted": self.accepted,
            "state": self.state,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CancelReceipt:
        m = _require_exact_keys(data, {"schema_version", "campaign_id", "accepted", "state"}, context="CancelReceipt")
        _require_schema_version_v1(m["schema_version"])
        return cls(
            schema_version=1,
            campaign_id=m["campaign_id"],
            accepted=m["accepted"],
            state=m["state"],
        )
