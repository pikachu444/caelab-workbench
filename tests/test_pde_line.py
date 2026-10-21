"""PDE line (FEniCSx): preflight unit tests + a real-solve smoke test.

The unit tests need no dolfinx (the parser/validator are stdlib only).
The smoke test runs the actual Poisson benchmark — exact solution
u = sin(pi x) sin(pi y) — and is skipped when no FEniCSx interpreter
is available.
"""

import math
import subprocess

import pytest

from caelab.pde import poisson_settings, run_pde, validate_settings
from caelab.pde.runner import find_fenicsx_python
from caelab.pde.worker import PDEInputError, constant_expression, parse_expression, interpret_expression


def test_poisson_settings_are_valid_and_self_consistent():
    settings = poisson_settings()
    checked = validate_settings(settings)
    assert checked["mesh"]["cell_counts"] == [8, 16, 32]
    # rhs at the centre must be 2*pi**2 for the manufactured solution
    functions = {name: getattr(math, name) for name in ("sin", "cos", "exp")}
    tree = parse_expression(checked["problem"]["weak_form"]["rhs"])
    assert interpret_expression(tree, [0.5, 0.5], functions) == pytest.approx(2 * math.pi**2)
    assert constant_expression(checked["problem"]["dirichlet"]) == 0.0


@pytest.mark.parametrize("bad", [
    "x[2]", "sin(x[0], x[1])", "__import__('os')", "x[0]**x[1]", "1/0", "",
])
def test_expression_language_rejects_unsafe_or_wrong_input(bad):
    with pytest.raises(PDEInputError):
        parse_expression(bad)


def test_invalid_settings_are_rejected_without_running(tmp_path):
    settings = poisson_settings()
    settings["problem"]["weak_form"]["diffusion"] = 0.0
    result = run_pde(settings, tmp_path / "out")
    assert result["status"] == "REJECTED"
    assert result["passed"] is False
    assert not (tmp_path / "out" / "worker_result.json").exists()


def _fenicsx_available() -> bool:
    python = find_fenicsx_python()
    if not python:
        return False
    try:
        proc = subprocess.run([python, "-c", "import dolfinx"],
                              capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


@pytest.mark.skipif(not _fenicsx_available(), reason="FEniCSx interpreter not available")
def test_poisson_solve_matches_analytical_solution(tmp_path):
    result = run_pde(poisson_settings(), tmp_path / "poisson")
    assert result["status"] == "COMPLETED", result.get("reason")
    assert result["passed"], result["checks"]
    finest = result["mesh_studies"][-1]
    # P1 on 32x32: L2 error well under 1e-3 territory, rate close to 2
    assert finest["l2_error"] < 0.003
    assert finest["l2_convergence_rate"] == pytest.approx(2.0, abs=0.25)
    assert finest["linear_residual"]["relative"] < 1e-10
    assert result["versions"]["dolfinx"]
