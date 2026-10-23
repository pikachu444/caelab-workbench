"""Result summaries — the evidence base for discussion.

The discussion program (the workbench's second half) reasons over
what actually ran: solver, success, headline numbers, verification
state, and the tail of the solver log. This module turns a ResultSet
into that compact, solver-neutral summary. It never invents values:
numbers come from the parsed solver output only.
"""

from __future__ import annotations

from .solvers.base import ResultSet


def summarize_result(result: ResultSet, solver: str | None = None) -> dict:
    """Preserve result provenance; the legacy solver argument cannot override it."""
    units = dict(getattr(result, "units", {}))
    displacement = result.max_displacement
    length = units.get("length")
    metres = (displacement if length == "m" else displacement / 1000.0
              if length == "mm" else None) if displacement is not None else None
    return {
        "solver": result.solver_id,
        "solver_id": result.solver_id,
        "success": result.success,
        "max_displacement": displacement,
        "units": units,
        "max_displacement_m": metres,
        "max_displacement_mm": metres * 1000.0 if metres is not None else None,
        "reaction_totals": dict(result.reaction_totals),
        "verification": result.verification,
        "log_tail": result.log_tail[-800:],
    }


def format_summary_ko(summary: dict) -> str:
    """One short paragraph a person (or an expert prompt) can read."""
    if not summary["success"]:
        return (
            f"{summary['solver']} 계산이 실패했습니다. "
            f"검증 상태: {summary['verification']}."
        )
    units = summary.get("units", {})
    displacement = summary["max_displacement"]
    length = units.get("length", "단위 미확인 — 사용자 선언 필요")
    displacement_text = (f"{displacement:.6g} {length}"
                         if displacement is not None else "미확인")
    fy = summary["reaction_totals"].get("fy")
    force = units.get("force", "단위 미확인 — 사용자 선언 필요")
    fy_text = f", 반력 합 Fy {fy:,.1f} {force}" if fy is not None else ""
    return (
        f"{summary['solver']} 계산 완료. "
        f"최대 변위 {displacement_text}{fy_text}. "
        f"검증 상태: {summary['verification']}."
    )
