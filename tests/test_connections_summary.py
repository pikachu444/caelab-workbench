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
    assert solvers == {"calculix", "codeaster", "opencourant"}


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
    assert "0.133 mm" in text
    assert "1,000.0 N" in text
