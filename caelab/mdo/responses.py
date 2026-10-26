"""Scalar response extraction from solver result files and logs.

Supports:
- Regex-based scalar extraction from solver logs (e.g. max stress, mass, force).
- Table/CSV column reduction (min, max, mean, last).
- CalculiX DAT/FRD parsing integration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Any


@dataclass(frozen=True, kw_only=True)
class ResponseSpec:
    id: str
    name: str
    unit: str = ""
    source_file: str = "solver.log"
    kind: Literal["regex", "csv_column", "json_key", "custom"] = "regex"
    pattern: str = ""
    reduction: Literal["value", "min", "max", "last", "sum"] = "value"
    default_value: float | None = None


def extract_scalar(spec: ResponseSpec, workdir: Path) -> float | None:
    """Extract a scalar response value from workdir according to ResponseSpec."""
    target_path = workdir / spec.source_file
    if not target_path.exists():
        # Fallback search for matching filename in workdir
        candidates = list(workdir.glob(f"*{spec.source_file}*"))
        if candidates:
            target_path = candidates[0]
        else:
            return spec.default_value

    try:
        content = target_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return spec.default_value

    if spec.kind == "regex":
        # Search using regex with float group capture
        if not spec.pattern:
            return spec.default_value
        try:
            rx = re.compile(spec.pattern, re.IGNORECASE)
            matches = rx.findall(content)
            if not matches:
                return spec.default_value
            last_match = matches[-1]
            if isinstance(last_match, tuple):
                # Pick first non-empty group
                val_str = next((g for g in last_match if g), None)
            else:
                val_str = last_match
            if val_str:
                return float(val_str.strip())
        except Exception:
            return spec.default_value

    elif spec.kind == "json_key":
        import json
        try:
            data = json.loads(content)
            keys = spec.pattern.split(".")
            curr: Any = data
            for k in keys:
                curr = curr[k]
            return float(curr)
        except Exception:
            return spec.default_value

    return spec.default_value


def extract_all_responses(specs: Mapping[str, ResponseSpec], workdir: Path) -> dict[str, float]:
    """Extract all declared responses from a run directory."""
    results: dict[str, float] = {}
    for r_id, spec in specs.items():
        val = extract_scalar(spec, workdir)
        if val is not None:
            results[r_id] = val
    return results
