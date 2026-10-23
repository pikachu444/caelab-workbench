"""Connection registry and discussion-summary contracts."""

import os

import pytest

from caelab.connections import (
    PROGRAMS,
    ProgramSpec,
    find_program,
    launch_command,
    program_statuses,
)
from caelab.result_summary import format_summary_ko, summarize_result
from caelab.solvers.base import ResultSet
from caelab.workbench import Workbench

CCX = os.environ.get("CAELAB_CCX")


def test_registry_covers_the_connected_programs():
    ids = {spec.id for spec in PROGRAMS}
    assert {"freecad", "salome", "gmsh", "calculix", "paraview",
            "codeaster", "opencourant"} <= ids
    roles = {spec.id: spec.role for spec in PROGRAMS}
    assert roles["freecad"] == "preprocessor"
    assert roles["paraview"] == "postprocessor"
    solvers = {spec.id for spec in PROGRAMS if spec.role == "solver"}
    assert solvers == {"calculix", "codeaster", "opencourant", "fenicsx"}


def test_env_override_wins_and_missing_program_is_not_an_error(tmp_path):
    spec = ProgramSpec("dummy", "Dummy", "solver", "CAELAB_DUMMY_XYZ", ("dummy-xyz",))
    assert find_program(spec) is None
    target = tmp_path / "dummy-xyz"
    target.write_text("")
    os.environ["CAELAB_DUMMY_XYZ"] = str(target)
    try:
        assert find_program(spec) == str(target)
    finally:
        del os.environ["CAELAB_DUMMY_XYZ"]


def test_statuses_report_all_programs():
    statuses = program_statuses()
    assert len(statuses) == len(PROGRAMS)
    by_id = {s.spec.id: s for s in statuses}
    if CCX:
        assert by_id["calculix"].available


def test_launch_command_requires_availability():
    spec = ProgramSpec("dummy", "Dummy", "solver", "CAELAB_DUMMY_XYZ", ("dummy-xyz",))
    from caelab.connections import ProgramStatus

    assert launch_command(ProgramStatus(spec, None), "a.inp") is None
    assert launch_command(ProgramStatus(spec, "/bin/dummy"), "a.inp") == ["/bin/dummy", "a.inp"]


@pytest.mark.skipif(not CCX, reason="CAELAB_CCX not set")
def test_summary_matches_end_to_end_numbers(tmp_path):
    from conftest import cantilever_model

    wb = Workbench()
    job = wb.run(cantilever_model(), "calculix", tmp_path)
    assert job.result is not None
    summary = summarize_result(job.result)
    assert summary["success"]
    # equilibrium: the summary must carry the same numbers as the run
    assert summary["reaction_totals"]["fy"] == pytest.approx(1000.0, abs=0.5)
    assert summary["max_displacement_mm"] == pytest.approx(0.133, abs=0.01)
    text = format_summary_ko(summary)
    assert " m" in text
    assert "1,000.0 N" in text


@pytest.mark.parametrize("length,value,metres", [("m", 0.002, 0.002),
                                                ("mm", 2.0, 0.002),
                                                ("in", 2.0, None)])
def test_summary_preserves_actual_solver_and_declared_units(length, value, metres):
    result = ResultSet(solver_id="code_aster", success=True,
                       max_displacement=value, reaction_totals={"fy": 12.0})
    result.units = {"length": length, "force": "kN"}
    summary = summarize_result(result, solver="calculix")
    assert summary["solver"] == summary["solver_id"] == "code_aster"
    assert summary["max_displacement"] == value
    assert summary["units"] == result.units
    assert summary["units"] is not result.units
    assert summary["max_displacement_m"] == metres
    assert summary["max_displacement_mm"] == (metres * 1000 if metres is not None else None)
    text = format_summary_ko(summary)
    assert f"{value:.6g} {length}" in text
    assert "12.0 kN" in text
    assert "calculix" not in text


def test_summary_does_not_guess_units_or_replace_missing_values_with_zero():
    result = ResultSet(solver_id="unknown-solver", success=True,
                       max_displacement=None, reaction_totals={})
    result.units = {}
    summary = summarize_result(result)
    assert summary["max_displacement"] is None
    assert summary["max_displacement_m"] is None
    assert summary["max_displacement_mm"] is None
    assert "최대 변위 미확인" in format_summary_ko(summary)
    result.max_displacement = 0.0
    result.reaction_totals = {"fy": 0.0}
    text = format_summary_ko(summarize_result(result))
    assert "최대 변위 0 단위 미확인" in text
    assert "반력 합 Fy 0.0 단위 미확인" in text
    assert "사용자 선언 필요" in text
    assert " mm" not in text and " N" not in text


def test_failed_result_summary_keeps_failure_and_solver():
    result = ResultSet(solver_id="fenicsx", success=False, max_displacement=None)
    summary = summarize_result(result)
    assert not summary["success"]
    assert "fenicsx 계산이 실패" in format_summary_ko(summary)
