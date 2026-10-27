"""General-purpose native CalculiX evaluator for caelab.mdo.optimize.

Connects caelab.mdo.optimize with native CalculiX FEA execution:
- Binds candidate design vectors into any TemplateDeck via token replacement.
- Executes the ccx binary in an isolated scratch directory.
- Extracts measured responses (displacements, von Mises stresses, DAT masses).
- Returns EvaluationObservation records complying with the MDO optimization contract.
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping, Sequence

from caelab.mdo.inp_binder import TemplateDeck
from caelab.mdo.optimize import EvaluationObservation, EvaluationRequest
from caelab.native.results import _Reader

DEFAULT_CCX = r"C:\SourceCodes\caelab-p7-public-native\windows\runtime\FreeCAD_1.1.4-Windows-x86_64-py311\bin\ccx.exe"


def extract_calculix_responses(workdir: Path, run_result: dict | None = None) -> dict[str, float]:
    """Final FRD nodal fields and same-time DAT set totals, in declared deck units."""
    obs = {}
    frd_path = workdir / "model.frd"
    final_time = None
    if frd_path.is_file():
        try:
            with frd_path.open(encoding="utf-8", errors="strict") as stream:
                reader = _Reader(stream, lambda: None)
                reader.read()
            if reader.frames:
                frame = reader.frames[-1]
                final_time = frame.metadata["value"]
                for result in frame.fields:
                    if result.warnings or not result.values or set(result.values) != set(reader.nodes):
                        continue
                    names = [c["name"].upper() for c in result.stored]
                    values = list(result.values.values())
                    if not all(math.isfinite(v) for row in values for v in row):
                        continue
                    if result.name == "DISP" and all(k in names for k in ("D1", "D2", "D3")):
                        obs["disp"] = max(math.hypot(*(row[names.index(k)] for k in ("D1", "D2", "D3"))) for row in values)
                    elif result.name == "STRESS" and all(k in names for k in ("SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX")):
                        vm = []
                        for row in values:
                            xx, yy, zz, xy, yz, zx = [row[names.index(k)] for k in ("SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX")]
                            vm.append(math.sqrt(.5 * ((xx-yy)**2 + (yy-zz)**2 + (zz-xx)**2) + 3 * (xy**2 + yz**2 + zx**2)))
                        obs["stress"] = max(vm)
        except (OSError, ValueError, OverflowError):
            return {}
    dat_path = workdir / "model.dat"
    if dat_path.is_file() and final_time is not None:
        text = dat_path.read_text(encoding="utf-8", errors="replace")
        # CalculiX printout.f EMAS TOTALS=ONLY: header, blank line, scalar.
        pattern = r"total mass for set\s+(\S+)\s+and time\s+([+\-\d.eEdD]+)\s*\n\s*([+\-\d.eEdD]+)"
        for match in re.finditer(pattern, text, re.IGNORECASE):
            name, stamp, raw = match.groups()
            try:
                stamp, mass = float(stamp.replace("D", "E")), float(raw.replace("D", "E"))
                if math.isclose(stamp, final_time, rel_tol=1e-6, abs_tol=1e-12) and math.isfinite(mass) and mass >= 0:
                    obs[f"mass_{name.lower()}"] = mass
            except ValueError:
                pass
    return {key: value for key, value in obs.items() if math.isfinite(value)}


class CalculiXEvaluator:
    """Evaluates design point batches against any native CalculiX template deck."""

    def __init__(
        self,
        template: TemplateDeck,
        *,
        ccx_bin: str | Path | None = None,
        timeout: float = 300.0,
        extra_files: Sequence[Path] = (),
    ) -> None:
        self.template = template
        self.ccx_bin = str(ccx_bin or os.environ.get("CAELAB_CCX", DEFAULT_CCX))
        self.timeout = timeout
        self.extra_files = tuple(extra_files)
        if not os.path.isfile(self.ccx_bin):
            raise FileNotFoundError(f"CalculiX binary not found at: {self.ccx_bin}")

    def evaluate_batch(
        self, requests: tuple[EvaluationRequest, ...]
    ) -> dict[str, EvaluationObservation]:
        observations = {}
        for req in requests:
            clean_id = req.point_id.replace(":", "_").replace("/", "_")
            with tempfile.TemporaryDirectory(prefix=f"mdo_eval_{clean_id}_") as tmpdir:
                work = Path(tmpdir)
                for extra in self.extra_files:
                    if extra.is_file():
                        (work / extra.name).write_bytes(extra.read_bytes())

                rendered = self.template.render(req.values)
                inp_file = work / "model.inp"
                inp_file.write_text(rendered, encoding="utf-8")

                try:
                    proc = subprocess.run(
                        [self.ccx_bin, "model"],
                        cwd=work,
                        capture_output=True,
                        text=True,
                        timeout=self.timeout,
                    )
                    run_result = {
                        "state": "completed" if proc.returncode == 0 else "failed",
                        "returncode": proc.returncode,
                    }
                    responses = (
                        extract_calculix_responses(work, run_result)
                        if proc.returncode == 0
                        else {}
                    )
                    observations[req.id] = EvaluationObservation(
                        request_id=req.id,
                        point_id=req.point_id,
                        responses=responses,
                        valid=bool(proc.returncode == 0 and responses),
                        reason=None if proc.returncode == 0 else (proc.stderr or proc.stdout)[:500],
                        mode=req.mode,
                        source_id=f"calculix:{Path(self.ccx_bin).name}",
                    )
                except subprocess.TimeoutExpired:
                    observations[req.id] = EvaluationObservation(
                        request_id=req.id,
                        point_id=req.point_id,
                        responses={},
                        valid=False,
                        reason=f"Timeout after {self.timeout}s",
                        mode=req.mode,
                        source_id=f"calculix:{Path(self.ccx_bin).name}",
                    )
                except Exception as exc:
                    observations[req.id] = EvaluationObservation(
                        request_id=req.id,
                        point_id=req.point_id,
                        responses={},
                        valid=False,
                        reason=str(exc),
                        mode=req.mode,
                        source_id=f"calculix:{Path(self.ccx_bin).name}",
                    )
        return observations
