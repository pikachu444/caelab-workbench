"""CORE02 RFC4180 UTF-8 CSV experiment importer and unit/recipe validation (no GUI/VTK/solver imports)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import csv
import hashlib
import io
from itertools import islice
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any

import numpy as np

from caelab.artifact_refs import (
    ArtifactRefV1,
    ProducerIdentity,
    _to_plain_json,
    canonical_array_sha256,
    canonical_json_bytes,
    canonical_sha256,
    read_safe_npy_v2,
    validate_relative_artifact_path,
    write_safe_npy_v2,
)
from caelab.materials.contracts import (
    ExperimentChannelSpec,
    ExperimentSpec,
    ObservableSpec,
)
from caelab.materials.registry import MaterialStudyError
from caelab.materials.units import (
    QuantityUnitSpec,
    convert_frequency_angular,
    convert_values,
    parse_material_unit,
)

__all__ = [
    "ImportedExperiment",
    "import_csv_experiment",
    "load_experiment_channel_arrays",
    "preview_csv_rows",
]

_PRODUCER = ProducerIdentity(id="caelab.materials.experiments", version="1.0.0")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_SPLITS = frozenset({"train", "validation", "holdout"})
_IND_UNIT_KIND_HINTS = {
    "s": "time",
    "ms": "time",
    "us": "time",
    "min": "time",
    "Hz": "frequency",
    "kHz": "frequency",
    "MHz": "frequency",
    "GHz": "frequency",
    "rad/s": "angular_frequency",
    "1": "stretch",
    "": "dimensionless",
    "mm/mm": "strain",
}
_VALID_PHASE_CONVENTIONS = frozenset({"exp(+iwt)", "exp(-iwt)", "positive_loss"})


class ImportedExperiment:
    """Container pairing immutable ExperimentSpec with persisted array ArtifactRefV1s."""

    def __init__(
        self,
        *,
        spec: ExperimentSpec,
        channel_arrays: dict[str, dict[str, Any]],
        recipe_ref: ArtifactRefV1,
        converted_refs: tuple[ArtifactRefV1, ...],
        warnings: tuple[str, ...],
        original_source_path: str,
    ) -> None:
        self.spec = spec
        self.channel_arrays = channel_arrays
        self.recipe_ref = recipe_ref
        self.converted_refs = converted_refs
        self.warnings = warnings
        self.original_source_path = original_source_path


def _sanitize_segment(name: str, fallback: str = "item") -> str:
    cleaned = _SAFE_NAME_RE.sub("_", name).strip("._")
    return cleaned or fallback


def storage_segment(identifier: str) -> str:
    """Keep a readable prefix and distinguish IDs on case-insensitive filesystems."""
    return f"{_sanitize_segment(identifier)[:64]}-{hashlib.sha256(identifier.encode('utf-8')).hexdigest()[:20]}"


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
    target = data_root / rel_path
    _atomic_write_bytes(target, raw)
    return ArtifactRefV1(
        id=artifact_id,
        role=role,
        path=rel_path,
        sha256=hashlib.sha256(raw).hexdigest(),
        size=len(raw),
        format_id="application/json",
        producer=_PRODUCER,
    )


def _parse_finite_float_cell(raw: str, *, row_num: int, column: str) -> float:
    text = raw.strip()
    if not text:
        raise MaterialStudyError(
            "INVALID_CSV",
            "import_csv_experiment",
            {"reason": "EMPTY_NUMERIC_CELL", "row": row_num, "column": column},
        )
    lower = text.lower()
    if lower in ("nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity", "true", "false"):
        raise MaterialStudyError(
            "INVALID_CSV",
            "import_csv_experiment",
            {"reason": "NONFINITE_CELL", "row": row_num, "column": column, "value": text},
        )
    try:
        val = float(text)
    except ValueError as exc:
        raise MaterialStudyError(
            "INVALID_CSV",
            "import_csv_experiment",
            {"reason": "NON_NUMERIC_CELL", "row": row_num, "column": column, "value": text},
        ) from exc
    if not math.isfinite(val):
        raise MaterialStudyError(
            "INVALID_CSV",
            "import_csv_experiment",
            {"reason": "NONFINITE_CELL", "row": row_num, "column": column, "value": text},
        )
    return val


def _coerce_quantity_unit_spec(raw: Any, context: str) -> QuantityUnitSpec:
    if isinstance(raw, QuantityUnitSpec):
        return raw
    if isinstance(raw, Mapping) and "unit" in raw and "quantity_kind" in raw:
        return QuantityUnitSpec(unit=str(raw["unit"]), quantity_kind=str(raw["quantity_kind"]))
    raise MaterialStudyError(
        "EXPERIMENT_METADATA_MISSING",
        "import_csv_experiment",
        {"reason": "INVALID_QUANTITY_UNIT_SPEC", "context": context},
    )


def _resolve_independent_quantity_kind(
    ch: ExperimentChannelSpec,
    column_mapping: Mapping[str, Any] | None,
    unit_specs: Mapping[str, Any] | None,
) -> QuantityUnitSpec:
    if unit_specs:
        for key in (ch.independent_column, "independent", f"{ch.observable_id}.independent"):
            if key in unit_specs:
                spec = _coerce_quantity_unit_spec(unit_specs[key], f"unit_specs[{key!r}]")
                if spec.unit != ch.independent_unit:
                    raise MaterialStudyError(
                        "UNSUPPORTED_UNIT",
                        "import_csv_experiment",
                        {
                            "reason": "INDEPENDENT_UNIT_MISMATCH",
                            "expected": ch.independent_unit,
                            "got": spec.unit,
                        },
                    )
                parse_material_unit(spec.unit, quantity_kind=spec.quantity_kind)
                return spec

    if column_mapping:
        if "independent_quantity_kind" in column_mapping:
            qkind = str(column_mapping["independent_quantity_kind"])
            spec = QuantityUnitSpec(unit=ch.independent_unit, quantity_kind=qkind)
            parse_material_unit(spec.unit, quantity_kind=spec.quantity_kind)
            return spec
        col_meta = column_mapping.get(ch.independent_column)
        if isinstance(col_meta, Mapping) and "quantity_kind" in col_meta:
            spec = QuantityUnitSpec(
                unit=str(col_meta.get("unit", ch.independent_unit)),
                quantity_kind=str(col_meta["quantity_kind"]),
            )
            if spec.unit != ch.independent_unit:
                raise MaterialStudyError(
                    "UNSUPPORTED_UNIT",
                    "import_csv_experiment",
                    {"reason": "INDEPENDENT_UNIT_MISMATCH", "expected": ch.independent_unit, "got": spec.unit},
                )
            parse_material_unit(spec.unit, quantity_kind=spec.quantity_kind)
            return spec

    if ch.independent_unit in _IND_UNIT_KIND_HINTS:
        spec = QuantityUnitSpec(unit=ch.independent_unit, quantity_kind=_IND_UNIT_KIND_HINTS[ch.independent_unit])
        parse_material_unit(spec.unit, quantity_kind=spec.quantity_kind)
        return spec

    raise MaterialStudyError(
        "EXPERIMENT_METADATA_MISSING",
        "import_csv_experiment",
        {
            "reason": "MISSING_INDEPENDENT_QUANTITY_UNIT_SPEC",
            "independent_column": ch.independent_column,
            "independent_unit": ch.independent_unit,
        },
    )


def _normalize_conditions(conditions: Mapping[str, Any] | None) -> dict[str, Any]:
    if conditions is None:
        return {}
    if not isinstance(conditions, Mapping):
        raise MaterialStudyError(
            "DATA_RECIPE_INCOMPATIBLE",
            "import_csv_experiment",
            {"reason": "INVALID_CONDITIONS_MAPPING"},
        )
    out = dict(_to_plain_json(conditions))
    if "temperature" in out and "temperature_K" not in out:
        temp_obj = out["temperature"]
        if isinstance(temp_obj, Mapping) and "value" in temp_obj and "unit" in temp_obj:
            try:
                t_val = convert_values(
                    float(temp_obj["value"]),
                    QuantityUnitSpec(str(temp_obj["unit"]), "absolute_temperature"),
                    QuantityUnitSpec("K", "absolute_temperature"),
                )
            except ValueError as exc:
                raise MaterialStudyError(
                    "UNSUPPORTED_UNIT",
                    "import_csv_experiment",
                    {"reason": "INVALID_TEMPERATURE_CONDITION", "error": str(exc)},
                ) from exc
            out["temperature_K"] = float(t_val)
        elif isinstance(temp_obj, (int, float)) and not isinstance(temp_obj, bool):
            unit = str(out.get("temperature_unit", ""))
            if not unit:
                raise MaterialStudyError(
                    "EXPERIMENT_METADATA_MISSING",
                    "import_csv_experiment",
                    {"reason": "MISSING_TEMPERATURE_UNIT"},
                )
            try:
                t_val = convert_values(
                    float(temp_obj),
                    QuantityUnitSpec(unit, "absolute_temperature"),
                    QuantityUnitSpec("K", "absolute_temperature"),
                )
            except ValueError as exc:
                raise MaterialStudyError(
                    "UNSUPPORTED_UNIT",
                    "import_csv_experiment",
                    {"reason": "INVALID_TEMPERATURE_CONDITION", "error": str(exc)},
                ) from exc
            out["temperature_K"] = float(t_val)
    elif "temperature_K" in out:
        raw_tk = out["temperature_K"]
        if isinstance(raw_tk, bool) or not isinstance(raw_tk, (int, float)) or not math.isfinite(float(raw_tk)):
            raise MaterialStudyError(
                "INVALID_PARAMETER",
                "import_csv_experiment",
                {"reason": "INVALID_TEMPERATURE_K"},
            )
        try:
            out["temperature_K"] = float(
                convert_values(
                    float(raw_tk),
                    QuantityUnitSpec("K", "absolute_temperature"),
                    QuantityUnitSpec("K", "absolute_temperature"),
                )
            )
        except ValueError as exc:
            raise MaterialStudyError(
                "INVALID_PARAMETER",
                "import_csv_experiment",
                {"reason": "NONPOSITIVE_KELVIN_TEMPERATURE", "error": str(exc)},
            ) from exc
    return out


def _jump_boundary_indices(sequence: Sequence[Mapping[str, Any]], source_rows: list[int]) -> set[int]:
    """Return local selected row indices where a declared jump segment boundary allows repeated time."""
    boundaries: set[int] = set()
    row_pos = {src_row: idx for idx, src_row in enumerate(source_rows)}
    for seg in sequence:
        if not isinstance(seg, Mapping):
            continue
        is_jump = bool(seg.get("jump", False)) or str(seg.get("kind", "")).lower() in ("jump", "step", "discontinuity")
        if not is_jump:
            continue
        if "row_index" in seg and type(seg["row_index"]) is int:
            boundaries.add(seg["row_index"])
        if "source_row" in seg and type(seg["source_row"]) is int and seg["source_row"] in row_pos:
            boundaries.add(row_pos[seg["source_row"]])
        if "start_row" in seg and type(seg["start_row"]) is int:
            boundaries.add(seg["start_row"])
    return boundaries


def _read_and_parse_csv(source_path: Path) -> tuple[bytes, str, list[str], list[list[str]]]:
    if not source_path.is_file():
        raise MaterialStudyError(
            "INVALID_CSV",
            "import_csv_experiment",
            {"reason": "FILE_NOT_FOUND", "path": str(source_path)},
        )
    raw_bytes = source_path.read_bytes()
    if not raw_bytes:
        raise MaterialStudyError("INVALID_CSV", "import_csv_experiment", {"reason": "EMPTY_FILE"})
    if b"\x00" in raw_bytes:
        raise MaterialStudyError("INVALID_CSV", "import_csv_experiment", {"reason": "NUL_BYTE_IN_CSV"})
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MaterialStudyError(
            "INVALID_CSV",
            "import_csv_experiment",
            {"reason": "INVALID_UTF8", "error": str(exc)},
        ) from exc

    if text.startswith("\ufeff"):
        text = text[1:]

    reader = csv.reader(io.StringIO(text, newline=""))
    rows = list(reader)
    if len(rows) < 2:
        raise MaterialStudyError(
            "INVALID_CSV",
            "import_csv_experiment",
            {"reason": "MISSING_HEADER_OR_DATA_ROWS"},
        )
    headers = rows[0]
    if not headers or any(not h or not h.strip() for h in headers):
        raise MaterialStudyError("INVALID_CSV", "import_csv_experiment", {"reason": "EMPTY_HEADER_COLUMN"})
    if len(set(headers)) != len(headers):
        raise MaterialStudyError("INVALID_CSV", "import_csv_experiment", {"reason": "DUPLICATE_HEADER_COLUMN"})

    data_rows = rows[1:]
    for idx, row in enumerate(data_rows):
        if len(row) != len(headers):
            raise MaterialStudyError(
                "INVALID_CSV",
                "import_csv_experiment",
                {
                    "reason": "RAGGED_RFC4180_ROW",
                    "row": idx,
                    "expected_columns": len(headers),
                    "actual_columns": len(row),
                },
            )
    source_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    return raw_bytes, source_sha256, headers, data_rows


def preview_csv_rows(source: str | Path, *, max_rows: int = 100) -> tuple[list[str], list[list[str]], int]:
    """Read a bounded preview. The row count is capped at max_rows + 1."""
    limit = max(0, int(max_rows))
    try:
        with Path(source).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream)
            headers = next(reader, [])
            rows = list(islice(reader, limit + 1))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MaterialStudyError("INVALID_CSV", "preview_csv", {"reason": str(exc)}) from exc
    if not headers or not rows or any(not col.strip() or "\x00" in col for col in headers):
        raise MaterialStudyError("INVALID_CSV", "preview_csv", {"reason": "MISSING_HEADER_OR_DATA_ROWS"})
    if len(set(headers)) != len(headers):
        raise MaterialStudyError("INVALID_CSV", "preview_csv", {"reason": "DUPLICATE_HEADER_COLUMN"})
    if any(len(row) != len(headers) or any("\x00" in col for col in row) for row in rows[:limit]):
        raise MaterialStudyError("INVALID_CSV", "preview_csv", {"reason": "INVALID_PREVIEW_ROW"})
    return headers, rows[:limit], len(rows)


def _match_row_filter(row_dict: Mapping[str, str], row_filter: Mapping[str, Any]) -> bool:
    for col, expected in row_filter.items():
        if col not in row_dict:
            raise MaterialStudyError(
                "INVALID_CSV",
                "import_csv_experiment",
                {"reason": "FILTER_COLUMN_MISSING", "column": col},
            )
        raw_val = row_dict[col].strip()
        if isinstance(expected, (int, float)) and not isinstance(expected, bool):
            try:
                num = float(raw_val)
            except ValueError:
                return False
            if not math.isclose(num, float(expected), rel_tol=1e-9, abs_tol=1e-12):
                return False
        else:
            if raw_val != str(expected).strip():
                return False
    return True


def import_csv_experiment(
    source: str | Path,
    *,
    study_id: str,
    data_root: Path,
    experiment_id: str,
    channels: Sequence[ExperimentChannelSpec | Mapping[str, Any]],
    column_mapping: Mapping[str, Any] | None = None,
    unit_specs: Mapping[str, QuantityUnitSpec | Mapping[str, str]] | None = None,
    sequence: Sequence[Mapping[str, Any]] = (),
    conditions: Mapping[str, Any] | None = None,
    split: str = "train",
    replicate_group: str = "",
    row_filter: Mapping[str, Any] | None = None,
    source_kind: str = "experiment",
    conversion_recipe: Mapping[str, Any] | None = None,
) -> ImportedExperiment:
    """Import an immutable UTF-8 RFC4180 CSV into <data_root> without altering or silently dropping raw rows."""
    if split not in _SPLITS:
        raise MaterialStudyError(
            "DATA_RECIPE_INCOMPATIBLE",
            "import_csv_experiment",
            {"reason": "INVALID_SPLIT", "split": split},
        )
    if not isinstance(experiment_id, str) or not experiment_id.strip() or "\x00" in experiment_id:
        raise MaterialStudyError("DATA_RECIPE_INCOMPATIBLE", "import_csv_experiment", {"reason": "INVALID_EXPERIMENT_ID"})
    if not isinstance(replicate_group, str) or "\x00" in replicate_group:
        raise MaterialStudyError("DATA_RECIPE_INCOMPATIBLE", "import_csv_experiment", {"reason": "INVALID_REPLICATE_GROUP"})

    source_path = Path(source).resolve()
    raw_bytes, source_sha256, headers, all_rows = _read_and_parse_csv(source_path)

    # Coerce channel specs
    if isinstance(channels, (str, bytes, Mapping)) or not isinstance(channels, Sequence) or not channels:
        raise MaterialStudyError("DATA_RECIPE_INCOMPATIBLE", "import_csv_experiment", {"reason": "CHANNELS_REQUIRED"})
    parsed_channels: list[ExperimentChannelSpec] = []
    for ch in channels:
        if isinstance(ch, ExperimentChannelSpec):
            parsed_channels.append(ch)
        elif isinstance(ch, Mapping):
            ch_dict = dict(ch)
            if isinstance(ch_dict.get("observable"), Mapping):
                ch_dict["observable"] = ObservableSpec(**ch_dict["observable"])
            parsed_channels.append(ExperimentChannelSpec(**ch_dict))
        else:
            raise MaterialStudyError("DATA_RECIPE_INCOMPATIBLE", "import_csv_experiment", {"reason": "INVALID_CHANNEL_SPEC"})

    # Validate explicit row filter or split-column consistency without silent dropping
    selected_indices: list[int] = []
    selected_rows: list[dict[str, str]] = []
    for idx, row in enumerate(all_rows):
        row_map = dict(zip(headers, row))
        if row_filter is None or _match_row_filter(row_map, row_filter):
            if "split" in row_map and row_map["split"].strip() != split:
                raise MaterialStudyError(
                    "DATA_RECIPE_INCOMPATIBLE",
                    "import_csv_experiment",
                    {
                        "reason": "SPLIT_COLUMN_MISMATCH",
                        "row": idx,
                        "csv_split": row_map["split"].strip(),
                        "declared_split": split,
                    },
                )
            selected_indices.append(idx)
            selected_rows.append(row_map)

    if not selected_rows:
        raise MaterialStudyError(
            "INVALID_CSV",
            "import_csv_experiment",
            {"reason": "NO_MATCHING_DATA_ROWS", "row_filter": dict(row_filter or {})},
        )

    # Validate conversion_recipe (e.g. amplitude/phase convention)
    recipe_dict = dict(_to_plain_json(conversion_recipe)) if conversion_recipe else {}
    if recipe_dict.get("representation") == "amplitude_phase":
        phase_conv = str(recipe_dict.get("phase_convention", "")).strip()
        if phase_conv not in _VALID_PHASE_CONVENTIONS:
            raise MaterialStudyError(
                "MEASURE_MISMATCH",
                "import_csv_experiment",
                {"reason": "PHASE_CONVENTION_UNKNOWN", "phase_convention": phase_conv},
            )

    norm_conditions = _normalize_conditions(conditions)
    warnings: list[str] = []
    if source_kind == "synthetic_arithmetic":
        warnings.append("SYNTHETIC_ARITHMETIC_SOURCE_NOT_PHYSICAL_EXPERIMENT")

    # Persist immutable source bytes cleanly inside data_root
    safe_filename = _sanitize_segment(source_path.name, "experiment.csv")
    source_rel = f"sources/{source_sha256[:16]}/{safe_filename}"
    validate_relative_artifact_path(source_rel, root=data_root)
    source_target = data_root / source_rel
    if source_target.exists():
        existing_bytes = source_target.read_bytes()
        if hashlib.sha256(existing_bytes).hexdigest() != source_sha256:
            source_rel = f"sources/{source_sha256[:16]}/{_sanitize_segment(experiment_id)}_{safe_filename}"
            validate_relative_artifact_path(source_rel, root=data_root)
            _atomic_write_bytes(data_root / source_rel, raw_bytes)
    else:
        _atomic_write_bytes(source_target, raw_bytes)

    source_ref = ArtifactRefV1(
        id=f"src:{source_sha256[:16]}",
        role="source_csv",
        path=source_rel,
        sha256=source_sha256,
        size=len(raw_bytes),
        format_id="text/csv",
        producer=_PRODUCER,
    )

    exp_slug = storage_segment(experiment_id)
    channel_arrays: dict[str, dict[str, Any]] = {}
    converted_refs: list[ArtifactRefV1] = []
    array_sha_summary: dict[str, dict[str, str]] = {}
    jump_boundaries = _jump_boundary_indices(sequence, selected_indices)

    for ch in parsed_channels:
        if ch.independent_column not in headers:
            raise MaterialStudyError(
                "INVALID_CSV",
                "import_csv_experiment",
                {"reason": "MISSING_INDEPENDENT_COLUMN", "column": ch.independent_column},
            )
        ind_unit_spec = _resolve_independent_quantity_kind(ch, column_mapping, unit_specs)

        # Resolve target independent quantity spec
        ind_target_unit = ch.independent_unit
        ind_target_spec = QuantityUnitSpec(ind_target_unit, ind_unit_spec.quantity_kind)
        if column_mapping and isinstance(column_mapping.get("target_independent_unit"), str):
            ind_target_unit = column_mapping["target_independent_unit"]
            ind_target_spec = QuantityUnitSpec(ind_target_unit, ind_unit_spec.quantity_kind)

        # Parse independent coordinates in raw row order
        raw_ind = np.array(
            [
                _parse_finite_float_cell(row_map[ch.independent_column], row_num=src_idx, column=ch.independent_column)
                for src_idx, row_map in zip(selected_indices, selected_rows)
            ],
            dtype=np.float64,
        )
        try:
            if ind_unit_spec.quantity_kind != ind_target_spec.quantity_kind:
                ind_values = np.asarray(
                    convert_frequency_angular(raw_ind, ind_unit_spec, ind_target_spec),
                    dtype=np.float64,
                )
            else:
                ind_values = np.asarray(convert_values(raw_ind, ind_unit_spec, ind_target_spec), dtype=np.float64)
        except ValueError as exc:
            raise MaterialStudyError(
                "UNSUPPORTED_UNIT",
                "import_csv_experiment",
                {"reason": "INDEPENDENT_UNIT_CONVERSION_FAILED", "error": str(exc)},
            ) from exc

        # Enforce time/frequency monotonicity & duplicate-coordinate rules (B3)
        if ind_unit_spec.quantity_kind == "time":
            for pos in range(1, len(ind_values)):
                if ind_values[pos] < ind_values[pos - 1]:
                    raise MaterialStudyError(
                        "DATA_RECIPE_INCOMPATIBLE",
                        "import_csv_experiment",
                        {
                            "reason": "NON_MONOTONIC_TIME",
                            "row": selected_indices[pos],
                            "prev_t": float(ind_values[pos - 1]),
                            "curr_t": float(ind_values[pos]),
                        },
                    )
                if ind_values[pos] == ind_values[pos - 1] and pos not in jump_boundaries:
                    raise MaterialStudyError(
                        "DATA_RECIPE_INCOMPATIBLE",
                        "import_csv_experiment",
                        {
                            "reason": "UNDECLARED_DUPLICATE_TIME",
                            "row": selected_indices[pos],
                            "time": float(ind_values[pos]),
                        },
                    )
        elif ind_unit_spec.quantity_kind in ("frequency", "angular_frequency"):
            seen_coords: set[float] = set()
            for pos, coord_val in enumerate(ind_values):
                if float(coord_val) in seen_coords and pos not in jump_boundaries:
                    raise MaterialStudyError(
                        "DATA_RECIPE_INCOMPATIBLE",
                        "import_csv_experiment",
                        {
                            "reason": "UNDECLARED_DUPLICATE_FREQUENCY",
                            "row": selected_indices[pos],
                            "coordinate": float(coord_val),
                        },
                    )
                seen_coords.add(float(coord_val))

        # Parse and convert dependent observable values
        obs = ch.observable
        if recipe_dict.get("representation") == "amplitude_phase" and ch.observable_id in ("E_storage", "E_loss"):
            amp_col = str(recipe_dict.get("amplitude_column", ch.column_name))
            phase_col = str(recipe_dict.get("phase_column", ""))
            phase_unit = str(recipe_dict.get("phase_unit", "rad")).strip()
            phase_conv = str(recipe_dict.get("phase_convention", "")).strip()
            if amp_col not in headers or phase_col not in headers:
                raise MaterialStudyError(
                    "INVALID_CSV",
                    "import_csv_experiment",
                    {"reason": "MISSING_AMPLITUDE_OR_PHASE_COLUMN", "amp_col": amp_col, "phase_col": phase_col},
                )
            raw_amp = np.array(
                [
                    _parse_finite_float_cell(row_map[amp_col], row_num=src_idx, column=amp_col)
                    for src_idx, row_map in zip(selected_indices, selected_rows)
                ],
                dtype=np.float64,
            )
            raw_phase = np.array(
                [
                    _parse_finite_float_cell(row_map[phase_col], row_num=src_idx, column=phase_col)
                    for src_idx, row_map in zip(selected_indices, selected_rows)
                ],
                dtype=np.float64,
            )
            if phase_unit == "deg":
                delta = raw_phase * (math.pi / 180.0)
            elif phase_unit == "rad":
                delta = raw_phase
            else:
                raise MaterialStudyError(
                    "UNSUPPORTED_UNIT",
                    "import_csv_experiment",
                    {"reason": "UNSUPPORTED_PHASE_UNIT", "phase_unit": phase_unit},
                )
            if phase_conv == "exp(-iwt)":
                delta = -delta
            raw_dep = raw_amp * np.cos(delta) if ch.observable_id == "E_storage" else raw_amp * np.sin(delta)
        else:
            if ch.column_name not in headers:
                raise MaterialStudyError(
                    "INVALID_CSV",
                    "import_csv_experiment",
                    {"reason": "MISSING_OBSERVABLE_COLUMN", "column": ch.column_name},
                )
            raw_dep = np.array(
                [
                    _parse_finite_float_cell(row_map[ch.column_name], row_num=src_idx, column=ch.column_name)
                    for src_idx, row_map in zip(selected_indices, selected_rows)
                ],
                dtype=np.float64,
            )

        dep_src_spec = QuantityUnitSpec(ch.source_unit, obs.quantity_kind)
        dep_tgt_spec = QuantityUnitSpec(obs.unit, obs.quantity_kind)
        if unit_specs:
            for key in (ch.column_name, ch.observable_id):
                if key in unit_specs:
                    explicit_dep = _coerce_quantity_unit_spec(unit_specs[key], f"unit_specs[{key!r}]")
                    if explicit_dep.unit != ch.source_unit or explicit_dep.quantity_kind != obs.quantity_kind:
                        raise MaterialStudyError(
                            "MEASURE_MISMATCH",
                            "import_csv_experiment",
                            {
                                "reason": "CHANNEL_UNIT_SPEC_MISMATCH",
                                "channel": ch.observable_id,
                                "explicit": {"unit": explicit_dep.unit, "quantity_kind": explicit_dep.quantity_kind},
                                "channel_spec": {"unit": ch.source_unit, "quantity_kind": obs.quantity_kind},
                            },
                        )
        try:
            dep_converted = np.asarray(convert_values(raw_dep, dep_src_spec, dep_tgt_spec), dtype=np.float64)
        except ValueError as exc:
            raise MaterialStudyError(
                "UNSUPPORTED_UNIT",
                "import_csv_experiment",
                {"reason": "OBSERVABLE_UNIT_CONVERSION_FAILED", "channel": ch.observable_id, "error": str(exc)},
            ) from exc

        values_2d = dep_converted.reshape(-1, 1)
        sample_keys_2d = np.asarray([[r] for r in selected_indices], dtype="<i8")
        mask_2d = np.ones((len(selected_indices), 1), dtype="|b1")

        ch_slug = storage_segment(ch.observable_id)
        base_rel = f"arrays/experiments/{exp_slug}/{ch_slug}"

        def _save_npy(suffix: str, role: str, arr: np.ndarray) -> ArtifactRefV1:
            rel = f"{base_rel}.{suffix}.npy"
            validate_relative_artifact_path(rel, root=data_root)
            file_sha = write_safe_npy_v2(rel, arr, root=data_root)
            size = (data_root / rel).stat().st_size
            ref = ArtifactRefV1(
                id=f"exp:{exp_slug}:{ch_slug}:{suffix}",
                role=role,
                path=rel,
                sha256=file_sha,
                size=size,
                format_id="application/x-npy-v2",
                producer=_PRODUCER,
            )
            converted_refs.append(ref)
            return ref

        ind_ref = _save_npy("independent", "observed_independent", ind_values)
        vals_ref = _save_npy("values", "observed_values", values_2d)
        keys_ref = _save_npy("sample_keys", "observed_sample_keys", sample_keys_2d)
        mask_ref = _save_npy("mask", "observed_mask", mask_2d)

        channel_arrays[ch.observable_id] = {
            "observable_id": ch.observable_id,
            "independent_quantity_kind": ind_unit_spec.quantity_kind,
            "independent_unit": ind_target_unit,
            "independent_ref": ind_ref.to_dict(),
            "values_ref": vals_ref.to_dict(),
            "sample_keys_ref": keys_ref.to_dict(),
            "mask_ref": mask_ref.to_dict(),
            "row_count": len(selected_indices),
            "sample_key_columns": ["source_row"],
        }
        array_sha_summary[ch.observable_id] = {
            "independent": canonical_array_sha256(ind_values),
            "values": canonical_array_sha256(values_2d),
            "sample_keys": canonical_array_sha256(sample_keys_2d),
            "mask": canonical_array_sha256(mask_2d),
        }

    # Check if CSV contains a constant/varying temperature column to keep condition consistent
    if "temperature_K" in headers and "temperature_K" not in norm_conditions:
        temps = [
            _parse_finite_float_cell(row_map["temperature_K"], row_num=src_idx, column="temperature_K")
            for src_idx, row_map in zip(selected_indices, selected_rows)
        ]
        if all(math.isclose(t, temps[0], rel_tol=1e-12, abs_tol=1e-12) for t in temps):
            norm_conditions["temperature_K"] = float(
                convert_values(
                    temps[0],
                    QuantityUnitSpec("K", "absolute_temperature"),
                    QuantityUnitSpec("K", "absolute_temperature"),
                )
            )

    recipe_payload = {
        "schema_version": 1,
        "study_id": study_id,
        "experiment_id": experiment_id,
        "split": split,
        "replicate_group": replicate_group,
        "source_ref": source_ref.to_dict(),
        "original_source_path": str(source_path),
        "column_mapping": _to_plain_json(column_mapping or {}),
        "unit_specs": {
            str(k): {"unit": _coerce_quantity_unit_spec(v, str(k)).unit, "quantity_kind": _coerce_quantity_unit_spec(v, str(k)).quantity_kind}
            for k, v in (unit_specs or {}).items()
        },
        "row_filter": _to_plain_json(row_filter) if row_filter is not None else None,
        "conversion_recipe": recipe_dict,
        "conditions": norm_conditions,
        "sequence": [_to_plain_json(seg) for seg in sequence],
        "row_count": len(selected_indices),
        "source_rows": selected_indices,
        "array_sha256s": array_sha_summary,
    }
    recipe_ref = _write_json_artifact(
        data_root,
        f"sources/{exp_slug}.recipe.json",
        recipe_payload,
        artifact_id=f"recipe:{exp_slug}",
        role="experiment_conversion_recipe",
    )
    converted_refs.append(recipe_ref)

    exp_rev_preimage = {
        "experiment_id": experiment_id,
        "split": split,
        "source_sha256": source_sha256,
        "channels": [ch.to_dict() for ch in parsed_channels],
        "replicate_group": replicate_group,
        "conditions": norm_conditions,
        "sequence": [_to_plain_json(seg) for seg in sequence],
        "source_kind": source_kind,
        "array_sha256s": array_sha_summary,
        "recipe_sha256": canonical_sha256(
            {
                **{key: value for key, value in recipe_payload.items()
                   if key not in ("study_id", "source_ref", "original_source_path")},
                "source_filename": source_path.name,
                "source_file_sha256": source_sha256,
            },
            exclude_keys=(),
        ),
    }
    experiment_revision = canonical_sha256(exp_rev_preimage, exclude_keys=())

    spec = ExperimentSpec(
        experiment_id=experiment_id,
        experiment_revision=experiment_revision,
        split=split,
        source_ref=source_ref,
        channels=tuple(parsed_channels),
        replicate_group=replicate_group,
        conditions=norm_conditions,
        sequence=tuple(sequence),
        source_kind=source_kind,
    )
    return ImportedExperiment(
        spec=spec,
        channel_arrays=channel_arrays,
        recipe_ref=recipe_ref,
        converted_refs=tuple(converted_refs),
        warnings=tuple(warnings),
        original_source_path=str(source_path),
    )


def load_experiment_channel_arrays(
    channel_entry: Mapping[str, Any],
    *,
    data_root: Path,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Load and SHA-verify observed (independent, values, sample_keys, mask) arrays."""
    out: list[np.ndarray] = []
    for key in ("independent_ref", "values_ref", "sample_keys_ref", "mask_ref"):
        ref = ArtifactRefV1.from_dict(channel_entry[key])
        target = (data_root / ref.path).resolve()
        validate_relative_artifact_path(ref.path, root=data_root)
        if not target.is_file():
            raise MaterialStudyError(
                "CORRUPT_ARTIFACT",
                "load_experiment_channel_arrays",
                {"reason": "MISSING_ARRAY_ARTIFACT", "path": ref.path, "artifact_id": ref.id},
            )
        actual_bytes = target.read_bytes()
        if hashlib.sha256(actual_bytes).hexdigest() != ref.sha256 or len(actual_bytes) != ref.size:
            raise MaterialStudyError(
                "CORRUPT_ARTIFACT",
                "load_experiment_channel_arrays",
                {"reason": "ARRAY_SHA256_MISMATCH", "path": ref.path, "artifact_id": ref.id},
            )
        out.append(read_safe_npy_v2(ref.path, root=data_root))
    return out[0], out[1], out[2], out[3]
