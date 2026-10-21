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
