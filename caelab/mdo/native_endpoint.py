"""Completion checks for the documented one-time-unit mechanical STATIC subset."""
from __future__ import annotations

import math
from pathlib import Path

from caelab.native.input_snapshot import _includes


def expected_static_endpoint(input_path):
    root = Path(input_path).resolve()
    def lines(path,stack=()):
        if path in stack or len(stack) > 9:
            raise ValueError("Cyclic/deep native input closure")
        for raw in path.read_bytes().splitlines(keepends=True):
            body = raw.decode("utf-8").strip().lstrip("\ufeff")
            if body.upper().startswith("*INCLUDE,"):
                includes = list(_includes(raw,path))
                if len(includes) != 1:
                    raise ValueError("Unresolved native INCLUDE at endpoint validation")
                target = Path(includes[0][2])
                yield from lines((root.parent/target).resolve() if not target.is_absolute() else target.resolve(),(*stack,path))
            elif body and not body.startswith("**"):
                yield body
    step = 0
    in_step = False
    static = False
    static_data = False
    periods = []
    section_forces = False
    for line in lines(root):
        upper = line.upper()
        if upper.startswith("*") and "SECTION FORCES" in upper:
            section_forces = True
        if "TIME RESET" in upper or "TOTAL TIME AT START" in upper or (upper.startswith("*RESTART") and "READ" in upper):
            raise ValueError("MDO endpoint validation does not support restart/reset time semantics")
        if upper.startswith("*"):
            keyword = upper.split(",")[0].strip()
            static_data = False
            if keyword == "*STEP":
                if in_step or "PERTURBATION" in upper:
                    raise ValueError("MDO endpoint validation requires nonperturbative mechanical STATIC steps")
                step += 1; in_step = True; static = False
            elif keyword == "*STATIC":
                if not in_step or static:
                    raise ValueError("Each supported MDO step needs exactly one STATIC procedure")
                static = True; static_data = True
            elif keyword == "*END STEP":
                if not in_step or not static:
                    raise ValueError("MDO native responses currently require completed mechanical STATIC steps")
                periods.append(1.); in_step = False
            elif keyword in {"*DYNAMIC","*FREQUENCY","*BUCKLE","*HEAT TRANSFER","*COUPLED TEMPERATURE-DISPLACEMENT",
                              "*STEADY STATE DYNAMICS","*MODAL DYNAMIC","*VISCO","*CFD","*ELECTROMAGNETICS"}:
                raise ValueError("MDO endpoint validation supports mechanical STATIC only; native result remains available for other procedures")
        elif static_data:
            fields = [part.strip() for part in line.split(",")]
            period = float(fields[1].replace("D","E").replace("d","e")) if len(fields) > 1 and fields[1] else 1.
            if period == 0:
                period = 1.
            # Producer documentation/source disagree about explicit non-unit linear periods.
            if not math.isfinite(period) or period != 1.:
                raise ValueError("MDO completion validation for explicit non-unit STATIC periods is not yet verified; use a unit-period study or inspect native results")
            static_data = False
    if in_step or not periods or len(periods) != step:
        raise ValueError("A complete mechanical STATIC step is required for MDO response validity")
    return dict(step=step,step_time=1.,total_time=sum(periods),section_forces=section_forces)


def validate_native_endpoint(workdir,reader):
    folder = Path(workdir)
    expected = expected_static_endpoint(folder/"model.inp")
    rows = []
    for line in (folder/"model.sta").read_text(encoding="utf-8",errors="strict").splitlines():
        fields = line.split()
        if fields and fields[0].isdigit():
            if len(fields) != 7:
                raise ValueError("Unrecognized CalculiX STA increment record")
            rows.append(fields)
    if not rows:
        raise ValueError("No final CalculiX STA increment record")
    last = rows[-1]
    step,increment = int(last[0]),int(last[1])
    if last[2].upper().endswith("U") or not last[2].isdigit() or increment <= 0:
        raise ValueError("Final CalculiX STA increment was unsuccessful")
    total,step_time = (float(last[i].replace("D","E").replace("d","e")) for i in (4,5))
    if step != expected["step"] or not math.isclose(total,expected["total_time"],rel_tol=6e-7,abs_tol=1e-12) or not math.isclose(step_time,expected["step_time"],rel_tol=6e-7,abs_tol=1e-12):
        raise ValueError("Native STA did not reach the immutable input's requested final step/time")
    final = reader.frames[-1].metadata
    if final["step"] != step or final["increment"] != increment or not math.isclose(final["value"],total,rel_tol=6e-6,abs_tol=1e-12):
        raise ValueError("Final FRD step/increment/time does not match the completed native STA endpoint")
    return expected
