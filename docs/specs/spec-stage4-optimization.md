# Specification: Stage 4-1 Optimization & Parametric Study 2-Panel Visualization Dashboard

**Author**: Antigravity Main (Gemini Flash High) & Reviewers (`chief-reviewer`, `cae-user-expert`)  
**Target Branch**: `feat/stage4-optimization`  
**Reference**: [DESIGN.md](../../DESIGN.md) (§11.5, §14), [AGENTS.md](../../AGENTS.md)

---

## 1. Goal

Transform `OptimizationPanel` from a cramped, text-only 1st-generation prototype into a commercial-grade 2-panel optimization dashboard (Left: 5-step workflow controller, Right: interactive visualization tabs with convergence, sensitivity tornado, and surrogate parity plots). Integrate the panel with `ProjectWindow` via a dedicated "도구(&T) ▸ 최적화…" menu entry, respecting all theme tokens, asynchronous cancellation, and honest failure logging.

---

## 2. Target Files & Responsibilities

1. **`apps/workbench_qt/optimization_panel.py`**:
   - Refactor main layout to use a horizontal `QSplitter` separating the 5-step controller (left) and the visualization dashboard (right).
   - **Left Controller**:
     - Step 1 (Setup): Variables table (name, bounds, base, unit), Objective (response, direction, unit).
     - Step 2 (DOE): Sample count, seed, holdout count, Run DOE button, Cancel button, status.
     - Step 3 (Surrogate): Model type combo (RBF / Polynomial), Fit button, holdout RMSE & relative error summary.
     - Step 4 (Optimization): Generations, population, algorithm combo, Run Optimization button, Cancel button, status.
     - Step 5 (Verification): Verify Optimum button, side-by-side predicted vs actual result, error fraction.
     - History Table: Clean list of evaluated candidates with source (`DOE`, `Test`, `Optimization (Pred)`, `Verify (Actual)`) and status (`SUCCEEDED`, `FAILED`, `INFEASIBLE`).
   - **Right Visualization Dashboard (`QTabWidget`)**:
     - **Tab 1: 수렴 이력 (Convergence History Plot)**:
       - Line chart showing objective progression across evaluations/iterations.
       - Best-so-far trace line.
       - Scatter markers for candidates, clearly distinguishing `SUCCEEDED` points from `FAILED`/`INFEASIBLE` points (red markers).
     - **Tab 2: 민감도 분석 (Sensitivity Tornado Chart)**:
       - Horizontal bar chart of normalized sensitivity coefficients (from `caelab.numerical.sensitivity` or `analyze_candidates`).
       - Ranked from highest impact to lowest impact.
       - Differentiated positive vs negative correlation colors.
     - **Tab 3: 대리모델 검증 (Surrogate Parity Plot)**:
       - Scatter plot of Actual FEA Value (X-axis) vs Surrogate Predicted Value (Y-axis) on the holdout test set and the final verified optimum point.
       - 45-degree reference line ($y = x$) and $\pm 5\% / \pm 10\%$ error guidelines.
       - Holdout RMSE / Max Error summary.
   - **Theme Integration**:
     - Chart canvas uses `matplotlib.backends.backend_qtagg.FigureCanvasQTAgg` with `matplotlib.figure.Figure`.
     - All plot elements (figure background, axes background, spine colors, tick colors, label text, grid lines, legend) must dynamically sync with `apps/workbench_qt/theme.py` tokens (`canvas_bg`, `card_bg`, `text_main`, `text_muted`, `border`, `accent`, `danger`, etc.).
   - **Cancellation & Non-blocking**:
     - Provide `self._cancel_requested` flag checked in evaluation loops, calling `QApplication.processEvents()` so the UI remains responsive and cancellation terminates cleanly.

2. **`apps/workbench_qt/project_window.py`**:
   - Add menu action under `self.menuBar()`: `도구(&T)` -> `최적화 및 매개변수 연구...`.
   - Clicking this action opens or raises `OptimizationPanel`.
   - If an Analysis box with a valid model is selected or available in the project, automatically bind the study or provide a clear status prompt.

3. **`tests/test_optimization_panel.py`**:
   - Update existing tests to reflect the new 2-panel architecture.
   - Add new tests verifying:
     - The presence of the 3 visualization tabs (`QTabWidget`).
     - Figure canvases render without exceptions.
     - Plot redraws update cleanly when DOE, surrogate fitting, optimization, and verification are triggered.
     - `apply_theme()` updates plot styles without crashing in both dark and light modes.
     - Cancellation stops DOE loops and records partial results without corrupting state.

---

## 3. Strict Invariants

1. **[Invariant 1: Visual Disambiguation]**: Actual solver evaluations (`DOE`, `Test`, `Verify`) and surrogate predictions (`Opt Best (Pred)`) must be visually distinct in both tables and plots.
2. **[Invariant 2: Honest Failure]**: Failed evaluations (`FAILED`) must NEVER be filled with `0.0`, `NaN`, or fake numbers. They must be logged with their exact failure reason and filtered out of surrogate training.
3. **[Invariant 3: Theme Adherence]**: Zero hardcoded black (`#000000`) or white (`#ffffff`) text/backgrounds in the plots. Always bind to `theme.palette()`.
4. **[Invariant 4: Clean Public Surface]**: No internal Python pointer addresses or raw UUIDs in labels or tooltips.
5. **[Invariant 5: Zero Regression]**: All 33 existing numerical and optimization tests must continue to pass cleanly.
