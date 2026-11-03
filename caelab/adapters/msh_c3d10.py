"""Gmsh MSH 4.1 straight Tet10 -> CalculiX C3D10 converter.

Enforces:
1. Valid caelab.standalone-msh sidecar metadata (length_unit == 'mm', schema/version, SHA256 match).
2. Pure MSH 4.1 ASCII format ($MeshFormat '4.1 0 8'), positive integer IDs, no duplicate nodes or elements.
3. Strict admission: Tet10 (Gmsh type 11) physical solids only; Tri6 (type 9) boundary tags identified separately.
4. Straight profile verification: finite coordinates, distinct node references, straight midside node placement, positive Jacobian volume.
5. Exact 1-time mid-side swap: raw Gmsh type 11 (12, 23, 31, 14, 34, 24) -> C3D10 (12, 23, 31, 14, 24, 34) (slots 9 and 10).
6. Lossless coordinate preservation and safe atomic writing preventing source file aliasing/overwrites.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import NamedTuple


class MshC3D10Error(ValueError):
    """Raised when MSH 4.1 or sidecar validation fails."""
    pass


class ConvertedMesh(NamedTuple):
    nodes: dict[int, tuple[float, float, float]]
    elements: dict[int, list[int]]
    tri6_boundary: dict[int, list[int]]
    metadata: dict
    raw_node_coords: dict[int, tuple[str, str, str]] | None = None


def verify_sidecar(msh_path: Path, sidecar_path: Path, msh_bytes: bytes | None = None) -> dict:
    """Verify standalone MSH sidecar metadata and SHA256 integrity against provided or read bytes."""
    if not sidecar_path.is_file():
        raise MshC3D10Error(f"Missing sidecar metadata file: {sidecar_path}")
    try:
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MshC3D10Error(f"Malformed sidecar JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise MshC3D10Error("Sidecar root must be a JSON object")

    if data.get("schema") != "caelab.standalone-msh" or data.get("version") != 1:
        raise MshC3D10Error(f"Unsupported sidecar schema/version: {data.get('schema')}/{data.get('version')}")

    if data.get("format_version") != "4.1":
        raise MshC3D10Error(f"Expected format_version '4.1', got: {data.get('format_version')}")

    if data.get("length_unit") != "mm":
        raise MshC3D10Error(f"Unsupported length_unit '{data.get('length_unit')}'; only 'mm' is supported")

    if data.get("element_profile") != "straight_tet10":
        raise MshC3D10Error(f"Expected element_profile 'straight_tet10', got: {data.get('element_profile')}")

    source = data.get("source")
    if not isinstance(source, dict) or "sha256" not in source:
        raise MshC3D10Error("Missing source.sha256 in sidecar metadata")

    expected_sha = source["sha256"].lower()
    if msh_bytes is None:
        msh_bytes = msh_path.read_bytes()
    actual_sha = hashlib.sha256(msh_bytes).hexdigest().lower()
    if actual_sha != expected_sha:
        raise MshC3D10Error(f"SHA256 mismatch for {msh_path.name}: expected {expected_sha}, got {actual_sha}")

    return data


def _check_straight_tet10(
    etag: int,
    raw_nodes: list[int],
    nodes: dict[int, tuple[float, float, float]],
) -> None:
    """Validate finite coordinates, positive Jacobian volume, and straight midside node placement."""
    coords = [nodes[n] for n in raw_nodes]

    # Vertices: 0: V0, 1: V1, 2: V2, 3: V3
    v0, v1, v2, v3 = coords[0], coords[1], coords[2], coords[3]

    # Scalar triple product: ((v1 - v0) x (v2 - v0)) . (v3 - v0)
    a = (v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2])
    b = (v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2])
    c = (v3[0] - v0[0], v3[1] - v0[1], v3[2] - v0[2])
    cross = (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )
    det = cross[0] * c[0] + cross[1] * c[1] + cross[2] * c[2]
    vol = det / 6.0
    if vol <= 1e-15:
        raise MshC3D10Error(
            f"Tet10 element {etag} has non-positive or degenerate Jacobian volume: {vol:.6g}"
        )

    # Gmsh Tet10 midside edge bindings:
    # 4: mid(0,1), 5: mid(1,2), 6: mid(2,0)
    # 7: mid(0,3), 8: mid(2,3), 9: mid(1,3)
    edges = [
        (0, 1, 4),
        (1, 2, 5),
        (2, 0, 6),
        (0, 3, 7),
        (2, 3, 8),
        (1, 3, 9),
    ]
    for v_a_idx, v_b_idx, m_idx in edges:
        p_a = coords[v_a_idx]
        p_b = coords[v_b_idx]
        p_m = coords[m_idx]
        exp_m = (
            0.5 * (p_a[0] + p_b[0]),
            0.5 * (p_a[1] + p_b[1]),
            0.5 * (p_a[2] + p_b[2]),
        )
        edge_len = math.dist(p_a, p_b)
        dist_to_mid = math.dist(p_m, exp_m)
        rel_dev = dist_to_mid / edge_len if edge_len > 0 else dist_to_mid
        if dist_to_mid > 1e-5 and rel_dev > 1e-4:
            raise MshC3D10Error(
                f"Tet10 element {etag} violates straight profile: "
                f"midside node {raw_nodes[m_idx]} deviates from edge ({raw_nodes[v_a_idx]}, {raw_nodes[v_b_idx]}) "
                f"midpoint by {dist_to_mid:.6g} (rel_dev={rel_dev:.6g})"
            )


def read_msh41_straight_tet10(
    msh_path: Path | str,
    sidecar_path: Path | str | None = None,
) -> ConvertedMesh:
    """Read MSH 4.1 ASCII straight Tet10 mesh, validating sidecar and converting node ordering."""
    msh_path = Path(msh_path).expanduser().resolve()
    if not msh_path.is_file():
        raise MshC3D10Error(f"MSH file not found: {msh_path}")

    if sidecar_path is None:
        adjacent = msh_path.with_name("metadata.json")
        if not adjacent.is_file():
            raise MshC3D10Error(f"No sidecar provided and adjacent metadata.json not found for: {msh_path}")
        sidecar_path = adjacent
    else:
        sidecar_path = Path(sidecar_path).expanduser().resolve()

    # R2: Single-read byte consumption for both hash verification and parsing
    try:
        msh_bytes = msh_path.read_bytes()
    except Exception as exc:
        raise MshC3D10Error(f"Cannot read MSH file: {exc}") from exc

    metadata = verify_sidecar(msh_path, sidecar_path, msh_bytes=msh_bytes)

    try:
        content = msh_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MshC3D10Error(f"MSH file is not valid UTF-8/ASCII: {exc}") from exc

    lines = [line.strip() for line in content.splitlines()]
    total_lines = len(lines)
    cursor = 0

    # 1. $MeshFormat (R5: Verify ASCII 4.1 0 8)
    while cursor < total_lines and lines[cursor] != "$MeshFormat":
        cursor += 1
    if cursor >= total_lines:
        raise MshC3D10Error("Missing $MeshFormat section")
    cursor += 1
    if cursor >= total_lines:
        raise MshC3D10Error("Premature EOF in $MeshFormat")
    fmt_tokens = lines[cursor].split()
    if len(fmt_tokens) < 3:
        raise MshC3D10Error(f"Malformed $MeshFormat header: '{lines[cursor]}'")
    version, file_type, data_size = fmt_tokens[0], fmt_tokens[1], fmt_tokens[2]
    if version != "4.1":
        raise MshC3D10Error(f"Expected MSH format version '4.1', got: '{version}'")
    if file_type != "0":
        raise MshC3D10Error(f"Expected ASCII MSH file (file-type '0'), got: '{file_type}'")
    if data_size != "8":
        raise MshC3D10Error(f"Expected data-size '8', got: '{data_size}'")

    cursor += 1
    while cursor < total_lines and lines[cursor] != "$EndMeshFormat":
        cursor += 1
    if cursor >= total_lines:
        raise MshC3D10Error("Unterminated $MeshFormat section")
    cursor += 1

    # 2. $Nodes (R4: duplicate detection within and across blocks, finite coords)
    while cursor < total_lines and lines[cursor] != "$Nodes":
        cursor += 1
    if cursor >= total_lines:
        raise MshC3D10Error("Missing $Nodes section")
    cursor += 1
    if cursor >= total_lines:
        raise MshC3D10Error("Premature EOF in $Nodes header")

    nodes_header = lines[cursor].split()
    cursor += 1
    if len(nodes_header) < 4:
        raise MshC3D10Error(f"Malformed $Nodes header: {' '.join(nodes_header)}")
    num_entity_blocks = int(nodes_header[0])
    num_nodes = int(nodes_header[1])

    nodes: dict[int, tuple[float, float, float]] = {}
    raw_node_coords: dict[int, tuple[str, str, str]] = {}
    seen_node_tags: set[int] = set()

    for _ in range(num_entity_blocks):
        if cursor >= total_lines:
            raise MshC3D10Error("Premature EOF in $Nodes entity block header")
        block_hdr = lines[cursor].split()
        cursor += 1
        if len(block_hdr) < 4:
            raise MshC3D10Error(f"Malformed node block header: {' '.join(block_hdr)}")
        num_nodes_in_block = int(block_hdr[3])

        # Node tags
        block_node_tags: list[int] = []
        for _ in range(num_nodes_in_block):
            if cursor >= total_lines:
                raise MshC3D10Error("Premature EOF reading node tags")
            tag = int(lines[cursor])
            if tag <= 0:
                raise MshC3D10Error(f"Invalid non-positive node tag: {tag}")
            if tag in seen_node_tags:
                raise MshC3D10Error(f"Duplicate node tag encountered: {tag}")
            seen_node_tags.add(tag)
            block_node_tags.append(tag)
            cursor += 1

        # Node coordinates
        for tag in block_node_tags:
            if cursor >= total_lines:
                raise MshC3D10Error("Premature EOF reading node coordinates")
            coords = lines[cursor].split()
            cursor += 1
            if len(coords) != 3:
                raise MshC3D10Error(f"Expected 3 coordinates for node {tag}, got {len(coords)}")
            x, y, z = float(coords[0]), float(coords[1]), float(coords[2])
            if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
                raise MshC3D10Error(f"Non-finite coordinate for node {tag}: ({x}, {y}, {z})")
            nodes[tag] = (x, y, z)
            raw_node_coords[tag] = (coords[0], coords[1], coords[2])

    while cursor < total_lines and lines[cursor] != "$EndNodes":
        cursor += 1
    if cursor >= total_lines:
        raise MshC3D10Error("Unterminated $Nodes section")
    cursor += 1

    if len(nodes) != num_nodes:
        raise MshC3D10Error(f"Node count mismatch: expected {num_nodes}, parsed {len(nodes)}")

    # 3. $Elements (R3: straight admission & positive Jacobian; R4: Tri6 & cross-element collision check)
    while cursor < total_lines and lines[cursor] != "$Elements":
        cursor += 1
    if cursor >= total_lines:
        raise MshC3D10Error("Missing $Elements section")
    cursor += 1
    if cursor >= total_lines:
        raise MshC3D10Error("Premature EOF in $Elements header")

    elem_header = lines[cursor].split()
    cursor += 1
    if len(elem_header) < 4:
        raise MshC3D10Error(f"Malformed $Elements header: {' '.join(elem_header)}")
    num_elem_blocks = int(elem_header[0])
    num_elements = int(elem_header[1])

    c3d10_elements: dict[int, list[int]] = {}
    tri6_boundary: dict[int, list[int]] = {}
    all_elem_tags: set[int] = set()
    parsed_total_elements = 0

    for _ in range(num_elem_blocks):
        if cursor >= total_lines:
            raise MshC3D10Error("Premature EOF in $Elements block header")
        b_hdr = lines[cursor].split()
        cursor += 1
        if len(b_hdr) < 4:
            raise MshC3D10Error(f"Malformed element block header: {' '.join(b_hdr)}")
        elem_dim = int(b_hdr[0])
        elem_type = int(b_hdr[2])
        num_elems_in_block = int(b_hdr[3])

        if elem_type == 11:
            # 10-node second-order tetrahedron (Tet10)
            if elem_dim != 3:
                raise MshC3D10Error(f"Tet10 element block has invalid dimension {elem_dim}")
            for _ in range(num_elems_in_block):
                if cursor >= total_lines:
                    raise MshC3D10Error("Premature EOF reading Tet10 elements")
                parts = lines[cursor].split()
                cursor += 1
                if len(parts) != 11:
                    raise MshC3D10Error(f"Expected 1 tag + 10 nodes for Tet10, got {len(parts)}: {' '.join(parts)}")
                etag = int(parts[0])
                if etag <= 0:
                    raise MshC3D10Error(f"Invalid non-positive element tag: {etag}")
                if etag in all_elem_tags:
                    raise MshC3D10Error(f"Duplicate element tag: {etag}")
                all_elem_tags.add(etag)

                raw_nodes = [int(p) for p in parts[1:]]
                if len(set(raw_nodes)) != 10:
                    raise MshC3D10Error(f"Tet10 element {etag} contains duplicate node references: {raw_nodes}")

                for nid in raw_nodes:
                    if nid not in nodes:
                        raise MshC3D10Error(f"Tet10 element {etag} references undefined node {nid}")

                # R3: Straight Tet10 geometry & positive Jacobian volume check
                _check_straight_tet10(etag, raw_nodes, nodes)

                # Exact 1-time swap: Gmsh slot 9 and 10 (0-based indices 8 and 9)
                # raw Gmsh: (12, 23, 31, 14, 34, 24) -> CalculiX: (12, 23, 31, 14, 24, 34)
                c3d10_nodes = [
                    raw_nodes[0], raw_nodes[1], raw_nodes[2], raw_nodes[3],
                    raw_nodes[4], raw_nodes[5], raw_nodes[6], raw_nodes[7],
                    raw_nodes[9], raw_nodes[8],
                ]
                c3d10_elements[etag] = c3d10_nodes
                parsed_total_elements += 1

        elif elem_type == 9:
            # 6-node second-order triangle (Tri6 boundary annotation)
            if elem_dim != 2:
                raise MshC3D10Error(f"Tri6 boundary element block has invalid dimension {elem_dim}")
            for _ in range(num_elems_in_block):
                if cursor >= total_lines:
                    raise MshC3D10Error("Premature EOF reading Tri6 elements")
                parts = lines[cursor].split()
                cursor += 1
                if len(parts) != 7:
                    raise MshC3D10Error(f"Expected 1 tag + 6 nodes for Tri6, got {len(parts)}")
                etag = int(parts[0])
                if etag <= 0:
                    raise MshC3D10Error(f"Invalid non-positive Tri6 element tag: {etag}")
                if etag in all_elem_tags:
                    raise MshC3D10Error(f"Duplicate element tag / collision with Tet10: {etag}")
                all_elem_tags.add(etag)

                raw_nodes = [int(p) for p in parts[1:]]
                if len(set(raw_nodes)) != 6:
                    raise MshC3D10Error(f"Tri6 element {etag} contains duplicate node references: {raw_nodes}")
                for nid in raw_nodes:
                    if nid not in nodes:
                        raise MshC3D10Error(f"Tri6 element {etag} references undefined node {nid}")

                tri6_boundary[etag] = raw_nodes
                parsed_total_elements += 1
        else:
            raise MshC3D10Error(f"Unsupported element type {elem_type} in straight Tet10 profile")

    while cursor < total_lines and lines[cursor] != "$EndElements":
        cursor += 1
    if cursor >= total_lines:
        raise MshC3D10Error("Unterminated $Elements section")

    if parsed_total_elements != num_elements:
        raise MshC3D10Error(f"Element count mismatch: header {num_elements}, parsed {parsed_total_elements}")

    if not c3d10_elements:
        raise MshC3D10Error("No Tet10 physical solid elements found in mesh")

    return ConvertedMesh(
        nodes=nodes,
        elements=c3d10_elements,
        tri6_boundary=tri6_boundary,
        metadata=metadata,
        raw_node_coords=raw_node_coords,
    )


def generate_c3d10_inp_deck(mesh: ConvertedMesh) -> str:
    """Generate CalculiX INP include deck containing *NODE and *ELEMENT, TYPE=C3D10."""
    lines: list[str] = [
        "** -------------------------------------------------------------",
        "** CalculiX C3D10 mesh include deck",
        f"** Converted from Gmsh MSH 4.1 (straight_tet10, unit=mm)",
        f"** Nodes: {len(mesh.nodes)}, Elements: {len(mesh.elements)}",
        "** -------------------------------------------------------------",
        "*NODE",
    ]

    for nid in sorted(mesh.nodes):
        if mesh.raw_node_coords and nid in mesh.raw_node_coords:
            x_s, y_s, z_s = mesh.raw_node_coords[nid]
            lines.append(f"{nid}, {x_s}, {y_s}, {z_s}")
        else:
            x, y, z = mesh.nodes[nid]
            lines.append(f"{nid}, {x:.17g}, {y:.17g}, {z:.17g}")

    lines.append("*ELEMENT, TYPE=C3D10, ELSET=EL_C3D10")
    for eid in sorted(mesh.elements):
        conn = ", ".join(str(n) for n in mesh.elements[eid])
        lines.append(f"{eid}, {conn}")

    lines.append("*ELSET, ELSET=ALL")
    eids = [str(e) for e in sorted(mesh.elements)]
    for i in range(0, len(eids), 16):
        lines.append(", ".join(eids[i:i + 16]))

    return "\n".join(lines) + "\n"


def convert_msh41_to_c3d10_file(
    msh_path: Path | str,
    output_inp_path: Path | str,
    sidecar_path: Path | str | None = None,
) -> Path:
    """Atomically convert MSH 4.1 file to CalculiX INP deck without overwriting sources."""
    target = Path(output_inp_path).expanduser().resolve()
    msh_resolved = Path(msh_path).expanduser().resolve()
    sidecar_resolved = (
        Path(sidecar_path).expanduser().resolve()
        if sidecar_path is not None
        else msh_resolved.with_name("metadata.json").resolve()
    )

    # R1: Reject target paths that alias or overwrite input MSH or metadata JSON
    if target == msh_resolved:
        raise MshC3D10Error(f"Target INP path aliases source MSH file: {target}")
    if target == sidecar_resolved:
        raise MshC3D10Error(f"Target INP path aliases sidecar metadata file: {target}")

    if target.is_file():
        try:
            if target.samefile(msh_resolved):
                raise MshC3D10Error(f"Target INP path aliases source MSH file: {target}")
        except FileNotFoundError:
            pass
        try:
            if sidecar_resolved.is_file() and target.samefile(sidecar_resolved):
                raise MshC3D10Error(f"Target INP path aliases sidecar metadata file: {target}")
        except FileNotFoundError:
            pass

    mesh = read_msh41_straight_tet10(msh_resolved, sidecar_resolved)
    deck_text = generate_c3d10_inp_deck(mesh)

    target.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_file = tempfile.mkstemp(prefix=".msh_c3d10-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(deck_text)
        os.replace(tmp_file, target)
    finally:
        if os.path.exists(tmp_file):
            try:
                os.unlink(tmp_file)
            except OSError:
                pass

    return target


def parse_c3d10_deck(inp_path: Path | str) -> tuple[dict[int, tuple[float, float, float]], dict[int, list[int]]]:
    """Parse a generated C3D10 INP include deck for regression and verification."""
    inp_path = Path(inp_path).resolve()
    nodes: dict[int, tuple[float, float, float]] = {}
    elements: dict[int, list[int]] = {}

    current_section = None

    for raw_line in inp_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            upper = line.upper()
            if upper.startswith("*NODE"):
                current_section = "NODE"
            elif upper.startswith("*ELEMENT"):
                if "TYPE=C3D10" not in upper:
                    raise MshC3D10Error(f"Unexpected element type in deck: {line}")
                current_section = "ELEMENT"
            else:
                current_section = None
            continue

        if current_section == "NODE":
            tokens = [t.strip() for t in line.split(",")]
            if len(tokens) == 4:
                nid = int(tokens[0])
                nodes[nid] = (float(tokens[1]), float(tokens[2]), float(tokens[3]))
        elif current_section == "ELEMENT":
            tokens = [t.strip() for t in line.split(",")]
            if len(tokens) == 11:
                eid = int(tokens[0])
                elements[eid] = [int(t) for t in tokens[1:]]

    return nodes, elements
