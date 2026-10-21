"""Input validation only (redesign decision D2).

Checks catch *input* errors: missing materials, broken set references,
empty selections, dangling mesh references. There is deliberately no
allow-list here: the absence of a benchmark case never blocks a run;
that state is reported on the result instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..model.schema import StructuralModel


@dataclass(frozen=True)
class Issue:
    severity: Literal["error", "warning"]
    target_path: str  # e.g. "materials[1]" — the GUI selects this object
    message: str      # one line: what is wrong

    def __str__(self) -> str:
        return f"[{self.severity}] {self.target_path}: {self.message}"


def validate_model(model: StructuralModel) -> list[Issue]:
    issues: list[Issue] = []
    sets = model.sets

    # mesh sanity ------------------------------------------------------
    if not model.mesh.nodes:
        issues.append(Issue("error", "mesh", "메시가 비어 있습니다. 절점이 없습니다."))
    if not model.mesh.elements:
        issues.append(Issue("error", "mesh", "메시가 비어 있습니다. 요소가 없습니다."))
    for eid, elem in model.mesh.elements.items():
        for nid in elem["nodes"]:
            if nid not in model.mesh.nodes:
                issues.append(
                    Issue("error", "mesh", f"요소 {eid}가 없는 절점 {nid}를 참조합니다.")
                )
                break

    # sets ---------------------------------------------------------------
    for name, s in sets.items():
        if not s.members:
            issues.append(Issue("error", f"sets.{name}", f"집합 '{name}'이 비어 있습니다."))

    def need_set(owner: str, set_name: str | None, what: str) -> None:
        if set_name is None:
            issues.append(Issue("error", owner, f"{what}의 대상 집합이 지정되지 않았습니다."))
        elif set_name not in sets:
            issues.append(
                Issue("error", owner, f"{what}가 없는 집합 '{set_name}'을 참조합니다.")
            )

    # materials / sections ----------------------------------------------
    material_names = set()
    for i, m in enumerate(model.materials):
        if m.name in material_names:
            issues.append(Issue("error", f"materials[{i}]", f"재료 이름 '{m.name}'이 중복됩니다."))
        material_names.add(m.name)
        if m.assigned_set is not None:
            need_set(f"materials[{i}]", m.assigned_set, f"재료 '{m.name}'")
    if model.mesh.elements and not model.materials:
        issues.append(Issue("error", "materials", "재료가 하나도 없습니다."))

    for i, sec in enumerate(model.sections):
        need_set(f"sections[{i}]", sec.element_set, f"단면 '{sec.name}'")
        if sec.material not in material_names:
            issues.append(
                Issue("error", f"sections[{i}]", f"단면 '{sec.name}'이 없는 재료 '{sec.material}'을 참조합니다.")
            )

    # loads / bcs ----------------------------------------------------------
    load_names = set()
    for i, ld in enumerate(model.loads):
        if ld.name in load_names:
            issues.append(Issue("error", f"loads[{i}]", f"하중 이름 '{ld.name}'이 중복됩니다."))
        load_names.add(ld.name)
        if ld.kind != "gravity":
            need_set(f"loads[{i}]", ld.target_set, f"하중 '{ld.name}'")

    bc_names = set()
    for i, bc in enumerate(model.bcs):
        if bc.name in bc_names:
            issues.append(Issue("error", f"bcs[{i}]", f"구속 이름 '{bc.name}'이 중복됩니다."))
        bc_names.add(bc.name)
        need_set(f"bcs[{i}]", bc.target_set, f"구속 '{bc.name}'")
        if not bc.fixed_dofs:
            issues.append(Issue("error", f"bcs[{i}]", f"구속 '{bc.name}'에 고정할 자유도가 없습니다."))
        for dof in bc.fixed_dofs:
            if dof not in (1, 2, 3, 4, 5, 6):
                issues.append(Issue("error", f"bcs[{i}]", f"구속 '{bc.name}'의 자유도 {dof}가 잘못됐습니다."))

    # interactions -----------------------------------------------------------
    for i, inter in enumerate(model.interactions):
        for set_name in inter.pair:
            need_set(f"interactions[{i}]", set_name, f"상호작용 '{inter.name}'")

    # steps ---------------------------------------------------------------------
    if not model.steps:
        issues.append(Issue("error", "steps", "해석 단계가 하나도 없습니다."))
    for i, step in enumerate(model.steps):
        for lname in step.loads:
            if lname not in load_names:
                issues.append(
                    Issue("error", f"steps[{i}]", f"단계 '{step.name}'이 없는 하중 '{lname}'을 참조합니다.")
                )
        for bname in step.bcs:
            if bname not in bc_names:
                issues.append(
                    Issue("error", f"steps[{i}]", f"단계 '{step.name}'이 없는 구속 '{bname}'을 참조합니다.")
                )

    return issues
