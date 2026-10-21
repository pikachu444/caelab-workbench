"""FEniCSx PDE line — runner (own format, not the common model schema).

Ported in spirit from autonomous-cae-lab ``caelab/adapters/fenicsx_pde.py``.
Per DESIGN §3.3 / §5 the PDE line is *not* put into the common structural
model: a PDE problem is a plain JSON-shaped dict

    {"problem": {"domain": "unit_square",
                  "weak_form": {"diffusion": 1.0, "reaction": 0.0, "rhs": "..."},
                  "dirichlet": "0.0",
                  "reference": {"solution": "...", "source": "..."}},
     "mesh": {"cell_counts": [8, 16, 32], "degree": 1},
     "validation": {"max_l2_error": ..., "min_l2_rate": ...,
                    "max_residual_relative": ...}}

Expressions use the bounded AST language of `caelab.pde.worker`
(pi, x[0], x[1], sin/cos/exp, arithmetic — no Python eval).

The actual solve runs in an isolated subprocess (`python -I worker.py`)
so the workbench process never imports dolfinx/PETSc itself. The
interpreter comes only from the ``CAELAB_FENICSX_PYTHON`` environment
variable (caelab package rule: no execution paths hardcoded in code).
On this machine that variable should point at the workspace install
``~/workspace/tools/fenicsx-env/bin/python`` — under ~/workspace, so
it survives VM replacement, unlike anything under /usr. Registering
that variable in the connection layer is requested in MERGE_REQUEST.md.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .worker import PDEInputError, validate_settings

WORKER = Path(__file__).with_name("worker.py")
WORKER_TIMEOUT = 300

LIMITATIONS = [
    "단위 정사각형, 상수 확산·반응 계수인 스칼라 선형 타원형 문제만 다룹니다.",
    "경계 전체에 상수 디리클레 값, 1차 삼각형 요소를 씁니다.",
    "오차는 해석해(제조해)와 비교한 값이며, H1 오차는 기울기만 본 세미노름입니다.",
]


def find_fenicsx_python() -> str | None:
    """The dolfinx-carrying Python, from CAELAB_FENICSX_PYTHON only."""
    override = os.environ.get("CAELAB_FENICSX_PYTHON")
    if override:
        return override if os.path.exists(override) else None
    return None


def poisson_settings(cell_counts=(8, 16, 32)) -> dict:
    """The classic Poisson benchmark with a known analytical solution.

    -Δu = 2π²·sin(πx)·sin(πy) on the unit square, u = 0 on the boundary,
    exact solution u = sin(πx)·sin(πy). P1 elements converge at L2 rate 2.
    """
    return {
        "problem": {
            "domain": "unit_square",
            "weak_form": {
                "diffusion": 1.0,
                "reaction": 0.0,
                "rhs": "2*pi**2*sin(pi*x[0])*sin(pi*x[1])",
            },
            "dirichlet": "0.0",
            "reference": {
                "solution": "sin(pi*x[0])*sin(pi*x[1])",
                "source": "Analytical manufactured solution, dimensionless unit square",
            },
        },
        "mesh": {"cell_counts": list(cell_counts), "degree": 1},
        "validation": {
            "max_l2_error": 0.003,
            "min_l2_rate": 1.8,
            "max_residual_relative": 1e-10,
        },
    }


def run_pde(settings: dict, output: str | Path, *, python: str | None = None,
            timeout: int = WORKER_TIMEOUT) -> dict:
    """Validate, run in a subprocess, and judge against the thresholds.

    Returns a plain dict: status ("COMPLETED" | "REJECTED" | "FAILED"),
    checks, the per-mesh studies (errors, rates, residuals), the library
    versions the worker actually used, and the interpreter path.
    Raises only for a non-empty output directory (never overwritten).
    """
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("PDE output directory must be new or empty")
    output.mkdir(parents=True, exist_ok=True)

    try:
        checked = validate_settings(settings)
    except PDEInputError as exc:
        result = {"status": "REJECTED", "passed": False,
                  "checks": [{"code": "pde_preflight", "status": "FAIL",
                              "observed": str(exc)}],
                  "mesh_studies": [], "versions": {},
                  "limitations": list(LIMITATIONS)}
        (output / "result.json").write_text(
            json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        return result

    (output / "input.json").write_text(
        json.dumps(checked, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8")
    shutil.copyfile(WORKER, output / "worker.py")

    interpreter = python or find_fenicsx_python()
    if not interpreter:
        result = {"status": "FAILED", "passed": False,
                  "reason": "FEniCSx용 파이썬을 찾지 못했습니다 (CAELAB_FENICSX_PYTHON 미설정)",
                  "mesh_studies": [], "versions": {}, "limitations": list(LIMITATIONS)}
        (output / "result.json").write_text(
            json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        return result

    env = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PETSC_DIR"):
        env.pop(name, None)
    try:
        proc = subprocess.run(
            [interpreter, "-I", str((output / "worker.py").resolve()),
             str((output / "input.json").resolve())],
            cwd=output.resolve(), capture_output=True, text=True,
            timeout=timeout, check=False, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        (output / "stderr.log").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        result = {"status": "FAILED", "passed": False, "reason": str(exc),
                  "interpreter": interpreter, "mesh_studies": [], "versions": {},
                  "limitations": list(LIMITATIONS)}
        (output / "result.json").write_text(
            json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        return result

    (output / "stdout.log").write_text(proc.stdout or "", encoding="utf-8")
    (output / "stderr.log").write_text(proc.stderr or "", encoding="utf-8")
    raw_path = output / "worker_result.json"
    if proc.returncode != 0 or not raw_path.is_file():
        detail = (proc.stderr or "").strip()[-800:]
        result = {"status": "FAILED", "passed": False,
                  "reason": f"worker exit {proc.returncode}: {detail}",
                  "interpreter": interpreter, "mesh_studies": [], "versions": {},
                  "limitations": list(LIMITATIONS)}
        (output / "result.json").write_text(
            json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        return result

    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    studies = raw["mesh_studies"]
    limits = checked["validation"]
    finest = studies[-1]
    checks = [
        {"code": "l2_error", "status": "PASS" if finest["l2_error"] <= limits["max_l2_error"] else "FAIL",
         "observed": finest["l2_error"], "limit": limits["max_l2_error"]},
        {"code": "l2_rate", "status": "PASS" if (finest["l2_convergence_rate"] or 0) >= limits["min_l2_rate"] else "FAIL",
         "observed": finest["l2_convergence_rate"], "limit": limits["min_l2_rate"]},
        {"code": "residual", "status": "PASS" if finest["linear_residual"]["relative"] <= limits["max_residual_relative"] else "FAIL",
         "observed": finest["linear_residual"]["relative"], "limit": limits["max_residual_relative"]},
    ]
    passed = all(c["status"] == "PASS" for c in checks)
    result = {"status": "COMPLETED", "passed": passed, "checks": checks,
              "mesh_studies": studies, "versions": raw.get("versions", {}),
              "interpreter": interpreter, "limitations": list(LIMITATIONS)}
    (output / "result.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result
