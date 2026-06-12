#!/usr/bin/env python3
"""Aggregate the three required follow-up GPU experiments."""

import argparse
import csv
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path


BOOTSTRAP_SEED = 20260612
BOOTSTRAP_ITERATIONS = 10000


def read_tsv(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(path, fieldnames, rows):
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def percentile(values, quantile):
    if not values:
        return math.nan
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def summarize(values):
    mean = statistics.mean(values) if values else math.nan
    std = statistics.stdev(values) if len(values) > 1 else (0.0 if values else math.nan)
    return {
        "sample_count": len(values),
        "median_time_s": statistics.median(values) if values else math.nan,
        "mean_time_s": mean,
        "std_time_s": std,
        "min_time_s": min(values) if values else math.nan,
        "max_time_s": max(values) if values else math.nan,
        "p95_time_s": percentile(values, 0.95),
        "cv": std / mean if values and mean else math.nan,
    }


def bootstrap_speedup(baseline, point, iterations=BOOTSTRAP_ITERATIONS, seed=BOOTSTRAP_SEED):
    if not baseline or not point:
        return {
            "median_speedup": math.nan,
            "ci_low": math.nan,
            "ci_high": math.nan,
            "observed_crossover": False,
            "robust_crossover": False,
        }
    rng = random.Random(seed)
    ratios = []
    for _ in range(iterations):
        base_sample = [rng.choice(baseline) for _ in baseline]
        point_sample = [rng.choice(point) for _ in point]
        ratios.append(statistics.median(base_sample) / statistics.median(point_sample))
    speedup = statistics.median(baseline) / statistics.median(point)
    low = percentile(ratios, 0.025)
    high = percentile(ratios, 0.975)
    return {
        "median_speedup": speedup,
        "ci_low": low,
        "ci_high": high,
        "observed_crossover": speedup > 1.0,
        "robust_crossover": low > 1.0,
    }


def reproducibility_verdict(rows):
    allocations = defaultdict(dict)
    for row in rows:
        allocations[str(row["allocation"])][int(row["mpi_ranks"])] = float(row["median_time_s"])
    complete = [values for values in allocations.values() if {1, 2, 4}.issubset(values)]
    flags = [values[2] > values[1] and values[4] > values[1] for values in complete]
    return {
        "allocation_count": len(complete),
        "reproducible_negative_scaling": len(complete) >= 3 and all(flags),
    }


def gate_path_verdict(rows):
    observed = {}
    for row in rows:
        key = (row["gate_kind"], int(row["mpi_ranks"]))
        observed[key] = (
            float(row.get("communication_time_s", 0) or 0) > 0
            and int(float(row.get("aggregate_mpi_send_bytes", 0) or 0)) > 0
            and row.get("communication_path") == "cpu_staged"
        )
    expected = {
        ("h", 1): False,
        ("h", 4): True,
        ("cphase", 1): False,
        ("cphase", 4): False,
    }
    return {
        "communication_only_on_h_p4": all(observed.get(key) == value for key, value in expected.items())
    }


def _measured_times(run_dir, point):
    rows = read_tsv(run_dir / point["source_file"])
    output = []
    for row in rows:
        if point["benchmark"] == "qft" and row.get("stage_label") != "total":
            continue
        if row.get("warmup") == "0" and row.get("status") == "PASS":
            output.append(float(row["total_time_s"]))
    return output


def _load_runs(campaign_dir):
    manifest_path = campaign_dir / "submission_manifest.tsv"
    if not manifest_path.is_file():
        return []
    runs = []
    for row in read_tsv(manifest_path):
        run_dir = campaign_dir / row["run_dir"]
        if run_dir.is_dir() and (run_dir / "point_manifest.tsv").is_file():
            item = dict(row)
            item["run_dir_path"] = run_dir
            runs.append(item)
    return runs


def _validate_campaign_structure(runs):
    by_mode = defaultdict(list)
    for run in runs:
        by_mode[run["mode"]].append(run)
        if run.get("state") != "COMPLETED":
            raise ValueError(f"job {run.get('job_id', 'unknown')} is not COMPLETED")

    if len(by_mode["repro"]) != 3:
        raise ValueError(f"required campaign needs three repro runs, found {len(by_mode['repro'])}")
    if len(by_mode["qft-sweep"]) != 1:
        raise ValueError(f"required campaign needs one qft-sweep run, found {len(by_mode['qft-sweep'])}")
    if len(by_mode["gate-path"]) != 1:
        raise ValueError(f"required campaign needs one gate-path run, found {len(by_mode['gate-path'])}")

    repro_nodes = {run.get("node", "").split(".")[0] for run in by_mode["repro"] if run.get("node")}
    if len(repro_nodes) < 2:
        raise ValueError(f"repro runs must cover at least two nodes, found {sorted(repro_nodes)}")

    expected_points = {"repro": 3, "qft-sweep": 18, "gate-path": 4}
    for mode, mode_runs in by_mode.items():
        for run in mode_runs:
            manifest = read_tsv(run["run_dir_path"] / "point_manifest.tsv")
            if len(manifest) != expected_points[mode]:
                raise ValueError(
                    f"{run['run_dir']}: expected {expected_points[mode]} points, found {len(manifest)}"
                )
            for point in manifest:
                raw_rows = read_tsv(run["run_dir_path"] / point["source_file"])
                if point["benchmark"] == "qft":
                    raw_rows = [row for row in raw_rows if row.get("stage_label") == "total"]
                warmups = [row for row in raw_rows if row.get("warmup") == "1" and row.get("status") == "PASS"]
                measured = [row for row in raw_rows if row.get("warmup") == "0" and row.get("status") == "PASS"]
                if len(warmups) != 1 or len(measured) != 10:
                    raise ValueError(
                        f"{point['point_id']}: expected 1 warmup and 10 measured PASS rows, "
                        f"found {len(warmups)} and {len(measured)}"
                    )

    gate_run = by_mode["gate-path"][0]["run_dir_path"]
    gate_manifest = read_tsv(gate_run / "point_manifest.tsv")
    for point in gate_manifest:
        profile_base = gate_run / "profiles" / point["point_id"]
        for suffix in (".nsys-rep", ".sqlite"):
            path = Path(f"{profile_base}{suffix}")
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError(f"profile artifact missing: {path}")
    for name in (
        "procedure_breakdown_rank.tsv",
        "procedure_breakdown.tsv",
        "communication_breakdown_rank.tsv",
        "computation_breakdown_rank.tsv",
        "lifecycle_breakdown_rank.tsv",
        "cuda_runtime_summary_rank.tsv",
    ):
        path = gate_run / name
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"gate-path breakdown missing: {path}")


def _validate_point(point_id, times, expected=10):
    if len(times) != expected:
        raise ValueError(f"{point_id}: expected {expected} measured PASS rows, found {len(times)}")


def _repro_outputs(runs, validate):
    allocation_rows = []
    samples_by_rank = defaultdict(list)
    for run in runs:
        if run["mode"] != "repro":
            continue
        for point in read_tsv(run["run_dir_path"] / "point_manifest.tsv"):
            times = _measured_times(run["run_dir_path"], point)
            if validate:
                _validate_point(point["point_id"], times)
            stats = summarize(times)
            rank = int(point["mpi_ranks"])
            samples_by_rank[rank].extend(times)
            allocation_rows.append({
                "allocation": int(run["allocation"]),
                "job_id": run["job_id"],
                "node": run.get("node", ""),
                "mpi_ranks": rank,
                "num_qubits": int(point["num_qubits"]),
                **stats,
            })
    by_allocation = defaultdict(dict)
    for row in allocation_rows:
        by_allocation[row["allocation"]][row["mpi_ranks"]] = row
    for points in by_allocation.values():
        if 1 not in points:
            continue
        baseline = points[1]["median_time_s"]
        for rank, row in points.items():
            row["speedup"] = baseline / row["median_time_s"]
            row["parallel_efficiency"] = row["speedup"] / rank

    summary_rows = []
    for rank in sorted(samples_by_rank):
        medians = [row["median_time_s"] for row in allocation_rows if row["mpi_ranks"] == rank]
        stats = summarize(medians)
        summary_rows.append({"mpi_ranks": rank, "allocation_count": len(medians), **stats})
    return allocation_rows, summary_rows


def _sweep_outputs(runs, validate):
    sample_groups = defaultdict(list)
    for run in runs:
        if run["mode"] != "qft-sweep":
            continue
        for point in read_tsv(run["run_dir_path"] / "point_manifest.tsv"):
            times = _measured_times(run["run_dir_path"], point)
            if validate:
                _validate_point(point["point_id"], times)
            sample_groups[(int(point["num_qubits"]), int(point["mpi_ranks"]))].extend(times)

    summary_rows = []
    crossover_rows = []
    for qubits in sorted({key[0] for key in sample_groups}):
        baseline = sample_groups[(qubits, 1)]
        for ranks in (1, 2, 4):
            values = sample_groups.get((qubits, ranks), [])
            stats = summarize(values)
            speedup = bootstrap_speedup(baseline, values, seed=BOOTSTRAP_SEED + qubits * 10 + ranks)
            summary_rows.append({
                "num_qubits": qubits,
                "mpi_ranks": ranks,
                **stats,
                "speedup": speedup["median_speedup"],
                "parallel_efficiency": speedup["median_speedup"] / ranks,
            })
            crossover_rows.append({
                "num_qubits": qubits,
                "mpi_ranks": ranks,
                "speedup": speedup["median_speedup"],
                "speedup_ci_low": speedup["ci_low"],
                "speedup_ci_high": speedup["ci_high"],
                "observed_crossover": int(speedup["observed_crossover"]),
                "robust_crossover": int(speedup["robust_crossover"]),
                "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
                "bootstrap_seed": BOOTSTRAP_SEED,
            })
    return summary_rows, crossover_rows


def _gate_outputs(runs, validate):
    timing_rows = []
    profile_rows = []
    for run in runs:
        if run["mode"] != "gate-path":
            continue
        manifest = read_tsv(run["run_dir_path"] / "point_manifest.tsv")
        metadata = {row["point_id"]: row for row in manifest}
        groups = {}
        for point in manifest:
            times = _measured_times(run["run_dir_path"], point)
            if validate:
                _validate_point(point["point_id"], times)
            groups[(point["gate_kind"], int(point["mpi_ranks"]))] = times
        for (gate, ranks), times in groups.items():
            stats = summarize(times)
            baseline = groups[(gate, 1)]
            speedup = statistics.median(baseline) / statistics.median(times)
            timing_rows.append({
                "gate_kind": gate,
                "num_qubits": 28,
                "mpi_ranks": ranks,
                **stats,
                "speedup": speedup,
                "parallel_efficiency": speedup / ranks,
            })
        procedure_path = run["run_dir_path"] / "procedure_breakdown.tsv"
        if procedure_path.is_file():
            for row in read_tsv(procedure_path):
                point = metadata.get(row["point"])
                if point is None:
                    continue
                joined = dict(row)
                joined["gate_kind"] = point["gate_kind"]
                profile_rows.append(joined)
    return timing_rows, profile_rows


def _answer_text(repro_rows, crossover_rows, gate_timing, gate_profiles):
    repro = reproducibility_verdict(repro_rows)
    robust = [row for row in crossover_rows if int(row["mpi_ranks"]) > 1 and int(row["robust_crossover"]) == 1]
    observed = [row for row in crossover_rows if int(row["mpi_ranks"]) > 1 and int(row["observed_crossover"]) == 1]
    gate = gate_path_verdict(gate_profiles) if gate_profiles else {"communication_only_on_h_p4": False}
    efficiency = {(row["gate_kind"], int(row["mpi_ranks"])): float(row["parallel_efficiency"]) for row in gate_timing}

    if repro["allocation_count"] < 3:
        repro_answer = "Insufficient completed allocations to answer reproducibility."
    elif repro["reproducible_negative_scaling"]:
        repro_answer = "Negative QFT q28 scaling was reproducible in all three independent allocations."
    else:
        repro_answer = "Negative QFT q28 scaling was not reproduced in every independent allocation."

    if robust:
        first = min(robust, key=lambda row: (int(row["num_qubits"]), int(row["mpi_ranks"])))
        sweep_answer = f"A robust crossover first appeared at q{first['num_qubits']} on {first['mpi_ranks']} GPUs."
    elif observed:
        first = min(observed, key=lambda row: (int(row["num_qubits"]), int(row["mpi_ranks"])))
        sweep_answer = f"Only an observed, non-robust crossover appeared, first at q{first['num_qubits']} on {first['mpi_ranks']} GPUs."
    elif crossover_rows:
        sweep_answer = "No QFT crossover was observed from q24 through q29 on 2 or 4 GPUs."
    else:
        sweep_answer = "The QFT size sweep is incomplete."

    h_eff = efficiency.get(("h", 4), math.nan)
    cphase_eff = efficiency.get(("cphase", 4), math.nan)
    if gate["communication_only_on_h_p4"] and math.isfinite(h_eff) and math.isfinite(cphase_eff):
        gate_answer = (
            "MPI traffic and communication time appeared only on the non-local 4-GPU H path; "
            f"CPhase retained higher 4-GPU parallel efficiency ({100*cphase_eff:.2f}% versus {100*h_eff:.2f}%)."
        )
    else:
        gate_answer = "The matched gate-path evidence did not fully satisfy the expected communication-path pattern."

    return (
        "# Required Experiment Answers\n\n"
        f"## Independent-allocation reproducibility\n\n{repro_answer}\n\n"
        f"## QFT problem-size sweep\n\n{sweep_answer}\n\n"
        f"## Controlled gate-path comparison\n\n{gate_answer}\n"
    )


def analyse_required_campaign(campaign_dir, validate=False):
    campaign_dir = Path(campaign_dir)
    runs = _load_runs(campaign_dir)
    if validate:
        _validate_campaign_structure(runs)
    repro_rows, repro_summary = _repro_outputs(runs, validate)
    sweep_summary, crossover = _sweep_outputs(runs, validate)
    gate_timing, gate_profiles = _gate_outputs(runs, validate)

    paths = {
        "repro_allocations": campaign_dir / "qft_repro_allocation_summary.tsv",
        "repro_summary": campaign_dir / "qft_repro_summary.tsv",
        "sweep_summary": campaign_dir / "qft_size_sweep_summary.tsv",
        "crossover": campaign_dir / "qft_crossover.tsv",
        "gate_timing": campaign_dir / "gate_path_timing_summary.tsv",
        "gate_profile": campaign_dir / "gate_path_profile_summary.tsv",
        "answers": campaign_dir / "required_experiment_answers.md",
    }
    write_tsv(paths["repro_allocations"], ["allocation", "job_id", "node", "mpi_ranks", "num_qubits", "sample_count", "median_time_s", "mean_time_s", "std_time_s", "min_time_s", "max_time_s", "p95_time_s", "cv", "speedup", "parallel_efficiency"], repro_rows)
    write_tsv(paths["repro_summary"], ["mpi_ranks", "allocation_count", "sample_count", "median_time_s", "mean_time_s", "std_time_s", "min_time_s", "max_time_s", "p95_time_s", "cv"], repro_summary)
    write_tsv(paths["sweep_summary"], ["num_qubits", "mpi_ranks", "sample_count", "median_time_s", "mean_time_s", "std_time_s", "min_time_s", "max_time_s", "p95_time_s", "cv", "speedup", "parallel_efficiency"], sweep_summary)
    write_tsv(paths["crossover"], ["num_qubits", "mpi_ranks", "speedup", "speedup_ci_low", "speedup_ci_high", "observed_crossover", "robust_crossover", "bootstrap_iterations", "bootstrap_seed"], crossover)
    write_tsv(paths["gate_timing"], ["gate_kind", "num_qubits", "mpi_ranks", "sample_count", "median_time_s", "mean_time_s", "std_time_s", "min_time_s", "max_time_s", "p95_time_s", "cv", "speedup", "parallel_efficiency"], gate_timing)
    profile_fields = []
    for row in gate_profiles:
        for key in row:
            if key not in profile_fields:
                profile_fields.append(key)
    write_tsv(paths["gate_profile"], profile_fields or ["point", "gate_kind"], gate_profiles)
    paths["answers"].write_text(_answer_text(repro_rows, crossover, gate_timing, gate_profiles), encoding="utf-8")
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    for name, path in analyse_required_campaign(args.campaign_dir, validate=args.validate).items():
        print(f"{name}={path}")


if __name__ == "__main__":
    main()
