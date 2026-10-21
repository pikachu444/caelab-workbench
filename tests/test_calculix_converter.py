import pytest

from caelab.model.schema import Material
from caelab.solvers.calculix import CalculiXConverter, find_ccx
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
