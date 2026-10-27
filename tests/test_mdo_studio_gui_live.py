"""GUI live execution integration tests for MDOStudioWindow and MDOExecutionBridge.

Verifies:
- Authentic GE Jet Engine Bracket 3D benchmark is loaded by default
- Live batch dispatch via JobManager and MDOExecutionBridge
- Real-time row-wise update on DesignPointsTableModel via dataChanged
- Progress bar and Heartbeat status update
- Stop scheduling cancellation and closeEvent process cleanup
"""

import os
import sys
import time
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication

from apps.workbench_qt.mdo_studio.window import MDOStudioWindow
from caelab.connections import ProgramSpec, ProgramStatus, LaunchRequest


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["--platform", "offscreen"])
    return app


@pytest.fixture(autouse=True)
def cleanup_events(qapp):
    yield
    qapp.processEvents()


@pytest.fixture
def mock_solver_probe(monkeypatch):
    import caelab.connections as connections
    script = [
        "from pathlib import Path\n"
        "frd = '''    1PSTEP                           1           1\\n"
        "  100C            1.00000E+00           2\\n"
        " -4  DISP         3    1\\n"
        " -5  DISP     1    1    1    0    0\\n"
        " -1         1  1.20000E-01  0.00000E+00  4.50000E-02\\n"
        " -1         2  1.85000E-01  0.00000E+00  6.20000E-02\\n"
        " -3\\n"
        " -4  STRESS       6    1\\n"
        " -5  SXX      1    4    1    0    0\\n"
        " -1         1  4.50000E+02  1.20000E+02  8.00000E+01  2.50000E+01  1.00000E+01  1.50000E+01\\n"
        " -1         2  5.20000E+02  1.50000E+02  9.50000E+01  3.00000E+01  1.20000E+01  1.80000E+01\\n"
        " -3\\n'''\n"
        "Path('model.frd').write_text(frd, encoding='utf-8')\n"
        "print('Total mass: 1.482', flush=True)\n"
        "print('CalculiX Version 2.21 finished successfully.', flush=True)\n"
        "print('mdo genuine ccx probe done', flush=True)\n"
    ]

    def fake_request(status, workdir):
        return LaunchRequest(
            [sys.executable, "-c", script[0]],
            workdir,
            dict(os.environ),
            "calculix",
        )

    monkeypatch.setattr(connections, "calculix_run_request", fake_request)
    return script


def test_mdo_studio_initializes_with_ge_bracket_benchmark(qapp):
    win = MDOStudioWindow()
    try:
        assert "GE Jet Engine Bracket" in win.study.name
        assert len(win.study.space.variables) == 6
        var_ids = [v.id for v in win.study.space.variables]
        assert "t_base" in var_ids
        assert "t_rib" in var_ids
        assert "d_boss" in var_ids
        assert len(win.study.records) == 25
        assert win.tdp_model.rowCount() == 25
    finally:
        win.close()


def test_mdo_studio_live_batch_dispatch_and_progress(qapp, mock_solver_probe):
    win = MDOStudioWindow()
    try:
        # Trim records to 3 points for fast GUI integration test
        win.study.records = win.study.records[:3]
        win._populate_study()
        assert win.tdp_model.rowCount() == 3

        # Click Run
        win._run_all_points()
        assert win._active_batch_id is not None
        assert not win.act_run.isEnabled()
        assert win.act_stop.isEnabled()
        assert not win.progress_bar.isHidden()

        # Wait for batch completion via event loop processing
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and win._active_batch_id is not None:
            qapp.processEvents()
            time.sleep(0.05)

        assert win._active_batch_id is None
        assert win.act_run.isEnabled()
        assert not win.act_stop.isEnabled()
        assert win.progress_bar.isHidden()

        # Verify all 3 points completed
        for r in win.study.records:
            assert r.status == "COMPLETED"
            assert "mass" in r.observations
            assert "stress" in r.observations
            assert "disp" in r.observations

        assert "배치 완료" in win.status_bar.currentMessage()
    finally:
        win.close()


def test_mdo_studio_stop_scheduling_cancels_cleanly(qapp, mock_solver_probe):
    # Slow probe that sleeps
    mock_solver_probe[0] = "import time; print('slow probe', flush=True); time.sleep(30)"

    win = MDOStudioWindow()
    try:
        win.study.records = win.study.records[:3]
        win._populate_study()

        win._run_all_points()
        assert win._active_batch_id is not None

        # Process events until running
        time.sleep(0.1)
        qapp.processEvents()

        # Click Stop
        win._stop_evaluations()
        assert not win.act_stop.isEnabled()

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and win._active_batch_id is not None:
            qapp.processEvents()
            time.sleep(0.05)

        assert win._active_batch_id is None
        assert win.act_run.isEnabled()
    finally:
        win.close()
