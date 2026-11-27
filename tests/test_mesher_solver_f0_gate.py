"""Real-solver checks for the M3-F0 mesher handoff; no GUI or emulated solvers."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


HEX = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
       (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
TET = [HEX[index] for index in (0, 1, 3, 4)]
WEDGE = [HEX[index] for index in (0, 1, 3, 4, 5, 7)]
TET_EDGES = [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]
TET10 = TET + [
    tuple((a + b) / 2 for a, b in zip(TET[first], TET[second]))
    for first, second in TET_EDGES
]
QUAD_EDGES = [(0, 1), (1, 2), (2, 3), (3, 0)]
HEX_EDGES = QUAD_EDGES + [(a + 4, b + 4) for a, b in QUAD_EDGES] + [(i, i + 4) for i in range(4)]
TRI_EDGES = [(0, 1), (1, 2), (2, 0)]
WEDGE_EDGES = TRI_EDGES + [(a + 3, b + 3) for a, b in TRI_EDGES] + [(i, i + 3) for i in range(3)]


def midpoint(a, b):
    return tuple((x + y) / 2 for x, y in zip(a, b))


def with_midsides(points, edges):
    return points + [midpoint(points[a], points[b]) for a, b in edges]


HEX20 = with_midsides(HEX, HEX_EDGES)
CCX_CELLS = {
    "C3D20": (HEX20, 27, 1.0),
    "C3D20R": (HEX20, 8, 1.0),
    "C3D15": (with_midsides(WEDGE, WEDGE_EDGES), 9, 0.5),
    "S8": (with_midsides(HEX[:4], QUAD_EDGES), 27, 1.0),
    "S6": (with_midsides(TET[:3], TRI_EDGES), 9, 0.5),
    "C3D6": (WEDGE, 2, 0.5),
    "C3D8": (HEX, 8, 1.0),
    "C3D8I": (HEX, 8, 1.0),
    "C3D4": (TET, 1, 1 / 6),
    "C3D10": (TET10, 4, 1 / 6),
    "S4": (HEX[:4], 8, 1.0),
    "S3": (TET[:3], 2, 0.5),
}
WSL = ["wsl", "-d", "Ubuntu"]
MIXED_POINTS = HEX + [(0.5, 0.5, 2), (0.5, -0.5, 2)]
MIXED_CONNECTIVITY = [(1, 1, 2, 3, 4, 5, 6, 7, 8), (2, 5, 6, 7, 8, 9), (3, 5, 6, 9, 10)]
WEDGE_PATCH_POINTS = HEX + [(0.5, 0, 2), (0.5, 1, 2), (0.5, -1, 1.5)]
WEDGE_PATCH_ELEMENTS = [
    ("C3D8", [(1, 1, 2, 3, 4, 5, 6, 7, 8)]),
    ("C3D6", [(2, 5, 9, 6, 8, 10, 7)]),
    ("C3D4", [(3, 5, 6, 9, 11)]),
]


@pytest.fixture(scope="module")
def ccx():
    native = os.environ.get("CAELAB_CCX") or shutil.which("ccx")
    if not native and os.name != "nt":
        pytest.skip("CalculiX ccx is not installed")
    if not native:
        if not shutil.which("wsl"):
            pytest.skip("Neither native CalculiX nor WSL is installed")
        found = subprocess.run(
            [*WSL, "-e", "which", "ccx"], capture_output=True, timeout=30
        )
        if found.returncode:
            pytest.skip("Ubuntu WSL CalculiX ccx is unavailable")

    def execute(folder):
        if native:
            command = [native, "-i", "model"]
        else:
            linux_folder = wsl_path(folder)
            command = [*WSL, "--cd", linux_folder, "-e", "env",
                       "OMP_NUM_THREADS=1", "ccx", "-i", "model"]
        return run_solver(command, folder, "ccx.log")

    return execute


def wsl_path(path):
    return subprocess.check_output(
        [*WSL, "wslpath", "-a", path.resolve().as_posix()], text=True, timeout=20
    ).strip()


def run_solver(command, folder, log_name, *, env=None):
    result = subprocess.run(command, cwd=folder, capture_output=True, timeout=90, env=env)
    log = (result.stdout + b"\n" + result.stderr).decode("utf-8", errors="replace")
    (folder / log_name).write_text(log, encoding="utf-8")
    return result.returncode, log


def ccx_mesh_lines(points, elements):
    lines = ["*NODE,NSET=ALLN"]
    lines.extend(f"{node},{x},{y},{z}" for node, (x, y, z) in enumerate(points, 1))
    for kind, ids in elements:
        lines.append(f"*ELEMENT,TYPE={kind},ELSET=BODY")
        for element in ids:
            # CalculiX accepts at most 16 entries on one input line.
            if len(element) > 16:
                lines.append(",".join(map(str, element[:16])) + ",")
                lines.append(",".join(map(str, element[16:])))
            else:
                lines.append(",".join(map(str, element)))
    lines.extend(["*MATERIAL,NAME=ELASTIC", "*ELASTIC", "1000.,0.25"])
    if elements[0][0].startswith("S"):
        lines.extend(["*SHELL SECTION,ELSET=BODY,MATERIAL=ELASTIC", "1."])
    else:
        lines.append("*SOLID SECTION,ELSET=BODY,MATERIAL=ELASTIC")
    return lines


def ccx_patch(points, elements, displacement):
    lines = ccx_mesh_lines(points, elements)
    lines.extend(["*STEP", "*STATIC", "*BOUNDARY"])
    for node, point in enumerate(points, 1):
        for dof, value in enumerate(displacement(*point), 1):
            lines.append(f"{node},{dof},{dof},{value:.12g}")
    lines.extend(["*EL PRINT,ELSET=BODY", "S,E", "*NODE PRINT,NSET=ALLN",
                  "U,RF", "*END STEP", ""])
    return "\n".join(lines)


def dat_rows(text, heading, number_of_keys, width):
    for block in text.split("\n\n"):
        if block.lstrip().startswith(heading + " ("):
            rows = text[text.index(block) + len(block):].strip().split("\n\n")[0]
            return [
                (tuple(map(int, row.split()[:number_of_keys])),
                 tuple(map(float, row.split()[number_of_keys:number_of_keys + width])))
                for row in rows.splitlines()
            ]
    raise AssertionError(f"Missing CalculiX DAT block {heading}")


@pytest.mark.parametrize("kind", CCX_CELLS)
@pytest.mark.parametrize("motion", ["axial_strain", "rigid_translation_rotation"])
def test_calculix_affine_element_control(tmp_path, ccx, kind, motion):
    points, ip_count, volume = CCX_CELLS[kind]
    is_shell = kind.startswith("S")
    axial = motion == "axial_strain"
    if axial:
        displacement = lambda x, y, z: (0.001 * x, 0, 0)
    else:
        # Infinitesimal rotation about z plus translation in linear statics.
        displacement = lambda x, y, z: (0.002 - 0.001 * y, -0.003 + 0.001 * x, 0.004)
    elements = [(kind, [(1, *range(1, len(points) + 1))])]
    (tmp_path / "model.inp").write_text(ccx_patch(points, elements, displacement))
    status, log = ccx(tmp_path)
    assert status == 0 and "Job finished" in log and "*ERROR" not in log, log
    dat = (tmp_path / "model.dat").read_text()
    expected_stress = (1.2, 0.4, 0.4, 0, 0, 0)
    expected_strain = (0.001, 0, 0, 0, 0, 0)
    if is_shell:
        expected_stress = (16 / 15, 4 / 15, 0, 0, 0, 0)
        expected_strain = (0.001, 0, -0.001 / 3, 0, 0, 0)
    for heading, expected in [("stresses", expected_stress), ("strains", expected_strain)]:
        rows = dat_rows(dat, heading, 2, 6)
        assert [key for key, values in rows] == [(1, ip) for ip in range(1, ip_count + 1)]
        for _, values in rows:
            assert values == pytest.approx(expected if axial else (0,) * 6, abs=1e-7, rel=1e-6)
    displacements = dat_rows(dat, "displacements", 1, 3)
    assert len(displacements) == len(points)
    for (node,), values in displacements:
        assert values == pytest.approx(displacement(*points[node - 1]), abs=1e-9)
    forces = dat_rows(dat, "forces", 1, 3)
    assert len(forces) == len(points)
    assert tuple(map(sum, zip(*(values for _, values in forces)))) == pytest.approx(
        (0, 0, 0), abs=1e-6
    )
    if axial:
        # Virtual work of unit axial strain = integral of Sxx over the domain.
        axial_resultant = sum(points[node - 1][0] * fx for (node,), (fx, _, _) in forces)
        assert axial_resultant == pytest.approx(expected_stress[0] * volume, abs=1e-6)
    else:
        for _, values in forces:
            assert values == pytest.approx((0, 0, 0), abs=1e-7)


def test_calculix_rejects_native_pyramid_in_conformal_mixed_patch(tmp_path, ccx):
    # The pyramid reuses the entire hex top face, and the tet reuses one
    # triangular pyramid side face. No duplicate interface nodes or singular brick.
    elements = [(kind, [ids]) for kind, ids in zip(("C3D8", "C3D5", "C3D4"), MIXED_CONNECTIVITY)]
    (tmp_path / "model.inp").write_text(
        ccx_patch(MIXED_POINTS, elements, lambda x, y, z: (0.001 * x, 0, 0))
    )
    status, log = ccx(tmp_path)
    assert status != 0 and "*ERROR reading *ELEMENT" in log, log
    assert re.search(r"C3D5\s+is an unknown element type", log), log
    assert not (tmp_path / "model.dat").read_text().strip()


@pytest.mark.parametrize("motion", ["uniaxial_z", "rigid_translation_rotation"])
def test_calculix_conformal_hex_wedge_tet_patch(tmp_path, ccx, motion):
    # Hex-wedge share the quad 5-6-7-8. Wedge-tet share the triangle 5-6-9.
    points, elements = WEDGE_PATCH_POINTS, WEDGE_PATCH_ELEMENTS
    traction = motion == "uniaxial_z"
    if traction:
        # Integrate t = sigma * n_z e_z on the outer faces, with sigma=1 MPa.
        # Hex bottom projects to -1 mm². The two wedge slopes each project
        # to +0.5 mm². The three exposed tet faces project to -0.5, +0.25,
        # +0.25 mm². Uniform face traction divides equally among face nodes.
        forces_z = {
            1: -0.25, 2: -0.25, 3: -0.25, 4: -0.25,
            5: 1 / 24, 6: 1 / 24, 7: 1 / 8, 8: 1 / 8,
            9: 5 / 12, 10: 1 / 4, 11: 0.0,
        }
        lines = ccx_mesh_lines(points, elements)
        lines.extend(["*STEP", "*STATIC", "*BOUNDARY", "1,1,3", "2,2,3", "4,3,3", "*CLOAD"])
        lines.extend(f"{node},3,{force:.15g}" for node, force in forces_z.items() if force)
        lines.extend(["*EL PRINT,ELSET=BODY", "S,E", "*NODE PRINT,NSET=ALLN",
                      "U,RF", "*END STEP", ""])
        deck = "\n".join(lines)
        displacement = lambda x, y, z: (-0.00025 * x, -0.00025 * y, 0.001 * z)
        stress = (0, 0, 1, 0, 0, 0)
        strain = (-0.00025, -0.00025, 0.001, 0, 0, 0)
    else:
        displacement = lambda x, y, z: (0.002 + 0.001 * z, -0.003, 0.004 - 0.001 * x)
        deck = ccx_patch(points, elements, displacement)
        stress = strain = (0,) * 6
        forces_z = dict.fromkeys(range(1, len(points) + 1), 0.0)

    (tmp_path / "model.inp").write_text(deck)
    status, log = ccx(tmp_path)
    assert status == 0 and "Job finished" in log and "*ERROR" not in log, log
    dat = (tmp_path / "model.dat").read_text()
    for heading, expected in [("stresses", stress), ("strains", strain)]:
        rows = dat_rows(dat, heading, 2, 6)
        assert [key for key, _ in rows] == [
            (cell, point) for cell, count in [(1, 8), (2, 2), (3, 1)]
            for point in range(1, count + 1)
        ]
        for _, values in rows:
            assert values == pytest.approx(expected, abs=1e-7)

    for heading in ("displacements", "forces"):
        rows = dat_rows(dat, heading, 1, 3)
        assert [node for (node,), _ in rows] == list(range(1, len(points) + 1))
        for (node,), values in rows:
            expected = displacement(*points[node - 1]) if heading == "displacements" else (
                0, 0, forces_z[node]
            )
            assert values == pytest.approx(expected, abs=1e-7)
    forces = dat_rows(dat, "forces", 1, 3)
    assert tuple(map(sum, zip(*(values for _, values in forces)))) == pytest.approx(
        (0, 0, 0), abs=1e-6
    )
    resultant = sum(points[node - 1][2] * fz for (node,), (_, _, fz) in forces)
    # Virtual work for unit axial strain gives sigma * volume.
    assert resultant == pytest.approx((1 + 1 / 2 + 1 / 6) if traction else 0, abs=1e-6)


RAD_CELLS = {
    "TETRA10": (TET10, 1e-6 / 6),
    # Radioss numbers vertical edges 13..16 and the top edges 17..20.
    "BRIC20": (HEX20[:12] + HEX20[16:20] + HEX20[12:16], 1e-6),
    "BRICK": (HEX, 1e-6),
    "TETRA4": (TET, 1e-6 / 6),
    "SHELL": (HEX[:4], 1e-7),
    "SH3N": (TET[:3], 5e-8),
    "PENTA6": (WEDGE, 5e-7),
    "PYRA5": (HEX[:4] + [(0.5, 0.5, 1)], None),
    "SH4N": (HEX[:4], None),
}


@pytest.fixture(scope="module", params=["OpenCourant", "OpenRadioss"])
def radioss(request):
    from caelab.connections import _radioss_installation

    windows = request.param == "OpenCourant"
    variable = "CAELAB_OPENCOURANT" if windows else "CAELAB_F0_OPENRADIOSS"
    path = os.environ.get(variable)
    if not path:
        pytest.skip(f"{request.param} requires {variable} pointing to an installed solver")
    if windows and os.name != "nt":
        pytest.skip("The OpenCourant F0 fixture requires the Windows release")
    if not windows and os.name == "nt" and not shutil.which("wsl"):
        pytest.skip("OpenRadioss F0 requires WSL or a Linux installation")
    home = Path(path).resolve()
    starter, engine, runtime = _radioss_installation(home, "windows" if windows else "linux")

    def execute(folder, step):
        binary = starter if step == "0000" else engine
        args = ["-i", f"model_{step}.rad", "-nt", "1"]
        executable = str(binary)
        if not windows and os.name == "nt":
            linux_home = wsl_path(home)
            lib_paths = ":".join(f"{linux_home}/{part}" for part in (
                "extlib/hm_reader/linux64", "extlib/h3d/lib/linux64", "extlib/zlib/linux64/lib"
            ))
            runtime_args = [f"RAD_CFG_PATH={linux_home}/hm_cfg_files",
                            f"OPENRADIOSS_PATH={linux_home}",
                            f"LD_LIBRARY_PATH={lib_paths}", "OMP_NUM_THREADS=1"]
            command = [*WSL, "--cd", wsl_path(folder), "-e", "env", *runtime_args,
                       wsl_path(binary), *args]
            return run_solver(command, folder, f"run_{step}.log")
        env = {**os.environ, **runtime}
        if windows:
            env["PATH"] += os.pathsep + os.environ.get("PATH", "")
        return run_solver([executable, *args], folder, f"run_{step}.log", env=env)

    return execute, home if windows else None


def i10(value):
    return f"{value:10d}"


def r20(value):
    return f"{value:20.9E}"


def rad_property(kind):
    if kind == "thick_shell":
        return ["/PROP/TYPE20/1", "PENTA6 as thick shell", i10(15) + i10(4),
                r20(0) * 3, r20(0) * 5]
    if kind == "shell":
        return ["/PROP/SHELL/1", "shell", "".join(map(i10, (24, 4, 2, 2))) + 20 * " " + r20(1),
                r20(0) * 4 + r20(0.015),
                i10(1) + 10 * " " + r20(0.1) + r20(5 / 6) + 10 * " " + i10(2) + i10(0)]
    formulation = 16 if kind == "bric20" else 1
    return ["/PROP/SOLID/1", "solid", "".join(map(i10, (formulation, 4, 0, 0, 0, 0, 1000, 1))) + r20(0),
            "".join(map(r20, (1.1, 0.05, 0.1, 0, 0))), r20(0) * 5]


def write_rad_translation(folder, points, elements, prop_kind):
    node_lines = ["".join(map(i10, range(i, min(i + 10, len(points) + 1))))
                  for i in range(1, len(points) + 1, 10)]
    starter = [
        "#RADIOSS STARTER", "/BEGIN", "model", "      2024",
        "                  Mg                  mm                   s",
        "                  Mg                  mm                   s",
        "/PART/1", "patch", i10(1) + i10(1) + i10(0), *rad_property(prop_kind),
        "/MAT/LAW1/1", "elastic", r20(1e-6), r20(1000) + r20(0.25),
        "/NODE",
        *(i10(node) + "".join(map(r20, point)) for node, point in enumerate(points, 1)),
    ]
    for keyword, connectivity in elements:
        starter.append(f"/{keyword}/1")
        if keyword == "TETRA10":
            rows = [connectivity[:1], connectivity[1:]]
        elif keyword == "BRIC20":
            rows = [connectivity[:9], connectivity[9:17], connectivity[17:]]
        else:
            rows = [connectivity]
        starter.extend("".join(map(i10, row)) for row in rows)
    starter.extend(["/GRNOD/NODE/1", "all nodes", *node_lines, "/INIVEL/TRA/1", "translation",
                    r20(1) + r20(0) + r20(0) + i10(1) + i10(0), "/END", ""])
    (folder / "model_0000.rad").write_text("\n".join(starter))
    engine = ["/RUN/model/1", "0.000100", "/PRINT/-1", "/ANIM/DT", "0.0 0.000025",
              "/ANIM/VECT/DISP", "/ANIM/ELEM/VONM", "/TFILE/4", "0.000025", "/END", ""]
    (folder / "model_0001.rad").write_text("\n".join(engine))


@pytest.mark.parametrize("kind", ["BRICK", "TETRA4", "SHELL", "SH3N", "PENTA6", "TETRA10", "BRIC20"])
def test_radioss_rigid_translation_energy(tmp_path, radioss, kind):
    from caelab.native.radioss import collect_radioss_results, parse_radioss_output

    points, mass = RAD_CELLS[kind]
    property_kind = {"BRIC20": "bric20", "PENTA6": "thick_shell", "SHELL": "shell", "SH3N": "shell"}.get(kind, "solid")
    write_rad_translation(
        tmp_path, points, [(kind, (1, *range(1, len(points) + 1)))], property_kind
    )
    execute, converter_home = radioss
    starter_status, starter_log = execute(tmp_path, "0000")
    assert starter_status == 0 and "ERROR TERMINATION" not in starter_log, starter_log
    assert list(tmp_path.glob("model_0000*.rst"))
    status, log = execute(tmp_path, "0001")
    assert status == 0 and "NORMAL TERMINATION" in log, log
    assert len(list(tmp_path.glob("modelA[0-9][0-9][0-9]"))) >= 2
    values = parse_radioss_output(tmp_path / "model_0001.out")
    assert values["total_cycles"] >= 2 and values["printed_time"] >= 5e-5
    assert values["kinetic_energy"] == pytest.approx(0.5 * mass, rel=1e-3)
    assert values["internal_energy"] == pytest.approx(0.0, abs=1e-12)
    assert values["external_work"] == pytest.approx(0.0, abs=1e-12)
    assert abs(values["energy_error"]) <= 0.1
    assert values["mass_error"] == 0.0

    # TYPE20 PENTA6 is a solver-only control. The Windows converter writes it
    # as VTK_HEXAHEDRON with duplicate ids, which the reader cannot use.
    if converter_home is not None and kind != "PENTA6":
        # Exercise the existing native animation reader with the Windows release converter.
        results = collect_radioss_results(tmp_path, converter_installation=converter_home)
        assert results["status"] == "readable", results["warnings"]
        frame = results["frames"][-1]
        assert frame["summary"]["displacement_node_count"] == len(points)
        assert frame["time"] >= 5e-5
        dx, dy, dz = frame["fields"][0]["summary"]["components"]
        assert (dx["minimum"], dx["maximum"]) == pytest.approx((frame["time"],) * 2, abs=1e-8)
        for transverse in dy, dz:
            assert (transverse["minimum"], transverse["maximum"]) == pytest.approx((0, 0), abs=1e-8)


@pytest.mark.parametrize(
    ("kind", "error"), [("PYRA5", 100210), ("PENTA6", 3107), ("SH4N", 100210)]
)
def test_radioss_rejects_unsupported_native_card_or_property(tmp_path, radioss, kind, error):
    if kind == "PYRA5":
        # The negative deck is the actual conformal three-cell transition.
        points = MIXED_POINTS
        elements = list(zip(("BRICK", "PYRA5", "TETRA4"), MIXED_CONNECTIVITY))
    else:
        points, _ = RAD_CELLS[kind]
        elements = [(kind, (1, *range(1, len(points) + 1)))]
    prop_kind = "shell" if kind == "SH4N" else "solid"
    write_rad_translation(tmp_path, points, elements, prop_kind)
    execute, _ = radioss
    status, log = execute(tmp_path, "0000")
    assert status != 0 and "ERROR TERMINATION" in log, log
    assert re.search(rf"ERROR ID\s*:\s*{error}\b", log), log
    if error == 100210:
        assert f"/{kind}/1" in log and "Unrecognized option" in log
    assert not list(tmp_path.glob("model_0000*.rst"))


def write_ccx_cantilever(folder, divisions, *, tied):
    # L/h=12. Four identical nodal forces represent the end-face shear.
    lines = ["*NODE"]
    unit_section = [(0, 0), (1, 0), (1, 1), (0, 1)]
    rings = []
    for station in range(divisions + 1):
        initial_ring = tuple(range(4 * len(rings) + 1, 4 * len(rings) + 5))
        rings.append(initial_ring)
        copy = tied and station == divisions // 2
        if copy:
            rings.append(tuple(range(initial_ring[-1] + 1, initial_ring[-1] + 5)))
        for ring in rings[-2:] if copy else rings[-1:]:
            for node, (y, z) in zip(ring, unit_section):
                lines.append(f"{node},{12 * station / divisions:.12g},{y},{z}")

    lines.append("*ELEMENT,TYPE=C3D8I,ELSET=BEAM")
    for index in range(divisions):
        start = index + int(tied and index >= divisions // 2)
        a, b, c, d = rings[start]
        e, f, g, h = rings[start + 1]
        lines.append(f"{index + 1},{a},{e},{f},{b},{d},{h},{g},{c}")

    left, right = rings[0], rings[-1]
    lines.extend(["*NSET,NSET=CLAMP", ",".join(map(str, left)), "*NSET,NSET=TIP",
                  ",".join(map(str, right)), "*MATERIAL,NAME=LINEAR", "*ELASTIC",
                  "1000.,0.25", "*SOLID SECTION,ELSET=BEAM,MATERIAL=LINEAR"])
    if tied:
        lines.extend(["*SURFACE,NAME=MAIN", f"{divisions // 2},S4",
                      "*SURFACE,NAME=SECONDARY", f"{divisions // 2 + 1},S6",
                      "*TIE,NAME=MIDSPAN_TIE", "SECONDARY,MAIN"])
    lines.extend(["*BOUNDARY", "CLAMP,1,3", "*STEP", "*STATIC", "*CLOAD"])
    lines.extend(f"{node},3,-0.0025" for node in right)
    lines.extend(["*NODE PRINT,NSET=CLAMP", "RF", "*NODE PRINT,NSET=TIP", "U", "*END STEP", ""])
    (folder / "model.inp").write_text("\n".join(lines))


def test_calculix_c3d8i_bending_refinement_and_tie(tmp_path, ccx):
    # Euler-Bernoulli cantilever plus Timoshenko shear correction.
    length, load, elastic, shear, inertia = 12, 0.01, 1000, 400, 1 / 12
    reference = load * length**3 / (3 * elastic * inertia) + load * length / ((5 / 6) * shear)
    deflections = {}
    for divisions, tied in [(4, False), (16, False), (16, True)]:
        folder = tmp_path / f"beam-{divisions}-{'tied' if tied else 'continuous'}"
        folder.mkdir()
        write_ccx_cantilever(folder, divisions, tied=tied)
        status, log = ccx(folder)
        assert status == 0 and "Job finished" in log and "*ERROR" not in log, log
        if tied:
            assert re.search(r"tie constraints:\s+1\b", log), log
        dat = (folder / "model.dat").read_text()
        tip = dat_rows(dat, "displacements", 1, 3)
        reactions = dat_rows(dat, "forces", 1, 3)
        assert len(tip) == len(reactions) == 4
        assert tuple(map(sum, zip(*(force for _, force in reactions)))) == pytest.approx(
            (0, 0, load), abs=1e-7
        )
        deflections[divisions, tied] = -sum(value[2] for _, value in tip) / 4

    coarse, refined, tied = (
        deflections[4, False], deflections[16, False], deflections[16, True]
    )
    assert abs(refined - reference) < abs(coarse - reference)
    assert refined == pytest.approx(reference, rel=0.03)
    assert tied == pytest.approx(refined, rel=1e-4)


def test_calculix_hex8_compression_contact_equilibrium(tmp_path, ccx):
    # Separate blocks share face geometry and have distinct nodal identities.
    points = HEX + [(x, y, z + 1) for x, y, z in HEX]
    lines = ["*NODE"]
    lines.extend(f"{node},{x},{y},{z}" for node, (x, y, z) in enumerate(points, 1))
    lines.extend([
        "*ELEMENT,TYPE=C3D8,ELSET=BLOCKS", "1,1,2,3,4,5,6,7,8",
        "2,9,10,11,12,13,14,15,16",
        "*MATERIAL,NAME=ELASTIC", "*ELASTIC", "1000.,0.25",
        "*SOLID SECTION,ELSET=BLOCKS,MATERIAL=ELASTIC",
        "*NSET,NSET=BASE", "1,2,3,4", "*NSET,NSET=TOP", "13,14,15,16",
        "*NSET,NSET=LATERAL", "5,6,7,8,9,10,11,12,13,14,15,16",
        "*SURFACE,NAME=MAIN", "1,S2", "*SURFACE,NAME=SECONDARY", "2,S1",
        "*SURFACE INTERACTION,NAME=FRICTIONLESS",
        "*SURFACE BEHAVIOR,PRESSURE-OVERCLOSURE=LINEAR", "1.E5",
        "*CONTACT PAIR,INTERACTION=FRICTIONLESS,TYPE=SURFACE TO SURFACE", "SECONDARY,MAIN",
        "*BOUNDARY", "BASE,1,3", "LATERAL,1,2", "*STEP,NLGEOM", "*STATIC",
        "1.,1.,1.E-5,1.", "*CLOAD",
        "13,3,-0.25", "14,3,-0.25", "15,3,-0.25", "16,3,-0.25",
        "*NODE PRINT,NSET=BASE", "RF", "*NODE PRINT,NSET=TOP", "U",
        "*END STEP", "",
    ])
    (tmp_path / "model.inp").write_text("\n".join(lines))
    status, log = ccx(tmp_path)
    assert status == 0 and "Job finished" in log and "*ERROR" not in log, log
    dat = (tmp_path / "model.dat").read_text()
    # NLGEOM may print each increment. Check the final load level.
    last_increment = dat.rsplit("INCREMENT", 1)[-1]
    reactions = dat_rows(last_increment, "forces", 1, 3)
    assert len(reactions) == 4
    assert tuple(map(sum, zip(*(force for _, force in reactions)))) == pytest.approx(
        (0, 0, 1), abs=1e-6
    )
    top = dat_rows(last_increment, "displacements", 1, 3)
    # Constrained 3D modulus is 1200. Add the 1/1e5 penalty overlap.
    assert [value[2] for _, value in top] == pytest.approx(
        [-(2 / 1200 + 1 / 1e5)] * 4, rel=0.02
    )

def test_calculix_quadratic_conformal_hex_wedge_tet(tmp_path, ccx):
    # Elevate common edges exactly once. Load only the exterior with t = sigma . n.
    points = list(WEDGE_PATCH_POINTS)
    edges = {}
    elements = []
    faces = {}
    shapes = [
        (
            "C3D20",
            HEX_EDGES,
            [
                (0, 3, 2, 1),
                (4, 5, 6, 7),
                (0, 1, 5, 4),
                (1, 2, 6, 5),
                (2, 3, 7, 6),
                (3, 0, 4, 7),
            ],
        ),
        (
            "C3D15",
            WEDGE_EDGES,
            [(0, 2, 1), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)],
        ),
        ("C3D10", TET_EDGES, [(0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3)]),
    ]
    for (_, [row]), (kind, local_edges, local_faces) in zip(
        WEDGE_PATCH_ELEMENTS, shapes
    ):
        element, *corners = row
        mids = []
        for a, b in local_edges:
            key = tuple(sorted((corners[a], corners[b])))
            if key not in edges:
                points.append(midpoint(points[key[0] - 1], points[key[1] - 1]))
                edges[key] = len(points)
            mids.append(edges[key])
        elements.append((kind, [(element, *corners, *mids)]))
        for face in local_faces:
            nodes = tuple(corners[i] for i in face)
            faces.setdefault(tuple(sorted(nodes)), []).append(nodes)

    assert sorted(len(face) for face, copies in faces.items() if len(copies) == 2) == [
        3,
        4,
    ]
    force = dict.fromkeys(range(1, len(points) + 1), 0.0)
    for copies in faces.values():
        if len(copies) == 2:
            continue
        (face,) = copies
        sides = list(zip(face, face[1:] + face[:1]))
        area_z = (
            sum(
                points[a - 1][0] * points[b - 1][1]
                - points[a - 1][1] * points[b - 1][0]
                for a, b in sides
            )
            / 2
        )
        for a, b in sides:
            if len(face) == 4:
                force[a] -= area_z / 12
            force[edges[tuple(sorted((a, b)))]] += area_z / 3
    lines = ccx_mesh_lines(points, elements)
    lines.extend(["*STEP", "*STATIC", "*BOUNDARY", "1,1,3", "2,2,3", "4,3,3", "*CLOAD"])
    lines.extend(f"{node},3,{fz:.15g}" for node, fz in force.items() if fz)
    lines.extend(
        ["*EL PRINT,ELSET=BODY", "S", "*NODE PRINT,NSET=ALLN", "U,RF", "*END STEP", ""]
    )
    (tmp_path / "model.inp").write_text("\n".join(lines))
    status, log = ccx(tmp_path)
    assert status == 0 and "Job finished" in log and "*ERROR" not in log, log
    dat = (tmp_path / "model.dat").read_text()
    stress = dat_rows(dat, "stresses", 2, 6)
    assert [key for key, _ in stress] == [
        (eid, ip)
        for eid, count in [(1, 27), (2, 9), (3, 4)]
        for ip in range(1, count + 1)
    ]
    for _, values in stress:
        assert values == pytest.approx((0, 0, 1, 0, 0, 0), abs=1e-7)
    displacements = dat_rows(dat, "displacements", 1, 3)
    forces = dat_rows(dat, "forces", 1, 3)
    for rows in [displacements, forces]:
        assert [n for (n,), _ in rows] == list(range(1, len(points) + 1))
    for (node,), u in displacements:
        x, y, z = points[node - 1]
        assert u == pytest.approx((-0.00025 * x, -0.00025 * y, 0.001 * z), abs=1e-9)
    for (node,), rf in forces:
        assert rf == pytest.approx((0, 0, force[node]), abs=1e-7)


def rad_node_group(group, name, nodes):
    rows = ["".join(map(i10, nodes[i : i + 10])) for i in range(0, len(nodes), 10)]
    return [f"/GRNOD/NODE/{group}", name, *rows]


def rad_fixed(group, axes):
    return [
        f"/BCS/{group}",
        "displacement restraint",
        f"   {axes} 000" + i10(0) + i10(group),
    ]


def rad_velocity(group, axis, scale):
    return [
        f"/IMPVEL/{group}",
        "prescribed speed",
        i10(1) + f"{axis:>10s}" + i10(0) * 2 + i10(group),
        r20(1) + r20(scale),
    ]


def replace_rad_motion(folder, cards):
    path = folder / "model_0000.rad"
    geometry, _ = path.read_text().split("/GRNOD/NODE/1\n", maxsplit=1)
    path.write_text(geometry + "\n".join([*cards, "/END", ""]))


@pytest.mark.parametrize("kind", ["BRIC20", "TETRA10"])
def test_radioss_quadratic_constrained_compression(tmp_path, radioss, kind):
    from caelab.native.radioss import parse_radioss_output

    points, mass = RAD_CELLS[kind]
    write_rad_translation(
        tmp_path,
        points,
        [(kind, (1, *range(1, len(points) + 1)))],
        "bric20" if kind == "BRIC20" else "solid",
    )
    cards = [
        "/FUNCT/1",
        "constant velocity",
        r20(0) + r20(1),
        r20(1) + r20(1),
        *rad_node_group(1, "all nodes", list(range(1, len(points) + 1))),
        *rad_fixed(1, "011"),
    ]
    for group, x in enumerate([0, 0.5, 1], 2):
        ids = [node for node, point in enumerate(points, 1) if point[0] == x]
        cards.extend(rad_node_group(group, "x plane", ids))
        cards.extend(rad_velocity(group, "X", -x) if x else rad_fixed(group, "100"))
    replace_rad_motion(tmp_path, cards)
    execute, _ = radioss
    for step in ["0000", "0001"]:
        status, log = execute(tmp_path, step)
        assert status == 0 and "ERROR TERMINATION" not in log, log
    assert "NORMAL TERMINATION" in log
    values = parse_radioss_output(tmp_path / "model_0001.out")
    t = values["printed_time"]
    assert t > 5e-5
    # epsilon_x = -t; constrained modulus 1200 MPa. Printed cycle energies
    # are staggered relative to the Engine's printed time.
    expected = 0.5 * 1200 * t**2 * (mass / 1.0e-6)
    assert values["internal_energy"] == pytest.approx(expected, rel=0.06)
    assert values["mass_error"] == 0


@pytest.mark.parametrize(
    "kind,tied,axial",
    [
        ("C3D4", False, False),
        ("C3D10", False, False),
        ("C3D20", False, False),
        ("C3D8", True, False),
        ("C3D20", True, False),
        ("C3D8", True, True),
        ("C3D20", True, True),
    ],
)
def test_calculix_bending_and_nonmatching_tied_zones(tmp_path, ccx, kind, tied, axial):
    from mesher_bending_case import Beam
    from math import sqrt

    beam = Beam(kind, tied=tied, axial=axial)
    lines = ccx_mesh_lines(beam.points, list(beam.elements.items()))
    # nu=0 isolates bending, suppressing lateral contraction and clamp end effects.
    lines[lines.index("1000.,0.25")] = "1000.,0."
    if tied:
        lines += ["*SURFACE,NAME=CORE"]
        lines += [f"{eid},S4" for eid, _ in beam.master]
        lines += ["*NSET,NSET=SKIN_NODES"]
        lines += [
            ",".join(map(str, beam.slave[i : i + 16]))
            for i in range(0, len(beam.slave), 16)
        ]
        lines += [
            "*SURFACE,NAME=SKIN,TYPE=NODE",
            "SKIN_NODES",
            "*TIE,NAME=CORE_SKIN,POSITION TOLERANCE=1.E-7",
            "SKIN,CORE",
        ]
    lines += ["*STEP", "*STATIC", "*BOUNDARY"]
    lines += [f"{n},1,3" for n, (x, _, _) in enumerate(beam.points, 1) if x == 0]
    lines += ["*CLOAD"] + [
        f"{n},1,{f:.15g}" for n, f in beam.loads.items() if abs(f) > 1.0e-14
    ]
    lines += [
        "*EL PRINT,ELSET=BODY",
        "S",
        "*NODE PRINT,NSET=ALLN",
        "U,RF",
        "*END STEP",
        "",
    ]
    (tmp_path / "model.inp").write_text("\n".join(lines))
    code, log = ccx(tmp_path)
    assert code == 0 and "Job finished" in log and "*ERROR" not in log, log
    if tied:
        assert "WarnNodeMissMasterIntersect" not in log
        assert re.search(r"tie constraints:\s+1\b", log)
    dat = (tmp_path / "model.dat").read_text()
    u = {n: v for (n,), v in dat_rows(dat, "displacements", 1, 3)}
    assert len(u) == len(beam.points)
    tip = [v[0 if axial else 2] for n, v in u.items() if beam.points[n - 1][0] == 12]
    tip_mean = sum(tip) / len(tip)
    jump = beam.interface_jump(u)
    # DAT prints 7 significant digits; interpolating several rounded values
    # can accumulate more than one last printed digit.
    assert jump < 2.0e-8
    stress = {}
    for (eid, _), sigma in dat_rows(dat, "stresses", 2, 6):
        stress.setdefault(eid, []).append(sigma[0])
    assert len(stress) == len(beam.element_z)
    rms = sqrt(
        sum(
            (sum(v) / len(v) - (1 if axial else -beam.element_z[eid])) ** 2
            for eid, v in stress.items()
        )
        / len(stress)
    )
    if not tied and kind in ("C3D10", "C3D20"):
        # Compare the entire through-thickness stress distribution within each
        # straight cell, independently of the solver's quadrature point labels.
        for eid, *conn in beam.elements[kind]:
            if kind == "C3D20":
                z0, z1 = (beam.points[conn[i] - 1][2] for i in (0, 4))
                locations = [
                    (z0 + z1) / 2 + (z1 - z0) * r / 2
                    for r in (-sqrt(3 / 5), 0, sqrt(3 / 5))
                    for _ in range(9)
                ]
            else:
                z = [beam.points[n - 1][2] for n in conn[:4]]
                b = (5 - sqrt(5)) / 20
                locations = [b * sum(z) + (1 - 4 * b) * corner for corner in z]
            assert sorted(stress[eid]) == pytest.approx(
                sorted(-z for z in locations), abs=1e-6
            )
    reactions = [
        (beam.points[n - 1], v)
        for (n,), v in dat_rows(dat, "forces", 1, 3)
        if beam.points[n - 1][0] == 0
    ]
    assert sum(v[0] for _, v in reactions) == pytest.approx(
        -1.0 if axial else 0, abs=2e-6
    )
    moment = sum(p[2] * v[0] for p, v in reactions)
    assert moment == pytest.approx(0.0 if axial else 1 / 12, abs=2e-6)
    print(
        f"{kind} tied={tied} axial={axial}: tip={tip_mean:.9g} stress_rms={rms:.6g} jump={jump:.3g}"
    )
    if axial and tied and kind == "C3D8":
        # Nonmatching linear triangle/quad traces transfer axial load with
        # local interface stress error. Report it against the applied 1 MPa.
        assert tip == pytest.approx([0.012] * len(tip), rel=5.0e-4)
        assert rms < 0.005
    elif axial or kind in ("C3D10", "C3D20"):
        assert tip == pytest.approx([0.012 if axial else 0.072] * len(tip), abs=1.0e-7)
        assert rms < (5.0e-5 if tied else 1.0e-6)
    else:
        # First order stiffness error at fixed longitudinal and thickness resolution.
        assert 0 < tip_mean < 0.9 * 0.072


def test_radioss_type2_nonmatching_compression(tmp_path, radioss):
    from mesher_bending_case import Beam
    from caelab.native.radioss import collect_radioss_results, parse_radioss_output

    beam = Beam("C3D8", tied=True)
    starter = [
        "#RADIOSS STARTER",
        "/BEGIN",
        "model",
        i10(2024),
        "                  Mg                  mm                   s",
        "                  Mg                  mm                   s",
    ]
    for part, title in [(1, "hex core"), (2, "tet skin")]:
        starter += [f"/PART/{part}", title, i10(1) + i10(1) + i10(0)]
    starter += [
        *rad_property("solid"),
        "/MAT/LAW1/1",
        "elastic",
        r20(1.0e-6),
        r20(1000) + r20(0),
        "/NODE",
    ]
    starter += [i10(n) + "".join(map(r20, p)) for n, p in enumerate(beam.points, 1)]
    for kind, card in [("C3D8", "/BRICK/1"), ("C3D4", "/TETRA4/2")]:
        starter.append(card)
        starter += ["".join(map(i10, row)) for row in beam.elements[kind]]
    root = [n for n, (x, _, _) in enumerate(beam.points, 1) if x == 0]
    tip = [n for n, (x, _, _) in enumerate(beam.points, 1) if x == 12]
    starter += [
        *rad_node_group(1, "base", root),
        *rad_fixed(1, "111"),
        *rad_node_group(2, "tip", tip),
        *rad_velocity(2, "X", -1),
        "/FUNCT/1",
        "triangular speed history",
        r20(0) + r20(0),
        r20(0.006) + r20(2),
        r20(0.012) + r20(0),
        r20(1) + r20(0),
        *rad_node_group(3, "tet interface nodes", beam.slave),
        "/SURF/SEG/1",
        "core interface",
    ]
    starter += [
        i10(i) + "".join(map(i10, face)) for i, (_, face) in enumerate(beam.master, 1)
    ]
    starter += [
        "/INTER/TYPE2/1",
        "core to skin",
        i10(3) + i10(1) + i10(0) + i10(1),
        "/END",
        "",
    ]
    (tmp_path / "model_0000.rad").write_text("\n".join(starter))
    engine = [
        "/RUN/model/1",
        "0.0125",
        "/PRINT/10",
        "/ANIM/DT",
        "0. 0.003",
        "/ANIM/VECT/DISP",
        "/END",
        "",
    ]
    (tmp_path / "model_0001.rad").write_text("\n".join(engine))
    execute, converter_home = radioss
    for step in ["0000", "0001"]:
        status, log = execute(tmp_path, step)
        assert status == 0 and "ERROR TERMINATION" not in log, log
    assert "NORMAL TERMINATION" in log
    listing = (tmp_path / "model_0000.out").read_text(errors="replace")
    assert "CHECK TIED INTERFACE" not in listing
    values = parse_radioss_output(tmp_path / "model_0001.out")
    assert values["printed_time"] >= 0.012
    # Strain -0.001, E=1000 MPa, 12 mm^3. At rest the exact energy is 0.006 N mm.
    assert values["internal_energy"] == pytest.approx(0.006, rel=0.01)
    assert values["kinetic_energy"] < 0.006 * 1.0e-3
    assert values["mass_error"] == 0
    if not converter_home and os.name == "nt":
        converter_home = os.environ.get("CAELAB_OPENCOURANT")
    if converter_home:
        from xml.etree import ElementTree as ET

        results = collect_radioss_results(
            tmp_path, converter_installation=Path(converter_home)
        )
        assert results["status"] == "readable", results["warnings"]
        frame = results["frames"][-1]
        assert frame["time"] >= 0.0119
        tree = ET.parse(tmp_path / frame["vtu_file"])
        arrays = {a.get("Name"): a for a in tree.findall(".//PointData/DataArray")}
        # Converted animation indices are local. Resolve original identities by
        # reference coordinates AND zone so coincident tie nodes stay distinct.
        xyz = list(map(float, tree.find(".//Points/DataArray").text.split()))
        cells = {
            a.get("Name"): list(map(int, a.text.split()))
            for a in tree.findall(".//Cells/DataArray")
        }
        vector = list(map(float, arrays["DISP"].text.split()))
        domains = {}
        start = 0
        for end, vtk_type in zip(cells["offsets"], cells["types"]):
            assert vtk_type in (10, 12)
            for local in cells["connectivity"][start:end]:
                assert domains.setdefault(local, vtk_type) == vtk_type
            start = end
        disp = {}
        for local, vtk_type in domains.items():
            point = tuple(round(value, 4) for value in xyz[3 * local : 3 * local + 3])
            n = beam.node_ids[vtk_type == 10, point]
            assert n not in disp
            disp[n] = vector[3 * local : 3 * local + 3]
        assert len(disp) == len(beam.points)
        jump = beam.interface_jump(disp)
        error = max(
            abs(disp[n][0] + 0.001 * p[0]) for n, p in enumerate(beam.points, 1)
        )
        assert jump < 5.0e-6
        assert error < 0.012 * 0.01
        print(
            f"TYPE2 t={frame['time']} IE={values['internal_energy']} KE={values['kinetic_energy']} "
            f"max_axial_error={error:.5g} max_jump={jump:.5g}"
        )


@pytest.mark.parametrize(
    "kind,a,b,error", [("TETRA10", 5, 10, 489), ("BRIC20", 9, 19, 484)]
)
def test_radioss_rejects_swapped_quadratic_midsides(
    tmp_path, radioss, kind, a, b, error
):
    points, _ = RAD_CELLS[kind]
    nodes = list(range(1, len(points) + 1))
    nodes[a - 1], nodes[b - 1] = nodes[b - 1], nodes[a - 1]
    write_rad_translation(
        tmp_path,
        points,
        [(kind, (1, *nodes))],
        "bric20" if kind == "BRIC20" else "solid",
    )
    execute, _ = radioss
    code, log = execute(tmp_path, "0000")
    assert code != 0 and "ERROR TERMINATION" in log
    assert re.search(rf"ERROR ID\s*:\s*{error}\b", log)
    assert not list(tmp_path.glob("model_0000*.rst"))
