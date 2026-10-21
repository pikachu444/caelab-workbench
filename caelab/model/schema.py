"""The one common structural model (redesign decision D1).

The model is a plain-data document. Solvers are converters from this
document; analysis *kinds* live in ``steps`` and features such as bolt
pretension or laminated shells are options inside sections/loads, never
separate model types. PDE, material-point and external-calculator
documents keep their own formats and are not part of this schema.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = 1

STEP_KINDS = (
    "linear_static",
    "nonlinear_static",
    "dynamic",
    "modal",
    "buckling",
    "frequency_response",
    "thermal",
    "thermal_structural",
    "explicit",
)

ELEMENT_TYPES = (
    "C3D4", "C3D10", "C3D8", "C3D8R", "C3D20",
    "C3D6", "C3D15",
    "S3", "S6", "S4", "S8R",
    "B31", "B32",
)


@dataclass
class NamedSet:
    """A named selection of nodes, elements, or element faces."""

    kind: str  # "node" | "element" | "face"
    members: list = field(default_factory=list)  # ids, or [element_id, "S1"] for faces


@dataclass
class MeshData:
    """Mesh payload. Inline for now; a file reference can replace the
    inline arrays without changing the rest of the schema."""

    nodes: dict = field(default_factory=dict)      # id -> [x, y, z]
    elements: dict = field(default_factory=dict)   # id -> {"type": str, "nodes": [ids]}
    source: str | None = None                      # e.g. originating FreeCAD document


@dataclass
class Material:
    name: str
    law: str  # "linear_elastic" | "j2_plasticity" | ...
    params: dict = field(default_factory=dict)     # SI units; law-specific keys
    assigned_set: str | None = None                # element set name; None = whole model


@dataclass
class Section:
    name: str
    kind: str  # "solid" | "shell" | "beam"
    element_set: str
    material: str
    options: dict = field(default_factory=dict)    # thickness / layup / profile


@dataclass
class Load:
    name: str
    kind: str  # "force" | "pressure" | "gravity" | "displacement" | "temperature"
    target_set: str | None = None
    values: dict = field(default_factory=dict)     # components / magnitude / direction


@dataclass
class BoundaryCondition:
    name: str
    target_set: str  # node set name
    fixed_dofs: list = field(default_factory=list)  # subset of [1, 2, 3] (UX, UY, UZ)
    values: dict = field(default_factory=dict)      # prescribed values per dof, default 0


@dataclass
class Interaction:
    name: str
    kind: str  # "contact" | "tie" | "spring" | "rigid"
    pair: list = field(default_factory=list)       # [set_a, set_b]
    options: dict = field(default_factory=dict)    # friction, stiffness, ...


@dataclass
class Step:
    name: str
    kind: str  # one of STEP_KINDS
    loads: list = field(default_factory=list)          # Load names active in this step
    bcs: list = field(default_factory=list)            # BoundaryCondition names
    options: dict = field(default_factory=dict)        # nlgeom, increments, time, ...


@dataclass
class StructuralModel:
    name: str
    unit_system: str = "si"  # metres, kilograms, seconds, pascals
    geometry_source: str | None = None
    sets: dict = field(default_factory=dict)       # name -> NamedSet
    mesh: MeshData = field(default_factory=MeshData)
    materials: list = field(default_factory=list)  # Material
    sections: list = field(default_factory=list)   # Section
    steps: list = field(default_factory=list)      # Step
    loads: list = field(default_factory=list)      # Load
    bcs: list = field(default_factory=list)        # BoundaryCondition
    interactions: list = field(default_factory=list)  # Interaction
    initial_conditions: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=lambda: {"fields": ["U", "S", "RF"]})
    schema_version: int = SCHEMA_VERSION

    # -- serialization -------------------------------------------------
    def to_dict(self) -> dict:
        data = asdict(self)
        data["schema_version"] = self.schema_version
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "StructuralModel":
        version = data.get("schema_version", 1)
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported model schema version {version}; expected {SCHEMA_VERSION}"
            )
        mesh = data.get("mesh") or {}
        return cls(
            name=data["name"],
            unit_system=data.get("unit_system", "si"),
            geometry_source=data.get("geometry_source"),
            sets={
                name: NamedSet(kind=s["kind"], members=list(s.get("members", [])))
                for name, s in (data.get("sets") or {}).items()
            },
            mesh=MeshData(
                nodes={int(k): list(v) for k, v in (mesh.get("nodes") or {}).items()},
                elements={
                    int(k): {"type": v["type"], "nodes": list(v["nodes"])}
                    for k, v in (mesh.get("elements") or {}).items()
                },
                source=mesh.get("source"),
            ),
            materials=[Material(**m) for m in data.get("materials", [])],
            sections=[Section(**s) for s in data.get("sections", [])],
            steps=[Step(**s) for s in data.get("steps", [])],
            loads=[Load(**ld) for ld in data.get("loads", [])],
            bcs=[BoundaryCondition(**b) for b in data.get("bcs", [])],
            interactions=[Interaction(**i) for i in data.get("interactions", [])],
            initial_conditions=dict(data.get("initial_conditions") or {}),
            outputs=dict(data.get("outputs") or {"fields": ["U", "S", "RF"]}),
            schema_version=version,
        )

    # -- lookups --------------------------------------------------------
    def material(self, name: str) -> Material:
        for m in self.materials:
            if m.name == name:
                return m
        raise KeyError(f"material not found: {name}")

    def load(self, name: str) -> Load:
        for ld in self.loads:
            if ld.name == name:
                return ld
        raise KeyError(f"load not found: {name}")

    def bc(self, name: str) -> BoundaryCondition:
        for b in self.bcs:
            if b.name == name:
                return b
        raise KeyError(f"boundary condition not found: {name}")
