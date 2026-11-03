"""Gmsh MSH 4.1 straight Tet10 -> CalculiX C3D10 converter.

Enforces:
1. Valid caelab.standalone-msh sidecar metadata (length_unit == 'mm', schema/version, SHA256 match).
2. Pure MSH 4.1 ASCII format, positive integer IDs, no duplicate nodes.
3. Strict admission: Tet10 (Gmsh type 11) physical solids only; Tri6 (type 9) boundary tags identified separately.
4. Exact 1-time mid-side swap: raw Gmsh type 11 (12, 23, 31, 14, 34, 24) -> C3D10 (12, 23, 31, 14, 24, 34) (slots 9 and 10).
5. Output format: CalculiX INP include deck (*NODE, *ELEMENT, TYPE=C3D10, *ELSET).
"""

from __future__ import annotations

import hashlib
import json
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


def verify_sidecar(msh_path: Path, sidecar_path: Path) -> dict:
    """Verify standalone MSH sidecar metadata and SHA256 integrity."""
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
    actual_sha = hashlib.sha256(msh_path.read_bytes()).hexdigest().lower()
    if actual_sha != expected_sha:
        raise MshC3D10Error(f"SHA256 mismatch for {msh_path.name}: expected {expected_sha}, got {actual_sha}")

    return data


def read_msh41_straight_tet10(msh_path: Path | str, sidecar_path: Path | str | None = None) -> ConvertedMesh:
    """Read MSH 4.1 ASCII straight Tet10 mesh, validating sidecar and converting node ordering."""
    msh_path = Path(msh_path).resolve()
    if not msh_path.is_file():
        raise MshC3D10Error(f"MSH file not found: {msh_path}")

    if sidecar_path is None:
        adjacent = msh_path.with_name("metadata.json")
        if not adjacent.is_file():
            raise MshC3D10Error(f"No sidecar provided and adjacent metadata.json not found for: {msh_path}")
        sidecar_path = adjacent
    else:
        sidecar_path = Path(sidecar_path).resolve()

    metadata = verify_sidecar(msh_path, sidecar_path)

    try:
        content = msh_path.read_text(encoding="utf-8")
    except Exception as exc:
        raise MshC3D10Error(f"Cannot read MSH file: {exc}") from exc

    lines = [line.strip() for line in content.splitlines()]
    total_lines = len(lines)
    cursor = 0

    # 1. $MeshFormat
    while cursor < total_lines and lines[cursor] != "$MeshFormat":
        cursor += 1
    if cursor >= total_lines:
        raise MshC3D10Error("Missing $MeshFormat section")
    cursor += 1
    if cursor >= total_lines:
        raise MshC3D10Error("Premature EOF in $MeshFormat")
    fmt_tokens = lines[cursor].split()
    if not fmt_tokens or fmt_tokens[0] != "4.1":
        raise MshC3D10Error(f"Expected MSH format 4.1, got: {lines[cursor]}")
    cursor += 1
    while cursor < total_lines and lines[cursor] != "$EndMeshFormat":
        cursor += 1
    if cursor >= total_lines:
        raise MshC3D10Error("Unterminated $MeshFormat section")
    cursor += 1

    # 2. $Nodes
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

    for _ in range(num_entity_blocks):
        if cursor >= total_lines:
            raise MshC3D10Error("Premature EOF in $Nodes entity block header")
        block_hdr = lines[cursor].split()
        cursor += 1
        if len(block_hdr) < 4:
            raise MshC3D10Error(f"Malformed node block header: {' '.join(block_hdr)}")
        num_nodes_in_block = int(block_hdr[3])

        # Node tags
        node_tags: list[int] = []
        for _ in range(num_nodes_in_block):
            if cursor >= total_lines:
                raise MshC3D10Error("Premature EOF reading node tags")
            tag = int(lines[cursor])
            if tag <= 0:
                raise MshC3D10Error(f"Invalid non-positive node tag: {tag}")
            if tag in nodes:
                raise MshC3D10Error(f"Duplicate node tag encountered: {tag}")
            node_tags.append(tag)
            cursor += 1

        # Node coordinates
        for tag in node_tags:
            if cursor >= total_lines:
                raise MshC3D10Error("Premature EOF reading node coordinates")
            coords = lines[cursor].split()
            cursor += 1
            if len(coords) != 3:
                raise MshC3D10Error(f"Expected 3 coordinates for node {tag}, got {len(coords)}")
            x, y, z = float(coords[0]), float(coords[1]), float(coords[2])
            nodes[tag] = (x, y, z)

    while cursor < total_lines and lines[cursor] != "$EndNodes":
        cursor += 1
    if cursor >= total_lines:
        raise MshC3D10Error("Unterminated $Nodes section")
    cursor += 1

    if len(nodes) != num_nodes:
        raise MshC3D10Error(f"Node count mismatch: expected {num_nodes}, parsed {len(nodes)}")

    # 3. $Elements
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
                if etag in c3d10_elements:
                    raise MshC3D10Error(f"Duplicate element tag: {etag}")

                raw_nodes = [int(p) for p in parts[1:]]
                for nid in raw_nodes:
                    if nid not in nodes:
                        raise MshC3D10Error(f"Element {etag} references undefined node {nid}")

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
                raw_nodes = [int(p) for p in parts[1:]]
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
        x, y, z = mesh.nodes[nid]
        lines.append(f"{nid}, {x:.16g}, {y:.16g}, {z:.16g}")

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
    """Atomically convert MSH 4.1 file to CalculiX INP deck."""
    mesh = read_msh41_straight_tet10(msh_path, sidecar_path)
    deck_text = generate_c3d10_inp_deck(mesh)

    target = Path(output_inp_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_file = tempfile.mkstemp(prefix=".msh_c3d10-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(deck_text)
        os.replace(tmp_file, target)
    finally:
        if os.path.exists(tmp_file):
            os.unlink(tmp_file)

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
