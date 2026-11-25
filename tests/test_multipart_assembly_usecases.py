"""End-to-end multi-part engineering assembly verification suite (UC-1 to UC-4).

All multi-part assemblies are generated programmatically in `tmp_path` so no
binary solver blobs are committed to Git, and both Windows (WSL CalculiX +
native OpenCourant) and Linux CI execute real solver physics.
"""
from __future__ import annotations

import csv
import functools
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from caelab.connections import PROGRAMS, ProgramStatus
from caelab.mdo.constraints import ConstraintSpec
from caelab.mdo.doe import DOEConfig
from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.variables import ContinuousVariable, VariableSpace
from caelab.materials import (
    CalibrationStudyService,
    ComparisonSpec,
    ExperimentChannelSpec,
    ObservableSpec,
    QuantityUnitSpec,
)
from caelab.native.adapter import CalculiXAdapter
from caelab.native.results import collect_results
from caelab.native.run_store import NativeRun, RunStore, utc_now
from caelab.workbench import Workbench


# ---------------------------------------------------------------------------
# Cross-platform solver discovery & execution helpers
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _native_ccx_path() -> str | None:
    env_val = os.environ.get("CAELAB_CCX", "").strip()
    if env_val and Path(env_val).is_file():
        return str(Path(env_val).resolve())
    found = shutil.which("ccx") or shutil.which("ccx.exe")
    return str(Path(found).resolve()) if found else None


@functools.lru_cache(maxsize=1)
def _has_wsl_ccx() -> bool:
    if os.name != "nt" or shutil.which("wsl") is None:
        return False
    try:
        res = subprocess.run(
            ["wsl", "bash", "-lc", "command -v ccx"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return res.returncode == 0 and bool(res.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def _ccx_available() -> bool:
    return _native_ccx_path() is not None or _has_wsl_ccx()


def _opencourant_home() -> Path | None:
    env_val = os.environ.get("CAELAB_OPENCOURANT", "").strip()
    if env_val:
        p = Path(env_val).expanduser()
        if p.exists():
            return p.resolve()
    return None


@pytest.fixture(scope="module", autouse=True)
def _restore_global_qt_and_matplotlib_state():
    """Preserve global QApplication style/font and matplotlib rcParams across modules."""
    try:
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QApplication, QStyleFactory
    except Exception:
        QApplication = None
        QFont = None
        QStyleFactory = None
    try:
        import matplotlib as mpl
    except Exception:
        mpl = None

    app = QApplication.instance() if QApplication is not None else None
    if app is None and QApplication is not None:
        app = QApplication(["pytest", "-platform", "offscreen"])
    prev_style = app.style().objectName() if app is not None and app.style() is not None else ""
    prev_font = QFont(app.font()) if app is not None and QFont is not None else None

    mpl_keys = ("font.family", "font.sans-serif", "mathtext.fontset", "axes.unicode_minus")
    prev_rc = {}
    if mpl is not None:
        for k in mpl_keys:
            if k in mpl.rcParams:
                val = mpl.rcParams[k]
                prev_rc[k] = list(val) if isinstance(val, list) else val

    yield

    if QApplication is not None:
        app_now = QApplication.instance()
        if app_now is not None:
            if prev_style and QStyleFactory is not None:
                restored = QStyleFactory.create(prev_style)
                if restored is not None:
                    app_now.setStyle(restored)
            if prev_font is not None:
                app_now.setFont(prev_font)
    if mpl is not None and prev_rc:
        mpl.rcParams.update(prev_rc)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_wsl_ccx(workdir: Path, stem: str = "model", timeout: float = 120.0) -> subprocess.CompletedProcess:
    wp = subprocess.check_output(["wsl", "wslpath", "-a", workdir.resolve().as_posix()], text=True).strip()
    return subprocess.run(
        ["wsl", "bash", "-lc", f"cd {wp!r} && OMP_NUM_THREADS=4 ccx -i {stem}"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _execute_calculix_run(wb: Workbench, project, node_id: str, timeout: float = 120.0) -> NativeRun:
    """Execute CalculiX natively via Workbench or via WSL ccx + RunStore receipt on Windows."""
    if _native_ccx_path() is not None:
        submitted = wb.submit_native(project, node_id, timeout=timeout)
        deadline = time.monotonic() + timeout + 10.0
        while time.monotonic() < deadline:
            run = next(r for r in wb.native_runs(project, node_id) if r.id == submitted.id)
            if run.finished_at:
                return run
            time.sleep(0.05)
        raise TimeoutError("Native CalculiX run did not terminate in time")

    if not _has_wsl_ccx():
        pytest.skip("CalculiX (native ccx or WSL ccx) is not installed on this machine")

    reference = wb.input_file(project, node_id)
    assert reference is not None
    store = wb._run_store(project)
    run_id = uuid.uuid4().hex
    workdir = store.root / run_id
    workdir.mkdir(parents=True, exist_ok=True)
    snapshot = CalculiXAdapter().snapshot_input(reference.path, workdir)
    proc = _run_wsl_ccx(workdir, "model", timeout=timeout)
    log_path = workdir / "job.log"
    log_path.write_text((proc.stdout or "") + "\n" + (proc.stderr or ""), encoding="utf-8")
    result = collect_results(workdir) if proc.returncode == 0 else {"status": "failed", "frames": [], "warnings": []}
    outputs = {}
    for name in ("model.frd", "model.dat", "model.sta"):
        p = workdir / name
        if p.is_file():
            outputs[name] = _sha256(p)
    snapshot["outputs"] = outputs
    snapshot["artifacts"] = {rel: _sha256(workdir / rel) for rel in result.get("exchange_files", []) if (workdir / rel).is_file()}
    run = NativeRun(
        id=run_id,
        node_id=node_id,
        workdir=workdir,
        solver_id="calculix",
        state="completed" if proc.returncode == 0 else "failed",
        parse_state=result.get("status", "failed"),
        input_path=reference.path,
        input_revision=reference.revision,
        units=dict(reference.units),
        unit_source=reference.unit_source,
        finished_at=utc_now(),
        returncode=proc.returncode,
        error="" if proc.returncode == 0 else f"WSL ccx exited with {proc.returncode}",
        log_path=str(log_path),
        result=result,
        snapshot=snapshot,
    )
    store.write(run)
    wb.select_native_run(project, node_id, run.id)
    return wb.selected_native_run(project, node_id)


def _wait_native_run(wb: Workbench, project, run_id: str, timeout: float = 120.0) -> NativeRun:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = next(item for item in wb.native_runs(project) if item.id == run_id)
        if run.finished_at:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run {run_id} did not terminate within {timeout}s")


# ---------------------------------------------------------------------------
# Programmatic Multi-Part Assembly Builders
# ---------------------------------------------------------------------------

def build_bolted_flange_assembly(out_dir: Path, *, mode: str = "nonlinear_contact") -> dict:
    """Build a 6-part 3D solid C3D8 bolted flange-gasket-gusset-pin assembly.

    Parts (3,184 C3D8 elements total):
      1. PART_1_BASE_FLANGE       (Steel, 120x80x10 mm, 2 bolt holes, BASE_MOUNT at |x|>=50 mm)
      2. PART_2_ELASTOMER_GASKET  (Elastomer E=45 MPa, nu=0.45, 120x80x3 mm)
      3. PART_3_GUSSET_BRACKET    (Al-6061 base + upright + diagonal gusset web + pin clevis)
      4. PART_4_BOLT_M8_L         (High-strength 10.9 M8 bolt at x=-35 mm with *PRE-TENSION SECTION)
      5. PART_5_BOLT_M8_R         (High-strength 10.9 M8 bolt at x=+35 mm with *PRE-TENSION SECTION)
      6. PART_6_LOAD_PIN          (Steel loading pin in clevis bore receiving oblique load)
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    parts_dir = out_dir / "parts"
    parts_dir.mkdir(exist_ok=True)

    nid = 1
    eid = 1

    def new_nodes_grid(xs, ys, zs, skip_cell_fn=None):
        nonlocal nid
        active_cells = []
        nx, ny, nz = len(xs) - 1, len(ys) - 1, len(zs) - 1
        needed = set()
        for k in range(nz):
            for j in range(ny):
                for i in range(nx):
                    if skip_cell_fn and skip_cell_fn(i, j, k, xs, ys, zs):
                        continue
                    active_cells.append((i, j, k))
                    for dk in (0, 1):
                        for dj in (0, 1):
                            for di in (0, 1):
                                needed.add((i + di, j + dj, k + dk))
        nmap = {}
        nodes = []
        for idx in sorted(needed):
            i, j, k = idx
            nmap[idx] = nid
            nodes.append((nid, xs[i], ys[j], zs[k]))
            nid += 1
        return nmap, nodes, active_cells

    def hex_elems(nmap, active_cells):
        nonlocal eid
        elems = []
        for i, j, k in active_cells:
            conn = (
                nmap[(i, j, k)], nmap[(i + 1, j, k)], nmap[(i + 1, j + 1, k)], nmap[(i, j + 1, k)],
                nmap[(i, j, k + 1)], nmap[(i + 1, j, k + 1)], nmap[(i + 1, j + 1, k + 1)], nmap[(i, j + 1, k + 1)],
            )
            elems.append((eid, conn))
            eid += 1
        return elems

    def write_inc(path: Path, nodes, elsets: dict[str, list], nsets: dict[str, list], extra_lines: list[str] | None = None):
        lines = []
        if nodes:
            lines.append("*NODE")
            for n, x, y, z in nodes:
                lines.append(f"{n}, {x:.6f}, {y:.6f}, {z:.6f}")
        for es_name, elems in elsets.items():
            lines.append(f"*ELEMENT, TYPE=C3D8, ELSET={es_name}")
            for e, conn in elems:
                lines.append(f"{e}, " + ", ".join(str(c) for c in conn))
        for ns_name, nlist in nsets.items():
            if not nlist:
                continue
            lines.append(f"*NSET, NSET={ns_name}")
            for s in range(0, len(nlist), 16):
                lines.append(", ".join(str(v) for v in nlist[s:s + 16]))
        if extra_lines:
            lines.extend(extra_lines)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    xs_plate = [-60.0 + 5.0 * i for i in range(25)]
    ys_plate = [-40.0 + 5.0 * j for j in range(17)]

    def bolt_hole_skip(i, j, k, xs, ys, zs):
        xc = 0.5 * (xs[i] + xs[i + 1])
        yc = 0.5 * (ys[j] + ys[j + 1])
        return ((-40.0 < xc < -30.0) or (30.0 < xc < 40.0)) and (-5.0 < yc < 5.0)

    # 1. Base Flange
    zs_flange = [0.0, 10.0 / 3.0, 20.0 / 3.0, 10.0]
    nmap1, nodes1, cells1 = new_nodes_grid(xs_plate, ys_plate, zs_flange, bolt_hole_skip)
    elems1 = hex_elems(nmap1, cells1)
    base_mount = [nmap1[(i, j, 0)] for (i, j, k) in nmap1 if k == 0 and abs(xs_plate[i]) >= 50.0]
    flange_top_s2 = [elems1[idx][0] for idx, (i, j, k) in enumerate(cells1) if k == 2]
    flange_bot_nut_s1 = [
        elems1[idx][0]
        for idx, (i, j, k) in enumerate(cells1)
        if k == 0 and (
            (-45.0 <= 0.5 * (xs_plate[i] + xs_plate[i + 1]) <= -25.0 or 25.0 <= 0.5 * (xs_plate[i] + xs_plate[i + 1]) <= 45.0)
            and -10.0 <= 0.5 * (ys_plate[j] + ys_plate[j + 1]) <= 10.0
        )
    ]
    write_inc(
        parts_dir / "base_flange.inc",
        nodes1,
        {"PART_1_BASE_FLANGE": elems1},
        {"BASE_MOUNT": sorted(set(base_mount))},
        [
            "*SURFACE, NAME=SURF_FLANGE_TOP, TYPE=ELEMENT",
            *[f"{e}, S2" for e in flange_top_s2],
            "*SURFACE, NAME=SURF_FLANGE_NUT_BOT, TYPE=ELEMENT",
            *[f"{e}, S1" for e in flange_bot_nut_s1],
        ],
    )

    # 2. Elastomer Gasket
    zs_gasket = [10.0, 11.5, 13.0]
    nmap2, nodes2, cells2 = new_nodes_grid(xs_plate, ys_plate, zs_gasket, bolt_hole_skip)
    elems2 = hex_elems(nmap2, cells2)
    gasket_bot_s1 = [elems2[idx][0] for idx, (i, j, k) in enumerate(cells2) if k == 0]
    gasket_top_s2 = [elems2[idx][0] for idx, (i, j, k) in enumerate(cells2) if k == 1]
    write_inc(
        parts_dir / "elastomer_gasket.inc",
        nodes2,
        {"PART_2_ELASTOMER_GASKET": elems2},
        {},
        [
            "*SURFACE, NAME=SURF_GASKET_BOT, TYPE=ELEMENT",
            *[f"{e}, S1" for e in gasket_bot_s1],
            "*SURFACE, NAME=SURF_GASKET_TOP, TYPE=ELEMENT",
            *[f"{e}, S2" for e in gasket_top_s2],
        ],
    )

    # 3. Gusset Bracket (base + upright + diagonal gusset + upper pin clevis)
    zs_bbase = [13.0, 18.0, 23.0]
    nmap3b, nodes3b, cells3b = new_nodes_grid(xs_plate, ys_plate, zs_bbase, bolt_hole_skip)
    elems3b = hex_elems(nmap3b, cells3b)
    bracket_bot_s1 = [elems3b[idx][0] for idx, (i, j, k) in enumerate(cells3b) if k == 0]
    bracket_head_s2 = [
        elems3b[idx][0]
        for idx, (i, j, k) in enumerate(cells3b)
        if k == 1 and (
            (-45.0 <= 0.5 * (xs_plate[i] + xs_plate[i + 1]) <= -25.0 or 25.0 <= 0.5 * (xs_plate[i] + xs_plate[i + 1]) <= 45.0)
            and -10.0 <= 0.5 * (ys_plate[j] + ys_plate[j + 1]) <= 10.0
        )
    ]

    xs_up_idx = list(range(8, 17))
    ys_up_idx = list(range(11, 14))
    zs_up = [23.0 + 5.0 * k for k in range(11)]
    nmap3u, nodes3u = {}, []
    for k, z in enumerate(zs_up):
        for j_local, j_plate in enumerate(ys_up_idx):
            for i_local, i_plate in enumerate(xs_up_idx):
                if k == 0:
                    nmap3u[(i_local, j_local, k)] = nmap3b[(i_plate, j_plate, 2)]
                else:
                    nmap3u[(i_local, j_local, k)] = nid
                    nodes3u.append((nid, xs_plate[i_plate], ys_plate[j_plate], z))
                    nid += 1
    elems3u = hex_elems(nmap3u, [(i, j, k) for k in range(10) for j in range(2) for i in range(8)])

    nmap3g, nodes3g = {}, []
    for k in range(10):
        z = 23.0 + 5.0 * k
        y_front = -25.0 + 35.0 * (k / 9.0)
        for j in range(5):
            y = y_front + (15.0 - y_front) * (j / 4.0)
            for i_local, i_plate in enumerate((11, 12, 13)):
                if k == 0:
                    nmap3g[(i_local, j, k)] = nmap3b[(i_plate, 3 + 2 * j, 2)]
                elif j == 4:
                    nmap3g[(i_local, j, k)] = nmap3u[(i_plate - 8, 0, k)]
                else:
                    nmap3g[(i_local, j, k)] = nid
                    nodes3g.append((nid, xs_plate[i_plate], y, z))
                    nid += 1
    elems3g = hex_elems(nmap3g, [(i, j, k) for k in range(9) for j in range(4) for i in range(2)])

    xs_h = [xs_plate[idx] for idx in xs_up_idx]
    ys_h = [5.0, 10.0, 15.0, 20.0, 25.0]
    zs_h = [73.0, 78.0, 83.0, 88.0, 93.0]
    nmap3h, nodes3h, needed_h, cells3h = {}, [], set(), []
    for k in range(4):
        for j in range(4):
            for i in range(8):
                if (j in (1, 2)) and (k in (1, 2)):
                    continue
                cells3h.append((i, j, k))
                for dk in (0, 1):
                    for dj in (0, 1):
                        for di in (0, 1):
                            needed_h.add((i + di, j + dj, k + dk))
    for i, j, k in sorted(needed_h):
        if k == 0 and j in (2, 3, 4):
            nmap3h[(i, j, k)] = nmap3u[(i, j - 2, 10)]
        else:
            nmap3h[(i, j, k)] = nid
            nodes3h.append((nid, xs_h[i], ys_h[j], zs_h[k]))
            nid += 1
    elems3h = hex_elems(nmap3h, cells3h)

    bore_faces = []
    for eid_h, (i, j, k) in zip([e[0] for e in elems3h], cells3h):
        if k == 0 and j in (1, 2):
            bore_faces.append(f"{eid_h}, S2")
        if k == 3 and j in (1, 2):
            bore_faces.append(f"{eid_h}, S1")
        if j == 0 and k in (1, 2):
            bore_faces.append(f"{eid_h}, S5")
        if j == 3 and k in (1, 2):
            bore_faces.append(f"{eid_h}, S3")

    write_inc(
        parts_dir / "gusset_bracket.inc",
        nodes3b + nodes3u + nodes3g + nodes3h,
        {"PART_3_GUSSET_BRACKET": elems3b + elems3u + elems3g + elems3h},
        {},
        [
            "*SURFACE, NAME=SURF_BRACKET_BOT, TYPE=ELEMENT",
            *[f"{e}, S1" for e in bracket_bot_s1],
            "*SURFACE, NAME=SURF_BRACKET_HEAD, TYPE=ELEMENT",
            *[f"{e}, S2" for e in bracket_head_s2],
            "*SURFACE, NAME=SURF_HOUSING_BORE, TYPE=ELEMENT",
            *bore_faces,
        ],
    )

    # 4 & 5. Preloaded M8 Bolts
    bolt_nodes, bolt_elsets, bolt_extra, pret_nodes = [], {}, [], []
    for label, xc, part_name in (("L", -35.0, "PART_4_BOLT_M8_L"), ("R", 35.0, "PART_5_BOLT_M8_R")):
        xs_b = [xc - 9.0, xc - 4.0, xc, xc + 4.0, xc + 9.0]
        ys_b = [-9.0, -4.0, 0.0, 4.0, 9.0]
        zs_b = [-6.0, -3.0, 0.0, 11.5 / 3.0, 23.0 / 3.0, 11.5, 11.5 + 11.5 / 3.0, 11.5 + 23.0 / 3.0, 23.0, 26.0, 29.0]
        nmap_b, nodes_b, cells_b = new_nodes_grid(
            xs_b, ys_b, zs_b,
            lambda i, j, k, *_: (2 <= k <= 7) and not (i in (1, 2) and j in (1, 2)),
        )
        elems_b = hex_elems(nmap_b, cells_b)
        bolt_nodes.extend(nodes_b)
        bolt_elsets[part_name] = elems_b
        ref_nid = nid
        nid += 1
        bolt_nodes.append((ref_nid, xc, 0.0, 11.5))
        pret_nodes.append((label, ref_nid))
        nut_top = [elems_b[idx][0] for idx, (i, j, k) in enumerate(cells_b) if k == 1 and not (i in (1, 2) and j in (1, 2))]
        head_bot = [elems_b[idx][0] for idx, (i, j, k) in enumerate(cells_b) if k == 8 and not (i in (1, 2) and j in (1, 2))]
        pret_s1 = [elems_b[idx][0] for idx, (i, j, k) in enumerate(cells_b) if k == 5]
        extra = [
            f"*SURFACE, NAME=SURF_NUT_TOP_{label}, TYPE=ELEMENT",
            *[f"{e}, S2" for e in nut_top],
            f"*SURFACE, NAME=SURF_HEAD_BOT_{label}, TYPE=ELEMENT",
            *[f"{e}, S1" for e in head_bot],
        ]
        if mode == "nonlinear_contact":
            extra.extend([
                f"*SURFACE, NAME=PRET_SURF_{label}, TYPE=ELEMENT",
                *[f"{e}, S1" for e in pret_s1],
                f"*PRE-TENSION SECTION, SURFACE=PRET_SURF_{label}, NODE={ref_nid}",
                "0., 0., -1.",
            ])
        bolt_extra.extend(extra)
    write_inc(parts_dir / "bolts_m8.inc", bolt_nodes, bolt_elsets, {}, bolt_extra)

    # 6. Load Pin
    xs_pin = [-30.0 + 5.0 * i for i in range(13)]
    ys_pin = [10.0, 15.0, 20.0]
    zs_pin = [78.0, 83.0, 88.0]
    nmap6, nodes6, cells6 = new_nodes_grid(xs_pin, ys_pin, zs_pin)
    elems6 = hex_elems(nmap6, cells6)
    pin_load_nodes = [nmap6[(i, j, k)] for (i, j, k) in nmap6 if i in (0, 12)]
    pin_outer_faces = []
    for eid_p, (i, j, k) in zip([e[0] for e in elems6], cells6):
        if 2 <= i <= 9:
            if k == 0:
                pin_outer_faces.append(f"{eid_p}, S1")
            if k == 1:
                pin_outer_faces.append(f"{eid_p}, S2")
            if j == 0:
                pin_outer_faces.append(f"{eid_p}, S3")
            if j == 1:
                pin_outer_faces.append(f"{eid_p}, S5")
    write_inc(
        parts_dir / "load_pin.inc",
        nodes6,
        {"PART_6_LOAD_PIN": elems6},
        {"PIN_LOAD": sorted(set(pin_load_nodes))},
        ["*SURFACE, NAME=SURF_PIN_CONTACT, TYPE=ELEMENT", *pin_outer_faces],
    )

    materials_inc = out_dir / "materials.inc"
    materials_inc.write_text(
        """*MATERIAL, NAME=MAT_STEEL
*ELASTIC
210000.0, 0.30
*DENSITY
7.85e-9
*MATERIAL, NAME=MAT_ELASTOMER
*ELASTIC
45.0, 0.45
*DENSITY
1.20e-9
*MATERIAL, NAME=MAT_AL6061
*ELASTIC
69000.0, 0.33
*DENSITY
2.70e-9, 293.15
*MATERIAL, NAME=MAT_BOLT_109
*ELASTIC
210000.0, 0.30
*DENSITY
7.85e-9
*SOLID SECTION, ELSET=PART_1_BASE_FLANGE, MATERIAL=MAT_STEEL
*SOLID SECTION, ELSET=PART_2_ELASTOMER_GASKET, MATERIAL=MAT_ELASTOMER
*SOLID SECTION, ELSET=PART_3_GUSSET_BRACKET, MATERIAL=MAT_AL6061
*SOLID SECTION, ELSET=PART_4_BOLT_M8_L, MATERIAL=MAT_BOLT_109
*SOLID SECTION, ELSET=PART_5_BOLT_M8_R, MATERIAL=MAT_BOLT_109
*SOLID SECTION, ELSET=PART_6_LOAD_PIN, MATERIAL=MAT_STEEL
""",
        encoding="utf-8",
    )

    n_pin = len(pin_load_nodes)
    fx_per = 1200.0 / n_pin
    fy_per = -2800.0 / n_pin
    fz_per = 900.0 / n_pin

    main_inp = out_dir / "bolted_assembly.inp"
    if mode == "nonlinear_contact":
        main_inp.write_text(
            f"""*HEADING
6-Part Bolted Flange-Gusset Bracket-Elastomer Gasket Assembly (mm, N, MPa)
*INCLUDE, INPUT=parts/base_flange.inc
*INCLUDE, INPUT=parts/elastomer_gasket.inc
*INCLUDE, INPUT=parts/gusset_bracket.inc
*INCLUDE, INPUT=parts/bolts_m8.inc
*INCLUDE, INPUT=parts/load_pin.inc
*INCLUDE, INPUT=materials.inc
**
*TIE, NAME=TIE_FLANGE_GASKET
SURF_GASKET_BOT, SURF_FLANGE_TOP
*TIE, NAME=TIE_PIN_HOUSING
SURF_PIN_CONTACT, SURF_HOUSING_BORE
*TIE, NAME=TIE_NUT_L
SURF_NUT_TOP_L, SURF_FLANGE_NUT_BOT
*TIE, NAME=TIE_NUT_R
SURF_NUT_TOP_R, SURF_FLANGE_NUT_BOT
*TIE, NAME=TIE_HEAD_L
SURF_HEAD_BOT_L, SURF_BRACKET_HEAD
*TIE, NAME=TIE_HEAD_R
SURF_HEAD_BOT_R, SURF_BRACKET_HEAD
*SURFACE INTERACTION, NAME=INT_GASKET_FRIC
*SURFACE BEHAVIOR, PRESSURE-OVERCLOSURE=LINEAR
500.0
*FRICTION
0.25, 100.0
*CONTACT PAIR, INTERACTION=INT_GASKET_FRIC, TYPE=SURFACE TO SURFACE
SURF_GASKET_TOP, SURF_BRACKET_BOT
**
*BOUNDARY
BASE_MOUNT, 1, 3, 0.0
**
*STEP, NLGEOM
*STATIC
1.0, 1.0, 1e-4, 1.0
*CLOAD
{pret_nodes[0][1]}, 1, 6000.0
{pret_nodes[1][1]}, 1, 6000.0
*NODE FILE
U
*EL FILE
S
*NODE PRINT, NSET=BASE_MOUNT, TOTALS=ONLY
RF
*END STEP
**
*STEP, NLGEOM
*STATIC
1.0, 1.0, 1e-4, 1.0
*BOUNDARY, FIXED
{pret_nodes[0][1]}, 1, 1
{pret_nodes[1][1]}, 1, 1
*CLOAD
PIN_LOAD, 1, {fx_per:.8f}
PIN_LOAD, 2, {fy_per:.8f}
PIN_LOAD, 3, {fz_per:.8f}
*NODE FILE
U
*EL FILE
S
*NODE PRINT, NSET=BASE_MOUNT, TOTALS=ONLY
RF
*NODE PRINT, NSET=PIN_LOAD, TOTALS=ONLY
RF
*END STEP
""",
            encoding="utf-8",
        )
    else:
        main_inp.write_text(
            f"""*HEADING
6-Part Bolted Flange-Gusset Bracket-Elastomer Gasket MDO Assembly (mm, N, MPa)
*INCLUDE, INPUT=parts/base_flange.inc
*INCLUDE, INPUT=parts/elastomer_gasket.inc
*INCLUDE, INPUT=parts/gusset_bracket.inc
*INCLUDE, INPUT=parts/bolts_m8.inc
*INCLUDE, INPUT=parts/load_pin.inc
*INCLUDE, INPUT=materials.inc
*ELSET, ELSET=EALL
PART_1_BASE_FLANGE, PART_2_ELASTOMER_GASKET, PART_3_GUSSET_BRACKET, PART_4_BOLT_M8_L, PART_5_BOLT_M8_R, PART_6_LOAD_PIN
**
*TIE, NAME=TIE_FLANGE_GASKET
SURF_GASKET_BOT, SURF_FLANGE_TOP
*TIE, NAME=TIE_GASKET_BRACKET
SURF_GASKET_TOP, SURF_BRACKET_BOT
*TIE, NAME=TIE_PIN_HOUSING
SURF_PIN_CONTACT, SURF_HOUSING_BORE
*TIE, NAME=TIE_NUT_L
SURF_NUT_TOP_L, SURF_FLANGE_NUT_BOT
*TIE, NAME=TIE_NUT_R
SURF_NUT_TOP_R, SURF_FLANGE_NUT_BOT
*TIE, NAME=TIE_HEAD_L
SURF_HEAD_BOT_L, SURF_BRACKET_HEAD
*TIE, NAME=TIE_HEAD_R
SURF_HEAD_BOT_R, SURF_BRACKET_HEAD
*BOUNDARY
BASE_MOUNT, 1, 3, 0.0
*STEP
*STATIC
*CLOAD
PIN_LOAD, 1, {fx_per:.8f}
PIN_LOAD, 2, {fy_per:.8f}
PIN_LOAD, 3, {fz_per:.8f}
*NODE FILE
U
*EL FILE
S
*NODE PRINT, NSET=BASE_MOUNT, TOTALS=ONLY
RF
*EL PRINT, ELSET=EALL, TOTALS=ONLY
EMAS
*END STEP
""",
            encoding="utf-8",
        )

    total_elems = (
        len(elems1)
        + len(elems2)
        + len(elems3b)
        + len(elems3u)
        + len(elems3g)
        + len(elems3h)
        + sum(len(v) for v in bolt_elsets.values())
        + len(elems6)
    )
    gasket_top_nodes = sorted({nmap2[(i, j, 2)] for (i, j, k) in nmap2 if k == 2})
    part_node_sets = {
        "PART_1_BASE_FLANGE": sorted(n[0] for n in nodes1),
        "PART_2_ELASTOMER_GASKET": sorted(n[0] for n in nodes2),
        "PART_3_GUSSET_BRACKET": sorted(n[0] for n in (nodes3b + nodes3u + nodes3g + nodes3h)),
        "PART_4_BOLT_M8_L": sorted(n[0] for n in bolt_nodes if n[1] < 0 and n[0] != pret_nodes[0][1]),
        "PART_5_BOLT_M8_R": sorted(n[0] for n in bolt_nodes if n[1] > 0 and n[0] != pret_nodes[1][1]),
        "PART_6_LOAD_PIN": sorted(n[0] for n in nodes6),
    }
    return {
        "main_inp": main_inp,
        "materials_inc": materials_inc,
        "total_elements": total_elems,
        "gasket_top_nodes": gasket_top_nodes,
        "part_node_sets": part_node_sets,
        "pin_load_nodes": pin_load_nodes,
        "fy_per_node": fy_per,
    }


def _i10(v: int) -> str:
    return f"{int(v):10d}"


def _r20(v: float) -> str:
    s = f"{float(v):.8g}"
    if len(s) > 20:
        s = f"{float(v):.10E}"
    return s.rjust(20)


def build_crash_assembly(out_dir: Path, *, end_time: float = 0.0025, anim_dt: float = 0.0005) -> dict:
    """Build a 5-part explicit crash assembly for OpenCourant & CAELab Prep (3,456 elements)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    inc = out_dir / "includes"
    inc.mkdir(exist_ok=True)

    nid = 1
    eid = 1
    all_nodes: list[tuple[int, float, float, float]] = []

    def add_node(x: float, y: float, z: float) -> int:
        nonlocal nid
        cur = nid
        nid += 1
        all_nodes.append((cur, x, y, z))
        return cur

    # 1. /PART/1 Closed-Box Hat Bumper Beam (QUAD4)
    nx_b = 48
    xs_b = [-240.0 + 10.0 * i for i in range(nx_b + 1)]
    prof = [
        (0.0, -45.0),
        (0.0, -28.0),
        (42.0, -24.0),
        (42.0, -12.0),
        (42.0, 0.0),
        (42.0, 12.0),
        (42.0, 24.0),
        (0.0, 28.0),
        (0.0, 45.0),
    ]
    b_nodes_list, b_grid, b_back_grid = [], {}, {}
    for i, x in enumerate(xs_b):
        bow = 15.0 * (1.0 - (x / 240.0) ** 2)
        for j, (dy, z) in enumerate(prof):
            y = dy + (bow if dy > 0 else 0.0)
            n = add_node(x, y, z)
            b_grid[(i, j)] = n
            b_nodes_list.append(n)
        b_back_grid[(i, 0)] = b_grid[(i, 1)]
        for k_back, z_back in ((1, -14.0), (2, 0.0), (3, 14.0)):
            nb = add_node(x, 0.0, z_back)
            b_back_grid[(i, k_back)] = nb
            b_nodes_list.append(nb)
        b_back_grid[(i, 4)] = b_grid[(i, 7)]
    bumper_elems = []
    for i in range(nx_b):
        for j in range(len(prof) - 1):
            bumper_elems.append((eid, 1, (b_grid[(i, j)], b_grid[(i + 1, j)], b_grid[(i + 1, j + 1)], b_grid[(i, j + 1)])))
            eid += 1
        for k_back in range(4):
            bumper_elems.append((eid, 1, (b_back_grid[(i, k_back)], b_back_grid[(i + 1, k_back)], b_back_grid[(i + 1, k_back + 1)], b_back_grid[(i, k_back + 1)])))
            eid += 1

    # 2 & 3. /PART/2 & /PART/3 Triggered Crash Boxes (QUAD4)
    cb_nodes = {2: [], 3: []}
    cb_elems = {2: [], 3: []}
    rear_mount_nodes, cb_front_nodes = [], []

    def tube_ring_coords(xc: float, y: float):
        pts, side = [], 6
        trig = 1.4 * math.sin(3.0 * math.pi * ((y + 108.0) / 96.0)) if -108.0 <= y <= -12.0 else 0.0
        for k in range(side):
            pts.append((xc - 25.0 + 50.0 * k / side, -25.0 - trig))
        for k in range(side):
            pts.append((25.0 + xc + trig, -25.0 + 50.0 * k / side))
        for k in range(side):
            pts.append((xc + 25.0 - 50.0 * k / side, 25.0 + trig))
        for k in range(side):
            pts.append((-25.0 + xc - trig, 25.0 - 50.0 * k / side))
        return pts

    ny_cb = 30
    ys_cb = [-180.0 + 6.0 * k for k in range(ny_cb + 1)]
    for part_id, xc in ((2, -140.0), (3, 140.0)):
        rings = []
        for ky, y in enumerate(ys_cb):
            ring = []
            for x, z in tube_ring_coords(xc, y):
                n = add_node(x, y, z)
                ring.append(n)
                cb_nodes[part_id].append(n)
                if ky == 0:
                    rear_mount_nodes.append(n)
                elif ky == ny_cb:
                    cb_front_nodes.append(n)
            rings.append(ring)
        nperim = len(rings[0])
        for ky in range(ny_cb):
            r0, r1 = rings[ky], rings[ky + 1]
            for p in range(nperim):
                p2 = (p + 1) % nperim
                cb_elems[part_id].append((eid, part_id, (r0[p], r0[p2], r1[p2], r1[p])))
                eid += 1

    # 4. /PART/4 3D Solid Foam Core Insert (BRICK8)
    nx_f, ny_f, nz_f = 44, 4, 6
    xs_f = [-220.0 + 10.0 * i for i in range(nx_f + 1)]
    zs_f = [-21.0 + 7.0 * k for k in range(nz_f + 1)]
    f_grid, foam_nodes_list = {}, []
    for i, x in enumerate(xs_f):
        bow = 15.0 * (1.0 - (x / 240.0) ** 2)
        y_back, y_front = 2.6, 39.2 + bow
        for j in range(ny_f + 1):
            y = y_back + (y_front - y_back) * (j / ny_f)
            for k, z in enumerate(zs_f):
                n = add_node(x, y, z)
                f_grid[(i, j, k)] = n
                foam_nodes_list.append(n)
    foam_elems = []
    for i in range(nx_f):
        for j in range(ny_f):
            for k in range(nz_f):
                conn = (
                    f_grid[(i, j, k)], f_grid[(i + 1, j, k)], f_grid[(i + 1, j + 1, k)], f_grid[(i, j + 1, k)],
                    f_grid[(i, j, k + 1)], f_grid[(i + 1, j, k + 1)], f_grid[(i + 1, j + 1, k + 1)], f_grid[(i, j + 1, k + 1)],
                )
                foam_elems.append((eid, 4, conn))
                eid += 1

    # 5. /PART/5 Cylindrical Pole Impactor (QUAD4)
    nt_p, nz_p = 24, 16
    r_pole, yc_pole = 55.0, 57.0 + 55.0 + 4.5
    zs_p = [-80.0 + 10.0 * k for k in range(nz_p + 1)]
    p_rings, pole_nodes = [], []
    for z in zs_p:
        ring = []
        for t in range(nt_p):
            ang = 2.0 * math.pi * t / nt_p
            n = add_node(r_pole * math.cos(ang), yc_pole + r_pole * math.sin(ang), z)
            ring.append(n)
            pole_nodes.append(n)
        p_rings.append(ring)
    pole_elems = []
    for k in range(nz_p):
        r0, r1 = p_rings[k], p_rings[k + 1]
        for t in range(nt_p):
            t2 = (t + 1) % nt_p
            pole_elems.append((eid, 5, (r0[t], r0[t2], r1[t2], r1[t])))
            eid += 1

    n_dict = {n: (n, x, y, z) for n, x, y, z in all_nodes}
    fmt_nodes = lambda nids: [f"{n_dict[n][0]:10d}{n_dict[n][1]:20.6f}{n_dict[n][2]:20.6f}{n_dict[n][3]:20.6f}" for n in nids]
    fmt_shells = lambda pid, elist: [f"/SHELL/{pid}", *[f"{e:10d}" + "".join(f"{c:10d}" for c in conn) for e, _, conn in elist]]
    fmt_bricks = lambda pid, elist: [f"/BRICK/{pid}", *[f"{e:10d}" + "".join(f"{c:10d}" for c in conn) for e, _, conn in elist]]
    grnod = lambda gid, name, nids: "\n".join([f"/GRNOD/NODE/{gid}", name, *["".join(_i10(n) for n in nids[i:i + 10]) for i in range(0, len(nids), 10)], ""])
    surf_part = lambda sid, name, pids: "\n".join([f"/SURF/PART/{sid}", name, *["".join(_i10(p) for p in pids[i:i + 10]) for i in range(0, len(pids), 10)], ""])

    def inter_type7(iid: int, name: str, gid: int, sid: int, fric: float = 0.2, gapmin: float = 0.8, istf: int = 4) -> str:
        return "\n".join([
            f"/INTER/TYPE7/{iid}",
            name,
            _i10(gid) + _i10(sid) + _i10(istf) + _i10(0) + _i10(3) + " " * 10 + _i10(0) + _i10(1000) + _i10(0) + _i10(0),
            _r20(1.0) + _r20(0.0) * 2 + " " * 20 + _i10(0),
            _r20(0.0) + _r20(1e30) + _r20(0.4) + _r20(0.0) + _i10(1) + _i10(3),
            _r20(1.0) + _r20(fric) + _r20(gapmin) + _r20(0.0) + _r20(1e30),
            "       000" + " " * 20 + _i10(5) + _r20(0.05) + _r20(1.0) + _r20(0.2),
            _i10(0) + _i10(0) + _r20(0.0) + _i10(2) + _i10(0) + _i10(0) + _r20(1.0) + _i10(0),
            "",
        ])

    def mat_law2(mid: int, name: str, rho: float, E: float, nu: float, a: float, b: float, n: float) -> str:
        return "\n".join([
            f"/MAT/LAW2/{mid}",
            name,
            _r20(rho),
            _r20(E) + _r20(nu),
            _r20(a) + _r20(b) + _r20(n) + _r20(1e30) + _r20(1e30),
            _r20(0.0) + _r20(0.0) + _i10(1) + _i10(0) + _r20(1e30) + _r20(0.0),
            "",
        ])

    def prop_shell(pid: int, name: str, thick: float) -> str:
        return "\n".join([
            f"/PROP/SHELL/{pid}",
            name,
            _i10(24) + _i10(4) + _i10(2) + _i10(2) + " " * 20 + _r20(1.0) + " " * 20,
            _r20(0.0) * 4 + _r20(0.015),
            _i10(5) + " " * 10 + _r20(thick) + _r20(5.0 / 6.0) + " " * 10 + _i10(2) + _i10(0),
            "",
        ])

    def prop_solid(pid: int, name: str) -> str:
        return "\n".join([
            f"/PROP/SOLID/{pid}",
            name,
            "".join(_i10(v) for v in (1, 4, 0, 0, 0, 0, 1000, 1)) + _r20(0.0),
            "".join(_r20(v) for v in (1.1, 0.05, 0.1, 0.0, 0.0)),
            _r20(0.0) * 5,
            "",
        ])

    (inc / "bumper_shell.inc").write_text(
        "\n".join(["# Bumper Beam QUAD4", "/NODE", *fmt_nodes(b_nodes_list), *fmt_shells(1, bumper_elems), "#enddata", ""]),
        encoding="utf-8",
    )
    (inc / "crashboxes.inc").write_text(
        "\n".join(["# Triggered Crash Boxes QUAD4", "/NODE", *fmt_nodes(cb_nodes[2] + cb_nodes[3]), *fmt_shells(2, cb_elems[2]), *fmt_shells(3, cb_elems[3]), "#enddata", ""]),
        encoding="utf-8",
    )
    (inc / "foam_core_solid.inc").write_text(
        "\n".join(["# Solid Foam Core BRICK8", "/NODE", *fmt_nodes(foam_nodes_list), *fmt_bricks(4, foam_elems), "#enddata", ""]),
        encoding="utf-8",
    )
    (inc / "barrier_and_bc.inc").write_text(
        "\n".join([
            "# Impactor, BC, Velocity, Contact",
            "/NODE",
            *fmt_nodes(pole_nodes),
            *fmt_shells(5, pole_elems),
            grnod(101, "Rear_Mounting_Nodes", rear_mount_nodes),
            grnod(102, "Impactor_Pole_Nodes", pole_nodes),
            grnod(103, "Foam_Core_Nodes", foam_nodes_list),
            grnod(104, "Crashbox_Front_Weld_Nodes", cb_front_nodes),
            grnod(105, "Crashbox_Buckle_Nodes", [n for n in (cb_nodes[2] + cb_nodes[3]) if n not in set(cb_front_nodes)]),
            surf_part(201, "Crashbox_Shell_Surf", [2, 3]),
            surf_part(202, "Bumper_Surf", [1]),
            "/BCS/301",
            "Rear_Crashbox_Clamp",
            "   111 111" + _i10(0) + _i10(101),
            "/INIVEL/TRA/401",
            "Pole_Impact_Velocity",
            _r20(0.0) + _r20(-10000.0) + _r20(0.0) + _i10(102) + _i10(0),
            inter_type7(501, "Impactor_To_Bumper", 102, 202, fric=0.2, gapmin=0.8, istf=4),
            inter_type7(502, "Foam_To_Bumper_Cavity", 103, 202, fric=0.2, gapmin=0.8, istf=4),
            inter_type7(504, "Crashbox_Self_Contact", 105, 201, fric=0.2, gapmin=0.8, istf=4),
            "/INTER/TYPE2/503",
            "Crashbox_To_Bumper_Spotweld",
            _i10(104) + _i10(202) + _i10(0) + _i10(0) + _i10(0) + _r20(5.0),
            "#enddata",
            "",
        ]),
        encoding="utf-8",
    )

    starter = out_dir / "crash_assembly_0000.rad"
    starter.write_text(
        "\n".join([
            "#RADIOSS STARTER",
            "/BEGIN",
            "crash_assembly",
            "      2024",
            "                  Mg                  mm                   s",
            "                  Mg                  mm                   s",
            "/PART/1",
            "Bumper_Beam_Hat_Section",
            _i10(11) + _i10(21) + _i10(0),
            "/PART/2",
            "Crash_Box_Left_Triggered",
            _i10(12) + _i10(21) + _i10(0),
            "/PART/3",
            "Crash_Box_Right_Triggered",
            _i10(12) + _i10(21) + _i10(0),
            "/PART/4",
            "Energy_Absorbing_Foam_Core",
            _i10(14) + _i10(22) + _i10(0),
            "/PART/5",
            "Rigid_Pole_Impactor",
            _i10(15) + _i10(23) + _i10(0),
            prop_shell(11, "Bumper_Shell_2p5mm", 2.5),
            prop_shell(12, "Crashbox_Shell_1p8mm", 1.8),
            prop_solid(14, "Foam_Core_Solid"),
            prop_shell(15, "Impactor_Shell_5mm", 5.0),
            mat_law2(21, "Al6061_Elastoplastic", 2.7e-9, 69000.0, 0.33, 275.0, 180.0, 0.35),
            mat_law2(22, "Polymer_Foam_Absorber", 4.5e-10, 180.0, 0.20, 8.0, 12.0, 0.50),
            mat_law2(23, "Heavy_Steel_Impactor", 2.5e-7, 210000.0, 0.30, 800.0, 200.0, 0.30),
            "#include includes/bumper_shell.inc",
            "#include includes/crashboxes.inc",
            "#include includes/foam_core_solid.inc",
            "#include includes/barrier_and_bc.inc",
            "/END",
            "",
        ]),
        encoding="utf-8",
    )

    engine = out_dir / "crash_assembly_0001.rad"
    engine.write_text(
        "\n".join([
            "/RUN/crash_assembly/1",
            f"{end_time:.6f}",
            "/PRINT/-50",
            "/ANIM/DT",
            f"0.0 {anim_dt:.6f}",
            "/ANIM/VECT/DISP",
            "/ANIM/VECT/VEL",
            "/ANIM/ELEM/VONM",
            "/TFILE/4",
            "0.00005",
            "/END",
            "",
        ]),
        encoding="utf-8",
    )

    return {
        "starter": starter,
        "engine": engine,
        "node_count": len(all_nodes),
        "element_count": len(bumper_elems) + len(cb_elems[2]) + len(cb_elems[3]) + len(foam_elems) + len(pole_elems),
        "quad4_count": len(bumper_elems) + len(cb_elems[2]) + len(cb_elems[3]) + len(pole_elems),
        "brick8_count": len(foam_elems),
    }


def _read_vtu_points_and_disp(vtu_path: Path):
    vtk = pytest.importorskip("vtkmodules.vtkIOXML")
    from vtkmodules.util.numpy_support import vtk_to_numpy

    reader = vtk.vtkXMLUnstructuredGridReader()
    reader.SetFileName(str(vtu_path))
    reader.Update()
    grid = reader.GetOutput()
    nids = vtk_to_numpy(grid.GetPointData().GetArray("NativeNodeId")).astype(int)
    disp = vtk_to_numpy(grid.GetPointData().GetArray("DISP")).astype(float)
    stress_arr = grid.GetPointData().GetArray("STRESS")
    stress = vtk_to_numpy(stress_arr).astype(float) if stress_arr is not None else None
    return grid.GetNumberOfPoints(), grid.GetNumberOfCells(), nids, disp, stress


# ---------------------------------------------------------------------------
# UC-1: 6-Part Bolted Assembly (CalculiX + Workbench + NativeAnalysisWindow + ExpertWindow)
# ---------------------------------------------------------------------------

def test_uc1_6part_bolted_assembly_calculix_workbench_and_expert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not _ccx_available():
        pytest.skip("CalculiX binary (native or WSL) not available")

    PySide6 = pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from apps.workbench_qt.expert_window import ExpertWindow
    from apps.workbench_qt.native_analysis import NativeAnalysisWindow

    monkeypatch.setenv("CAELAB_UI_SETTINGS_PATH", str(tmp_path / "ui.ini"))
    monkeypatch.setenv("CAELAB_EXPERT_SETTINGS_PATH", str(tmp_path / "expert.ini"))

    info = build_bolted_flange_assembly(tmp_path / "deck", mode="nonlinear_contact")
    assert info["total_elements"] == 3184
    assert len(info["part_node_sets"]) == 6

    wb = Workbench()
    project = wb.new_native_project("6파트 볼트 체결 어셈블리")
    node = wb.add_program_node(project, "analysis", "calculix")
    wb.set_node_file(
        project,
        node.id,
        info["main_inp"],
        units={"mass": "Mg", "length": "mm", "time": "s", "force": "N"},
        unit_source="explicit deck",
    )
    wb.save_native_project(project, tmp_path / "bolted_project.json")

    try:
        run = _execute_calculix_run(wb, project, node.id, timeout=120.0)
        assert run.state == "completed", run.error
        assert run.parse_state == "readable", run.result.get("warnings")
        assert "Job finished" in Path(run.log_path).read_text(encoding="utf-8", errors="replace")

        frames = run.result.get("frames", [])
        assert len(frames) >= 2
        step1_frames = [f for f in frames if f.get("step") == 1]
        step2_frames = [f for f in frames if f.get("step") == 2]
        assert step1_frames and step2_frames

        # Verify Step 1 clamping compression on PART_2_ELASTOMER_GASKET & symmetry
        _, _, nids1, disp1, _ = _read_vtu_points_and_disp(run.workdir / step1_frames[-1]["vtu_file"])
        idx1 = {int(n): i for i, n in enumerate(nids1)}
        gasket_uz = np.array([disp1[idx1[n], 2] for n in info["gasket_top_nodes"]])
        assert float(np.mean(gasket_uz)) < -1e-4, "Step 1 bolt pretension must compress elastomer gasket downward"
        left_bolt_uz = np.mean([disp1[idx1[n], 2] for n in info["part_node_sets"]["PART_4_BOLT_M8_L"]])
        right_bolt_uz = np.mean([disp1[idx1[n], 2] for n in info["part_node_sets"]["PART_5_BOLT_M8_R"]])
        assert abs(left_bolt_uz - right_bolt_uz) < 1e-4

        # Verify Step 2 oblique deflection + stresses across all 6 parts
        n_pts, n_cells, nids2, disp2, stress2 = _read_vtu_points_and_disp(run.workdir / step2_frames[-1]["vtu_file"])
        assert n_cells == 3184
        assert stress2 is not None and np.all(np.isfinite(disp2)) and np.all(np.isfinite(stress2))
        idx2 = {int(n): i for i, n in enumerate(nids2)}
        for pname, nlist in info["part_node_sets"].items():
            part_disp = np.linalg.norm(disp2[[idx2[n] for n in nlist]], axis=1)
            assert float(np.max(part_disp)) > 0.0, f"{pname} must deform under service load"
        pin_disp = np.mean(disp2[[idx2[n] for n in info["pin_load_nodes"]]], axis=0)
        assert pin_disp[0] > 0.0 and pin_disp[1] < 0.0

        # Verify global force equilibrium from .dat
        dat_sets = run.result["dat"]["sets"]
        f_base = np.array(dat_sets["BASE_MOUNT"]["total_force"], dtype=float)
        f_pin = np.array(dat_sets["PIN_LOAD"]["total_force"], dtype=float)
        assert np.allclose(f_pin, [1200.0, -2800.0, 900.0], atol=1.0)
        residual = float(np.linalg.norm(f_base + f_pin))
        assert residual < 5.0, f"Global force equilibrium residual too large: {residual} N"

        # Verify NativeAnalysisWindow + ExpertWindow handoff
        app = QApplication.instance() or QApplication([])
        win = NativeAnalysisWindow(project.path, node.id, wb)
        win.timer.stop()
        try:
            win.resize(1360, 860)
            win.show()
            app.processEvents()
            assert win.field_table.rowCount() >= 2
            assert win.dat_table.rowCount() >= 2
            assert win.frame_selector.count() == len(frames)

            # Poll NativeResultView background VTU decode
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and win.native_view.native_grid is None:
                app.processEvents()
                time.sleep(0.02)
            assert win.native_view.native_grid is not None
            assert win.native_view.native_grid.GetNumberOfCells() == 3184

            # Ctrl+E / ExpertWindow draft handoff
            win.request_expert_debate()
            expert = win._expert_window
            assert isinstance(expert, ExpertWindow)
            draft = expert.question.toPlainText()
            assert "6파트 볼트 체결 어셈블리" in draft
            assert "calculix" in draft
            assert expert._operation is None
        finally:
            win.close()
            win.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
    finally:
        wb.close()


# ---------------------------------------------------------------------------
# UC-2: 5-Part Crash Assembly (CAELab Prep + Real OpenCourant + Workbench)
# ---------------------------------------------------------------------------

def test_uc2_5part_crash_assembly_prep_and_opencourant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    oc_home = _opencourant_home()
    if oc_home is None:
        pytest.skip("OpenCourant installation not found (set CAELAB_OPENCOURANT)")

    monkeypatch.setenv("CAELAB_OPENCOURANT", str(oc_home))
    info = build_crash_assembly(tmp_path / "crash_deck", end_time=0.0025, anim_dt=0.0005)
    assert info["element_count"] == 3456
    assert info["quad4_count"] == 2400
    assert info["brick8_count"] == 1056

    # 1. Verify CAELab Prep loads the 5-part #include deck cleanly with zero ID leaks when installed
    import importlib.util

    if importlib.util.find_spec("caelab_prep") is not None:
        from caelab_prep.application.session import PrepSession

        session = PrepSession()
        desc = session.open_rad(str(info["starter"]))
        assert desc["node_count"] == 4059
        assert desc["element_count"] == 3456
        assert not any(d.get("unresolved_include") for d in desc["diagnostics"])
        # All 5 #include / root deck resources preserved
        assert len(session._deck_resources()) == 5
        cards = session.list_cards()
        assert sum(1 for c in cards if str(c.get("keyword", "")).upper().startswith("/PART/")) == 5
        assert not re.search(r"[0-9a-f]{32}", json.dumps(desc["diagnostics"]))

    # 2. Execute real Workbench.submit_native() -> starter + engine + anim_to_vtk
    wb = Workbench()
    project = wb.new_native_project("5파트 범퍼-크래시박스 충돌 해석")
    node = wb.add_program_node(project, "analysis", "opencourant")
    wb.set_node_file(
        project,
        node.id,
        info["starter"],
        units={"mass": "Mg", "length": "mm", "time": "s"},
        unit_source="explicit deck",
    )
    wb.save_native_project(project, tmp_path / "crash_project.json")

    try:
        submitted = wb.submit_native(project, node.id, timeout=90.0)
        run = _wait_native_run(wb, project, submitted.id, timeout=95.0)
        assert run.state == "completed", run.error
        assert run.parse_state == "readable", run.result.get("warnings")
        assert [(s["name"], s["state"], s["returncode"]) for s in run.snapshot["stages"]] == [
            ("starter", "completed", 0),
            ("engine", "completed", 0),
        ]
        assert "NORMAL TERMINATION" in (run.workdir / "crash_assembly_0000.out").read_text(errors="replace")
        assert "NORMAL TERMINATION" in (run.workdir / "crash_assembly_0001.out").read_text(errors="replace")

        frames = run.result.get("frames", [])
        assert len(frames) >= 4
        max_disp = float(frames[-1]["summary"]["max_displacement"])
        assert max_disp > 1.0, f"Expected substantial crash deformation (>1 mm), got {max_disp}"
        summary = run.result["summary"]
        assert float(summary["internal_energy"]) > 0.0
        assert abs(float(summary["energy_error"])) < 5.0
        hg_ratio = summary.get("hourglass_ratio")
        if hg_ratio is not None:
            assert float(hg_ratio) < 0.05
    finally:
        wb.close()


# ---------------------------------------------------------------------------
# UC-3: Elastomer Gasket Multi-Temp Prony+WLF Calibration & Export
# ---------------------------------------------------------------------------

def _wlf_shift(T_k: float, Tref: float = 293.15, C1: float = 5.0, C2: float = 100.0) -> float:
    return 10.0 ** (-C1 * (T_k - Tref) / (C2 + T_k - Tref))


def _dma_exact(omega_r: np.ndarray, E0: float = 60.0, a: tuple[float, float] = (0.25, 0.20), tau: tuple[float, float] = (0.05, 5.0)):
    w = np.asarray(omega_r, dtype=float)
    e_inf = E0 * (1.0 - sum(a))
    stor = np.full_like(w, e_inf)
    loss = np.zeros_like(w)
    for ai, ti in zip(a, tau):
        wt2 = (w * ti) ** 2
        stor = stor + E0 * ai * wt2 / (1.0 + wt2)
        loss = loss + E0 * ai * (w * ti) / (1.0 + wt2)
    return stor, loss


def _relax_exact(t_r: np.ndarray, E0: float = 60.0, a: tuple[float, float] = (0.25, 0.20), tau: tuple[float, float] = (0.05, 5.0)):
    t = np.asarray(t_r, dtype=float)
    return E0 * (1.0 - sum(a) + a[0] * np.exp(-t / tau[0]) + a[1] * np.exp(-t / tau[1]))


def _write_csv(path: Path, headers: list[str], rows: list[list[float]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def build_gasket_material_study(root: Path) -> Path:
    """Create multi-temperature elastomer gasket DMA + relaxation study (20C/30C/40C train + 50C holdout)."""
    root.mkdir(parents=True, exist_ok=True)
    obs_stor = ObservableSpec(id="E_storage", quantity_kind="storage_modulus", unit="MPa", measure="viscoelastic", component_order=("E_storage",))
    obs_loss = ObservableSpec(id="E_loss", quantity_kind="loss_modulus", unit="MPa", measure="viscoelastic", component_order=("E_loss",))
    obs_rel = ObservableSpec(id="E_relax", quantity_kind="relaxation_modulus", unit="MPa", measure="viscoelastic", component_order=("E_relax",))

    freqs = np.logspace(-3, 3, 41)
    times = np.logspace(-3, 3, 41)
    study_path = root / "elastomer_gasket_visco.caemat.json"
    with CalibrationStudyService.create(study_path, name="PART_2 엘라스토머 가스켓 점탄성 보정", model="B-VIS-TRS-1", adapter="1.0.0") as svc:
        comps: list[ComparisonSpec] = []
        temps = [(293.15, "train"), (303.15, "train"), (313.15, "train"), (323.15, "holdout")]
        for temp_k, split in temps:
            csv_p = root / f"gasket_dma_{int(round(temp_k * 100))}.csv"
            stor, loss = _dma_exact(2.0 * math.pi * freqs * _wlf_shift(temp_k))
            _write_csv(csv_p, ["frequency_Hz", "storage_MPa", "loss_MPa"], [[float(f), float(s), float(l)] for f, s, l in zip(freqs, stor, loss)])
            eid = f"dma_{int(round(temp_k * 100))}"
            svc.import_experiment(
                csv_p,
                experiment_id=eid,
                channels=[
                    ExperimentChannelSpec(observable_id="E_storage", observable=obs_stor, column_name="storage_MPa", source_unit="MPa", independent_column="frequency_Hz", independent_unit="Hz"),
                    ExperimentChannelSpec(observable_id="E_loss", observable=obs_loss, column_name="loss_MPa", source_unit="MPa", independent_column="frequency_Hz", independent_unit="Hz"),
                ],
                unit_specs={
                    "frequency_Hz": QuantityUnitSpec("Hz", "frequency"),
                    "storage_MPa": QuantityUnitSpec("MPa", "storage_modulus"),
                    "loss_MPa": QuantityUnitSpec("MPa", "loss_modulus"),
                },
                conditions={"temperature_K": temp_k},
                split=split,
                replicate_group=f"gasket-dma-{split}",
                expected_revision=svc.snapshot().document_revision,
            )
            for suffix, obs_id in (("stor", "E_storage"), ("loss", "E_loss")):
                comps.append(ComparisonSpec(
                    id=f"{eid}_{suffix}", experiment_id=eid, observable_id=obs_id, split=split,
                    scale=60.0, weight=0.5, required_for_fit=(split == "train"),
                    required_for_validation=(split != "train"), acceptance_limit=1e-4,
                    sample_weights_policy="log10_trapezoidal",
                ))

        rel_p = root / "gasket_relax_29315.csv"
        rel = _relax_exact(times)
        _write_csv(rel_p, ["time_s", "E_relax_MPa"], [[float(t), float(r)] for t, r in zip(times, rel)])
        svc.import_experiment(
            rel_p,
            experiment_id="rel_29315",
            channels=[ExperimentChannelSpec(observable_id="E_relax", observable=obs_rel, column_name="E_relax_MPa", source_unit="MPa", independent_column="time_s", independent_unit="s")],
            unit_specs={"time_s": QuantityUnitSpec("s", "time"), "E_relax_MPa": QuantityUnitSpec("MPa", "relaxation_modulus")},
            conditions={"temperature_K": 293.15},
            split="train",
            replicate_group="gasket-rel-train",
            expected_revision=svc.snapshot().document_revision,
        )
        comps.append(ComparisonSpec(
            id="rel_29315_c", experiment_id="rel_29315", observable_id="E_relax", split="train",
            scale=60.0, weight=1.0, required_for_fit=True, acceptance_limit=1e-4,
            sample_weights_policy="log10_trapezoidal",
        ))
        svc.configure(
            parameters=svc.parameters(),
            comparisons=comps,
            assessment_policy={"selection_policy": {"mode": "training_only", "validation_comparison_ids": []}},
            numerical_settings={"wlf_provenance": {"source": "DMA multi-temp gasket test", "C1": 5.0, "C2": 100.0, "Tref": 293.15}},
            expected_revision=svc.snapshot().document_revision,
        )
    return study_path


def test_uc3_elastomer_gasket_prony_wlf_material_study(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    PySide6 = pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from apps.workbench_qt.material_study import MaterialStudyWindow, load_korean_font

    monkeypatch.setenv("CAELAB_UI_SETTINGS_PATH", str(tmp_path / "ui.ini"))
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    load_korean_font(app, 10)

    study_path = build_gasket_material_study(tmp_path / "mat_study")
    win = MaterialStudyWindow(study_path=study_path)
    try:
        win.resize(1440, 900)
        win.show()
        app.processEvents()
        assert win.badge_hold.text() == "보류: 미확인"

        win.on_optimize()
        assert win.wait_for_idle(timeout=240.0)
        assert win._active_eval_artifact is not None
        assert win._active_eval_artifact.assessment.objective["fit_loss"] <= 1e-6
        assert win.badge_fit.text() == "학습: 적합"

        # Confirm holdout (50°C)
        win.on_confirm_holdout()
        assert win.wait_for_idle(timeout=60.0)
        assert win.badge_hold.text() == "보류: 적합"

        # Export calibrated report & verify Prony/WLF + equilibrium modulus
        export_json = tmp_path / "gasket_prony_export.json"
        snap = win.service.snapshot()
        receipt = win.service.export(win._active_eval_artifact.evaluation_id, export_json, expected_revision=snap.document_revision)
        assert export_json.is_file()
        payload = json.loads(export_json.read_text(encoding="utf-8"))
        e0_val = float(payload["parameters"]["E0"]["value"])
        a1_val = float(payload["parameters"]["a1"]["value"])
        a2_val = float(payload["parameters"]["a2"]["value"])
        e_inf = float(payload["formulation"]["equilibrium_modulus_MPa"])
        assert abs(e0_val - 60.0) / 60.0 < 0.02
        assert abs((a1_val + a2_val) - 0.45) < 0.02
        assert abs(e_inf - 33.0) / 33.0 < 0.03
        assert payload["assessment_summary"]["holdout_status"] == "pass"
        assert receipt.export_id
    finally:
        win.close()
        win.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()


# ---------------------------------------------------------------------------
# UC-4: Multi-Part Assembly 4-Variable Parametric MDO Closed Loop
# ---------------------------------------------------------------------------

def _install_wsl_batch_ccx_bridge(wb: Workbench) -> None:
    """Provide real WSL CalculiX batch execution on Windows when native ccx.exe is absent."""
    if _native_ccx_path() is not None:
        return
    spec = next(item for item in PROGRAMS if item.id == "calculix")
    fake_status = ProgramStatus(spec, str(Path(__file__).resolve()), source="WSL CalculiX 2.21")
    wb.programs = lambda: [fake_status]

    def wsl_submit_batch(store, items, **kwargs):
        from caelab.jobs import BatchProgress, BatchRun
        from caelab.mdo import apply_transaction as tx

        batch_id = kwargs["batch_id"]
        batch = BatchRun(batch_id, kwargs.get("name", "WSL MDO"), store.root, list(items), total_count=len(items), state="running")
        for idx, item in enumerate(items, 1):
            t0 = time.monotonic()
            run_id = uuid.uuid4().hex
            folder = store.root / run_id
            folder.mkdir(parents=True, exist_ok=True)
            manifest = CalculiXAdapter().snapshot_input(item.input_path, folder)
            proc = _run_wsl_ccx(folder, "model", timeout=float(item.timeout))
            log_path = folder / "job.log"
            log_path.write_text((proc.stdout or "") + "\n" + (proc.stderr or ""), encoding="utf-8")
            result = collect_results(folder) if proc.returncode == 0 else {"status": "failed", "frames": []}
            manifest["outputs"] = {
                p.name: tx.digest(p.read_bytes())
                for p in (folder / "model.frd", folder / "model.dat")
                if p.is_file()
            }
            manifest["artifacts"] = {
                rel: tx.digest((folder / rel).read_bytes())
                for rel in result.get("exchange_files", [])
                if (folder / rel).is_file()
            }
            run = NativeRun(
                run_id, item.node_id, folder, state="completed" if proc.returncode == 0 else "failed",
                parse_state=result.get("status", "failed"), input_path=str(item.input_path),
                returncode=proc.returncode, finished_at=utc_now(), snapshot=manifest,
                result=result, log_path=str(log_path),
            )
            store.write(run)
            item.state = "completed" if proc.returncode == 0 else "failed"
            item.run_id = run_id
            item.batch_id = batch_id
            item.duration_sec = time.monotonic() - t0
            batch.completed_count += 1 if proc.returncode == 0 else 0
            batch.failed_count += 0 if proc.returncode == 0 else 1
            if kwargs.get("on_progress"):
                kwargs["on_progress"](BatchProgress(batch_id, len(items), batch.completed_count, batch.failed_count, 0, item.item_id, "running", 0.0, "WSL CalculiX"))
            kwargs["on_item_finished"](item, run)
        batch.state = "completed"
        kwargs["on_batch_finished"](batch)
        return batch

    wb.jobs.submit_batch = wsl_submit_batch


def _wait_mdo_batch(app, service, timeout: float = 300.0) -> None:
    """Wait until an active MDO batch finishes while pumping Qt events."""
    deadline = time.monotonic() + timeout
    while service.active_batch_id is not None:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"MDO batch {service.active_batch_id} did not finish within {timeout:.1f}s")
        app.processEvents()
        time.sleep(0.05)
    app.processEvents()


def setup_multipart_mdo_study(root: Path):
    """Create and configure a 4-variable multi-part MDO study across *INCLUDE files."""
    info = build_bolted_flange_assembly(root / "mdo_deck", mode="mdo_linear")
    main_inp = info["main_inp"]
    mat_inc = info["materials_inc"]

    wb = Workbench()
    _install_wsl_batch_ccx_bridge(wb)
    service = wb.create_mdo_study()
    service.open_input(main_inp)

    mat_lines = mat_inc.read_text(encoding="utf-8").splitlines()
    inp_lines = main_inp.read_text(encoding="utf-8").splitlines()

    # Find exact lines in materials.inc and bolted_assembly.inp
    gasket_idx = next(i for i, l in enumerate(mat_lines) if l.strip() == "*MATERIAL, NAME=MAT_ELASTOMER")
    al_idx = next(i for i, l in enumerate(mat_lines) if l.strip() == "*MATERIAL, NAME=MAT_AL6061")
    cload_idx = next(i for i, l in enumerate(inp_lines) if l.strip() == "*CLOAD")

    al_rho_line = mat_lines[al_idx + 4]
    al_rho_literal = al_rho_line.split(",")[0].strip()
    fy_line = inp_lines[cload_idx + 2]
    fy_literal = fy_line.split(",")[-1].strip()

    tokens = service.parameterize([
        {
            "token": "BRACKET_RHO",
            "source_path": str(mat_inc),
            "line_number": al_idx + 5,
            "expected_line": al_rho_line,
            "old_literal": al_rho_literal,
            "occurrence": 1,
        },
        {
            "token": "GASKET_MOD",
            "source_path": str(mat_inc),
            "line_number": gasket_idx + 3,
            "expected_line": mat_lines[gasket_idx + 2],
            "old_literal": "45.0",
            "occurrence": 1,
        },
        {
            "token": "BRACKET_MOD",
            "source_path": str(mat_inc),
            "line_number": al_idx + 3,
            "expected_line": mat_lines[al_idx + 2],
            "old_literal": "69000.0",
            "occurrence": 1,
        },
        {
            "token": "PIN_FY",
            "source_path": str(main_inp),
            "line_number": cload_idx + 3,
            "expected_line": fy_line,
            "old_literal": fy_literal,
            "occurrence": 1,
        },
    ])
    assert set(tokens) == {"BRACKET_MOD", "BRACKET_RHO", "GASKET_MOD", "PIN_FY"}

    space = VariableSpace(variables=(
        ContinuousVariable(id="bracket_rho", name="브라켓 밀도", unit="Mg/mm**3", lower=2.4e-9, upper=7.8e-9, initial=float(al_rho_literal)),
        ContinuousVariable(id="gasket_mod", name="가스켓 탄성계수", unit="MPa", lower=25.0, upper=85.0, initial=45.0),
        ContinuousVariable(id="bracket_mod", name="브라켓 탄성계수", unit="MPa", lower=55000.0, upper=110000.0, initial=69000.0),
        ContinuousVariable(id="pin_fy", name="핀 Y절점하중", unit="N", lower=-200.0, upper=-100.0, initial=float(fy_literal)),
    ))
    service.configure(
        space=space,
        objectives=[
            ObjectiveSpec(id="mass_eall", response_id="mass_eall", sense="MIN"),
            ObjectiveSpec(id="disp", response_id="disp", sense="MIN"),
        ],
        constraints=[
            ConstraintSpec(id="stress_limit", response_id="stress", kind="UPPER", limit=145.0, unit="MPa"),
        ],
        response_units={"mass_eall": "Mg", "disp": "mm", "stress": "MPa"},
    )
    return wb, service, main_inp, mat_inc


def test_uc4_multipart_assembly_4var_mdo_closed_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not _ccx_available():
        pytest.skip("CalculiX binary (native or WSL) not available")

    PySide6 = pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from apps.workbench_qt.mdo_studio.window import MDOStudioWindow

    monkeypatch.setenv("CAELAB_UI_SETTINGS_PATH", str(tmp_path / "ui.ini"))
    monkeypatch.setenv("CAELAB_MDO_SETTINGS_PATH", str(tmp_path / "mdo.ini"))
    app = QApplication.instance() or QApplication([])

    wb, service, main_inp, mat_inc = setup_multipart_mdo_study(tmp_path)
    original_main_bytes = main_inp.read_bytes()
    original_mat_bytes = mat_inc.read_bytes()

    win = MDOStudioWindow(service=service)
    try:
        win.resize(1440, 900)
        win.show()
        app.processEvents()

        rows = service.generate_doe(DOEConfig(method="lhs", n_samples=8, seed=7))
        assert len(rows) == 8
        service.submit([r.point_id for r in rows])
        _wait_mdo_batch(app, service, timeout=300.0)

        completed = [r for r in service.study.records if r.status == "COMPLETED"]
        assert len(completed) == 8
        distinct_masses = {round(r.observations["mass_eall"], 8) for r in completed}
        assert len(distinct_masses) >= 6, f"Expected real mass variation across LHS points, got {distinct_masses}"
        feasible = [r for r in completed if r.feasibility == "FEASIBLE"]
        assert feasible, "Expected at least one feasible design point under stress <= 145 MPa"
        pareto = [r for r in feasible if r.is_pareto]
        assert pareto, "Expected non-empty Pareto front"

        win._populate_study()
        app.processEvents()
        assert win.tdp_model.rowCount() == 8

        # Apply best feasible Pareto point and verify backup & cross-INCLUDE update
        best = min(pareto, key=lambda r: r.observations["disp"])
        plan = service.prepare_apply(best.point_id)
        receipt = service.apply(plan)
        assert receipt["files"]
        assert main_inp.with_name("bolted_assembly.inp.bak").read_bytes() == original_main_bytes
        assert mat_inc.with_name("materials.inc.bak").read_bytes() == original_mat_bytes
        assert main_inp.read_bytes() != original_main_bytes
        assert mat_inc.read_bytes() != original_mat_bytes
    finally:
        win.close()
        win.deleteLater()
        service.close()
        wb.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
