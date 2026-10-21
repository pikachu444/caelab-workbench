"""Shared builders for a small cantilever model (test fixture, not a
product example: tests may construct any model they need)."""

from caelab.model.schema import (
    BoundaryCondition,
    Load,
    Material,
    MeshData,
    NamedSet,
    Section,
    Step,
    StructuralModel,
)


def cantilever_model(nx=10, ny=2, nz=2, length=1.0, width=0.1, height=0.1,
                     force_y=-1000.0) -> StructuralModel:
    nodes: dict[int, list[float]] = {}
    nid = 0
    index: dict[tuple[int, int, int], int] = {}
    for i in range(nx + 1):
        for j in range(ny + 1):
            for k in range(nz + 1):
                nid += 1
                index[(i, j, k)] = nid
                nodes[nid] = [length * i / nx, width * j / ny, height * k / nz]
    elements: dict[int, dict] = {}
    eid = 0
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                eid += 1
                n = [index[(i + di, j + dj, k + dk)] for di, dj, dk in
                     [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                      (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]]
                elements[eid] = {"type": "C3D8", "nodes": n}
    fixed = [index[(0, j, k)] for j in range(ny + 1) for k in range(nz + 1)]
    tip = [index[(nx, j, k)] for j in range(ny + 1) for k in range(nz + 1)]
    return StructuralModel(
        name="cantilever",
        sets={
            "FIX": NamedSet("node", fixed),
            "TIP": NamedSet("node", tip),
            "ALL_ELEMS": NamedSet("element", list(elements)),
        },
        mesh=MeshData(nodes=nodes, elements=elements),
        materials=[Material("STEEL", "linear_elastic",
                            {"youngs_modulus": 210e9, "poissons_ratio": 0.3,
                             "density": 7850.0})],
        sections=[Section("SOLID", "solid", "ALL_ELEMS", "STEEL")],
        steps=[Step("STEP1", "linear_static", loads=["TIP_FORCE"], bcs=["FIX_BC"])],
        loads=[Load("TIP_FORCE", "force", "TIP", {"fy": force_y})],
        bcs=[BoundaryCondition("FIX_BC", "FIX", [1, 2, 3])],
    )
