"""CORE02 & STORE01 CalibrationStudyService: lifecycle, revisions, holdout seal, locks, SaveAs, recovery."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import threading
import uuid
from typing import Any

from filelock import FileLock, Timeout

from caelab.artifact_refs import (
    ArtifactRefV1,
    ProducerIdentity,
    _to_plain_json,
    canonical_json_bytes,
    canonical_sha256,
    validate_relative_artifact_path,
)
from caelab.materials.contracts import (
    AdapterIdentity,
    CampaignReceipt,
    CancelReceipt,
    ComparisonSpec,
    EvaluationReceipt,
    ExperimentChannelSpec,
    ExperimentSpec,
    ImportReceipt,
    ModelDescriptorV1,
    ParameterSpec,
    ScalarResponseSpecV1,
    SelectionRecordV1,
    StudySnapshot,
)
from caelab.materials.evaluator import (
    MaterialBatchEvaluator,
    build_variable_space,
    compute_candidate_cache_key,
    execute_single_evaluation,
    resolve_parameter_values,
)
from caelab.materials.experiments import (
    import_csv_experiment,
    preview_csv_rows,
)
from caelab.materials.registry import (
    AdapterRegistry,
    ApplyPlan,
    ApplyReceipt,
    CalibrationEvaluationArtifact,
    ExportReceipt,
    MaterialAdapter,
    MaterialStudyError,
    ReconcileReport,
    RecoveryReceipt,
    RecoveryReport,
    RelinkReceipt,
    SaveAsReceipt,
    default_registry,
)
from caelab.materials.residuals import build_normalization_payload
from caelab.mdo.constraints import ConstraintSpec
from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.optimize import OptimizationConfig, OptimizationProblem, optimize as mdo_optimize
from caelab.native.run_store import atomic_json, utc_now

__all__ = ["CalibrationStudyService"]

_PRODUCER = ProducerIdentity(id="caelab.materials.service", version="1.0.0")
_STUDY_FORMAT = "caelab-material-study"
_CAEMAT_SUFFIX = ".caemat.json"
_CACHE_POLICIES = frozenset({"reuse", "refresh"})
_EXECUTION_PHASES = frozenset({"exploration", "confirmation"})
_SELECTION_MODES = frozenset({"training_only", "manual_with_validation"})
_RECOVERY_ACTIONS = frozenset({"mark_interrupted", "finish_save_as", "discard_incomplete_copy"})


def _derive_study_paths(path: str | Path) -> tuple[Path, Path, str]:
    """Derive (manifest_path, data_root_path, data_root_rel_posix) removing exact .caemat.json suffix."""
    p = Path(path).expanduser()
    if p.name == "calibration.json":
        resolved = p.resolve()
        return resolved, resolved.parent, "."
    if not p.name.endswith(_CAEMAT_SUFFIX):
        raise MaterialStudyError(
            "INVALID_PATH",
            "study_path",
            {"reason": "STUDY_PATH_MUST_END_WITH_CAEMAT_JSON", "path": str(p)},
        )
    stem = p.name[: -len(_CAEMAT_SUFFIX)]
    if not stem or stem in (".", ".."):
        raise MaterialStudyError(
            "INVALID_PATH",
            "study_path",
            {"reason": "INVALID_STUDY_STEM", "path": str(p)},
        )
    resolved = p.resolve()
    data_root_rel = f"{stem}.caemat-data"
    return resolved, (resolved.parent / data_root_rel), data_root_rel


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_no_symlinks_under(root: Path) -> None:
    if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
        raise MaterialStudyError(
            "CORRUPT_ARTIFACT",
            "storage_integrity",
            {"reason": "SYMLINK_OR_JUNCTION_REJECTED", "path": str(root)},
        )
    if not root.is_dir():
        return
    for item in root.rglob("*"):
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise MaterialStudyError(
                "CORRUPT_ARTIFACT",
                "storage_integrity",
                {"reason": "SYMLINK_OR_JUNCTION_REJECTED", "path": str(item)},
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


def _coerce_parameter_spec(raw: ParameterSpec | Mapping[str, Any]) -> ParameterSpec:
    if isinstance(raw, ParameterSpec):
        return raw
    if isinstance(raw, Mapping):
        if "schema_version" in raw:
            return ParameterSpec.from_dict(raw)
        return ParameterSpec(**raw)
    raise MaterialStudyError("INVALID_CONFIGURATION", "parameter_spec", {"reason": "INVALID_PARAMETER_SPEC"})


def _coerce_comparison_spec(raw: ComparisonSpec | Mapping[str, Any]) -> ComparisonSpec:
    if isinstance(raw, ComparisonSpec):
        return raw
    if isinstance(raw, Mapping):
        if "schema_version" in raw:
            return ComparisonSpec.from_dict(raw)
        return ComparisonSpec(**raw)
    raise MaterialStudyError("INVALID_CONFIGURATION", "comparison_spec", {"reason": "INVALID_COMPARISON_SPEC"})


def _coerce_constraint_spec(raw: ConstraintSpec | Mapping[str, Any]) -> ConstraintSpec:
    if isinstance(raw, ConstraintSpec):
        return raw
    if isinstance(raw, Mapping):
        return ConstraintSpec(**raw)
    raise MaterialStudyError("INVALID_CONFIGURATION", "constraint_spec", {"reason": "INVALID_CONSTRAINT_SPEC"})


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


def _opt_config_to_dict(cfg: OptimizationConfig | Mapping[str, Any] | None) -> dict[str, Any] | None:
    if cfg is None:
        return None
    if isinstance(cfg, Mapping):
        return dict(_to_plain_json(cfg))
    return {
        "algorithm": cfg.algorithm,
        "seed": cfg.seed,
        "max_evaluations": cfg.max_evaluations,
        "max_iterations": cfg.max_iterations,
        "population_size": cfg.population_size,
        "max_failures": cfg.max_failures,
        "wall_seconds": cfg.wall_seconds,
        "weighted_sum": cfg.weighted_sum,
        "ftol": cfg.ftol,
        "finite_difference_step": cfg.finite_difference_step,
        "crossover_probability": cfg.crossover_probability,
        "crossover_eta": cfg.crossover_eta,
        "mutation_eta": cfg.mutation_eta,
        "de_strategy": cfg.de_strategy,
        "de_mutation": list(cfg.de_mutation),
        "de_recombination": cfg.de_recombination,
    }


class CalibrationStudyService:
    """Headless calibration study service managing C2 revisions, C4 holdout seal, C7 execution, and C8 storage."""

    def __init__(
        self,
        *,
        path: Path,
        data_root: Path,
        data_root_rel: str,
        manifest: dict[str, Any],
        document_revision: str,
        read_only: bool,
        lease: FileLock | None,
        registry: AdapterRegistry,
        workbench: Any = None,
    ) -> None:
        self._path = path
        self._data_root = data_root
        self._data_root_rel = data_root_rel
        self._manifest = manifest
        self._document_revision = document_revision
        self._read_only = read_only
        self._lease = lease
        self._registry = registry
        self.workbench = workbench

        self._state_lock = threading.RLock()
        self._busy_operation: str | None = None
        self._active_campaign_id: str | None = None
        self._active_operation_id: str | None = None
        self._active_cancel_event: threading.Event | None = None
        self._active_stop_event: threading.Event | None = None
        self._active_workers: set[threading.Thread] = set()
        self._closed = False
        self._diagnostics: list[Any] = []
        self._corrupt_artifacts: list[dict[str, Any]] = []
        self._eval_cache_index: dict[str, str] = {}

        self._refresh_integrity_diagnostics()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def data_root(self) -> Path:
        return self._data_root

    @property
    def read_only(self) -> bool:
        return self._read_only

    @property
    def study_id(self) -> str:
        return str(self._manifest["study_id"])

    @property
    def name(self) -> str:
        return str(self._manifest["name"])

    @classmethod
    def create(
        cls,
        path: str | Path,
        *,
        name: str,
        model: str | ModelDescriptorV1,
        adapter: str | AdapterIdentity | None = None,
        parameters: Sequence[ParameterSpec | Mapping[str, Any]] | None = None,
        workbench: Any = None,
        registry: AdapterRegistry | None = None,
    ) -> CalibrationStudyService:
        if not isinstance(name, str) or not name.strip() or "\x00" in name:
            raise MaterialStudyError("INVALID_CONFIGURATION", "create", {"reason": "INVALID_STUDY_NAME"})
        reg = registry or default_registry()
        manifest_path, data_root, data_root_rel = _derive_study_paths(path)
        if manifest_path.exists():
            raise MaterialStudyError(
                "STUDY_ALREADY_EXISTS",
                "create",
                {"path": str(manifest_path)},
            )
        if data_root != manifest_path.parent and data_root.exists():
            raise MaterialStudyError(
                "STUDY_ALREADY_EXISTS",
                "create",
                {"data_root": str(data_root)},
            )

        if isinstance(model, ModelDescriptorV1):
            model_id = model.model_id
        else:
            model_id = str(model)

        adapter_version: str | None = None
        if isinstance(adapter, AdapterIdentity):
            adapter_version = adapter.version
        elif isinstance(adapter, str) and adapter.strip():
            # Allow passing version or adapter id; if matching registered id with single version use default
            try:
                adapter_obj = reg.get(model_id, adapter)
                adapter_version = adapter_obj.identity.version
            except MaterialStudyError:
                default_obj = reg.get(model_id, None)
                if default_obj.identity.id == adapter:
                    adapter_version = default_obj.identity.version
                else:
                    raise

        adapter_obj = reg.get(model_id, adapter_version)
        descriptor = model if isinstance(model, ModelDescriptorV1) else adapter_obj.descriptor()
        param_specs = (
            tuple(_coerce_parameter_spec(p) for p in parameters)
            if parameters is not None
            else descriptor.parameter_specs
        )

        data_root.mkdir(parents=True, exist_ok=True)
        for sub in ("sources", "arrays", "revisions", "evaluations", "holdout-exposures", "exports"):
            (data_root / sub).mkdir(parents=True, exist_ok=True)

        lease = FileLock(data_root / "study.lock", timeout=0, thread_local=False)
        try:
            lease.acquire()
        except Timeout as exc:
            raise MaterialStudyError("STUDY_LOCKED", "create", {"path": str(data_root / "study.lock")}) from exc

        try:
            study_id = uuid.uuid4().hex
            manifest: dict[str, Any] = {
                "schema_version": 1,
                "format": _STUDY_FORMAT,
                "study_id": study_id,
                "name": name,
                "data_root": data_root_rel,
                "model": descriptor.to_dict(),
                "adapter": adapter_obj.identity.to_dict(),
                "parameters": [p.to_dict() for p in param_specs],
                "experiments": [],
                "experiment_data": {},
                "experiment_sources": {},
                "comparisons": [],
                "assessment_policy": {
                    "selection_policy": {
                        "mode": "training_only",
                        "validation_comparison_ids": [],
                    },
                },
                "user_constraints": [],
                "numerical_settings": {},
                "optimization_config": None,
                "revisions": {},
                "evaluations": {},
                "campaigns": {},
                "holdout_exposures": [],
                "exports": [],
                "selected_evaluation_id": None,
                "view_state": {},
                "created_at": utc_now(),
            }
            content_rev = cls._compute_content_revision(manifest)
            manifest["content_revision"] = content_rev
            manifest["revisions"][content_rev] = cls._build_revision_entry(manifest)
            atomic_json(manifest_path, manifest)
            doc_rev = _sha256_file(manifest_path)
            return cls(
                path=manifest_path,
                data_root=data_root,
                data_root_rel=data_root_rel,
                manifest=manifest,
                document_revision=doc_rev,
                read_only=False,
                lease=lease,
                registry=reg,
                workbench=workbench,
            )
        except Exception:
            lease.release()
            raise

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        read_only: bool = False,
        workbench: Any = None,
        registry: AdapterRegistry | None = None,
    ) -> CalibrationStudyService:
        reg = registry or default_registry()
        manifest_path, expected_data_root, expected_rel = _derive_study_paths(path)
        if manifest_path.is_symlink() or (hasattr(manifest_path, "is_junction") and manifest_path.is_junction()):
            raise MaterialStudyError("INVALID_PATH", "open", {"reason": "SYMLINK_MANIFEST_REJECTED"})
        if not manifest_path.is_file():
            raise MaterialStudyError("STUDY_NOT_FOUND", "open", {"path": str(manifest_path)})

        raw_bytes = manifest_path.read_bytes()
        doc_rev = _sha256_bytes(raw_bytes)
        try:
            manifest = json.loads(raw_bytes.decode("utf-8"))
        except Exception as exc:
            raise MaterialStudyError("CORRUPT_MANIFEST", "open", {"error": str(exc)}) from exc

        if not isinstance(manifest, dict) or manifest.get("format") != _STUDY_FORMAT or manifest.get("schema_version") != 1:
            raise MaterialStudyError("CORRUPT_MANIFEST", "open", {"reason": "INVALID_STUDY_FORMAT_OR_VERSION"})

        data_root_rel = manifest.get("data_root")
        if data_root_rel == ".":
            data_root = manifest_path.parent
        else:
            if not isinstance(data_root_rel, str):
                raise MaterialStudyError("CORRUPT_MANIFEST", "open", {"reason": "INVALID_DATA_ROOT"})
            validate_relative_artifact_path(data_root_rel, root=manifest_path.parent)
            data_root = (manifest_path.parent / data_root_rel).resolve()
            if data_root != expected_data_root.resolve():
                raise MaterialStudyError(
                    "CORRUPT_MANIFEST",
                    "open",
                    {"reason": "DATA_ROOT_MISMATCH", "expected": expected_rel, "got": data_root_rel},
                )

        if not data_root.is_dir():
            raise MaterialStudyError("CORRUPT_ARTIFACT", "open", {"reason": "MISSING_DATA_ROOT", "data_root": str(data_root)})
        _check_no_symlinks_under(data_root)

        lease: FileLock | None = None
        if not read_only:
            lease = FileLock(data_root / "study.lock", timeout=0, thread_local=False)
            try:
                lease.acquire()
            except Timeout as exc:
                raise MaterialStudyError("STUDY_LOCKED", "open", {"path": str(data_root / "study.lock")}) from exc

        try:
            return cls(
                path=manifest_path,
                data_root=data_root,
                data_root_rel=str(data_root_rel),
                manifest=manifest,
                document_revision=doc_rev,
                read_only=bool(read_only),
                lease=lease,
                registry=reg,
                workbench=workbench,
            )
        except Exception:
            if lease is not None:
                lease.release()
            raise

    @staticmethod
    def _compute_content_revision(manifest: Mapping[str, Any]) -> str:
        """Compute canonical C2 content_revision excluding selection, view_state, indices, and absolute paths."""
        preimage = {
            "model": manifest["model"],
            "adapter": manifest["adapter"],
            "parameters": manifest["parameters"],
            "experiments": manifest["experiments"],
            "comparisons": manifest["comparisons"],
            "assessment_policy": manifest["assessment_policy"],
            "user_constraints": manifest["user_constraints"],
            "numerical_settings": manifest["numerical_settings"],
            "optimization_config": manifest.get("optimization_config"),
        }
        return canonical_sha256(preimage, exclude_keys=())

    @staticmethod
    def _build_revision_entry(manifest: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "created_at": utc_now(),
            "model": manifest["model"],
            "adapter": manifest["adapter"],
            "parameters": manifest["parameters"],
            "experiments": manifest["experiments"],
            "comparisons": manifest["comparisons"],
            "assessment_policy": manifest["assessment_policy"],
            "user_constraints": manifest["user_constraints"],
            "numerical_settings": manifest["numerical_settings"],
            "optimization_config": manifest.get("optimization_config"),
            "selections": [],
        }

    def _adapter_or_none(self) -> MaterialAdapter | None:
        model_id = self._manifest["model"]["model_id"]
        adapter_ver = self._manifest["adapter"]["version"]
        try:
            return self._registry.get(model_id, adapter_ver)
        except MaterialStudyError:
            return None

    def _require_adapter(self, stage: str) -> MaterialAdapter:
        model_id = self._manifest["model"]["model_id"]
        adapter_ver = self._manifest["adapter"]["version"]
        return self._registry.get(model_id, adapter_ver)

    def _descriptor(self) -> ModelDescriptorV1:
        return ModelDescriptorV1.from_dict(self._manifest["model"])

    def _parameters(self) -> tuple[ParameterSpec, ...]:
        return tuple(ParameterSpec.from_dict(p) for p in self._manifest["parameters"])

    def _experiments(self) -> tuple[ExperimentSpec, ...]:
        return tuple(ExperimentSpec.from_dict(e) for e in self._manifest["experiments"])

    def _experiments_map(self) -> dict[str, ExperimentSpec]:
        return {e.experiment_id: e for e in self._experiments()}

    def _comparisons(self) -> tuple[ComparisonSpec, ...]:
        return tuple(ComparisonSpec.from_dict(c) for c in self._manifest["comparisons"])

    def _collect_artifact_refs(self, manifest: Mapping[str, Any] | None = None) -> list[ArtifactRefV1]:
        m = manifest or self._manifest
        refs: list[ArtifactRefV1] = []
        for exp_raw in m.get("experiments", ()):
            refs.append(ArtifactRefV1.from_dict(exp_raw["source_ref"]))
        for exp_entry in m.get("experiment_data", {}).values():
            for ch_entry in exp_entry.values():
                for key in ("independent_ref", "values_ref", "sample_keys_ref", "mask_ref"):
                    if key in ch_entry:
                        refs.append(ArtifactRefV1.from_dict(ch_entry[key]))
        for exp_src in m.get("experiment_sources", {}).values():
            if "recipe_ref" in exp_src:
                refs.append(ArtifactRefV1.from_dict(exp_src["recipe_ref"]))
        for rev_entry in m.get("revisions", {}).values():
            for sel_ref in rev_entry.get("selections", ()):
                refs.append(ArtifactRefV1.from_dict(sel_ref))
            for k in ("normalization_ref", "assessment_policy_ref", "selection_policy_ref"):
                if k in rev_entry and rev_entry[k] is not None:
                    refs.append(ArtifactRefV1.from_dict(rev_entry[k]))
        for ev_idx in m.get("evaluations", {}).values():
            if ev_idx.get("artifact_ref"):
                refs.append(ArtifactRefV1.from_dict(ev_idx["artifact_ref"]))
            if ev_idx.get("selection_ref"):
                refs.append(ArtifactRefV1.from_dict(ev_idx["selection_ref"]))
        for exp_ref in m.get("holdout_exposures", ()):
            refs.append(ArtifactRefV1.from_dict(exp_ref))
        for export_rec in m.get("exports", ()):
            if export_rec.get("manifest_ref"):
                refs.append(ArtifactRefV1.from_dict(export_rec["manifest_ref"]))
            for art in export_rec.get("artifacts", ()):
                refs.append(ArtifactRefV1.from_dict(art))
        return refs

    def _verify_ref(self, ref: ArtifactRefV1, root: Path) -> str | None:
        try:
            validate_relative_artifact_path(ref.path, root=root)
        except ValueError as exc:
            return f"INVALID_PATH:{ref.path}:{exc}"
        target = (root / ref.path).resolve()
        if not target.is_file():
            return f"MISSING_ARTIFACT:{ref.path}"
        if target.stat().st_size != ref.size:
            return f"SIZE_MISMATCH:{ref.path}"
        if _sha256_file(target) != ref.sha256:
            return f"SHA256_MISMATCH:{ref.path}"
        return None

    def _refresh_integrity_diagnostics(self) -> None:
        corrupt: list[dict[str, Any]] = []
        diags: list[Any] = []
        try:
            _check_no_symlinks_under(self._data_root)
        except MaterialStudyError as exc:
            corrupt.append({"path": str(self._data_root), "reason": exc.code})
            diags.append(exc.to_dict())

        for ref in self._collect_artifact_refs():
            issue = self._verify_ref(ref, self._data_root)
            if issue is not None:
                corrupt.append({"artifact_id": ref.id, "path": ref.path, "issue": issue})
        if corrupt:
            diags.append({"code": "CORRUPT_ARTIFACT", "count": len(corrupt)})

        # Check interrupted campaigns left on disk without re-starting workers
        interrupted = [
            cid
            for cid, c in self._manifest.get("campaigns", {}).items()
            if c.get("state") in ("running", "queued", "preparing")
        ]
        if interrupted:
            diags.append({"code": "INTERRUPTED_CAMPAIGNS_DETECTED", "campaign_ids": interrupted})

        self._corrupt_artifacts = corrupt
        self._diagnostics = diags

    def _check_writable_and_cas(self, expected_revision: str, stage: str) -> None:
        if self._closed:
            raise MaterialStudyError("STUDY_CLOSED", stage, {"reason": "SERVICE_CLOSED"})
        if self._read_only:
            raise MaterialStudyError("READ_ONLY_STUDY", stage, {"reason": "STUDY_OPENED_READ_ONLY"})
        if self._busy_operation is not None or self._active_campaign_id is not None:
            raise MaterialStudyError(
                "STUDY_BUSY",
                stage,
                {
                    "busy_operation": self._busy_operation,
                    "active_campaign_id": self._active_campaign_id,
                    "active_operation_id": self._active_operation_id,
                },
            )
        self._check_disk_cas(expected_revision, stage)

    def _check_disk_cas(self, expected_revision: str | None, stage: str) -> None:
        if not self._path.is_file():
            raise MaterialStudyError("EXTERNAL_MANIFEST_CHANGED", stage, {"reason": "MANIFEST_MISSING_ON_DISK"})
        disk_rev = _sha256_file(self._path)
        if disk_rev != self._document_revision:
            if self._active_cancel_event is not None:
                self._active_cancel_event.set()
            raise MaterialStudyError(
                "EXTERNAL_MANIFEST_CHANGED",
                stage,
                {"expected_in_memory": self._document_revision, "actual_on_disk": disk_rev},
            )
        if expected_revision is not None and expected_revision != self._document_revision:
            raise MaterialStudyError(
                "REVISION_MISMATCH",
                stage,
                {"expected_revision": expected_revision, "document_revision": self._document_revision},
            )

    def _commit_manifest_unlocked(self, new_manifest: dict[str, Any], stage: str) -> str:
        """Write manifest atomically after verifying disk CAS; caller ensures logical ownership."""
        self._check_disk_cas(None, stage)
        atomic_json(self._path, new_manifest)
        new_rev = _sha256_file(self._path)
        self._manifest = new_manifest
        self._document_revision = new_rev
        return new_rev

    def snapshot(self) -> StudySnapshot:
        """Return immutable non-blocking snapshot of current service state (C2/C7)."""
        with self._state_lock:
            evals = self._manifest.get("evaluations", {})
            point_ids = tuple(
                str(v["point_id"])
                for v in evals.values()
                if v.get("point_id") is not None
            )
            exp_ids = tuple(str(e["experiment_id"]) for e in self._manifest.get("experiments", ()))
            return StudySnapshot(
                study_id=self.study_id,
                document_revision=self._document_revision,
                content_revision=str(self._manifest["content_revision"]),
                path=str(self._path),
                data_root=str(self._data_root),
                read_only=self._read_only,
                busy_operation=self._busy_operation,
                active_campaign_id=self._active_campaign_id,
                active_operation_id=self._active_operation_id,
                experiment_ids=exp_ids,
                point_ids=point_ids,
                diagnostics=tuple(self._diagnostics),
            )

    def list_evaluations(self) -> tuple[dict[str, Any], ...]:
        """Return evaluation index metadata without leaking sealed holdout values."""
        with self._state_lock:
            out: list[dict[str, Any]] = []
            for ev_id, entry in self._manifest.get("evaluations", {}).items():
                item = dict(entry)
                if item.get("execution_phase") == "confirmation" and not item.get("holdout_exposed", False):
                    item["fit_loss"] = item.get("fit_loss")
                out.append(item)
            return tuple(out)

    def experiments(self) -> tuple[ExperimentSpec, ...]:
        with self._state_lock:
            return self._experiments()

    def parameters(self) -> tuple[ParameterSpec, ...]:
        with self._state_lock:
            return self._parameters()

    def comparisons(self) -> tuple[ComparisonSpec, ...]:
        with self._state_lock:
            return self._comparisons()

    def assessment_policy(self) -> dict[str, Any]:
        with self._state_lock:
            return json.loads(json.dumps(self._manifest["assessment_policy"]))

    def user_constraints(self) -> tuple[ConstraintSpec, ...]:
        with self._state_lock:
            return tuple(_coerce_constraint_spec(c) for c in self._manifest.get("user_constraints", ()))

    def mandatory_constraints(self) -> tuple[ConstraintSpec, ...]:
        adapter = self._adapter_or_none()
        if adapter is None:
            return ()
        return adapter.capabilities(self._descriptor(), None).optimizer_constraints

    def preview_experiment_csv(self, source: str | Path, *, max_rows: int = 100) -> tuple[list[str], list[list[str]], int]:
        return preview_csv_rows(source, max_rows=max_rows)

    def import_experiment(
        self,
        source: str | Path,
        *,
        channels: Sequence[ExperimentChannelSpec | Mapping[str, Any]],
        column_mapping: Mapping[str, Any] | None = None,
        sequence: Sequence[Mapping[str, Any]] = (),
        conditions: Mapping[str, Any] | None = None,
        split: str = "train",
        replicate_group: str = "",
        expected_revision: str,
        experiment_id: str | None = None,
        unit_specs: Mapping[str, Any] | None = None,
        row_filter: Mapping[str, Any] | None = None,
        source_kind: str = "experiment",
        conversion_recipe: Mapping[str, Any] | None = None,
    ) -> ImportReceipt:
        with self._state_lock:
            self._check_writable_and_cas(expected_revision, "import_experiment")
            if self._corrupt_artifacts:
                raise MaterialStudyError("CORRUPT_ARTIFACT", "import_experiment", {"corrupt": self._corrupt_artifacts})
            existing_ids = {e["experiment_id"] for e in self._manifest["experiments"]}
            exp_id = experiment_id or f"exp-{len(existing_ids) + 1:03d}-{uuid.uuid4().hex[:6]}"
            if exp_id in existing_ids:
                raise MaterialStudyError(
                    "INVALID_CONFIGURATION",
                    "import_experiment",
                    {"reason": "DUPLICATE_EXPERIMENT_ID", "experiment_id": exp_id},
                )
            prev_doc_rev = self._document_revision
            prev_content_rev = str(self._manifest["content_revision"])
            op_id = uuid.uuid4().hex
            self._busy_operation = "import_experiment"
            self._active_operation_id = op_id
            study_id = self.study_id
            data_root = self._data_root

        try:
            imported = import_csv_experiment(
                source,
                study_id=study_id,
                data_root=data_root,
                experiment_id=exp_id,
                channels=channels,
                column_mapping=column_mapping,
                unit_specs=unit_specs,
                sequence=sequence,
                conditions=conditions,
                split=split,
                replicate_group=replicate_group,
                row_filter=row_filter,
                source_kind=source_kind,
                conversion_recipe=conversion_recipe,
            )

            with self._state_lock:
                self._check_disk_cas(prev_doc_rev, "import_experiment")
                new_manifest = json.loads(json.dumps(self._manifest))
                new_manifest["experiments"].append(imported.spec.to_dict())
                new_manifest["experiment_data"][exp_id] = imported.channel_arrays
                new_manifest["experiment_sources"][exp_id] = {
                    "source_id": imported.spec.source_ref.id,
                    "original_path": imported.original_source_path,
                    "recipe_ref": imported.recipe_ref.to_dict(),
                }
                new_content_rev = self._compute_content_revision(new_manifest)
                new_manifest["content_revision"] = new_content_rev
                if new_content_rev not in new_manifest["revisions"]:
                    new_manifest["revisions"][new_content_rev] = self._build_revision_entry(new_manifest)
                new_doc_rev = self._commit_manifest_unlocked(new_manifest, "import_experiment")
        finally:
            with self._state_lock:
                self._busy_operation = None
                self._active_operation_id = None

        return ImportReceipt(
            study_id=study_id,
            previous_document_revision=prev_doc_rev,
            document_revision=new_doc_rev,
            previous_content_revision=prev_content_rev,
            content_revision=new_content_rev,
            experiment_id=exp_id,
            source_ref=imported.spec.source_ref,
            converted_refs=imported.converted_refs,
            warnings=imported.warnings,
        )

    def configure(
        self,
        *,
        parameters: Sequence[ParameterSpec | Mapping[str, Any]],
        comparisons: Sequence[ComparisonSpec | Mapping[str, Any]],
        assessment_policy: Mapping[str, Any] | None = None,
        user_constraints: Sequence[ConstraintSpec | Mapping[str, Any]] = (),
        numerical_settings: Mapping[str, Any] | None = None,
        optimization_config: OptimizationConfig | Mapping[str, Any] | None = None,
        expected_revision: str,
    ) -> StudySnapshot:
        with self._state_lock:
            self._check_writable_and_cas(expected_revision, "configure")
            if self._corrupt_artifacts:
                raise MaterialStudyError("CORRUPT_ARTIFACT", "configure", {"corrupt": self._corrupt_artifacts})
            adapter = self._require_adapter("configure")
            descriptor = self._descriptor()
            cap = adapter.capabilities(descriptor, None)

            param_specs = tuple(_coerce_parameter_spec(p) for p in parameters)
            if len({p.id for p in param_specs}) != len(param_specs):
                raise MaterialStudyError("INVALID_CONFIGURATION", "configure", {"reason": "DUPLICATE_PARAMETER_ID"})
            # Verify parameter expression / defaults / bounds resolve cleanly
            resolve_parameter_values(param_specs)
            build_variable_space(param_specs)

            comp_specs = tuple(_coerce_comparison_spec(c) for c in comparisons)
            if len({c.id for c in comp_specs}) != len(comp_specs):
                raise MaterialStudyError("INVALID_CONFIGURATION", "configure", {"reason": "DUPLICATE_COMPARISON_ID"})

            exp_map = self._experiments_map()
            for comp in comp_specs:
                if comp.experiment_id not in exp_map:
                    raise MaterialStudyError(
                        "INVALID_CONFIGURATION",
                        "configure",
                        {"reason": "UNKNOWN_COMPARISON_EXPERIMENT", "experiment_id": comp.experiment_id},
                    )
                exp = exp_map[comp.experiment_id]
                if comp.split != exp.split:
                    raise MaterialStudyError(
                        "INVALID_CONFIGURATION",
                        "configure",
                        {
                            "reason": "COMPARISON_SPLIT_MISMATCH",
                            "comparison_id": comp.id,
                            "comparison_split": comp.split,
                            "experiment_split": exp.split,
                        },
                    )
                exp_obs_ids = {ch.observable_id for ch in exp.channels}
                if comp.observable_id not in exp_obs_ids:
                    raise MaterialStudyError(
                        "INVALID_CONFIGURATION",
                        "configure",
                        {
                            "reason": "UNKNOWN_COMPARISON_OBSERVABLE",
                            "comparison_id": comp.id,
                            "observable_id": comp.observable_id,
                        },
                    )

            # Explicit C4 selection_policy defaults cleanly to training_only when omitted
            raw_policy = dict(_to_plain_json(assessment_policy or {}))
            raw_sel_policy = raw_policy.get("selection_policy")
            if raw_sel_policy is None:
                sel_mode = "training_only"
                val_comp_ids: list[str] = []
            elif isinstance(raw_sel_policy, Mapping):
                sel_mode = str(raw_sel_policy.get("mode", ""))
                val_ids_raw = raw_sel_policy.get("validation_comparison_ids", [])
                if not isinstance(val_ids_raw, Sequence) or isinstance(val_ids_raw, (str, bytes)):
                    raise MaterialStudyError(
                        "INVALID_CONFIGURATION",
                        "configure",
                        {"reason": "INVALID_VALIDATION_COMPARISON_IDS"},
                    )
                val_comp_ids = [str(x) for x in val_ids_raw]
            else:
                raise MaterialStudyError("INVALID_CONFIGURATION", "configure", {"reason": "INVALID_SELECTION_POLICY"})

            if sel_mode not in _SELECTION_MODES:
                raise MaterialStudyError(
                    "INVALID_CONFIGURATION",
                    "configure",
                    {"reason": "INVALID_SELECTION_MODE", "mode": sel_mode},
                )
            comp_by_id = {c.id: c for c in comp_specs}
            for cid in val_comp_ids:
                if cid not in comp_by_id:
                    raise MaterialStudyError(
                        "INVALID_CONFIGURATION",
                        "configure",
                        {"reason": "UNKNOWN_VALIDATION_COMPARISON_ID", "comparison_id": cid},
                    )
                if comp_by_id[cid].split != "validation":
                    raise MaterialStudyError(
                        "INVALID_CONFIGURATION",
                        "configure",
                        {
                            "reason": "SELECTION_POLICY_ONLY_ALLOWS_VALIDATION_SPLIT",
                            "comparison_id": cid,
                            "split": comp_by_id[cid].split,
                        },
                    )
            if sel_mode == "training_only" and val_comp_ids:
                raise MaterialStudyError(
                    "INVALID_CONFIGURATION",
                    "configure",
                    {"reason": "TRAINING_ONLY_FORBIDS_VALIDATION_SELECTION_IDS"},
                )

            raw_policy["selection_policy"] = {
                "mode": sel_mode,
                "validation_comparison_ids": list(dict.fromkeys(val_comp_ids)),
            }

            # Validate mandatory + user constraints (C5)
            mandatory = cap.optimizer_constraints
            mandatory_ids = {c.id for c in mandatory}
            registered_resp_ids = {r.response_id for r in cap.optimizer_responses} | {"fit_loss"}
            parsed_user_constraints = tuple(_coerce_constraint_spec(c) for c in user_constraints)
            if len({c.id for c in parsed_user_constraints}) != len(parsed_user_constraints):
                raise MaterialStudyError("INVALID_CONFIGURATION", "configure", {"reason": "DUPLICATE_USER_CONSTRAINT_ID"})
            for uc in parsed_user_constraints:
                if uc.id in mandatory_ids:
                    raise MaterialStudyError(
                        "INVALID_CONFIGURATION",
                        "configure",
                        {"reason": "CANNOT_OVERRIDE_MANDATORY_CONSTRAINT", "constraint_id": uc.id},
                    )
                if uc.response_id is not None and uc.response_id not in registered_resp_ids:
                    raise MaterialStudyError(
                        "INVALID_CONFIGURATION",
                        "configure",
                        {"reason": "UNREGISTERED_CONSTRAINT_RESPONSE_ID", "response_id": uc.response_id},
                    )

            num_settings = dict(_to_plain_json(numerical_settings if numerical_settings is not None else self._manifest.get("numerical_settings", {})))
            opt_cfg_dict = _opt_config_to_dict(optimization_config) if optimization_config is not None else self._manifest.get("optimization_config")

            new_manifest = json.loads(json.dumps(self._manifest))
            new_manifest["parameters"] = [p.to_dict() for p in param_specs]
            new_manifest["comparisons"] = [c.to_dict() for c in comp_specs]
            new_manifest["assessment_policy"] = raw_policy
            new_manifest["user_constraints"] = [_constraint_to_dict(c) for c in parsed_user_constraints]
            new_manifest["numerical_settings"] = num_settings
            new_manifest["optimization_config"] = opt_cfg_dict
            new_content_rev = self._compute_content_revision(new_manifest)
            new_manifest["content_revision"] = new_content_rev
            if new_content_rev not in new_manifest["revisions"]:
                new_manifest["revisions"][new_content_rev] = self._build_revision_entry(new_manifest)
            self._commit_manifest_unlocked(new_manifest, "configure")
            return self.snapshot()

    def _planned_experiments_for_phase(
        self,
        *,
        execution_phase: str,
        experiments: Mapping[str, ExperimentSpec],
        comparisons: Sequence[ComparisonSpec],
        assessment_policy: Mapping[str, Any],
    ) -> tuple[ExperimentSpec, ...]:
        """Select ONLY allowed experiments for exploration vs confirmation (C4 Holdout Seal)."""
        ordered_exps = self._experiments()
        if execution_phase == "confirmation":
            return ordered_exps
        sel_policy = assessment_policy.get("selection_policy", {})
        mode = sel_policy.get("mode", "training_only")
        val_comp_ids = set(sel_policy.get("validation_comparison_ids", ()))
        allowed_val_exp_ids = {
            c.experiment_id for c in comparisons if c.id in val_comp_ids and c.split == "validation"
        } if mode == "manual_with_validation" else set()
        return tuple(
            exp
            for exp in ordered_exps
            if exp.split == "train" or (exp.split == "validation" and exp.experiment_id in allowed_val_exp_ids)
        )

    def _write_revision_json_ref(
        self,
        *,
        content_revision: str,
        rel_subpath: str,
        payload: Any,
        artifact_id: str,
        role: str,
    ) -> ArtifactRefV1:
        rel_path = f"revisions/{content_revision}/{rel_subpath}"
        validate_relative_artifact_path(rel_path, root=self._data_root)
        raw = canonical_json_bytes(payload)
        _atomic_write_bytes(self._data_root / rel_path, raw)
        return ArtifactRefV1(
            id=artifact_id,
            role=role,
            path=rel_path,
            sha256=_sha256_bytes(raw),
            size=len(raw),
            format_id="application/json",
            producer=_PRODUCER,
        )

    def _selection_procedure_sha256(
        self,
        *,
        content_revision: str,
        model_revision: str,
        adapter_identity: AdapterIdentity,
        physical_parameters: Mapping[str, Any],
        numerical_settings_sha256: str,
        normalization_sha256: str,
        assessment_policy_sha256: str,
        selection_policy_sha256: str,
    ) -> str:
        return canonical_sha256(
            {
                "content_revision": content_revision,
                "model_revision": model_revision,
                "adapter": adapter_identity.to_dict(),
                "physical_parameters": _to_plain_json(physical_parameters),
                "numerical_settings_sha256": numerical_settings_sha256,
                "normalization_sha256": normalization_sha256,
                "assessment_policy_sha256": assessment_policy_sha256,
                "selection_policy_sha256": selection_policy_sha256,
            },
            exclude_keys=(),
        )

    def _create_selection_record(
        self,
        *,
        content_revision: str,
        model_descriptor: ModelDescriptorV1,
        adapter: MaterialAdapter,
        physical_parameters: Mapping[str, float],
        experiments: Mapping[str, ExperimentSpec],
        experiment_data: Mapping[str, Mapping[str, Any]],
        comparisons: Sequence[ComparisonSpec],
        assessment_policy: Mapping[str, Any],
        numerical_settings: Mapping[str, Any],
        optimization_config: Mapping[str, Any] | None,
    ) -> tuple[SelectionRecordV1, ArtifactRefV1, str]:
        norm_payload = build_normalization_payload(
            experiments=experiments,
            experiment_data=experiment_data,
            comparisons=comparisons,
            data_root=self._data_root,
        )
        norm_ref = self._write_revision_json_ref(
            content_revision=content_revision,
            rel_subpath="normalization.json",
            payload=norm_payload,
            artifact_id=f"norm:{content_revision[:16]}",
            role="normalization_policy",
        )
        ap_ref = self._write_revision_json_ref(
            content_revision=content_revision,
            rel_subpath="assessment_policy.json",
            payload=_to_plain_json(assessment_policy),
            artifact_id=f"ap:{content_revision[:16]}",
            role="assessment_policy",
        )
        sel_policy = assessment_policy.get(
            "selection_policy", {"mode": "training_only", "validation_comparison_ids": []}
        )
        sp_payload = {
            "schema_version": 1,
            "mode": sel_policy.get("mode", "training_only"),
            "validation_comparison_ids": list(sel_policy.get("validation_comparison_ids", ())),
            "optimization_config_sha256": (
                None if optimization_config is None else canonical_sha256(optimization_config, exclude_keys=())
            ),
            "comparisons_sha256": canonical_sha256([c.to_dict() for c in comparisons], exclude_keys=()),
        }
        sp_ref = self._write_revision_json_ref(
            content_revision=content_revision,
            rel_subpath="selection_policy.json",
            payload=sp_payload,
            artifact_id=f"sp:{content_revision[:16]}",
            role="selection_policy",
        )

        param_sha = canonical_sha256(_to_plain_json(physical_parameters), exclude_keys=())
        num_sha = canonical_sha256(_to_plain_json(numerical_settings), exclude_keys=())

        # Matching prior exploration evaluations with identical parameters and content_revision
        matching_eval_ids: list[str] = []
        for ev_id, ev_meta in self._manifest.get("evaluations", {}).items():
            if ev_meta.get("content_revision") != content_revision:
                continue
            if ev_meta.get("execution_phase") != "exploration":
                continue
            if ev_meta.get("state") != "completed":
                continue
            if ev_meta.get("parameter_values_sha256") == param_sha:
                matching_eval_ids.append(ev_id)

        ordered = self._experiments()
        train_revs = tuple(
            {"experiment_id": e.experiment_id, "experiment_revision": e.experiment_revision}
            for e in ordered
            if e.split == "train"
        )
        val_revs = tuple(
            {"experiment_id": e.experiment_id, "experiment_revision": e.experiment_revision}
            for e in ordered
            if e.split == "validation"
        )
        hold_revs = tuple(
            {"experiment_id": e.experiment_id, "experiment_revision": e.experiment_revision}
            for e in ordered
            if e.split == "holdout"
        )

        selection_id = uuid.uuid4().hex
        sel_record = SelectionRecordV1(
            selection_id=selection_id,
            study_id=self.study_id,
            content_revision=content_revision,
            model_revision=model_descriptor.model_revision,
            adapter=adapter.identity,
            physical_parameters=physical_parameters,
            parameter_values_sha256=param_sha,
            numerical_settings_sha256=num_sha,
            normalization_ref=norm_ref,
            assessment_policy_ref=ap_ref,
            selection_policy_ref=sp_ref,
            source_evaluation_ids=tuple(matching_eval_ids),
            training_experiment_revisions=train_revs,
            validation_experiment_revisions=val_revs,
            holdout_experiment_revisions=hold_revs,
            created_at=utc_now(),
        )
        sel_ref = self._write_revision_json_ref(
            content_revision=content_revision,
            rel_subpath=f"selections/{selection_id}.json",
            payload=sel_record.to_dict(),
            artifact_id=f"sel:{selection_id}",
            role="selection_record",
        )
        proc_sha = self._selection_procedure_sha256(
            content_revision=content_revision,
            model_revision=model_descriptor.model_revision,
            adapter_identity=adapter.identity,
            physical_parameters=physical_parameters,
            numerical_settings_sha256=num_sha,
            normalization_sha256=norm_ref.sha256,
            assessment_policy_sha256=ap_ref.sha256,
            selection_policy_sha256=sp_ref.sha256,
        )
        return sel_record, sel_ref, proc_sha

    def _validate_existing_selection_ref(
        self,
        selection_ref: ArtifactRefV1,
        *,
        content_revision: str,
        model_descriptor: ModelDescriptorV1,
        adapter: MaterialAdapter,
        physical_parameters: Mapping[str, float],
        numerical_settings: Mapping[str, Any],
    ) -> tuple[SelectionRecordV1, str]:
        issue = self._verify_ref(selection_ref, self._data_root)
        if issue is not None:
            raise MaterialStudyError("SELECTION_MISMATCH", "evaluate", {"reason": issue})
        raw = json.loads((self._data_root / selection_ref.path).read_text(encoding="utf-8"))
        rec = SelectionRecordV1.from_dict(raw)
        param_sha = canonical_sha256(_to_plain_json(physical_parameters), exclude_keys=())
        num_sha = canonical_sha256(_to_plain_json(numerical_settings), exclude_keys=())
        ordered = self._experiments()
        expected_hold = tuple(
            {"experiment_id": e.experiment_id, "experiment_revision": e.experiment_revision}
            for e in ordered
            if e.split == "holdout"
        )
        if (
            rec.study_id != self.study_id
            or rec.content_revision != content_revision
            or rec.model_revision != model_descriptor.model_revision
            or rec.adapter.id != adapter.identity.id
            or rec.adapter.version != adapter.identity.version
            or rec.parameter_values_sha256 != param_sha
            or rec.numerical_settings_sha256 != num_sha
            or rec.holdout_experiment_revisions != expected_hold
        ):
            raise MaterialStudyError(
                "SELECTION_MISMATCH",
                "evaluate",
                {"reason": "FROZEN_SELECTION_DOES_NOT_MATCH_REQUEST", "selection_id": rec.selection_id},
            )
        proc_sha = self._selection_procedure_sha256(
            content_revision=rec.content_revision,
            model_revision=rec.model_revision,
            adapter_identity=rec.adapter,
            physical_parameters=rec.physical_parameters,
            numerical_settings_sha256=rec.numerical_settings_sha256,
            normalization_sha256=rec.normalization_ref.sha256,
            assessment_policy_sha256=rec.assessment_policy_ref.sha256,
            selection_policy_sha256=rec.selection_policy_ref.sha256,
        )
        return rec, proc_sha

    def _detect_holdout_reuse(
        self,
        *,
        holdout_experiments: Sequence[ExperimentSpec],
        current_proc_sha: str,
    ) -> list[dict[str, Any]]:
        """Check holdout-exposures/*.json to detect reuse of same holdout source under different selection (C4)."""
        if not holdout_experiments:
            return []
        exposures_dir = self._data_root / "holdout-exposures"
        if not exposures_dir.is_dir():
            return []

        prior_by_source_sha: dict[str, str] = {}
        for exp_file in sorted(exposures_dir.glob("*.json")):
            try:
                data = json.loads(exp_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            proc = str(data.get("selection_procedure_sha256", ""))
            for item in data.get("holdout_experiments", ()):
                src_sha = str(item.get("source_sha256", ""))
                if src_sha and proc and proc != current_proc_sha:
                    prior_by_source_sha[src_sha] = proc

        checks: list[dict[str, Any]] = []
        for exp in holdout_experiments:
            if exp.source_ref.sha256 in prior_by_source_sha:
                checks.append(
                    {
                        "code": "HOLDOUT_REUSED_FOR_SELECTION",
                        "experiment_id": exp.experiment_id,
                        "source_sha256": exp.source_ref.sha256,
                        "prior_selection_procedure_sha256": prior_by_source_sha[exp.source_ref.sha256],
                        "current_selection_procedure_sha256": current_proc_sha,
                        "independent_validation_satisfied": False,
                    }
                )
        return checks

    def _record_holdout_exposure(
        self,
        *,
        evaluation_id: str,
        content_revision: str,
        selection_ref: ArtifactRefV1 | None,
        selection_procedure_sha256: str,
        holdout_experiments: Sequence[ExperimentSpec],
    ) -> ArtifactRefV1:
        """Atomically persist holdout exposure event via fsync + os.replace BEFORE exposing holdout data (C4)."""
        event_id = uuid.uuid4().hex
        rel_path = f"holdout-exposures/{event_id}.json"
        validate_relative_artifact_path(rel_path, root=self._data_root)
        target = self._data_root / rel_path
        payload = {
            "schema_version": 1,
            "event_id": event_id,
            "study_id": self.study_id,
            "evaluation_id": evaluation_id,
            "content_revision": content_revision,
            "selection_ref": None if selection_ref is None else selection_ref.to_dict(),
            "selection_procedure_sha256": selection_procedure_sha256,
            "holdout_experiments": [
                {
                    "experiment_id": e.experiment_id,
                    "experiment_revision": e.experiment_revision,
                    "source_sha256": e.source_ref.sha256,
                }
                for e in holdout_experiments
            ],
            "exposed_at": utc_now(),
        }
        try:
            atomic_json(target, payload)
            raw_bytes = target.read_bytes()
        except Exception as exc:
            raise MaterialStudyError(
                "HOLDOUT_EXPOSURE_RECORD_FAILED",
                "expose_holdout",
                {"evaluation_id": evaluation_id, "error": str(exc)},
            ) from exc

        return ArtifactRefV1(
            id=f"exposure:{event_id}",
            role="holdout_exposure",
            path=rel_path,
            sha256=_sha256_bytes(raw_bytes),
            size=len(raw_bytes),
            format_id="application/json",
            producer=_PRODUCER,
        )

    def _has_verified_holdout_exposure(self, evaluation_id: str) -> bool:
        for ref_raw in self._manifest.get("holdout_exposures", ()):
            ref = ArtifactRefV1.from_dict(ref_raw)
            if self._verify_ref(ref, self._data_root) is not None:
                continue
            try:
                data = json.loads((self._data_root / ref.path).read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("evaluation_id") == evaluation_id:
                return True
        return False

    def _lookup_cached_evaluation(self, cache_key: str) -> CalibrationEvaluationArtifact | None:
        with self._state_lock:
            eval_id = self._eval_cache_index.get(cache_key)
            if eval_id is None:
                for eid, entry in reversed(list(self._manifest.get("evaluations", {}).items())):
                    if entry.get("cache_key") == cache_key and entry.get("state") == "completed":
                        eval_id = eid
                        self._eval_cache_index[cache_key] = eid
                        break
            if eval_id is None:
                return None
            ev_entry = self._manifest.get("evaluations", {}).get(eval_id)
            if not ev_entry or not ev_entry.get("artifact_ref"):
                return None
            ref = ArtifactRefV1.from_dict(ev_entry["artifact_ref"])
        if self._verify_ref(ref, self._data_root) is not None:
            return None
        try:
            art_dict = json.loads((self._data_root / ref.path).read_text(encoding="utf-8"))
            art = CalibrationEvaluationArtifact.from_dict(art_dict)
        except Exception:
            return None
        # Verify prediction bundle artifacts still exist intact
        if art.prediction_bundle is not None:
            for a_ref in art.prediction_bundle.artifacts:
                if self._verify_ref(a_ref, self._data_root) is not None:
                    return None
        return art

    def evaluate(
        self,
        values: Mapping[str, Any] | None = None,
        *,
        expected_revision: str,
        on_progress: Callable[[Mapping[str, Any]], None] | None = None,
        cache_policy: str = "reuse",
        execution_phase: str = "exploration",
        selection_ref: ArtifactRefV1 | None = None,
        compute_identifiability: bool = True,
    ) -> EvaluationReceipt:
        if cache_policy not in _CACHE_POLICIES or execution_phase not in _EXECUTION_PHASES:
            raise MaterialStudyError(
                "INVALID_EVALUATION_POLICY",
                "evaluate",
                {"cache_policy": cache_policy, "execution_phase": execution_phase},
            )
        if execution_phase == "confirmation" and cache_policy != "refresh":
            raise MaterialStudyError(
                "CONFIRMATION_REQUIRES_REFRESH",
                "evaluate",
                {"execution_phase": execution_phase, "cache_policy": cache_policy},
            )

        with self._state_lock:
            self._check_writable_and_cas(expected_revision, "evaluate")
            if self._corrupt_artifacts:
                raise MaterialStudyError("CORRUPT_ARTIFACT", "evaluate", {"corrupt": self._corrupt_artifacts})
            adapter = self._require_adapter("evaluate")
            descriptor = self._descriptor()
            cap = adapter.capabilities(descriptor, None)
            if not cap.can_run:
                code = cap.reason_codes[0] if cap.reason_codes else "BACKEND_UNAVAILABLE"
                raise MaterialStudyError(code, "evaluate", {"reason_codes": list(cap.reason_codes)})

            param_specs = self._parameters()
            full_phys = resolve_parameter_values(param_specs, values)
            experiments = self._experiments_map()
            exp_data = json.loads(json.dumps(self._manifest["experiment_data"]))
            comparisons = self._comparisons()
            assessment_policy = json.loads(json.dumps(self._manifest["assessment_policy"]))
            numerical_settings = json.loads(json.dumps(self._manifest["numerical_settings"]))
            opt_config = self._manifest.get("optimization_config")
            user_constraints = tuple(_coerce_constraint_spec(c) for c in self._manifest.get("user_constraints", ()))
            all_constraints = tuple(cap.optimizer_constraints) + user_constraints
            content_rev = str(self._manifest["content_revision"])
            planned_exps = self._planned_experiments_for_phase(
                execution_phase=execution_phase,
                experiments=experiments,
                comparisons=comparisons,
                assessment_policy=assessment_policy,
            )
            holdout_exps = tuple(e for e in planned_exps if e.split == "holdout")

            op_id = uuid.uuid4().hex
            eval_id = f"eval-{uuid.uuid4().hex[:16]}"
            req_id = f"req-{eval_id}"
            cancel_event = threading.Event()
            self._busy_operation = "evaluate"
            self._active_operation_id = op_id
            self._active_campaign_id = None
            self._active_cancel_event = cancel_event
            start_doc_rev = self._document_revision
            study_id = self.study_id
            data_root = self._data_root

        try:
            # Notify started BEFORE executing calculation and OUTSIDE _state_lock (C2).
            if on_progress is not None:
                on_progress(
                    {
                        "stage": "started",
                        "campaign_id": None,
                        "operation_id": op_id,
                        "content_revision": content_rev,
                    }
                )

            # Holdout Seal step 1: atomically freeze SelectionRecordV1 BEFORE any holdout execution!
            proc_sha = ""
            extra_split_checks: list[dict[str, Any]] = []
            if execution_phase == "confirmation":
                if selection_ref is not None:
                    _, proc_sha = self._validate_existing_selection_ref(
                        selection_ref,
                        content_revision=content_rev,
                        model_descriptor=descriptor,
                        adapter=adapter,
                        physical_parameters=full_phys,
                        numerical_settings=numerical_settings,
                    )
                else:
                    _, selection_ref, proc_sha = self._create_selection_record(
                        content_revision=content_rev,
                        model_descriptor=descriptor,
                        adapter=adapter,
                        physical_parameters=full_phys,
                        experiments=experiments,
                        experiment_data=exp_data,
                        comparisons=comparisons,
                        assessment_policy=assessment_policy,
                        numerical_settings=numerical_settings,
                        optimization_config=opt_config,
                    )
                extra_split_checks.extend(
                    self._detect_holdout_reuse(holdout_experiments=holdout_exps, current_proc_sha=proc_sha)
                )
            else:
                selection_ref = None

            cache_key = compute_candidate_cache_key(
                content_revision=content_rev,
                model_revision=descriptor.model_revision,
                adapter_id=adapter.identity.id,
                adapter_version=adapter.identity.version,
                implementation_sha256=adapter.identity.implementation_sha256,
                physical_parameters=full_phys,
                numerical_settings=numerical_settings,
                execution_phase=execution_phase,
                planned_experiments=planned_exps,
            )

            # Reuse cache ONLY in exploration when cache_policy == "reuse"
            if cache_policy == "reuse" and execution_phase == "exploration":
                cached = self._lookup_cached_evaluation(cache_key)
                if cached is not None:
                    with self._state_lock:
                        cached_ref = ArtifactRefV1.from_dict(
                            self._manifest["evaluations"][cached.evaluation_id]["artifact_ref"]
                        )
                        doc_rev_now = self._document_revision
                    return EvaluationReceipt(
                        campaign_id=None,
                        evaluation_id=cached.evaluation_id,
                        document_revision=doc_rev_now,
                        content_revision=content_rev,
                        state=cached.state,
                        artifact_ref=cached_ref,
                        observation={
                            "request_id": req_id,
                            "point_id": None,
                            "responses": dict(cached.optimizer_scalars),
                            "valid": bool(cached.assessment.objective["eligible"]),
                            "cache_hit": True,
                            "source_id": cached.source_id,
                            "run_ids": list(cached.run_ids),
                        },
                    )

            ap_ref = self._write_revision_json_ref(
                content_revision=content_rev,
                rel_subpath="assessment_policy.json",
                payload=_to_plain_json(assessment_policy),
                artifact_id=f"ap:{content_rev[:16]}",
                role="assessment_policy",
            )

            artifact, eval_ref, res_out = execute_single_evaluation(
                evaluation_id=eval_id,
                request_id=req_id,
                campaign_id=None,
                study_id=study_id,
                content_revision=content_rev,
                adapter=adapter,
                model_descriptor=descriptor,
                parameter_specs=param_specs,
                physical_parameters=full_phys,
                experiments=experiments,
                experiment_data=exp_data,
                comparisons=comparisons,
                planned_experiments=planned_exps,
                numerical_settings=numerical_settings,
                execution_phase=execution_phase,
                selection_ref=selection_ref,
                registered_responses=cap.optimizer_responses,
                constraint_specs=all_constraints,
                assessment_policy=assessment_policy,
                policy_ref=ap_ref,
                data_root=data_root,
                cancel_event=cancel_event,
                compute_identifiability=compute_identifiability,
                extra_split_checks=extra_split_checks,
            )

            # Save evaluation index entry (with holdout_exposed=False initially for confirmation)
            param_sha = canonical_sha256(_to_plain_json(full_phys), exclude_keys=())
            with self._state_lock:
                self._check_disk_cas(start_doc_rev, "evaluate")
                new_manifest = json.loads(json.dumps(self._manifest))
                rev_entry = new_manifest["revisions"].setdefault(content_rev, self._build_revision_entry(new_manifest))
                rev_entry["assessment_policy_ref"] = ap_ref.to_dict()
                if selection_ref is not None:
                    existing_sel_paths = {s["path"] for s in rev_entry.get("selections", ())}
                    if selection_ref.path not in existing_sel_paths:
                        rev_entry.setdefault("selections", []).append(selection_ref.to_dict())
                new_manifest["evaluations"][eval_id] = {
                    "evaluation_id": eval_id,
                    "campaign_id": None,
                    "content_revision": content_rev,
                    "state": artifact.state,
                    "execution_phase": execution_phase,
                    "selection_ref": None if selection_ref is None else selection_ref.to_dict(),
                    "artifact_ref": eval_ref.to_dict(),
                    "cache_key": cache_key,
                    "parameter_values_sha256": param_sha,
                    "holdout_exposed": False,
                    "point_id": None,
                    "fit_loss": res_out.fit_loss,
                    "created_at": artifact.created_at,
                }
                if artifact.state == "completed" and res_out.objective_eligible and not (execution_phase == "confirmation" and holdout_exps):
                    new_manifest["selected_evaluation_id"] = eval_id
                    self._eval_cache_index[cache_key] = eval_id
                doc_rev_after = self._commit_manifest_unlocked(new_manifest, "evaluate")

            # Holdout Seal step 2: atomically record holdout exposure BEFORE returning ANY holdout result!
            if execution_phase == "confirmation" and holdout_exps:
                exp_ref = self._record_holdout_exposure(
                    evaluation_id=eval_id,
                    content_revision=content_rev,
                    selection_ref=selection_ref,
                    selection_procedure_sha256=proc_sha,
                    holdout_experiments=holdout_exps,
                )
                with self._state_lock:
                    self._check_disk_cas(doc_rev_after, "evaluate")
                    m2 = json.loads(json.dumps(self._manifest))
                    m2["evaluations"][eval_id]["holdout_exposed"] = True
                    m2.setdefault("holdout_exposures", []).append(exp_ref.to_dict())
                    if artifact.state == "completed" and res_out.objective_eligible:
                        m2["selected_evaluation_id"] = eval_id
                        self._eval_cache_index[cache_key] = eval_id
                    doc_rev_after = self._commit_manifest_unlocked(m2, "evaluate")

            obs_dict = {
                "request_id": req_id,
                "point_id": None,
                "responses": dict(res_out.all_scalars),
                "valid": bool(res_out.objective_eligible and artifact.state == "completed"),
                "reason": res_out.reason_codes[0] if (not res_out.objective_eligible and res_out.reason_codes) else None,
                "cache_hit": False,
                "source_id": artifact.source_id,
                "run_ids": list(artifact.run_ids),
            }
            return EvaluationReceipt(
                campaign_id=None,
                evaluation_id=eval_id,
                document_revision=doc_rev_after,
                content_revision=content_rev,
                state=artifact.state,
                artifact_ref=eval_ref,
                observation=obs_dict,
            )
        finally:
            with self._state_lock:
                self._busy_operation = None
                self._active_operation_id = None
                self._active_cancel_event = None

    def optimize(
        self,
        config: OptimizationConfig | Mapping[str, Any] | None = None,
        *,
        expected_revision: str,
        on_progress: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> CampaignReceipt:
        with self._state_lock:
            self._check_writable_and_cas(expected_revision, "optimize")
            if self._corrupt_artifacts:
                raise MaterialStudyError("CORRUPT_ARTIFACT", "optimize", {"corrupt": self._corrupt_artifacts})
            adapter = self._require_adapter("optimize")
            descriptor = self._descriptor()
            cap = adapter.capabilities(descriptor, None)
            if not cap.can_run:
                code = cap.reason_codes[0] if cap.reason_codes else "BACKEND_UNAVAILABLE"
                raise MaterialStudyError(code, "optimize", {"reason_codes": list(cap.reason_codes)})

            param_specs = self._parameters()
            space = build_variable_space(param_specs)
            if space.dimension == 0:
                raise MaterialStudyError(
                    "INVALID_CONFIGURATION",
                    "optimize",
                    {"reason": "NO_FREE_PARAMETERS"},
                )

            comparisons = self._comparisons()
            fit_comps = [c for c in comparisons if c.required_for_fit]
            if not fit_comps:
                raise MaterialStudyError(
                    "INVALID_CONFIGURATION",
                    "optimize",
                    {"reason": "NO_REQUIRED_FIT_COMPARISONS"},
                )

            if config is None:
                if cap.differentiable:
                    opt_cfg = OptimizationConfig(
                        algorithm="slsqp",
                        seed=17,
                        max_evaluations=1500,
                        max_iterations=200,
                        ftol=1e-10,
                        finite_difference_step=1e-6,
                    )
                else:
                    opt_cfg = OptimizationConfig(
                        algorithm="de",
                        seed=17,
                        max_evaluations=1000,
                        max_iterations=100,
                        population_size=20,
                        max_failures=10,
                        wall_seconds=3600.0,
                    )
            elif isinstance(config, OptimizationConfig):
                opt_cfg = config
            elif isinstance(config, Mapping):
                opt_cfg = OptimizationConfig(**config)
            else:
                raise MaterialStudyError("INVALID_CONFIGURATION", "optimize", {"reason": "INVALID_OPT_CONFIG"})

            experiments = self._experiments_map()
            exp_data = json.loads(json.dumps(self._manifest["experiment_data"]))
            assessment_policy = json.loads(json.dumps(self._manifest["assessment_policy"]))
            numerical_settings = json.loads(json.dumps(self._manifest["numerical_settings"]))
            user_constraints = tuple(_coerce_constraint_spec(c) for c in self._manifest.get("user_constraints", ()))
            all_constraints = tuple(cap.optimizer_constraints) + user_constraints
            response_units = {r.response_id: r.unit for r in cap.optimizer_responses}
            response_units.setdefault("fit_loss", "")
            content_rev = str(self._manifest["content_revision"])

            planned_exps = self._planned_experiments_for_phase(
                execution_phase="exploration",
                experiments=experiments,
                comparisons=comparisons,
                assessment_policy=assessment_policy,
            )

            problem = OptimizationProblem(
                space=space,
                objectives=(ObjectiveSpec(id="fit", response_id="fit_loss", sense="MIN"),),
                constraints=all_constraints,
                mode="ACTUAL",
                revision=content_rev,
                criteria_revision=content_rev,
                differentiable=cap.differentiable,
                response_units=response_units,
            )

            campaign_id = uuid.uuid4().hex
            op_id = uuid.uuid4().hex
            cancel_event = threading.Event()
            stop_event = threading.Event()
            self._busy_operation = "optimize"
            self._active_campaign_id = campaign_id
            self._active_operation_id = op_id
            self._active_cancel_event = cancel_event
            self._active_stop_event = stop_event
            study_id = self.study_id
            data_root = self._data_root
            started_at = utc_now()

        try:
            ap_ref = self._write_revision_json_ref(
                content_revision=content_rev,
                rel_subpath="assessment_policy.json",
                payload=_to_plain_json(assessment_policy),
                artifact_id=f"ap:{content_rev[:16]}",
                role="assessment_policy",
            )

            if on_progress is not None:
                on_progress(
                    {
                        "stage": "started",
                        "campaign_id": campaign_id,
                        "operation_id": op_id,
                        "content_revision": content_rev,
                    }
                )

            recorded_lock = threading.Lock()
            recorded_entries: list[tuple[CalibrationEvaluationArtifact, ArtifactRefV1, str]] = []
            point_to_eval: dict[str, str] = {}

            def _on_eval_recorded(art: CalibrationEvaluationArtifact, ref: ArtifactRefV1, pt_id: str) -> None:
                cache_k = compute_candidate_cache_key(
                    content_revision=content_rev,
                    model_revision=descriptor.model_revision,
                    adapter_id=adapter.identity.id,
                    adapter_version=adapter.identity.version,
                    implementation_sha256=adapter.identity.implementation_sha256,
                    physical_parameters=art.physical_parameters,
                    numerical_settings=numerical_settings,
                    execution_phase="exploration",
                    planned_experiments=planned_exps,
                )
                with recorded_lock:
                    recorded_entries.append((art, ref, cache_k))
                    point_to_eval[pt_id] = art.evaluation_id
                with self._state_lock:
                    # Update lightweight manifest index safely if disk document hasn't been touched externally
                    if _sha256_file(self._path) != self._document_revision:
                        cancel_event.set()
                        return
                    m = json.loads(json.dumps(self._manifest))
                    m["evaluations"][art.evaluation_id] = {
                        "evaluation_id": art.evaluation_id,
                        "campaign_id": campaign_id,
                        "content_revision": content_rev,
                        "state": art.state,
                        "execution_phase": "exploration",
                        "selection_ref": None,
                        "artifact_ref": ref.to_dict(),
                        "cache_key": cache_k,
                        "parameter_values_sha256": canonical_sha256(_to_plain_json(art.physical_parameters), exclude_keys=()),
                        "holdout_exposed": False,
                        "point_id": pt_id,
                        "fit_loss": art.assessment.objective["fit_loss"],
                        "created_at": art.created_at,
                    }
                    if art.state == "completed" and art.assessment.objective["eligible"]:
                        self._eval_cache_index[cache_k] = art.evaluation_id
                    self._commit_manifest_unlocked(m, "optimize.record_eval")

            batch_eval = MaterialBatchEvaluator(
                campaign_id=campaign_id,
                study_id=study_id,
                content_revision=content_rev,
                adapter=adapter,
                model_descriptor=descriptor,
                parameter_specs=param_specs,
                experiments=experiments,
                experiment_data=exp_data,
                comparisons=comparisons,
                planned_experiments=planned_exps,
                numerical_settings=numerical_settings,
                registered_responses=cap.optimizer_responses,
                constraint_specs=all_constraints,
                assessment_policy=assessment_policy,
                policy_ref=ap_ref,
                data_root=data_root,
                cancel_event=cancel_event,
                stop_event=stop_event,
                cache_lookup=self._lookup_cached_evaluation,
                on_evaluation_recorded=_on_eval_recorded,
                on_progress=on_progress,
            )

            opt_result = mdo_optimize(
                problem,
                batch_eval,
                opt_cfg,
                cancel=lambda: cancel_event.is_set() or stop_event.is_set(),
            )

            # Determine CampaignReceipt state & termination_reason per C7
            if cancel_event.is_set():
                final_state = "cancelled"
                term_reason = "USER_CANCEL"
            elif opt_result.termination_reason == "FAILED":
                final_state = "failed"
                term_reason = "FAILED"
            elif stop_event.is_set():
                final_state = "stopped"
                term_reason = "USER_STOP"
            elif opt_result.termination_reason == "NO_VALID_EVALUATIONS":
                final_state = "failed"
                term_reason = "NO_VALID_EVALUATIONS"
            else:
                final_state = "completed"
                term_reason = str(opt_result.termination_reason)

            # Map selected point IDs to evaluation IDs (never fabricating new candidates on stop!)
            selected_eval_ids: list[str] = []
            for pt_id in opt_result.selected_ids:
                if pt_id in point_to_eval:
                    selected_eval_ids.append(point_to_eval[pt_id])

            if not selected_eval_ids and opt_result.actual_archive:
                feasible = [p for p in opt_result.actual_archive if p.feasible]
                if feasible:
                    best_pt = min(feasible, key=lambda p: (float(p.objectives[0]), p.point_id))
                    if best_pt.point_id in point_to_eval:
                        selected_eval_ids.append(point_to_eval[best_pt.point_id])

            # Ensure final selected evaluation updates selected_evaluation_id when not cancelled/failed
            best_selected_eval = selected_eval_ids[0] if selected_eval_ids else None
            budget_dict = {
                "dispatched_requests": opt_result.budget_usage.dispatched_requests,
                "dispatcher_calls": opt_result.budget_usage.dispatcher_calls,
                "cache_hits": opt_result.budget_usage.cache_hits + batch_eval.cache_hits,
                "duplicate_proposals": opt_result.budget_usage.duplicate_proposals,
                "predictions": opt_result.budget_usage.predictions,
                "native_attempts": opt_result.budget_usage.native_attempts,
                "failures": opt_result.budget_usage.failures,
                "elapsed_seconds": opt_result.budget_usage.elapsed_seconds,
            }

            with self._state_lock:
                self._check_disk_cas(None, "optimize.finish")
                m_final = json.loads(json.dumps(self._manifest))
                m_final["campaigns"][campaign_id] = {
                    "campaign_id": campaign_id,
                    "content_revision": content_rev,
                    "state": final_state,
                    "termination_reason": term_reason,
                    "raw_optimizer_termination_reason": opt_result.termination_reason,
                    "evaluation_ids": list(batch_eval.evaluation_ids),
                    "selected_ids": list(selected_eval_ids),
                    "budget_usage": budget_dict,
                    "started_at": started_at,
                    "finished_at": utc_now(),
                }
                if best_selected_eval is not None and final_state != "cancelled":
                    m_final["selected_evaluation_id"] = best_selected_eval
                self._commit_manifest_unlocked(m_final, "optimize.finish")

            return CampaignReceipt(
                campaign_id=campaign_id,
                content_revision=content_rev,
                state=final_state,
                termination_reason=term_reason,
                evaluation_ids=tuple(batch_eval.evaluation_ids),
                selected_ids=tuple(selected_eval_ids),
                budget_usage=budget_dict,
            )
        finally:
            with self._state_lock:
                self._busy_operation = None
                self._active_campaign_id = None
                self._active_operation_id = None
                self._active_cancel_event = None
                self._active_stop_event = None

    def stop(self, campaign_id: str) -> CancelReceipt:
        with self._state_lock:
            if self._active_campaign_id == campaign_id and self._active_stop_event is not None:
                self._active_stop_event.set()
                return CancelReceipt(campaign_id=campaign_id, accepted=True, state="requested")
            past = self._manifest.get("campaigns", {}).get(campaign_id)
            if past is not None:
                return CancelReceipt(campaign_id=campaign_id, accepted=False, state=str(past.get("state", "completed")))
            return CancelReceipt(campaign_id=campaign_id, accepted=False, state="not_active")

    def cancel(self, campaign_id: str) -> CancelReceipt:
        with self._state_lock:
            if self._active_campaign_id == campaign_id and self._active_cancel_event is not None:
                self._active_cancel_event.set()
                return CancelReceipt(campaign_id=campaign_id, accepted=True, state="requested")
            past = self._manifest.get("campaigns", {}).get(campaign_id)
            if past is not None:
                return CancelReceipt(campaign_id=campaign_id, accepted=False, state=str(past.get("state", "completed")))
            return CancelReceipt(campaign_id=campaign_id, accepted=False, state="not_active")

    def evaluation(self, evaluation_id: str) -> CalibrationEvaluationArtifact:
        with self._state_lock:
            ev_entry = self._manifest.get("evaluations", {}).get(evaluation_id)
            if ev_entry is None or not ev_entry.get("artifact_ref"):
                raise MaterialStudyError(
                    "EVALUATION_NOT_FOUND",
                    "evaluation",
                    {"evaluation_id": evaluation_id},
                )
            ref = ArtifactRefV1.from_dict(ev_entry["artifact_ref"])
            data_root = self._data_root
            read_only = self._read_only
            content_rev = str(ev_entry["content_revision"])

        issue = self._verify_ref(ref, data_root)
        if issue is not None:
            raise MaterialStudyError("CORRUPT_ARTIFACT", "evaluation", {"evaluation_id": evaluation_id, "issue": issue})
        raw = json.loads((data_root / ref.path).read_text(encoding="utf-8"))
        art = CalibrationEvaluationArtifact.from_dict(raw)

        # Enforce Holdout Seal BEFORE returning any holdout values/curves/scores (C4)
        holdout_exps = tuple(e for e in self._experiments() if e.split == "holdout")
        has_holdout_data = art.execution_phase == "confirmation" and (
            any(ch.get("split") == "holdout" for ch in art.channel_results)
            or (
                art.prediction_bundle is not None
                and any(ep.experiment_id in {h.experiment_id for h in holdout_exps} for ep in art.prediction_bundle.experiments)
            )
        )
        if has_holdout_data and not self._has_verified_holdout_exposure(evaluation_id):
            if read_only:
                raise MaterialStudyError(
                    "HOLDOUT_EXPOSURE_RECORD_FAILED",
                    "evaluation",
                    {"evaluation_id": evaluation_id, "reason": "READ_ONLY_CANNOT_RECORD_HOLDOUT_EXPOSURE"},
                )
            proc_sha = canonical_sha256(_to_plain_json(art.physical_parameters), exclude_keys=())
            exp_ref = self._record_holdout_exposure(
                evaluation_id=evaluation_id,
                content_revision=content_rev,
                selection_ref=art.selection_ref,
                selection_procedure_sha256=proc_sha,
                holdout_experiments=holdout_exps,
            )
            with self._state_lock:
                self._check_disk_cas(None, "evaluation")
                m = json.loads(json.dumps(self._manifest))
                m["evaluations"][evaluation_id]["holdout_exposed"] = True
                m.setdefault("holdout_exposures", []).append(exp_ref.to_dict())
                self._commit_manifest_unlocked(m, "evaluation")
        return art

    def save(self, *, view_state: Mapping[str, Any] | None = None) -> StudySnapshot:
        with self._state_lock:
            self._check_writable_and_cas(self._document_revision, "save")
            new_manifest = json.loads(json.dumps(self._manifest))
            if view_state is not None:
                new_manifest["view_state"] = dict(_to_plain_json(view_state))
            self._commit_manifest_unlocked(new_manifest, "save")
            return self.snapshot()

    def save_as(
        self,
        destination: str | Path,
        *,
        expected_revision: str,
        on_progress: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> SaveAsReceipt:
        dest_manifest, dest_data_root, dest_data_rel = _derive_study_paths(destination)
        if dest_manifest == self._path or self._data_root in dest_manifest.parents or dest_data_root == self._data_root:
            raise MaterialStudyError(
                "INVALID_PATH",
                "save_as",
                {"reason": "CANNOT_SAVE_AS_INTO_SELF", "destination": str(dest_manifest)},
            )
        if dest_manifest.exists() or (dest_data_root != dest_manifest.parent and dest_data_root.exists()):
            raise MaterialStudyError(
                "DESTINATION_ALREADY_EXISTS",
                "save_as",
                {"destination": str(dest_manifest)},
            )

        with self._state_lock:
            if self._closed:
                raise MaterialStudyError("STUDY_CLOSED", "save_as", {"reason": "SERVICE_CLOSED"})
            if self._busy_operation is not None or self._active_campaign_id is not None:
                raise MaterialStudyError("STUDY_BUSY", "save_as", {"busy_operation": self._busy_operation})
            self._check_disk_cas(expected_revision, "save_as")
            tx_id = uuid.uuid4().hex
            op_id = uuid.uuid4().hex
            self._busy_operation = "save_as"
            self._active_operation_id = op_id
            src_manifest = json.loads(json.dumps(self._manifest))
            src_path = self._path
            src_data_root = self._data_root
            src_read_only = self._read_only
            m0_rev = self._document_revision
            content_rev = str(src_manifest["content_revision"])

        journal_path = dest_manifest.parent / f".caemat-saveas-{tx_id}.json"
        tmp_data_dir = dest_manifest.parent / f".caemat-saveas-{tx_id}-data"
        tmp_manifest_path = dest_manifest.parent / f".caemat-saveas-{tx_id}.manifest.json"
        dest_lease: FileLock | None = None
        committed = False

        try:
            if on_progress is not None:
                on_progress(
                    {
                        "stage": "started",
                        "campaign_id": None,
                        "operation_id": op_id,
                        "content_revision": content_rev,
                    }
                )

            # 1. Verify source integrity & collect all referenced artifacts (excluding *.lock, *.tmp)
            if on_progress is not None:
                on_progress({"stage": "verifying_source", "transaction_id": tx_id, "content_revision": content_rev})
            _check_no_symlinks_under(src_data_root)
            refs = self._collect_artifact_refs(src_manifest)
            for ref in refs:
                issue = self._verify_ref(ref, src_data_root)
                if issue is not None:
                    raise MaterialStudyError("CORRUPT_ARTIFACT", "save_as", {"artifact_id": ref.id, "issue": issue})

            # Inventory every regular non-lock/non-tmp file inside src_data_root
            source_inventory: dict[str, str] = {}
            for item in sorted(src_data_root.rglob("*")):
                if item.name.endswith(".lock") or item.name.endswith(".tmp") or item.name.startswith(".caemat-saveas-"):
                    continue
                if item.is_dir():
                    continue
                rel_posix = item.relative_to(src_data_root).as_posix()
                validate_relative_artifact_path(rel_posix, root=src_data_root)
                source_inventory[rel_posix] = _sha256_file(item)

            # 2. Write staging journal in destination parent (NEVER in original study!)
            if on_progress is not None:
                on_progress({"stage": "staging_copy", "transaction_id": tx_id, "content_revision": content_rev})
            dest_manifest.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(
                journal_path,
                {
                    "schema_version": 1,
                    "transaction_id": tx_id,
                    "state": "staging",
                    "study_id": src_manifest["study_id"],
                    "source_path": str(src_path),
                    "destination_path": str(dest_manifest),
                    "destination_data_root": str(dest_data_root),
                    "temp_data_dir": str(tmp_data_dir),
                    "temp_manifest_path": str(tmp_manifest_path),
                    "expected_artifact_hashes": source_inventory,
                    "created_at": utc_now(),
                },
            )

            # 3. Copy bytes to sibling temp dir and verify hashes
            tmp_data_dir.mkdir(parents=True, exist_ok=False)
            for sub in ("sources", "arrays", "revisions", "evaluations", "holdout-exposures", "exports"):
                (tmp_data_dir / sub).mkdir(parents=True, exist_ok=True)
            copied_count = 0
            for rel_posix, expected_sha in source_inventory.items():
                src_file = src_data_root / rel_posix
                dst_file = tmp_data_dir / rel_posix
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src_file, dst_file)
                if _sha256_file(dst_file) != expected_sha:
                    raise MaterialStudyError(
                        "CORRUPT_ARTIFACT",
                        "save_as",
                        {"reason": "COPIED_FILE_HASH_MISMATCH", "path": rel_posix},
                    )
                copied_count += 1

            dest_manifest_payload = json.loads(json.dumps(src_manifest))
            dest_manifest_payload["data_root"] = dest_data_rel
            atomic_json(tmp_manifest_path, dest_manifest_payload)

            # 4. If read-only source, verify M1 == M0 and all source bytes unchanged during copy (C8)
            if src_read_only:
                if not src_path.is_file() or _sha256_file(src_path) != m0_rev:
                    raise MaterialStudyError(
                        "SOURCE_CHANGED_DURING_COPY",
                        "save_as",
                        {"reason": "MANIFEST_CHANGED_DURING_READONLY_COPY"},
                    )
                for rel_posix, expected_sha in source_inventory.items():
                    cur_file = src_data_root / rel_posix
                    if not cur_file.is_file() or _sha256_file(cur_file) != expected_sha:
                        raise MaterialStudyError(
                            "SOURCE_CHANGED_DURING_COPY",
                            "save_as",
                            {"reason": "SOURCE_FILE_CHANGED_DURING_READONLY_COPY", "path": rel_posix},
                        )

            # 5. Update journal, atomically rename temp data dir -> dest_data_root, acquire dest study.lock, commit manifest
            if on_progress is not None:
                on_progress({"stage": "committing_destination", "transaction_id": tx_id, "content_revision": content_rev})
            atomic_json(
                journal_path,
                {
                    "schema_version": 1,
                    "transaction_id": tx_id,
                    "state": "committing",
                    "study_id": src_manifest["study_id"],
                    "source_path": str(src_path),
                    "destination_path": str(dest_manifest),
                    "destination_data_root": str(dest_data_root),
                    "temp_data_dir": str(tmp_data_dir),
                    "temp_manifest_path": str(tmp_manifest_path),
                    "expected_artifact_hashes": source_inventory,
                    "created_at": utc_now(),
                },
            )
            os.replace(tmp_data_dir, dest_data_root)
            dest_lease = FileLock(dest_data_root / "study.lock", timeout=0, thread_local=False)
            try:
                dest_lease.acquire()
            except Timeout as exc:
                raise MaterialStudyError("STUDY_LOCKED", "save_as", {"path": str(dest_data_root / "study.lock")}) from exc

            for ref in self._collect_artifact_refs(dest_manifest_payload):
                issue = self._verify_ref(ref, dest_data_root)
                if issue is not None:
                    raise MaterialStudyError("CORRUPT_ARTIFACT", "save_as", {"artifact_id": ref.id, "issue": issue})

            os.replace(tmp_manifest_path, dest_manifest)
            new_doc_rev = _sha256_file(dest_manifest)
            committed = True

            # Switch service to committed writable destination and release original lock
            old_lease = self._lease
            with self._state_lock:
                self._path = dest_manifest
                self._data_root = dest_data_root
                self._data_root_rel = dest_data_rel
                self._manifest = dest_manifest_payload
                self._document_revision = new_doc_rev
                self._read_only = False
                self._lease = dest_lease
                dest_lease = None
            if old_lease is not None:
                old_lease.release()

            # 6. Post-commit cleanup; failure does NOT roll back valid committed destination (C8)
            warnings: list[str] = []
            receipt_state = "committed"
            try:
                if on_progress is not None:
                    on_progress({"stage": "cleaning_up", "transaction_id": tx_id, "content_revision": content_rev})
                journal_path.unlink(missing_ok=True)
            except OSError as exc:
                receipt_state = "committed_cleanup_pending"
                warnings.append(f"DESTINATION_COMMITTED_CLEANUP_PENDING: {exc}")

            return SaveAsReceipt(
                transaction_id=tx_id,
                source_path=str(src_path),
                destination_path=str(dest_manifest),
                document_revision=new_doc_rev,
                content_revision=content_rev,
                state=receipt_state,
                copied_artifact_count=copied_count,
                warnings=tuple(warnings),
            )
        except Exception:
            if not committed:
                if dest_lease is not None:
                    dest_lease.release()
                if dest_data_root.exists() and not dest_manifest.exists():
                    shutil.rmtree(dest_data_root, ignore_errors=True)
                shutil.rmtree(tmp_data_dir, ignore_errors=True)
                tmp_manifest_path.unlink(missing_ok=True)
                journal_path.unlink(missing_ok=True)
            raise
        finally:
            with self._state_lock:
                self._busy_operation = None
                self._active_operation_id = None

    def reconcile(self, *, source_policy: str = "inspect") -> ReconcileReport:
        if source_policy != "inspect":
            raise MaterialStudyError("INVALID_CONFIGURATION", "reconcile", {"source_policy": source_policy})
        with self._state_lock:
            doc_rev = self._document_revision
            content_rev = str(self._manifest["content_revision"])
            data_root = self._data_root
            experiments = self._experiments()
            exp_sources = dict(self._manifest.get("experiment_sources", {}))
            refs = self._collect_artifact_refs()

        source_states: dict[str, Any] = {}
        for exp in experiments:
            internal_issue = self._verify_ref(exp.source_ref, data_root)
            ext_meta = exp_sources.get(exp.experiment_id, {})
            ext_path_str = ext_meta.get("original_path")
            ext_status = "unknown"
            if ext_path_str:
                ext_p = Path(ext_path_str)
                if not ext_p.is_file():
                    ext_status = "missing"
                elif _sha256_file(ext_p) == exp.source_ref.sha256:
                    ext_status = "unchanged"
                else:
                    ext_status = "modified"
            source_states[exp.source_ref.id] = {
                "experiment_id": exp.experiment_id,
                "stored_status": "valid" if internal_issue is None else internal_issue,
                "external_path": ext_path_str,
                "external_status": ext_status,
                "sha256": exp.source_ref.sha256,
            }

        artifact_states: dict[str, Any] = {}
        corrupt_count = 0
        for ref in refs:
            issue = self._verify_ref(ref, data_root)
            artifact_states[ref.id] = "valid" if issue is None else issue
            if issue is not None:
                corrupt_count += 1

        disk_changed = (not self._path.is_file()) or (_sha256_file(self._path) != doc_rev)
        applicability = {
            "can_evaluate": corrupt_count == 0 and not disk_changed,
            "corrupt_artifact_count": corrupt_count,
            "external_manifest_changed": disk_changed,
            "external_sources_modified": any(v["external_status"] == "modified" for v in source_states.values()),
        }
        return ReconcileReport(
            document_revision=doc_rev,
            content_revision=content_rev,
            source_states=source_states,
            artifact_states=artifact_states,
            applicability=applicability,
            diagnostics=tuple(self._diagnostics),
        )

    def relink_source(
        self,
        source_id: str,
        new_path: str | Path,
        *,
        expected_revision: str,
    ) -> RelinkReceipt:
        with self._state_lock:
            self._check_writable_and_cas(expected_revision, "relink_source")
            new_p = Path(new_path).resolve()
            if not new_p.is_file():
                raise MaterialStudyError("INVALID_PATH", "relink_source", {"reason": "FILE_NOT_FOUND", "path": str(new_p)})
            new_sha = _sha256_file(new_p)
            new_size = new_p.stat().st_size

            matched_exp_ids: list[str] = []
            expected_sha: str | None = None
            expected_size: int | None = None
            for exp_raw in self._manifest.get("experiments", ()):
                sref = exp_raw["source_ref"]
                if sref["id"] == source_id:
                    matched_exp_ids.append(exp_raw["experiment_id"])
                    expected_sha = sref["sha256"]
                    expected_size = int(sref["size"])

            if not matched_exp_ids or expected_sha is None:
                raise MaterialStudyError("SOURCE_NOT_FOUND", "relink_source", {"source_id": source_id})

            if new_sha != expected_sha or new_size != expected_size:
                raise MaterialStudyError(
                    "RELINK_HASH_MISMATCH",
                    "relink_source",
                    {
                        "source_id": source_id,
                        "expected_sha256": expected_sha,
                        "actual_sha256": new_sha,
                        "reason": "NEW_FILE_BYTES_DIFFER_USE_IMPORT_EXPERIMENT",
                    },
                )

            new_manifest = json.loads(json.dumps(self._manifest))
            old_path = ""
            for eid in matched_exp_ids:
                src_entry = new_manifest["experiment_sources"].setdefault(eid, {})
                if not old_path:
                    old_path = str(src_entry.get("original_path", ""))
                src_entry["original_path"] = str(new_p)
            new_doc_rev = self._commit_manifest_unlocked(new_manifest, "relink_source")
            return RelinkReceipt(
                source_id=source_id,
                old_path=old_path or str(new_p),
                new_path=str(new_p),
                sha256=expected_sha,
                document_revision=new_doc_rev,
                content_revision=str(new_manifest["content_revision"]),
            )

    def inspect_recovery(self, *, destination: str | Path | None = None) -> RecoveryReport:
        search_dirs = [self._path.parent]
        if destination is not None:
            dest_p = Path(destination).resolve()
            parent = dest_p if dest_p.is_dir() else dest_p.parent
            if parent not in search_dirs:
                search_dirs.append(parent)

        transactions: list[dict[str, Any]] = []
        for sdir in search_dirs:
            if not sdir.is_dir():
                continue
            for jpath in sorted(sdir.glob(".caemat-saveas-*.json")):
                try:
                    jdata = json.loads(jpath.read_text(encoding="utf-8"))
                    jdata["journal_path"] = str(jpath)
                    transactions.append(jdata)
                except Exception as exc:
                    transactions.append({"journal_path": str(jpath), "corrupt_journal": str(exc)})

        interrupted = [
            dict(c)
            for c in self._manifest.get("campaigns", {}).values()
            if c.get("state") in ("running", "queued", "preparing", "interrupted")
        ]
        corrupt = list(self._corrupt_artifacts)
        allowed: list[str] = []
        if any(c.get("state") in ("running", "queued", "preparing") for c in interrupted):
            allowed.append("mark_interrupted")
        if transactions:
            allowed.extend(["finish_save_as", "discard_incomplete_copy"])

        return RecoveryReport(
            transactions=tuple(transactions),
            interrupted_campaigns=tuple(interrupted),
            corrupt_artifacts=tuple(corrupt),
            allowed_actions=tuple(dict.fromkeys(allowed)),
            diagnostics=tuple(self._diagnostics),
        )

    def recover(
        self,
        action: str,
        transaction_id: str,
        *,
        expected_revision: str,
        destination: str | Path | None = None,
    ) -> RecoveryReceipt:
        if action not in _RECOVERY_ACTIONS:
            raise MaterialStudyError("INVALID_RECOVERY_ACTION", "recover", {"action": action})

        with self._state_lock:
            self._check_writable_and_cas(expected_revision, "recover")

            if action == "mark_interrupted":
                new_manifest = json.loads(json.dumps(self._manifest))
                updated = 0
                for cid, camp in new_manifest.get("campaigns", {}).items():
                    if camp.get("state") in ("running", "queued", "preparing"):
                        camp["state"] = "interrupted"
                        camp["termination_reason"] = "INTERRUPTED"
                        camp["finished_at"] = utc_now()
                        updated += 1
                new_doc_rev = self._commit_manifest_unlocked(new_manifest, "recover")
                self._refresh_integrity_diagnostics()
                return RecoveryReceipt(
                    transaction_id=transaction_id,
                    action=action,
                    state="interrupted_marked",
                    document_revision=new_doc_rev,
                    diagnostics=({"updated_campaigns": updated},),
                )

            # Locate matching SaveAs transaction journal
            search_dirs = [self._path.parent]
            if destination is not None:
                dest_p = Path(destination).resolve()
                parent = dest_p if dest_p.is_dir() else dest_p.parent
                if parent not in search_dirs:
                    search_dirs.append(parent)

            journal_path: Path | None = None
            journal_data: dict[str, Any] | None = None
            for sdir in search_dirs:
                cand = sdir / f".caemat-saveas-{transaction_id}.json"
                if cand.is_file():
                    journal_path = cand
                    journal_data = json.loads(cand.read_text(encoding="utf-8"))
                    break
            if journal_path is None or journal_data is None or journal_data.get("transaction_id") != transaction_id:
                raise MaterialStudyError(
                    "RECOVERY_TRANSACTION_NOT_FOUND",
                    "recover",
                    {"transaction_id": transaction_id},
                )

            tmp_data = Path(journal_data["temp_data_dir"])
            tmp_manifest = Path(journal_data["temp_manifest_path"])
            dest_manifest = Path(journal_data["destination_path"])
            dest_data = Path(journal_data["destination_data_root"])

            if action == "discard_incomplete_copy":
                shutil.rmtree(tmp_data, ignore_errors=True)
                tmp_manifest.unlink(missing_ok=True)
                if dest_data.exists() and not dest_manifest.exists():
                    shutil.rmtree(dest_data, ignore_errors=True)
                journal_path.unlink(missing_ok=True)
                return RecoveryReceipt(
                    transaction_id=transaction_id,
                    action=action,
                    state="discarded",
                    document_revision=self._document_revision,
                    diagnostics=(),
                )

            # finish_save_as
            expected_hashes: dict[str, str] = dict(journal_data.get("expected_artifact_hashes", {}))
            data_dir = dest_data if dest_data.is_dir() else tmp_data
            if not data_dir.is_dir():
                raise MaterialStudyError("CORRUPT_ARTIFACT", "recover", {"reason": "STAGED_DATA_MISSING"})
            for rel_posix, exp_sha in expected_hashes.items():
                f = data_dir / rel_posix
                if not f.is_file() or _sha256_file(f) != exp_sha:
                    raise MaterialStudyError(
                        "CORRUPT_ARTIFACT",
                        "recover",
                        {"reason": "STAGED_ARTIFACT_CORRUPT", "path": rel_posix},
                    )
            if not dest_data.exists() and tmp_data.is_dir():
                os.replace(tmp_data, dest_data)
            if not dest_manifest.exists() and tmp_manifest.is_file():
                os.replace(tmp_manifest, dest_manifest)
            journal_path.unlink(missing_ok=True)
            return RecoveryReceipt(
                transaction_id=transaction_id,
                action=action,
                state="committed",
                document_revision=self._document_revision,
                diagnostics=({"destination_path": str(dest_manifest)},),
            )

    def export(
        self,
        evaluation_id: str,
        destination: str | Path,
        *,
        expected_revision: str,
    ) -> ExportReceipt:
        with self._state_lock:
            self._check_writable_and_cas(expected_revision, "export")
            adapter = self._require_adapter("export")
            study_id = self.study_id
            data_root = self._data_root
            exps = self._experiments()

        # Calling self.evaluation() enforces Holdout Seal before returning/exporting any holdout metrics!
        art = self.evaluation(evaluation_id)
        eval_payload = art.to_dict()
        eval_payload["sources"] = [
            {"experiment_id": e.experiment_id, "split": e.split, "source_ref": e.source_ref.to_dict()}
            for e in exps
        ]

        dest_p = Path(destination).expanduser().resolve()
        plan = adapter.export(eval_payload, destination=dest_p)

        export_artifacts: list[ArtifactRefV1] = []
        exp_dir_rel = f"exports/{plan.export_id}"
        for idx, file_spec in enumerate(plan.files):
            rel_name = str(file_spec["rel_path"])
            internal_rel = f"{exp_dir_rel}/{rel_name}"
            validate_relative_artifact_path(internal_rel, root=data_root)
            content_bytes = str(file_spec["content_text"]).encode("utf-8")
            _atomic_write_bytes(data_root / internal_rel, content_bytes)
            ref = ArtifactRefV1(
                id=f"export:{plan.export_id}:{idx}",
                role=str(file_spec.get("role", "export_artifact")),
                path=internal_rel,
                sha256=_sha256_bytes(content_bytes),
                size=len(content_bytes),
                format_id=str(file_spec.get("format_id", "application/json")),
                producer=_PRODUCER,
            )
            export_artifacts.append(ref)
            if idx == 0:
                if dest_p.suffix:
                    _atomic_write_bytes(dest_p, content_bytes)
                else:
                    dest_p.mkdir(parents=True, exist_ok=True)
                    _atomic_write_bytes(dest_p / rel_name, content_bytes)
            elif not dest_p.suffix:
                _atomic_write_bytes(dest_p / rel_name, content_bytes)

        manifest_rel = f"{exp_dir_rel}/manifest.json"
        validate_relative_artifact_path(manifest_rel, root=data_root)
        manifest_payload = {
            "schema_version": 1,
            "export_id": plan.export_id,
            "format_kind": plan.format_kind,
            "supports_native_card": plan.supports_native_card,
            "supported_solver_identity": _to_plain_json(plan.supported_solver_identity),
            "study_id": study_id,
            "content_revision": art.content_revision,
            "evaluation_id": evaluation_id,
            "model_id": plan.model_id,
            "model_revision": plan.model_revision,
            "adapter": plan.adapter.to_dict(),
            "formulation": _to_plain_json(plan.formulation),
            "parameters": _to_plain_json(plan.parameters),
            "provenance": _to_plain_json(plan.provenance),
            "assessment_summary": _to_plain_json(plan.assessment_summary),
            "limitations": list(plan.limitations),
            "artifacts": [r.to_dict() for r in export_artifacts],
        }
        m_bytes = canonical_json_bytes(manifest_payload)
        _atomic_write_bytes(data_root / manifest_rel, m_bytes)
        manifest_ref = ArtifactRefV1(
            id=f"export:{plan.export_id}:manifest",
            role="export_manifest",
            path=manifest_rel,
            sha256=_sha256_bytes(m_bytes),
            size=len(m_bytes),
            format_id="application/json",
            producer=_PRODUCER,
        )

        receipt = ExportReceipt(
            export_id=plan.export_id,
            evaluation_id=evaluation_id,
            manifest_ref=manifest_ref,
            artifacts=tuple(export_artifacts),
            supported_solver_identity=plan.supported_solver_identity,
            diagnostics=plan.limitations,
        )
        with self._state_lock:
            self._check_disk_cas(expected_revision, "export")
            new_manifest = json.loads(json.dumps(self._manifest))
            new_manifest.setdefault("exports", []).append(
                {
                    "export_id": receipt.export_id,
                    "evaluation_id": receipt.evaluation_id,
                    "manifest_ref": receipt.manifest_ref.to_dict(),
                    "artifacts": [a.to_dict() for a in receipt.artifacts],
                    "supported_solver_identity": _to_plain_json(receipt.supported_solver_identity),
                    "created_at": utc_now(),
                }
            )
            self._commit_manifest_unlocked(new_manifest, "export")
        return receipt

    def prepare_apply(
        self,
        evaluation_id: str,
        target: Mapping[str, Any],
        *,
        expected_revision: str,
    ) -> ApplyPlan:
        with self._state_lock:
            self._check_writable_and_cas(expected_revision, "prepare_apply")
            adapter = self._require_adapter("prepare_apply")
            cap = adapter.capabilities(self._descriptor(), None)
        if not cap.supports_apply:
            raise MaterialStudyError(
                "UNSUPPORTED_ATOMIC_APPLY",
                "prepare_apply",
                {
                    "model_id": cap.model_id,
                    "adapter_id": cap.adapter.id,
                    "reason": "Adapter declares supports_apply=False",
                },
            )
        raise MaterialStudyError("UNSUPPORTED_ATOMIC_APPLY", "prepare_apply", {"reason": "NOT_IMPLEMENTED"})

    def commit_apply(
        self,
        plan: ApplyPlan,
        *,
        expected_revision: str,
    ) -> ApplyReceipt:
        with self._state_lock:
            self._check_writable_and_cas(expected_revision, "commit_apply")
            adapter = self._require_adapter("commit_apply")
            cap = adapter.capabilities(self._descriptor(), None)
        if not cap.supports_apply or not plan.supported:
            raise MaterialStudyError(
                "UNSUPPORTED_ATOMIC_APPLY",
                "commit_apply",
                {"plan_id": plan.plan_id, "model_id": cap.model_id},
            )
        raise MaterialStudyError("UNSUPPORTED_ATOMIC_APPLY", "commit_apply", {"reason": "NOT_IMPLEMENTED"})

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            cancel_ev = self._active_cancel_event
            lease = self._lease
            self._closed = True
            self._lease = None
        if cancel_ev is not None:
            cancel_ev.set()
        if lease is not None:
            try:
                lease.release()
            except Exception:
                pass

    def __enter__(self) -> CalibrationStudyService:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
