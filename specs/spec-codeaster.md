# Code_Aster Native Adapter Implementation Specification

## 1. Scope & Single-File Responsibility
- Target file: `caelab/native/code_aster.py`
- Test file: `tests/test_code_aster_native.py`
- Worker isolation: Do NOT edit `caelab/workbench.py`, `caelab/jobs.py`, `caelab/connections.py`, or `caelab/native/adapter.py`.

## 2. Invariants & Domain Rules (From CAE Domain Expert)
1. **Multi-File Deck Structure**:
   - Primary command deck: `.comm` (Python-based Code_Aster commands).
   - Companion mesh file: `.med` (MED format, HDF5-based).
   - The primary command deck references the mesh file (e.g. `LIRE_MAILLAGE(FORMAT='MED', UNITE=20)`).
2. **Result Formats**:
   - Field results: `.rmed` (MED format containing displacement `DEPL`, stress `SIEF_ELGA`/`SIEF_NOEU`, etc.).
   - Text output / numerical tables: `.resu` (ASCII printout from `IMPR_RESU`).
3. **VTU Conversion & Output Contract**:
   - `collect_codeaster_results(workdir: Path, *, check_cancelled=None) -> dict`:
     - Reads `.rmed` or `.resu` in `workdir`.
     - Produces exchange VTU file in `workdir / "results" / "frame-000000.vtu"`.
     - Returns dictionary matching standard schema:
       ```python
       {
           "status": "readable" | "partial" | "failed",
           "frames": [
               {
                   "index": 0,
                   "time": float,
                   "name": "Frame 0",
                   "vtu": "results/frame-000000.vtu",
                   "fields": {
                       "DISP": {"name": "DISP", "components": ["DX", "DY", "DZ", "Magnitude"], "type": "vector"},
                       "STRESS": {"name": "STRESS", "components": ["SIXX", "SIYY", "SIZZ", "SIXY", "SIXZ", "SIYZ", "VON_MISES"], "type": "tensor"}
                   }
               }
           ],
           "exchange_files": ["results/frame-000000.vtu"],
           "summary": {
               "max_displacement": float,
               "max_stress": float,
               "num_nodes": int,
               "num_elements": int,
               "solver_id": "codeaster"
           },
           "warnings": []
       }
       ```
4. **Units Contract**:
   - Standard SI units (`m, N, Pa, kg, s`). Preserves user declared units if specified.
5. **Execution Command**:
   - If `.export` file is present or generated, runs `run_aster export_file`.
   - On Windows/WSL: supports mock or containerized command execution path.

## 3. Unit Test Requirements (`tests/test_code_aster_native.py`)
- Test reading a synthetic or sample `.comm` and parsing keywords (`DEBUT`, `LIRE_MAILLAGE`, `AFFE_MATERIAU`, `MECA_STATIQUE`, `FIN`).
- Test result collection with mock `.rmed` / `.resu` outputs.
- Test error handling when result files are missing, corrupt, or report solver convergence error.
