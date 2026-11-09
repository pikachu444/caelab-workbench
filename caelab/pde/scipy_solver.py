"""Embedded scalar T3 FEM on rectangles; no FEniCSx or structural schema.

Solves -div(k grad(u)) = f with constant positive k. Coordinates and data must
use caller-consistent units. Neumann data is g = k du/dn (outward heat flux
is -g). Eigenvalues solve K phi = lambda M phi; their physical interpretation
depends on the supplied diffusion and mass density, and is not assumed to be Hz.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import numpy as np
from scipy.sparse import coo_matrix, csr_matrix
from scipy.sparse.linalg import eigsh, spsolve

ScalarField = float | Callable[[float, float], float]
BoundaryData = ScalarField | Mapping[str, ScalarField] | None

# Degree-five triangle rule, weights normalized to unit area.
_BARY = np.array([
    [1/3, 1/3, 1/3],
    [.059715871789770, .470142064105115, .470142064105115],
    [.470142064105115, .059715871789770, .470142064105115],
    [.470142064105115, .470142064105115, .059715871789770],
    [.797426985353087, .101286507323456, .101286507323456],
    [.101286507323456, .797426985353087, .101286507323456],
    [.101286507323456, .101286507323456, .797426985353087],
])
_WEIGHTS = np.array([.225, *([.132394152788506] * 3), *([.125939180544827] * 3)])


def _sample(field: ScalarField, points: np.ndarray) -> np.ndarray:
    if callable(field):
        values = np.array([float(field(float(x), float(y))) for x, y in points])
    else:
        values = np.full(len(points), float(field))
    if not np.all(np.isfinite(values)):
        raise ValueError("Scalar data must be finite")
    return values


def _positive(value: float, name: str) -> float:
    value = float(value)
    if not np.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


@dataclass
class FEMSystem:
    """Zero-based mesh and unconstrained, symmetric assembled operators."""

    nodes: np.ndarray
    triangles: np.ndarray
    stiffness: csr_matrix
    mass: csr_matrix
    load: np.ndarray
    areas: np.ndarray
    gradients: np.ndarray
    sides: dict[str, np.ndarray]
    diffusion: float


@dataclass
class PoissonSolution:
    system: FEMSystem
    values: np.ndarray
    flux: np.ndarray  # area-weighted nodal q = -k grad(u)
    constrained_nodes: np.ndarray
    reactions: np.ndarray  # scalar K u - F at constrained nodes, not forces
    relative_residual: float

    def l2_error(self, exact: ScalarField) -> float:
        """Integrate (u_h - exact)^2 inside elements, rather than nodal RMS."""
        system = self.system
        coords = system.nodes[system.triangles]
        nodal = self.values[system.triangles]
        integral = 0.0
        for bary, weight in zip(_BARY, _WEIGHTS):
            difference = nodal @ bary - _sample(exact, np.einsum("eij,i->ej", coords, bary))
            integral += weight * float(np.dot(system.areas, difference**2))
        return float(np.sqrt(integral))


@dataclass
class Eigenmodes:
    system: FEMSystem
    eigenvalues: np.ndarray
    modes: np.ndarray  # columns, including zero constrained DOFs; M-orthonormal
    free_nodes: np.ndarray
    relative_residuals: np.ndarray


def assemble_poisson_2d(nx=32, ny=32, lx=1.0, ly=1.0, diffusion=1.0,
                        rhs_func: ScalarField | None = None, *, density=1.0) -> FEMSystem:
    """Assemble K, consistent M and F on nx by ny cells (two T3 per cell).

    rhs_func=None means zero source. Scalar data can also be f(x, y) callables.
    density is the positive constant coefficient in the mass integral.
    """
    for count in (nx, ny):
        if isinstance(count, bool) or not isinstance(count, (int, np.integer)) or count < 1:
            raise ValueError("nx and ny must be positive integers")
    lx, ly = _positive(lx, "lx"), _positive(ly, "ly")
    diffusion, density = _positive(diffusion, "diffusion"), _positive(density, "density")
    x, y = np.meshgrid(np.linspace(0, lx, nx + 1), np.linspace(0, ly, ny + 1))
    nodes = np.column_stack((x.ravel(), y.ravel()))
    grid = np.arange(len(nodes)).reshape(ny + 1, nx + 1)
    a, b, c, d = (part.ravel() for part in
                  (grid[:-1, :-1], grid[:-1, 1:], grid[1:, 1:], grid[1:, :-1]))
    triangles = np.concatenate((np.column_stack((a, b, c)), np.column_stack((a, c, d))))
    coords = nodes[triangles]
    edge1, edge2 = coords[:, 1] - coords[:, 0], coords[:, 2] - coords[:, 0]
    twice_area = edge1[:, 0] * edge2[:, 1] - edge1[:, 1] * edge2[:, 0]
    if not np.all(np.isfinite(twice_area)) or np.any(twice_area <= 0):
        raise ValueError("Mesh has nonfinite or degenerate triangles")
    areas = twice_area / 2
    gradients = np.stack((
        np.column_stack((coords[:, 1, 1] - coords[:, 2, 1], coords[:, 2, 0] - coords[:, 1, 0])),
        np.column_stack((coords[:, 2, 1] - coords[:, 0, 1], coords[:, 0, 0] - coords[:, 2, 0])),
        np.column_stack((coords[:, 0, 1] - coords[:, 1, 1], coords[:, 1, 0] - coords[:, 0, 0])),
    ), axis=1) / twice_area[:, None, None]
    ke = diffusion * areas[:, None, None] * np.einsum("eik,ejk->eij", gradients, gradients)
    me = density * areas[:, None, None] * (np.ones((3, 3)) + np.eye(3)) / 12
    rows = np.broadcast_to(triangles[:, :, None], ke.shape).ravel()
    cols = np.broadcast_to(triangles[:, None, :], ke.shape).ravel()
    shape = (len(nodes), len(nodes))
    stiffness = coo_matrix((ke.ravel(), (rows, cols)), shape=shape).tocsr()
    mass = coo_matrix((me.ravel(), (rows, cols)), shape=shape).tocsr()
    fe = np.zeros_like(triangles, dtype=float)
    for bary, weight in zip(_BARY, _WEIGHTS):
        points = np.einsum("eij,i->ej", coords, bary)
        values = _sample(0.0 if rhs_func is None else rhs_func, points)
        fe += (areas * weight * values)[:, None] * bary
    load = np.zeros(len(nodes))
    np.add.at(load, triangles.ravel(), fe.ravel())
    if not all(np.all(np.isfinite(v)) for v in (stiffness.data, mass.data, load, gradients)):
        raise ValueError("Assembly produced nonfinite values")
    sides = {"left": grid[:, 0], "right": grid[:, -1],
             "bottom": grid[0], "top": grid[-1]}
    return FEMSystem(nodes, triangles, stiffness, mass, load, areas, gradients, sides, diffusion)


def _boundary(system: FEMSystem, dirichlet_bc: BoundaryData,
              neumann_bc: Mapping[str, ScalarField] | None = None):
    prescribed = ({side: 0.0 for side in system.sides} if dirichlet_bc is None else
                  dict(dirichlet_bc) if isinstance(dirichlet_bc, Mapping) else
                  {side: dirichlet_bc for side in system.sides})
    natural = {} if neumann_bc is None else dict(neumann_bc)
    if (prescribed.keys() | natural.keys()) - system.sides.keys():
        raise ValueError("Boundary names must be left, right, bottom or top")
    if prescribed.keys() & natural.keys():
        raise ValueError("Dirichlet and Neumann sides must not overlap")
    if not prescribed:
        raise ValueError("At least one Dirichlet side is required; pure Neumann is unsupported")
    fixed = {}
    for side, field in prescribed.items():
        ids = system.sides[side]
        for node, value in zip(ids, _sample(field, system.nodes[ids])):
            if node in fixed and fixed[node] != value:
                raise ValueError("Conflicting Dirichlet values at a boundary corner")
            fixed[node] = value
    ids = np.array(sorted(fixed), dtype=int)
    values = np.array([fixed[node] for node in ids])
    load = system.load.copy()
    # Two-point Gauss rule on each Neumann edge, including consistent nodal load.
    for side, field in natural.items():
        side_nodes = system.sides[side]
        edges = np.column_stack((side_nodes[:-1], side_nodes[1:]))
        coords = system.nodes[edges]
        lengths = np.linalg.norm(coords[:, 1] - coords[:, 0], axis=1)
        for t in ((1 - 1/np.sqrt(3))/2, (1 + 1/np.sqrt(3))/2):
            basis = np.array([1 - t, t])
            data = _sample(field, np.einsum("eij,i->ej", coords, basis))
            np.add.at(load, edges.ravel(), (lengths[:, None] * data[:, None] * basis / 2).ravel())
    if not np.all(np.isfinite(load)):
        raise ValueError("Boundary loads must remain finite")
    free = np.setdiff1d(np.arange(len(system.nodes)), ids)
    return ids, values, free, load


def solve_poisson_2d(nx=32, ny=32, lx=1.0, ly=1.0, diffusion=1.0,
                     rhs_func: ScalarField | None = None, dirichlet_bc: BoundaryData = None,
                     *, neumann_bc: Mapping[str, ScalarField] | None = None) -> PoissonSolution:
    """Solve steady diffusion with symmetric free-DOF elimination.

    None prescribes zero on all sides; scalar/callable prescribes all sides.
    A side-to-data mapping prescribes only those sides. Unspecified sides have
    g=0; neumann_bc provides g=k du/dn on other sides. At least one Dirichlet
    side is required. Incompatible corner prescriptions are rejected.
    """
    system = assemble_poisson_2d(nx, ny, lx, ly, diffusion, rhs_func)
    fixed, prescribed, free, load = _boundary(system, dirichlet_bc, neumann_bc)
    values = np.zeros(len(system.nodes))
    values[fixed] = prescribed
    reduced_rhs = load[free] - system.stiffness[free][:, fixed] @ prescribed
    if len(free):
        values[free] = spsolve(system.stiffness[free][:, free], reduced_rhs)
    residual = system.stiffness @ values - load
    scale = max(np.linalg.norm(load[free]), np.linalg.norm(reduced_rhs), np.finfo(float).tiny)
    relative = float(np.linalg.norm(residual[free]) / scale)
    element_flux = -system.diffusion * np.einsum("ei,eij->ej", values[system.triangles], system.gradients)
    flux = np.zeros_like(system.nodes)
    nodal_areas = np.zeros(len(values))
    for local in range(3):
        np.add.at(flux, system.triangles[:, local], element_flux * system.areas[:, None])
        np.add.at(nodal_areas, system.triangles[:, local], system.areas)
    flux /= nodal_areas[:, None]
    if not all(np.all(np.isfinite(v)) for v in (values, flux, residual)) or not np.isfinite(relative):
        raise RuntimeError("Poisson solve produced nonfinite output")
    if relative > 1e-8:
        raise RuntimeError(f"Poisson solve residual too large: {relative:.3g}")
    # Include natural boundary loads in the retained original-equation system.
    system.load = load
    return PoissonSolution(system, values, flux, fixed, residual[fixed], relative)


def solve_eigenmodes_2d(nx=32, ny=32, lx=1.0, ly=1.0, diffusion=1.0,
                        num_modes=6, *, density=1.0,
                        dirichlet_bc: BoundaryData = None) -> Eigenmodes:
    """Lowest generic eigenpairs, homogeneous essential BC and natural g=0.

    num_modes must be strictly smaller than the number of free DOFs (eigsh).
    Nonzero Dirichlet values do not define a linear eigenspace and are rejected.
    """
    system = assemble_poisson_2d(nx, ny, lx, ly, diffusion, density=density)
    _, prescribed, free, _ = _boundary(system, dirichlet_bc)
    if np.any(prescribed != 0):
        raise ValueError("Eigenmodes require homogeneous Dirichlet values")
    if (isinstance(num_modes, bool) or not isinstance(num_modes, (int, np.integer))
            or not 1 <= num_modes < len(free)):
        raise ValueError("num_modes must be a positive integer smaller than the free DOF count")
    k, m = system.stiffness[free][:, free], system.mass[free][:, free]
    eigenvalues, vectors = eigsh(k, M=m, k=num_modes, which="SM", tol=1e-10)
    order = np.argsort(eigenvalues)
    eigenvalues, vectors = eigenvalues[order], vectors[:, order]
    vectors /= np.sqrt(np.sum(vectors * (m @ vectors), axis=0))
    kv, mv = k @ vectors, m @ vectors
    residuals = np.linalg.norm(kv - mv * eigenvalues, axis=0) / (
        np.linalg.norm(kv, axis=0) + np.abs(eigenvalues) * np.linalg.norm(mv, axis=0))
    if (not np.all(np.isfinite(eigenvalues)) or np.any(eigenvalues <= 0)
            or not np.all(np.isfinite(vectors)) or not np.all(np.isfinite(residuals))
            or np.any(residuals > 1e-8)):
        raise RuntimeError("Eigenmode solve failed the finite, positive eigenvalue/residual checks")
    modes = np.zeros((len(system.nodes), num_modes))
    modes[free] = vectors
    return Eigenmodes(system, eigenvalues, modes, free, residuals)
