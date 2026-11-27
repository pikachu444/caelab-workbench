"""Independent structured elasticity benchmark in native CalculiX ordering."""

from collections import defaultdict
from itertools import product
from math import sqrt

# CCX shapes and an orientation-consistent six-tet Freudenthal subdivision.
CUBE = [
    (0, 0, 0),
    (1, 0, 0),
    (1, 1, 0),
    (0, 1, 0),
    (0, 0, 1),
    (1, 0, 1),
    (1, 1, 1),
    (0, 1, 1),
]
TETS = [
    (0, 1, 2, 6),
    (0, 2, 3, 6),
    (0, 3, 7, 6),
    (0, 7, 4, 6),
    (0, 4, 5, 6),
    (0, 5, 1, 6),
]
HEX_EDGES = [
    (0, 1),
    (1, 2),
    (2, 3),
    (3, 0),
    (4, 5),
    (5, 6),
    (6, 7),
    (7, 4),
    (0, 4),
    (1, 5),
    (2, 6),
    (3, 7),
]
TET_EDGES = [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)]


def face_shape(count, xi, eta):
    """Face interpolation, with xi, eta in [-1,1] (quad) or barycentric (tri)."""
    if count in (3, 6):
        a, b, c = xi, eta, 1 - xi - eta
        if count == 3:
            return [a, b, c]
        return [
            a * (2 * a - 1),
            b * (2 * b - 1),
            c * (2 * c - 1),
            4 * a * b,
            4 * b * c,
            4 * c * a,
        ]
    coefs = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    bilinear = [(1 + u * xi) * (1 + v * eta) / 4 for u, v in coefs]
    if count == 4:
        return bilinear
    corners = [w * (u * xi + v * eta - 1) for w, (u, v) in zip(bilinear, coefs)]
    return corners + [
        (1 - xi * xi) * (1 - eta) / 2,
        (1 + xi) * (1 - eta * eta) / 2,
        (1 - xi * xi) * (1 + eta) / 2,
        (1 - xi) * (1 - eta * eta) / 2,
    ]


class Beam:
    # E=1000, nu=0. Unit square section, length 12; kappa=1e-3.
    def __init__(self, kind, *, tied=False, axial=False):
        self.points = []
        self.node_ids = {}
        self.elements = defaultdict(list)
        self.element_z = {}
        self.master = []
        self.slave = []
        self.loads = defaultdict(float)
        for station in range(12):
            skin = tied and station >= 6
            family = {"C3D8": "C3D4", "C3D20": "C3D10"}[kind] if skin else kind
            divisions = 4 if skin else 2
            for j, k in product(range(divisions), repeat=2):
                corners = [
                    self.node(
                        skin,
                        (
                            station + i,
                            (j + y) / divisions - 0.5,
                            (k + z) / divisions - 0.5,
                        ),
                    )
                    for i, y, z in CUBE
                ]
                use_hex = family in ("C3D8", "C3D20")
                for local in [range(8)] if use_hex else TETS:
                    nodes = [corners[index] for index in local]
                    eid = len(self.element_z) + 1
                    self.element_z[eid] = sum(
                        self.points[n - 1][2] for n in nodes
                    ) / len(nodes)
                    faces = [n for n in nodes if self.points[n - 1][0] == station + 1]
                    if use_hex:
                        # CCX S4; corner order follows the positive-x normal.
                        faces = [nodes[i] for i in (1, 2, 6, 5)]
                    quadratic = family in ("C3D10", "C3D20")
                    if quadratic:
                        mids = [
                            self.midnode(skin, nodes[a], nodes[b])
                            for a, b in (HEX_EDGES if use_hex else TET_EDGES)
                        ]
                        nodes += mids
                        if len(faces) >= 3:
                            faces += [
                                self.midnode(skin, a, b)
                                for a, b in zip(faces, faces[1:] + faces[:1])
                            ]
                    self.elements[family].append((eid, *nodes))
                    if tied and station == 5:
                        self.master.append((eid, faces))
                    if station == 11 and len(faces) >= 3:
                        self.traction(faces, divisions, axial)
        self.slave = [n for (zone, p), n in self.node_ids.items() if zone and p[0] == 6]

    def node(self, zone, point):
        key = zone, point
        if key not in self.node_ids:
            self.points.append(point)
            self.node_ids[key] = len(self.points)
        return self.node_ids[key]

    def midnode(self, zone, a, b):
        return self.node(
            zone,
            tuple((x + y) / 2 for x, y in zip(self.points[a - 1], self.points[b - 1])),
        )

    def traction(self, face, divisions, axial):
        # Exact face quadrature for N_i(y,z) times a linear end-face traction.
        area = 1 / divisions**2
        if len(face) in (4, 8):
            gauss = [(-1 / sqrt(3), 0.5), (1 / sqrt(3), 0.5)]
            samples = [(x, y, wx * wy) for x, wx in gauss for y, wy in gauss]
        else:
            area /= 2
            samples = [
                (1 / 3, 1 / 3, -27 / 48),
                (0.6, 0.2, 25 / 48),
                (0.2, 0.6, 25 / 48),
                (0.2, 0.2, 25 / 48),
            ]
        for x, y, weight in samples:
            shape = face_shape(len(face), x, y)
            sigma = (
                1
                if axial
                else -sum(s * self.points[n - 1][2] for s, n in zip(shape, face))
            )
            for n, s in zip(face, shape):
                self.loads[n] += s * sigma * area * weight

    def interface_jump(self, u):
        jump = 0.0
        for n in self.slave:
            _, y, z = self.points[n - 1]
            for _, face in self.master:
                _, ymin, zmin = self.points[face[0] - 1]
                _, ymax, zmax = self.points[face[2] - 1]
                if ymin <= y <= ymax and zmin <= z <= zmax:
                    shape = face_shape(
                        len(face),
                        2 * (y - ymin) / (ymax - ymin) - 1,
                        2 * (z - zmin) / (zmax - zmin) - 1,
                    )
                    interpolated = [
                        sum(s * u[p][c] for s, p in zip(shape, face)) for c in range(3)
                    ]
                    jump = max(jump, *(abs(a - b) for a, b in zip(u[n], interpolated)))
                    break
            else:
                raise AssertionError(f"Secondary node {n} is outside master surface")
        return jump
