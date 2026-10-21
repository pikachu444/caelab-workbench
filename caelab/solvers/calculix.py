"""CalculiX converter: common model -> .inp deck -> ccx -> .frd results.

One converter with options replaces the former per-feature CalculiX
backends (frozen/contact/shell/beam/pretension/...). Element, material
and step coverage grows inside this converter and its support table,
not by adding new backends.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..model.schema import StructuralModel
from ..validate.checker import Issue
from .base import ResultSet, SupportTable

FACE_TO_P = {"S1": "P1", "S2": "P2", "S3": "P3", "S4": "P4", "S5": "P5", "S6": "P6"}


def _id_lines(ids) -> list[str]:
    """CalculiX accepts at most 16 entries per data line."""
    ids = [str(i) for i in ids]
    return [", ".join(ids[i:i + 16]) for i in range(0, len(ids), 16)] or [""]


def find_ccx() -> str | None:
    return os.environ.get("CAELAB_CCX") or shutil.which("ccx")


class CalculiXConverter:
    id = "calculix"

    def support(self) -> SupportTable:
        return SupportTable(
            elements=frozenset({"C3D4", "C3D10", "C3D8", "C3D8R"}),
            materials=frozenset({"linear_elastic", "j2_plasticity"}),
            steps=frozenset({"linear_static", "nonlinear_static"}),
            loads=frozenset({"force", "pressure", "gravity"}),
            interactions=frozenset(),
        )

    def validate(self, model: StructuralModel) -> list[Issue]:
        return self.support().check(model)

    # -- deck writing ---------------------------------------------------
    def write_input(self, model: StructuralModel, workdir: Path) -> Path:
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        deck = workdir / "job.inp"
        deck.write_text(self._deck_text(model), encoding="utf-8")
        return deck

    def _deck_text(self, model: StructuralModel) -> str:
        lines: list[str] = [f"*HEADING\n{model.name}"]
        # nodes / elements
        lines.append("*NODE")
        for nid in sorted(model.mesh.nodes):
            x, y, z = model.mesh.nodes[nid]
            lines.append(f"{nid}, {x!r}, {y!r}, {z!r}")
        by_type: dict[str, list[int]] = {}
        for eid, elem in model.mesh.elements.items():
            by_type.setdefault(elem["type"], []).append(eid)
        for etype, eids in sorted(by_type.items()):
            lines.append(f"*ELEMENT, TYPE={etype}, ELSET=EL_{etype}")
            for eid in sorted(eids):
                conn = ", ".join(str(n) for n in model.mesh.elements[eid]["nodes"])
                lines.append(f"{eid}, {conn}")
        all_elements = sorted(model.mesh.elements)
        lines.append("*ELSET, ELSET=ALL")
        lines.extend(_id_lines(all_elements))
        # named sets
        for name, s in model.sets.items():
            if s.kind == "node":
                lines.append(f"*NSET, NSET={name}")
                lines.extend(_id_lines(s.members))
            elif s.kind == "element":
                lines.append(f"*ELSET, ELSET={name}")
                lines.extend(_id_lines(s.members))
        # materials
        needs_density = any(ld.kind == "gravity" for ld in model.loads)
        for m in model.materials:
            lines.append(f"*MATERIAL, NAME={m.name}")
            lines.append("*ELASTIC")
            lines.append(f"{m.params['youngs_modulus']!r}, {m.params['poissons_ratio']!r}")
            if "density" in m.params or needs_density:
                lines.append("*DENSITY")
                lines.append(f"{m.params.get('density', 0.0)!r}")
            if m.law == "j2_plasticity":
                lines.append("*PLASTIC")
                for stress, pstrain in m.params.get("plastic", []):
                    lines.append(f"{stress!r}, {pstrain!r}")
        # sections: explicit sections win; otherwise one per material
        if model.sections:
            for sec in model.sections:
                lines.append(
                    f"*SOLID SECTION, ELSET={sec.element_set}, MATERIAL={sec.material}"
                )
        else:
            for m in model.materials:
                elset = m.assigned_set or "ALL"
                lines.append(f"*SOLID SECTION, ELSET={elset}, MATERIAL={m.name}")
        # steps
        for step in model.steps:
            nlgeom = step.kind == "nonlinear_static" or step.options.get("nlgeom")
            lines.append("*STEP, NLGEOM" if nlgeom else "*STEP")
            lines.append("*STATIC")
            for bname in step.bcs:
                bc = model.bc(bname)
                for dof in bc.fixed_dofs:
                    value = bc.values.get(str(dof), bc.values.get(dof, 0.0))
                    lines.append("*BOUNDARY")
                    lines.append(f"{bc.target_set}, {dof}, {dof}, {value!r}")
            for lname in step.loads:
                ld = model.load(lname)
                if ld.kind == "force":
                    nodes = model.sets[ld.target_set].members
                    n = max(len(nodes), 1)
                    lines.append("*CLOAD")
                    for comp, dof in (("fx", 1), ("fy", 2), ("fz", 3)):
                        total = ld.values.get(comp, 0.0)
                        if total:
                            lines.append(f"{ld.target_set}, {dof}, {total / n!r}")
                elif ld.kind == "pressure":
                    lines.append("*DLOAD")
                    for eid, face in model.sets[ld.target_set].members:
                        lines.append(f"{eid}, {FACE_TO_P[face]}, {ld.values['magnitude']!r}")
                elif ld.kind == "gravity":
                    accel = ld.values.get("accel", 9.81)
                    d = ld.values.get("direction", [0.0, 0.0, -1.0])
                    lines.append("*DLOAD")
                    lines.append(f"ALL, GRAV, {accel!r}, {d[0]!r}, {d[1]!r}, {d[2]!r}")
            lines.append("*NODE FILE\nU, RF")
            lines.append("*EL FILE\nS")
            lines.append("*END STEP")
        return "\n".join(lines) + "\n"

    # -- execution --------------------------------------------------------
    def run(self, model: StructuralModel, deck: Path, workdir: Path) -> ResultSet:
        workdir = Path(workdir)
        ccx = find_ccx()
        if ccx is None:
            return ResultSet(
                solver_id=self.id,
                success=False,
                log_tail="ccx 실행 파일을 찾지 못했습니다 (PATH 또는 CAELAB_CCX 환경변수).",
            )
        proc = subprocess.run(
            [ccx, "-i", "job"],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        log = (proc.stdout or "") + (proc.stderr or "")
        frd = workdir / "job.frd"
        if proc.returncode != 0 or not frd.exists():
            return ResultSet(solver_id=self.id, success=False, log_tail=log[-4000:])
        result = parse_frd(frd)
        # Reactions are the nodal forces at constrained nodes only; the
        # FORC block also carries applied loads, which cancel in the sum.
        constrained: set[int] = set()
        for step in model.steps:
            for bname in step.bcs:
                constrained.update(model.sets[model.bc(bname).target_set].members)
        result.reactions = {
            n: v for n, v in result.nodal_forces.items() if n in constrained
        }
        result.reaction_totals = {
            axis: sum(v[i] for v in result.reactions.values())
            for i, axis in enumerate(("fx", "fy", "fz"))
        }
        result.log_tail = log[-4000:]
        return result


def parse_frd(path: Path) -> ResultSet:
    """Parse the fixed-format .frd DISP/FORC blocks written by *NODE FILE."""
    disp: dict[int, list[float]] = {}
    forc: dict[int, list[float]] = {}
    current: dict | None = None
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(" -4"):
            name = line[5:13].strip()
            current = {"DISP": disp, "FORC": forc}.get(name)
            continue
        if line.startswith(" -3"):
            current = None
            continue
        if current is not None and line.startswith(" -1"):
            try:
                node = int(line[3:13])
                values = [float(line[13 + 12 * i: 13 + 12 * (i + 1)]) for i in range(3)]
            except ValueError:
                continue
            current[node] = values
    max_disp = 0.0
    for v in disp.values():
        mag = (v[0] ** 2 + v[1] ** 2 + v[2] ** 2) ** 0.5
        max_disp = max(max_disp, mag)
    return ResultSet(
        solver_id="calculix",
        success=True,
        displacements=dict(disp),
        nodal_forces=dict(forc),
        max_displacement=max_disp,
    )
