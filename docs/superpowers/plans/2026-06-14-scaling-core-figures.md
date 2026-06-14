# Scaling Core Figures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate the six publication-ready Core Figures from the completed GPU MPI scaling campaigns as reproducible SVG and PDF artifacts.

**Architecture:** A single Matplotlib entry point loads validated TSV records with the Python standard library, derives plot-ready series through small pure functions, and renders six independent figures. A unittest module exercises parsing, deduplication, critical-rank communication aggregation, and a complete temporary-directory render.

**Tech Stack:** Python 3, `csv`, `argparse`, `unittest`, Matplotlib 3.10.

---

### Task 1: Specify data loading and transformations

**Files:**
- Create: `experiments/results/raw/gpu_mpi_scaling_2080ti_3502237/plot_script/test_plot_core_figures.py`
- Create: `experiments/results/raw/gpu_mpi_scaling_2080ti_3502237/plot_script/plot_core_figures.py`

- [ ] Write failing tests that import the plotting module and require: typed TSV loading, required-column validation, unique profile-point selection, and critical-rank communication aggregation.
- [ ] Run `python3 -m unittest plot_script/test_plot_core_figures.py -v` from the campaign directory and confirm failure because the plotting module does not exist.
- [ ] Implement the minimal loader and transformation helpers. Reject missing columns, non-finite numbers, non-PASS summary rows, inconsistent duplicate point records, and absent critical-rank communication rows.
- [ ] Re-run the focused tests and require all transformation tests to pass.

### Task 2: Implement the six figure builders

**Files:**
- Modify: `experiments/results/raw/gpu_mpi_scaling_2080ti_3502237/plot_script/plot_core_figures.py`
- Modify: `experiments/results/raw/gpu_mpi_scaling_2080ti_3502237/plot_script/test_plot_core_figures.py`

- [ ] Add a failing integration test that invokes `main()` against the real campaign data and requires exactly six SVG and six PDF files with non-zero size and recognizable SVG titles.
- [ ] Run the test and confirm failure because the render entry point or figures are missing.
- [ ] Implement builders for strong runtime, speedup/efficiency, weak slowdown, QFT sweep heatmap, whole-procedure breakdown, and communication breakdown.
- [ ] Apply one shared publication style, fixed workload/component palettes, editable SVG text, PDF TrueType fonts, explicit GPU/MPI labels, shared q28 deduplication, and separate timing/profile semantics.
- [ ] Re-run the focused integration test and require twelve vector outputs.

### Task 3: Generate and visually verify artifacts

**Files:**
- Generate: `experiments/results/raw/gpu_mpi_scaling_2080ti_3502237/plot_script/01_*.{svg,pdf}` through `06_*.{svg,pdf}`

- [ ] Run `python3 plot_script/plot_core_figures.py` from the campaign directory using the required-experiment q24-q29 summary default.
- [ ] Run the full unittest module, `python3 -m py_compile`, and an XML parse over all SVG files.
- [ ] Render the six SVG files to temporary PNG previews, inspect them for clipping, overlap, unreadable labels, misleading axes, and inconsistent legends, then adjust layout while keeping tests green.
- [ ] Confirm every PDF is vector-generated and every output is non-empty; record file names and dimensions.

### Task 4: Final verification and workspace cleanup

**Files:**
- Verify: `docs/superpowers/specs/2026-06-14-scaling-core-figures-design.md`
- Verify: `experiments/results/raw/gpu_mpi_scaling_2080ti_3502237/plot_script/`

- [ ] Re-run the complete test and generation command from a clean output state.
- [ ] Verify that the six figures match the six Core Figure instructions and that no benchmark timing is mixed with profile timing.
- [ ] Stop the visual-companion server and remove its untracked `.superpowers/` working directory.
- [ ] Report the plotting script, twelve vector outputs, source campaigns, and verification results. Keep the ignored raw artifacts local unless the user separately requests distribution.
