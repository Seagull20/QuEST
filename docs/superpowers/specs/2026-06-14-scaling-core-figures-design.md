# GPU MPI Scaling Core Figures Design

## Goal

Create the six Core Figures specified in the presentation note from the existing RTX 2080 Ti scaling and required-experiment campaigns. Store a reproducible plotting script and all generated vector figures under `experiments/results/raw/gpu_mpi_scaling_2080ti_3502237/plot_script/`.

## Output Set

The script generates both SVG and PDF for each figure:

1. `01_strong_scaling_runtime`: five workload panels at q28, showing measured samples, median lines, and min-to-p95 intervals.
2. `02_strong_scaling_speedup_efficiency`: aligned speedup and parallel-efficiency panels with ideal references.
3. `03_weak_scaling_slowdown`: aligned raw and per-gate-normalized slowdown panels for the q26/q27/q28 weak-scaling matrix.
4. `04_qft_size_sweep_heatmap`: q24-q29 by 1/2/4 GPUs, coloured by median runtime and annotated with runtime and speedup.
5. `05_whole_procedure_breakdown`: aligned seconds and percentage stacked bars for unique profiled points, split into computation, communication, lifecycle, and execution overhead.
6. `06_communication_breakdown`: critical-rank D2H, MPI, H2D, and measurable pack time for each unique CPU-staged communication point.

## Data And Semantics

- Timing figures read `scaling_samples.tsv` and `scaling_summary.tsv` from the parent campaign directory.
- The QFT heatmap reads the completed q24-q29 `qft_size_sweep_summary.tsv`; the path is a CLI option with the local required-experiment campaign as its default.
- Profile figures read `scaling_profile_summary.tsv` and `communication_breakdown_rank.tsv`.
- Shared q28 strong/weak endpoints are plotted once by deduplicating `source_point_id` or `point`.
- Communication stages are summed only for the point's critical rank. Times are not summed across ranks because ranks execute concurrently.
- Benchmark timing and profiler timing remain separate and are never combined into one performance series.

## Visual System

- Use Matplotlib with the Python standard library TSV parser; do not require pandas or seaborn.
- Use a colour-blind-safe workload palette and a separate fixed component palette.
- Use linear axes, explicit `GPUs / MPI ranks` labels, stable workload names, light horizontal grids, and publication-sized typography.
- Figures use landscape layouts sized for thesis insertion. Legends are outside dense plotting areas where needed.
- SVG text remains editable; PDFs use embedded vector graphics.

## Interface And Validation

The entry point is `plot_core_figures.py` with `--campaign-dir`, `--qft-sweep-summary`, and `--output-dir` options. Defaults make it runnable directly from `plot_script/` without additional arguments.

Validation fails clearly on missing files, missing required columns, non-PASS summary rows, duplicate inconsistent points, or non-finite plotted values. A companion test script verifies parsing, deduplication, critical-rank selection, expected figure names, and non-empty SVG/PDF output. Generated SVGs are rendered to PNG for visual inspection, but PNG is verification-only and is not part of the requested deliverable.
