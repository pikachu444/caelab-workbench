"""Pure immutable v1 artifact, result, manifest, and evidence DTOs with strict JSON/array/path validation."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import tempfile
from types import MappingProxyType
from typing import Any

import numpy as np
import numpy.lib.format as npy_format

__all__ = [
    "ArtifactOwner",
    "ArtifactRefV1",
    "DatasetManifestV1",
    "EvidenceEnvelopeV1",
    "NumericResultV1",
    "ProducerIdentity",
    "ResultRefV1",
    "SeriesManifestV1",
    "canonical_array_sha256",
    "canonical_json_bytes",
    "canonical_sha256",
    "read_safe_npy_v2",
    "validate_relative_artifact_path",
    "write_safe_npy_v2",
]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")
_OWNER_SCOPES = frozenset({"run", "operation", "consumer"})
_DATASET_KINDS = frozenset({"field", "history"})
_GEOMETRY_STATES = frozenset({"reference", "deformed", "unknown"})
_EVIDENCE_KINDS = frozenset({"native_run", "manual", "calibration", "document_chunk"})
_SOURCE_KINDS = frozenset({"native_run", "manual", "calibration", "official_source", "paper", "solver_manual"})
_AVAILABILITY_STATES = frozenset({"available", "partial", "source_unavailable", "corrupt", "unsupported"})
_SUPPORTED_NPY_MAGIC = frozenset({(1, 0), (2, 0), (3, 0)})


def _require_nonempty_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or "\x00" in value or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _require_optional_nonempty_str(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _require_nonempty_str(value, name)


def _require_bool(value: Any, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a bool")
    return value


def _require_int(value: Any, name: str, *, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an int (not bool or float)")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _require_finite_float(value: Any, name: str) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.integer, np.floating)):
        raise ValueError(f"{name} must be a finite real number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _require_sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a 64-char lowercase hex SHA256 digest")
    return value


def _require_schema_version_v1(value: Any) -> int:
    if type(value) is not int or value != 1:
        raise ValueError("schema_version must equal integer 1")
    return 1


def _require_exact_keys(
    data: Any,
    required: set[str] | frozenset[str],
    *,
    optional: set[str] | frozenset[str] = frozenset(),
    context: str,
) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError(f"{context} must be a mapping")
    keys = set(data.keys())
    for k in keys:
        if not isinstance(k, str):
            raise ValueError(f"{context} mapping keys must be strings")
    allowed = set(required) | set(optional)
    extra = keys - allowed
    if extra:
        raise ValueError(f"{context} contains unexpected fields: {sorted(extra)}")
    missing = set(required) - keys
    if missing:
        raise ValueError(f"{context} is missing required fields: {sorted(missing)}")
    return data


def _freeze_json_value(value: Any, *, context: str = "value") -> Any:
    """Recursively validate and freeze JSON-compatible data structures."""
    if value is None or type(value) is bool:
        return value
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError(f"{context} must not contain NUL bytes")
        return value
    if isinstance(value, (int, np.integer)) and not isinstance(value, (bool, np.bool_)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        fval = float(value)
        if not math.isfinite(fval):
            raise ValueError(f"{context} must contain only finite numbers")
        return fval
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError(f"{context} mapping keys must be strings")
            frozen[k] = _freeze_json_value(v, context=f"{context}.{k}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item, context=f"{context}[]") for item in value)
    if hasattr(value, "to_dict"):
        return value
    raise ValueError(f"{context} contains unsupported non-JSON value of type {type(value).__name__}")


def _freeze_mapping(mapping: Any, *, context: str) -> MappingProxyType[str, Any]:
    if not isinstance(mapping, Mapping):
        raise ValueError(f"{context} must be a mapping")
    return _freeze_json_value(mapping, context=context)


def _to_plain_json(value: Any) -> Any:
    """Recursively convert frozen DTOs, MappingProxyType, and tuples into plain JSON dicts/lists."""
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _to_plain_json(value.to_dict())
    if value is None or type(value) is bool or isinstance(value, str):
        return value
    if isinstance(value, (int, np.integer)) and not isinstance(value, (bool, np.bool_)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        fval = float(value)
        if not math.isfinite(fval):
            raise ValueError("JSON output cannot contain non-finite floats")
        return fval
    if isinstance(value, Mapping):
        plain: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise ValueError("JSON mapping keys must be strings")
            plain[k] = _to_plain_json(v)
        return plain
    if isinstance(value, (list, tuple)):
        return [_to_plain_json(item) for item in value]
    raise ValueError(f"Unsupported type for JSON export: {type(value).__name__}")


def _require_str_tuple(values: Any, name: str, *, allow_empty_item: bool = False) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, Mapping)) or not isinstance(values, Sequence):
        raise ValueError(f"{name} must be a sequence of strings")
    result: list[str] = []
    for idx, item in enumerate(values):
        if allow_empty_item:
            if not isinstance(item, str) or "\x00" in item:
                raise ValueError(f"{name}[{idx}] must be a valid string")
            result.append(item)
        else:
            result.append(_require_nonempty_str(item, f"{name}[{idx}]"))
    return tuple(result)


def canonical_json_bytes(obj: Any, *, exclude_keys: Sequence[str] = ()) -> bytes:
    """Serialize obj as canonical UTF-8 JSON (sorted keys, compact separators, finite numbers only)."""
    plain = _to_plain_json(obj)
    if exclude_keys:
        excluded = set(exclude_keys)
        if isinstance(plain, dict):
            plain = {k: v for k, v in plain.items() if k not in excluded}
    return json.dumps(
        plain,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(obj: Any, *, exclude_keys: Sequence[str] = ("content_sha256",)) -> str:
    """Return lowercase SHA-256 hex digest of canonical JSON bytes."""
    return hashlib.sha256(canonical_json_bytes(obj, exclude_keys=exclude_keys)).hexdigest()


def _check_not_reparse_or_symlink(candidate: Path, display_path: str) -> None:
    try:
        st = os.lstat(candidate)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ValueError(f"Cannot inspect artifact path {display_path!r}: {exc}") from exc

    if stat.S_ISLNK(st.st_mode) or candidate.is_symlink():
        raise ValueError(f"Symlink component rejected in artifact path: {display_path!r}")
    if hasattr(candidate, "is_junction") and candidate.is_junction():
        raise ValueError(f"Junction component rejected in artifact path: {display_path!r}")
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attrs = getattr(st, "st_file_attributes", 0)
    if attrs & reparse_flag:
        raise ValueError(f"Windows reparse point rejected in artifact path: {display_path!r}")


def validate_relative_artifact_path(path: str, root: Path | None = None) -> str:
    """Validate a strict POSIX relative artifact path and optional root containment without reparse escape."""
    if not isinstance(path, str) or not path or not path.strip():
        raise ValueError("Artifact path must be a nonempty POSIX relative path")
    if "\x00" in path:
        raise ValueError("Artifact path must not contain NUL bytes")
    if "\\" in path:
        raise ValueError(f"Artifact path must use POSIX '/' separators, not '\\': {path!r}")
    if path.startswith("/") or path.endswith("/") or "//" in path:
        raise ValueError(f"Artifact path must not start/end with '/' or contain '//': {path!r}")
    if _DRIVE_PREFIX_RE.match(path):
        raise ValueError(f"Artifact path must not contain a drive prefix: {path!r}")

    win_path = PureWindowsPath(path)
    posix_path = PurePosixPath(path)
    if win_path.drive or win_path.root or win_path.is_absolute() or posix_path.is_absolute():
        raise ValueError(f"Artifact path must be relative, not absolute/UNC: {path!r}")

    parts = path.split("/")
    for part in parts:
        if not part or part in (".", ".."):
            raise ValueError(f"Artifact path contains invalid segment {part!r}: {path!r}")

    if root is not None:
        root_path = Path(root)
        root_resolved = root_path.resolve()
        cur_raw = root_path
        cur_res = root_resolved
        for part in parts:
            cur_raw = cur_raw / part
            cur_res = cur_res / part
            _check_not_reparse_or_symlink(cur_raw, path)
            if cur_res != cur_raw:
                _check_not_reparse_or_symlink(cur_res, path)
        target_resolved = cur_raw.resolve()
        if not target_resolved.is_relative_to(root_resolved):
            raise ValueError(f"Artifact path escapes root {str(root_resolved)!r}: {path!r}")

    return path


def _reject_mixed_bool_sequence(seq: Any) -> None:
    if isinstance(seq, (str, bytes, Mapping)):
        raise ValueError("Array cannot be constructed from string, bytes, or mapping")
    if isinstance(seq, np.ndarray):
        return
    if isinstance(seq, Sequence):
        has_bool = False
        has_nonbool_num = False
        stack = [seq]
        while stack:
            cur = stack.pop()
            for item in cur:
                if isinstance(item, (list, tuple)):
                    stack.append(item)
                elif isinstance(item, (bool, np.bool_)):
                    has_bool = True
                elif isinstance(item, (int, float, np.integer, np.floating)):
                    has_nonbool_num = True
                else:
                    raise ValueError(f"Unsupported array element type: {type(item).__name__}")
        if has_bool and has_nonbool_num:
            raise ValueError("Cannot mix bool and int/float in canonical numeric array")


def _canonicalize_ndarray(arr: Any) -> np.ndarray:
    if not isinstance(arr, np.ndarray):
        _reject_mixed_bool_sequence(arr)
        try:
            arr = np.asarray(arr)
        except Exception as exc:
            raise ValueError(f"Invalid or ragged array input: {exc}") from exc

    dt = arr.dtype
    if dt.kind == "O":
        raise ValueError("Object arrays are forbidden")
    if dt.kind == "c":
        raise ValueError("Complex arrays are forbidden in canonical v1 array format")
    if dt.kind == "f" and dt.itemsize == 8:
        target_dtype = np.dtype("<f8")
    elif dt.kind == "i" and dt.itemsize == 8:
        target_dtype = np.dtype("<i8")
    elif dt.kind == "b" and dt.itemsize == 1:
        target_dtype = np.dtype("|b1")
    else:
        raise ValueError(f"Unsupported canonical array dtype {dt!r}; only <f8, <i8, |b1 allowed")

    canonical = np.ascontiguousarray(arr, dtype=target_dtype)
    if target_dtype.kind == "f" and not np.isfinite(canonical).all():
        raise ValueError("Floating array must contain only finite entries (no NaN/Inf)")
    return canonical


def canonical_array_sha256(arr: Any) -> str:
    """Hash canonical header JSON + LF + C-contiguous little-endian array bytes."""
    canonical = _canonicalize_ndarray(arr)
    header = json.dumps(
        {"dtype": canonical.dtype.str, "order": "C", "shape": list(canonical.shape)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(header)
    digest.update(b"\n")
    digest.update(canonical.tobytes(order="C"))
    return digest.hexdigest()


def _resolve_npy_path(path: str | Path, root: Path | None) -> Path:
    if isinstance(path, str):
        if "\x00" in path:
            raise ValueError("Path must not contain NUL bytes")
        if root is not None:
            rel = validate_relative_artifact_path(path, root=root)
            return Path(root).resolve() / PurePosixPath(rel)
        p = Path(path)
    elif isinstance(path, Path):
        if "\x00" in str(path):
            raise ValueError("Path must not contain NUL bytes")
        if root is not None:
            root_path = Path(root)
            root_resolved = root_path.resolve()
            if path.is_absolute():
                try:
                    rel_part = path.relative_to(root_path)
                    rel_posix = rel_part.as_posix()
                except ValueError:
                    resolved = path.resolve()
                    if not resolved.is_relative_to(root_resolved):
                        raise ValueError(f"Path {str(path)!r} escapes root {str(root_resolved)!r}") from None
                    rel_posix = resolved.relative_to(root_resolved).as_posix()
                validate_relative_artifact_path(rel_posix, root=root_path)
                return root_resolved / PurePosixPath(rel_posix)
            rel_posix = path.as_posix()
            validate_relative_artifact_path(rel_posix, root=root_path)
            return root_resolved / PurePosixPath(rel_posix)
        p = path
    else:
        raise ValueError("path must be a str or pathlib.Path")

    if not p.is_absolute():
        if ".." in p.parts:
            raise ValueError(f"Relative path must not contain '..': {str(p)!r}")
        cur = Path()
        for part in p.parts:
            cur = cur / part
            _check_not_reparse_or_symlink(cur, str(p))
    else:
        _check_not_reparse_or_symlink(p, str(p))
    return p


def write_safe_npy_v2(path: str | Path, arr: Any, *, root: Path | None = None) -> str:
    """Validate array & path, atomically write .npy format (2, 0) with allow_pickle=False, return file SHA256."""
    target = _resolve_npy_path(path, root)
    canonical = _canonicalize_ndarray(arr)

    buf = io.BytesIO()
    npy_format.write_array(buf, canonical, version=(2, 0), allow_pickle=False)
    payload = buf.getvalue()
    file_sha256 = hashlib.sha256(payload).hexdigest()

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
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    return file_sha256


def read_safe_npy_v2(path: str | Path, *, root: Path | None = None) -> np.ndarray:
    """Safely load a .npy array with allow_pickle=False, strict dtype (<f8/<i8/|b1), and finiteness checks."""
    target = _resolve_npy_path(path, root)
    if not target.is_file():
        raise ValueError(f"Artifact .npy file does not exist or is not a regular file: {str(target)!r}")

    with target.open("rb") as stream:
        version = npy_format.read_magic(stream)
        if version not in _SUPPORTED_NPY_MAGIC:
            raise ValueError(f"Unsupported .npy format version: {version!r}")
        stream.seek(0)
        arr = npy_format.read_array(stream, allow_pickle=False)

    return _canonicalize_ndarray(arr)


@dataclass(frozen=True)
class ProducerIdentity:
    """Identity of the producer that generated an artifact or evidence payload."""

    id: str
    version: str

    def __post_init__(self) -> None:
        _require_nonempty_str(self.id, "producer.id")
        _require_nonempty_str(self.version, "producer.version")

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "version": self.version}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProducerIdentity:
        m = _require_exact_keys(data, {"id", "version"}, context="ProducerIdentity")
        return cls(id=m["id"], version=m["version"])


@dataclass(frozen=True)
class ArtifactOwner:
    """Ownership context binding an artifact ID to run, operation, or consumer scope."""

    scope: str
    id: str
    source_run_id: str | None = None

    def __post_init__(self) -> None:
        if self.scope not in _OWNER_SCOPES:
            raise ValueError(f"ArtifactOwner.scope must be one of {sorted(_OWNER_SCOPES)}, got {self.scope!r}")
        _require_nonempty_str(self.id, "ArtifactOwner.id")
        if self.scope == "operation":
            _require_nonempty_str(self.source_run_id, "ArtifactOwner.source_run_id")
        elif self.source_run_id is not None:
            _require_nonempty_str(self.source_run_id, "ArtifactOwner.source_run_id")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"scope": self.scope, "id": self.id}
        if self.source_run_id is not None:
            out["source_run_id"] = self.source_run_id
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ArtifactOwner:
        m = _require_exact_keys(data, {"scope", "id"}, optional={"source_run_id"}, context="ArtifactOwner")
        return cls(scope=m["scope"], id=m["id"], source_run_id=m.get("source_run_id"))


@dataclass(frozen=True)
class ArtifactRefV1:
    """Immutable v1 reference to a stored artifact within an owner-scoped POSIX relative path."""

    id: str
    role: str
    path: str
    sha256: str
    size: int
    format_id: str
    producer: ProducerIdentity
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.id, "ArtifactRefV1.id")
        _require_nonempty_str(self.role, "ArtifactRefV1.role")
        validate_relative_artifact_path(self.path)
        _require_sha256(self.sha256, "ArtifactRefV1.sha256")
        _require_int(self.size, "ArtifactRefV1.size", minimum=0)
        _require_nonempty_str(self.format_id, "ArtifactRefV1.format_id")
        if not isinstance(self.producer, ProducerIdentity):
            raise ValueError("ArtifactRefV1.producer must be a ProducerIdentity")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "role": self.role,
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "format_id": self.format_id,
            "producer": self.producer.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ArtifactRefV1:
        m = _require_exact_keys(
            data,
            {"schema_version", "id", "role", "path", "sha256", "size", "format_id", "producer"},
            context="ArtifactRefV1",
        )
        _require_schema_version_v1(m["schema_version"])
        return cls(
            schema_version=1,
            id=m["id"],
            role=m["role"],
            path=m["path"],
            sha256=m["sha256"],
            size=m["size"],
            format_id=m["format_id"],
            producer=ProducerIdentity.from_dict(m["producer"]),
        )


@dataclass(frozen=True)
class ResultRefV1:
    """Canonical immutable v1 reference to a native solver result field, selection, and operator."""

    source_run_id: str
    source_node_id: str
    source_receipt_sha256: str
    source_receipt_ref: ArtifactRefV1
    operation_id: str
    operation_manifest_sha256: str
    dataset_id: str
    dataset_manifest_sha256: str
    source_artifacts: tuple[ArtifactRefV1, ...]
    frame_id: str | None
    field_id: str
    component: str | None
    selection: Mapping[str, Any]
    location: Mapping[str, Any]
    basis: Mapping[str, Any]
    measure: str
    shear_convention: str
    units: Mapping[str, Any]
    geometry_state: str
    operator: Mapping[str, Any]
    coverage: Mapping[str, Any]
    eligibility: Mapping[str, Any]
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.source_run_id, "ResultRefV1.source_run_id")
        _require_nonempty_str(self.source_node_id, "ResultRefV1.source_node_id")
        _require_sha256(self.source_receipt_sha256, "ResultRefV1.source_receipt_sha256")
        if not isinstance(self.source_receipt_ref, ArtifactRefV1):
            raise ValueError("ResultRefV1.source_receipt_ref must be an ArtifactRefV1")
        if self.source_receipt_sha256 != self.source_receipt_ref.sha256:
            raise ValueError("ResultRefV1.source_receipt_sha256 must match source_receipt_ref.sha256")
        _require_nonempty_str(self.operation_id, "ResultRefV1.operation_id")
        _require_sha256(self.operation_manifest_sha256, "ResultRefV1.operation_manifest_sha256")
        _require_nonempty_str(self.dataset_id, "ResultRefV1.dataset_id")
        _require_sha256(self.dataset_manifest_sha256, "ResultRefV1.dataset_manifest_sha256")

        if isinstance(self.source_artifacts, (str, bytes, Mapping)) or not isinstance(self.source_artifacts, Sequence):
            raise ValueError("ResultRefV1.source_artifacts must be a sequence of ArtifactRefV1")
        artifacts = tuple(self.source_artifacts)
        for idx, item in enumerate(artifacts):
            if not isinstance(item, ArtifactRefV1):
                raise ValueError(f"ResultRefV1.source_artifacts[{idx}] must be an ArtifactRefV1")
        object.__setattr__(self, "source_artifacts", artifacts)

        _require_optional_nonempty_str(self.frame_id, "ResultRefV1.frame_id")
        _require_nonempty_str(self.field_id, "ResultRefV1.field_id")
        _require_optional_nonempty_str(self.component, "ResultRefV1.component")
        _require_nonempty_str(self.measure, "ResultRefV1.measure")
        _require_nonempty_str(self.shear_convention, "ResultRefV1.shear_convention")
        if self.geometry_state not in _GEOMETRY_STATES:
            raise ValueError(
                f"ResultRefV1.geometry_state must be one of {sorted(_GEOMETRY_STATES)}, got {self.geometry_state!r}"
            )

        for map_field in ("selection", "location", "basis", "units", "operator", "coverage", "eligibility"):
            frozen = _freeze_mapping(getattr(self, map_field), context=f"ResultRefV1.{map_field}")
            object.__setattr__(self, map_field, frozen)

    def with_operator(self, operator: Mapping[str, Any]) -> ResultRefV1:
        """Return a new ResultRefV1 with updated operator without mutating self."""
        return replace(self, operator=_freeze_mapping(operator, context="ResultRefV1.operator"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_run_id": self.source_run_id,
            "source_node_id": self.source_node_id,
            "source_receipt_sha256": self.source_receipt_sha256,
            "source_receipt_ref": self.source_receipt_ref.to_dict(),
            "operation_id": self.operation_id,
            "operation_manifest_sha256": self.operation_manifest_sha256,
            "dataset_id": self.dataset_id,
            "dataset_manifest_sha256": self.dataset_manifest_sha256,
            "source_artifacts": [item.to_dict() for item in self.source_artifacts],
            "frame_id": self.frame_id,
            "field_id": self.field_id,
            "component": self.component,
            "selection": _to_plain_json(self.selection),
            "location": _to_plain_json(self.location),
            "basis": _to_plain_json(self.basis),
            "measure": self.measure,
            "shear_convention": self.shear_convention,
            "units": _to_plain_json(self.units),
            "geometry_state": self.geometry_state,
            "operator": _to_plain_json(self.operator),
            "coverage": _to_plain_json(self.coverage),
            "eligibility": _to_plain_json(self.eligibility),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ResultRefV1:
        required = {
            "schema_version",
            "source_run_id",
            "source_node_id",
            "source_receipt_sha256",
            "source_receipt_ref",
            "operation_id",
            "operation_manifest_sha256",
            "dataset_id",
            "dataset_manifest_sha256",
            "source_artifacts",
            "frame_id",
            "field_id",
            "component",
            "selection",
            "location",
            "basis",
            "measure",
            "shear_convention",
            "units",
            "geometry_state",
            "operator",
            "coverage",
            "eligibility",
        }
        m = _require_exact_keys(data, required, context="ResultRefV1")
        _require_schema_version_v1(m["schema_version"])
        raw_artifacts = m["source_artifacts"]
        if isinstance(raw_artifacts, (str, bytes, Mapping)) or not isinstance(raw_artifacts, Sequence):
            raise ValueError("ResultRefV1.source_artifacts must be a list")
        return cls(
            schema_version=1,
            source_run_id=m["source_run_id"],
            source_node_id=m["source_node_id"],
            source_receipt_sha256=m["source_receipt_sha256"],
            source_receipt_ref=ArtifactRefV1.from_dict(m["source_receipt_ref"]),
            operation_id=m["operation_id"],
            operation_manifest_sha256=m["operation_manifest_sha256"],
            dataset_id=m["dataset_id"],
            dataset_manifest_sha256=m["dataset_manifest_sha256"],
            source_artifacts=tuple(ArtifactRefV1.from_dict(item) for item in raw_artifacts),
            frame_id=m["frame_id"],
            field_id=m["field_id"],
            component=m["component"],
            selection=m["selection"],
            location=m["location"],
            basis=m["basis"],
            measure=m["measure"],
            shear_convention=m["shear_convention"],
            units=m["units"],
            geometry_state=m["geometry_state"],
            operator=m["operator"],
            coverage=m["coverage"],
            eligibility=m["eligibility"],
        )


@dataclass(frozen=True)
class SeriesManifestV1:
    """Time-history series metadata inside a DatasetManifestV1."""

    series_id: str
    coordinate_ref: ArtifactRefV1
    row_count: int
    source_row_index_ref: ArtifactRefV1
    field_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_nonempty_str(self.series_id, "SeriesManifestV1.series_id")
        if not isinstance(self.coordinate_ref, ArtifactRefV1):
            raise ValueError("SeriesManifestV1.coordinate_ref must be an ArtifactRefV1")
        _require_int(self.row_count, "SeriesManifestV1.row_count", minimum=0)
        if not isinstance(self.source_row_index_ref, ArtifactRefV1):
            raise ValueError("SeriesManifestV1.source_row_index_ref must be an ArtifactRefV1")
        field_ids = _require_str_tuple(self.field_ids, "SeriesManifestV1.field_ids")
        if len(set(field_ids)) != len(field_ids):
            raise ValueError("SeriesManifestV1.field_ids must not contain duplicates")
        object.__setattr__(self, "field_ids", field_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "coordinate_ref": self.coordinate_ref.to_dict(),
            "row_count": self.row_count,
            "source_row_index_ref": self.source_row_index_ref.to_dict(),
            "field_ids": list(self.field_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SeriesManifestV1:
        m = _require_exact_keys(
            data,
            {"series_id", "coordinate_ref", "row_count", "source_row_index_ref", "field_ids"},
            context="SeriesManifestV1",
        )
        return cls(
            series_id=m["series_id"],
            coordinate_ref=ArtifactRefV1.from_dict(m["coordinate_ref"]),
            row_count=m["row_count"],
            source_row_index_ref=ArtifactRefV1.from_dict(m["source_row_index_ref"]),
            field_ids=tuple(m["field_ids"]) if isinstance(m["field_ids"], Sequence) and not isinstance(m["field_ids"], (str, bytes)) else m["field_ids"],
        )


@dataclass(frozen=True)
class DatasetManifestV1:
    """Dataset manifest separating spatial field frames from time-history series."""

    dataset_id: str
    dataset_kind: str
    frames: tuple[Mapping[str, Any], ...] = ()
    series: tuple[SeriesManifestV1, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.dataset_id, "DatasetManifestV1.dataset_id")
        if self.dataset_kind not in _DATASET_KINDS:
            raise ValueError(
                f"DatasetManifestV1.dataset_kind must be one of {sorted(_DATASET_KINDS)}, got {self.dataset_kind!r}"
            )
        if isinstance(self.frames, (str, bytes, Mapping)) or not isinstance(self.frames, Sequence):
            raise ValueError("DatasetManifestV1.frames must be a sequence")
        if isinstance(self.series, (str, bytes, Mapping)) or not isinstance(self.series, Sequence):
            raise ValueError("DatasetManifestV1.series must be a sequence")

        frozen_frames = tuple(_freeze_mapping(f, context="DatasetManifestV1.frames[]") for f in self.frames)
        series_tuple = tuple(self.series)
        for idx, s in enumerate(series_tuple):
            if not isinstance(s, SeriesManifestV1):
                raise ValueError(f"DatasetManifestV1.series[{idx}] must be a SeriesManifestV1")

        if self.dataset_kind == "field" and series_tuple:
            raise ValueError("DatasetManifestV1 with dataset_kind='field' must have empty series")
        if self.dataset_kind == "history" and frozen_frames:
            raise ValueError("DatasetManifestV1 with dataset_kind='history' must have empty frames")

        object.__setattr__(self, "frames", frozen_frames)
        object.__setattr__(self, "series", series_tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "dataset_kind": self.dataset_kind,
            "frames": [_to_plain_json(f) for f in self.frames],
            "series": [s.to_dict() for s in self.series],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DatasetManifestV1:
        m = _require_exact_keys(
            data,
            {"schema_version", "dataset_id", "dataset_kind", "frames", "series"},
            context="DatasetManifestV1",
        )
        _require_schema_version_v1(m["schema_version"])
        raw_frames = m["frames"]
        raw_series = m["series"]
        if isinstance(raw_frames, (str, bytes, Mapping)) or not isinstance(raw_frames, Sequence):
            raise ValueError("DatasetManifestV1.frames must be a list")
        if isinstance(raw_series, (str, bytes, Mapping)) or not isinstance(raw_series, Sequence):
            raise ValueError("DatasetManifestV1.series must be a list")
        return cls(
            schema_version=1,
            dataset_id=m["dataset_id"],
            dataset_kind=m["dataset_kind"],
            frames=tuple(raw_frames),
            series=tuple(SeriesManifestV1.from_dict(item) for item in raw_series),
        )


@dataclass(frozen=True)
class NumericResultV1:
    """Validated numeric query payload bound to an immutable ResultRefV1."""

    ref: ResultRefV1
    values: tuple[float | None, ...] | None
    values_ref: ArtifactRefV1 | None
    sample_coordinates: tuple[float, ...]
    counts: Mapping[str, Any]
    status: str
    reason_codes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    operator_provenance: Mapping[str, Any] = MappingProxyType({})
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        if not isinstance(self.ref, ResultRefV1):
            raise ValueError("NumericResultV1.ref must be a ResultRefV1")

        if self.values is not None:
            if isinstance(self.values, (str, bytes, Mapping)) or not isinstance(self.values, Sequence):
                raise ValueError("NumericResultV1.values must be None or a sequence of finite floats/None")
            norm_vals = tuple(
                None if item is None else _require_finite_float(item, "NumericResultV1.values[]")
                for item in self.values
            )
            object.__setattr__(self, "values", norm_vals)

        if self.values_ref is not None and not isinstance(self.values_ref, ArtifactRefV1):
            raise ValueError("NumericResultV1.values_ref must be None or an ArtifactRefV1")

        if isinstance(self.sample_coordinates, (str, bytes, Mapping)) or not isinstance(
            self.sample_coordinates, Sequence
        ):
            raise ValueError("NumericResultV1.sample_coordinates must be a sequence of finite floats")
        coords = tuple(
            _require_finite_float(item, "NumericResultV1.sample_coordinates[]")
            for item in self.sample_coordinates
        )
        object.__setattr__(self, "sample_coordinates", coords)

        object.__setattr__(self, "counts", _freeze_mapping(self.counts, context="NumericResultV1.counts"))
        _require_nonempty_str(self.status, "NumericResultV1.status")
        object.__setattr__(self, "reason_codes", _require_str_tuple(self.reason_codes, "NumericResultV1.reason_codes"))
        object.__setattr__(self, "warnings", _require_str_tuple(self.warnings, "NumericResultV1.warnings"))
        object.__setattr__(
            self,
            "operator_provenance",
            _freeze_mapping(self.operator_provenance, context="NumericResultV1.operator_provenance"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ref": self.ref.to_dict(),
            "values": None if self.values is None else list(self.values),
            "values_ref": None if self.values_ref is None else self.values_ref.to_dict(),
            "sample_coordinates": list(self.sample_coordinates),
            "counts": _to_plain_json(self.counts),
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "warnings": list(self.warnings),
            "operator_provenance": _to_plain_json(self.operator_provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NumericResultV1:
        required = {
            "schema_version",
            "ref",
            "values",
            "values_ref",
            "sample_coordinates",
            "counts",
            "status",
            "reason_codes",
            "warnings",
            "operator_provenance",
        }
        m = _require_exact_keys(data, required, context="NumericResultV1")
        _require_schema_version_v1(m["schema_version"])
        return cls(
            schema_version=1,
            ref=ResultRefV1.from_dict(m["ref"]),
            values=m["values"],
            values_ref=None if m["values_ref"] is None else ArtifactRefV1.from_dict(m["values_ref"]),
            sample_coordinates=m["sample_coordinates"],
            counts=m["counts"],
            status=m["status"],
            reason_codes=m["reason_codes"],
            warnings=m["warnings"],
            operator_provenance=m["operator_provenance"],
        )


def _coerce_artifact_ref(value: Any, name: str) -> ArtifactRefV1:
    if isinstance(value, ArtifactRefV1):
        return value
    if isinstance(value, Mapping):
        return ArtifactRefV1.from_dict(value)
    raise ValueError(f"{name} must be an ArtifactRefV1 or mapping")


def _coerce_optional_result_ref(value: Any, name: str) -> ResultRefV1 | None:
    if value is None:
        return None
    if isinstance(value, ResultRefV1):
        return value
    if isinstance(value, Mapping):
        return ResultRefV1.from_dict(value)
    raise ValueError(f"{name} must be None, ResultRefV1, or mapping")


def _validate_provenance(provenance: Any) -> MappingProxyType[str, Any]:
    m = _require_exact_keys(
        provenance,
        {"producer", "sources", "artifact_owners"},
        context="EvidenceEnvelopeV1.provenance",
    )
    raw_producer = m["producer"]
    producer = (
        raw_producer
        if isinstance(raw_producer, ProducerIdentity)
        else ProducerIdentity.from_dict(raw_producer)
    )

    raw_sources = m["sources"]
    if isinstance(raw_sources, (str, bytes, Mapping)) or not isinstance(raw_sources, Sequence):
        raise ValueError("EvidenceEnvelopeV1.provenance.sources must be a sequence")
    frozen_sources: list[MappingProxyType[str, Any]] = []
    for idx, src in enumerate(raw_sources):
        sm = _require_exact_keys(
            src,
            {"source_kind", "source_id", "source_revision", "url"},
            context=f"EvidenceEnvelopeV1.provenance.sources[{idx}]",
        )
        if sm["source_kind"] not in _SOURCE_KINDS:
            raise ValueError(f"Unsupported provenance source_kind: {sm['source_kind']!r}")
        _require_nonempty_str(sm["source_id"], f"sources[{idx}].source_id")
        _require_optional_nonempty_str(sm["source_revision"], f"sources[{idx}].source_revision")
        _require_optional_nonempty_str(sm["url"], f"sources[{idx}].url")
        frozen_sources.append(
            MappingProxyType(
                {
                    "source_kind": sm["source_kind"],
                    "source_id": sm["source_id"],
                    "source_revision": sm["source_revision"],
                    "url": sm["url"],
                }
            )
        )

    raw_owners = m["artifact_owners"]
    if not isinstance(raw_owners, Mapping):
        raise ValueError("EvidenceEnvelopeV1.provenance.artifact_owners must be a mapping")
    frozen_owners: dict[str, ArtifactOwner] = {}
    for artifact_id, owner in raw_owners.items():
        _require_nonempty_str(artifact_id, "artifact_owners key")
        frozen_owners[artifact_id] = (
            owner if isinstance(owner, ArtifactOwner) else ArtifactOwner.from_dict(owner)
        )

    return MappingProxyType(
        {
            "producer": producer,
            "sources": tuple(frozen_sources),
            "artifact_owners": MappingProxyType(frozen_owners),
        }
    )


def _validate_availability(availability: Any) -> MappingProxyType[str, Any]:
    m = _require_exact_keys(
        availability,
        {"state", "reason_codes", "missing_artifact_ids"},
        context="EvidenceEnvelopeV1.availability",
    )
    if m["state"] not in _AVAILABILITY_STATES:
        raise ValueError(
            f"availability.state must be one of {sorted(_AVAILABILITY_STATES)}, got {m['state']!r}"
        )
    reason_codes = _require_str_tuple(m["reason_codes"], "availability.reason_codes")
    missing_ids = _require_str_tuple(m["missing_artifact_ids"], "availability.missing_artifact_ids")
    return MappingProxyType(
        {
            "state": m["state"],
            "reason_codes": reason_codes,
            "missing_artifact_ids": missing_ids,
        }
    )


def _validate_envelope_payload(kind: str, payload: Any) -> MappingProxyType[str, Any]:
    if kind == "native_run":
        m = _require_exact_keys(
            payload,
            {"legacy_evidence_ref", "source_run_id", "source_node_id", "derived_result_ref"},
            context="EvidenceEnvelopeV1.payload(native_run)",
        )
        legacy_ref = _coerce_artifact_ref(m["legacy_evidence_ref"], "payload.legacy_evidence_ref")
        source_run_id = _require_nonempty_str(m["source_run_id"], "payload.source_run_id")
        source_node_id = _require_nonempty_str(m["source_node_id"], "payload.source_node_id")
        derived = _coerce_optional_result_ref(m["derived_result_ref"], "payload.derived_result_ref")
        if derived is not None:
            if derived.source_run_id != source_run_id or derived.source_node_id != source_node_id:
                raise ValueError(
                    "native_run payload.derived_result_ref source_run_id/source_node_id must match payload"
                )
        return MappingProxyType(
            {
                "legacy_evidence_ref": legacy_ref,
                "source_run_id": source_run_id,
                "source_node_id": source_node_id,
                "derived_result_ref": derived,
            }
        )

    if kind == "manual":
        m = _require_exact_keys(payload, {"legacy_evidence_ref"}, context="EvidenceEnvelopeV1.payload(manual)")
        return MappingProxyType(
            {"legacy_evidence_ref": _coerce_artifact_ref(m["legacy_evidence_ref"], "payload.legacy_evidence_ref")}
        )

    if kind == "calibration":
        m = _require_exact_keys(
            payload,
            {"evaluation_ref", "study_id", "content_revision", "evaluation_id"},
            context="EvidenceEnvelopeV1.payload(calibration)",
        )
        return MappingProxyType(
            {
                "evaluation_ref": _coerce_artifact_ref(m["evaluation_ref"], "payload.evaluation_ref"),
                "study_id": _require_nonempty_str(m["study_id"], "payload.study_id"),
                "content_revision": _require_nonempty_str(m["content_revision"], "payload.content_revision"),
                "evaluation_id": _require_nonempty_str(m["evaluation_id"], "payload.evaluation_id"),
            }
        )

    if kind == "document_chunk":
        required = {
            "corpus_id",
            "corpus_revision",
            "source_id",
            "source_ref",
            "chunk_id",
            "chunk_ref",
            "document_sha256",
            "text_sha256",
            "edition",
            "span",
            "page_start",
            "page_end",
            "section_path",
        }
        m = _require_exact_keys(payload, required, context="EvidenceEnvelopeV1.payload(document_chunk)")
        span = _require_exact_keys(
            m["span"],
            {"line_start", "line_end", "char_start", "char_end"},
            context="EvidenceEnvelopeV1.payload.span",
        )
        line_start = _require_int(span["line_start"], "span.line_start", minimum=1)
        line_end = _require_int(span["line_end"], "span.line_end", minimum=line_start)
        char_start = _require_int(span["char_start"], "span.char_start", minimum=0)
        char_end = _require_int(span["char_end"], "span.char_end", minimum=char_start + 1)

        page_start = m["page_start"]
        page_end = m["page_end"]
        if page_start is not None:
            page_start = _require_int(page_start, "payload.page_start", minimum=1)
        if page_end is not None:
            page_end = _require_int(page_end, "payload.page_end", minimum=1)
        if (page_start is None) != (page_end is None):
            raise ValueError("document_chunk page_start and page_end must both be int or both be None")
        if page_start is not None and page_end is not None and page_end < page_start:
            raise ValueError("document_chunk page_end must be >= page_start")

        return MappingProxyType(
            {
                "corpus_id": _require_nonempty_str(m["corpus_id"], "payload.corpus_id"),
                "corpus_revision": _require_nonempty_str(m["corpus_revision"], "payload.corpus_revision"),
                "source_id": _require_nonempty_str(m["source_id"], "payload.source_id"),
                "source_ref": _coerce_artifact_ref(m["source_ref"], "payload.source_ref"),
                "chunk_id": _require_nonempty_str(m["chunk_id"], "payload.chunk_id"),
                "chunk_ref": _coerce_artifact_ref(m["chunk_ref"], "payload.chunk_ref"),
                "document_sha256": _require_sha256(m["document_sha256"], "payload.document_sha256"),
                "text_sha256": _require_sha256(m["text_sha256"], "payload.text_sha256"),
                "edition": _require_nonempty_str(m["edition"], "payload.edition"),
                "span": MappingProxyType(
                    {
                        "line_start": line_start,
                        "line_end": line_end,
                        "char_start": char_start,
                        "char_end": char_end,
                    }
                ),
                "page_start": page_start,
                "page_end": page_end,
                "section_path": _require_str_tuple(m["section_path"], "payload.section_path"),
            }
        )

    raise ValueError(f"Unsupported EvidenceEnvelopeV1.kind: {kind!r}")


@dataclass(frozen=True)
class EvidenceEnvelopeV1:
    """Common v1 evidence envelope verifying provenance, availability, payload, and content_sha256."""

    id: str
    kind: str
    content_sha256: str
    provenance: Mapping[str, Any]
    availability: Mapping[str, Any]
    payload: Mapping[str, Any]
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_schema_version_v1(self.schema_version)
        _require_nonempty_str(self.id, "EvidenceEnvelopeV1.id")
        if self.kind not in _EVIDENCE_KINDS:
            raise ValueError(
                f"EvidenceEnvelopeV1.kind must be one of {sorted(_EVIDENCE_KINDS)}, got {self.kind!r}"
            )
        _require_sha256(self.content_sha256, "EvidenceEnvelopeV1.content_sha256")
        object.__setattr__(self, "provenance", _validate_provenance(self.provenance))
        object.__setattr__(self, "availability", _validate_availability(self.availability))
        object.__setattr__(self, "payload", _validate_envelope_payload(self.kind, self.payload))

        expected_sha = canonical_sha256(self.to_dict(), exclude_keys=("content_sha256",))
        if self.content_sha256 != expected_sha:
            raise ValueError(
                f"EvidenceEnvelopeV1.content_sha256 mismatch: expected {expected_sha}, got {self.content_sha256}"
            )

    @classmethod
    def create(
        cls,
        *,
        id: str,
        kind: str,
        provenance: Mapping[str, Any],
        availability: Mapping[str, Any],
        payload: Mapping[str, Any],
        schema_version: int = 1,
    ) -> EvidenceEnvelopeV1:
        """Construct an EvidenceEnvelopeV1 and compute its canonical content_sha256."""
        _require_schema_version_v1(schema_version)
        _require_nonempty_str(id, "EvidenceEnvelopeV1.id")
        if kind not in _EVIDENCE_KINDS:
            raise ValueError(f"EvidenceEnvelopeV1.kind must be one of {sorted(_EVIDENCE_KINDS)}, got {kind!r}")
        frozen_prov = _validate_provenance(provenance)
        frozen_avail = _validate_availability(availability)
        frozen_payload = _validate_envelope_payload(kind, payload)
        preimage = {
            "schema_version": 1,
            "id": id,
            "kind": kind,
            "provenance": _to_plain_json(frozen_prov),
            "availability": _to_plain_json(frozen_avail),
            "payload": _to_plain_json(frozen_payload),
        }
        digest = canonical_sha256(preimage, exclude_keys=("content_sha256",))
        return cls(
            schema_version=1,
            id=id,
            kind=kind,
            content_sha256=digest,
            provenance=frozen_prov,
            availability=frozen_avail,
            payload=frozen_payload,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "kind": self.kind,
            "content_sha256": self.content_sha256,
            "provenance": _to_plain_json(self.provenance),
            "availability": _to_plain_json(self.availability),
            "payload": _to_plain_json(self.payload),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvidenceEnvelopeV1:
        required = {"schema_version", "id", "kind", "content_sha256", "provenance", "availability", "payload"}
        m = _require_exact_keys(data, required, context="EvidenceEnvelopeV1")
        _require_schema_version_v1(m["schema_version"])
        return cls(
            schema_version=1,
            id=m["id"],
            kind=m["kind"],
            content_sha256=m["content_sha256"],
            provenance=m["provenance"],
            availability=m["availability"],
            payload=m["payload"],
        )
