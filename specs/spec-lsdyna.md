# LS-DYNA Native Adapter Implementation Specification

## 1. Scope & Single-File Responsibility
- Target file: `caelab/native/lsdyna.py`
- Test file: `tests/test_lsdyna_native.py`
- Worker isolation: Do NOT edit `caelab/workbench.py`, `caelab/jobs.py`, `caelab/connections.py`, or `caelab/native/adapter.py`.

## 2. Invariants & Domain Rules (From CAE Domain Expert)
1. **Multi-File Deck Structure**:
   - Primary keyword deck: `*.k` or `*.key` (`*KEYWORD`, `*CONTROL_TERMINATION`, `*NODE`, `*ELEMENT_SOLID`, `*MAT_PIECEWISE_LINEAR_PLASTICITY`).
   - Supports `*INCLUDE` hierarchical files. Adapter records all included file references for snapshotting.
2. **Result Formats**:
   - 3D Field animation: `d3plot` (standard binary plot file) or `d3plot01`, `d3plot02`.
   - Global ASCII energy/statistics: `glstat` (Kinetic, Internal, Spring/Damper, Sliding Interface, Total Energy, Energy ratio).
   - Nodal time history: `nodout` (displacements, velocities, accelerations).
   - Contact forces: `rcforc`.
3. **VTU Conversion & Output Contract**:
   - `collect_lsdyna_results(workdir: Path, *, check_cancelled=None) -> dict`:
     - Reads `d3plot` or `glstat`/`nodout` in `workdir`.
     - Generates VTU exchange files (`results/frame-000000.vtu`, etc.).
     - **CRITICAL DOMAIN KPI: Global Energy Balance Summary**:
       - `kinetic_energy`: float
       - `internal_energy`: float
       - `total_energy`: float
       - `sliding_energy`: float
       - `hourglass_energy`: float
       - `energy_ratio`: float (Total / Initial Total Energy, ideally ~1.00)
     - Standard summary schema:
       ```python
       {
           "status": "readable" | "partial" | "failed",
           "frames": [...],
           "exchange_files": ["results/frame-000000.vtu"],
           "summary": {
               "max_displacement": float,
               "kinetic_energy": float,
               "internal_energy": float,
               "hourglass_energy": float,
               "energy_ratio": float,
               "solver_id": "lsdyna"
           },
           "warnings": []
       }
       ```
4. **Units Contract**:
   - Unitless solver. Consistent unit system (`[mm, N, MPa, tonne, ms]` vs `[m, N, Pa, kg, s]`).

## 3. Unit Test Requirements (`tests/test_lsdyna_native.py`)
- Test reading `deck.k` and parsing `*INCLUDE` directives.
- Test parsing `glstat` ASCII file to extract kinetic, internal, hourglass, sliding interface energy and energy ratio.
- Test result collection with mock `d3plot` / `nodout` outputs.
- Test error termination handling (e.g. `Error termination`, out-of-core error, negative volume in solid element).
