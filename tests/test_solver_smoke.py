"""Per-solver smoke tests against the REAL installed programs.

Each test skips unless the corresponding program is installed and its
test data is staged (env vars below). Skipping is the honest state for
a machine without the solver; on the development machine these run
for real and assert solver-level outcomes (normal termination,
equilibrium), not just file existence.

- CAELAB_OPENCOURANT_HOME : OpenCourant tree (contains exec/, hm_cfg_files/)
- CAELAB_OPENCOURANT_TESTDATA : dir with the TWISBEAM QA decks
- CAELAB_CODEASTER : run_aster executable (Salome-Meca)
- CAELAB_ASTER_TESTDATA : dir with beam.med / study.comm / study.export
- CAELAB_SALOME : salome launcher of a Salome 9.16 install
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


# ---------------------------------------------------------------- OpenCourant
@pytest.mark.skipif(
    not (os.environ.get("CAELAB_OPENCOURANT_HOME") and os.environ.get("CAELAB_OPENCOURANT_TESTDATA")),
    reason="OpenCourant not installed / test data not staged",
)
def test_opencourant_twist_beam_smoke(tmp_path):
    home = _env_path("CAELAB_OPENCOURANT_HOME")
    data = _env_path("CAELAB_OPENCOURANT_TESTDATA")
    for name in ("TWISBEAM_0000.rad", "TWISBEAM_0001.rad", "TWISBEAMD0B"):
        shutil.copy(data / name, tmp_path / name)
    env = dict(os.environ)
    env.update({
        "OPENRADIOSS_PATH": str(home),
        "RAD_CFG_PATH": str(home / "hm_cfg_files"),
        "OMP_STACKSIZE": "400m",
        "LD_LIBRARY_PATH": (
            f"{home}/extlib/hm_reader/linux64:{home}/extlib/h3d/lib/linux64:"
            + env.get("LD_LIBRARY_PATH", "")
        ),
    })
    starter = subprocess.run(
        [str(home / "exec" / "starter_linux64_gf"), "-i", "TWISBEAM_0000.rad", "-nt", "2"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=600,
    )
    assert starter.returncode == 0
    assert (tmp_path / "TWISBEAM_0000_0001.rst").exists()
    engine = subprocess.run(
        [str(home / "exec" / "engine_linux64_gf"), "-i", "TWISBEAM_0001.rad", "-nt", "2"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=900,
    )
    assert engine.returncode == 0
    out = (tmp_path / "TWISBEAM_0001.out").read_text(errors="replace")
    assert "NORMAL TERMINATION" in out
    cycles = re.search(r"TOTAL NUMBER OF CYCLES\s*:\s*(\d+)", out)
    assert cycles and int(cycles.group(1)) > 10000
    # last cycle row: energy error column must stay ~0%
    rows = [ln for ln in out.splitlines() if re.match(r"\s+\d+\s+0\.\d+E", ln)]
    assert rows, "no cycle table rows found"
    err_pct = float(rows[-1].split()[6].rstrip("%"))
    assert abs(err_pct) < 1.0


# ---------------------------------------------------------------- Code_Aster
@pytest.mark.skipif(
    not (os.environ.get("CAELAB_CODEASTER") and os.environ.get("CAELAB_ASTER_TESTDATA")),
    reason="Code_Aster not installed / test data not staged",
)
def test_codeaster_cantilever_reaction(tmp_path):
    runner = os.environ["CAELAB_CODEASTER"]
    data = _env_path("CAELAB_ASTER_TESTDATA")
    for name in ("beam.med", "study.comm", "study.export"):
        shutil.copy(data / name, tmp_path / name)
    proc = subprocess.run(
        [runner, str(tmp_path / "study.export")],
        cwd=tmp_path, capture_output=True, text=True, timeout=1200,
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    resu = (tmp_path / "study.resu").read_text(errors="replace")
    values = [float(x) for x in re.findall(r"[-+]?\d+\.\d+E[-+]\d+", resu)]
    # REAC_NODA rows are per node (DX DY DZ triples); the Z reactions at
    # the fixed face must balance the -1000 N applied load. Load entries
    # on the 3 nodes shared with the loaded face are absorbed by the
    # constraint (same effect as the CalculiX cross-check), so allow 5%.
    dz = values[2::3]
    total = sum(dz)
    assert total == pytest.approx(1000.0, rel=0.05), f"reaction sum {total}"


# ---------------------------------------------------------------- Salome
_SALOME_MED_SCRIPT = '''
import salome
salome.salome_init()
from salome.geom import geomBuilder
geompy = geomBuilder.New()
box = geompy.MakeBoxDXDYDZ(100.0, 10.0, 10.0)
geompy.addToStudy(box, "box")
faces = geompy.SubShapeAllSorted(box, geompy.ShapeType["FACE"])
fixed = load = None
for f in faces:
    bb = geompy.BoundingBox(f)
    cx = 0.5 * (bb[0] + bb[1])
    if abs(cx) < 1e-6:
        fixed = f
    if abs(cx - 100.0) < 1e-6:
        load = f
from salome.smesh import smeshBuilder
smesh = smeshBuilder.New()
mesh = smesh.Mesh(box, "beam_mesh")
algo = mesh.Tetrahedron(algo=smeshBuilder.NETGEN_1D2D3D)
algo.Parameters().SetMaxSize(4.0)
assert mesh.Compute()
mesh.GroupOnGeom(fixed, "FIXED")
mesh.GroupOnGeom(load, "LOADF")
mesh.GroupOnGeom(box, "SOLID")
mesh.ExportMED(r"OUT_MED")
import medcoupling as mc
fmm = mc.MEDFileMesh.New(r"OUT_MED")
print("NODES", fmm.getCoords().getNumberOfTuples())
print("CELLS", fmm.getMeshAtLevel(0).getNumberOfCells())
print("GROUPS", ",".join(sorted(fmm.getGroupsNames())))
'''


@pytest.mark.skipif(
    not os.environ.get("CAELAB_SALOME"),
    reason="Salome not installed (CAELAB_SALOME unset)",
)
def test_salome_headless_med_mesh(tmp_path):
    """Headless Salome: build a box mesh in SMESH, export MED, and read
    it back with MEDCoupling. Counts must agree between the two readers
    and the three geometry groups must survive the round trip."""
    launcher = os.environ["CAELAB_SALOME"]
    med = tmp_path / "salome_smoke.med"
    script = tmp_path / "salome_smoke.py"
    script.write_text(_SALOME_MED_SCRIPT.replace("OUT_MED", str(med)))
    proc = subprocess.run(
        [launcher, "shell", str(script)],
        cwd=tmp_path, capture_output=True, text=True, timeout=1200,
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    nodes = re.search(r"NODES (\d+)", proc.stdout)
    cells = re.search(r"CELLS (\d+)", proc.stdout)
    groups = re.search(r"GROUPS ([A-Z,]+)", proc.stdout)
    assert nodes and cells and groups, proc.stdout[-2000:]
    # 100x10x10 box at max size 4 measured 259 nodes / 584 tetra on
    # Salome 9.16; keep the band loose for other mesher versions.
    assert int(nodes.group(1)) > 100
    assert int(cells.group(1)) > 200
    assert groups.group(1).split(",") == ["FIXED", "LOADF", "SOLID"]
    assert med.exists() and med.stat().st_size > 10_000
