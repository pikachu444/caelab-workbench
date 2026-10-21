from caelab.model.store import load_project, save_project
from caelab.validate.checker import validate_model
from caelab.workbench import Workbench
from conftest import cantilever_model


def test_roundtrip(tmp_path):
    model = cantilever_model()
    save_project(model, tmp_path)
    loaded = load_project(tmp_path)
    assert loaded.to_dict() == model.to_dict()


def test_open_has_no_side_effects(tmp_path):
    model = cantilever_model()
    save_project(model, tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    Workbench().open_project(tmp_path)
    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after  # opening never creates run artefacts


def test_valid_model_has_no_issues():
    assert validate_model(cantilever_model()) == []


def test_missing_material_reference_is_an_issue_with_target():
    model = cantilever_model()
    model.sections[0].material = "NOPE"
    issues = validate_model(model)
    assert any(i.target_path == "sections[0]" for i in issues)


def test_broken_step_reference_is_an_issue():
    model = cantilever_model()
    model.steps[0].loads = ["GHOST"]
    issues = validate_model(model)
    assert any(i.target_path == "steps[0]" for i in issues)


def test_empty_set_is_an_issue():
    model = cantilever_model()
    model.sets["TIP"].members = []
    issues = validate_model(model)
    assert any(i.target_path == "sets.TIP" for i in issues)
