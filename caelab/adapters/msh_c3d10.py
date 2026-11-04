"""Gmsh MSH 4.1 straight Tet10 -> CalculiX C3D10 converter.

Enforces:
1. Valid caelab.standalone-msh sidecar metadata (length_unit == 'mm', schema/version, SHA256 match).
2. Pure MSH 4.1 ASCII format ($MeshFormat '4.1 0 8'), positive integer IDs, no duplicate nodes or elements.
3. Strict admission: Tet10 (Gmsh type 11) physical solids only; Tri6 (type 9) boundary tags identified separately.
4. Straight profile verification: finite coordinates, distinct node references, exact 8-ULP midpoint residual budget, and exact corner-matrix perturbation norm positivity certificate.
5. Exact 1-time mid-side swap: raw Gmsh type 11 (12, 23, 31, 14, 34, 24) -> C3D10 (12, 23, 31, 14, 24, 34) (slots 9 and 10).
6. Lossless coordinate preservation and safe atomic writing preventing source file aliasing/overwrites.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Callable, Iterable, NamedTuple

from ..model.schema import (
    BoundaryCondition,
    Load,
    Material,
    MeshData,
    NamedSet,
    Section,
    Step,
    StructuralModel,
)
from ..result_summary import summarize_result
from ..solvers.base import ResultSet
from ..solvers.calculix import CalculiXConverter


class MshC3D10Error(ValueError):
    """Raised when MSH 4.1 or sidecar validation fails."""
    pass


class ConvertedMesh(NamedTuple):
    nodes: dict[int, tuple[float, float, float]]
    elements: dict[int, list[int]]
    tri6_boundary: dict[int, list[int]]
    metadata: dict
    raw_node_coords: dict[int, tuple[str, str, str]] | None = None


GMSH_TET10_EDGES = ((0, 1), (1, 2), (2, 0), (0, 3), (2, 3), (1, 3))


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


def _det3(a: tuple[tuple[Fraction, ...], ...]) -> Fraction:
    return (
        a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
        - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
        + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0])
    )


def _minor3(a: tuple[tuple[Fraction, ...], ...], row: int, col: int) -> Fraction:
    remaining = [[a[r][c] for c in range(3) if c != col] for r in range(3) if r != row]
    return remaining[0][0] * remaining[1][1] - remaining[0][1] * remaining[1][0]


def _adjugate3(a: tuple[tuple[Fraction, ...], ...]) -> tuple[tuple[Fraction, ...], ...]:
    return tuple(
        tuple(
            (-1 if (row + col) % 2 else 1) * _minor3(a, col, row)
            for col in range(3)
        )
        for row in range(3)
    )


def certify_straight_tet10(
    coords: list[tuple[float, float, float]],
    *,
    ulp_budget: int = 8,
) -> dict:
    """Exact, sufficient straight Tet10 certificate aligned with caelab-prep producer policy.

    1. Midpoint proximity is validated within an exact ULP budget (default 8 ULP).
    2. Jacobian positivity throughout the element is certified via exact matrix-norm
       perturbation bound relative to the corner tetrahedron.
    """
    p = tuple(tuple(Fraction.from_float(float(v)) for v in pt) for pt in coords)
    a = tuple(tuple(p[col + 1][row] - p[0][row] for col in range(3)) for row in range(3))
    det = _det3(a)

    residuals: dict[frozenset[int], tuple[Fraction, Fraction, Fraction]] = {}
    within_budget = True
    max_res = Fraction(0)

    for midpoint, (i, j) in zip(p[4:], GMSH_TET10_EDGES):
        avg = tuple((x + y) / 2 for x, y in zip(p[i], p[j]))
        res = tuple(x - y for x, y in zip(midpoint, avg))
        residuals[frozenset((i, j))] = res
        max_res = max(max_res, *(abs(v) for v in res))
        within_budget &= all(
            abs(v) <= Fraction.from_float(math.ulp(float(exp))) * ulp_budget
            for v, exp in zip(res, avg)
        )

    report = {
        "certified_positive": False,
        "reason": "parent_not_positive",
        "relative_perturbation_bound": None,
        "midpoint_within_ulp_budget": within_budget,
        "maximum_midpoint_residual": float(max_res) if math.isfinite(float(max_res)) else None,
        "ulp_budget": ulp_budget,
    }

    if det <= 0:
        return report

    gradients = ((-1, -1, -1), (1, 0, 0), (0, 1, 0), (0, 0, 1))
    adj = _adjugate3(a)
    max_norm_num = Fraction(0)

    for k in range(4):
        perturbation = tuple(
            tuple(
                4 * sum(
                    residuals[frozenset((k, j))][row] * gradients[j][col]
                    for j in range(4)
                    if j != k
                )
                for col in range(3)
            )
            for row in range(3)
        )
        product = tuple(
            tuple(
                sum(adj[row][j] * perturbation[j][col] for j in range(3))
                for col in range(3)
            )
            for row in range(3)
        )
        max_norm_num = max(
            max_norm_num,
            *(sum(abs(v) for v in r) for r in product)
        )

    bound = max_norm_num / det
    report["relative_perturbation_bound"] = float(bound) if math.isfinite(float(bound)) else None
    report["certified_positive"] = max_norm_num < det
    report["reason"] = None if report["certified_positive"] else "positivity_not_certified"
    return report


def _check_straight_tet10(
    etag: int,
    raw_nodes: list[int],
    nodes: dict[int, tuple[float, float, float]],
) -> None:
    """Validate finite coordinates, 8-ULP midpoint residual budget, and exact Jacobian positivity certificate."""
    coords = [nodes[n] for n in raw_nodes]
    cert = certify_straight_tet10(coords, ulp_budget=8)

    if not cert["midpoint_within_ulp_budget"]:
        raise MshC3D10Error(
            f"Tet10 element {etag} violates straight profile: midside node not within "
            f"{cert['ulp_budget']} ULP budget (max_residual={cert['maximum_midpoint_residual']})"
        )

    if not cert["certified_positive"]:
        raise MshC3D10Error(
            f"Tet10 element {etag} violates straight profile: Jacobian positivity not certified "
            f"(reason={cert['reason']}, bound={cert['relative_perturbation_bound']})"
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

                # R3: Straight Tet10 exact 8-ULP midpoint and perturbation-norm positive certificate
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


def _id_lines(ids: Iterable[int | str]) -> list[str]:
    """CalculiX accepts at most 16 entries per data line."""
    items = [str(i) for i in ids]
    return [", ".join(items[i:i + 16]) for i in range(0, len(items), 16)] or [""]


def extract_boundary_nodes(
    mesh: ConvertedMesh,
    predicate: Callable[[float, float, float], bool] | None = None,
) -> list[int]:
    """Extract sorted unique node IDs present in Tri6 boundary annotations satisfying predicate."""
    candidates: set[int] = set()
    for tri_nodes in mesh.tri6_boundary.values():
        candidates.update(tri_nodes)

    if predicate is None:
        return sorted(candidates)

    return [nid for nid in sorted(candidates) if predicate(*mesh.nodes[nid])]


def assemble_c3d10_master_deck(
    include_deck_filename: str | Path,
    *,
    fixed_nodes: list[int] | set[int],
    loaded_nodes: list[int] | set[int] | None = None,
    load_vector: tuple[float, float, float] = (0.0, 0.0, 0.0),
    youngs_modulus: float = 210000.0,
    poissons_ratio: float = 0.3,
    density: float | None = None,
    heading: str = "CalculiX C3D10 Simulation Deck",
    fixed_set_name: str = "FIXED_NODES",
    loaded_set_name: str = "LOAD_NODES",
    include_dat_totals: bool = False,
) -> str:
    """Assemble a complete CalculiX master simulation deck referencing a C3D10 include deck."""
    inc_name = str(include_deck_filename).replace("\\", "/")
    if not inc_name.strip():
        raise MshC3D10Error("Include deck filename must not be empty")

    fixed_list = sorted(set(fixed_nodes))
    if not fixed_list:
        raise MshC3D10Error("Fixed nodes list must not be empty")

    loaded_list = sorted(set(loaded_nodes)) if loaded_nodes else []

    # R76-2: Strict 3-component load vector check
    if not isinstance(load_vector, (tuple, list)) or len(load_vector) != 3:
        raise MshC3D10Error(
            f"load_vector must be a sequence of exactly 3 components (fx, fy, fz), got length {len(load_vector) if isinstance(load_vector, (tuple, list)) else type(load_vector)}"
        )

    # R76-2: Strict 3-component load vector check
    if not isinstance(load_vector, (tuple, list)) or len(load_vector) != 3:
        raise MshC3D10Error(
            f"load_vector must be a sequence of exactly 3 components (fx, fy, fz), got length {len(load_vector) if isinstance(load_vector, (tuple, list)) else type(load_vector)}"
        )

    # R76-2: Non-zero load with empty target check
    if any(load_vector) and not loaded_list:
        raise MshC3D10Error("Non-zero load_vector specified but loaded_nodes is empty")

    # R76-2: Numeric parameter checks
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in load_vector):
        raise MshC3D10Error("load_vector components must be finite numbers")

    if not isinstance(youngs_modulus, (int, float)) or not math.isfinite(youngs_modulus) or youngs_modulus <= 0:
        raise MshC3D10Error("youngs_modulus must be a positive finite number")

    if not isinstance(poissons_ratio, (int, float)) or not math.isfinite(poissons_ratio) or not (-1.0 < poissons_ratio < 0.5):
        raise MshC3D10Error("poissons_ratio must be strictly between -1.0 and 0.5")

    if density is not None and (not isinstance(density, (int, float)) or not math.isfinite(density) or density <= 0):
        raise MshC3D10Error("density must be a positive finite number")

    collision = set(fixed_list).intersection(set(loaded_list))
    if collision:
        raise MshC3D10Error(
            f"Fixed and loaded node sets must be disjoint to prevent load-reaction ambiguity. Colliding: {sorted(collision)}"
        )

    lines: list[str] = [
        "** -------------------------------------------------------------",
        "** CalculiX C3D10 master simulation deck",
        "** -------------------------------------------------------------",
        "*HEADING",
        heading,
        f"*INCLUDE, INPUT={inc_name}",
        f"*NSET, NSET={fixed_set_name}",
    ]
    lines.extend(_id_lines(fixed_list))

    if loaded_list:
        lines.append(f"*NSET, NSET={loaded_set_name}")
        lines.extend(_id_lines(loaded_list))

    lines.extend([
        "*MATERIAL, NAME=STEEL",
        "*ELASTIC",
        f"{youngs_modulus!r}, {poissons_ratio!r}",
    ])
    if density is not None:
        lines.extend([
            "*DENSITY",
            f"{density!r}",
        ])

    lines.extend([
        "*SOLID SECTION, ELSET=EL_C3D10, MATERIAL=STEEL",
        "*STEP",
        "*STATIC",
        "*BOUNDARY",
        f"{fixed_set_name}, 1, 3, 0.0",
    ])

    if loaded_list and any(load_vector):
        lines.append("*CLOAD")
        n = len(loaded_list)
        for comp, dof in zip(load_vector, (1, 2, 3)):
            if comp != 0.0:
                lines.append(f"{loaded_set_name}, {dof}, {comp / n!r}")

    lines.extend([
        "*NODE FILE",
        "U, RF",
        "*EL FILE",
        "S",
    ])
    if include_dat_totals:
        lines.extend([
            f"*NODE PRINT, NSET={fixed_set_name}, TOTALS=ONLY",
            "RF",
        ])

    lines.append("*END STEP")
    return "\n".join(lines) + "\n"


def build_c3d10_structural_model(
    mesh: ConvertedMesh,
    *,
    name: str = "c3d10_simulation",
    unit_system: str = "mm-t-s",
    fixed_nodes: list[int] | set[int] | None = None,
    loaded_nodes: list[int] | set[int] | None = None,
    load_vector: tuple[float, float, float] = (0.0, 0.0, 0.0),
    youngs_modulus: float = 210000.0,
    poissons_ratio: float = 0.3,
    density: float | None = None,
) -> StructuralModel:
    """Construct a caelab.model.schema.StructuralModel from ConvertedMesh for CalculiX."""
    # R76-1: Prevent mm mesh from being labeled as SI without coordinate conversion
    mesh_unit = mesh.metadata.get("length_unit") if mesh.metadata else None
    if mesh_unit == "mm" and unit_system == "si":
        raise MshC3D10Error(
            "MSH input has length_unit 'mm'; labeling as 'si' without coordinate conversion is prohibited. "
            "Use unit_system='mm-t-s' or 'mm'."
        )

    fixed_list = sorted(set(fixed_nodes)) if fixed_nodes else []
    loaded_list = sorted(set(loaded_nodes)) if loaded_nodes else []

    # R76-2: Strict 3-component load vector check
    if not isinstance(load_vector, (tuple, list)) or len(load_vector) != 3:
        raise MshC3D10Error(
            f"load_vector must be a sequence of exactly 3 components (fx, fy, fz), got length {len(load_vector) if isinstance(load_vector, (tuple, list)) else type(load_vector)}"
        )

    # R76-2: Non-zero load with empty target check
    if any(load_vector) and not loaded_list:
        raise MshC3D10Error("Non-zero load_vector specified but loaded_nodes is empty")

    # R76-2: Numeric parameter checks
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in load_vector):
        raise MshC3D10Error("load_vector components must be finite numbers")

    if not isinstance(youngs_modulus, (int, float)) or not math.isfinite(youngs_modulus) or youngs_modulus <= 0:
        raise MshC3D10Error("youngs_modulus must be a positive finite number")

    if not isinstance(poissons_ratio, (int, float)) or not math.isfinite(poissons_ratio) or not (-1.0 < poissons_ratio < 0.5):
        raise MshC3D10Error("poissons_ratio must be strictly between -1.0 and 0.5")

    if density is not None and (not isinstance(density, (int, float)) or not math.isfinite(density) or density <= 0):
        raise MshC3D10Error("density must be a positive finite number")

    # Validate that nodes exist in mesh
    missing_fixed = [nid for nid in fixed_list if nid not in mesh.nodes]
    if missing_fixed:
        raise MshC3D10Error(f"fixed_nodes contains node IDs not present in mesh: {missing_fixed}")

    missing_loaded = [nid for nid in loaded_list if nid not in mesh.nodes]
    if missing_loaded:
        raise MshC3D10Error(f"loaded_nodes contains node IDs not present in mesh: {missing_loaded}")

    collision = set(fixed_list).intersection(set(loaded_list))
    if collision:
        raise MshC3D10Error(
            f"Fixed and loaded node sets must be disjoint. Colliding: {sorted(collision)}"
        )

    mesh_data = MeshData(
        nodes={nid: list(coords) for nid, coords in mesh.nodes.items()},
        elements={eid: {"type": "C3D10", "nodes": list(conn)} for eid, conn in mesh.elements.items()},
    )

    sets: dict[str, NamedSet] = {
        "ALL": NamedSet(kind="element", members=sorted(mesh.elements.keys())),
        "EL_C3D10": NamedSet(kind="element", members=sorted(mesh.elements.keys())),
    }
    bcs: list[BoundaryCondition] = []
    if fixed_list:
        sets["FIXED_NODES"] = NamedSet(kind="node", members=fixed_list)
        bcs.append(BoundaryCondition(name="fixed_support", target_set="FIXED_NODES", fixed_dofs=[1, 2, 3]))

    loads: list[Load] = []
    if loaded_list and any(load_vector):
        sets["LOAD_NODES"] = NamedSet(kind="node", members=loaded_list)
        vals = {}
        for comp, key in zip(load_vector, ("fx", "fy", "fz")):
            if comp != 0.0:
                vals[key] = comp
        loads.append(Load(name="applied_load", kind="force", target_set="LOAD_NODES", values=vals))

    mat_params: dict[str, float] = {
        "youngs_modulus": youngs_modulus,
        "poissons_ratio": poissons_ratio,
    }
    if density is not None:
        mat_params["density"] = density

    materials = [Material(name="STEEL", law="linear_elastic", params=mat_params)]
    sections = [Section(name="solid_section", kind="solid", element_set="ALL", material="STEEL")]
    steps = [
        Step(
            name="static_step",
            kind="linear_static",
            loads=[ld.name for ld in loads],
            bcs=[bc.name for bc in bcs],
        )
    ]

    return StructuralModel(
        name=name,
        unit_system=unit_system,
        mesh=mesh_data,
        sets=sets,
        materials=materials,
        sections=sections,
        loads=loads,
        bcs=bcs,
        steps=steps,
        outputs={"fields": ["U", "RF", "S"]},
    )


def parse_deck_all_nsets(deck_text: str) -> dict[str, set[int]]:
    """Parse all *NSET definitions in the deck, returning dict of nset_name -> set of node IDs."""
    nsets: dict[str, set[int]] = {}
    current_set: str | None = None
    is_generate = False
    for raw_line in deck_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            upper_line = line.upper().replace(" ", "")
            if upper_line.startswith("*NSET"):
                current_set = None
                is_generate = "GENERATE" in upper_line
                for part in line.split(",")[1:]:
                    part_strip = part.strip()
                    if part_strip.upper().startswith("NSET="):
                        current_set = part_strip[5:].strip().upper()
                        if current_set not in nsets:
                            nsets[current_set] = set()
                        break
            else:
                current_set = None
        elif current_set:
            parts = [p.strip() for p in line.split(",") if p.strip()]
            if is_generate and len(parts) >= 2:
                try:
                    start_node = int(parts[0])
                    end_node = int(parts[1])
                    step = int(parts[2]) if len(parts) >= 3 else 1
                    nsets[current_set].update(range(start_node, end_node + 1, step))
                except ValueError:
                    pass
            else:
                for p in parts:
                    try:
                        nsets[current_set].add(int(p))
                    except ValueError:
                        pass
    return nsets


def parse_deck_nset_nodes(deck_text: str, nset_name: str) -> set[int] | None:
    """Parse node IDs defined under an *NSET card with the given set name."""
    nsets = parse_deck_all_nsets(deck_text)
    return nsets.get(nset_name.upper())


def parse_deck_boundary_conditions(
    deck_text: str,
    nsets: dict[str, set[int]] | None = None,
) -> dict[tuple[int, int], float]:
    """
    Parse all boundary conditions in the deck under *BOUNDARY cards.
    Returns mapping of (node_id, dof) -> prescribed_value.
    Raises MshC3D10Error if non-finite or invalid values are encountered.
    """
    if nsets is None:
        nsets = parse_deck_all_nsets(deck_text)
    nsets_upper = {k.upper(): v for k, v in nsets.items()}

    bcs: dict[tuple[int, int], float] = {}
    in_boundary = False

    for raw_line in deck_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            upper_line = line.upper().replace(" ", "")
            if upper_line.startswith("*BOUNDARY"):
                in_boundary = True
                continue
            elif in_boundary:
                in_boundary = False
        elif in_boundary:
            parts = [p.strip() for p in line.split(",") if p.strip()]
            if not parts:
                continue
            target = parts[0].strip()
            if len(parts) < 2:
                continue
            try:
                start_dof = int(parts[1])
            except ValueError:
                continue

            end_dof = start_dof
            val = 0.0
            if len(parts) >= 3 and parts[2].isdigit():
                end_dof = int(parts[2])
                if len(parts) >= 4:
                    try:
                        val = float(parts[3])
                    except ValueError:
                        raise MshC3D10Error(f"Invalid prescribed boundary value in card: {line}")
            elif len(parts) >= 3:
                try:
                    val = float(parts[2])
                except ValueError:
                    raise MshC3D10Error(f"Invalid prescribed boundary value in card: {line}")

            if not math.isfinite(val):
                raise MshC3D10Error(f"Non-finite prescribed boundary value ({val}) in card: {line}")

            if target.isdigit():
                target_nodes = {int(target)}
            elif target.upper() in nsets_upper:
                target_nodes = nsets_upper[target.upper()]
            else:
                raise MshC3D10Error(f"*BOUNDARY references unknown node or NSET '{target}' in deck")

            for nid in target_nodes:
                for dof in range(start_dof, end_dof + 1):
                    key = (nid, dof)
                    if key in bcs and not math.isclose(bcs[key], val, abs_tol=1e-7):
                        raise MshC3D10Error(
                            f"Conflicting boundary conditions for node {nid}, DOF {dof}: "
                            f"{bcs[key]} vs {val}"
                        )
                    bcs[key] = val

    return bcs


def parse_deck_boundary_dofs(deck_text: str, nset_name: str) -> set[int]:
    """Parse constrained DOFs for a given node set under *BOUNDARY cards."""
    nsets = parse_deck_all_nsets(deck_text)
    target_nodes = nsets.get(nset_name.upper(), set())
    bcs = parse_deck_boundary_conditions(deck_text, nsets)
    return {dof for (nid, dof) in bcs if nid in target_nodes}


def parse_deck_cloads(
    deck_text: str,
    nsets: dict[str, set[int]] | None = None,
) -> dict[tuple[int, int], float]:
    """
    Parse all concentrated nodal loads in the deck under *CLOAD cards.
    Returns mapping of (node_id, dof) -> force_value.
    Raises MshC3D10Error if non-finite or invalid values are encountered.
    """
    if nsets is None:
        nsets = parse_deck_all_nsets(deck_text)
    nsets_upper = {k.upper(): v for k, v in nsets.items()}

    loads: dict[tuple[int, int], float] = {}
    in_cload = False

    for raw_line in deck_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            upper_line = line.upper().replace(" ", "")
            if upper_line.startswith("*CLOAD"):
                in_cload = True
                continue
            elif in_cload:
                in_cload = False
        elif in_cload:
            parts = [p.strip() for p in line.split(",") if p.strip()]
            if len(parts) >= 3:
                target = parts[0].strip()
                try:
                    dof = int(parts[1])
                    val = float(parts[2])
                except ValueError:
                    raise MshC3D10Error(f"Invalid numeric values in *CLOAD card: {line}")

                if not math.isfinite(val):
                    raise MshC3D10Error(f"Non-finite load value ({val}) in *CLOAD card: {line}")

                if target.isdigit():
                    target_nodes = {int(target)}
                elif target.upper() in nsets_upper:
                    target_nodes = nsets_upper[target.upper()]
                else:
                    raise MshC3D10Error(f"*CLOAD references unknown node or NSET '{target}' in deck")

                for nid in target_nodes:
                    key = (nid, dof)
                    loads[key] = loads.get(key, 0.0) + val

    return loads


def parse_deck_cload_totals(deck_text: str, nset_sizes: dict[str, int] | None = None) -> dict[int, float]:
    """Parse total force applied in each DOF under *CLOAD cards."""
    loads = parse_deck_cloads(deck_text)
    totals: dict[int, float] = {}
    for (nid, dof), val in loads.items():
        totals[dof] = totals.get(dof, 0.0) + val
    return totals


def run_c3d10_simulation(
    workdir: Path | str,
    model: StructuralModel,
    deck_path: Path | str,
) -> tuple[ResultSet, dict]:
    """Execute CalculiX simulation using CalculiXConverter and summarize results."""
    workdir = Path(workdir).resolve()
    deck_path = Path(deck_path).resolve()

    # R76-4: Ensure deck_path resides inside workdir
    if deck_path.parent != workdir:
        raise MshC3D10Error(f"deck_path must be located directly within workdir: {deck_path} vs {workdir}")

    # R76-3: Verify model and deck consistency before solver invocation
    deck_text = deck_path.read_text(encoding="utf-8", errors="replace")
    deck_nsets = parse_deck_all_nsets(deck_text)

    # 1. Verify Node Sets
    for set_name in ("FIXED_NODES", "LOAD_NODES"):
        if set_name in model.sets and model.sets[set_name].kind == "node":
            expected_nodes = set(model.sets[set_name].members)
            actual_nodes = deck_nsets.get(set_name.upper())
            if actual_nodes is None:
                raise MshC3D10Error(f"deck_path is missing *NSET, NSET={set_name} defined in model")
            if actual_nodes != expected_nodes:
                raise MshC3D10Error(
                    f"deck_path {set_name} node IDs do not match model {set_name} set. "
                    f"Model has {len(expected_nodes)} nodes, deck has {len(actual_nodes)} nodes."
                )

    # 2. Verify Boundary Conditions (Exact (node, DOF) -> value mapping)
    expected_bcs: dict[tuple[int, int], float] = {}
    for bc in model.bcs:
        target_nodes: set[int] = set()
        if bc.target_set and bc.target_set in model.sets:
            target_nodes = set(model.sets[bc.target_set].members)
        elif bc.target_set and bc.target_set.isdigit():
            target_nodes = {int(bc.target_set)}

        # Validate finiteness of all defined values in bc.values upfront
        if bc.values:
            for v_dof, v_val in bc.values.items():
                try:
                    f_val = float(v_val)
                except (ValueError, TypeError):
                    raise MshC3D10Error(f"Model boundary condition '{bc.name}' defines invalid value ({v_val})")
                if not math.isfinite(f_val):
                    raise MshC3D10Error(f"Model boundary condition '{bc.name}' defines non-finite value ({v_val})")

        for nid in target_nodes:
            for dof in bc.fixed_dofs:
                val = 0.0
                if bc.values:
                    raw_val = bc.values.get(dof, bc.values.get(str(dof), 0.0))
                    try:
                        val = float(raw_val)
                    except (ValueError, TypeError):
                        raise MshC3D10Error(f"Model boundary condition '{bc.name}' defines invalid value ({raw_val})")
                    if not math.isfinite(val):
                        raise MshC3D10Error(f"Model boundary condition '{bc.name}' defines non-finite value ({val})")
                key = (nid, dof)
                if key in expected_bcs and not math.isclose(expected_bcs[key], val, abs_tol=1e-12):
                    raise MshC3D10Error(
                        f"Model defines conflicting boundary conditions for node {nid}, DOF {dof}: "
                        f"{expected_bcs[key]} vs {val}"
                    )
                expected_bcs[key] = val

    if expected_bcs and "*BOUNDARY" not in deck_text.upper():
        raise MshC3D10Error("model defines boundary conditions, but deck_path lacks *BOUNDARY keyword")

    deck_bcs = parse_deck_boundary_conditions(deck_text, deck_nsets)
    if set(deck_bcs.keys()) != set(expected_bcs.keys()):
        extra = set(deck_bcs.keys()) - set(expected_bcs.keys())
        missing = set(expected_bcs.keys()) - set(deck_bcs.keys())
        raise MshC3D10Error(
            f"deck_path boundary conditions do not match model expected boundary conditions. "
            f"Missing (node, DOF): {sorted(missing)}, Extra (node, DOF): {sorted(extra)}"
        )
    for key, exp_val in expected_bcs.items():
        act_val = deck_bcs[key]
        bc_budget = 10 * max(math.ulp(exp_val), 1e-12)
        if not math.isfinite(act_val) or abs(act_val - exp_val) > bc_budget:
            raise MshC3D10Error(
                f"deck_path boundary condition value for (node {key[0]}, DOF {key[1]}) ({act_val}) "
                f"does not match model expected value ({exp_val})"
            )

    # 3. Verify Concentrated Loads (Exact (node, DOF) -> value mapping)
    expected_loads: dict[tuple[int, int], float] = {}
    dof_map = {"fx": 1, "fy": 2, "fz": 3, "1": 1, "2": 2, "3": 3, 1: 1, 2: 2, 3: 3}
    for ld in model.loads:
        if ld.kind not in ("force", "cload"):
            continue
        target_nodes = set()
        if ld.target_set and ld.target_set in model.sets:
            target_nodes = set(model.sets[ld.target_set].members)
        elif ld.target_set and ld.target_set.isdigit():
            target_nodes = {int(ld.target_set)}
        if not target_nodes or not ld.values:
            continue

        # Validate finiteness of all defined load components upfront
        for k, comp in ld.values.items():
            try:
                f_comp = float(comp)
            except (ValueError, TypeError):
                raise MshC3D10Error(f"Model load '{ld.name}' defines invalid value ({comp})")
            if not math.isfinite(f_comp):
                raise MshC3D10Error(f"Model load '{ld.name}' defines non-finite value ({comp})")

        n = len(target_nodes)
        for k, comp in ld.values.items():
            dof = dof_map.get(str(k).lower(), dof_map.get(k))
            if dof is None:
                continue
            per_node_val = float(comp) / n
            for nid in target_nodes:
                expected_loads[(nid, dof)] = expected_loads.get((nid, dof), 0.0) + per_node_val

    if expected_loads and "*CLOAD" not in deck_text.upper():
        raise MshC3D10Error("model defines loads, but deck_path lacks *CLOAD keyword")

    deck_loads = parse_deck_cloads(deck_text, deck_nsets)
    if set(deck_loads.keys()) != set(expected_loads.keys()):
        extra = set(deck_loads.keys()) - set(expected_loads.keys())
        missing = set(expected_loads.keys()) - set(deck_loads.keys())
        raise MshC3D10Error(
            f"deck_path *CLOAD loads do not match model expected loads. "
            f"Missing (node, DOF): {sorted(missing)}, Extra (node, DOF): {sorted(extra)}"
        )
    for key, exp_val in expected_loads.items():
        act_val = deck_loads[key]
        load_budget = 10 * max(math.ulp(exp_val), 1e-12)
        if not math.isfinite(act_val) or abs(act_val - exp_val) > load_budget:
            raise MshC3D10Error(
                f"deck_path *CLOAD force for (node {key[0]}, DOF {key[1]}) ({act_val}) "
                f"does not match model expected per-node load ({exp_val})"
            )

    conv = CalculiXConverter()
    result = conv.run(model, deck_path, workdir)
    summary = summarize_result(result)
    return result, summary
