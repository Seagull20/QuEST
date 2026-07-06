#!/usr/bin/env python3
"""Self-contained T-030/T-045 Bitcomp figures for the in-QuEST campaign.

Adapted from the generalization-study plotting scripts:
`experiments/quest_compression_generalization/scripts/plot_speedup_conditions.py`
and `plot_stage_breakdown.py`.

Input : corrected T-030 processed directory containing runtime_comparison.tsv
        plus campaign_{off,on}_corrected profile breakdown TSVs.
Output: t030_speedup_by_circuit.{svg,png,pdf}
        t030_stage_breakdown.{svg,png,pdf}
        t030_stage_breakdown_relative.{svg,png,pdf}
        t030_residual_validation.tsv
"""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path


PANELS = [
    (("gate_micro", "h"), "Hadamard gates"),
    (("gate_micro", "cnot"), "CNOT gates"),
    (("qft", ""), "QFT circuit"),
    (("random", ""), "Random circuit"),
]
PANEL_KEYS = {key for key, _ in PANELS}

SPEEDUP_FORMULA_TEXT = (
    r"$\mathrm{speedup}=\frac{T_{\mathrm{compression\ off}}}"
    r"{T_{\mathrm{compression\ on}}}$"
)

STAGE_POINTS = [
    ("h_p4_q28", "Hadamard q28 / 4 ranks"),
    ("qft_p4_q28", "QFT q28 / 4 ranks"),
    ("random_p4_q28", "Random q28 / 4 ranks"),
]

# stage -> (label, colour, hatch). Every stage has a hatch so the figures remain
# readable in grayscale and for color-blind readers.
STAGES = {
    "computation_time_s": ("compute", "#4c72b0", "//"),
    "pack_time_s": ("pack", "#9a9a48", "--"),
    "d2h_time_s": ("D2H copy", "#4878a8", "\\\\"),
    "mpi_time_s": ("MPI payload", "#e49444", "xx"),
    "h2d_time_s": ("H2D copy", "#6aa56e", "++"),
    "compress_time_s": ("compress (GPU)", "#b05cc6", "oo"),
    "size_exchange_time_s": ("size exchange", "#a3a3a3", ".."),
    "decompress_time_s": ("decompress (GPU)", "#d65f5f", "**"),
    "lifecycle_time_s": ("lifecycle", "#8c6d31", "OO"),
    "execution_overhead_time_s": ("execution overhead", "#d4a65a", "||"),
    "unclassified_residual_time_s": ("unclassified residual", "#777777", "..."),
}
COMM_STAGE_FIELDS = [
    "pack_time_s",
    "d2h_time_s",
    "mpi_time_s",
    "h2d_time_s",
    "compress_time_s",
    "size_exchange_time_s",
    "decompress_time_s",
]
RAW_STAGES = ["d2h_time_s", "mpi_time_s", "h2d_time_s"]
BITCOMP_STAGES = [
    "pack_time_s",
    "d2h_time_s",
    "mpi_time_s",
    "h2d_time_s",
    "compress_time_s",
    "size_exchange_time_s",
    "decompress_time_s",
]
END_TO_END_STAGES = [
    "computation_time_s",
    "pack_time_s",
    "d2h_time_s",
    "mpi_time_s",
    "h2d_time_s",
    "compress_time_s",
    "size_exchange_time_s",
    "decompress_time_s",
    "lifecycle_time_s",
    "execution_overhead_time_s",
    "unclassified_residual_time_s",
]

WIN_COLOR = "#4878a8"
FAIL_COLOR = "#c44e52"
HATCH_EDGE_COLOR = "#333333"
FLOAT_TOLERANCE_S = 1e-9


def setup_matplotlib():
    try:
        import matplotlib
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "matplotlib is required for T-030 figure generation. "
            "Use a Python environment with matplotlib, e.g. /opt/miniconda3/bin/python3."
        ) from exc

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: E402
    from matplotlib.patches import Patch  # noqa: E402

    return plt, Patch


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def fnum(value: str | float | int | None) -> float:
    if value is None or value == "":
        return 0.0
    return float(value)


def clamp_residual(value: float, context: str) -> float:
    if -FLOAT_TOLERANCE_S < value < 0:
        return 0.0
    if value < 0:
        raise ValueError(f"negative residual for {context}: {value}")
    return value


def read_runtime_conditions(runtime_tsv: Path) -> dict[tuple[str, str], list[tuple[str, float]]]:
    per_panel: dict[tuple[str, str], list[tuple[int, int, str, float]]] = defaultdict(list)
    seen_points: set[str] = set()
    for row in read_tsv(runtime_tsv):
        point_id = row["source_point_id"]
        if point_id in seen_points:
            continue
        seen_points.add(point_id)
        key = (row["benchmark"], row.get("gate_kind", ""))
        if key not in PANEL_KEYS:
            continue
        per_panel[key].append(
            (
                int(row["mpi_ranks"]),
                int(row["num_qubits"]),
                point_id,
                float(row["runtime_speedup_on_vs_off"]),
            )
        )

    out: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for key, rows in per_panel.items():
        rows.sort()
        out[key] = [(f"q{qubits} p{ranks}", speedup) for ranks, qubits, _, speedup in rows]
    return out


def read_timing_medians(runtime_tsv: Path) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for row in read_tsv(runtime_tsv):
        point = row["source_point_id"]
        out.setdefault((point, "off"), fnum(row.get("off_median_time_s")))
        out.setdefault((point, "on"), fnum(row.get("on_median_time_s")))
    return out


def read_stage_breakdown(processed_dir: Path) -> dict[tuple[str, str], dict[str, float]]:
    out: dict[tuple[str, str], dict[str, float]] = {}
    for mode in ("off", "on"):
        campaign = processed_dir / f"campaign_{mode}_corrected"
        critical_ranks = {
            row["point"]: int(row["critical_rank"])
            for row in read_tsv(campaign / "procedure_breakdown.tsv")
        }
        procedure_by_rank = {
            (row["point"], int(row["rank"])): row
            for row in read_tsv(campaign / "procedure_breakdown_rank.tsv")
        }
        comm_by_rank: dict[tuple[str, int], dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for row in read_tsv(campaign / "communication_breakdown_rank.tsv"):
            key = (row["point"], int(row["rank"]))
            for stage in COMM_STAGE_FIELDS:
                comm_by_rank[key][stage] += fnum(row.get(stage))
            comm_by_rank[key]["exchange_wall_time_s"] += fnum(row.get("exchange_wall_time_s"))
            comm_by_rank[key]["send_bytes"] += fnum(row.get("send_bytes"))

        for point, critical_rank in critical_ranks.items():
            proc = procedure_by_rank.get((point, critical_rank))
            if proc is None:
                continue
            comm = comm_by_rank[(point, critical_rank)]
            rec: dict[str, float] = {
                "critical_rank": float(critical_rank),
                "procedure_wall_time_s": fnum(proc.get("procedure_wall_time_s")),
                "execution_wall_time_s": fnum(proc.get("execution_wall_time_s")),
                "computation_time_s": fnum(proc.get("computation_time_s")),
                "communication_time_s": fnum(proc.get("communication_time_s")),
                "lifecycle_time_s": fnum(proc.get("lifecycle_time_s")),
                "execution_overhead_time_s": fnum(proc.get("execution_overhead_time_s")),
                "send_bytes": comm["send_bytes"],
                "exchange_wall_time_s": comm["exchange_wall_time_s"],
            }
            for stage in COMM_STAGE_FIELDS:
                rec[stage] = comm[stage]
            stage_sum = rec["computation_time_s"] + sum(rec[stage] for stage in COMM_STAGE_FIELDS)
            rec["old_residual_time_s"] = clamp_residual(
                rec["procedure_wall_time_s"] - stage_sum,
                f"{point} {mode} old collapsed residual",
            )
            residual_stage_sum = stage_sum + rec["lifecycle_time_s"] + rec["execution_overhead_time_s"]
            rec["unclassified_residual_time_s"] = clamp_residual(
                rec["procedure_wall_time_s"] - residual_stage_sum,
                f"{point} {mode} unclassified residual",
            )
            out[(point, mode)] = rec
    return out


def stage_relative_percentages(rec: dict[str, float]) -> dict[str, float]:
    wall = rec.get("procedure_wall_time_s", 0.0)
    if wall <= 0:
        return {stage: 0.0 for stage in END_TO_END_STAGES}
    return {stage: 100.0 * rec.get(stage, 0.0) / wall for stage in END_TO_END_STAGES}


def read_lifecycle_contributors(processed_dir: Path) -> dict[tuple[str, str, int], list[tuple[str, float]]]:
    out: dict[tuple[str, str, int], list[tuple[str, float]]] = defaultdict(list)
    for mode in ("off", "on"):
        path = processed_dir / f"campaign_{mode}_corrected" / "lifecycle_breakdown_rank.tsv"
        if not path.exists():
            continue
        for row in read_tsv(path):
            out[(row["point"], mode, int(row["rank"]))].append((row["stage"], fnum(row.get("wall_time_s"))))
    return out


def read_top_cuda_runtime_apis(processed_dir: Path) -> dict[tuple[str, str, int], tuple[str, float]]:
    out: dict[tuple[str, str, int], tuple[str, float]] = {}
    for mode in ("off", "on"):
        path = processed_dir / f"campaign_{mode}_corrected" / "cuda_runtime_summary_rank.tsv"
        if not path.exists():
            continue
        grouped: dict[tuple[str, str, int], list[tuple[str, float]]] = defaultdict(list)
        for row in read_tsv(path):
            grouped[(row["point"], mode, int(row["rank"]))].append(
                (row["api"], fnum(row.get("total_time_s")))
            )
        for key, rows in grouped.items():
            out[key] = max(rows, key=lambda item: item[1]) if rows else ("", 0.0)
    return out


def selected_stage_keys(data: dict[tuple[str, str], dict[str, float]]) -> list[tuple[str, str]]:
    ordered: list[tuple[str, str]] = []
    for point, _title in STAGE_POINTS:
        for mode in ("off", "on"):
            key = (point, mode)
            if key in data:
                ordered.append(key)
    return ordered


def write_residual_validation(processed_dir: Path, output_path: Path) -> None:
    data = read_stage_breakdown(processed_dir)
    lifecycle = read_lifecycle_contributors(processed_dir)
    top_cuda = read_top_cuda_runtime_apis(processed_dir)
    fields = [
        "point",
        "mode",
        "critical_rank",
        "procedure_wall_time_s",
        "old_residual_time_s",
        "lifecycle_time_s",
        "execution_overhead_time_s",
        "unclassified_residual_time_s",
        "top_lifecycle_stage",
        "top_lifecycle_time_s",
        "lifecycle_contributors",
        "top_cuda_api",
        "top_cuda_api_time_s",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for point, mode in selected_stage_keys(data):
            rec = data[(point, mode)]
            rank = int(rec["critical_rank"])
            contributors = sorted(
                lifecycle.get((point, mode, rank), []),
                key=lambda item: item[1],
                reverse=True,
            )
            top_lifecycle = contributors[0] if contributors else ("", 0.0)
            top_api = top_cuda.get((point, mode, rank), ("", 0.0))
            writer.writerow(
                {
                    "point": point,
                    "mode": mode,
                    "critical_rank": rank,
                    "procedure_wall_time_s": f"{rec['procedure_wall_time_s']:.9f}",
                    "old_residual_time_s": f"{rec['old_residual_time_s']:.9f}",
                    "lifecycle_time_s": f"{rec['lifecycle_time_s']:.9f}",
                    "execution_overhead_time_s": f"{rec['execution_overhead_time_s']:.9f}",
                    "unclassified_residual_time_s": f"{rec['unclassified_residual_time_s']:.9f}",
                    "top_lifecycle_stage": top_lifecycle[0],
                    "top_lifecycle_time_s": f"{top_lifecycle[1]:.9f}",
                    "lifecycle_contributors": ";".join(
                        f"{stage}={seconds:.9f}" for stage, seconds in contributors
                    ),
                    "top_cuda_api": top_api[0],
                    "top_cuda_api_time_s": f"{top_api[1]:.9f}",
                }
            )


def write_speedup_by_circuit(processed_dir: Path, output_dir: Path, basename: str) -> None:
    plt, _ = setup_matplotlib()
    data = read_runtime_conditions(processed_dir / "runtime_comparison.tsv")
    all_values = [value for key, _ in PANELS for _, value in data.get(key, [])]
    structured_values = [
        value
        for key in [("gate_micro", "h"), ("gate_micro", "cnot"), ("qft", "")]
        for label, value in data.get(key, [])
        if not label.endswith("p1")
    ]
    structured_median = statistics.median(structured_values)
    peak = max(all_values)

    fig, axes = plt.subplots(2, 2, figsize=(12, 8.8), sharey=True)
    fig.suptitle(
        "In-QuEST Bitcomp runtime speedup, per circuit\n"
        "real QuEST GPU+MPI benchmarks - compression OFF vs ON - landonia01, 4x RTX 2080 Ti - all timing rows PASS",
        fontsize=12,
        y=0.975,
    )

    for ax, (key, title) in zip(axes.flat, PANELS):
        rows = data.get(key, [])
        labels = [label for label, _ in rows]
        values = [value for _, value in rows]
        colors = [WIN_COLOR if value >= 1.0 else FAIL_COLOR for value in values]
        hatches = ["" if value >= 1.0 else "//" for value in values]
        bars = ax.bar(range(len(values)), values, color=colors)
        for bar, hatch in zip(bars, hatches):
            bar.set_hatch(hatch)
        for index, value in enumerate(values):
            ax.annotate(
                f"{value:.2f}" if value < 10 else f"{value:.1f}",
                (index, value),
                ha="center",
                va="bottom",
                fontsize=7.5,
            )
        panel_median = statistics.median(values) if values else 0.0
        ax.axhline(1.0, color="#333333", linewidth=1.0, linestyle="--")
        ax.set_title(f"{title} - median {panel_median:.2f}x", fontsize=10)
        ax.set_yscale("log")
        ax.set_ylim(0.5, 60)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=8)
        ax.grid(axis="y", which="both", alpha=0.25)
        ax.set_axisbelow(True)

    for ax in axes[:, 0]:
        ax.set_ylabel("speedup vs compression-off\n(wall-time ratio, log scale)", fontsize=9)
    for ax in axes[1, :]:
        ax.set_xlabel("condition: qubits / MPI ranks", fontsize=9)

    fig.text(
        0.5,
        0.84,
        f"structured multi-rank median {structured_median:.1f}x, peak {peak:.1f}x; "
        "dashed line = break-even 1.0x; hatched red = slower than compression off",
        ha="center",
        va="center",
        fontsize=9,
    )
    fig.text(
        0.5,
        0.81,
        SPEEDUP_FORMULA_TEXT,
        ha="center",
        va="center",
        fontsize=10,
    )
    fig.text(
        0.995,
        0.005,
        "data: runtime_comparison.tsv; duplicate strong/weak q28-p4 rows deduped by source point",
        ha="right",
        fontsize=7,
        color="#555555",
    )
    fig.subplots_adjust(left=0.075, right=0.985, bottom=0.12, top=0.72, hspace=0.55, wspace=0.08)

    for ext in ("svg", "png", "pdf"):
        fig.savefig(output_dir / f"{basename}.{ext}", dpi=200)
    plt.close(fig)


def write_stage_breakdown(processed_dir: Path, output_dir: Path, basename: str) -> None:
    plt, Patch = setup_matplotlib()
    data = read_stage_breakdown(processed_dir)
    timing_medians = read_timing_medians(processed_dir / "runtime_comparison.tsv")

    fig, axes = plt.subplots(1, 3, figsize=(13.5, 5.8), sharey=True)
    fig.suptitle(
        "End-to-end profiled runtime decomposition in real QuEST: compression OFF vs ON\n"
        "q28 / 4-rank selected profiles - critical rank only - landonia01, 4x RTX 2080 Ti - corrected T-045 MPI byte accounting",
        fontsize=11.5,
        y=0.98,
    )

    for ax, (point, title) in zip(axes, STAGE_POINTS):
        for path_i, mode in enumerate(["off", "on"]):
            rec = data[(point, mode)]
            bottom = 0.0
            for stage in END_TO_END_STAGES:
                label, color, hatch = STAGES[stage]
                value = rec[stage]
                if value <= 0:
                    continue
                ax.bar(
                    path_i,
                    value,
                    0.44,
                    bottom=bottom,
                    color=color,
                    hatch=hatch,
                    edgecolor=HATCH_EDGE_COLOR,
                    linewidth=0.25,
                )
                bottom += value
            timing = timing_medians.get((point, mode), 0.0)
            ax.annotate(
                f"profile {rec['procedure_wall_time_s']:.1f}s\n"
                f"T {timing:.1f}s\n"
                f"{rec['send_bytes'] / 1e9:.2f} GB",
                (path_i, max(bottom, 0.001)),
                ha="center",
                va="bottom",
                fontsize=7.0,
                color="#333333",
            )
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["OFF", "ON"], fontsize=9)
        ax.set_title(title, fontsize=10.5)
        ax.grid(axis="y", alpha=0.25)
        ax.set_axisbelow(True)

    ymax = max(data[(point, mode)]["procedure_wall_time_s"] for point, _ in STAGE_POINTS for mode in ("off", "on"))
    axes[0].set_ylim(0, ymax * 1.25)
    axes[0].set_ylabel(
        "critical-rank profiled wall time (s)\n"
        "stacked: compute + communication substages + lifecycle/overhead/residual",
        fontsize=9,
    )
    axes[1].set_xlabel("compression mode", fontsize=9)
    handles = [
        Patch(facecolor=color, hatch=hatch, edgecolor=HATCH_EDGE_COLOR, label=label)
        for label, color, hatch in STAGES.values()
    ]
    fig.legend(
        handles=handles,
        fontsize=8,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.045),
        ncol=4,
        framealpha=0.9,
    )
    fig.text(
        0.5,
        0.868,
        "Each bar is one profiled critical-rank application run. "
        "Unclassified residual = profile wall - compute - profiled communication substages "
        "- lifecycle - execution overhead.",
        ha="center",
        va="top",
        fontsize=8.5,
        bbox=dict(boxstyle="round", facecolor="#f5f0e6", edgecolor="#999999"),
    )
    fig.text(
        0.995,
        0.005,
        "data: corrected procedure_breakdown_rank.tsv + communication_breakdown_rank.tsv; "
        "T labels are timing medians from runtime_comparison.tsv",
        ha="right",
        fontsize=7,
        color="#555555",
    )
    fig.subplots_adjust(left=0.07, right=0.99, bottom=0.22, top=0.70, wspace=0.05)

    for ext in ("svg", "png", "pdf"):
        fig.savefig(output_dir / f"{basename}.{ext}", dpi=200)
    plt.close(fig)


def write_stage_breakdown_relative(
    processed_dir: Path,
    output_dir: Path,
    basename: str,
    data: dict[tuple[str, str], dict[str, float]] | None = None,
    timing_medians: dict[tuple[str, str], float] | None = None,
) -> None:
    plt, Patch = setup_matplotlib()
    data = data or read_stage_breakdown(processed_dir)
    timing_medians = timing_medians or read_timing_medians(processed_dir / "runtime_comparison.tsv")

    fig, axes = plt.subplots(1, len(STAGE_POINTS), figsize=(13.5, 5.9), sharey=True)
    if len(STAGE_POINTS) == 1:
        axes = [axes]
    fig.suptitle(
        "End-to-end profiled runtime partition in real QuEST: compression OFF vs ON\n"
        "q28 / 4-rank selected profiles - critical rank only - each bar normalized to 100%",
        fontsize=11.5,
        y=0.98,
    )

    for ax, (point, title) in zip(axes, STAGE_POINTS):
        for path_i, mode in enumerate(["off", "on"]):
            rec = data[(point, mode)]
            shares = stage_relative_percentages(rec)
            bottom = 0.0
            for stage in END_TO_END_STAGES:
                label, color, hatch = STAGES[stage]
                value = shares[stage]
                if value <= 0:
                    continue
                ax.bar(
                    path_i,
                    value,
                    0.44,
                    bottom=bottom,
                    color=color,
                    hatch=hatch,
                    edgecolor=HATCH_EDGE_COLOR,
                    linewidth=0.25,
                )
                bottom += value
            timing = timing_medians.get((point, mode), 0.0)
            ax.annotate(
                f"profile {rec['procedure_wall_time_s']:.1f}s\n"
                f"T {timing:.1f}s\n"
                f"{rec['send_bytes'] / 1e9:.2f} GB",
                (path_i, 101.0),
                ha="center",
                va="bottom",
                fontsize=7.0,
                color="#333333",
            )
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["OFF", "ON"], fontsize=9)
        ax.set_title(title, fontsize=10.5)
        ax.grid(axis="y", alpha=0.25)
        ax.set_axisbelow(True)
        ax.set_ylim(0, 116)

    axes[0].set_ylabel(
        "share of critical-rank profiled wall time (%)\n"
        "100% stacked end-to-end partition",
        fontsize=9,
    )
    axes[len(axes) // 2].set_xlabel("compression mode", fontsize=9)
    handles = [
        Patch(facecolor=color, hatch=hatch, edgecolor=HATCH_EDGE_COLOR, label=label)
        for label, color, hatch in STAGES.values()
    ]
    fig.legend(
        handles=handles,
        fontsize=8,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.045),
        ncol=4,
        framealpha=0.9,
    )
    fig.text(
        0.5,
        0.868,
        "Fractions use the same critical-rank profile wall time as the absolute figure; "
        "runtime medians are labels, not rescaling factors.",
        ha="center",
        va="top",
        fontsize=8.5,
        bbox=dict(boxstyle="round", facecolor="#f5f0e6", edgecolor="#999999"),
    )
    fig.text(
        0.995,
        0.005,
        "data: corrected procedure_breakdown_rank.tsv + communication_breakdown_rank.tsv; "
        "relative shares sum to 100% per bar",
        ha="right",
        fontsize=7,
        color="#555555",
    )
    fig.subplots_adjust(left=0.07, right=0.99, bottom=0.22, top=0.70, wspace=0.05)

    for ext in ("svg", "png", "pdf"):
        fig.savefig(output_dir / f"{basename}.{ext}", dpi=200)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--speedup-basename", default="t030_speedup_by_circuit")
    parser.add_argument("--stage-basename", default="t030_stage_breakdown")
    parser.add_argument("--relative-stage-basename", default="t030_stage_breakdown_relative")
    parser.add_argument("--residual-validation-basename", default="t030_residual_validation")
    args = parser.parse_args()

    output_dir = args.output_dir or args.processed_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    write_speedup_by_circuit(args.processed_dir, output_dir, args.speedup_basename)
    write_stage_breakdown(args.processed_dir, output_dir, args.stage_basename)
    write_stage_breakdown_relative(args.processed_dir, output_dir, args.relative_stage_basename)
    write_residual_validation(args.processed_dir, output_dir / f"{args.residual_validation_basename}.tsv")
    print(f"wrote {output_dir}/{args.speedup_basename}.{{svg,png,pdf}}")
    print(f"wrote {output_dir}/{args.stage_basename}.{{svg,png,pdf}}")
    print(f"wrote {output_dir}/{args.relative_stage_basename}.{{svg,png,pdf}}")
    print(f"wrote {output_dir}/{args.residual_validation_basename}.tsv")


if __name__ == "__main__":
    main()
