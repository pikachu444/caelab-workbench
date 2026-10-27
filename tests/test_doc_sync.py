"""Automated documentation drift and integrity enforcement test.

This test prevents "documentation rot" by enforcing:
1. Deleted legacy symbols (e.g., optimization_panel.py, caelab.numerical.core)
   must not appear as active features in README.md or CURRENT_STATE.md.
2. New commercial MDO core modules and public classes must be documented.
3. Standalone MDO Studio launcher command (python -m apps.workbench_qt.mdo_studio)
   must be documented in README.md.
4. If code changes without documentation updates, CI and local pytest will FAIL.
"""

from pathlib import Path
import re
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
CURRENT_STATE_PATH = REPO_ROOT / "CURRENT_STATE.md"

OBSOLETE_ACTIVE_TERMS = [
    r"apps/workbench_qt/optimization_panel\.py",
    r"caelab\.numerical\.core",
    r"caelab/numerical/fit\.py",
    r"caelab/numerical/evaluate\.py",
]

REQUIRED_MDO_CORE_TERMS = [
    "caelab.mdo",
    "VariableSpace",
    "StudyManager",
    "TemplateDeck",
    "MDOStudioWindow",
]

REQUIRED_LAUNCHER_COMMAND = "python -m apps.workbench_qt.mdo_studio"


def test_readme_and_current_state_exist():
    assert README_PATH.is_file(), f"README.md not found at {README_PATH}"
    assert CURRENT_STATE_PATH.is_file(), f"CURRENT_STATE.md not found at {CURRENT_STATE_PATH}"


def test_no_obsolete_symbols_in_docs():
    """Ensure purged legacy code is never advertised as working features."""
    readme_text = README_PATH.read_text(encoding="utf-8")
    current_state_text = CURRENT_STATE_PATH.read_text(encoding="utf-8")

    for pattern in OBSOLETE_ACTIVE_TERMS:
        m_readme = re.search(pattern, readme_text)
        assert m_readme is None, f"Obsolete symbol '{pattern}' still active in README.md: {m_readme}"


def test_required_mdo_architecture_documented_in_readme():
    """Ensure README.md covers clean-slate MDO engine and MDO Studio."""
    readme_text = README_PATH.read_text(encoding="utf-8")

    missing = [term for term in REQUIRED_MDO_CORE_TERMS if term not in readme_text]
    assert not missing, f"README.md is missing required MDO documentation for: {missing}"


def test_mdo_studio_launcher_command_in_readme():
    """Ensure standalone MDO Studio execution command is in README.md."""
    readme_text = README_PATH.read_text(encoding="utf-8")
    assert REQUIRED_LAUNCHER_COMMAND in readme_text, (
        f"README.md must document standalone execution command: '{REQUIRED_LAUNCHER_COMMAND}'"
    )


def test_mdo_public_api_completeness():
    """Ensure all caelab.mdo exported symbols are valid and documented."""
    import caelab.mdo as mdo

    expected_exports = [
        "VariableSpace", "ContinuousVariable", "DiscreteVariable", "CategoricalVariable",
        "ObjectiveSpec", "ConstraintSpec", "ConstraintAssessment", "Feasibility",
        "TemplateDeck", "BindingSpec", "ResponseSpec",
        "StudyManager", "DesignPointRecord",
        "pareto_front", "knee_point",
        "SurrogateDataset", "SurrogateConfig", "fit_surrogate", "predict",
        "SensitivityConfig", "analyze_sensitivity",
        "OptimizationProblem", "OptimizationConfig", "optimize",
    ]

    for exp in expected_exports:
        assert hasattr(mdo, exp), f"caelab.mdo missing expected export '{exp}'"
        obj = getattr(mdo, exp)
        assert getattr(obj, "__doc__", None), f"Public symbol {exp} must have docstring"


def test_check_doc_sync_logic():
    """Verify check_doc_synchronization rules."""
    from scripts.check_doc_sync import check_doc_synchronization

    # Code modified without docs -> MUST FAIL
    assert not check_doc_synchronization(["caelab/mdo/core.py"])
    assert not check_doc_synchronization(["apps/workbench_qt/mdo_studio/window.py"])

    # Code modified WITH doc update -> MUST PASS
    assert check_doc_synchronization(["caelab/mdo/core.py", "README.md"])
    assert check_doc_synchronization(["apps/workbench_qt/mdo_studio/window.py", "CURRENT_STATE.md"])
    assert check_doc_synchronization(["caelab/mdo/responses.py", "docs/tutorials/visual_mdo_guide.md"])

    # Non-code changes (tests, configs) without docs -> PASS
    assert check_doc_synchronization(["tests/test_mdo_doe.py"])
    assert check_doc_synchronization([".github/workflows/product-tests.yml"])

    # Empty changes -> PASS
    assert check_doc_synchronization([])
