# Specification: Commercial-Grade CAE MDO Engine & GUI Rearchitecture

> Superseded on 2026-10-26 by [the clean-slate MDO specification](spec-clean-slate-mdo-design.md). This proposal is historical: its numerical-package reuse, panel layout, statistics and application semantics are not implementation instructions.

**Status**: PROPOSED (Under User Review)  
**Date**: 2026-10-26  
**Authors**: Antigravity Main Session, in collaboration with `chief-reviewer`, `cae-user-expert`, and Codex (`gpt-6-astra`)  
**Target Repository**: `pikachu444/caelab-workbench`  
**Reference Standards**: [DESIGN.md](../../DESIGN.md), [AGENTS.md](../../AGENTS.md), ANSYS DesignXplorer/optiSLang, SIMULIA Isight, Siemens HEEDS, ESTECO modeFRONTIER, Altair HyperStudy

---

## 1. Executive Summary & Problem Definition

### 1.1 The Failure of the Legacy 1st-Generation Prototype
The previous optimization implementation (`apps/workbench_qt/optimization_panel.py`) was an ad-hoc 5-step wizard built around simple in-memory SciPy mathematical benchmark functions (e.g. Rosenbrock, Sphere) and 1D analytical beam formulas. 

It exhibited critical architectural flaws that rendered it unusable as a commercial-grade CAE tool:
1. **Disconnection from Real CAE Solvers**: No parameter extraction or substitution into native solver decks (`.inp`), no meshing loop, and zero execution provenance through the workbench's `JobManager`.
2. **Lack of Multi-Objective Optimization (MOO)**: Restricted to scalar single-objective optimization (`minimize` or `maximize`), incapable of finding Pareto frontiers between conflicting structural criteria (e.g. minimizing mass while minimizing max von Mises stress and deflection).
3. **No True Constraint Handling**: Lacked explicit inequality ($g_j(x) \le 0$) and equality ($h_k(x) = 0$) constraint support in the UI, treating all points as unconstrained or relying on opaque SciPy penalties.
4. **Primitive Surrogate Modeling**: Restricted to basic polynomial fits or plain RBF without uncertainty estimation ($\sigma^2(x)$), essential for Bayesian optimization and trust-region CAE studies.
5. **Toy-like Visualization & UX**: Awkward, unscalable Matplotlib plots with hardcoded bar heights, chaotic population trace webs, and zero commercial MDO visualization staples (2D/3D Pareto fronts, Parallel Coordinates, 3D Response Surfaces with slice sliders, Sensitivity Tornado charts).

### 1.2 Objective
Completely rearchitect both the **numerical optimization engine (`caelab/numerical/`)** and the **MDO Studio GUI (`apps/workbench_qt/mdo_studio/`)** from scratch, aligning with established commercial CAE MDO paradigms (optiSLang, Isight, HEEDS, modeFRONTIER, HyperStudy).

---

## 2. Commercial MDO Benchmark & Architectural Principles

Based on primary research into commercial MDO software manuals and workflows:

| Software | Core Architectural Strengths | Key Lessons for `caelab-workbench` |
|---|---|---|
| **ANSYS optiSLang / DesignXplorer** | Metamodel of Optimal Prognosis (MOP), parameter sensitivity filtering before optimization, Pareto front trade-off exploration. | We must implement Global Sensitivity Analysis (Sobol/Morris) to filter non-influential variables, and provide Kriging with confidence intervals. |
| **SIMULIA Isight** | Visual workflow integration, asynchronous solver execution loops, approximation models with cross-validation. | The MDO engine must dispatch evaluations through `JobManager` asynchronously, recording full solver execution provenance. |
| **Siemens HEEDS** | SHERPA hybrid adaptive search, explicit multi-constraint handling with feasibility tracking. | The engine must treat constraints as first-class citizens, tracking $g(x) \le 0$ violation magnitude and feasibility status (`FEASIBLE` vs `INFEASIBLE`). |
| **ESTECO modeFRONTIER** | Gold-standard Multi-Objective Pareto optimization (NSGA-II, MOSA), multi-criteria decision making (MCDM), Parallel Coordinates plots. | Multi-objective optimization with Pareto dominance ranking and Parallel Coordinates visualization is non-negotiable. |
| **Altair HyperStudy** | Table of Design Points (TDP), parameterized token binding in FE decks, Tornado charts, "Set as Current" design point feedback. | The GUI must center around an interactive Table of Design Points, with one-click feedback of optimal parameter values to CAD/INP models. |

---

## 3. Decoupled 3-Layer Architecture

The rearchitecture separates concerns into three strictly decoupled layers:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   Layer 3: GUI - MDO Studio Workspace                  │
│  [Study Tree]  [Parameter Dock]  [Table of Design Points]  [Viz Studio] │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Study Config & Async Control
┌───────────────────────────────────▼────────────────────────────────────┐
│              Layer 2: MDO Study Manager & CAE Provenance               │
│  - Parameter Binding (CalculiX .inp token replacement)                 │
│  - Batch Evaluation Dispatcher via JobManager                          │
│  - Design Point Caching, Stale Tracking & Crash Recovery               │
│  - "Set as Current Design Point" Feedback Loop                         │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Pure Math & Numerical Interfaces
┌───────────────────────────────────▼────────────────────────────────────┐
│                    Layer 1: Modular Numerical Engine                   │
│   variables | objectives | constraints | doe | surrogate | optimize    │
│                 pareto | sensitivity | uq                              │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Layer 1: Numerical Engine Specifications (`caelab/numerical/`)

The monolithic `core.py`, `fit.py`, and `analysis.py` will be modularized into 8 dedicated, single-responsibility modules:

### 4.1 `caelab/numerical/variables.py`
- **Variable Types**:
  - `ContinuousVariable`: `lower_bound`, `upper_bound`, `initial_value`, `unit`, `distribution` (uniform, normal).
  - `DiscreteVariable`: integer step size or explicit discrete allowed values (e.g. standard sheet thicknesses `[1.0, 1.2, 1.5, 2.0, 2.5] mm`).
  - `CategoricalVariable`: discrete unordered choices (e.g. material selection `["AL6061", "STEEL_AISI1045", "TI_6AL_4V"]`).
  - `LinkedVariable`: dependent on another variable via an analytical expression or ratio (e.g. $w_{flange} = 0.5 \times h_{web}$).
- **Transformation & Encoders**:
  - Normalization to $[0, 1]$ or $[-1, 1]$ for surrogate training and numerical stability.
  - Rounding/quantization for discrete variables.
  - One-hot or integer encoding for categorical parameters.

### 4.2 `caelab/numerical/objectives.py`
- Support for multiple conflicting objectives:
  $$\min / \max \quad f_m(x), \quad m = 1, \dots, M$$
- Target-matching objectives: $\min |f(x) - T|$ with tolerance bands.
- Weights and normalization scales to prevent large-magnitude responses (e.g. stress in Pa) from dominating small-magnitude ones (e.g. mass in kg).

### 4.3 `caelab/numerical/constraints.py`
- Explicit inequality and equality constraints:
  $$g_j(x) \le 0, \quad j = 1, \dots, J$$
  $$h_k(x) = 0, \quad k = 1, \dots, K \quad (|h_k(x)| \le \epsilon)$$
- Constraint violation evaluation:
  $$v_j(x) = \max(0, g_j(x))$$
- Feasibility classification:
  - `FEASIBLE`: $\forall j, v_j(x) = 0$
  - `MARGINAL`: $0 < v_j(x) \le \text{tolerance}$
  - `INFEASIBLE`: $\exists j, v_j(x) > \text{tolerance}$
- Penalty formulations for metaheuristic solvers ($P(x) = \sum w_j v_j(x)^2$).

### 4.4 `caelab/numerical/doe.py`
- **Opt-LHS (Optimal Latin Hypercube Sampling)**: Enhanced LHS optimizing the Audze-Eglais or maximin distance criterion to eliminate correlation and sample clustering.
- **Sobol Sequence**: Low-discrepancy quasi-random sequence providing uniform multi-dimensional space filling.
- **Classic Response Surface DOEs**:
  - Full Factorial ($2^k, 3^k$).
  - Box-Behnken (efficient 3-level design without corner points).
  - Central Composite Design (CCD: factorial + star/axial + center points).
- **Holdout Validation Split**: Deterministic holdout partitioning (e.g. 80% train, 20% test) for surrogate validation.

### 4.5 `caelab/numerical/surrogate.py`
- **Kriging / Gaussian Process Regression (GPR)**:
  - Kernel options: Matern 5/2, Matern 3/2, RBF (Squared Exponential).
  - Automatic hyperparameter tuning via maximum likelihood estimation (L-BFGS-B).
  - **Crucial Capability**: Must return both predictive mean $\hat{y}(x)$ and predictive variance $\sigma^2(x)$, enabling $95\%$ confidence interval bounds ($\hat{y} \pm 1.96\sigma$).
- **Radial Basis Functions (RBF)**: Thin-plate spline, multiquadric, linear RBF.
- **Polynomial Response Surface (PRS)**: 1st-order linear, 2nd-order full quadratic with cross-terms ($x_i x_j$).
- **Surrogate Quality Metrics**:
  - Root Mean Square Error ($RMSE$).
  - Coefficient of Determination ($R^2$).
  - Maximum Absolute Error ($MAE$).
  - Leave-One-Out Cross-Validation (LOOCV).

### 4.6 `caelab/numerical/optimize.py`
- **Single-Objective Optimization (SOO)**:
  - Gradient-based: SLSQP (Sequential Least Squares Programming) with explicit constraints and bounds.
  - Derivative-free local: Nelder-Mead simplex with bound constraints.
  - Global: Differential Evolution (DE) supporting integrality constraints and nonlinear constraints.
- **Multi-Objective Optimization (MOO)**:
  - **NSGA-II (Non-dominated Sorting Genetic Algorithm II)**: Fast non-dominated sorting, crowding distance assignment, simulated binary crossover (SBX), polynomial mutation.
  - Population size, generation limits, mutation/crossover probabilities.

### 4.7 `caelab/numerical/pareto.py`
- Fast non-dominated sorting algorithms partitioning candidate solutions into Pareto fronts ($F_1, F_2, \dots, F_k$).
- Pareto front extraction ($F_1$: mutually non-dominated, feasible solutions).
- Crowding distance calculation to assess diversity along the front.
- Hypervolume Indicator (HV) for convergence and spread assessment against a reference point.

### 4.8 `caelab/numerical/sensitivity.py`
- **Global Sensitivity Analysis (GSA)**:
  - Sobol Sensitivity Analysis: First-order indices $S_i$ (main effect), Total-order indices $S_{Ti}$ (including interactions).
  - Morris Elementary Effects: Efficient qualitative screening for high-dimensional models ($D > 10$).
- **Correlation Matrix**: Pearson (linear) and Spearman (rank-order monotonic) correlation between all inputs and all responses.
- **Tornado Ranking**: Ranked parameter contributions normalized to $100\%$, with directional polarity (+ / -).

---

## 5. Layer 2: MDO Study Manager & CAE Provenance (`caelab/study/`)

### 5.1 Native Solver Parameter Binding
- **CalculiX Deck Token Binder**:
  - User defines tokens in the CalculiX `.inp` deck (e.g. `*SHELL SECTION, ELSET=Eall, MATERIAL=STEEL\n<THICKNESS>, 5`).
  - The binder replaces tokens with candidate values for each design point, preserving original deck structure, boundary conditions, and element sets.
- **FreeCAD / Mesh Parametric Hook**:
  - Scripted parametric update for `.FCStd` models, regenerating mesh via Gmsh/FreeCAD before solver execution when CAD geometry is parameterized.

### 5.2 Asynchronous Batch Evaluation via `JobManager`
- Every Design Point (DP) is assigned a persistent identifier (`DP_0001`, `DP_0002`, ...).
- Evaluations are dispatched to `JobManager` as non-blocking background tasks.
- For each DP, the workbench maintains:
  - Evaluated parameter dictionary.
  - Working directory snapshot (`<project>.runs/mdo/<study_id>/dp_<index>/`).
  - Solver exit status, execution duration, and full stdout/stderr log.
  - Parsed response outputs (Mass, Max von Mises Stress, Displacement, Reaction Force).
  - Computed constraint residuals and feasibility flag.
- **Crash Recovery & Caching**: If a DP crashes or diverges, it is logged as `FAILED` with the exact solver error, without halting the entire optimization campaign. Evaluated points are cached by parameter hash to prevent redundant solver runs.

### 5.3 "Set as Current Design Point" Feedback Loop
- In the Table of Design Points or Pareto front view, the user can right-click any desirable candidate and select **"Set as Current Design Point"** (상용 MDO 필수 기능).
- This automatically:
  1. Updates the native `.inp` or FreeCAD model with the candidate's variable values.
  2. Marks the main project tree as updated.
  3. Prepares the native analysis window for full-field post-processing in ParaView.

---

## 6. Layer 3: MDO Studio GUI Specification (`apps/workbench_qt/mdo_studio/`)

The cramped, toy-like 2-panel widget is replaced by a professional **4-Area MDO Workspace**:

```
+-------------------------------------------------------------------------------------------------------+
| File(F)   Edit(E)   Study(S)   View(V)   Help(H)                                                      |
+-------------------------------------------------------------------------------------------------------+
|  AREA 1: STUDY TREE    |  AREA 2: SETUP DOCK / CONFIGURATION TABS                                     |
|  v MDO Study: Wing_Opt |  [Variables] [Objectives] [Constraints] [DOE Config] [Algorithm Setup]       |
|    > Parameters        |  --------------------------------------------------------------------------- |
|    > DOE Setup         |  Name        Type       Lower    Upper    Default   Unit     Distribution      |
|    > Surrogate Models  |  t_skin      Continuous 1.0      5.0      2.5       mm       Uniform           |
|    > Optimization Run  |  h_rib       Continuous 20.0     60.0     40.0      mm       Uniform           |
|    > Trade-Off (Pareto)|  mat_choice  Discrete   -        -        AL6061    -        [AL, TI, STEEL]   |
|                        |  [+ Add Variable]  [- Remove]  [Auto-Detect from INP Tokens...]              |
+------------------------+------------------------------------------------------------------------------+
|  AREA 3: TABLE OF DESIGN POINTS (TDP)                                                                 |
|  ID      | Status    | Feasibility | t_skin [mm] | h_rib [mm] | Mass [kg] | Max Stress [MPa] | Actions |
|  DP_0001 | SUCCEEDED | FEASIBLE    | 2.500       | 40.000     | 12.45     | 185.2            | [Apply] |
|  DP_0002 | SUCCEEDED | INFEASIBLE  | 1.050       | 21.200     | 7.82      | 342.1 (Violated) | [Apply] |
|  DP_0003 | SUCCEEDED | FEASIBLE *  | 1.850       | 48.600     | 9.15      | 210.4            | [Apply] |
|  [Run Selected]  [Stop Run]  [Export CSV]  [Set Selected as Current Design Point (CAD/INP)]          |
+-------------------------------------------------------------------------------------------------------+
|  AREA 4: INTERACTIVE MDO VISUALIZATION STUDIO (Tabs)                                                  |
|  [Tab 1: 2D/3D Pareto Front] [Tab 2: Parallel Coordinates] [Tab 3: 3D Response Surface] [Tab 4: Tornado]
|  +-------------------------------------------------------------------------------------------------+  |
|  | (Interactive Matplotlib / Qt Canvas with dark/light theme sync, crosshair inspection,           |  |
|  |  Pareto front frontier line, feasible/infeasible color coding, dynamic variable sliders)        |  |
|  +-------------------------------------------------------------------------------------------------+  |
+-------------------------------------------------------------------------------------------------------+
```

### 6.1 Area 1: MDO Study Tree
Hierarchical workflow navigator:
- **Study Properties**: Study name, execution directory, linked solver deck (`CalculiX`).
- **Parameter Hub**: Variable bounds, discrete sets, linked rules.
- **Criteria Hub**: Objectives ($\min/\max$) and Constraints ($g(x) \le 0$).
- **Design of Experiments (DOE)**: Sampling method, sample budget, run status.
- **Surrogate Fitting**: Model type (Kriging / RBF / PRS), cross-validation error, training status.
- **Optimization Strategy**: Algorithm selection (NSGA-II, SLSQP, DE), convergence tolerances.
- **Trade-Off & Post-Processing**: Pareto selection, correlation analysis, sensitivity charts.

### 6.2 Area 2: Parameter & Setup Dock
Context-sensitive property editor adapting dynamically to the selected node in the Study Tree:
- Full support for adding/editing variables with explicit physical units (`mm`, `MPa`, `kg`, `N`).
- Expression builder for constraint limits (e.g. `Max_Stress <= 250.0 MPa`).
- Objective priority weights and target values.

### 6.3 Area 3: Table of Design Points (TDP)
- Commercial-grade tabular design point browser (similar to ANSYS DesignXplorer & HyperStudy).
- Displays all evaluated candidates with real-time status updates (`QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`).
- Visual badges:
  - Feasibility badge: Green `FEASIBLE`, Amber `MARGINAL`, Red `INFEASIBLE`.
  - Pareto status badge: Gold `PARETO OPTIMAL (Rank 1)`.
- Interactive actions:
  - Double-clicking a row highlights the corresponding point in all active visualization charts.
  - "Set as Current Design Point" pushes values directly into the active `.inp` or FreeCAD document.

### 6.4 Area 4: Interactive MDO Visualization Studio

#### Tab 1: 2D / 3D Pareto Frontier Plot
- Plots trade-offs between conflicting objectives (e.g. X: Mass [kg], Y: Max Stress [MPa], Bubble Size: Deflection [mm]).
- Non-dominated points are connected by a Pareto frontier line or marked with distinct star glyphs.
- Dominated points are displayed with low opacity; infeasible points are marked with red 'X' markers.
- Lasso or click selection directly selects the row in the Table of Design Points.

#### Tab 2: Parallel Coordinates Plot
- Standard MDO visual trade-off tool: vertical parallel axes representing each design variable, constraint, and objective.
- Each design point is drawn as a continuous polyline connecting its values across all axes.
- Color mapping by objective value or feasibility.
- Interactive range filtering (brushing): user can drag ranges along any axis (e.g. `Max Stress <= 250`) to visually filter down the qualifying design candidates.

#### Tab 3: Dynamic 3D Response Surface Viewer
- 3D surface plot displaying predicted response $f(x_1, x_2)$ for any selected pair of variables.
- 2D contour projection on the bottom floor.
- Slice sliders for all remaining variables ($x_3, x_4, \dots, x_N$) allowing the user to dynamically slice through high-dimensional design spaces in real-time.
- Uncertainty envelope visualization: translucent upper and lower bounding surfaces ($\hat{y} \pm 2\sigma$) derived from the Kriging surrogate.

#### Tab 4: Sensitivity Tornado Chart
- Ranked horizontal bar chart displaying the sensitivity of selected objectives/constraints to each design variable.
- Clearly displays percentage contribution and positive/negative correlation direction.
- Based on Sobol total-order indices or Spearman rank correlation.

#### Tab 5: Convergence & Constraint History
- Iteration/evaluation progression trace with moving best-so-far step line.
- Secondary axes showing constraint violation magnitude, demonstrating how the optimizer navigates into the feasible region.

---

## 7. Migration & Phased Implementation Roadmap

To maintain repository stability and adhere to [AGENTS.md](../../AGENTS.md) quality rules:

### Phase 1: Core Engine Modularization (`feat/mdo-engine-core`)
- Implement modular `caelab/numerical/`:
  - `variables.py`, `objectives.py`, `constraints.py`, `doe.py`, `surrogate.py` (with Kriging GPR), `optimize.py` (with NSGA-II & SLSQP), `pareto.py`, `sensitivity.py`.
- Comprehensive unit and contract tests in `tests/test_numerical/`.
- Ensure 100% backward compatibility for existing legacy imports.

### Phase 2: Study Manager & Native Solver Parameter Binder (`feat/mdo-study-manager`)
- Implement `caelab/study/`:
  - `study_manager.py`, `inp_binder.py`, `batch_runner.py` (connected to `JobManager`).
- Integration tests with CalculiX native decks (beam, plate, bracket) testing end-to-end token substitution, asynchronous execution, and response extraction.

### Phase 3: Commercial MDO Studio GUI & Interactive Visualizations (`feat/mdo-studio-gui`)
- Implement `apps/workbench_qt/mdo_studio/`:
  - 4-area workspace layout (`mdo_window.py`).
  - Table of Design Points (`design_points_table.py`).
  - Interactive Visualization canvases (`pareto_plot.py`, `parallel_coords.py`, `response_surface_3d.py`, `tornado_chart.py`).
  - Dynamic theme integration with `theme.palette()`.
- Windows headless and interactive screenshot validation.

---

## 8. Non-Negotiable Invariants & Validation Rules

1. **[Invariant 1: First-Class Constraints]**: Never drop or ignore user constraints. Every candidate must report explicit violation magnitudes and feasibility status.
2. **[Invariant 2: Multi-Objective by Design]**: The engine and UI must treat multi-objective Pareto optimization as a primary workflow, not an afterthought.
3. **[Invariant 3: Full Solver Provenance]**: Every evaluated design point must link to its actual execution working directory, input deck snapshot, and solver log. No fabricated numbers.
4. **[Invariant 4: Safe Model Feedback]**: "Set as Current Design Point" must validate parameter types and bounds before modifying user `.inp` or CAD files, preserving original backups.
5. **[Invariant 5: Theme Synchronization]**: All 5 visualization tabs must dynamically update their palette, axes, labels, and legends when switching between Dark and Light mode.
6. **[Invariant 6: Visual Inspection Mandate]**: Every visual UI update must be inspected using real screen captures before claiming completion.
