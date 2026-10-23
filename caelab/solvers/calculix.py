"""CalculiX converter: common model -> .inp deck -> ccx -> .frd results.

One converter with options replaces the former per-feature CalculiX
backends (frozen/contact/shell/beam/pretension/...). Element, material
and step coverage grows inside this converter and its support table,
not by adding new backends.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..model.schema import StructuralModel
from ..validate.checker import Issue, validate_model
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
        issues = self.support().check(model)

        def error(path, message):
            issues.append(Issue("error", path, message))

        def keys(path, values, supported):
            for key in values.keys() - supported:
                error(f"{path}.{key}", "이 보조 변환기가 기록하지 않는 옵션입니다.")

        def target(path, name, kind):
            if name in model.sets and model.sets[name].kind != kind:
                error(path, f"{kind} 집합이 필요합니다.")

        if len(model.steps) > 1:
            error("steps", "다중 단계의 하중·구속 이력은 이 보조 변환기가 지원하지 않습니다.")
        for i, step in enumerate(model.steps):
            keys(f"steps[{i}].options", step.options, {"nlgeom"})
            if "nlgeom" in step.options and not isinstance(step.options["nlgeom"], bool):
                error(f"steps[{i}].options.nlgeom", "nlgeom은 참/거짓 값이어야 합니다.")
            if step.kind == "nonlinear_static" and step.options.get("nlgeom") is False:
                error(f"steps[{i}].options.nlgeom", "이 보조 변환기의 nonlinear_static 단계는 NLGEOM을 사용합니다.")
        for i, material in enumerate(model.materials):
            path = f"materials[{i}]"
            allowed = {"youngs_modulus", "poissons_ratio", "density"}
            if material.law == "j2_plasticity":
                allowed.add("plastic")
            keys(path + ".params", material.params, allowed)
            for key in ("youngs_modulus", "poissons_ratio"):
                value = material.params.get(key)
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    error(path + ".params." + key, "유한한 재료 상수가 필요합니다.")
            if isinstance(material.params.get("youngs_modulus"), (int, float)) and material.params["youngs_modulus"] <= 0:
                error(path + ".params.youngs_modulus", "탄성계수는 양수여야 합니다.")
            if isinstance(material.params.get("poissons_ratio"), (int, float)) and not -1 < material.params["poissons_ratio"] < 0.5:
                error(path + ".params.poissons_ratio", "포아송비는 -1과 0.5 사이여야 합니다.")
            if "density" in material.params or any(ld.kind == "gravity" for ld in model.loads):
                density = material.params.get("density")
                if not isinstance(density, (int, float)) or not math.isfinite(density) or density <= 0:
                    error(path + ".params.density", "양의 유한한 밀도가 필요합니다.")
            if material.law == "j2_plasticity":
                plastic = material.params.get("plastic", [])
                if not plastic or any(
                    not isinstance(row, (list, tuple)) or len(row) != 2
                    or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in row)
                    for row in plastic
                ):
                    error(path + ".params.plastic", "유효한 응력·소성변형률 표가 필요합니다.")
            if material.assigned_set:
                target(path + ".assigned_set", material.assigned_set, "element")
                if model.sections:
                    error(path + ".assigned_set", "명시적 단면과 함께 지정된 재료 집합은 기록되지 않습니다.")
        for i, section in enumerate(model.sections):
            if section.kind != "solid":
                error(f"sections[{i}].kind", "이 보조 변환기는 solid 단면만 기록합니다.")
            keys(f"sections[{i}].options", section.options, set())
            target(f"sections[{i}].element_set", section.element_set, "element")
        coverage = {}
        assignments = [(f"sections[{i}]", sec.element_set) for i, sec in enumerate(model.sections)] if model.sections else [
            (f"materials[{i}]", mat.assigned_set) for i, mat in enumerate(model.materials)
        ]
        for path, name in assignments:
            members = model.sets[name].members if name in model.sets else (model.mesh.elements if name is None else [])
            for eid in members:
                if eid in coverage:
                    error(path, f"요소 {eid}의 단면·재료 할당이 중복됩니다.")
                coverage[eid] = path
        if model.mesh.elements.keys() - coverage.keys():
            error("sections", "단면·재료가 할당되지 않은 요소가 있습니다.")
        for i, load in enumerate(model.loads):
            path = f"loads[{i}]"
            keys(path + ".values", load.values, {
                "force": {"fx", "fy", "fz"}, "pressure": {"magnitude"},
                "gravity": {"accel", "direction"},
            }.get(load.kind, set()))
            if load.kind in ("force", "pressure"):
                target(path + ".target_set", load.target_set, "node" if load.kind == "force" else "face")
            if load.kind == "gravity" and load.target_set is not None:
                error(path + ".target_set", "이 보조 변환기의 자중은 전체 요소에만 적용됩니다.")
            for key, value in load.values.items():
                values = value if key == "direction" and isinstance(value, (list, tuple)) else [value]
                if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
                    error(path + ".values." + key, "유한한 숫자가 필요합니다.")
            if load.kind == "pressure" and "magnitude" not in load.values:
                error(path + ".values.magnitude", "압력 크기가 필요합니다.")
            if load.kind == "gravity":
                direction = load.values.get("direction", [0, 0, -1])
                if not isinstance(direction, (list, tuple)) or len(direction) != 3 or not any(direction):
                    error(path + ".values.direction", "0이 아닌 세 성분 방향 벡터가 필요합니다.")
                if model.unit_system != "si" and "accel" not in load.values:
                    error(path + ".values.accel", "SI 이외의 단위계에는 자중 가속도를 명시해야 합니다.")
        for i, bc in enumerate(model.bcs):
            target(f"bcs[{i}].target_set", bc.target_set, "node")
            keys(f"bcs[{i}].values", bc.values, set(bc.fixed_dofs) | {str(d) for d in bc.fixed_dofs})
            if any(d not in (1, 2, 3) for d in bc.fixed_dofs):
                error(f"bcs[{i}].fixed_dofs", "solid 요소에는 병진 자유도 1, 2, 3만 기록합니다.")
            for dof, value in bc.values.items():
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    error(f"bcs[{i}].values.{dof}", "유한한 변위가 필요합니다.")
            for dof in bc.fixed_dofs:
                if dof in bc.values and str(dof) in bc.values and bc.values[dof] != bc.values[str(dof)]:
                    error(f"bcs[{i}].values.{dof}", "같은 자유도의 값이 충돌합니다.")
        for name, selection in model.sets.items():
            path = f"sets.{name}"
            if selection.kind in ("node", "element"):
                available = model.mesh.nodes if selection.kind == "node" else model.mesh.elements
                if any(member not in available for member in selection.members):
                    error(path, "집합에 존재하지 않는 메시 항목이 있습니다.")
            elif selection.kind == "face":
                for member in selection.members:
                    if not isinstance(member, (list, tuple)) or len(member) != 2 or member[0] not in model.mesh.elements:
                        error(path, "유효한 요소·면 쌍이 필요합니다.")
                        break
                    element = model.mesh.elements[member[0]]["type"]
                    faces = {"S1", "S2", "S3", "S4"} if element in ("C3D4", "C3D10") else set(FACE_TO_P)
                    if member[1] not in faces:
                        error(path, "요소에 존재하지 않는 면입니다.")
            else:
                error(path, "이 보조 변환기가 지원하지 않는 집합 종류입니다.")
            if len(selection.members) != len({str(member) for member in selection.members}):
                error(path, "중복 집합 항목은 하중 분배를 바꿀 수 있습니다.")
        if model.initial_conditions:
            error("initial_conditions", "이 보조 변환기는 초기조건을 기록하지 않습니다.")
        keys("outputs", model.outputs, {"fields"})
        if set(model.outputs.get("fields", [])) != {"U", "RF", "S"}:
            error("outputs.fields", "이 보조 변환기는 U, RF, S 출력 조합만 기록합니다.")
        return issues

    # -- deck writing ---------------------------------------------------
    def write_input(self, model: StructuralModel, workdir: Path) -> Path:
        errors = [str(issue) for issue in validate_model(model) + self.validate(model) if issue.severity == "error"]
        if errors:
            raise ValueError("\n".join(errors))
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
        workdir = Path(workdir).resolve()
        log_path = workdir / "job.log"
        units = {"length": "m", "force": "N"} if model.unit_system == "si" else {}

        def finish(result, message=""):
            if message:
                with log_path.open("a", encoding="utf-8") as log:
                    log.write("\n" + message)
            result.log_path = str(log_path)
            result.log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            result.units = units
            return result

        log_path.write_text("", encoding="utf-8")
        ccx = find_ccx()
        if ccx is None:
            return finish(ResultSet(self.id, False), "ccx 실행 파일을 찾지 못했습니다 (PATH 또는 CAELAB_CCX 환경변수).")
        # A failed rerun must never consume an earlier run's output.
        for suffix in ("frd", "dat"):
            (workdir / f"{Path(deck).stem}.{suffix}").unlink(missing_ok=True)
        try:
            proc = subprocess.run(
                [ccx, "-i", Path(deck).stem], cwd=workdir,
                capture_output=True, text=True, timeout=3600,
            )
        except subprocess.TimeoutExpired as exc:
            def decoded(value):
                return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")
            return finish(ResultSet(self.id, False), decoded(exc.stdout) + decoded(exc.stderr) + f"\nTimeoutExpired: {exc}")
        except OSError as exc:
            return finish(ResultSet(self.id, False), f"{type(exc).__name__}: {exc}")
        log_path.write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")
        if proc.returncode != 0:
            return finish(ResultSet(self.id, False), f"솔버 종료 코드: {proc.returncode}")
        requested = _output_requests(Path(deck))
        frd_path = workdir / f"{Path(deck).stem}.frd"
        dat_path = workdir / f"{Path(deck).stem}.dat"
        frd = _read_frd(frd_path) if frd_path.exists() else FrdResults()
        dat = parse_dat(dat_path) if dat_path.exists() else DatResults()
        result = frd.result
        use_dat = bool(
            (not result.displacements and dat.displacements)
            or (not result.nodal_forces and (dat.nodal_forces or dat.totals_by_set))
            or any(kind == "*NODE PRINT" for _, kind, _ in requested)
        )
        errors = list(result.validation_issues) + (dat.errors if use_dat or not frd.fields else [])
        if use_dat and frd.frame is not None and dat.frame is not None and (
            any(a is not None and b is not None and a != b for a, b in zip(frd.frame[:2], dat.frame[:2]))
            or frd.frame[2] is None or dat.frame[2] is None
            or not math.isclose(frd.frame[2], dat.frame[2], rel_tol=1e-6, abs_tol=1e-12)
        ):
            errors.append("results.frame: FRD와 DAT의 마지막 단계·증분·시간이 다릅니다.")
        if use_dat and dat.frame is not None and dat.frame[2] is not None:
            compatible = [frame for frame in frd.frames if frame is not None and frame[2] is not None
                          and all(a is None or b is None or a == b for a, b in zip(frame[:2], dat.frame[:2]))
                          and math.isclose(frame[2], dat.frame[2], rel_tol=1e-6, abs_tol=1e-12)]
            if len(compatible) > 1:
                errors.append("results.frame: DAT 결과에 대응하는 FRD 프레임이 여러 개입니다.")
        if not result.displacements and dat.displacements:
            result.displacements = dict(dat.displacements)
            result.max_displacement = dat.max_displacement
        if not result.nodal_forces and dat.nodal_forces:
            result.nodal_forces = dict(dat.nodal_forces)
        if not frd.fields and not dat.displacements and not dat.nodal_forces and not dat.totals_by_set:
            errors.append("results: 유효한 결과 프레임이 없습니다.")
        for field, kind, options in requested:
            scope = options.get("NSET")
            if kind == "*NODE PRINT":
                blocks = dat.displacements_by_set if field == "U" else dat.forces_by_set
                totals = dat.displacement_totals_by_set if field == "U" else dat.totals_by_set
                mode = options.get("TOTALS", "NO")
                available = bool(scope) and field in ("U", "RF") and mode in ("NO", "YES", "ONLY")
                if mode != "ONLY":
                    selection = next((s for name, s in model.sets.items() if name.upper() == scope), None)
                    available = (
                        available and bool(blocks.get(scope)) and selection is not None
                        and selection.kind == "node" and bool(selection.members)
                        and set(selection.members) <= blocks[scope].keys()
                    )
                if mode in ("YES", "ONLY"):
                    available = available and scope in totals
            elif kind == "*NODE FILE" and field in ("U", "RF"):
                values = result.displacements if field == "U" else result.nodal_forces
                selection = next((s for name, s in model.sets.items() if name.upper() == scope), None) if scope else None
                required = selection.members if selection is not None and selection.kind == "node" else model.mesh.nodes
                available = bool(values) and set(required) <= values.keys()
                if scope and (selection is None or selection.kind != "node"):
                    available = False
            else:
                # Scoped element output needs a field/element mapping this reader lacks.
                available = field == "S" and "STRESS" in frd.fields and not options.get("ELSET")
            if not available:
                errors.append(f"outputs.{field}{'.' + scope if scope else ''}: 요청한 결과 필드·집합·출력 형식이 없거나 확인할 수 없습니다.")
        constrained = {}
        for step in model.steps:
            for name in step.bcs:
                bc = model.bc(name)
                for node in model.sets[bc.target_set].members:
                    constrained.setdefault(node, set()).update(bc.fixed_dofs)
        loaded = set()
        for load in model.loads:
            if load.kind == "force" and load.target_set in model.sets:
                loaded.update(model.sets[load.target_set].members)
            elif load.kind in ("pressure", "gravity"):
                elements = model.mesh.elements.values() if load.kind == "gravity" else (
                    model.mesh.elements[eid] for eid, _ in model.sets[load.target_set].members
                )
                for element in elements:
                    loaded.update(element["nodes"])
        # RF/FORC includes applied nodal forces. Do not label a loaded support's
        # external force as a reaction without subtracting equivalent loads.
        if constrained and not loaded.intersection(constrained):
            if result.nodal_forces and set(constrained) <= result.nodal_forces.keys():
                result.reactions = {
                    node: [value if i + 1 in dofs else 0.0 for i, value in enumerate(result.nodal_forces[node])]
                    for node, dofs in constrained.items()
                }
                result.reaction_totals = {
                    axis: sum(value[i] for value in result.reactions.values())
                    for i, axis in enumerate(("fx", "fy", "fz"))
                }
            elif dat.totals_by_set:
                covered = set()
                matched = []
                for name, selection in model.sets.items():
                    members = set(selection.members) if selection.kind == "node" else set()
                    if name.upper() in dat.totals_by_set and members and all(constrained.get(n) == {1, 2, 3} for n in members):
                        if covered.intersection(members):
                            matched = []
                            break
                        covered.update(members)
                        matched.append(dat.totals_by_set[name.upper()])
                if covered == set(constrained) and matched:
                    result.reaction_totals = {
                        axis: sum(value[i] for value in matched)
                        for i, axis in enumerate(("fx", "fy", "fz"))
                    }
        result.validation_issues = errors
        result.success = not errors
        return finish(result, "\n".join(errors))


def _output_requests(deck: Path) -> list[tuple[str, str, dict]]:
    """Known output requests used by this writer and direct U/RF/S decks.

    This does not interpret arbitrary native decks or included files.
    """
    requested = []
    kind = ""
    options = {}
    for raw in deck.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip().upper()
        if line.startswith("**"):
            continue
        if line.startswith("*"):
            parts = [part.strip() for part in line.split(",")]
            kind = parts[0] if parts[0] in ("*NODE FILE", "*NODE PRINT", "*EL FILE") else ""
            options = dict(part.split("=", 1) for part in parts[1:] if "=" in part)
            options = {key.strip(): value.strip() for key, value in options.items()}
        elif kind:
            requested.extend((token.strip(), kind, dict(options)) for token in line.split(",") if token.strip() in ("U", "RF", "S"))
    return requested


@dataclass
class FrdResults:
    result: ResultSet = field(default_factory=lambda: ResultSet("calculix", False))
    frame: tuple | None = None
    fields: set = field(default_factory=set)
    frames: tuple = ()


def _read_frd(path: Path) -> FrdResults:
    frames = {}
    frame = None
    step, increment = None, None
    count = None
    current = None
    rows = {}
    errors = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if line.startswith("    1PSTEP"):
            # CalculiX frdheader.c: dataset counter is separate from step/increment.
            try:
                increment, step = int(line[36:48]), int(line[48:60])
            except ValueError:
                errors.append(f"{path.name}:{line_number}: 손상된 PSTEP 헤더입니다.")
        elif line.startswith("  100C"):
            try:
                frame = (step, increment, float(line[12:24]))
                count = int(line[24:36])
                if not math.isfinite(frame[2]) or count <= 0:
                    raise ValueError
                frames.setdefault(frame, {})
            except ValueError:
                errors.append(f"{path.name}:{line_number}: 손상된 프레임 헤더입니다.")
        elif line.startswith(" -4"):
            if current is not None:
                errors.append(f"{path.name}:{line_number}: 닫히지 않은 {current} 블록입니다.")
            name = line[5:13].strip()
            current = name if name in ("DISP", "FORC", "STRESS") else None
            if current is not None and frame is None:
                errors.append(f"{path.name}:{line_number}: {current}의 유효한 프레임 헤더가 없습니다.")
            rows = {}
            frames.setdefault(frame, {})
        elif line.startswith(" -3"):
            if current is not None:
                if not rows or (count is not None and len(rows) != count):
                    errors.append(f"{path.name}:{line_number}: {current} 레코드가 없거나 절점 수가 맞지 않습니다.")
                if current in frames[frame]:
                    errors.append(f"{path.name}:{line_number}: 같은 프레임의 {current} 블록이 중복됩니다.")
                frames[frame][current] = rows
            current = None
        elif current is not None and line.startswith(" -1"):
            try:
                node = int(line[3:13])
                components = 6 if current == "STRESS" else 3
                values = [float(line[13 + 12 * i:13 + 12 * (i + 1)]) for i in range(components)]
                if node <= 0 or node in rows or not all(math.isfinite(v) for v in values):
                    raise ValueError
                rows[node] = values
            except ValueError:
                errors.append(f"{path.name}:{line_number}: 손상된 {current} 레코드입니다.")
    if current is not None:
        errors.append(f"{path.name}: 닫히지 않은 {current} 블록입니다.")
    selected = next(reversed(frames), None)
    fields = frames.get(selected, {})
    disp, forc = fields.get("DISP", {}), fields.get("FORC", {})
    result = ResultSet(
        "calculix", bool(fields) and not errors, displacements=disp, nodal_forces=forc,
        max_displacement=max((math.hypot(*value) for value in disp.values()), default=None),
        validation_issues=errors,
    )
    return FrdResults(result, selected, set(fields), tuple(frames))


def parse_frd(path: Path) -> ResultSet:
    """Read the final frame; absent or corrupt displacement is never zero."""
    parsed = _read_frd(path)
    if not parsed.result.displacements:
        parsed.result.validation_issues.append(f"{Path(path).name}.DISP: 유효한 최종 변위 프레임이 없습니다.")
        parsed.result.success = False
    return parsed.result


@dataclass
class DatResults:
    """Known *NODE PRINT values and observed result headers in a CalculiX .dat.

    Two shapes occur in the wild:
    - per-node blocks (``displacements (vx,vy,vz) for set X`` /
      ``forces (fx,fy,fz) for set X``) with one row per node, and
    - totals blocks (``total force (fx,fy,fz) for set X``) with a single
      row of three values and no node id — this is what FreeCAD FEM
      writes for reaction forces (``TOTALS=ONLY``).
    Only supported blocks from the last observed frame occurrence are exposed.
    Other explicit set-result headers still select that frame and are recorded
    in unsupported_blocks; their numeric rows are never interpreted here.
    """

    displacements: dict = field(default_factory=dict)  # node id -> [ux, uy, uz]
    nodal_forces: dict = field(default_factory=dict)  # node id -> [fx, fy, fz]
    totals_by_set: dict = field(default_factory=dict)  # SET name (upper) -> [fx, fy, fz]
    displacements_by_set: dict = field(default_factory=dict)
    forces_by_set: dict = field(default_factory=dict)
    displacement_totals_by_set: dict = field(default_factory=dict)
    max_displacement: float | None = None
    frame: tuple | None = None
    errors: list[str] = field(default_factory=list)
    unsupported_blocks: list[dict] = field(default_factory=list)


_DAT_HEADER = re.compile(
    r"^\s*(displacements|forces|total force|total displacement)\s*"
    r"\([^)]*\)\s+for set\s+(\S+)",
    re.IGNORECASE,
)
_DAT_RESULT_HEADER = re.compile(
    r"^\s*(.+?)\s+for set\s+(\S+)(?:\s+and time\b(.*))?\s*$",
    re.IGNORECASE,
)


def parse_dat(path: Path, *, check_cancelled=None) -> DatResults:
    """Read complete global-coordinate blocks from the final DAT frame."""
    # Observe every explicit set-result header, including fields whose values we
    # cannot read. Otherwise a later EL PRINT/thermal block leaves old U/RF final.
    # Keep the last contiguous frame occurrence, not a dictionary insertion order.
    blocks = {}
    unsupported_blocks = []
    errors = []
    step, increment = None, None
    current = None
    current_set = ""
    frame = None
    rows = {}

    def finish():
        if current is None:
            return
        if not rows:
            errors.append(f"{Path(path).name}.{current_set}: 유효한 {current} 레코드가 없습니다.")
        if (current, current_set) in blocks:
            errors.append(f"{Path(path).name}.{current_set}: 같은 프레임의 {current} 블록이 중복됩니다.")
        blocks[(current, current_set)] = dict(rows)

    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if check_cancelled is not None and line_number % 1024 == 1:
            check_cancelled()
        step_match = re.search(r"S T E P\s+(\d+)", line)
        inc_match = re.search(r"INCREMENT\s+(\d+)", line)
        if step_match or inc_match:
            finish()
            current = None
            if step_match:
                step = int(step_match.group(1))
                increment = None
            if inc_match:
                increment = int(inc_match.group(1))
            continue
        observed = _DAT_RESULT_HEADER.match(line)
        if observed:
            finish()
            current = None
            time_match = re.search(r"and time\s+(\S+)", line, re.IGNORECASE)
            try:
                time = float(time_match.group(1)) if time_match else None
                if time is None or not math.isfinite(time):
                    raise ValueError
            except ValueError:
                errors.append(f"{path.name}:{line_number}: 유효한 결과 시간이 없습니다.")
                time = None
            observed_frame = (step, increment, time)
            if observed_frame != frame:
                blocks = {}
                unsupported_blocks = []
            frame = observed_frame
            rows = {}
            header = _DAT_HEADER.match(line)
            if header:
                current = header.group(1).lower()
                current_set = header.group(2).upper()
            else:
                unsupported_blocks.append({"header": line.strip(), "set": observed.group(2).upper(),
                                           "frame": frame, "line": line_number})
            continue
        if current is None or not line.strip():
            continue
        parts = line.split()
        if not parts or not (parts[0][0].isdigit() or parts[0][0] in "+-.*"):
            finish()
            current = None
            continue
        try:
            total = current.startswith("total")
            if len(parts) != (3 if total else 4):
                raise ValueError
            node = 0 if total else int(parts[0])
            values = [float(value) for value in (parts if total else parts[1:])]
            if node in rows or not all(math.isfinite(value) for value in values):
                raise ValueError
            rows[node] = values
        except ValueError:
            errors.append(f"{path.name}:{line_number}: 손상되었거나 전역 좌표가 아닌 {current} 레코드입니다.")
    finish()
    out = DatResults(errors=errors, frame=frame, unsupported_blocks=unsupported_blocks)
    if frame is not None:
        for (kind, name), block in blocks.items():
            if kind == "displacements":
                out.displacements_by_set[name] = block
                if any(node in out.displacements and out.displacements[node] != value for node, value in block.items()):
                    out.errors.append(f"{path.name}.{name}: 집합 사이의 변위 값이 충돌합니다.")
                out.displacements.update(block)
            elif kind == "forces":
                out.forces_by_set[name] = block
                if any(node in out.nodal_forces and out.nodal_forces[node] != value for node, value in block.items()):
                    out.errors.append(f"{path.name}.{name}: 집합 사이의 힘 값이 충돌합니다.")
                out.nodal_forces.update(block)
            elif kind == "total force" and block:
                out.totals_by_set[name] = block[0]
            elif kind == "total displacement" and block:
                out.displacement_totals_by_set[name] = block[0]
    out.max_displacement = max((math.hypot(*value) for value in out.displacements.values()), default=None)
    return out
