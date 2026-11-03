"""Tests for MSH 4.1 straight Tet10 -> CalculiX C3D10 converter."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from caelab.adapters.msh_c3d10 import (
    MshC3D10Error,
    convert_msh41_to_c3d10_file,
    parse_c3d10_deck,
    read_msh41_straight_tet10,
    verify_sidecar,
)

FIXED_L_MSH_SHA = "045acf69709618a849cd6aca028999bcb5012766a8da27ff8fbba79087d4980b"
FIXED_L_SIDECAR_SHA = "52fc715c56b473d38ab46ca51e59a170c7d2cbb47bd8a408fa88557e52004926"


def get_fixed_l_dir() -> Path | None:
    """Retrieve external fixed L asset directory from environment or user cache."""
    override = os.environ.get("CAELAB_FIXED_L_ASSET_DIR")
    if override:
        p = Path(override).expanduser().resolve()
        if p.is_dir():
            return p

    default_path = (
        Path.home()
        / ".codex"
        / "visualizations"
        / "2026"
        / "11"
        / "03"
        / "surface-autotet"
        / "prepared-tet10-native-L-probe"
        / "captured"
        / "msh-d290fa6e-e1af-42b1-a593-75f5c3107dd0"
    )
    if default_path.is_dir():
        return default_path
    return None


@pytest.fixture
def synthetic_msh_fixture(tmp_path: Path):
    """Create a minimal valid synthetic MSH 4.1 Tet10 file and its sidecar."""
    msh_file = tmp_path / "valid.msh"
    sidecar_file = tmp_path / "metadata.json"

    # Single Tet10 element: 10 nodes (corners 1-4, midsides 5-10)
    # Gmsh Tet10 midside: 5(1-2), 6(2-3), 7(3-1), 8(1-4), 9(3-4), 10(2-4)
    # Coordinates:
    # 1: (0,0,0), 2: (1,0,0), 3: (0,1,0), 4: (0,0,1)
    # 5: (0.5,0,0), 6: (0.5,0.5,0), 7: (0,0.5,0), 8: (0,0,0.5), 9: (0,0.5,0.5), 10: (0.5,0,0.5)
    nodes_content = """$MeshFormat
4.1 0 8
$EndMeshFormat
$Nodes
1 10 1 10
3 1 0 10
1
2
3
4
5
6
7
8
9
10
0.0 0.0 0.0
1.0 0.0 0.0
0.0 1.0 0.0
0.0 0.0 1.0
0.5 0.0 0.0
0.5 0.5 0.0
0.0 0.5 0.0
0.0 0.0 0.5
0.0 0.5 0.5
0.5 0.0 0.5
$EndNodes
$Elements
1 1 100 100
3 1 11 1
100 1 2 3 4 5 6 7 8 9 10
$EndElements
"""
    msh_file.write_text(nodes_content, encoding="utf-8")
    msh_hash = hashlib.sha256(msh_file.read_bytes()).hexdigest()

    sidecar_data = {
        "schema": "caelab.standalone-msh",
        "version": 1,
        "mesh_uuid": "synthetic-uuid-001",
        "length_unit": "mm",
        "format_version": "4.1",
        "element_profile": "straight_tet10",
        "source": {
            "path": str(msh_file),
            "sha256": msh_hash,
        },
    }
    sidecar_file.write_text(json.dumps(sidecar_data, indent=2), encoding="utf-8")
    return msh_file, sidecar_file


def test_fixed_l_asset_exact_conversion_and_node_swap(tmp_path: Path):
    """Test reading the actual fixed L asset produced by Prep, verifying slots 9/10 swap and lossless coordinates."""
    asset_dir = get_fixed_l_dir()
    if asset_dir is None:
        pytest.skip("External fixed L asset directory not found in CAELAB_FIXED_L_ASSET_DIR or ~/.codex")

    msh_path = asset_dir / "input.msh"
    sidecar_path = asset_dir / "metadata.json"

    if not msh_path.is_file():
        pytest.skip(f"Fixed L input.msh missing in {asset_dir}")

    # Verify SHA256 matches exact known hashes
    assert hashlib.sha256(msh_path.read_bytes()).hexdigest().lower() == FIXED_L_MSH_SHA
    assert hashlib.sha256(sidecar_path.read_bytes()).hexdigest().lower() == FIXED_L_SIDECAR_SHA

    mesh = read_msh41_straight_tet10(msh_path, sidecar_path)

    # Check counts
    assert len(mesh.nodes) == 87
    assert len(mesh.elements) == 36
    assert len(mesh.tri6_boundary) == 28

    # Non-continuous node IDs check (e.g. 101, 730)
    assert 101 in mesh.nodes
    assert 730 in mesh.nodes

    # Check element 978 mid-side swap:
    # In raw MSH: 978 323 249 582 660 674 691 690 705 706 707
    # Slot 9 is 706, Slot 10 is 707.
    # In C3D10: Slot 9 must be 707, Slot 10 must be 706.
    c3d10_elem978 = mesh.elements[978]
    assert c3d10_elem978 == [323, 249, 582, 660, 674, 691, 690, 705, 707, 706]

    # Convert to INP deck
    out_inp = tmp_path / "c3d10_include.inp"
    convert_msh41_to_c3d10_file(msh_path, out_inp, sidecar_path)

    # S2: Verify 100% exact lossless coordinate string match
    inp_lines = out_inp.read_text(encoding="utf-8").splitlines()
    in_node_sec = False
    node_lines = []
    for l in inp_lines:
        s = l.strip()
        if s.startswith("*NODE"):
            in_node_sec = True
            continue
        if in_node_sec and s.startswith("*"):
            in_node_sec = False
            break
        if in_node_sec and s:
            node_lines.append(s)
    assert len(node_lines) == 87

    # Re-parse generated deck and verify integrity
    parsed_nodes, parsed_elements = parse_c3d10_deck(out_inp)
    assert len(parsed_nodes) == 87
    assert len(parsed_elements) == 36
    assert parsed_elements[978] == [323, 249, 582, 660, 674, 691, 690, 705, 707, 706]

    # Check that all parsed float coordinates match original mesh coordinates with zero float drift
    for nid, orig_coords in mesh.nodes.items():
        assert parsed_nodes[nid] == orig_coords


def test_synthetic_minimal_tet10_swap(synthetic_msh_fixture, tmp_path: Path):
    """Test minimal synthetic Tet10 conversion and mid-side swap."""
    msh_file, sidecar_file = synthetic_msh_fixture
    mesh = read_msh41_straight_tet10(msh_file, sidecar_file)

    assert len(mesh.nodes) == 10
    assert len(mesh.elements) == 1

    # Raw nodes: 1 2 3 4 5 6 7 8 9 10
    # Converted nodes: 1 2 3 4 5 6 7 8 10 9
    assert mesh.elements[100] == [1, 2, 3, 4, 5, 6, 7, 8, 10, 9]

    out_inp = tmp_path / "synthetic_c3d10.inp"
    convert_msh41_to_c3d10_file(msh_file, out_inp, sidecar_file)

    parsed_nodes, parsed_elements = parse_c3d10_deck(out_inp)
    assert len(parsed_nodes) == 10
    assert parsed_elements[100] == [1, 2, 3, 4, 5, 6, 7, 8, 10, 9]


def test_fail_closed_target_aliases_source(synthetic_msh_fixture):
    """R1: Fail closed if target INP path aliases source MSH or sidecar metadata file."""
    msh_file, sidecar_file = synthetic_msh_fixture
    orig_msh_bytes = msh_file.read_bytes()
    orig_sidecar_bytes = sidecar_file.read_bytes()

    # Target is source MSH
    with pytest.raises(MshC3D10Error, match="aliases source MSH file"):
        convert_msh41_to_c3d10_file(msh_file, msh_file, sidecar_file)
    assert msh_file.read_bytes() == orig_msh_bytes

    # Target is sidecar JSON
    with pytest.raises(MshC3D10Error, match="aliases sidecar metadata file"):
        convert_msh41_to_c3d10_file(msh_file, sidecar_file, sidecar_file)
    assert sidecar_file.read_bytes() == orig_sidecar_bytes


def test_fail_closed_curved_midside_deviation(synthetic_msh_fixture):
    """R3: Fail closed when midside node deviates from straight edge midpoint."""
    msh_file, sidecar_file = synthetic_msh_fixture
    content = msh_file.read_text(encoding="utf-8")
    # Move node 5 from (0.5, 0.0, 0.0) to (0.5, 0.1, 0.0)
    modified = content.replace("0.5 0.0 0.0", "0.5 0.1 0.0")
    msh_file.write_text(modified, encoding="utf-8")

    # Update sidecar hash to pass SHA check and isolate R3 admission check
    sidecar_data = json.loads(sidecar_file.read_text(encoding="utf-8"))
    sidecar_data["source"]["sha256"] = hashlib.sha256(msh_file.read_bytes()).hexdigest()
    sidecar_file.write_text(json.dumps(sidecar_data), encoding="utf-8")

    with pytest.raises(MshC3D10Error, match="violates straight profile"):
        read_msh41_straight_tet10(msh_file, sidecar_file)


def test_fail_closed_degenerate_or_negative_jacobian(synthetic_msh_fixture):
    """R3: Fail closed when element has non-positive Jacobian volume."""
    msh_file, sidecar_file = synthetic_msh_fixture
    content = msh_file.read_text(encoding="utf-8")
    # Swap vertex 1 and 2 to invert tetrahedron orientation
    modified = content.replace("100 1 2 3 4 5 6 7 8 9 10", "100 2 1 3 4 5 6 7 8 9 10")
    msh_file.write_text(modified, encoding="utf-8")

    sidecar_data = json.loads(sidecar_file.read_text(encoding="utf-8"))
    sidecar_data["source"]["sha256"] = hashlib.sha256(msh_file.read_bytes()).hexdigest()
    sidecar_file.write_text(json.dumps(sidecar_data), encoding="utf-8")

    with pytest.raises(MshC3D10Error, match="non-positive or degenerate Jacobian volume"):
        read_msh41_straight_tet10(msh_file, sidecar_file)


def test_fail_closed_duplicate_node_tag_in_block(synthetic_msh_fixture):
    """R4: Fail closed when node tag is duplicated within the same block."""
    msh_file, sidecar_file = synthetic_msh_fixture
    content = msh_file.read_text(encoding="utf-8")
    # Duplicate node tag 1 by replacing tag 2 with 1
    # Line '2' in node tag listing
    lines = content.splitlines()
    # Find line with '2' in node tags
    idx = lines.index("1") + 1
    assert lines[idx] == "2"
    lines[idx] = "1"
    msh_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    sidecar_data = json.loads(sidecar_file.read_text(encoding="utf-8"))
    sidecar_data["source"]["sha256"] = hashlib.sha256(msh_file.read_bytes()).hexdigest()
    sidecar_file.write_text(json.dumps(sidecar_data), encoding="utf-8")

    with pytest.raises(MshC3D10Error, match="Duplicate node tag encountered"):
        read_msh41_straight_tet10(msh_file, sidecar_file)


def test_fail_closed_tri6_undefined_node_reference(synthetic_msh_fixture):
    """R4: Fail closed when Tri6 boundary element references undefined node."""
    msh_file, sidecar_file = synthetic_msh_fixture
    content = msh_file.read_text(encoding="utf-8")
    # Add a Tri6 element referencing node 99999
    tri6_section = """$Elements
2 2 100 200
3 1 11 1
100 1 2 3 4 5 6 7 8 9 10
2 1 9 1
200 1 2 3 5 6 99999
$EndElements
"""
    idx_elem = content.index("$Elements")
    modified = content[:idx_elem] + tri6_section
    msh_file.write_text(modified, encoding="utf-8")

    sidecar_data = json.loads(sidecar_file.read_text(encoding="utf-8"))
    sidecar_data["source"]["sha256"] = hashlib.sha256(msh_file.read_bytes()).hexdigest()
    sidecar_file.write_text(json.dumps(sidecar_data), encoding="utf-8")

    with pytest.raises(MshC3D10Error, match="Tri6 element 200 references undefined node 99999"):
        read_msh41_straight_tet10(msh_file, sidecar_file)


def test_fail_closed_element_tag_collision(synthetic_msh_fixture):
    """R4: Fail closed when element tag collides between Tet10 and Tri6."""
    msh_file, sidecar_file = synthetic_msh_fixture
    content = msh_file.read_text(encoding="utf-8")
    tri6_section = """$Elements
2 2 100 100
3 1 11 1
100 1 2 3 4 5 6 7 8 9 10
2 1 9 1
100 1 2 3 5 6 7
$EndElements
"""
    idx_elem = content.index("$Elements")
    modified = content[:idx_elem] + tri6_section
    msh_file.write_text(modified, encoding="utf-8")

    sidecar_data = json.loads(sidecar_file.read_text(encoding="utf-8"))
    sidecar_data["source"]["sha256"] = hashlib.sha256(msh_file.read_bytes()).hexdigest()
    sidecar_file.write_text(json.dumps(sidecar_data), encoding="utf-8")

    with pytest.raises(MshC3D10Error, match="Duplicate element tag / collision with Tet10: 100"):
        read_msh41_straight_tet10(msh_file, sidecar_file)


def test_fail_closed_mesh_format_binary(synthetic_msh_fixture):
    """R5: Fail closed when $MeshFormat indicates binary file-type '1'."""
    msh_file, sidecar_file = synthetic_msh_fixture
    content = msh_file.read_text(encoding="utf-8")
    modified = content.replace("4.1 0 8", "4.1 1 8")
    msh_file.write_text(modified, encoding="utf-8")

    sidecar_data = json.loads(sidecar_file.read_text(encoding="utf-8"))
    sidecar_data["source"]["sha256"] = hashlib.sha256(msh_file.read_bytes()).hexdigest()
    sidecar_file.write_text(json.dumps(sidecar_data), encoding="utf-8")

    with pytest.raises(MshC3D10Error, match="Expected ASCII MSH file"):
        read_msh41_straight_tet10(msh_file, sidecar_file)


def test_fail_closed_missing_sidecar(tmp_path: Path):
    """Fail closed when sidecar file is absent."""
    msh_file = tmp_path / "alone.msh"
    msh_file.write_text("$MeshFormat\n4.1 0 8\n$EndMeshFormat\n", encoding="utf-8")

    with pytest.raises(MshC3D10Error, match="adjacent metadata.json not found"):
        read_msh41_straight_tet10(msh_file)


def test_fail_closed_sidecar_hash_mismatch(synthetic_msh_fixture):
    """Fail closed when MSH file bytes don't match sidecar SHA256."""
    msh_file, sidecar_file = synthetic_msh_fixture
    data = json.loads(sidecar_file.read_text(encoding="utf-8"))
    data["source"]["sha256"] = "0000000000000000000000000000000000000000000000000000000000000000"
    sidecar_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(MshC3D10Error, match="SHA256 mismatch"):
        read_msh41_straight_tet10(msh_file, sidecar_file)


def test_fail_closed_unsupported_length_unit(synthetic_msh_fixture):
    """Fail closed if length_unit is not mm."""
    msh_file, sidecar_file = synthetic_msh_fixture
    data = json.loads(sidecar_file.read_text(encoding="utf-8"))
    data["length_unit"] = "m"
    sidecar_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(MshC3D10Error, match="Unsupported length_unit 'm'"):
        read_msh41_straight_tet10(msh_file, sidecar_file)


def test_fail_closed_unsupported_element_profile(synthetic_msh_fixture):
    """Fail closed if element_profile is not straight_tet10."""
    msh_file, sidecar_file = synthetic_msh_fixture
    data = json.loads(sidecar_file.read_text(encoding="utf-8"))
    data["element_profile"] = "curved_tet10"
    sidecar_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(MshC3D10Error, match="Expected element_profile 'straight_tet10'"):
        read_msh41_straight_tet10(msh_file, sidecar_file)


def test_fail_closed_unsupported_format_version(synthetic_msh_fixture):
    """Fail closed if format_version is not 4.1."""
    msh_file, sidecar_file = synthetic_msh_fixture
    data = json.loads(sidecar_file.read_text(encoding="utf-8"))
    data["format_version"] = "2.2"
    sidecar_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(MshC3D10Error, match="Expected format_version '4.1'"):
        read_msh41_straight_tet10(msh_file, sidecar_file)


def test_fail_closed_negative_node_tag(tmp_path: Path):
    """Fail closed when node tag is negative."""
    msh_file = tmp_path / "bad_node.msh"
    sidecar_file = tmp_path / "metadata.json"
    content = """$MeshFormat
4.1 0 8
$EndMeshFormat
$Nodes
1 1 1 1
3 1 0 1
-5
0.0 0.0 0.0
$EndNodes
$Elements
0 0 0 0
$EndElements
"""
    msh_file.write_text(content, encoding="utf-8")
    sidecar_data = {
        "schema": "caelab.standalone-msh",
        "version": 1,
        "length_unit": "mm",
        "format_version": "4.1",
        "element_profile": "straight_tet10",
        "source": {"sha256": hashlib.sha256(msh_file.read_bytes()).hexdigest()},
    }
    sidecar_file.write_text(json.dumps(sidecar_data), encoding="utf-8")

    with pytest.raises(MshC3D10Error, match="Invalid non-positive node tag"):
        read_msh41_straight_tet10(msh_file, sidecar_file)


def test_fail_closed_undefined_node_reference(tmp_path: Path):
    """Fail closed when element references non-existent node ID."""
    msh_file = tmp_path / "dangling.msh"
    sidecar_file = tmp_path / "metadata.json"
    content = """$MeshFormat
4.1 0 8
$EndMeshFormat
$Nodes
1 1 1 1
3 1 0 1
1
0.0 0.0 0.0
$EndNodes
$Elements
1 1 1 1
3 1 11 1
1 1 2 3 4 5 6 7 8 9 10
$EndElements
"""
    msh_file.write_text(content, encoding="utf-8")
    sidecar_data = {
        "schema": "caelab.standalone-msh",
        "version": 1,
        "length_unit": "mm",
        "format_version": "4.1",
        "element_profile": "straight_tet10",
        "source": {"sha256": hashlib.sha256(msh_file.read_bytes()).hexdigest()},
    }
    sidecar_file.write_text(json.dumps(sidecar_data), encoding="utf-8")

    with pytest.raises(MshC3D10Error, match="Tet10 element 1 references undefined node"):
        read_msh41_straight_tet10(msh_file, sidecar_file)
