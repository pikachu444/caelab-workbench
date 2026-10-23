from pathlib import Path
import re

import pytest

from caelab.model.schema import (
    BoundaryCondition,
    Material,
    MeshData,
    NamedSet,
    Step,
    StructuralModel,
)
from caelab.solvers.calculix import CalculiXConverter, find_ccx, parse_dat
from conftest import cantilever_model


def test_support_table_is_the_single_declaration():
    conv = CalculiXConverter()
    table = conv.support()
    assert "C3D10" in table.elements
    assert "linear_static" in table.steps
    assert conv.validate(cantilever_model()) == []


def test_unsupported_element_is_a_support_issue_not_an_allowlist():
    model = cantilever_model()
    for elem in model.mesh.elements.values():
        elem["type"] = "C3D20"  # schema allows it; this converter does not write it yet
    issues = CalculiXConverter().validate(model)
    assert any(i.target_path == "mesh" for i in issues)


def test_deck_keywords(tmp_path):
    conv = CalculiXConverter()
    deck = conv.write_input(cantilever_model(), tmp_path)
    text = deck.read_text()
    for keyword in ("*NODE", "*ELEMENT, TYPE=C3D8", "*MATERIAL, NAME=STEEL",
                    "*ELASTIC", "*SOLID SECTION", "*STEP", "*STATIC",
                    "*BOUNDARY", "*CLOAD", "*NODE FILE", "*END STEP"):
        assert keyword in text, keyword


def test_plastic_material_and_nlgeom_step(tmp_path):
    model = cantilever_model()
    model.materials[0] = Material(
        "STEEL", "j2_plasticity",
        {"youngs_modulus": 210e9, "poissons_ratio": 0.3, "density": 7850.0,
         "plastic": [[250e6, 0.0], [300e6, 0.01]]},
    )
    model.steps[0].kind = "nonlinear_static"
    text = CalculiXConverter().write_input(model, tmp_path).read_text()
    assert "*PLASTIC" in text
    assert "*STEP, NLGEOM" in text


# ------------------------------------------------------------- .dat parsing
# Both texts are the real ccx 2.23 .dat shapes (per-node *NODE PRINT and
# the FreeCAD FEM TOTALS=ONLY reaction print), trimmed to a few rows.

_PER_NODE_DAT = """
                        S T E P       1


                                INCREMENT     1


 displacements (vx,vy,vz) for set ALL_NODES and time  0.1000000E+01

         1  0.000000E+00  0.000000E+00  0.000000E+00
         4 -1.428571E-03 -1.428571E-03 -1.714286E-03

 forces (fx,fy,fz) for set ALL_NODES and time  0.1000000E+01

         1  6.454589E-14  1.898626E-13  1.000000E+02
         4 -5.099293E-15  1.121845E-14 -1.000000E+02
"""

_TOTALS_DAT = """
                        S T E P       1


                                INCREMENT     1


 total force (fx,fy,fz) for set FIXED and time  0.1000000E+01

        1.268745E-10 -1.652438E-10  9.813477E+02
"""


def test_parse_dat_per_node_blocks(tmp_path):
    path = tmp_path / "job.dat"
    path.write_text(_PER_NODE_DAT)
    parsed = parse_dat(path)
    assert parsed.nodal_forces[1][2] == pytest.approx(100.0)
    assert parsed.nodal_forces[4][2] == pytest.approx(-100.0)
    assert parsed.displacements[4][0] == pytest.approx(-1.428571e-3)
    assert parsed.max_displacement == pytest.approx(2.6496e-3, rel=0.01)
    assert parsed.totals_by_set == {}


def test_parse_dat_totals_only_block(tmp_path):
    path = tmp_path / "job.dat"
    path.write_text(_TOTALS_DAT)
    parsed = parse_dat(path)
    assert parsed.nodal_forces == {}
    # the FreeCAD cantilever's measured reaction: +981.35 N in Z
    assert parsed.totals_by_set["FIXED"][2] == pytest.approx(981.3477)


_TET_DECK = """*HEADING
dat-reader test tet
*NODE
1, 0, 0, 0
2, 10, 0, 0
3, 0, 10, 0
4, 0, 0, 10
5, 5, 0, 0
6, 5, 5, 0
7, 0, 5, 0
8, 0, 0, 5
9, 5, 0, 5
10, 0, 5, 5
*ELEMENT, TYPE=C3D10, ELSET=BODY
1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
*NSET, NSET=ALL_NODES
1, 2, 3, 4, 5, 6, 7, 8, 9, 10
*NSET, NSET=PIN
1
*MATERIAL, NAME=MAT
*ELASTIC
210000, 0.3
*SOLID SECTION, ELSET=BODY, MATERIAL=MAT
*STEP
*STATIC
*BOUNDARY
1, 1, 3, 0
2, 2, 3, 0
3, 3, 3, 0
*CLOAD
4, 3, -100
{output}
*END STEP
"""


def _tet_model(bc_set: str) -> StructuralModel:
    return StructuralModel(
        name="tet",
        sets={bc_set: NamedSet("node", [1])},
        mesh=MeshData(nodes={1: [0, 0, 0]}, elements={}),
        steps=[Step("S1", "linear_static", bcs=["BC1"])],
        bcs=[BoundaryCondition("BC1", bc_set, [1, 2, 3])],
    )


@pytest.mark.skipif(find_ccx() is None, reason="ccx not available")
def test_run_reads_reactions_from_dat_per_node(tmp_path):
    """Deck with no FORC in the .frd: reactions must come from the
    per-node forces block of the .dat (*NODE PRINT U, RF)."""
    deck = _TET_DECK.format(
        output="*NODE PRINT, NSET=ALL_NODES\nU, RF\n*NODE FILE, NSET=ALL_NODES\nU"
    )
    (tmp_path / "job.inp").write_text(deck)
    result = CalculiXConverter().run(_tet_model("ALL_NODES"), tmp_path / "job.inp", tmp_path)
    assert result.success, result.log_tail
    assert result.reaction_totals["fz"] == pytest.approx(100.0, rel=0.01)


@pytest.mark.skipif(find_ccx() is None, reason="ccx not available")
def test_run_reads_reactions_from_dat_totals_only(tmp_path):
    """FreeCAD-style deck: *NODE PRINT, TOTALS=ONLY for the fixed set."""
    deck = _TET_DECK.format(
        output="*NODE PRINT, NSET=PIN, TOTALS=ONLY\nRF\n*NODE FILE\nU"
    )
    (tmp_path / "job.inp").write_text(deck)
    result = CalculiXConverter().run(_tet_model("PIN"), tmp_path / "job.inp", tmp_path)
    assert result.success, result.log_tail
    assert result.reaction_totals["fz"] == pytest.approx(100.0, rel=0.01)


@pytest.mark.skipif(find_ccx() is None, reason="ccx not available")
def test_end_to_end_cantilever(tmp_path):
    """Real solver run: equilibrium must hold exactly and the tip
    deflection must land in a sane band of the analytic value."""
    from caelab.workbench import Workbench

    model = cantilever_model()
    job = Workbench().run(model, "calculix", tmp_path / "run")
    assert job.state == "succeeded", job.result.log_tail if job.result else ""
    result = job.result
    # equilibrium: reactions balance the applied -1000 N in Y
    assert result.reaction_totals["fy"] == pytest.approx(1000.0, rel=0.005)
    assert abs(result.reaction_totals["fx"]) < 1.0
    assert abs(result.reaction_totals["fz"]) < 1.0
    # analytic tip deflection: P L^3 / (3 E I), I = W H^3 / 12
    inertia = 0.1 * 0.1 ** 3 / 12
    analytic = 1000.0 * 1.0 ** 3 / (3 * 210e9 * inertia)
    assert 0.4 * analytic < result.max_displacement < 1.2 * analytic


# Synthetic fixed-width records follow CalculiX frdheader.c / frd.c layout.
def _frd_block(name, values, time=1.0, step=1, increment=1, count=None):
    header = "    1PSTEP".ljust(24) + f"{1:12d}{increment:12d}{step:12d}"
    frame = "  100CL".ljust(12) + f"{time:12.5E}{len(values) if count is None else count:12d}"
    data = [header, frame, " -4  " + name.ljust(8)]
    data += [f" -1{node:10d}" + "".join(f"{value:12.5E}" for value in vector) for node, vector in values.items()]
    return "\n".join(data + [" -3"]) + "\n"


@pytest.mark.parametrize("change,path", [
    (lambda m: setattr(m.steps[0], "kind", "modal"), "steps[0]"),
    (lambda m: m.steps[0].options.update(increments=20), "steps[0].options.increments"),
    (lambda m: m.materials[0].params.update(damping=0.1), "materials[0].params.damping"),
    (lambda m: setattr(m.sections[0], "kind", "shell"), "sections[0].kind"),
    (lambda m: m.sections[0].options.update(thickness=0.1), "sections[0].options.thickness"),
    (lambda m: m.initial_conditions.update(temperature=300), "initial_conditions"),
    (lambda m: m.outputs.update(frequency=2), "outputs.frequency"),
    (lambda m: m.outputs.update(fields=["U", "TEMP"]), "outputs.fields"),
    (lambda m: m.loads[0].values.update(amplitude="Ramp"), "loads[0].values.amplitude"),
    (lambda m: m.bcs[0].values.update({"2": 0.0, 2: 1.0}), "bcs[0].values.2"),
])
def test_ignored_input_is_rejected_before_writing(tmp_path, change, path):
    model = cantilever_model()
    change(model)
    converter = CalculiXConverter()
    assert any(issue.target_path == path for issue in converter.validate(model))
    with pytest.raises(ValueError, match=re.escape(path)):
        converter.write_input(model, tmp_path)
    assert not (tmp_path / "job.inp").exists()


@pytest.mark.parametrize("text", ["", "arbitrary bytes", " -4  DISP\n -1       broken\n -3\n"])
def test_frd_empty_or_corrupt_is_not_success_or_zero(tmp_path, text):
    from caelab.solvers.calculix import parse_frd
    path = tmp_path / "job.frd"
    path.write_text(text)
    result = parse_frd(path)
    assert not result.success
    assert result.max_displacement is None


def test_frd_selects_latest_frame_and_keeps_valid_zero(tmp_path):
    from caelab.solvers.calculix import parse_frd
    path = tmp_path / "job.frd"
    path.write_text(_frd_block("DISP", {1: [3, 4, 0]}, time=0.5) + _frd_block("DISP", {1: [0, 0, 0]}))
    result = parse_frd(path)
    assert result.success
    assert result.max_displacement == 0.0
    path.write_text(path.read_text() + _frd_block("FORC", {1: [1, 2, 3]}, time=2.0))
    result = parse_frd(path)
    assert not result.success
    assert result.max_displacement is None


@pytest.mark.parametrize("text", [
    _frd_block("DISP", {1: [0, 0, 0]}, count=2),
    _frd_block("DISP", {1: [float("nan"), 0, 0]}),
    _frd_block("DISP", {1: [0, 0, 0]}).removesuffix(" -3\n"),
])
def test_frd_incomplete_or_nonfinite_records_fail(tmp_path, text):
    from caelab.solvers.calculix import parse_frd
    path = tmp_path / "job.frd"
    path.write_text(text)
    assert not parse_frd(path).success


def test_dat_final_frame_replaces_earlier_nodes_and_detects_local_or_corrupt_rows(tmp_path):
    path = tmp_path / "job.dat"
    path.write_text(_PER_NODE_DAT + _PER_NODE_DAT.replace("INCREMENT     1", "INCREMENT     2").replace("0.1000000E+01", "0.2000000E+01").replace("         4 -1.428571E-03 -1.428571E-03 -1.714286E-03", ""))
    assert set(parse_dat(path).displacements) == {1}
    path.write_text(_PER_NODE_DAT.replace("1.000000E+02", "1.000000E+02 L"))
    assert parse_dat(path).errors
    path.write_text(_PER_NODE_DAT.replace("1.000000E+02", "NaN"))
    assert parse_dat(path).errors


def test_dat_reappearing_frame_selects_last_occurrence_without_old_fields(tmp_path):
    path = tmp_path / "job.dat"
    path.write_text(
        " displacements (vx,vy,vz) for set OLD and time 1.0\n 1 3.0 4.0 0.0\n"
        " temperatures for set HOT and time 2.0\n 1 300.0\n"
        " forces (fx,fy,fz) for set FINAL and time 1.0\n 2 1.0 2.0 3.0\n")
    parsed = parse_dat(path)
    assert parsed.frame == (None, None, 1.0)
    assert parsed.nodal_forces == {2: [1, 2, 3]}
    assert parsed.displacements == {} and parsed.max_displacement is None
    assert parsed.unsupported_blocks == [] and parsed.errors == []


def test_dat_final_unsupported_frame_preserves_header_without_interpreting_values(tmp_path):
    path = tmp_path / "job.dat"
    path.write_text(_PER_NODE_DAT + " S T E P 2\n INCREMENT 1\n"
                    " stresses (elem, integ.pnt.,sxx,syy,szz,sxy,sxz,syz) for set BODY and time 2.0\n"
                    " 71 1 20.0 0.0 0.0 0.0 0.0 0.0\n")
    parsed = parse_dat(path)
    assert parsed.frame == (2, 1, 2.0)
    assert parsed.displacements == {} and parsed.nodal_forces == {} and parsed.max_displacement is None
    assert parsed.unsupported_blocks[0]["set"] == "BODY"
    assert parsed.unsupported_blocks[0]["frame"] == (2, 1, 2.0)
    assert parsed.errors == []


def _fake_solver(monkeypatch, tmp_path, frd_text, dat_text=None, stdout="solver output"):
    import subprocess
    import caelab.solvers.calculix as module
    monkeypatch.setattr(module, "find_ccx", lambda: "mock-ccx")
    def execute(*args, **kwargs):
        if frd_text is not None:
            (tmp_path / "job.frd").write_text(frd_text)
        if dat_text is not None:
            (tmp_path / "job.dat").write_text(dat_text)
        return subprocess.CompletedProcess(args[0], 0, stdout, "")
    monkeypatch.setattr(module.subprocess, "run", execute)


def test_converter_does_not_use_displacements_before_final_unsupported_dat_frame(tmp_path, monkeypatch):
    model = cantilever_model()
    converter = CalculiXConverter()
    deck = converter.write_input(model, tmp_path)
    dat = _PER_NODE_DAT + " temperatures for set HOT and time 2.0\n 1 300.0\n"
    _fake_solver(monkeypatch, tmp_path, None, dat)
    result = converter.run(model, deck, tmp_path)
    assert not result.success
    assert result.displacements == {} and result.max_displacement is None
    assert any("유효한 결과 프레임" in issue for issue in result.validation_issues)


def test_run_requires_actual_requested_stress_and_keeps_full_log(tmp_path, monkeypatch):
    model = cantilever_model()
    converter = CalculiXConverter()
    deck = converter.write_input(model, tmp_path)
    text = _frd_block("DISP", {1: [0, 0, 0]}) + _frd_block("FORC", {1: [0, 0, 1]})
    _fake_solver(monkeypatch, tmp_path, text, stdout="beginning\n" + "x" * 5000)
    result = converter.run(model, deck, tmp_path)
    assert not result.success
    assert any("outputs.S" in issue for issue in result.validation_issues)
    assert "beginning" in Path(result.log_path).read_text(encoding="utf-8")
    assert "beginning" not in result.log_tail
    assert result.units == {"length": "m", "force": "N"}


def test_run_direct_deck_uses_requested_fields_and_exact_reaction_set(tmp_path, monkeypatch):
    deck = tmp_path / "job.inp"
    deck.write_text("*NODE FILE\nU\n*NODE PRINT, NSET=PIN, TOTALS=ONLY\nRF\n")
    _fake_solver(monkeypatch, tmp_path, _frd_block("DISP", {1: [0, 0, 0]}), _TOTALS_DAT)
    result = CalculiXConverter().run(_tet_model("PIN"), deck, tmp_path)
    assert not result.success
    assert any("outputs.RF.PIN" in issue for issue in result.validation_issues)
    assert result.reaction_totals == {}  # FIXED output must not be relabelled PIN
    _fake_solver(monkeypatch, tmp_path, _frd_block("DISP", {1: [0, 0, 0]}), _TOTALS_DAT.replace("FIXED", "PIN"))
    assert CalculiXConverter().run(_tet_model("PIN"), deck, tmp_path).reaction_totals["fz"] == pytest.approx(981.3477)


def test_run_rejects_stale_output_and_timeout_preserves_captured_log(tmp_path, monkeypatch):
    import subprocess
    import caelab.solvers.calculix as module
    deck = tmp_path / "job.inp"
    deck.write_text("*NODE FILE\nU\n")
    (tmp_path / "job.frd").write_text(_frd_block("DISP", {1: [0, 0, 0]}))
    _fake_solver(monkeypatch, tmp_path, None)
    assert not CalculiXConverter().run(_tet_model("PIN"), deck, tmp_path).success
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 3600, output=b"partial output", stderr=b"partial error")
    monkeypatch.setattr(module.subprocess, "run", timeout)
    result = CalculiXConverter().run(_tet_model("PIN"), deck, tmp_path)
    assert not result.success
    assert "partial output" in Path(result.log_path).read_text(encoding="utf-8")
    assert "TimeoutExpired" in result.log_tail


@pytest.mark.parametrize("change,path", [
    (lambda m: m.steps.append(Step("SECOND", "linear_static")), "steps"),
    (lambda m: (setattr(m.steps[0], "kind", "nonlinear_static"), m.steps[0].options.update(nlgeom=False)), "steps[0].options.nlgeom"),
    (lambda m: m.materials[0].params.pop("density"), "materials[0].params.density"),
    (lambda m: setattr(m.loads[0], "target_set", "TIP"), "loads[0].target_set"),
    (lambda m: (setattr(m, "unit_system", "mm-tonne-s"), m.loads[0].values.pop("accel")), "loads[0].values.accel"),
])
def test_unrepresented_step_and_gravity_semantics_are_rejected(change, path):
    from caelab.model.schema import Load
    model = cantilever_model()
    model.loads = [Load("TIP_FORCE", "gravity", values={"accel": 9.81})]
    change(model)
    assert any(issue.target_path == path for issue in CalculiXConverter().validate(model))


def test_dat_duplicate_or_conflicting_blocks_fail(tmp_path):
    path = tmp_path / "job.dat"
    path.write_text(_PER_NODE_DAT + _PER_NODE_DAT)
    assert parse_dat(path).errors
    path.write_text(_PER_NODE_DAT + _PER_NODE_DAT.replace("ALL_NODES", "OTHER").replace("1.000000E+02", "2.000000E+02"))
    assert parse_dat(path).errors


def test_run_does_not_merge_different_frames(tmp_path, monkeypatch):
    deck = tmp_path / "job.inp"
    deck.write_text("*NODE FILE\nU\n*NODE PRINT, NSET=PIN\nRF\n")
    _fake_solver(monkeypatch, tmp_path, _frd_block("DISP", {1: [0, 0, 0]}, time=2), _TOTALS_DAT)
    result = CalculiXConverter().run(_tet_model("PIN"), deck, tmp_path)
    assert not result.success
    assert any("results.frame" in issue for issue in result.validation_issues)


def test_run_support_force_requires_complete_unloaded_supports(tmp_path, monkeypatch):
    from caelab.model.schema import Load
    deck = tmp_path / "job.inp"
    deck.write_text("*NODE FILE\nU, RF\n")
    text = _frd_block("DISP", {1: [0, 0, 0]}) + _frd_block("FORC", {1: [7, 8, 9]})
    _fake_solver(monkeypatch, tmp_path, text)
    model = _tet_model("PIN")
    model.bcs[0].fixed_dofs = [3]
    result = CalculiXConverter().run(model, deck, tmp_path)
    assert result.success
    assert result.reaction_totals == {"fx": 0, "fy": 0, "fz": 9}
    model.sets["PIN"].members.append(2)
    assert CalculiXConverter().run(model, deck, tmp_path).reaction_totals == {}
    model.sets["PIN"].members = [1]
    model.loads.append(Load("support_force", "force", "PIN", {"fz": 3}))
    assert CalculiXConverter().run(model, deck, tmp_path).reaction_totals == {}



def test_frd_requires_header_and_rejects_repeated_field(tmp_path):
    from caelab.solvers.calculix import parse_frd
    path = tmp_path / "job.frd"
    block = _frd_block("DISP", {1: [0, 0, 0]})
    path.write_text("\n".join(block.splitlines()[2:]))
    assert not parse_frd(path).success
    path.write_text(block + block)
    assert not parse_frd(path).success


def test_run_ignores_unused_dat_and_does_not_invent_dat_step_ids(tmp_path, monkeypatch):
    deck = tmp_path / "job.inp"
    deck.write_text("*NODE FILE\nU, RF\n")
    frd = _frd_block("DISP", {1: [0, 0, 0]}, step=2, increment=5)
    frd += _frd_block("FORC", {1: [0, 0, 2]}, step=2, increment=5)
    _fake_solver(monkeypatch, tmp_path, frd, _TOTALS_DAT.replace("0.1000000E+01", "0.2000000E+01"))
    assert CalculiXConverter().run(_tet_model("PIN"), deck, tmp_path).success
    deck.write_text("*NODE FILE\nU\n*NODE PRINT, NSET=PIN, TOTALS=ONLY\nRF\n")
    dat = _TOTALS_DAT[_TOTALS_DAT.index(" total force"):].replace("FIXED", "PIN")
    frd = _frd_block("DISP", {1: [0, 0, 0]}, step=2, increment=5)
    _fake_solver(monkeypatch, tmp_path, frd, dat)
    assert CalculiXConverter().run(_tet_model("PIN"), deck, tmp_path).success
    _fake_solver(monkeypatch, tmp_path, _frd_block("DISP", {1: [0, 0, 0]}) + frd, dat)
    assert not CalculiXConverter().run(_tet_model("PIN"), deck, tmp_path).success



@pytest.mark.parametrize("output_request,dat", [
    ("*NODE PRINT, NSET=PIN, TOTALS=ONLY\nRF", _TOTALS_DAT),
    ("*NODE PRINT, NSET=PIN\nRF", _PER_NODE_DAT),
    ("*NODE PRINT, NSET=PIN\nU", _PER_NODE_DAT),
    ("*NODE PRINT, NSET=PIN\nRF", _TOTALS_DAT.replace("FIXED", "PIN")),
    ("*NODE PRINT, NSET=PIN, TOTALS=ONLY\nRF", _PER_NODE_DAT.replace("ALL_NODES", "PIN")),
    ("*NODE FILE, NSET=MISSING\nRF", _PER_NODE_DAT),
])
def test_requested_output_scope_and_form_cannot_use_unrelated_dat(tmp_path, monkeypatch, output_request, dat):
    deck = tmp_path / "job.inp"
    deck.write_text("*NODE FILE\nU\n" + output_request + "\n")
    frd = _frd_block("DISP", {1: [0, 0, 0]}) + _frd_block("FORC", {1: [1, 2, 3]})
    _fake_solver(monkeypatch, tmp_path, frd, dat)
    result = CalculiXConverter().run(_tet_model("PIN"), deck, tmp_path)
    assert not result.success
    assert any("outputs." in issue for issue in result.validation_issues)


def test_present_scoped_force_can_remain_unqualified_as_reaction(tmp_path, monkeypatch):
    from caelab.model.schema import Load
    deck = tmp_path / "job.inp"
    deck.write_text("*NODE FILE\nU\n*NODE PRINT, NSET=PIN\nRF\n")
    _fake_solver(monkeypatch, tmp_path, _frd_block("DISP", {1: [0, 0, 0]}), _PER_NODE_DAT.replace("ALL_NODES", "PIN"))
    model = _tet_model("PIN")
    model.loads = [Load("SUPPORT_LOAD", "force", "PIN", {"fz": 1})]
    result = CalculiXConverter().run(model, deck, tmp_path)
    assert result.success
    assert result.nodal_forces[1][2] == 100
    assert result.reaction_totals == {}



@pytest.mark.parametrize("field,mode,complete,with_total,expected", [
    ("RF", "NO", False, False, False),
    ("U", "NO", False, False, False),
    ("RF", "YES", False, True, False),
    ("RF", "YES", True, False, False),
    ("RF", "YES", True, True, True),
    ("RF", "NO", True, False, True),
    ("RF", "ONLY", False, True, True),
])
def test_node_print_requires_complete_requested_membership(tmp_path, monkeypatch, field, mode, complete, with_total, expected):
    deck = tmp_path / "job.inp"
    deck.write_text(f"*NODE FILE\nU\n*NODE PRINT, NSET=SUPPORT, TOTALS={mode}\n{field}\n")
    label = "forces (fx,fy,fz)" if field == "RF" else "displacements (vx,vy,vz)"
    dat = f" {label} for set SUPPORT and time 1.0\n 1 1.0 2.0 3.0\n"
    if complete:
        dat += " 2 4.0 5.0 6.0\n"
    if with_total:
        dat += " total force (fx,fy,fz) for set SUPPORT and time 1.0\n 5.0 7.0 9.0\n"
    frd = _frd_block("DISP", {1: [0, 0, 0], 2: [0, 0, 0]})
    _fake_solver(monkeypatch, tmp_path, frd, dat)
    model = _tet_model("SUPPORT")
    model.sets["SUPPORT"].members = [1, 2]
    model.mesh.nodes[2] = [1, 0, 0]
    result = CalculiXConverter().run(model, deck, tmp_path)
    assert result.success is expected
    if not expected:
        assert any(f"outputs.{field}.SUPPORT" in issue for issue in result.validation_issues)
