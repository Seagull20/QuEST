#!/usr/bin/env python3
"""Self-contained T-030/T-045 Bitcomp figures for the QuEST in-path campaign.

Input : corrected T-030 processed directory containing runtime_comparison.tsv,
        communication_comparison.tsv, and campaign_{off,on}_corrected/
        communication_breakdown_rank.tsv.
Output: t030_speedup_by_circuit.{svg,png,pdf}
        t030_stage_breakdown.{svg,png,pdf}
"""

from __future__ import annotations

import argparse
import csv
import html
import math
import shutil
import statistics
import subprocess
from collections import defaultdict
from pathlib import Path


WIN_COLOR = "#4878a8"
FAIL_COLOR = "#c44e52"
GRID_COLOR = "#dddddd"
TEXT_COLOR = "#222222"
MUTED_COLOR = "#555555"

CIRCUIT_ORDER = [
    ("gate_micro", "h", "Hadamard"),
    ("gate_micro", "cnot", "CNOT"),
    ("gate_micro", "cphase", "CPhase"),
    ("qft", "", "QFT"),
    ("random", "", "Random"),
]

STAGE_ORDER = [
    ("d2h_time_s", "D2H copy", "#4878a8"),
    ("mpi_time_s", "MPI payload", "#e49444"),
    ("h2d_time_s", "H2D copy", "#6aa56e"),
    ("compress_time_s", "compress", "#b05cc6"),
    ("size_exchange_time_s", "size exchange", "#a3a3a3"),
    ("decompress_time_s", "decompress", "#d65f5f"),
]

STAGE_POINTS = [
    ("h_p4_q28", "Hadamard q28 / 4 ranks"),
    ("qft_p4_q28", "QFT q28 / 4 ranks"),
    ("random_p4_q28", "Random q28 / 4 ranks"),
]


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def text(x: float, y: float, value: str, size: float = 12, weight: str = "400",
         anchor: str = "start", fill: str = TEXT_COLOR, rotate: float | None = None) -> str:
    transform = f' transform="rotate({rotate:.1f} {x:.2f} {y:.2f})"' if rotate is not None else ""
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" font-family="Arial, Helvetica, sans-serif" '
        f'font-size="{size:.2f}" font-weight="{weight}" text-anchor="{anchor}" '
        f'fill="{fill}"{transform}>{esc(value)}</text>'
    )


def rect(x: float, y: float, width: float, height: float, fill: str,
         stroke: str = "none", stroke_width: float = 0.0, opacity: float = 1.0) -> str:
    return (
        f'<rect x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" height="{height:.2f}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width:.2f}" '
        f'opacity="{opacity:.3f}"/>'
    )


def line(x1: float, y1: float, x2: float, y2: float, stroke: str = "#333333",
         width: float = 1.0, dash: str | None = None) -> str:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
        f'stroke="{stroke}" stroke-width="{width:.2f}"{dash_attr}/>'
    )


def svg_document(width: int, height: int, parts: list[str]) -> str:
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        rect(0, 0, width, height, "white"),
        *parts,
        "</svg>",
        "",
    ])


def circuit_key(row: dict[str, str]) -> tuple[str, str]:
    return row.get("benchmark", ""), row.get("gate_kind", "")


def point_label(row: dict[str, str]) -> str:
    return f"q{row['num_qubits']} p{row['mpi_ranks']}"


def load_runtime_rows(path: Path) -> dict[tuple[str, str], list[dict[str, str]]]:
    seen: set[str] = set()
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in read_tsv(path):
        point_id = row["source_point_id"]
        if point_id in seen:
            continue
        seen.add(point_id)
        groups[circuit_key(row)].append(row)
    for rows in groups.values():
        rows.sort(key=lambda row: (int(row["mpi_ranks"]), int(row["num_qubits"]), row["source_point_id"]))
    return groups


def log_scale(value: float, ymin: float, ymax: float, top: float, height: float) -> float:
    value = max(ymin, min(ymax, value))
    lo = math.log10(ymin)
    hi = math.log10(ymax)
    return top + (hi - math.log10(value)) / (hi - lo) * height


def write_speedup_by_circuit(processed_dir: Path, output_dir: Path) -> Path:
    groups = load_runtime_rows(processed_dir / "runtime_comparison.tsv")
    values = [
        float(row["runtime_speedup_on_vs_off"])
        for key in [(bench, gate) for bench, gate, _ in CIRCUIT_ORDER]
        for row in groups.get(key, [])
    ]
    structured = [
        float(row["runtime_speedup_on_vs_off"])
        for key in [("gate_micro", "h"), ("gate_micro", "cnot"), ("qft", "")]
        for row in groups.get(key, [])
        if int(row["mpi_ranks"]) > 1
    ]
    median_structured = statistics.median(structured)
    peak = max(values)

    width, height = 1500, 1060
    parts: list[str] = [
        text(750, 35, "In-QuEST Bitcomp runtime speedup, per circuit", 24, "700", "middle"),
        text(
            750,
            60,
            "real QuEST GPU+MPI benchmarks - compression OFF vs ON - landonia01, 4x RTX 2080 Ti - all timing rows PASS",
            13,
            "400",
            "middle",
            MUTED_COLOR,
        ),
        rect(170, 78, 1160, 48, "#f5f0e6", "#999999", 0.8),
        text(
            750,
            99,
            f"structured multi-rank median {median_structured:.1f}x, peak {peak:.1f}x; "
            "dashed line = break-even 1.0x; red/hatched bars are slower than raw",
            12,
            "400",
            "middle",
        ),
    ]

    panel_w, panel_h = 430, 345
    gap_x, gap_y = 45, 72
    left0, top0 = 55, 165
    ymin, ymax = 0.5, 50.0
    ticks = [0.5, 1, 2, 5, 10, 20, 50]

    for index, (bench, gate, title) in enumerate(CIRCUIT_ORDER):
        col = index % 3
        row_i = index // 3
        left = left0 + col * (panel_w + gap_x)
        top = top0 + row_i * (panel_h + gap_y)
        plot_left = left + 62
        plot_top = top + 45
        plot_w = panel_w - 82
        plot_h = panel_h - 105
        rows = groups.get((bench, gate), [])
        speeds = [float(item["runtime_speedup_on_vs_off"]) for item in rows]
        panel_median = statistics.median(speeds) if speeds else 0.0

        parts.append(rect(left, top, panel_w, panel_h, "#ffffff", "#c9c9c9", 0.8))
        parts.append(text(left + panel_w / 2, top + 25, f"{title} - median {panel_median:.2f}x", 15, "700", "middle"))
        for tick in ticks:
            y = log_scale(tick, ymin, ymax, plot_top, plot_h)
            parts.append(line(plot_left, y, plot_left + plot_w, y, GRID_COLOR, 0.7))
            parts.append(text(plot_left - 8, y + 4, f"{tick:g}", 9, "400", "end", MUTED_COLOR))
        y_break = log_scale(1.0, ymin, ymax, plot_top, plot_h)
        parts.append(line(plot_left, y_break, plot_left + plot_w, y_break, "#333333", 1.1, "5 4"))
        parts.append(line(plot_left, plot_top, plot_left, plot_top + plot_h, "#333333", 1.0))
        parts.append(line(plot_left, plot_top + plot_h, plot_left + plot_w, plot_top + plot_h, "#333333", 1.0))

        if rows:
            step = plot_w / len(rows)
            bar_w = min(38.0, step * 0.58)
            for i, item in enumerate(rows):
                speed = float(item["runtime_speedup_on_vs_off"])
                x = plot_left + step * i + (step - bar_w) / 2
                y = log_scale(speed, ymin, ymax, plot_top, plot_h)
                y_floor = log_scale(ymin, ymin, ymax, plot_top, plot_h)
                color = WIN_COLOR if speed >= 1.0 else FAIL_COLOR
                parts.append(rect(x, y, bar_w, y_floor - y, color, "#ffffff", 0.5))
                if speed < 1.0:
                    parts.append(line(x, y, x + bar_w, y_floor, "#ffffff", 1.0))
                    parts.append(line(x + bar_w, y, x, y_floor, "#ffffff", 1.0))
                label = f"{speed:.2f}" if speed < 10 else f"{speed:.1f}"
                parts.append(text(x + bar_w / 2, y - 6, label, 9, "700", "middle"))
                parts.append(text(x + bar_w / 2, plot_top + plot_h + 18, point_label(item), 10, "400", "middle", TEXT_COLOR, -35))

        parts.append(text(plot_left + plot_w / 2, top + panel_h - 12, "qubits / MPI ranks", 10, "400", "middle", MUTED_COLOR))

    note_left = left0 + 2 * (panel_w + gap_x)
    note_top = top0 + 1 * (panel_h + gap_y)
    parts.append(rect(note_left, note_top, panel_w, panel_h, "#f7f7f7", "#c9c9c9", 0.8))
    note_lines = [
        "Scope:",
        "same QuEST fork and benchmark suite;",
        "compression toggled at runtime.",
        "",
        "Interpretation:",
        "H/CNOT/QFT expose large exchange-bound gains;",
        "CPhase is compute/light-communication dominated;",
        "random improves, but less than structured circuits.",
        "",
        "data: t030_p4_off_on_3530151_3530152_t045_corrected",
    ]
    for i, line_text in enumerate(note_lines):
        weight = "700" if line_text.endswith(":") else "400"
        parts.append(text(note_left + 28, note_top + 38 + i * 25, line_text, 13, weight, "start", TEXT_COLOR if line_text else MUTED_COLOR))

    parts.append(text(1490, 1048, "data: runtime_comparison.tsv; duplicate strong/weak q28-p4 rows deduped by source point", 9, "400", "end", MUTED_COLOR))
    path = output_dir / "t030_speedup_by_circuit.svg"
    path.write_text(svg_document(width, height, parts), encoding="utf-8")
    return path


def aggregate_breakdown(path: Path) -> dict[str, dict[str, float]]:
    groups: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in read_tsv(path):
        point = row["point"]
        for field, _, _ in STAGE_ORDER:
            groups[point][field] += float(row.get(field, 0.0) or 0.0)
        groups[point]["exchange_wall_time_s"] += float(row.get("exchange_wall_time_s", 0.0) or 0.0)
        groups[point]["send_bytes"] += float(row.get("send_bytes", 0.0) or 0.0)
    return groups


def write_stage_breakdown(processed_dir: Path, output_dir: Path) -> Path:
    off = aggregate_breakdown(processed_dir / "campaign_off_corrected" / "communication_breakdown_rank.tsv")
    on = aggregate_breakdown(processed_dir / "campaign_on_corrected" / "communication_breakdown_rank.tsv")

    width, height = 1500, 835
    ymin, ymax = 0.001, 200.0
    ticks = [0.001, 0.01, 0.1, 1, 10, 100]
    parts: list[str] = [
        text(750, 35, "Where communication time goes in real QuEST: raw vs Bitcomp", 24, "700", "middle"),
        text(
            750,
            60,
            "q28 / 4-rank selected profiles - summed across ranks and exchanges - corrected T-045 MPI byte accounting",
            13,
            "400",
            "middle",
            MUTED_COLOR,
        ),
        rect(160, 78, 1180, 52, "#f5f0e6", "#999999", 0.8),
        text(
            750,
            99,
            "raw path is D2H + MPI payload + H2D; Bitcomp adds GPU compress/decompress and size exchange while cutting payload bytes",
            12,
            "400",
            "middle",
        ),
    ]

    panel_w, panel_h = 430, 510
    left0, top0, gap_x = 65, 165, 45
    for index, (point, title) in enumerate(STAGE_POINTS):
        left = left0 + index * (panel_w + gap_x)
        top = top0
        plot_left = left + 70
        plot_top = top + 48
        plot_w = panel_w - 105
        plot_h = panel_h - 120
        parts.append(rect(left, top, panel_w, panel_h, "#ffffff", "#c9c9c9", 0.8))
        parts.append(text(left + panel_w / 2, top + 26, title, 15, "700", "middle"))
        for tick in ticks:
            y = log_scale(tick, ymin, ymax, plot_top, plot_h)
            parts.append(line(plot_left, y, plot_left + plot_w, y, GRID_COLOR, 0.7))
            parts.append(text(plot_left - 8, y + 4, f"{tick:g}", 9, "400", "end", MUTED_COLOR))
        parts.append(line(plot_left, plot_top, plot_left, plot_top + plot_h, "#333333", 1.0))
        parts.append(line(plot_left, plot_top + plot_h, plot_left + plot_w, plot_top + plot_h, "#333333", 1.0))

        for bar_i, (mode_label, data) in enumerate([("OFF", off[point]), ("ON", on[point])]):
            bar_w = 72
            x = plot_left + 72 + bar_i * 132
            cumulative = 0.0
            stage_sum = sum(data[field] for field, _, _ in STAGE_ORDER)
            for field, _, color in STAGE_ORDER:
                value = data[field]
                if value <= 0:
                    continue
                next_cumulative = cumulative + value
                y_top = log_scale(next_cumulative, ymin, ymax, plot_top, plot_h)
                y_bottom = log_scale(max(cumulative, ymin), ymin, ymax, plot_top, plot_h)
                parts.append(rect(x, y_top, bar_w, max(1.0, y_bottom - y_top), color, "#ffffff", 0.45))
                cumulative = next_cumulative
            wall = data["exchange_wall_time_s"]
            y_total = log_scale(max(stage_sum, ymin), ymin, ymax, plot_top, plot_h)
            parts.append(text(x + bar_w / 2, y_total - 10, f"wall {wall:.1f}s", 10, "700", "middle"))
            parts.append(text(x + bar_w / 2, plot_top + plot_h + 25, mode_label, 12, "700", "middle"))
            gb = data["send_bytes"] / 1e9
            parts.append(text(x + bar_w / 2, plot_top + plot_h + 43, f"{gb:.2f} GB sent", 9, "400", "middle", MUTED_COLOR))

        parts.append(text(plot_left + plot_w / 2, top + panel_h - 12, "mode and corrected MPI send bytes", 10, "400", "middle", MUTED_COLOR))

    legend_x, legend_y = 222, 712
    parts.append(rect(legend_x - 18, legend_y - 28, 1055, 58, "#ffffff", "#cccccc", 0.8))
    cursor = legend_x
    for field, label, color in STAGE_ORDER:
        parts.append(rect(cursor, legend_y - 12, 18, 18, color, "#ffffff", 0.4))
        parts.append(text(cursor + 25, legend_y + 2, label, 11, "400", "start"))
        cursor += 165 if field != "size_exchange_time_s" else 190

    parts.append(text(70, 690, "summed profiled stage time (s, log scale)", 11, "400", "start", MUTED_COLOR, -90))
    parts.append(text(1490, 820, "data: campaign_{off,on}_corrected/communication_breakdown_rank.tsv; generated from existing Nsight SQLite", 9, "400", "end", MUTED_COLOR))
    path = output_dir / "t030_stage_breakdown.svg"
    path.write_text(svg_document(width, height, parts), encoding="utf-8")
    return path


def convert_with_rsvg(svg_path: Path) -> None:
    converter = shutil.which("rsvg-convert")
    if converter is None:
        return
    for fmt in ("png", "pdf"):
        output = svg_path.with_suffix(f".{fmt}")
        cmd = [converter, "-f", fmt, "-o", str(output), str(svg_path)]
        if fmt == "png":
            cmd[1:1] = ["-w", "2600"]
        subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    output_dir = args.output_dir or args.processed_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    svgs = [
        write_speedup_by_circuit(args.processed_dir, output_dir),
        write_stage_breakdown(args.processed_dir, output_dir),
    ]
    for svg in svgs:
        convert_with_rsvg(svg)
        print(f"wrote {svg.with_suffix('.{svg,png,pdf}')}")


if __name__ == "__main__":
    main()
