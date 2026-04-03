#!/usr/bin/env python3

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_RAW_DIR = REPO_ROOT / "experiments" / "results" / "raw"
DEFAULT_OUT_DIR = REPO_ROOT / "experiments" / "results" / "processed"


def info(message: str) -> None:
    print(f">>> {message}", flush=True)


def float_or_none(text: Optional[str]) -> Optional[float]:
    if text is None or text == "":
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if math.isnan(value):
        return None
    return value


def int_or_none(text: Optional[str]) -> Optional[int]:
    if text is None or text == "":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def load_rows(raw_dir: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for path in sorted(raw_dir.glob("*.tsv")):
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                row["_source_file"] = path.name
                rows.append(row)
    return rows


def row_env_num_threads(row: Dict[str, str]) -> Optional[int]:
    return int_or_none(row.get("env_num_threads"))


def grouped_stats(values: List[float]) -> Tuple[float, float, int]:
    if not values:
        return math.nan, math.nan, 0
    if len(values) == 1:
        return values[0], 0.0, 1
    return statistics.fmean(values), statistics.stdev(values), len(values)


def write_tsv(path: Path, header: List[str], rows: List[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def build_capacity_matrix(rows: List[Dict[str, str]]) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, str, str, Optional[int]], Dict[str, str]] = {}
    for row in rows:
        if row.get("benchmark") != "probe":
            continue
        key = (row["platform"], row["backend"], row["deployment"], row_env_num_threads(row))
        current = grouped.get(key)
        if current is None or int_or_none(row.get("max_qubits")) is not None and int_or_none(row.get("max_qubits")) > int_or_none(current.get("max_qubits")):
            grouped[key] = row

    out: List[Dict[str, object]] = []
    for (platform, backend, deployment, env_num_threads), row in sorted(grouped.items()):
        out.append(
            {
                "platform": platform,
                "backend": backend,
                "deployment": deployment,
                "env_num_threads": env_num_threads,
                "max_qubits": int_or_none(row.get("max_qubits")),
                "probe_attempts": int_or_none(row.get("probe_attempts")),
                "alloc_time_s": float_or_none(row.get("alloc_time_s")),
                "validation_time_s": float_or_none(row.get("validation_time_s")),
                "status": row.get("status"),
            }
        )
    return out


def build_perf_matrix(rows: List[Dict[str, str]]) -> List[Dict[str, object]]:
    qft_values: Dict[Tuple[str, str, str, str, int, Optional[int]], List[float]] = defaultdict(list)
    random_values: Dict[Tuple[str, str, str, str, int, Optional[int]], List[float]] = defaultdict(list)
    h_rep_values: Dict[Tuple[str, str, str, str, int, Optional[int], str], List[float]] = defaultdict(list)

    for row in rows:
        if row.get("warmup") != "0" or row.get("status") != "PASS":
            continue
        platform = row["platform"]
        backend = row["backend"]
        deployment = row["deployment"]
        benchmark = row["benchmark"]
        qubits = int_or_none(row.get("num_qubits"))
        env_num_threads = row_env_num_threads(row)
        if qubits is None:
            continue

        if benchmark == "qft" and row.get("stage_label") == "total":
            value = float_or_none(row.get("total_time_s"))
            if value is not None:
                qft_values[(platform, backend, deployment, benchmark, qubits, env_num_threads)].append(value)
        elif benchmark == "random":
            value = float_or_none(row.get("total_time_s"))
            if value is not None:
                random_values[(platform, backend, deployment, benchmark, qubits, env_num_threads)].append(value)
        elif benchmark == "h_sweep":
            value = float_or_none(row.get("gate_time_s"))
            rep = row.get("rep")
            if value is not None and rep is not None:
                h_rep_values[(platform, backend, deployment, benchmark, qubits, env_num_threads, rep)].append(value)

    perf_rows: List[Dict[str, object]] = []
    for key, values in sorted(qft_values.items()):
        mean_value, std_value, sample_count = grouped_stats(values)
        platform, backend, deployment, benchmark, qubits, env_num_threads = key
        perf_rows.append(
            {
                "platform": platform,
                "backend": backend,
                "deployment": deployment,
                "benchmark": benchmark,
                "num_qubits": qubits,
                "env_num_threads": env_num_threads,
                "mean_time_s": mean_value,
                "std_time_s": std_value,
                "samples": sample_count,
            }
        )

    for key, values in sorted(random_values.items()):
        mean_value, std_value, sample_count = grouped_stats(values)
        platform, backend, deployment, benchmark, qubits, env_num_threads = key
        perf_rows.append(
            {
                "platform": platform,
                "backend": backend,
                "deployment": deployment,
                "benchmark": benchmark,
                "num_qubits": qubits,
                "env_num_threads": env_num_threads,
                "mean_time_s": mean_value,
                "std_time_s": std_value,
                "samples": sample_count,
            }
        )

    h_grouped: Dict[Tuple[str, str, str, str, int, Optional[int]], List[float]] = defaultdict(list)
    for key, values in sorted(h_rep_values.items()):
        platform, backend, deployment, benchmark, qubits, env_num_threads, _rep = key
        h_grouped[(platform, backend, deployment, benchmark, qubits, env_num_threads)].append(statistics.fmean(values))

    for key, values in sorted(h_grouped.items()):
        mean_value, std_value, sample_count = grouped_stats(values)
        platform, backend, deployment, benchmark, qubits, env_num_threads = key
        perf_rows.append(
            {
                "platform": platform,
                "backend": backend,
                "deployment": deployment,
                "benchmark": benchmark,
                "num_qubits": qubits,
                "env_num_threads": env_num_threads,
                "mean_time_s": mean_value,
                "std_time_s": std_value,
                "samples": sample_count,
            }
        )

    return perf_rows


def build_degradation_matrix(perf_rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    baseline: Dict[Tuple[str, str, str, str, Optional[int]], float] = {}
    for row in perf_rows:
        key = (
            str(row["platform"]),
            str(row["backend"]),
            str(row["deployment"]),
            str(row["benchmark"]),
            row.get("env_num_threads") if isinstance(row.get("env_num_threads"), int) else int_or_none(str(row.get("env_num_threads"))) if row.get("env_num_threads") not in ("", None) else None,
        )
        if row["num_qubits"] == 26:
            baseline[key] = float(row["mean_time_s"])

    out: List[Dict[str, object]] = []
    for row in perf_rows:
        key = (
            str(row["platform"]),
            str(row["backend"]),
            str(row["deployment"]),
            str(row["benchmark"]),
            row.get("env_num_threads") if isinstance(row.get("env_num_threads"), int) else int_or_none(str(row.get("env_num_threads"))) if row.get("env_num_threads") not in ("", None) else None,
        )
        if key not in baseline or baseline[key] == 0.0:
            continue
        out.append(
            {
                "platform": row["platform"],
                "backend": row["backend"],
                "deployment": row["deployment"],
                "benchmark": row["benchmark"],
                "num_qubits": row["num_qubits"],
                "env_num_threads": row.get("env_num_threads"),
                "baseline_qubits": 26,
                "baseline_mean_time_s": baseline[key],
                "mean_time_s": row["mean_time_s"],
                "slowdown_vs_26": float(row["mean_time_s"]) / baseline[key],
            }
        )
    return out


def build_qft_stage_matrix(rows: List[Dict[str, str]]) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, str, str, int, Optional[int], int, str], List[float]] = defaultdict(list)
    for row in rows:
        if row.get("benchmark") != "qft" or row.get("warmup") != "0" or row.get("status") != "PASS":
            continue
        if row.get("stage_label") == "total":
            continue
        value = float_or_none(row.get("stage_time_s"))
        qubits = int_or_none(row.get("num_qubits"))
        stage = int_or_none(row.get("stage"))
        env_num_threads = row_env_num_threads(row)
        if value is None or qubits is None or stage is None:
            continue
        key = (row["platform"], row["backend"], row["deployment"], qubits, env_num_threads, stage, row["stage_label"])
        grouped[key].append(value)

    out: List[Dict[str, object]] = []
    for key, values in sorted(grouped.items()):
        platform, backend, deployment, qubits, env_num_threads, stage, stage_label = key
        mean_value, std_value, sample_count = grouped_stats(values)
        out.append(
            {
                "platform": platform,
                "backend": backend,
                "deployment": deployment,
                "num_qubits": qubits,
                "env_num_threads": env_num_threads,
                "stage": stage,
                "stage_label": stage_label,
                "mean_stage_time_s": mean_value,
                "std_stage_time_s": std_value,
                "samples": sample_count,
            }
        )
    return out


def build_h_target_matrix(rows: List[Dict[str, str]]) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, str, str, int, Optional[int], int], List[float]] = defaultdict(list)
    for row in rows:
        if row.get("benchmark") != "h_sweep" or row.get("warmup") != "0" or row.get("status") != "PASS":
            continue
        value = float_or_none(row.get("gate_time_s"))
        qubits = int_or_none(row.get("num_qubits"))
        target = int_or_none(row.get("target_qubit"))
        env_num_threads = row_env_num_threads(row)
        if value is None or qubits is None or target is None:
            continue
        key = (row["platform"], row["backend"], row["deployment"], qubits, env_num_threads, target)
        grouped[key].append(value)

    out: List[Dict[str, object]] = []
    for key, values in sorted(grouped.items()):
        platform, backend, deployment, qubits, env_num_threads, target = key
        mean_value, std_value, sample_count = grouped_stats(values)
        out.append(
            {
                "platform": platform,
                "backend": backend,
                "deployment": deployment,
                "num_qubits": qubits,
                "env_num_threads": env_num_threads,
                "target_qubit": target,
                "mean_gate_time_s": mean_value,
                "std_gate_time_s": std_value,
                "samples": sample_count,
            }
        )
    return out


def build_mpi_extension_matrix(rows: List[Dict[str, str]]) -> List[Dict[str, object]]:
    grouped: Dict[Tuple[str, str, str, int, int, Optional[int], str], List[float]] = defaultdict(list)
    counts: Dict[Tuple[str, str, str, int, int, Optional[int], str], int] = defaultdict(int)

    for row in rows:
        if row.get("benchmark") != "qft" or row.get("deployment") != "on":
            continue
        if row.get("stage_label") != "total":
            continue
        qubits = int_or_none(row.get("num_qubits"))
        num_nodes = int_or_none(row.get("env_num_nodes"))
        env_num_threads = row_env_num_threads(row)
        if qubits is None or num_nodes is None:
            continue
        status = row.get("status") or ""
        key = (row["platform"], row["backend"], row["deployment"], qubits, num_nodes, env_num_threads, status)
        counts[key] += 1
        value = float_or_none(row.get("total_time_s"))
        if value is not None:
            grouped[key].append(value)

    out: List[Dict[str, object]] = []
    keys = sorted(set(counts) | set(grouped))
    for key in keys:
        platform, backend, deployment, qubits, num_nodes, env_num_threads, status = key
        values = grouped.get(key, [])
        mean_value, std_value, sample_count = grouped_stats(values)
        out.append(
            {
                "platform": platform,
                "backend": backend,
                "deployment": deployment,
                "num_qubits": qubits,
                "env_num_nodes": num_nodes,
                "env_num_threads": env_num_threads,
                "status": status,
                "mean_total_time_s": mean_value,
                "std_total_time_s": std_value,
                "samples": sample_count,
                "rows": counts.get(key, 0),
            }
        )
    return out


def build_thread_perf_matrix(perf_rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for row in perf_rows:
        if row.get("env_num_threads") is None:
            continue
        out.append(dict(row))
    return out


def build_thread_speedup_matrix(perf_rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    baseline: Dict[Tuple[str, str, str, str, int], float] = {}
    out: List[Dict[str, object]] = []

    for row in perf_rows:
        threads = row.get("env_num_threads")
        if threads == 32:
            key = (str(row["platform"]), str(row["backend"]), str(row["deployment"]), str(row["benchmark"]), int(row["num_qubits"]))
            baseline[key] = float(row["mean_time_s"])

    for row in perf_rows:
        threads = row.get("env_num_threads")
        if threads is None:
            continue
        key = (str(row["platform"]), str(row["backend"]), str(row["deployment"]), str(row["benchmark"]), int(row["num_qubits"]))
        if key not in baseline or float(row["mean_time_s"]) == 0.0:
            continue
        speedup = baseline[key] / float(row["mean_time_s"])
        efficiency = speedup / (float(threads) / 32.0)
        out.append(
            {
                "platform": row["platform"],
                "backend": row["backend"],
                "deployment": row["deployment"],
                "benchmark": row["benchmark"],
                "num_qubits": row["num_qubits"],
                "env_num_threads": threads,
                "baseline_threads": 32,
                "baseline_mean_time_s": baseline[key],
                "mean_time_s": row["mean_time_s"],
                "speedup_vs_32": speedup,
                "efficiency_vs_32": efficiency,
                "samples": row["samples"],
            }
        )
    return out


def build_thread_qft_stage_matrix(qft_stage_rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    out: List[Dict[str, object]] = []
    for row in qft_stage_rows:
        if row.get("env_num_threads") is None:
            continue
        out.append(dict(row))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate raw benchmark TSV files")
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    rows = load_rows(raw_dir)
    info(f"Loaded {len(rows)} raw rows from {raw_dir}")

    capacity_rows = build_capacity_matrix(rows)
    perf_rows = build_perf_matrix(rows)
    degradation_rows = build_degradation_matrix(perf_rows)
    qft_stage_rows = build_qft_stage_matrix(rows)
    h_target_rows = build_h_target_matrix(rows)
    mpi_rows = build_mpi_extension_matrix(rows)
    thread_perf_rows = build_thread_perf_matrix(perf_rows)
    thread_speedup_rows = build_thread_speedup_matrix(perf_rows)
    thread_qft_stage_rows = build_thread_qft_stage_matrix(qft_stage_rows)

    write_tsv(
        out_dir / "capacity_matrix.tsv",
        ["platform", "backend", "deployment", "env_num_threads", "max_qubits", "probe_attempts", "alloc_time_s", "validation_time_s", "status"],
        capacity_rows,
    )
    write_tsv(
        out_dir / "perf_matrix.tsv",
        ["platform", "backend", "deployment", "benchmark", "num_qubits", "env_num_threads", "mean_time_s", "std_time_s", "samples"],
        perf_rows,
    )
    write_tsv(
        out_dir / "degradation_matrix.tsv",
        ["platform", "backend", "deployment", "benchmark", "num_qubits", "env_num_threads", "baseline_qubits", "baseline_mean_time_s", "mean_time_s", "slowdown_vs_26"],
        degradation_rows,
    )
    write_tsv(
        out_dir / "qft_stage_matrix.tsv",
        ["platform", "backend", "deployment", "num_qubits", "env_num_threads", "stage", "stage_label", "mean_stage_time_s", "std_stage_time_s", "samples"],
        qft_stage_rows,
    )
    write_tsv(
        out_dir / "h_target_matrix.tsv",
        ["platform", "backend", "deployment", "num_qubits", "env_num_threads", "target_qubit", "mean_gate_time_s", "std_gate_time_s", "samples"],
        h_target_rows,
    )
    write_tsv(
        out_dir / "mpi_extension_matrix.tsv",
        ["platform", "backend", "deployment", "num_qubits", "env_num_nodes", "env_num_threads", "status", "mean_total_time_s", "std_total_time_s", "samples", "rows"],
        mpi_rows,
    )
    write_tsv(
        out_dir / "thread_perf_matrix.tsv",
        ["platform", "backend", "deployment", "benchmark", "num_qubits", "env_num_threads", "mean_time_s", "std_time_s", "samples"],
        thread_perf_rows,
    )
    write_tsv(
        out_dir / "thread_speedup_matrix.tsv",
        ["platform", "backend", "deployment", "benchmark", "num_qubits", "env_num_threads", "baseline_threads", "baseline_mean_time_s", "mean_time_s", "speedup_vs_32", "efficiency_vs_32", "samples"],
        thread_speedup_rows,
    )
    write_tsv(
        out_dir / "thread_qft_stage_matrix.tsv",
        ["platform", "backend", "deployment", "num_qubits", "env_num_threads", "stage", "stage_label", "mean_stage_time_s", "std_stage_time_s", "samples"],
        thread_qft_stage_rows,
    )

    info(f"Wrote processed matrices to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
