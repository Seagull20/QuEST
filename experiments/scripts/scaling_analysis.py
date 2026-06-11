#!/usr/bin/env python3
"""Aggregate intra-node GPU scaling samples and profile metadata."""

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path


SAMPLE_FIELDS = [
    "source_point_id",
    "scale_type",
    "benchmark",
    "gate_kind",
    "num_qubits",
    "local_qubits",
    "local_amplitudes",
    "logical_gate_count",
    "mpi_ranks",
    "gpus",
    "slurm_nodes",
    "rep",
    "warmup",
    "status",
    "total_time_s",
    "time_per_gate_s",
    "source_file",
]

SUMMARY_FIELDS = [
    "source_point_id",
    "scale_type",
    "benchmark",
    "gate_kind",
    "num_qubits",
    "local_qubits",
    "local_amplitudes",
    "logical_gate_count",
    "mpi_ranks",
    "gpus",
    "slurm_nodes",
    "summary_status",
    "sample_count",
    "median_time_s",
    "mean_time_s",
    "std_time_s",
    "min_time_s",
    "max_time_s",
    "p95_time_s",
    "cv",
    "median_time_per_gate_s",
    "speedup",
    "parallel_efficiency",
    "parallel_overhead_s",
    "weak_efficiency",
    "weak_slowdown",
    "normalized_per_gate_efficiency",
    "normalized_per_gate_slowdown",
]


def qft_logical_gate_count(num_qubits):
    return num_qubits + num_qubits * (num_qubits - 1) // 2 + num_qubits // 2


def percentile(values, quantile):
    if not values:
        return math.nan
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def read_tsv(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(path, fieldnames, rows):
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _integer_log2(value):
    if value <= 0 or value & (value - 1):
        raise ValueError(f"MPI ranks must be a positive power of two, got {value}")
    return value.bit_length() - 1


def _logical_gate_count(benchmark, row, num_qubits):
    if row.get("gate_count") not in (None, ""):
        return int(row["gate_count"])
    if benchmark == "qft":
        return qft_logical_gate_count(num_qubits)
    return 0


def load_samples(campaign_dir, manifest_rows):
    campaign_dir = Path(campaign_dir)
    samples = []
    for point in manifest_rows:
        source_path = campaign_dir / point["source_file"]
        benchmark = point["benchmark"]
        num_qubits = int(point["num_qubits"])
        mpi_ranks = int(point["mpi_ranks"])
        local_qubits = num_qubits - _integer_log2(mpi_ranks)
        scale_types = [item.strip() for item in point["scale_membership"].split(",") if item.strip()]
        for raw in read_tsv(source_path):
            if benchmark == "qft" and raw.get("stage_label") != "total":
                continue
            gate_count = _logical_gate_count(benchmark, raw, num_qubits)
            total_time_s = float(raw["total_time_s"])
            time_per_gate_s = float(raw["time_per_gate_s"]) if raw.get("time_per_gate_s") not in (None, "") else (
                total_time_s / gate_count if gate_count else math.nan
            )
            for scale_type in scale_types:
                samples.append(
                    {
                        "source_point_id": point["point_id"],
                        "scale_type": scale_type,
                        "benchmark": benchmark,
                        "gate_kind": "" if point.get("gate_kind", "") in ("", "none") else point["gate_kind"],
                        "num_qubits": num_qubits,
                        "local_qubits": local_qubits,
                        "local_amplitudes": 1 << local_qubits,
                        "logical_gate_count": gate_count,
                        "mpi_ranks": mpi_ranks,
                        "gpus": int(point["gpus"]),
                        "slurm_nodes": int(point["slurm_nodes"]),
                        "rep": int(raw["rep"]),
                        "warmup": int(raw["warmup"]),
                        "status": raw["status"],
                        "total_time_s": total_time_s,
                        "time_per_gate_s": time_per_gate_s,
                        "source_file": point["source_file"],
                    }
                )
    return samples


def _summary_status(rows, measured_pass):
    measured = [row for row in rows if row["warmup"] == 0]
    if not measured:
        return "MISSING"
    if not measured_pass:
        return "FAIL"
    if len(measured_pass) != len(measured):
        return "PARTIAL"
    return "PASS"


def summarize_samples(samples):
    grouped = defaultdict(list)
    for row in samples:
        key = (
            row["scale_type"],
            row["benchmark"],
            row.get("gate_kind", ""),
            row["mpi_ranks"],
            row["num_qubits"],
        )
        grouped[key].append(row)

    summaries = []
    for key, rows in grouped.items():
        scale_type, benchmark, gate_kind, mpi_ranks, num_qubits = key
        measured_pass = [row for row in rows if row["warmup"] == 0 and row["status"] == "PASS"]
        durations = [row["total_time_s"] for row in measured_pass]
        per_gate = [row["time_per_gate_s"] for row in measured_pass if math.isfinite(row["time_per_gate_s"])]
        mean = statistics.mean(durations) if durations else math.nan
        std = statistics.stdev(durations) if len(durations) > 1 else (0.0 if durations else math.nan)
        first = rows[0]
        summaries.append(
            {
                "source_point_id": first["source_point_id"],
                "scale_type": scale_type,
                "benchmark": benchmark,
                "gate_kind": gate_kind,
                "num_qubits": num_qubits,
                "local_qubits": first["local_qubits"],
                "local_amplitudes": first["local_amplitudes"],
                "logical_gate_count": first["logical_gate_count"],
                "mpi_ranks": mpi_ranks,
                "gpus": first["gpus"],
                "slurm_nodes": first["slurm_nodes"],
                "summary_status": _summary_status(rows, measured_pass),
                "sample_count": len(durations),
                "median_time_s": statistics.median(durations) if durations else math.nan,
                "mean_time_s": mean,
                "std_time_s": std,
                "min_time_s": min(durations) if durations else math.nan,
                "max_time_s": max(durations) if durations else math.nan,
                "p95_time_s": percentile(durations, 0.95),
                "cv": std / mean if durations and mean else math.nan,
                "median_time_per_gate_s": statistics.median(per_gate) if per_gate else math.nan,
                "speedup": math.nan,
                "parallel_efficiency": math.nan,
                "parallel_overhead_s": math.nan,
                "weak_efficiency": math.nan,
                "weak_slowdown": math.nan,
                "normalized_per_gate_efficiency": math.nan,
                "normalized_per_gate_slowdown": math.nan,
            }
        )

    baselines = {}
    for row in summaries:
        key = (row["scale_type"], row["benchmark"], row["gate_kind"])
        if row["mpi_ranks"] == 1 and row["summary_status"] == "PASS":
            baselines[key] = row

    for row in summaries:
        baseline = baselines.get((row["scale_type"], row["benchmark"], row["gate_kind"]))
        if baseline is None or row["summary_status"] != "PASS":
            continue
        base_time = baseline["median_time_s"]
        point_time = row["median_time_s"]
        base_per_gate = baseline["median_time_per_gate_s"]
        point_per_gate = row["median_time_per_gate_s"]
        if row["scale_type"] == "strong":
            row["speedup"] = base_time / point_time
            row["parallel_efficiency"] = row["speedup"] / row["mpi_ranks"]
            row["parallel_overhead_s"] = row["mpi_ranks"] * point_time - base_time
        elif row["scale_type"] == "weak":
            row["weak_efficiency"] = base_time / point_time
            row["weak_slowdown"] = point_time / base_time
            if math.isfinite(base_per_gate) and math.isfinite(point_per_gate):
                row["normalized_per_gate_efficiency"] = base_per_gate / point_per_gate
                row["normalized_per_gate_slowdown"] = point_per_gate / base_per_gate

    return sorted(summaries, key=lambda row: (row["scale_type"], row["benchmark"], row["gate_kind"], row["mpi_ranks"]))


def validate_samples(samples, manifest_rows, expected_reps, expected_warmup):
    physical = defaultdict(dict)
    for row in samples:
        key = (row["rep"], row["warmup"])
        physical[row["source_point_id"]][key] = row

    errors = []
    for point in manifest_rows:
        point_id = point["point_id"]
        rows = list(physical.get(point_id, {}).values())
        warmups = [row for row in rows if row["warmup"] == 1]
        measured = [row for row in rows if row["warmup"] == 0]
        if len(warmups) != expected_warmup:
            errors.append(f"{point_id}: expected {expected_warmup} warmup rows, found {len(warmups)}")
        if len(measured) != expected_reps:
            errors.append(f"{point_id}: expected {expected_reps} measured rows, found {len(measured)}")
        failed = [row for row in rows if row["status"] != "PASS"]
        if failed:
            errors.append(f"{point_id}: found {len(failed)} non-PASS rows")
    if errors:
        raise ValueError("Scaling campaign validation failed:\n" + "\n".join(errors))


def load_profile_summary(path, manifest_rows):
    metadata = {row.get("profile_point", ""): row for row in manifest_rows if row.get("profile_point")}
    output = []
    for profile in read_tsv(path):
        point = metadata.get(profile["point"])
        if point is None:
            continue
        for scale_type in [item.strip() for item in point["scale_membership"].split(",") if item.strip()]:
            joined = dict(profile)
            joined.update(
                {
                    "source_point_id": point["point_id"],
                    "scale_type": scale_type,
                    "gate_kind": "" if point.get("gate_kind", "") in ("", "none") else point["gate_kind"],
                    "gpus": int(point["gpus"]),
                    "slurm_nodes": int(point["slurm_nodes"]),
                }
            )
            output.append(joined)
    return output


def _workload_label(row):
    gate_kind = row.get("gate_kind", "")
    return f"{row['benchmark']}/{gate_kind}" if gate_kind else row["benchmark"]


def _scaling_label(row):
    return f"{row['scale_type']} {_workload_label(row)}"


def generate_highlights(summaries, profiles):
    highlights = []
    for row in summaries:
        label = _scaling_label(row)
        ranks = row["mpi_ranks"]
        speedup = row.get("speedup", math.nan)
        efficiency = row.get("parallel_efficiency", math.nan)
        weak_slowdown = row.get("weak_slowdown", math.nan)
        cv = row.get("cv", math.nan)
        if math.isfinite(speedup) and speedup < 1.0:
            highlights.append(f"- {label} at {ranks} GPUs shows negative scaling: speedup {speedup:.3f}x.")
        if math.isfinite(efficiency) and efficiency < 0.70:
            highlights.append(f"- {label} at {ranks} GPUs has parallel efficiency {100.0 * efficiency:.1f}%.")
        if math.isfinite(weak_slowdown) and weak_slowdown > 1.25:
            highlights.append(f"- {label} at {ranks} GPUs has weak slowdown {weak_slowdown:.3f}x.")
        if math.isfinite(cv) and cv > 0.10:
            highlights.append(f"- {label} at {ranks} GPUs has timing CV {100.0 * cv:.1f}%.")

    profile_groups = defaultdict(list)
    for row in profiles:
        key = (row["scale_type"], row["benchmark"], row.get("gate_kind", ""))
        profile_groups[key].append(row)
        imbalance = float(row.get("rank_imbalance_pct", 0) or 0)
        if imbalance > 10.0:
            highlights.append(
                f"- {_scaling_label(row)} at {row['mpi_ranks']} GPUs has rank imbalance {imbalance:.1f}%."
            )

    for rows in profile_groups.values():
        ordered = sorted(rows, key=lambda row: int(row["mpi_ranks"]))
        if len(ordered) < 2:
            continue
        first = ordered[0]
        last = ordered[-1]
        growth = float(last.get("communication_pct", 0) or 0) - float(first.get("communication_pct", 0) or 0)
        if growth >= 10.0:
            highlights.append(
                f"- {_scaling_label(last)} communication share increased by {growth:.1f} percentage points "
                f"from {first['mpi_ranks']} to {last['mpi_ranks']} GPUs."
            )

    if not highlights:
        highlights.append("- No configured scaling warning threshold was crossed.")
    return "# Scaling Highlights\n\n" + "\n".join(highlights) + "\n"


def _profile_fields(rows):
    preferred = ["source_point_id", "scale_type", "gate_kind"]
    discovered = []
    for row in rows:
        for key in row:
            if key not in preferred and key not in discovered:
                discovered.append(key)
    return preferred + discovered


def analyse_campaign(campaign_dir, manifest_path=None, validate=False, expected_reps=5, expected_warmup=1):
    campaign_dir = Path(campaign_dir)
    manifest_path = Path(manifest_path) if manifest_path is not None else campaign_dir / "point_manifest.tsv"
    manifest_rows = read_tsv(manifest_path)
    samples = load_samples(campaign_dir, manifest_rows)
    if validate:
        validate_samples(samples, manifest_rows, expected_reps, expected_warmup)
    summaries = summarize_samples(samples)
    procedure_path = campaign_dir / "procedure_breakdown.tsv"
    profiles = load_profile_summary(procedure_path, manifest_rows) if procedure_path.is_file() else []

    paths = {
        "samples": campaign_dir / "scaling_samples.tsv",
        "summary": campaign_dir / "scaling_summary.tsv",
        "profile_summary": campaign_dir / "scaling_profile_summary.tsv",
        "highlights": campaign_dir / "scaling_highlights.md",
    }
    write_tsv(paths["samples"], SAMPLE_FIELDS, samples)
    write_tsv(paths["summary"], SUMMARY_FIELDS, summaries)
    write_tsv(paths["profile_summary"], _profile_fields(profiles), profiles)
    paths["highlights"].write_text(generate_highlights(summaries, profiles), encoding="utf-8")
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--expected-reps", default=5, type=int)
    parser.add_argument("--expected-warmup", default=1, type=int)
    args = parser.parse_args()
    paths = analyse_campaign(
        args.campaign_dir,
        args.manifest,
        validate=args.validate,
        expected_reps=args.expected_reps,
        expected_warmup=args.expected_warmup,
    )
    for name, path in paths.items():
        print(f"{name}={path}")


if __name__ == "__main__":
    main()
