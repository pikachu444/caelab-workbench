"""CORE02 adapter registry, evaluation/export plans, receipts, and error contracts (no GUI/VTK/solver imports)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
import threading
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from caelab.artifact_refs import (
    ArtifactRefV1,
    _freeze_json_value,
    _freeze_mapping,
    _require_bool,
    _require_exact_keys,
    _require_finite_float,
    _require_int,
    _require_nonempty_str,
    _require_optional_nonempty_str,
    _require_schema_version_v1,
    _require_sha256,
    _require_str_tuple,
    _to_plain_json,
)
from caelab.materials.contracts import (
    AdapterIdentity,
    AssessmentResultV1,
    CapabilityReport,
    ComparisonSpec,
    ExperimentSpec,
    ModelDescriptorV1,
    ParameterSpec,
    PredictionBundleV1,
    _coerce_diagnostics,
    _coerce_mapping_or_dataclass,
    _coerce_typed_tuple,
)

__all__ = [
    "AdapterRegistry",
    "ApplyPlan",
    "ApplyReceipt",
    "CalibrationEvaluationArtifact",
    "EvaluationPlan",
    "ExportReceipt",
    "MaterialAdapter",
    "MaterialEvaluationRequest",
    "MaterialExportPlan",
    "MaterialStudyError",
    "ReconcileReport",
    "RecoveryReceipt",
    "RecoveryReport",
    "RelinkReceipt",
    "SaveAsReceipt",
    "default_registry",
]

_EXECUTION_PHASES = frozenset({"exploration", "confirmation"})
_PLAN_KINDS = frozenset({"in_process", "native_run"})
_SAVEAS_STATES = frozenset({"committed", "committed_cleanup_pending"})
_EVAL_STATES = frozenset({"pending", "running", "completed", "failed", "cancelled", "stopped", "interrupted"})


class MaterialStudyError(Exception):
    """Structured error raised by material calibration services and adapters (C7)."""

    def __init__(
        self,
        code: str,
        stage: str,
        details: Mapping[str, Any] | str | None = None,
        message: str | None = None,
    ) -> None:
        self.code = _require_nonempty_str(code, "MaterialStudyError.code")
        self.stage = _require_nonempty_str(stage, "MaterialStudyError.stage")
        if details is None:
            detail_dict: dict[str, Any] = {}
        elif isinstance(details, str):
            detail_dict = {"message": details}
        elif isinstance(details, Mapping):
            detail_dict = dict(details)
        else:
            raise ValueError("MaterialStudyError.details must be a mapping, str, or None")
        if message is not None and "message" not in detail_dict:
            detail_dict["message"] = message
        self.details: dict[str, Any] = detail_dict
        msg = detail_dict.get("message") or detail_dict.get("reason") or self.code
        super().__init__(f"[{self.code}] ({self.stage}): {msg}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "stage": self.stage,
            "details": _to_plain_json(self.details),
        }


@dataclass(frozen=True)
class MaterialEvaluationRequest:
    """Host-built immutable request for MaterialAdapter.plan/execute (C4)."""

    request_id: str
    evaluation_id: str
    study_id: str
    study_revision: str
    model_descriptor: ModelDescriptorV1
    parameter_specs: tuple[ParameterSpec, ...]
    physical_parameters: Mapping[str, float | str]
    experiments: tuple[ExperimentSpec, ...]
    comparisons: tuple[ComparisonSpec, ...] = ()
    numerical_settings: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    execution_phase: str = "exploration"
    selection_ref: ArtifactRefV1 | None = None
    experiment_data: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    runtime_cache: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.request_id, "MaterialEvaluationRequest.request_id")
        _require_nonempty_str(self.evaluation_id, "MaterialEvaluationRequest.evaluation_id")
        _require_nonempty_str(self.study_id, "MaterialEvaluationRequest.study_id")
        _require_nonempty_str(self.study_revision, "MaterialEvaluationRequest.study_revision")
        if not isinstance(self.model_descriptor, ModelDescriptorV1):
            raise ValueError("MaterialEvaluationRequest.model_descriptor must be a ModelDescriptorV1")
        object.__setattr__(
            self,
            "parameter_specs",
            _coerce_typed_tuple(self.parameter_specs, ParameterSpec, "MaterialEvaluationRequest.parameter_specs"),
        )
        if not isinstance(self.physical_parameters, Mapping):
            raise ValueError("MaterialEvaluationRequest.physical_parameters must be a mapping")
        params: dict[str, float | str] = {}
        for k, v in self.physical_parameters.items():
            _require_nonempty_str(k, "physical_parameters key")
            if isinstance(v, str):
                params[k] = _require_nonempty_str(v, f"physical_parameters[{k!r}]")
            else:
                params[k] = _require_finite_float(v, f"physical_parameters[{k!r}]")
        object.__setattr__(self, "physical_parameters", MappingProxyType(params))
        object.__setattr__(
            self,
            "experiments",
            _coerce_typed_tuple(self.experiments, ExperimentSpec, "MaterialEvaluationRequest.experiments"),
        )
        object.__setattr__(
            self,
            "comparisons",
            _coerce_typed_tuple(self.comparisons, ComparisonSpec, "MaterialEvaluationRequest.comparisons"),
        )
        object.__setattr__(
            self,
            "numerical_settings",
            _freeze_mapping(self.numerical_settings, context="MaterialEvaluationRequest.numerical_settings"),
        )
        if self.execution_phase not in _EXECUTION_PHASES:
            raise ValueError(
                f"MaterialEvaluationRequest.execution_phase must be one of {sorted(_EXECUTION_PHASES)}, got {self.execution_phase!r}"
            )
        if self.selection_ref is not None and not isinstance(self.selection_ref, ArtifactRefV1):
            raise ValueError("MaterialEvaluationRequest.selection_ref must be None or ArtifactRefV1")
        if self.execution_phase == "exploration" and self.selection_ref is not None:
            raise ValueError("MaterialEvaluationRequest in 'exploration' phase must have selection_ref=None")
        if self.execution_phase == "exploration":
            for exp in self.experiments:
                if exp.split == "holdout":
                    raise ValueError(
                        f"MaterialEvaluationRequest in 'exploration' phase must not include holdout experiment {exp.experiment_id!r}"
                    )
        object.__setattr__(
            self,
            "experiment_data",
            _freeze_mapping(self.experiment_data, context="MaterialEvaluationRequest.experiment_data"),
        )


@dataclass(frozen=True)
class EvaluationPlan:
    """Deterministic execution plan returned by MaterialAdapter.plan."""

    plan_id: str
    evaluation_id: str
    request_id: str
    study_revision: str
    model_id: str
    model_revision: str
    adapter: AdapterIdentity
    kind: str
    execution_phase: str
    physical_parameters: Mapping[str, float | str] = field(default_factory=lambda: MappingProxyType({}))
    experiment_ids: tuple[str, ...] = ()
    experiment_plans: tuple[Mapping[str, Any], ...] = ()
    expected_artifacts: tuple[str, ...] = ()
    numerical_settings: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    callback_id: str | None = None
    diagnostics: tuple[Mapping[str, Any] | str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.plan_id, "EvaluationPlan.plan_id")
        _require_nonempty_str(self.evaluation_id, "EvaluationPlan.evaluation_id")
        _require_nonempty_str(self.request_id, "EvaluationPlan.request_id")
        _require_nonempty_str(self.study_revision, "EvaluationPlan.study_revision")
        _require_nonempty_str(self.model_id, "EvaluationPlan.model_id")
        _require_nonempty_str(self.model_revision, "EvaluationPlan.model_revision")
        if not isinstance(self.adapter, AdapterIdentity):
            raise ValueError("EvaluationPlan.adapter must be an AdapterIdentity")
        if self.kind not in _PLAN_KINDS:
            raise ValueError(f"EvaluationPlan.kind must be one of {sorted(_PLAN_KINDS)}, got {self.kind!r}")
        if self.execution_phase not in _EXECUTION_PHASES:
            raise ValueError(
                f"EvaluationPlan.execution_phase must be one of {sorted(_EXECUTION_PHASES)}, got {self.execution_phase!r}"
            )
        object.__setattr__(
            self,
            "physical_parameters",
            _freeze_mapping(self.physical_parameters, context="EvaluationPlan.physical_parameters"),
        )
        object.__setattr__(
            self,
            "experiment_ids",
            _require_str_tuple(self.experiment_ids, "EvaluationPlan.experiment_ids"),
        )
        if isinstance(self.experiment_plans, (str, bytes, Mapping)) or not isinstance(self.experiment_plans, Sequence):
            raise ValueError("EvaluationPlan.experiment_plans must be a sequence")
        object.__setattr__(
            self,
            "experiment_plans",
            tuple(
                _freeze_mapping(ep, context=f"EvaluationPlan.experiment_plans[{idx}]")
                for idx, ep in enumerate(self.experiment_plans)
            ),
        )
        object.__setattr__(
            self,
            "expected_artifacts",
            _require_str_tuple(self.expected_artifacts, "EvaluationPlan.expected_artifacts"),
        )
        object.__setattr__(
            self,
            "numerical_settings",
            _freeze_mapping(self.numerical_settings, context="EvaluationPlan.numerical_settings"),
        )
        _require_optional_nonempty_str(self.callback_id, "EvaluationPlan.callback_id")
        object.__setattr__(self, "diagnostics", _coerce_diagnostics(self.diagnostics, "EvaluationPlan.diagnostics"))


@dataclass(frozen=True)
class MaterialExportPlan:
    """Export report/artifact plan generated by MaterialAdapter.export (B0/C11)."""

    export_id: str
    format_kind: str
    supports_native_card: bool
    supported_solver_identity: Mapping[str, Any] | None
    model_id: str
    model_revision: str
    adapter: AdapterIdentity
    study_id: str
    content_revision: str
    evaluation_id: str
    formulation: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    parameters: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    provenance: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    sources: tuple[Mapping[str, Any], ...] = ()
    assessment_summary: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    limitations: tuple[str, ...] = ()
    files: tuple[Mapping[str, Any], ...] = ()
    diagnostics: tuple[Mapping[str, Any] | str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.export_id, "MaterialExportPlan.export_id")
        _require_nonempty_str(self.format_kind, "MaterialExportPlan.format_kind")
        _require_bool(self.supports_native_card, "MaterialExportPlan.supports_native_card")
        if self.supported_solver_identity is not None:
            object.__setattr__(
                self,
                "supported_solver_identity",
                _freeze_mapping(self.supported_solver_identity, context="MaterialExportPlan.supported_solver_identity"),
            )
        _require_nonempty_str(self.model_id, "MaterialExportPlan.model_id")
        _require_nonempty_str(self.model_revision, "MaterialExportPlan.model_revision")
        if not isinstance(self.adapter, AdapterIdentity):
            raise ValueError("MaterialExportPlan.adapter must be an AdapterIdentity")
        _require_nonempty_str(self.study_id, "MaterialExportPlan.study_id")
        _require_nonempty_str(self.content_revision, "MaterialExportPlan.content_revision")
        _require_nonempty_str(self.evaluation_id, "MaterialExportPlan.evaluation_id")
        for attr in ("formulation", "parameters", "provenance", "assessment_summary"):
            object.__setattr__(self, attr, _freeze_mapping(getattr(self, attr), context=f"MaterialExportPlan.{attr}"))
        if isinstance(self.sources, (str, bytes, Mapping)) or not isinstance(self.sources, Sequence):
            raise ValueError("MaterialExportPlan.sources must be a sequence")
        object.__setattr__(
            self,
            "sources",
            tuple(_freeze_mapping(s, context=f"MaterialExportPlan.sources[{i}]") for i, s in enumerate(self.sources)),
        )
        object.__setattr__(self, "limitations", _require_str_tuple(self.limitations, "MaterialExportPlan.limitations"))
        if isinstance(self.files, (str, bytes, Mapping)) or not isinstance(self.files, Sequence):
            raise ValueError("MaterialExportPlan.files must be a sequence")
        object.__setattr__(
            self,
            "files",
            tuple(_freeze_mapping(f, context=f"MaterialExportPlan.files[{i}]") for i, f in enumerate(self.files)),
        )
        object.__setattr__(self, "diagnostics", _coerce_diagnostics(self.diagnostics, "MaterialExportPlan.diagnostics"))


@dataclass(frozen=True)
class CalibrationEvaluationArtifact:
    """Immutable persisted artifact for one material calibration evaluation (C4/C7)."""

    evaluation_id: str
    campaign_id: str | None
    study_id: str
    content_revision: str
    model_id: str
    model_revision: str
    adapter: AdapterIdentity
    execution_phase: str
    selection_ref: ArtifactRefV1 | None
    physical_parameters: Mapping[str, float | str]
    numerical_settings: Mapping[str, Any]
    state: str
    cache_hit: bool
    prediction_bundle: PredictionBundleV1 | None
    assessment: AssessmentResultV1
    channel_results: tuple[Mapping[str, Any], ...] = ()
    identifiability: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    optimizer_scalars: Mapping[str, float | None] = field(default_factory=lambda: MappingProxyType({}))
    source_id: str = ""
    run_ids: tuple[str, ...] = ()
    diagnostics: tuple[Mapping[str, Any] | str, ...] = ()
    created_at: str = ""
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.evaluation_id, "CalibrationEvaluationArtifact.evaluation_id")
        _require_optional_nonempty_str(self.campaign_id, "CalibrationEvaluationArtifact.campaign_id")
        _require_nonempty_str(self.study_id, "CalibrationEvaluationArtifact.study_id")
        _require_nonempty_str(self.content_revision, "CalibrationEvaluationArtifact.content_revision")
        _require_nonempty_str(self.model_id, "CalibrationEvaluationArtifact.model_id")
        _require_nonempty_str(self.model_revision, "CalibrationEvaluationArtifact.model_revision")
        if not isinstance(self.adapter, AdapterIdentity):
            raise ValueError("CalibrationEvaluationArtifact.adapter must be an AdapterIdentity")
        if self.execution_phase not in _EXECUTION_PHASES:
            raise ValueError(
                f"CalibrationEvaluationArtifact.execution_phase must be one of {sorted(_EXECUTION_PHASES)}"
            )
        if self.selection_ref is not None and not isinstance(self.selection_ref, ArtifactRefV1):
            raise ValueError("CalibrationEvaluationArtifact.selection_ref must be None or ArtifactRefV1")
        if not isinstance(self.physical_parameters, Mapping):
            raise ValueError("CalibrationEvaluationArtifact.physical_parameters must be a mapping")
        params: dict[str, float | str] = {}
        for k, v in self.physical_parameters.items():
            _require_nonempty_str(k, "physical_parameters key")
            params[k] = _require_nonempty_str(v, k) if isinstance(v, str) else _require_finite_float(v, k)
        object.__setattr__(self, "physical_parameters", MappingProxyType(params))
        object.__setattr__(
            self,
            "numerical_settings",
            _freeze_mapping(self.numerical_settings, context="CalibrationEvaluationArtifact.numerical_settings"),
        )
        if self.state not in _EVAL_STATES:
            raise ValueError(f"CalibrationEvaluationArtifact.state must be one of {sorted(_EVAL_STATES)}")
        _require_bool(self.cache_hit, "CalibrationEvaluationArtifact.cache_hit")
        if self.prediction_bundle is not None and not isinstance(self.prediction_bundle, PredictionBundleV1):
            raise ValueError("CalibrationEvaluationArtifact.prediction_bundle must be None or PredictionBundleV1")
        if not isinstance(self.assessment, AssessmentResultV1):
            raise ValueError("CalibrationEvaluationArtifact.assessment must be an AssessmentResultV1")
        if isinstance(self.channel_results, (str, bytes, Mapping)) or not isinstance(self.channel_results, Sequence):
            raise ValueError("CalibrationEvaluationArtifact.channel_results must be a sequence")
        object.__setattr__(
            self,
            "channel_results",
            tuple(
                _freeze_mapping(item, context=f"CalibrationEvaluationArtifact.channel_results[{i}]")
                for i, item in enumerate(self.channel_results)
            ),
        )
        object.__setattr__(
            self,
            "identifiability",
            _freeze_mapping(self.identifiability, context="CalibrationEvaluationArtifact.identifiability"),
        )
        if not isinstance(self.optimizer_scalars, Mapping):
            raise ValueError("CalibrationEvaluationArtifact.optimizer_scalars must be a mapping")
        scalars: dict[str, float | None] = {}
        for k, v in self.optimizer_scalars.items():
            _require_nonempty_str(k, "optimizer_scalars key")
            scalars[k] = None if v is None else _require_finite_float(v, f"optimizer_scalars[{k!r}]")
        object.__setattr__(self, "optimizer_scalars", MappingProxyType(scalars))
        if not isinstance(self.source_id, str) or "\x00" in self.source_id:
            raise ValueError("CalibrationEvaluationArtifact.source_id must be a valid string")
        object.__setattr__(
            self,
            "run_ids",
            _require_str_tuple(self.run_ids, "CalibrationEvaluationArtifact.run_ids"),
        )
        object.__setattr__(
            self,
            "diagnostics",
            _coerce_diagnostics(self.diagnostics, "CalibrationEvaluationArtifact.diagnostics"),
        )
        _require_nonempty_str(self.created_at, "CalibrationEvaluationArtifact.created_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evaluation_id": self.evaluation_id,
            "campaign_id": self.campaign_id,
            "study_id": self.study_id,
            "content_revision": self.content_revision,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "adapter": self.adapter.to_dict(),
            "execution_phase": self.execution_phase,
            "selection_ref": None if self.selection_ref is None else self.selection_ref.to_dict(),
            "physical_parameters": dict(self.physical_parameters),
            "numerical_settings": _to_plain_json(self.numerical_settings),
            "state": self.state,
            "cache_hit": self.cache_hit,
            "prediction_bundle": None if self.prediction_bundle is None else self.prediction_bundle.to_dict(),
            "assessment": self.assessment.to_dict(),
            "channel_results": _to_plain_json(self.channel_results),
            "identifiability": _to_plain_json(self.identifiability),
            "optimizer_scalars": dict(self.optimizer_scalars),
            "source_id": self.source_id,
            "run_ids": list(self.run_ids),
            "diagnostics": _to_plain_json(self.diagnostics),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CalibrationEvaluationArtifact:
        required = {
            "schema_version",
            "evaluation_id",
            "campaign_id",
            "study_id",
            "content_revision",
            "model_id",
            "model_revision",
            "adapter",
            "execution_phase",
            "selection_ref",
            "physical_parameters",
            "numerical_settings",
            "state",
            "cache_hit",
            "prediction_bundle",
            "assessment",
            "channel_results",
            "identifiability",
            "optimizer_scalars",
            "source_id",
            "run_ids",
            "diagnostics",
            "created_at",
        }
        m = _require_exact_keys(data, required, context="CalibrationEvaluationArtifact")
        _require_schema_version_v1(m["schema_version"])
        return cls(
            schema_version=1,
            evaluation_id=m["evaluation_id"],
            campaign_id=m["campaign_id"],
            study_id=m["study_id"],
            content_revision=m["content_revision"],
            model_id=m["model_id"],
            model_revision=m["model_revision"],
            adapter=AdapterIdentity.from_dict(m["adapter"]),
            execution_phase=m["execution_phase"],
            selection_ref=None if m["selection_ref"] is None else ArtifactRefV1.from_dict(m["selection_ref"]),
            physical_parameters=m["physical_parameters"],
            numerical_settings=m["numerical_settings"],
            state=m["state"],
            cache_hit=m["cache_hit"],
            prediction_bundle=(
                None if m["prediction_bundle"] is None else PredictionBundleV1.from_dict(m["prediction_bundle"])
            ),
            assessment=AssessmentResultV1.from_dict(m["assessment"]),
            channel_results=m["channel_results"],
            identifiability=m["identifiability"],
            optimizer_scalars=m["optimizer_scalars"],
            source_id=m["source_id"],
            run_ids=m["run_ids"],
            diagnostics=m["diagnostics"],
            created_at=m["created_at"],
        )


@dataclass(frozen=True)
class SaveAsReceipt:
    """Receipt returned by CalibrationStudyService.save_as (C7/C8)."""

    transaction_id: str
    source_path: str
    destination_path: str
    document_revision: str
    content_revision: str
    state: str
    copied_artifact_count: int
    warnings: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.transaction_id, "SaveAsReceipt.transaction_id")
        _require_nonempty_str(self.source_path, "SaveAsReceipt.source_path")
        _require_nonempty_str(self.destination_path, "SaveAsReceipt.destination_path")
        _require_sha256(self.document_revision, "SaveAsReceipt.document_revision")
        _require_sha256(self.content_revision, "SaveAsReceipt.content_revision")
        if self.state not in _SAVEAS_STATES:
            raise ValueError(f"SaveAsReceipt.state must be one of {sorted(_SAVEAS_STATES)}, got {self.state!r}")
        _require_int(self.copied_artifact_count, "SaveAsReceipt.copied_artifact_count", minimum=0)
        object.__setattr__(self, "warnings", _require_str_tuple(self.warnings, "SaveAsReceipt.warnings"))


@dataclass(frozen=True)
class ReconcileReport:
    """Read-only reconciliation report for study sources and artifacts (C7)."""

    document_revision: str
    content_revision: str
    source_states: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    artifact_states: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    applicability: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    diagnostics: tuple[Mapping[str, Any] | str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_sha256(self.document_revision, "ReconcileReport.document_revision")
        _require_sha256(self.content_revision, "ReconcileReport.content_revision")
        for attr in ("source_states", "artifact_states", "applicability"):
            object.__setattr__(self, attr, _freeze_mapping(getattr(self, attr), context=f"ReconcileReport.{attr}"))
        object.__setattr__(self, "diagnostics", _coerce_diagnostics(self.diagnostics, "ReconcileReport.diagnostics"))


@dataclass(frozen=True)
class RecoveryReport:
    """Report of pending transactions, interrupted campaigns, and corrupt artifacts (C7/C8)."""

    transactions: tuple[Mapping[str, Any], ...] = ()
    interrupted_campaigns: tuple[Mapping[str, Any], ...] = ()
    corrupt_artifacts: tuple[Mapping[str, Any], ...] = ()
    allowed_actions: tuple[str, ...] = ()
    diagnostics: tuple[Mapping[str, Any] | str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        for attr in ("transactions", "interrupted_campaigns", "corrupt_artifacts"):
            raw = getattr(self, attr)
            if isinstance(raw, (str, bytes, Mapping)) or not isinstance(raw, Sequence):
                raise ValueError(f"RecoveryReport.{attr} must be a sequence")
            object.__setattr__(
                self,
                attr,
                tuple(_freeze_mapping(item, context=f"RecoveryReport.{attr}[{i}]") for i, item in enumerate(raw)),
            )
        object.__setattr__(
            self, "allowed_actions", _require_str_tuple(self.allowed_actions, "RecoveryReport.allowed_actions")
        )
        object.__setattr__(self, "diagnostics", _coerce_diagnostics(self.diagnostics, "RecoveryReport.diagnostics"))


@dataclass(frozen=True)
class RecoveryReceipt:
    """Receipt returned by CalibrationStudyService.recover (C7/C8)."""

    transaction_id: str
    action: str
    state: str
    document_revision: str
    diagnostics: tuple[Mapping[str, Any] | str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.transaction_id, "RecoveryReceipt.transaction_id")
        _require_nonempty_str(self.action, "RecoveryReceipt.action")
        _require_nonempty_str(self.state, "RecoveryReceipt.state")
        _require_sha256(self.document_revision, "RecoveryReceipt.document_revision")
        object.__setattr__(self, "diagnostics", _coerce_diagnostics(self.diagnostics, "RecoveryReceipt.diagnostics"))


@dataclass(frozen=True)
class RelinkReceipt:
    """Receipt returned by CalibrationStudyService.relink_source (C7)."""

    source_id: str
    old_path: str
    new_path: str
    sha256: str
    document_revision: str
    content_revision: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.source_id, "RelinkReceipt.source_id")
        _require_nonempty_str(self.old_path, "RelinkReceipt.old_path")
        _require_nonempty_str(self.new_path, "RelinkReceipt.new_path")
        _require_sha256(self.sha256, "RelinkReceipt.sha256")
        _require_sha256(self.document_revision, "RelinkReceipt.document_revision")
        _require_sha256(self.content_revision, "RelinkReceipt.content_revision")


@dataclass(frozen=True)
class ExportReceipt:
    """Receipt returned by CalibrationStudyService.export (C7)."""

    export_id: str
    evaluation_id: str
    manifest_ref: ArtifactRefV1
    artifacts: tuple[ArtifactRefV1, ...] = ()
    supported_solver_identity: Mapping[str, Any] | None = None
    diagnostics: tuple[Mapping[str, Any] | str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.export_id, "ExportReceipt.export_id")
        _require_nonempty_str(self.evaluation_id, "ExportReceipt.evaluation_id")
        if not isinstance(self.manifest_ref, ArtifactRefV1):
            raise ValueError("ExportReceipt.manifest_ref must be an ArtifactRefV1")
        object.__setattr__(
            self, "artifacts", _coerce_typed_tuple(self.artifacts, ArtifactRefV1, "ExportReceipt.artifacts")
        )
        if self.supported_solver_identity is not None:
            object.__setattr__(
                self,
                "supported_solver_identity",
                _freeze_mapping(self.supported_solver_identity, context="ExportReceipt.supported_solver_identity"),
            )
        object.__setattr__(self, "diagnostics", _coerce_diagnostics(self.diagnostics, "ExportReceipt.diagnostics"))


@dataclass(frozen=True)
class ApplyPlan:
    """Prepared atomic apply plan for an open Prep document (C7/C10)."""

    plan_id: str
    evaluation_id: str
    target: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    supported: bool = False
    reason_codes: tuple[str, ...] = ()
    changes_preview: tuple[Mapping[str, Any], ...] = ()
    expires_at: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.plan_id, "ApplyPlan.plan_id")
        _require_nonempty_str(self.evaluation_id, "ApplyPlan.evaluation_id")
        object.__setattr__(self, "target", _freeze_mapping(self.target, context="ApplyPlan.target"))
        _require_bool(self.supported, "ApplyPlan.supported")
        object.__setattr__(self, "reason_codes", _require_str_tuple(self.reason_codes, "ApplyPlan.reason_codes"))
        if isinstance(self.changes_preview, (str, bytes, Mapping)) or not isinstance(self.changes_preview, Sequence):
            raise ValueError("ApplyPlan.changes_preview must be a sequence")
        object.__setattr__(
            self,
            "changes_preview",
            tuple(_freeze_mapping(c, context=f"ApplyPlan.changes_preview[{i}]") for i, c in enumerate(self.changes_preview)),
        )
        _require_optional_nonempty_str(self.expires_at, "ApplyPlan.expires_at")


@dataclass(frozen=True)
class ApplyReceipt:
    """Receipt returned by CalibrationStudyService.commit_apply (C7/C10)."""

    plan_id: str
    evaluation_id: str
    status: str
    reason_codes: tuple[str, ...] = ()
    applied_hashes: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.plan_id, "ApplyReceipt.plan_id")
        _require_nonempty_str(self.evaluation_id, "ApplyReceipt.evaluation_id")
        _require_nonempty_str(self.status, "ApplyReceipt.status")
        object.__setattr__(self, "reason_codes", _require_str_tuple(self.reason_codes, "ApplyReceipt.reason_codes"))
        object.__setattr__(
            self, "applied_hashes", _freeze_mapping(self.applied_hashes, context="ApplyReceipt.applied_hashes")
        )


@runtime_checkable
class MaterialAdapter(Protocol):
    """Unified MaterialAdapter plugin contract (C3/B0/A2)."""

    @property
    def identity(self) -> AdapterIdentity: ...

    def descriptor(self) -> ModelDescriptorV1: ...

    def capabilities(
        self,
        model: ModelDescriptorV1 | None = None,
        environment: Mapping[str, Any] | None = None,
    ) -> CapabilityReport: ...

    def validate(
        self,
        model: ModelDescriptorV1,
        parameters: Mapping[str, Any] | Sequence[ParameterSpec],
        experiments: Sequence[ExperimentSpec],
        numerical_settings: Mapping[str, Any] | None = None,
    ) -> tuple[Mapping[str, Any], ...]: ...

    def plan(self, request: MaterialEvaluationRequest) -> EvaluationPlan: ...

    def execute(
        self,
        plan: EvaluationPlan,
        request: MaterialEvaluationRequest,
        *,
        data_root: Path,
        study_id: str,
        cancel_event: threading.Event | None = None,
    ) -> Mapping[str, Any]: ...

    def collect(
        self,
        plan: EvaluationPlan,
        artifacts: Mapping[str, Any],
        *,
        data_root: Path,
        study_id: str,
    ) -> PredictionBundleV1: ...

    def export(
        self,
        evaluation_artifact: Mapping[str, Any],
        *,
        destination: Path | None = None,
    ) -> MaterialExportPlan: ...


class AdapterRegistry:
    """Trusted registry mapping (model_id, adapter_version) to MaterialAdapter plugins."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._adapters: dict[tuple[str, str], MaterialAdapter] = {}
        self._defaults: dict[str, str] = {}

    def register(self, adapter: MaterialAdapter, *, default: bool = True, replace: bool = False) -> None:
        if not isinstance(adapter, MaterialAdapter):
            raise ValueError("Registered adapter must implement MaterialAdapter protocol")
        desc = adapter.descriptor()
        ident = adapter.identity
        if not isinstance(desc, ModelDescriptorV1):
            raise ValueError("adapter.descriptor() must return ModelDescriptorV1")
        if not isinstance(ident, AdapterIdentity):
            raise ValueError("adapter.identity must return AdapterIdentity")
        cap = adapter.capabilities(desc, None)
        if not isinstance(cap, CapabilityReport):
            raise ValueError("adapter.capabilities() must return CapabilityReport")
        if cap.model_id != desc.model_id:
            raise ValueError(f"CapabilityReport.model_id {cap.model_id!r} != descriptor.model_id {desc.model_id!r}")
        if cap.adapter.id != ident.id or cap.adapter.version != ident.version:
            raise ValueError("CapabilityReport.adapter must match adapter.identity")

        key = (desc.model_id, ident.version)
        with self._lock:
            if key in self._adapters and not replace:
                raise ValueError(f"Adapter already registered for model={desc.model_id!r}, version={ident.version!r}")
            self._adapters[key] = adapter
            if default or desc.model_id not in self._defaults:
                self._defaults[desc.model_id] = ident.version

    def get(self, model_id: str, adapter_version: str | None = None) -> MaterialAdapter:
        with self._lock:
            if adapter_version is None:
                version = self._defaults.get(model_id)
                if version is None:
                    raise MaterialStudyError(
                        "UNSUPPORTED_MODEL",
                        "registry.get",
                        {"model_id": model_id, "reason": f"No adapter registered for model {model_id!r}"},
                    )
                return self._adapters[(model_id, version)]
            key = (model_id, adapter_version)
            if key not in self._adapters:
                if model_id not in self._defaults:
                    raise MaterialStudyError(
                        "UNSUPPORTED_MODEL",
                        "registry.get",
                        {"model_id": model_id, "reason": f"Unknown model {model_id!r}"},
                    )
                raise MaterialStudyError(
                    "UNSUPPORTED_ADAPTER_VERSION",
                    "registry.get",
                    {
                        "model_id": model_id,
                        "adapter_version": adapter_version,
                        "reason": f"Unsupported adapter version {adapter_version!r} for {model_id!r}",
                    },
                )
            return self._adapters[key]

    def descriptor(self, model_id: str, adapter_version: str | None = None) -> ModelDescriptorV1:
        return self.get(model_id, adapter_version).descriptor()

    def capabilities(
        self,
        model_id: str,
        adapter_version: str | None = None,
        environment: Mapping[str, Any] | None = None,
    ) -> CapabilityReport:
        adapter = self.get(model_id, adapter_version)
        return adapter.capabilities(adapter.descriptor(), environment)

    def list_models(self) -> tuple[ModelDescriptorV1, ...]:
        with self._lock:
            return tuple(
                self._adapters[(model_id, version)].descriptor()
                for model_id, version in sorted(self._defaults.items())
            )

    def list_adapters(self) -> tuple[tuple[str, str, AdapterIdentity], ...]:
        with self._lock:
            return tuple(
                (model_id, version, adapter.identity)
                for (model_id, version), adapter in sorted(self._adapters.items())
            )


_DEFAULT_REGISTRY_LOCK = threading.Lock()
_DEFAULT_REGISTRY: AdapterRegistry | None = None


def default_registry() -> AdapterRegistry:
    """Return the process-wide default AdapterRegistry with built-in adapters registered."""
    global _DEFAULT_REGISTRY
    with _DEFAULT_REGISTRY_LOCK:
        if _DEFAULT_REGISTRY is None:
            reg = AdapterRegistry()
            from caelab.materials.adapters.prony_trs import PronyTRSAdapter

            reg.register(PronyTRSAdapter(), default=True)
            _DEFAULT_REGISTRY = reg
        return _DEFAULT_REGISTRY
