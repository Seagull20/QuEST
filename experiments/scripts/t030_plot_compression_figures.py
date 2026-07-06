#!/usr/bin/env python3
"""Self-contained T-030/T-045 Bitcomp figures for the in-QuEST campaign.

Adapted from the generalization-study plotting scripts:
`experiments/quest_compression_generalization/scripts/plot_speedup_conditions.py`
and `plot_stage_breakdown.py`.

Input : corrected T-030 processed directory containing runtime_comparison.tsv
        plus campaign_{off,on}_corrected/communication_breakdown_rank.tsv.
Output: t030_speedup_by_circuit.{svg,png,pdf}
        t030_stage_breakdown.{svg,png,pdf}
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
    (("gate_micro", "cphase"), "CPhase gates"),
    (("qft", ""), "QFT circuit"),
    (("random", ""), "Random circuit"),
]

STAGE_POINTS = [
    ("h_p4_q28", "Hadamard q28 / 4 ranks"),
    ("qft_p4_q28", "QFT q28 / 4 ranks"),
    ("random_p4_q28", "Random q28 / 4 ranks"),
]

STAGES = {
    "d2h_time_s": ("D2H copy", "#4878a8", ""),
    "mpi_time_s": ("MPI payload", "#e49444", ""),
    "h2d_time_s": ("H2D copy", "#6aa56e", ""),
    "compress_time_s": ("compress (GPU)", "#b05cc6", "//"),
    "size_exchange_time_s": ("size exchange", "#a3a3a3", ".."),
    "decompress_time_s": ("decompress (GPU)", "#d65f5f", "\\\\"),
}
RAW_STAGES = ["d2h_time_s", "mpi_time_s", "h2d_time_s"]
BITCOMP_STAGES = [
    "d2h_time_s",
    "mpi_time_s",
    "h2d_time_s",
    "compress_time_s",
    "size_exchange_time_s",
    "decompress_time_s",
]

WIN_COLOR = "#4878a8"
FAIL_COLOR = "#c44e52"


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


def read_runtime_conditions(runtime_tsv: Path) -> dict[tuple[str, str], list[tuple[str, float]]]:
    per_panel: dict[tuple[str, str], list[tuple[int, int, str, float]]] = defaultdict(list)
    seen_points: set[str] = set()
    for row in read_tsv(runtime_tsv):
        point_id = row["source_point_id"]
        if point_id in seen_points:
            continue
        seen_points.add(point_id)
        key = (row["benchmark"], row.get("gate_kind", ""))
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


def read_stage_breakdown(processed_dir: Path) -> dict[tuple[str, str], dict[str, float]]:
    out: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for mode in ("off", "on"):
        path = processed_dir / f"campaign_{mode}_corrected" / "communication_breakdown_rank.tsv"
        for row in read_tsv(path):
            key = (row["point"], mode)
            for stage in STAGES:
                out[key][stage] += float(row.get(stage, 0.0) or 0.0)
            out[key]["exchange_wall_time_s"] += float(row.get("exchange_wall_time_s", 0.0) or 0.0)
            out[key]["send_bytes"] += float(row.get("send_bytes", 0.0) or 0.0)
    return out


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

    fig, axes = plt.subplots(2, 3, figsize=(13, 9.2), sharey=True)
    fig.suptitle(
        "In-QuEST Bitcomp runtime speedup, per circuit\n"
        "real QuEST GPU+MPI benchmarks - compression OFF vs ON - landonia01, 4x RTX 2080 Ti - all timing rows PASS",
        fontsize=12,
        y=0.995,
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

    axes.flat[-1].axis("off")
    axes.flat[-1].text(
        0.02,
        0.95,
        "Scope:\n"
        "same QuEST fork and benchmark suite;\n"
        "compression toggled at runtime.\n\n"
        "Interpretation:\n"
        "H/CNOT/QFT expose large exchange-bound gains;\n"
        "CPhase is compute/light-communication dominated;\n"
        "random improves, but less than structured circuits.\n\n"
        "data: t030_p4_off_on_3530151_3530152_t045_corrected",
        va="top",
        fontsize=9.5,
        bbox=dict(boxstyle="round", facecolor="#f7f7f7", edgecolor="#bbbbbb"),
    )

    for ax in axes[:, 0]:
        ax.set_ylabel("speedup vs compression-off\n(wall-time ratio, log scale)", fontsize=9)
    for ax in axes[1, :2]:
        ax.set_xlabel("condition: qubits / MPI ranks", fontsize=9)

    fig.text(
        0.5,
        0.90,
        f"structured multi-rank median {structured_median:.1f}x, peak {peak:.1f}x; "
        "dashed line = break-even 1.0x; hatched red = slower than raw",
        ha="center",
        va="top",
        fontsize=9,
        bbox=dict(boxstyle="round", facecolor="#f5f0e6", edgecolor="#999999"),
    )
    fig.text(
        0.995,
        0.005,
        "data: runtime_comparison.tsv; duplicate strong/weak q28-p4 rows deduped by source point",
        ha="right",
        fontsize=7,
        color="#555555",
    )
    fig.tight_layout(rect=(0, 0.015, 1, 0.865))

    for ext in ("svg", "png", "pdf"):
        fig.savefig(output_dir / f"{basename}.{ext}", dpi=200)
    plt.close(fig)


def write_stage_breakdown(processed_dir: Path, output_dir: Path, basename: str) -> None:
    plt, Patch = setup_matplotlib()
    data = read_stage_breakdown(processed_dir)

    fig, axes = plt.subplots(1, 3, figsize=(13, 5.8), sharey=True)
    fig.suptitle(
        "Where communication time goes in real QuEST: raw vs Bitcomp\n"
        "q28 / 4-rank selected profiles - summed across ranks and exchanges - corrected T-045 MPI byte accounting",
        fontsize=11.5,
        y=0.99,
    )

    for ax, (point, title) in zip(axes, STAGE_POINTS):
        for path_i, (mode, stages) in enumerate([("off", RAW_STAGES), ("on", BITCOMP_STAGES)]):
            rec = data[(point, mode)]
            bottom = 0.0
            for stage in stages:
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
                    edgecolor="white",
                    linewidth=0.3,
                )
                bottom += value
            ax.annotate(
                f"wall {rec['exchange_wall_time_s']:.1f}s\n{rec['send_bytes'] / 1e9:.2f} GB",
                (path_i, max(bottom, 0.001)),
                ha="center",
                va="bottom",
                fontsize=7.0,
                color="#333333",
            )
        ax.set_yscale("log")
        ax.set_ylim(0.001, 200)
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["OFF", "ON"], fontsize=9)
        ax.set_title(title, fontsize=10.5)
        ax.grid(axis="y", which="both", alpha=0.25)
        ax.set_axisbelow(True)

    axes[0].set_ylabel("summed profiled stage time (s, log scale)\nstacked per stage; labels show wall time and send bytes", fontsize=9)
    axes[1].set_xlabel("compression mode", fontsize=9)
    handles = [
        Patch(facecolor=color, hatch=hatch, edgecolor="white", label=label)
        for label, color, hatch in STAGES.values()
    ]
    fig.legend(handles=handles, fontsize=8, loc="lower center", ncol=6, framealpha=0.9)
    fig.text(
        0.5,
        0.868,
        "raw path: D2H + MPI payload + H2D. Bitcomp adds GPU codec stages and size exchange while cutting payload bytes.",
        ha="center",
        va="top",
        fontsize=8.5,
        bbox=dict(boxstyle="round", facecolor="#f5f0e6", edgecolor="#999999"),
    )
    fig.text(
        0.995,
        0.005,
        "data: corrected communication_breakdown_rank.tsv",
        ha="right",
        fontsize=7,
        color="#555555",
    )
    fig.tight_layout(rect=(0, 0.11, 1, 0.80))

    for ext in ("svg", "png", "pdf"):
        fig.savefig(output_dir / f"{basename}.{ext}", dpi=200)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--speedup-basename", default="t030_speedup_by_circuit")
    parser.add_argument("--stage-basename", default="t030_stage_breakdown")
    args = parser.parse_args()

    output_dir = args.output_dir or args.processed_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    write_speedup_by_circuit(args.processed_dir, output_dir, args.speedup_basename)
    write_stage_breakdown(args.processed_dir, output_dir, args.stage_basename)
    print(f"wrote {output_dir}/{args.speedup_basename}.{{svg,png,pdf}}")
    print(f"wrote {output_dir}/{args.stage_basename}.{{svg,png,pdf}}")


if __name__ == "__main__":
    main()
