#!/usr/bin/env python3
"""Build per-rank and critical-rank procedure breakdowns from Nsight SQLite."""

import argparse
import csv
import sqlite3
from collections import defaultdict
from pathlib import Path


RANK_FIELDS = [
    "point",
    "benchmark",
    "num_qubits",
    "env_num_nodes",
    "rank",
    "procedure_wall_time_s",
    "computation_time_s",
    "communication_time_s",
    "others_time_s",
    "overlap_time_s",
    "computation_pct",
    "communication_pct",
    "others_pct",
    "mpi_send_calls",
    "mpi_send_bytes",
    "communication_path",
]

SUMMARY_FIELDS = [
    "point",
    "benchmark",
    "num_qubits",
    "env_num_nodes",
    "critical_rank",
    "procedure_wall_time_s",
    "computation_time_s",
    "communication_time_s",
    "others_time_s",
    "overlap_time_s",
    "computation_pct",
    "communication_pct",
    "others_pct",
    "mpi_send_calls",
    "mpi_send_bytes",
    "communication_path",
]

PACK_KERNEL_NAMES = (
    "kernel_statevec_packAmpsIntoBuffer",
    "kernel_statevec_packPairSummedAmpsIntoBuffer",
)


def merge_intervals(intervals):
    merged = []
    for start, end in sorted((int(start), int(end)) for start, end in intervals if end > start):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def intersect_intervals(left, right):
    left = merge_intervals(left)
    right = merge_intervals(right)
    intersections = []
    left_index = 0
    right_index = 0
    while left_index < len(left) and right_index < len(right):
        start = max(left[left_index][0], right[right_index][0])
        end = min(left[left_index][1], right[right_index][1])
        if start < end:
            intersections.append((start, end))
        if left[left_index][1] <= right[right_index][1]:
            left_index += 1
        else:
            right_index += 1
    return intersections


def subtract_intervals(intervals, exclusions):
    result = []
    exclusions = merge_intervals(exclusions)
    for start, end in merge_intervals(intervals):
        cursor = start
        for excluded_start, excluded_end in exclusions:
            if excluded_end <= cursor:
                continue
            if excluded_start >= end:
                break
            if cursor < excluded_start:
                result.append((cursor, min(excluded_start, end)))
            cursor = max(cursor, excluded_end)
            if cursor >= end:
                break
        if cursor < end:
            result.append((cursor, end))
    return result


def interval_duration(intervals):
    return sum(end - start for start, end in merge_intervals(intervals))


def classify_intervals(procedure, execution, communication, pack_kernels, simulation_kernels):
    procedure = merge_intervals(procedure)
    execution = intersect_intervals(execution, procedure)
    communication = intersect_intervals(
        merge_intervals(list(communication) + list(pack_kernels)), procedure
    )
    computation_candidates = intersect_intervals(simulation_kernels, execution)
    overlap = intersect_intervals(computation_candidates, communication)
    computation = subtract_intervals(computation_candidates, communication)

    procedure_ns = interval_duration(procedure)
    communication_ns = interval_duration(communication)
    computation_ns = interval_duration(computation)
    overlap_ns = interval_duration(overlap)
    covered_ns = interval_duration(merge_intervals(communication + computation))
    others_ns = max(0, procedure_ns - covered_ns)

    return {
        "procedure_ns": procedure_ns,
        "computation_ns": computation_ns,
        "communication_ns": communication_ns,
        "others_ns": others_ns,
        "overlap_ns": overlap_ns,
    }


def table_exists(connection, table_name):
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone() is not None


def table_columns(connection, table_name):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})")}


def string_values(connection):
    if not table_exists(connection, "StringIds"):
        return {}
    return dict(connection.execute("SELECT id, value FROM StringIds"))


def process_id_from_thread(global_thread_id):
    return int(global_thread_id) & ~0xFFFFFF


def load_rank_maps(connection):
    if not table_exists(connection, "MPI_RANKS"):
        return {}, {}
    thread_to_rank = {
        int(global_tid): int(rank)
        for global_tid, rank in connection.execute("SELECT globalTid, rank FROM MPI_RANKS")
    }
    process_to_rank = {
        process_id_from_thread(global_tid): rank
        for global_tid, rank in thread_to_rank.items()
    }
    return thread_to_rank, process_to_rank


def resolve_text(row, strings, text_index, text_id_index):
    text = row[text_index] if text_index is not None else None
    if text:
        return str(text)
    text_id = row[text_id_index] if text_id_index is not None else None
    return strings.get(text_id, "")


def load_nvtx(connection, strings, thread_to_rank, process_to_rank):
    by_rank = defaultdict(lambda: defaultdict(list))
    if not table_exists(connection, "NVTX_EVENTS"):
        return by_rank
    columns = table_columns(connection, "NVTX_EVENTS")
    text_expr = "text" if "text" in columns else "NULL"
    text_id_expr = "textId" if "textId" in columns else "NULL"
    query = f"SELECT start, end, {text_expr}, {text_id_expr}, globalTid FROM NVTX_EVENTS WHERE end IS NOT NULL"
    for start, end, text, text_id, global_tid in connection.execute(query):
        rank = thread_to_rank.get(int(global_tid))
        if rank is None:
            rank = process_to_rank.get(process_id_from_thread(global_tid))
        if rank is None:
            continue
        name = text or strings.get(text_id, "")
        if name == "quest.procedure":
            by_rank[rank]["procedure"].append((start, end))
        elif name == "quest.execution.timed":
            by_rank[rank]["execution"].append((start, end))
        elif name == "quest.communication.pack":
            by_rank[rank]["communication_pack"].append((start, end))
        elif name == "quest.communication.exchange.cpu_staged":
            by_rank[rank]["communication"].append((start, end))
            by_rank[rank]["paths"].append("cpu_staged")
        elif name == "quest.communication.exchange.direct_gpu":
            by_rank[rank]["communication"].append((start, end))
            by_rank[rank]["paths"].append("direct_gpu")
    return by_rank


def load_kernels(connection, strings, process_to_rank):
    by_rank = defaultdict(lambda: defaultdict(list))
    table = "CUPTI_ACTIVITY_KIND_KERNEL"
    if not table_exists(connection, table):
        return by_rank
    columns = table_columns(connection, table)
    name_column = "demangledName" if "demangledName" in columns else "shortName"
    for start, end, global_pid, name_id in connection.execute(
        f"SELECT start, end, globalPid, {name_column} FROM {table}"
    ):
        rank = process_to_rank.get(int(global_pid))
        if rank is None:
            continue
        name = strings.get(name_id, "")
        key = "pack_kernels" if any(token in name for token in PACK_KERNEL_NAMES) else "simulation_kernels"
        by_rank[rank][key].append((start, end))
    return by_rank


def load_mpi_sends(connection, strings, thread_to_rank, process_to_rank):
    sends = defaultdict(lambda: {"calls": 0, "bytes": 0})
    table = "MPI_P2P_EVENTS"
    if not table_exists(connection, table):
        return sends
    columns = table_columns(connection, table)
    text_expr = "text" if "text" in columns else "NULL"
    text_id_expr = "textId" if "textId" in columns else "NULL"
    size_expr = "size" if "size" in columns else "0"
    query = f"SELECT {text_expr}, {text_id_expr}, globalTid, {size_expr} FROM {table}"
    for text, text_id, global_tid, size in connection.execute(query):
        name = text or strings.get(text_id, "")
        if "send" not in name.lower():
            continue
        rank = thread_to_rank.get(int(global_tid))
        if rank is None:
            rank = process_to_rank.get(process_id_from_thread(global_tid))
        if rank is None:
            continue
        sends[rank]["calls"] += 1
        sends[rank]["bytes"] += int(size or 0)
    return sends


def communication_path(paths):
    unique_paths = sorted(set(paths))
    if not unique_paths:
        return "none"
    if len(unique_paths) == 1:
        return unique_paths[0]
    return "mixed"


def seconds(nanoseconds):
    return nanoseconds / 1_000_000_000.0


def percentage(part, total):
    return 0.0 if total == 0 else (100.0 * part / total)


def format_rank_row(point, benchmark, num_qubits, env_num_nodes, rank, classified, mpi, paths):
    total = classified["procedure_ns"]
    return {
        "point": point,
        "benchmark": benchmark,
        "num_qubits": num_qubits,
        "env_num_nodes": env_num_nodes,
        "rank": rank,
        "procedure_wall_time_s": seconds(total),
        "computation_time_s": seconds(classified["computation_ns"]),
        "communication_time_s": seconds(classified["communication_ns"]),
        "others_time_s": seconds(classified["others_ns"]),
        "overlap_time_s": seconds(classified["overlap_ns"]),
        "computation_pct": percentage(classified["computation_ns"], total),
        "communication_pct": percentage(classified["communication_ns"], total),
        "others_pct": percentage(classified["others_ns"], total),
        "mpi_send_calls": mpi["calls"],
        "mpi_send_bytes": mpi["bytes"],
        "communication_path": communication_path(paths),
    }


def parse_profile(sqlite_path, point, benchmark, num_qubits, env_num_nodes):
    connection = sqlite3.connect(str(sqlite_path))
    try:
        strings = string_values(connection)
        thread_to_rank, process_to_rank = load_rank_maps(connection)
        if not thread_to_rank:
            raise ValueError("Nsight SQLite does not contain MPI_RANKS; profile with --trace=mpi")
        nvtx = load_nvtx(connection, strings, thread_to_rank, process_to_rank)
        kernels = load_kernels(connection, strings, process_to_rank)
        sends = load_mpi_sends(connection, strings, thread_to_rank, process_to_rank)

        rank_rows = []
        for rank in sorted(set(thread_to_rank.values())):
            rank_nvtx = nvtx[rank]
            if not rank_nvtx["procedure"]:
                raise ValueError(f"rank {rank} has no quest.procedure NVTX range")
            classified = classify_intervals(
                procedure=rank_nvtx["procedure"],
                execution=rank_nvtx["execution"],
                communication=rank_nvtx["communication"] + rank_nvtx["communication_pack"],
                pack_kernels=kernels[rank]["pack_kernels"],
                simulation_kernels=kernels[rank]["simulation_kernels"],
            )
            rank_rows.append(
                format_rank_row(
                    point,
                    benchmark,
                    num_qubits,
                    env_num_nodes,
                    rank,
                    classified,
                    sends[rank],
                    rank_nvtx["paths"],
                )
            )
    finally:
        connection.close()

    critical = max(rank_rows, key=lambda row: row["procedure_wall_time_s"])
    summary_row = {
        key: value for key, value in critical.items() if key not in {"rank", "mpi_send_calls", "mpi_send_bytes", "communication_path"}
    }
    summary_row["critical_rank"] = critical["rank"]
    summary_row["mpi_send_calls"] = sum(row["mpi_send_calls"] for row in rank_rows)
    summary_row["mpi_send_bytes"] = sum(row["mpi_send_bytes"] for row in rank_rows)
    summary_row["communication_path"] = communication_path(
        [row["communication_path"] for row in rank_rows if row["communication_path"] != "none"]
    )
    return rank_rows, summary_row


def append_tsv(path, fieldnames, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True, type=Path)
    parser.add_argument("--point", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--num-qubits", required=True, type=int)
    parser.add_argument("--env-num-nodes", required=True, type=int)
    parser.add_argument("--rank-output", required=True, type=Path)
    parser.add_argument("--summary-output", required=True, type=Path)
    return parser.parse_args()


def main():
    args = parse_args()
    rank_rows, summary_row = parse_profile(
        sqlite_path=args.sqlite,
        point=args.point,
        benchmark=args.benchmark,
        num_qubits=args.num_qubits,
        env_num_nodes=args.env_num_nodes,
    )
    append_tsv(args.rank_output, RANK_FIELDS, rank_rows)
    append_tsv(args.summary_output, SUMMARY_FIELDS, [summary_row])


if __name__ == "__main__":
    main()
