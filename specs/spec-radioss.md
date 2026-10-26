# OpenRadioss Native Adapter Implementation Specification

## 1. Scope & Single-File Responsibility
- Target file: `caelab/native/radioss.py`
- Test file: `tests/test_radioss_native.py`
- Worker isolation: Do NOT edit `caelab/workbench.py`, `caelab/jobs.py`, `caelab/connections.py`, or `caelab/native/adapter.py`.

## 2. Invariants & Domain Rules (From CAE Domain Expert)
1. **Multi-File Deck Structure**:
   - Starter deck: `*_0000.rad` (Block format: geometry, materials, parts, boundary conditions).
   - Engine deck: `*_0001.rad` (Time step control, `/ANIM/DT`, `/RUN`, `/DT/NODA`).
   - If user supplies `model_0000.rad`, the adapter verifies presence of `model_0001.rad` or provides standard defaults.
2. **Result Formats**:
   - 3D Animation time history: `A001`, `A002`, ... or converted `.h3d` / `.vtk`.
   - Time History ASCII/binary: `T01` (energy balance, contact force, sensor curves).
   - Engine listing / log: `*_0001.out` (energy error %, time step drop, warnings).
3. **VTU Conversion & Output Contract**:
   - `collect_radioss_results(workdir: Path, *, check_cancelled=None) -> dict`:
     - Reads `A001` or animation frames in `workdir`.
     - Produces exchange VTU files (`results/frame-000000.vtu`, etc.) or time series PVD (`results/time_series.pvd`).
     - **CRITICAL DOMAIN KPI: Global Energy Balance Summary**:
       - `kinetic_energy`: float
       - `internal_energy`: float
       - `total_energy`: float
       - `hourglass_energy`: float
       - `hourglass_ratio`: float (Hourglass / Internal energy, recommended < 0.10)
       - `energy_error`: float (%)
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
               "hourglass_ratio": float,
               "energy_error": float,
               "solver_id": "radioss"
           },
           "warnings": []
       }
       ```
4. **Units Contract**:
   - Unitless solver. Consistent unit system (`[mm, N, MPa, tonne, ms]` vs `[m, N, Pa, kg, s]`). Warns on inconsistent mass/time combinations.

## 3. Unit Test Requirements (`tests/test_radioss_native.py`)
- Test reading `_0000.rad` and identifying starter blocks (`/BEGIN`, `/NODE`, `/PART`, `/MAT/PLAS_JOHNS_COOK`).
- Test parsing `_0001.out` energy table and verifying extraction of kinetic, internal, hourglass, and total energy error %.
- Test result collection with mock animation file and verify VTU generation.
- Test solver failure detection from return code or negative volume / runaway error in log.
