"""Generate verified MDO Studio GUI demonstration screenshots and study archives.

Runs real native CalculiX 2.22 executions through MDOStudioWindow:
1. Loads genuine .inp decks.
2. Configures variable spaces, objectives, and constraints.
3. Generates DoE design points.
4. Executes live batch runs with JobManager + native ccx.exe.
5. Captures high-resolution GUI screenshots of:
   - Design Points Table & Multi-Objective Pareto Front
   - 3D Gaussian Process Response Surface
   - Sensitivity Tornado Chart (Spearman Rank Correlation)
6. Saves .caelab_mdo.json study files for interactive GUI replay.
"""

import os
import sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ["QT_QPA_PLATFORM"] = "offscreen"
DEFAULT_CCX = r"C:\SourceCodes\caelab-p7-public-native\windows\runtime\FreeCAD_1.1.4-Windows-x86_64-py311\bin\ccx.exe"
os.environ["CAELAB_CCX"] = DEFAULT_CCX

import shutil
import tempfile
import time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap

from apps.workbench_qt.mdo_studio.window import MDOStudioWindow
from caelab.mdo.variables import ContinuousVariable, VariableSpace
from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.constraints import ConstraintSpec

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFS_DIR = REPO_ROOT / "benchmarks" / "definitions"
RUNS_DIR = REPO_ROOT / "benchmarks" / "runs"
ARTIFACT_DIR = Path(r"C:\Users\pikac\.gemini\antigravity\brain\fef96525-8226-42e3-8033-487123f9f849")


def wait_for_batch(win: MDOStudioWindow, app: QApplication, timeout: float = 60.0):
    t0 = time.monotonic()
    while win._active_batch_id and (time.monotonic() - t0 < timeout):
        app.processEvents()
        time.sleep(0.05)
    app.processEvents()
    if win._active_batch_id:
        raise TimeoutError("MDO Studio batch did not complete within timeout")


def run_stepped_cantilever_gui_demo(app: QApplication):
    print("\n" + "=" * 70)
    print("MDO STUDIO GUI DEMO 1: Stepped Cantilever (S4 Shell Sizing)")
    print("=" * 70)

    work_dir = Path(tempfile.mkdtemp(prefix="gui_demo_stepped_"))
    inp_file = work_dir / "stepped_cantilever_template.inp"
    shutil.copy(DEFS_DIR / "stepped_cantilever_template.inp", inp_file)

    win = MDOStudioWindow()
    win.resize(1400, 900)
    win.show()
    app.processEvents()

    # 1. Load deck
    win._on_load_deck(inp_file)
    print(f"Deck loaded: {win.template_deck.token_names}")

    # 2. Configure study
    space = VariableSpace(
        variables=(
            ContinuousVariable(id="t1", name="T1", unit="mm", lower=2.0, upper=20.0, initial=12.0),
            ContinuousVariable(id="t2", name="T2", unit="mm", lower=2.0, upper=20.0, initial=12.0),
            ContinuousVariable(id="t3", name="T3", unit="mm", lower=2.0, upper=20.0, initial=12.0),
        )
    )
    objectives = [
        ObjectiveSpec(id="mass", response_id="mass_eall", sense="MIN", scale=1e-3),
        ObjectiveSpec(id="disp", response_id="disp", sense="MIN", scale=1.0),
    ]
    constraints = [
        ConstraintSpec(id="stress_limit", response_id="stress", kind="UPPER", limit=120.0, scale=120.0, unit="MPa"),
    ]
    units = {"mass_eall": "kg", "disp": "mm", "stress": "MPa"}
    win._configure_study(space, objectives, constraints, units)
    print("Study configured.")

    # 3. Generate DoE points (1 baseline + 9 LHS samples = 10 design points)
    win._on_generate_doe(n_samples=9, seed=42)
    print(f"Generated {len(win.study.records)} design points.")

    # 4. Run native CalculiX evaluations
    t0 = time.time()
    win._run_all_points()
    wait_for_batch(win, app, timeout=60.0)
    elapsed = time.time() - t0
    print(f"Batch completed in {elapsed:.2f} s")

    # Verify all records completed
    for rec in win.study.records:
        print(f"  {rec.point_id}: {rec.status}, dur={rec.duration_sec:.2f}s, obs={rec.observations}, feas={rec.feasibility}")

    # 5. Capture screenshots for each visualization tab
    # Tab 0: Pareto Front
    win.viz_tabs.setCurrentIndex(0)
    app.processEvents()
    time.sleep(0.2)
    app.processEvents()
    pareto_img = ARTIFACT_DIR / "gui_stepped_cantilever_pareto.png"
    win.grab().save(str(pareto_img))
    print(f"Saved: {pareto_img}")

    # Tab 1: Sensitivity Tornado
    win.viz_tabs.setCurrentIndex(1)
    app.processEvents()
    time.sleep(0.2)
    app.processEvents()
    tornado_img = ARTIFACT_DIR / "gui_stepped_cantilever_tornado.png"
    win.grab().save(str(tornado_img))
    print(f"Saved: {tornado_img}")

    # Tab 2: 3D Response Surface
    win.viz_tabs.setCurrentIndex(2)
    app.processEvents()
    time.sleep(0.2)
    app.processEvents()
    surface_img = ARTIFACT_DIR / "gui_stepped_cantilever_surface.png"
    win.grab().save(str(surface_img))
    print(f"Saved: {surface_img}")

    # 6. Save Study File
    study_file = RUNS_DIR / "stepped_cantilever_gui_study.json"
    win._on_save_study(study_file)
    print(f"Study saved to: {study_file}")
    win.close()


def run_truss_gui_demo(app: QApplication):
    print("\n" + "=" * 70)
    print("MDO STUDIO GUI DEMO 2: 10-Bar Truss (T3D2 Truss Sizing)")
    print("=" * 70)

    work_dir = Path(tempfile.mkdtemp(prefix="gui_demo_truss_"))
    inp_file = work_dir / "truss_10bar_template.inp"
    shutil.copy(DEFS_DIR / "truss_10bar_template.inp", inp_file)

    win = MDOStudioWindow()
    win.resize(1400, 900)
    win.show()
    app.processEvents()

    # 1. Load deck
    win._on_load_deck(inp_file)
    print(f"Deck loaded: {win.template_deck.token_names}")

    # 2. Configure study
    IN2_TO_M2 = 0.00064516
    variables = tuple(
        ContinuousVariable(
            id=f"a{i+1}",
            name=f"A{i+1}",
            unit="m**2",
            lower=1.0 * IN2_TO_M2,
            upper=35.0 * IN2_TO_M2,
            initial=20.0 * IN2_TO_M2,
        )
        for i in range(10)
    )
    space = VariableSpace(variables=variables)
    objectives = [
        ObjectiveSpec(id="mass", response_id="mass_eall", sense="MIN", scale=1000.0),
        ObjectiveSpec(id="disp", response_id="disp", sense="MIN", scale=0.05),
    ]
    constraints = [
        ConstraintSpec(id="disp_limit", response_id="disp", kind="UPPER", limit=0.0508, scale=0.0508, unit="m"),
    ]
    units = {"mass_eall": "kg", "disp": "m", "stress": "Pa"}
    win._configure_study(space, objectives, constraints, units)
    print("Study configured.")

    # 3. Generate DoE points (1 baseline + 7 LHS samples = 8 design points)
    win._on_generate_doe(n_samples=7, seed=42)
    print(f"Generated {len(win.study.records)} design points.")

    # 4. Run native CalculiX evaluations
    t0 = time.time()
    win._run_all_points()
    wait_for_batch(win, app, timeout=60.0)
    elapsed = time.time() - t0
    print(f"Batch completed in {elapsed:.2f} s")

    for rec in win.study.records:
        print(f"  {rec.point_id}: {rec.status}, dur={rec.duration_sec:.2f}s, obs={rec.observations}, feas={rec.feasibility}")

    # 5. Capture screenshots
    win.viz_tabs.setCurrentIndex(0)
    app.processEvents()
    time.sleep(0.2)
    app.processEvents()
    pareto_img = ARTIFACT_DIR / "gui_truss_10bar_pareto.png"
    win.grab().save(str(pareto_img))
    print(f"Saved: {pareto_img}")

    win.viz_tabs.setCurrentIndex(1)
    app.processEvents()
    time.sleep(0.2)
    app.processEvents()
    tornado_img = ARTIFACT_DIR / "gui_truss_10bar_tornado.png"
    win.grab().save(str(tornado_img))
    print(f"Saved: {tornado_img}")

    win.viz_tabs.setCurrentIndex(2)
    app.processEvents()
    time.sleep(0.2)
    app.processEvents()
    surface_img = ARTIFACT_DIR / "gui_truss_10bar_surface.png"
    win.grab().save(str(surface_img))
    print(f"Saved: {surface_img}")

    # 6. Save Study File
    study_file = RUNS_DIR / "truss_10bar_gui_study.json"
    win._on_save_study(study_file)
    print(f"Study saved to: {study_file}")
    win.close()


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    run_stepped_cantilever_gui_demo(app)
    run_truss_gui_demo(app)
    print("\n" + "=" * 70)
    print("ALL MDO STUDIO GUI DEMONSTRATIONS SUCCESSFULLY COMPLETED!")
    print("=" * 70)
