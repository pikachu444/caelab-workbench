"""Result summaries — the evidence base for discussion.

The discussion program (the workbench's second half) reasons over
what actually ran: solver, success, headline numbers, verification
state, and the tail of the solver log. This module turns a ResultSet
into that compact, solver-neutral summary. It never invents values:
numbers come from the parsed solver output only.
"""

from __future__ import annotations

from .solvers.base import ResultSet


def summarize_result(result: ResultSet, solver: str = "calculix") -> dict:
    return {
        "solver": solver,
        "success": result.success,
        "max_displacement_m": result.max_displacement,
        "max_displacement_mm": result.max_displacement * 1000.0,
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
    fy = summary["reaction_totals"].get("fy")
    fy_text = f", 반력 합 Fy {fy:,.1f} N" if fy is not None else ""
    return (
        f"{summary['solver']} 계산 완료. "
        f"최대 변위 {summary['max_displacement_mm']:.3f} mm{fy_text}. "
        f"검증 상태: {summary['verification']}."
    )
