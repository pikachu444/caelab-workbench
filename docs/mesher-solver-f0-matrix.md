# M3-F0 native solver evidence

## Round 2 on Windows and Ubuntu WSL

Workbench baseline `aca064ec95d1da36453428a66b66c8e0e76d619c`. The build hashes in
[Build identity and replay](#build-identity-and-replay) apply to this run.
The F0 decks exercise native element ordering, stresses, force transfer and output
under stated loads. Geometry generation and CAD projection are tested in Prep.

| Native family | CalculiX 2.21 | OpenCourant 20261011 and GNU OpenRadioss |
|---|---|---|
| Hex20 | `C3D20` 27 integration points, `C3D20R` 8, axial and rigid controls. Full integration also passed bending. | `/BRIC20`, SOLID Isolid=16, passed rigid translation and constrained axial compression. |
| Wedge15 | `C3D15`, 9 points, axial, rigid and mixed traction. | Solid Wedge15 has no positive control. |
| Tet10 | `C3D10`, 4 points, axial, rigid, mixed traction and bending. | `/TETRA10`, ordinary solid property, passed rigid translation and constrained axial compression. |
| Shells | `S8`, 27 points, and `S6`, 9 points, passed axial plane stress and rigid motion. | No new quadratic shell control. |
| Conformal solid transition | `C3D20 + C3D15 + C3D10` shared a complete eight-node quad and six-node triangle. The independent linear three-family control also passed. | Mixed wedge transition remains unavailable. |
| Tied solid zones | `C3D8 + C3D4` and `C3D20 + C3D10` transmitted load under axial traction and pure bending. | A loaded `/INTER/TYPE2` joining linear hex and tet parts passed both Engines and final-frame checks. Quadratic mixed-order ties have no RAD control. |
| Pyramid5 | `C3D5` rejected | `/PYRA5` rejected |

### CalculiX bending and tie

The 12 mm long, 1 mm square beam uses E=1000 MPa and ν=0.
On the free end the consistent axial surface traction is σxx=−z MPa (z in mm).
It supplies a pure moment of magnitude 1/12 N·mm. With the opposite face fully
clamped, the exact small-strain 3D solution has curvature κ=0.001/mm,
ux=−κxz and uz=κx²/2. It predicts 0.072 mm tip displacement and a linear
through-thickness stress. All other stress components vanish.
The axial patch applies 1 MPa uniformly to the far end and predicts 0.012 mm.
In the tied tests the end load acts on the tet half, and only the far hex face is clamped.

| Beam case | Elements | Mean tip displacement (mm) | RMS cell-mean σxx error (MPa) | Largest interface displacement mismatch (mm) |
|---|---:|---:|---:|---:|
| C3D4 bending | 288 | 0.0246172344 | 0.173582 | Shared mesh |
| C3D10 bending | 288 | 0.072 | 0 at DAT precision | Shared mesh |
| C3D20 bending | 48 | 0.072 | 1.00e−8 | Shared mesh |
| Linear tied bending | 24 + 576 | 0.042919844 | 0.170923 | 5.00e−9 |
| Quadratic tied bending | 24 + 576 | 0.07200001 | 1.98e−5 | 1.06e−8 |
| Linear tied axial | 24 + 576 | 0.012000318 | 0.00215087 | 5.00e−10 |
| Quadratic tied axial | 24 + 576 | 0.012 | 0 at DAT precision | 8.67e−19 |

The fixed grid has 12 longitudinal intervals and two intervals across each transverse
dimension. In the tied tests the tet half has four transverse intervals in each
direction. The planar midspan interface separates two adjoining half-beams. Qualification of a
closed interface around a generated CAD core requires the Prep mesh and its emitted surfaces.
The node-based secondary surface has 25 linear or 81 quadratic nodes,
with separate identities from the four master faces. Interpolating each master
face at each secondary coordinate checks all three displacement components.
Measured clamp moments for continuous C3D4, C3D10 and C3D20 were
0.083333325, 0.0833333355 and 0.083333337 N·mm. For the linear and quadratic
tied beams they were 0.083333340 and 0.0833333365 N·mm. Axial clamp forces
were −1.00000002 and −1.00000011 N. The stress RMS gives equal weight to each
cell mean. Continuous C3D10 and C3D20 additionally compare all printed
integration-point axial stresses with the analytical through-thickness distribution.

C3D4 gives a 65.8% tip deflection deficit on this grid. Both quadratic elements
recover the bending field for this load. The nonmatching linear tie carries the
axial load with a local stress error. Its large linear bending error persists even
with a small displacement jump. These results are specific to this mesh, material,
boundary conditions and load.

The separate conformal quadratic patch applies consistent exterior tractions
for σzz=1 MPa and restrains six rigid motion DOFs. Across 40 integration
points the greatest absolute stress-component error was 4.71e−15 MPa. The test
also checks all displacements and each nodal force against the analytical field
and the integrated face loads.

### Radioss constrained compression and interface controls

The tests used LAW1, ρ=1e−6 Mg/mm³ and E=1000 MPa. In the quadratic axial
control ν=0.25 and the imposed field is ux=−t·x with other components fixed.
Each integration step therefore loads the solid. The energy reference at printed
cycle time is ½(1200 MPa)t²V. The tests allow 6% for explicit time staggering.
Both builds produced these values.

| Native card | Cycles | Last printed t (s) | Translation KE (N·mm) | Compression IE (N·mm) |
|---|---:|---:|---:|---:|
| /TETRA10 | 22 | 9.671e−5 | 8.333e−8 | 9.448e−7 |
| /BRIC20 | 17 | 9.886e−5 | 5.000e−7 | 5.947e−6 |

The Windows animation converter retained displacement vectors at all 10 and
20 nodes. In the converted VTU, the tet cell has VTK type 10 and four corner
indices (`0 3 1 2`). The brick has VTK type 12 and eight corner indices
(`0 1 2 3 4 5 6 7`). A curved quadratic result surface cannot be reconstructed
from that cell connectivity. The native run and the converted topology have
different qualification scopes.

Native `/TETRA10` stores the element ID on a line followed by ten node IDs.
`/BRIC20` stores the element ID and eight corners, then eight and four midside
IDs. In reference corner numbering the brick's midsides follow 1–2, 2–3, 3–4,
4–1, 1–5, 2–6, 3–7, 4–8, 5–6, 6–7, 7–8, 8–5. The native keyword is
`/BRIC20`. Swapping midsides 5 and 10 on the tetra or 9 and 19 on the brick
caused both Starters to exit 2 before a restart. Their diagnostics included the following lines.

```text
ERROR ID :    489
** ERROR: BADLY SHAPED 10-NODE TETRA ELEMENT

ERROR ID :    484
** ERROR: ZERO OR NEGATIVE SUBVOLUME ON 3D ELEMENT
```

In the TYPE2 control, 25 Tet4 secondary nodes are tied to four Hex8 quads.
Each `/SURF/SEG` row includes a segment ID and four ordered node IDs. A
`/GRNOD/NODE` names the secondary nodes, and the `/INTER/TYPE2` parameter
row uses secondary group, main surface, Ignore=0 and Spotflag=1. The test
drives the tet end through a triangular velocity history to −0.012 mm and
holds it, with the far hex end clamped. ν=0. The two parts total 12 mm³.
Both Starters reported zero warnings. Both Engines ended with the following lines.

```text
     NORMAL TERMINATION
     TOTAL NUMBER OF CYCLES  :    2613
```

At the last printed cycle (0.01249 s), IE was 0.006003 N·mm against
the analytical 0.006, KE was 2.155e−10 N·mm, and mass error was zero.
Both native final animations were decoded with the Windows converter.
At t=0.0125017 s the maximum mismatch between a secondary displacement
and its interpolated master displacement was 5e−9 mm. The maximum axial
displacement error across all nodes was 2.391e−5 mm. A malformed earlier
segment row produced Starter warning 1079 and IE=0.005860 N·mm. The test
rejects that warning even if both processes exit successfully.

Native card definitions and compatibility follow the [Altair solid element
reference](https://help.altair.com/hwsolvers/rad/topics/solvers/rad/element_solids_starter_r.htm),
[segment definition](https://www.2022.help.altair.com/2022.1/hwsolvers/rad/topics/solvers/rad/surf_seg_starter_r.htm)
and [TYPE2 interface reference](https://2025.help.altair.com/2025/hwsolvers/rad/topics/solvers/rad/inter_type2_starter_r_2.htm).
The [solver oracle](../tests/test_mesher_solver_f0_gate.py) and [beam construction](../tests/mesher_bending_case.py)
generate the decks independently of Prep. Repeat the command under
[Build identity and replay](#build-identity-and-replay) with
`--basetemp scratch/mesher-f0/r2-qualified` to retain input decks, solver
listings, DAT and animation files on the local machine. The combined native run
and a focused rerun after tightening the mixed-patch output coverage and
bending stress assertions reported the following summaries.

```text
67 passed in 569.29s (0:09:29)
8 passed, 59 deselected in 15.20s
```

The focused rerun used `-q -s -k "nonmatching_tied or quadratic_conformal"`
on the same test file. The final test and beam generator Git blob IDs were
`fc5aae2846ab5fc55fe5ea85ad3141afefbb3d95` and
`9c0ee602c7bd9a6fdcafd3cc5cbfc4dbf8dbe97b`.

## Earlier linear and pyramid campaign

2026-11-27, Windows with Ubuntu WSL. Workbench baseline
`636532fbb15fa5ad37ee663ac24d18dcfbeae888`.
[Test code](../tests/test_mesher_solver_f0_gate.py) Git blob
`3f9f0bd147adc24dfad9fb02f577e6b1171d4bcf`.
The full native campaign passed 35 tests in 98.75 s without skips.
The recorded case directories are under `scratch/mesher-f0/final-mixed`.

## Generated mesh handoff

All three tested builds reject the native five-node pyramid card in a genuine
conformal Hex8–Pyr5–Tet4 patch. The hex and pyramid share nodes 5–8 across
the quadrilateral. The pyramid and tetrahedron share nodes 5, 6, 9 across the
triangle. CalculiX stops on `C3D5` (exit 201). Both RAD Starters stop on
`/PYRA5/1` (exit 2, error 100210) before writing a restart. No mixed-patch
stress, strain, equilibrium, bending, contact, tie, convergence, or energy
result exists. A generated mesh that requires a pyramid stays unavailable for
native handoff to these builds. The build-specific evidence applies to the
generated handoff. [Workbench native input policy](../DESIGN.md#1-불변-원칙).

| Mesh type | CalculiX 2.21 | OpenCourant 20261011 and OpenRadioss GNU build |
|---|---|---|
| Hex8 | `C3D8` and `C3D8I` pass affine controls | `/BRICK` with `/PROP/SOLID` passes translation control |
| Pyr5 | `C3D5` rejected as unknown element type | `/PYRA5` rejected as unrecognized option |
| Wedge6 | `C3D6` passes affine controls | `/PENTA6` with ordinary `/PROP/SOLID` fails with error 3107. With `/PROP/TYPE20`, Isolid=15, it passes translation as a **thick shell**. The ordinary solid wedge path remains unverified |
| Tet4 | `C3D4` passes affine controls | `/TETRA4` with `/PROP/SOLID` passes translation control |
| Tet10 | `C3D10` passes affine controls | `/TETRA10` is documented. This campaign did not run it |
| Quad4 shell | `S4` passes affine controls | `/SHELL` with `/PROP/SHELL` passes translation control. `/SH4N` rejected as unrecognized option (100210) |
| Tri3 shell | `S3` passes affine controls | `/SH3N` with `/PROP/SHELL` passes translation control |

The RAD shell controls use LAW1 and one through-thickness integration point.
An exploratory five-point quad run produced Starter warning 1084, which
switches LAW1 to global integration. The verified test specifies one point.
Altair documents the thick-shell restriction for `/PENTA6` and a degenerated
brick convention for ordinary solid wedges. That convention was not enabled
in this handoff or used to substitute for a pyramid.
[Radioss solids reference](https://help.altair.com/hwsolvers/rad/topics/solvers/rad/element_solids_starter_r.htm),
[Radioss shell mapping](https://2024.help.altair.com/2024/hwdesktop/engsol/topics/conversion_between_solvers/convert_radioss_to_optistruct_mapping.htm),
[CalculiX element input source](https://github.com/Dhondtguido/CalculiX/blob/master/src/elements.f).

## Independent controls

- CalculiX also solved a conformal Hex8–Wedge6–Tet4 control using one
  `C3D8`, one `C3D6`, and one `C3D4`. A quadrilateral shares nodes 5–8
  between the hex and wedge, and a triangle shares nodes 5, 6, 9 between
  the wedge and tet. The axial case integrates `σ n_z` over the exterior
  faces for `σ = 1 MPa` and constrains six rigid-body DOFs. Lateral
  contraction is free. The 11 integration points returned
  `σzz = 1 MPa`, `εzz = 0.001`, `εxx = εyy = −0.00025` to printed
  precision. The other stress components were at most `4.45e-16 MPa`
  in magnitude. The net printed axial force was `+4e-8 N`, and
  `Σ z_i F_iz = 1.66666674 N·mm` matches `σ V = 5/3 N·mm`. In the
  second case, an infinitesimal rotation about y plus translation on the same
  mesh produced maximum stress `9.22e-16 MPa`, strain `7.59e-19`, and
  nodal force `1.29e-16 N` in magnitude. Both checks passed for this
  wedge transition. The pyramid candidate remains blocked.
- CalculiX applied `u_x = 0.001 x`, `u_y = u_z = 0` with
  `E = 1000 MPa`, `ν = 0.25`. All solid integration points returned
  `(Sxx, Syy, Szz) = (1.2, 0.4, 0.4) MPa`. Both shell elements returned
  `(1.066667, 0.2666667, ~0) MPa`, consistent with plane stress.
  These single-element kinematic controls prescribe all translational DOFs.
  The tests check all six stress and strain components, prescribed nodal
  displacements, total force balance within `1e-6 N`, and
  `Σ x_i F_ix = ∫ Sxx dV`. Independent infinitesimal rigid translation and
  rotation gave stress, strain and individual RF within `1e-7` of zero.
- CalculiX C3D8I cantilever, length 12 mm, 1 mm square section, 0.01 N end load.
  Timoshenko reference is 0.06948 mm in magnitude. Axial refinement from 4 to
  16 elements reduced the error from 3.19% to 0.68%
  (0.06726165 mm → 0.06900705 mm). Splitting the 16-element mesh at midspan
  and tying coincident faces reproduced 0.06900705 mm to output precision.
  Clamp reaction was +0.01 N in all three solves.
- CalculiX frictionless surface-to-surface compression of two C3D8
  blocks gave +1.0 N base reaction for −1.0 N applied load.
  Top displacement −0.001678755 mm agrees within 0.13% with the constrained
  elastic shortening plus penalty overlap (−0.001676667 mm).

The RAD controls initialized every node at `1 mm/s` along x and ran to
`0.0001 s` with `ρ = 1e-6 Mg/mm³`, `E = 1000 MPa` and `ν = 0.25`.
Both builds gave the same printed cycle diagnostics below. The translational
KE matches `½mv²` within 0.1%, and the external work, printed energy error and
mass error are zero. Internal energy stays within `1e-12 N·mm` of zero. The
Windows converter and native reader additionally check every node's
`(t, 0, 0) mm` displacement on the final saved frame for `/BRICK`,
`/TETRA4`, `/SHELL`, and `/SH3N`. In a separate TYPE20 `/PENTA6` output
check, the converter wrote VTK type 12 with connectivity `0 1 2 0 3 4 5 3`
for six points. Workbench reported `INVALID_CONNECTIVITY` on all four
frames and retained the native outputs. The thick-shell solve is a solver
control only.

| Positive RAD control | Cycles | Final printed time (s) | KE (N·mm) | IE (N·mm) |
|---|---:|---:|---:|---:|
| /BRICK | 5 | 9.886e-5 | 5e-7 | 4.365e-27 |
| /TETRA4 | 8 | 9.989e-5 | 8.333e-8 | 1.106e-25 |
| /SHELL | 4 | 7.524e-5 | 5e-8 | 0 |
| /SH3N | 6 | 9.913e-5 | 2.5e-8 | 0 |
| /PENTA6 with TYPE20 | 12 | 9.612e-5 | 2.5e-7 | 6.224e-31 |

The RAD controls establish output under rigid translation. That campaign did not exercise mixed explicit
bending, contact, ties or hourglass qualification. Rigid
translation supplies no usable hourglass-to-internal-energy denominator.

## Build identity and replay

| Executable | SHA-256 |
|---|---|
| Ubuntu `/usr/bin/ccx`, version 2.21 | `6adaabf5bf0382fc2bfd692b984320ed375dba777f7dc8297562f818043faa1b` |
| OpenCourant Windows `starter_win64.exe`, release latest-20261011 | `606b8d9785bb79305ede5e1467fff6e07331678ae619fe9d4e118df6c5c1309e` |
| OpenCourant Windows `engine_win64.exe` | `7530cba04c5e73fa93167496d5e11476318084c84fff9c6b0dfccc35267a81a9` |
| OpenRadioss `starter_linux64_gf`, local source tree revision `2472f27484c2f19c2300d9c18d522b297ce818eb` | `b09ed11250aea33e95a0002fc5ee5e90b92e37ddb4c446db3611e7af739281bd` |
| OpenRadioss `engine_linux64_gf` | `59096a444c9646901a52252b11e24bace7e3b63d26776f20d2b9fa29b7aeaaaf` |

From the Workbench worktree root, set the installation paths for this machine.

```powershell
New-Item -ItemType Directory -Force scratch/mesher-f0 | Out-Null
$env:TEMP = "$PWD/scratch/mesher-f0"
$env:TMP = $env:TEMP
$env:PYTHONPATH = "$PWD"
$env:CAELAB_OPENCOURANT = 'C:/SourceCodes/.tools/opencourant-20261011/win64/OpenCourant'
$env:CAELAB_F0_OPENRADIOSS = 'C:/SourceCodes/OpenRadioss'
& 'C:/Program Files/Python313/python.exe' -m pytest tests/test_mesher_solver_f0_gate.py --basetemp scratch/mesher-f0/replay -q
```

The tests generate each native deck from the committed source. Keep the
named basetemp to reopen the exact inputs, STDOUT/STDERR, CalculiX DAT,
RAD Starter/Engine listings, restarts, animations and decoded OpenCourant
frames. WSL runs use `wsl -d Ubuntu --cd <case> -e env OMP_NUM_THREADS=1 ccx -i model`
or the installed GNU Starter and Engine with their reader/shared library paths.
Windows runs use the installed Starter and Engine and the DLL environment
from the Workbench connection. Each RAD step uses
`-i model_0000.rad -nt 1` or `-i model_0001.rad -nt 1`.
