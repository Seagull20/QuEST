#!/usr/bin/env python3
"""Compare T-030 compression-off and compression-on scaling campaigns."""

import argparse
import csv
import html
from collections import defaultdict
from pathlib import Path


RUNTIME_FIELDS = [
    "source_point_id",
    "scale_type",
    "benchmark",
    "gate_kind",
    "num_qubits",
    "mpi_ranks",
    "off_median_time_s",
    "on_median_time_s",
    "runtime_speedup_on_vs_off",
    "runtime_delta_pct",
]

COMMUNICATION_FIELDS = [
    "point",
    "benchmark",
    "num_qubits",
    "mpi_ranks",
    "off_send_bytes",
    "on_send_bytes",
    "mpi_send_byte_reduction_pct",
    "off_mpi_time_s",
    "on_mpi_time_s",
    "on_compress_time_s",
    "on_size_exchange_time_s",
    "on_decompress_time_s",
    "off_exchange_wall_time_s",
    "on_exchange_wall_time_s",
]


def read_tsv(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(path, fieldnames, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def as_float(value, default=0.0):
    if value in (None, ""):
        return default
    return float(value)


def runtime_key(row):
    return (
        row["source_point_id"],
        row["scale_type"],
        row["benchmark"],
        row.get("gate_kind", ""),
        row["num_qubits"],
        row["mpi_ranks"],
    )


def compare_runtime(off_dir, on_dir):
    off_rows = {runtime_key(row): row for row in read_tsv(Path(off_dir) / "scaling_summary.tsv") if row.get("summary_status") == "PASS"}
    on_rows = {runtime_key(row): row for row in read_tsv(Path(on_dir) / "scaling_summary.tsv") if row.get("summary_status") == "PASS"}
    rows = []
    for key in sorted(off_rows):
        if key not in on_rows:
            continue
        off = off_rows[key]
        on = on_rows[key]
        off_time = as_float(off["median_time_s"])
        on_time = as_float(on["median_time_s"])
        speedup = off_time / on_time if on_time else 0.0
        delta_pct = 100.0 * (on_time - off_time) / off_time if off_time else 0.0
        rows.append(
            {
                "source_point_id": off["source_point_id"],
                "scale_type": off["scale_type"],
                "benchmark": off["benchmark"],
                "gate_kind": off.get("gate_kind", ""),
                "num_qubits": off["num_qubits"],
                "mpi_ranks": off["mpi_ranks"],
                "off_median_time_s": f"{off_time:.6f}",
                "on_median_time_s": f"{on_time:.6f}",
                "runtime_speedup_on_vs_off": f"{speedup:.6f}",
                "runtime_delta_pct": f"{delta_pct:.6f}",
            }
        )
    return rows


def communication_key(row):
    return (row["point"], row["benchmark"], row["num_qubits"], row["mpi_ranks"])


def aggregate_communication(campaign_dir):
    groups = defaultdict(lambda: defaultdict(float))
    for row in read_tsv(Path(campaign_dir) / "communication_breakdown_rank.tsv"):
        values = groups[communication_key(row)]
        values["send_bytes"] += as_float(row.get("send_bytes"))
        values["mpi_time_s"] += as_float(row.get("mpi_time_s"))
        values["compress_time_s"] += as_float(row.get("compress_time_s"))
        values["size_exchange_time_s"] += as_float(row.get("size_exchange_time_s"))
        values["decompress_time_s"] += as_float(row.get("decompress_time_s"))
        values["exchange_wall_time_s"] += as_float(row.get("exchange_wall_time_s"))
    return groups


def compare_communication(off_dir, on_dir):
    off = aggregate_communication(off_dir)
    on = aggregate_communication(on_dir)
    rows = []
    for key in sorted(off):
        if key not in on:
            continue
        point, benchmark, num_qubits, mpi_ranks = key
        off_values = off[key]
        on_values = on[key]
        off_bytes = off_values["send_bytes"]
        on_bytes = on_values["send_bytes"]
        reduction = 100.0 * (off_bytes - on_bytes) / off_bytes if off_bytes else 0.0
        rows.append(
            {
                "point": point,
                "benchmark": benchmark,
                "num_qubits": num_qubits,
                "mpi_ranks": mpi_ranks,
                "off_send_bytes": f"{off_bytes:.0f}",
                "on_send_bytes": f"{on_bytes:.0f}",
                "mpi_send_byte_reduction_pct": f"{reduction:.6f}",
                "off_mpi_time_s": f"{off_values['mpi_time_s']:.6f}",
                "on_mpi_time_s": f"{on_values['mpi_time_s']:.6f}",
                "on_compress_time_s": f"{on_values['compress_time_s']:.6f}",
                "on_size_exchange_time_s": f"{on_values['size_exchange_time_s']:.6f}",
                "on_decompress_time_s": f"{on_values['decompress_time_s']:.6f}",
                "off_exchange_wall_time_s": f"{off_values['exchange_wall_time_s']:.6f}",
                "on_exchange_wall_time_s": f"{on_values['exchange_wall_time_s']:.6f}",
            }
        )
    return rows


def workload_label(row):
    gate = row.get("gate_kind", "")
    base = row.get("benchmark", row.get("point", "point"))
    if gate:
        base = f"{base}/{gate}"
    return f"{base} q{row['num_qubits']} p{row['mpi_ranks']}"


def write_bar_svg(path, title, rows, value_key, ylabel):
    width = 980
    height = max(320, 120 + 34 * len(rows))
    left = 250
    right = 40
    top = 70
    bar_h = 20
    gap = 14
    max_value = max([as_float(row[value_key]) for row in rows] + [1.0])
    scale = (width - left - right) / max_value
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{left}" y="34" font-family="Arial" font-size="22" font-weight="700">{html.escape(title)}</text>',
        f'<text x="{left}" y="58" font-family="Arial" font-size="13" fill="#333">{html.escape(ylabel)}</text>',
    ]
    for index, row in enumerate(rows):
        y = top + index * (bar_h + gap)
        value = as_float(row[value_key])
        label = workload_label(row)
        bar_w = max(1.0, value * scale)
        parts.append(f'<text x="12" y="{y + 15}" font-family="Arial" font-size="12">{html.escape(label)}</text>')
        parts.append(f'<rect x="{left}" y="{y}" width="{bar_w:.2f}" height="{bar_h}" fill="#1f77b4"/>')
        parts.append(f'<text x="{left + bar_w + 8:.2f}" y="{y + 15}" font-family="Arial" font-size="12">{value:.2f}</text>')
    parts.append("</svg>")
    Path(path).write_text("\n".join(parts) + "\n", encoding="utf-8")


def write_minimal_pdf(path, title, rows, value_key):
    lines = [title, ""]
    lines.extend(f"{workload_label(row)}: {as_float(row[value_key]):.3f}" for row in rows[:28])
    escaped_lines = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    text_ops = ["BT", "/F1 12 Tf", "50 780 Td"]
    first = True
    for line in escaped_lines:
        if first:
            first = False
        else:
            text_ops.append("0 -16 Td")
        text_ops.append(f"({line}) Tj")
    text_ops.append("ET")
    stream = "\n".join(text_ops).encode("utf-8")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f"{number} 0 obj\n".encode("ascii"))
        content.extend(obj)
        content.extend(b"\nendobj\n")
    xref_offset = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    Path(path).write_bytes(content)


def write_teams_update(path, runtime_rows, communication_rows, off_dir, on_dir):
    best = max(runtime_rows, key=lambda row: as_float(row["runtime_speedup_on_vs_off"])) if runtime_rows else None
    best_comm = max(communication_rows, key=lambda row: as_float(row["mpi_send_byte_reduction_pct"])) if communication_rows else None
    lines = [
        "# T-030 P4 Teams Update Draft",
        "",
        "I ran the same QuEST GPU+MPI scaling suite with nvCOMP built in and runtime exchange compression toggled off/on.",
        "The MPI byte table uses corrected single-direction MPI send bytes from Nsight P2P events.",
        f"Compression-off source: `{off_dir}`",
        f"Compression-on source: `{on_dir}`",
        "",
    ]
    random_p4 = next((row for row in communication_rows if row["point"] == "random_p4_q28"), None)
    if random_p4:
        lines.append(
            "Corrected random q28 p4 byte accounting: "
            f"{random_p4['off_send_bytes']} off bytes vs {random_p4['on_send_bytes']} on bytes, "
            f"{random_p4['mpi_send_byte_reduction_pct']}% reduction."
        )
    if best:
        lines.append(
            "Largest runtime improvement in the joined PASS set: "
            f"{best['source_point_id']} ({workload_label(best)}) at "
            f"{best['runtime_speedup_on_vs_off']}x."
        )
    if best_comm:
        lines.append(
            "Largest profiled MPI byte reduction: "
            f"{best_comm['point']} at {best_comm['mpi_send_byte_reduction_pct']}%."
        )
    lines.extend(
        [
            "",
            "Recommended figures: `t030_speedup_by_circuit.{png,pdf}` and `t030_stage_breakdown.{png,pdf}`.",
            "Please treat this as the with/without-compression whole-application follow-up to the earlier raw breakdown.",
            "",
        ]
    )
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def compare_campaigns(off_dir, on_dir, output_dir):
    off_dir = Path(off_dir)
    on_dir = Path(on_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    runtime_rows = compare_runtime(off_dir, on_dir)
    communication_rows = compare_communication(off_dir, on_dir)

    paths = {
        "runtime": output_dir / "runtime_comparison.tsv",
        "communication": output_dir / "communication_comparison.tsv",
        "runtime_svg": output_dir / "runtime_speedup.svg",
        "runtime_pdf": output_dir / "runtime_speedup.pdf",
        "communication_svg": output_dir / "communication_byte_reduction.svg",
        "communication_pdf": output_dir / "communication_byte_reduction.pdf",
        "teams": output_dir / "t030_teams_update.md",
    }
    write_tsv(paths["runtime"], RUNTIME_FIELDS, runtime_rows)
    write_tsv(paths["communication"], COMMUNICATION_FIELDS, communication_rows)
    write_bar_svg(paths["runtime_svg"], "T-030 Bitcomp Runtime Speedup", runtime_rows, "runtime_speedup_on_vs_off", "Higher is faster; 1.0 means no runtime change.")
    write_minimal_pdf(paths["runtime_pdf"], "T-030 Bitcomp Runtime Speedup", runtime_rows, "runtime_speedup_on_vs_off")
    write_bar_svg(paths["communication_svg"], "T-030 MPI Byte Reduction", communication_rows, "mpi_send_byte_reduction_pct", "Percent reduction in profiled MPI send bytes.")
    write_minimal_pdf(paths["communication_pdf"], "T-030 MPI Byte Reduction", communication_rows, "mpi_send_byte_reduction_pct")
    write_teams_update(paths["teams"], runtime_rows, communication_rows, off_dir, on_dir)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--off", required=True, type=Path, help="Compression-off campaign directory")
    parser.add_argument("--on", required=True, type=Path, help="Compression-on campaign directory")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    paths = compare_campaigns(args.off, args.on, args.output_dir)
    for name, path in paths.items():
        print(f"{name}={path}")


if __name__ == "__main__":
    main()
