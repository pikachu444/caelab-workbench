"""Actual embedded SciPy solves against analytic and independently integrated data."""

import math

import numpy as np
import pytest
from scipy.sparse import isspmatrix_csr

from caelab.pde.scipy_solver import (
    assemble_poisson_2d, solve_eigenmodes_2d, solve_poisson_2d,
)
from caelab.solvers.base import ResultSet
from caelab.solvers.pde_solver import SciPyPDESolver


def _sine(x, y):
    return math.sin(math.pi * x) * math.sin(math.pi * y)


def _independent_l2(solution, exact):
    # Tensor Gauss on a Duffy-mapped square, independent of production triangle rule.
    points, weights = np.polynomial.legendre.leggauss(6)
    points, weights = (points + 1) / 2, weights / 2
    total = 0.0
    for ids in solution.system.triangles:
        coords = solution.system.nodes[ids]
        jac = abs(np.linalg.det(np.column_stack((coords[1] - coords[0], coords[2] - coords[0]))))
        for r, wr in zip(points, weights):
            for s, ws in zip(points, weights):
                bary = np.array([(1-r)*(1-s), r, (1-r)*s])
                xy = bary @ coords
                error = bary @ solution.values[ids] - exact(*xy)
                total += jac * (1-r) * wr * ws * error**2
    return math.sqrt(total)


def test_manufactured_solution_true_l2_and_second_order():
    errors = []
    for n in (16, 32):
        solution = solve_poisson_2d(n, n, rhs_func=lambda x, y: 2*math.pi**2*_sine(x, y))
        error = _independent_l2(solution, _sine)
        assert solution.l2_error(_sine) == pytest.approx(error, rel=2e-5)
        assert solution.relative_residual < 1e-10
        errors.append(error)
    assert errors[-1] < 0.003
    assert math.log2(errors[0] / errors[1]) == pytest.approx(2.0, abs=.15)


def test_sparse_operators_consistent_mass_and_area_scaled_l2():
    system = assemble_poisson_2d(3, 2, lx=2, ly=3, diffusion=4, density=5, rhs_func=7)
    assert isspmatrix_csr(system.stiffness) and isspmatrix_csr(system.mass)
    assert np.max(np.abs((system.stiffness - system.stiffness.T).data), initial=0) < 1e-14
    assert np.max(np.abs((system.mass - system.mass.T).data), initial=0) < 1e-14
    assert np.sum(system.mass) == pytest.approx(30)
    assert np.sum(system.load) == pytest.approx(42)
    assert np.allclose(system.stiffness @ np.ones(len(system.nodes)), 0, atol=1e-13)
    assert np.count_nonzero(system.mass.toarray() - np.diag(system.mass.diagonal())) > 0
    zero = solve_poisson_2d(3, 2, lx=2, ly=3)
    assert zero.l2_error(1.0) == pytest.approx(math.sqrt(6))
    assert np.all(zero.values == 0)


@pytest.mark.parametrize("mixed", [False, True])
def test_nonzero_dirichlet_lifting_and_neumann_sign_affine_patch(mixed):
    exact = lambda x, y: 1 + 2*x - 3*y
    solution = solve_poisson_2d(
        7, 4, lx=2, ly=3, diffusion=4,
        dirichlet_bc={"left": exact} if mixed else exact,
        neumann_bc={"right": 8, "bottom": 12, "top": -12} if mixed else None,
    )
    assert solution.values == pytest.approx([exact(*p) for p in solution.system.nodes], abs=2e-12)
    assert solution.flux == pytest.approx(np.tile([-8, 12], (len(solution.values), 1)), abs=2e-12)
    assert solution.l2_error(exact) < 2e-12
    assert np.sum(solution.reactions) + np.sum(solution.system.load) == pytest.approx(0, abs=1e-11)


def test_1d_linear_profile_and_unspecified_natural_sides():
    solution = solve_poisson_2d(9, 3, lx=2, diffusion=3,
                                dirichlet_bc={"left": 2, "right": -4})
    assert solution.values == pytest.approx(2 - 3*solution.system.nodes[:, 0], abs=1e-12)
    assert solution.flux[:, 0] == pytest.approx(np.full(len(solution.values), 9), abs=1e-12)
    assert solution.flux[:, 1] == pytest.approx(np.zeros(len(solution.values)), abs=1e-12)


def test_flux_is_incident_element_area_average():
    solution = solve_poisson_2d(3, 2, lx=2, ly=1, rhs_func=5,
                                dirichlet_bc=lambda x, y: x*x + y*y)
    flux = np.zeros_like(solution.flux)
    areas = np.zeros(len(solution.values))
    for ids in solution.system.triangles:
        xy = solution.system.nodes[ids]
        affine = np.linalg.solve(np.column_stack((np.ones(3), xy)), solution.values[ids])
        area = abs(np.linalg.det(np.column_stack((xy[1]-xy[0], xy[2]-xy[0])))) / 2
        flux[ids] -= area * affine[1:]
        areas[ids] += area
    assert solution.flux == pytest.approx(flux / areas[:, None], abs=1e-12)


def test_eigenvalues_mass_orthogonality_constraints_and_residual():
    modes = solve_eigenmodes_2d(16, 16, num_modes=3)
    assert modes.eigenvalues == pytest.approx(np.array([2, 5, 5])*math.pi**2, rel=.04)
    assert modes.modes.T @ (modes.system.mass @ modes.modes) == pytest.approx(np.eye(3), abs=1e-10)
    boundary = np.setdiff1d(np.arange(len(modes.system.nodes)), modes.free_nodes)
    assert np.all(modes.modes[boundary] == 0)
    kphi = (modes.system.stiffness @ modes.modes)[modes.free_nodes]
    mphi = (modes.system.mass @ modes.modes)[modes.free_nodes]
    assert np.linalg.norm(kphi - mphi*modes.eigenvalues) / np.linalg.norm(kphi) < 1e-9
    assert np.max(modes.relative_residuals) < 1e-9
    scaled = solve_eigenmodes_2d(16, 16, diffusion=6, density=2, num_modes=3)
    assert scaled.eigenvalues == pytest.approx(3*modes.eigenvalues, rel=1e-10)


def test_eigenvalue_mixed_boundary_rectangle():
    modes = solve_eigenmodes_2d(16, 8, lx=2, ly=1, num_modes=1,
                                dirichlet_bc={"left": 0})
    assert modes.eigenvalues[0] == pytest.approx((math.pi/4)**2, rel=.01)


@pytest.mark.parametrize("kwargs", [
    {"nx": 0}, {"ny": 2.5}, {"nx": True}, {"lx": -1}, {"ly": np.inf},
    {"diffusion": 0}, {"diffusion": np.nan}, {"rhs_func": lambda x, y: np.inf},
    {"dirichlet_bc": {}}, {"dirichlet_bc": {"left": 0, "bottom": 1}},
    {"dirichlet_bc": {"front": 0}}, {"dirichlet_bc": np.nan},
    {"dirichlet_bc": {"left": 0}, "neumann_bc": {"left": 1}},
    {"dirichlet_bc": {"left": 0}, "neumann_bc": {"right": np.nan}},
])
def test_invalid_poisson_inputs_are_rejected(kwargs):
    with pytest.raises(ValueError):
        solve_poisson_2d(**kwargs)


@pytest.mark.parametrize("kwargs", [
    {"density": 0}, {"dirichlet_bc": 1}, {"num_modes": 0},
    {"num_modes": True}, {"nx": 2, "ny": 2, "num_modes": 1},
])
def test_invalid_eigenmode_inputs_are_rejected(kwargs):
    with pytest.raises(ValueError):
        solve_eigenmodes_2d(**kwargs)


def test_resultset_shapes_units_summary_and_flux_sum_semantics():
    units = {"length": "mm", "temperature": "K", "flux": "W/mm^2"}
    result = SciPyPDESolver().solve_poisson_2d(4, 3, diffusion=2,
                                               dirichlet_bc=lambda x, y: -2-x, units=units)
    assert isinstance(result, ResultSet)
    assert result.success and result.solver_id == "scipy_pde"
    assert set(result.displacements) == set(result.stresses) == set(range(1, 21))
    assert all(len(v) == 3 and v[1:] == [0, 0] for v in result.displacements.values())
    assert all(len(v) == 6 and v[2:] == [0]*4 for v in result.stresses.values())
    assert result.max_displacement == pytest.approx(3)
    assert result.reaction_totals == pytest.approx({"fx": 40, "fy": 0, "fz": 0}, abs=1e-12)
    assert result.reactions == {}
    assert result.units == units and result.units is not units
    assert "max |u|" in result.summary() and "메시 의존" in result.summary()
    assert "최대 변위" not in result.summary() and "반력" not in result.summary()
    assert "not reactions" in result.log_tail
    assert "현재 입력 오차는 미평가" in result.verification
    assert SciPyPDESolver().solve_poisson_2d(1, 1).units == {}
    with pytest.raises(ValueError):
        SciPyPDESolver().solve_poisson_2d(diffusion=-1)
