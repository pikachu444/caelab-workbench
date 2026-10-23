"""Auxiliary model converter contract; not a native-deck capability gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..model.schema import StructuralModel
from ..validate.checker import Issue


@dataclass(frozen=True)
class SupportTable:
    elements: frozenset = frozenset()
    materials: frozenset = frozenset()
    steps: frozenset = frozenset()
    loads: frozenset = frozenset()
    interactions: frozenset = frozenset()

    def check(self, model: StructuralModel) -> list[Issue]:
        """Support check: reports combos this converter cannot write.

        This is a capability statement about the converter, not an
        allow-list on the user's model: the model stays valid, and
        another converter may accept the same combination.
        """
        issues: list[Issue] = []
        used_elements = {e["type"] for e in model.mesh.elements.values()}
        for etype in sorted(used_elements - self.elements):
            issues.append(
                Issue("error", "mesh", f"이 솔버 변환기는 요소 {etype}를 지원하지 않습니다.")
            )
        for i, m in enumerate(model.materials):
            if m.law not in self.materials:
                issues.append(
                    Issue("error", f"materials[{i}]", f"이 솔버 변환기는 재료 법칙 {m.law}를 지원하지 않습니다.")
                )
        for i, step in enumerate(model.steps):
            if step.kind not in self.steps:
                issues.append(
                    Issue("error", f"steps[{i}]", f"이 솔버 변환기는 해석 단계 {step.kind}를 지원하지 않습니다.")
                )
        for i, ld in enumerate(model.loads):
            if ld.kind not in self.loads:
                issues.append(
                    Issue("error", f"loads[{i}]", f"이 솔버 변환기는 하중 {ld.kind}를 지원하지 않습니다.")
                )
        for i, inter in enumerate(model.interactions):
            if inter.kind not in self.interactions:
                issues.append(
                    Issue("error", f"interactions[{i}]", f"이 솔버 변환기는 상호작용 {inter.kind}를 지원하지 않습니다.")
                )
        return issues


@dataclass
class ResultSet:
    """Normalised solver output."""

    solver_id: str
    success: bool
    displacements: dict = field(default_factory=dict)   # node id -> [ux, uy, uz]
    reactions: dict = field(default_factory=dict)       # constrained node id -> [fx, fy, fz]
    nodal_forces: dict = field(default_factory=dict)    # every node id -> [fx, fy, fz]
    max_displacement: float | None = None
    reaction_totals: dict = field(default_factory=dict)  # {"fx":.., "fy":.., "fz":..}
    log_tail: str = ""
    verification: str = "검증 사례 없음"  # benchmark coverage state, shown on the result
    units: dict = field(default_factory=dict)
    log_path: str | None = None
    validation_issues: list[str] = field(default_factory=list)

    def summary(self) -> str:
        state = "성공" if self.success else "실패"
        displacement = "미확인" if self.max_displacement is None else f"{self.max_displacement:.6g}"
        forces = " ".join(
            f"{axis.upper()}={self.reaction_totals[axis]:.6g}"
            if axis in self.reaction_totals else f"{axis.upper()}=미확인"
            for axis in ("fx", "fy", "fz")
        )
        return (
            f"{self.solver_id} 실행 {state}: 최대 변위 {displacement}, "
            f"반력 합 {forces} ({self.verification})"
        )


class SolverConverter(Protocol):
    id: str

    def support(self) -> SupportTable: ...

    def validate(self, model: StructuralModel) -> list[Issue]: ...

    def write_input(self, model: StructuralModel, workdir: Path) -> Path: ...

    def run(self, model: StructuralModel, deck: Path, workdir: Path) -> ResultSet: ...
