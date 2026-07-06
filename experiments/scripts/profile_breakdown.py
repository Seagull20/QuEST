#!/usr/bin/env python3
"""Build whole-procedure and diagnostic breakdowns from Nsight SQLite."""

import argparse
import csv
import math
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path


RANK_FIELDS = [
    "point",
    "benchmark",
    "num_qubits",
    "env_num_nodes",
    "mpi_ranks",
    "slurm_nodes",
    "gpus",
    "rank",
    "procedure_wall_time_s",
    "execution_wall_time_s",
    "computation_time_s",
    "communication_time_s",
    "lifecycle_time_s",
    "execution_overhead_time_s",
    "others_time_s",
    "overlap_time_s",
    "computation_pct",
    "communication_pct",
    "lifecycle_pct",
    "execution_overhead_pct",
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
    "mpi_ranks",
    "slurm_nodes",
    "gpus",
    "critical_rank",
    "procedure_wall_time_s",
    "execution_wall_time_s",
    "computation_time_s",
    "communication_time_s",
    "lifecycle_time_s",
    "execution_overhead_time_s",
    "others_time_s",
    "overlap_time_s",
    "computation_pct",
    "communication_pct",
    "lifecycle_pct",
    "execution_overhead_pct",
    "others_pct",
    "critical_rank_mpi_send_calls",
    "critical_rank_mpi_send_bytes",
    "aggregate_mpi_send_calls",
    "aggregate_mpi_send_bytes",
    "communication_path",
    "rank_wall_min_s",
    "rank_wall_max_s",
    "rank_imbalance_s",
    "rank_imbalance_pct",
]

COMMUNICATION_FIELDS = [
    "point",
    "benchmark",
    "num_qubits",
    "mpi_ranks",
    "rank",
    "exchange_id",
    "communication_path",
    "peer_rank",
    "send_bytes",
    "recv_bytes",
    "mpi_send_calls",
    "mpi_recv_calls",
    "mpi_wait_calls",
    "pack_time_s",
    "d2h_time_s",
    "compress_time_s",
    "size_exchange_time_s",
    "mpi_time_s",
    "mpi_wait_time_s",
    "h2d_time_s",
    "decompress_time_s",
    "exchange_wall_time_s",
    "effective_bandwidth_gbps",
]

COMPUTATION_FIELDS = [
    "point",
    "benchmark",
    "num_qubits",
    "rank",
    "kernel_class",
    "calls",
    "gpu_active_time_s",
    "computation_pct",
]

LIFECYCLE_FIELDS = [
    "point",
    "benchmark",
    "num_qubits",
    "rank",
    "stage",
    "calls",
    "wall_time_s",
]

RUNTIME_FIELDS = [
    "point",
    "benchmark",
    "num_qubits",
    "rank",
    "api",
    "calls",
    "total_time_s",
    "median_time_s",
    "p95_time_s",
]

PACK_KERNEL_NAMES = (
    "kernel_statevec_packAmpsIntoBuffer",
    "kernel_statevec_packPairSummedAmpsIntoBuffer",
)

LIFECYCLE_PREFIX = "quest.lifecycle."
RUNTIME_APIS = {
    "cudaMalloc",
    "cudaFree",
    "cudaMemcpy",
    "cudaMemcpyAsync",
    "cudaStreamSynchronize",
    "cudaDeviceSynchronize",
    "cudaLaunchKernel",
}


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


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return 0
    index = max(0, math.ceil(fraction * len(values)) - 1)
    return values[index]


def classify_intervals(procedure, execution, communication, pack_kernels, simulation_kernels):
    procedure = merge_intervals(procedure)
    execution = intersect_intervals(execution, procedure)
    communication = intersect_intervals(
        merge_intervals(list(communication) + list(pack_kernels)), execution
    )
    computation_candidates = intersect_intervals(simulation_kernels, execution)
    overlap = intersect_intervals(computation_candidates, communication)
    computation = subtract_intervals(computation_candidates, communication)
    execution_overhead = subtract_intervals(execution, communication + computation)
    lifecycle = subtract_intervals(procedure, execution)

    procedure_ns = interval_duration(procedure)
    execution_ns = interval_duration(execution)
    communication_ns = interval_duration(communication)
    computation_ns = interval_duration(computation)
    lifecycle_ns = interval_duration(lifecycle)
    execution_overhead_ns = interval_duration(execution_overhead)
    overlap_ns = interval_duration(overlap)

    return {
        "procedure_ns": procedure_ns,
        "execution_ns": execution_ns,
        "computation_ns": computation_ns,
        "communication_ns": communication_ns,
        "lifecycle_ns": lifecycle_ns,
        "execution_overhead_ns": execution_overhead_ns,
        "others_ns": lifecycle_ns + execution_overhead_ns,
        "overlap_ns": overlap_ns,
        "procedure_intervals": procedure,
        "execution_intervals": execution,
        "communication_intervals": communication,
        "computation_intervals": computation,
        "lifecycle_intervals": lifecycle,
        "execution_overhead_intervals": execution_overhead,
    }


def table_exists(connection, table_name):
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone() is not None


def table_columns(connection, table_name):
    if not table_exists(connection, table_name):
        return set()
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


def rank_for_thread(global_tid, thread_to_rank, process_to_rank):
    if global_tid is None:
        return None
    global_tid = int(global_tid)
    return thread_to_rank.get(global_tid, process_to_rank.get(process_id_from_thread(global_tid)))


def resolve_text(text, text_id, strings):
    return str(text) if text else strings.get(text_id, "")


def load_nvtx(connection, strings, thread_to_rank, process_to_rank):
    by_rank = defaultdict(lambda: defaultdict(list))
    if not table_exists(connection, "NVTX_EVENTS"):
        return by_rank
    columns = table_columns(connection, "NVTX_EVENTS")
    text_expr = "text" if "text" in columns else "NULL"
    text_id_expr = "textId" if "textId" in columns else "NULL"
    query = f"SELECT start, end, {text_expr}, {text_id_expr}, globalTid FROM NVTX_EVENTS WHERE end IS NOT NULL"
    for start, end, text, text_id, global_tid in connection.execute(query):
        rank = rank_for_thread(global_tid, thread_to_rank, process_to_rank)
        if rank is None:
            continue
        name = resolve_text(text, text_id, strings)
        interval = (int(start), int(end))
        if name == "quest.procedure":
            by_rank[rank]["procedure"].append(interval)
        elif name == "quest.execution.timed":
            by_rank[rank]["execution"].append(interval)
        elif name == "quest.communication.pack":
            by_rank[rank]["pack"].append(interval)
        elif name == "quest.communication.exchange.cpu_staged":
            by_rank[rank]["exchanges"].append({"start": int(start), "end": int(end), "path": "cpu_staged"})
        elif name == "quest.communication.exchange.direct_gpu":
            by_rank[rank]["exchanges"].append({"start": int(start), "end": int(end), "path": "direct_gpu"})
        elif name == "quest.communication.d2h":
            by_rank[rank]["d2h"].append(interval)
        elif name == "quest.communication.compress":
            by_rank[rank]["compress"].append(interval)
        elif name == "quest.communication.size_exchange":
            by_rank[rank]["size_exchange"].append(interval)
        elif name == "quest.communication.mpi":
            by_rank[rank]["mpi"].append(interval)
        elif name == "quest.communication.h2d":
            by_rank[rank]["h2d"].append(interval)
        elif name == "quest.communication.decompress":
            by_rank[rank]["decompress"].append(interval)
        elif name.startswith(LIFECYCLE_PREFIX):
            by_rank[rank]["lifecycle"].append(
                {"stage": name[len(LIFECYCLE_PREFIX):], "start": int(start), "end": int(end)}
            )
    return by_rank


def select_optional(columns, name):
    return name if name in columns else "NULL"


def load_runtime(connection, strings, thread_to_rank, process_to_rank):
    by_rank = defaultdict(list)
    table = "CUPTI_ACTIVITY_KIND_RUNTIME"
    if not table_exists(connection, table):
        return by_rank
    columns = table_columns(connection, table)
    correlation_expr = select_optional(columns, "correlationId")
    query = f"SELECT start, end, globalTid, {correlation_expr}, nameId FROM {table}"
    for start, end, global_tid, correlation_id, name_id in connection.execute(query):
        rank = rank_for_thread(global_tid, thread_to_rank, process_to_rank)
        if rank is None:
            continue
        by_rank[rank].append(
            {
                "start": int(start),
                "end": int(end),
                "correlation_id": correlation_id,
                "name": strings.get(name_id, ""),
            }
        )
    return by_rank


def load_kernels(connection, strings, process_to_rank):
    by_rank = defaultdict(list)
    table = "CUPTI_ACTIVITY_KIND_KERNEL"
    if not table_exists(connection, table):
        return by_rank
    columns = table_columns(connection, table)
    name_column = "demangledName" if "demangledName" in columns else "shortName"
    correlation_expr = select_optional(columns, "correlationId")
    query = f"SELECT start, end, globalPid, {correlation_expr}, {name_column} FROM {table}"
    for start, end, global_pid, correlation_id, name_id in connection.execute(query):
        rank = process_to_rank.get(int(global_pid))
        if rank is None:
            continue
        name = strings.get(name_id, "")
        by_rank[rank].append(
            {
                "start": int(start),
                "end": int(end),
                "correlation_id": correlation_id,
                "name": name,
                "is_pack": any(token in name for token in PACK_KERNEL_NAMES),
            }
        )
    return by_rank


def load_memcopies(connection, process_to_rank):
    by_rank = defaultdict(list)
    table = "CUPTI_ACTIVITY_KIND_MEMCPY"
    if not table_exists(connection, table):
        return by_rank
    columns = table_columns(connection, table)
    correlation_expr = select_optional(columns, "correlationId")
    kind_labels = {}
    if table_exists(connection, "ENUM_CUDA_MEMCPY_OPER"):
        enum_columns = table_columns(connection, "ENUM_CUDA_MEMCPY_OPER")
        label_column = "label" if "label" in enum_columns else "name"
        kind_labels = dict(connection.execute(f"SELECT id, {label_column} FROM ENUM_CUDA_MEMCPY_OPER"))
    query = f"SELECT start, end, globalPid, bytes, copyKind, {correlation_expr} FROM {table}"
    for start, end, global_pid, num_bytes, copy_kind, correlation_id in connection.execute(query):
        rank = process_to_rank.get(int(global_pid))
        if rank is None:
            continue
        by_rank[rank].append(
            {
                "start": int(start),
                "end": int(end),
                "bytes": int(num_bytes or 0),
                "kind": kind_labels.get(copy_kind, str(copy_kind)),
                "correlation_id": correlation_id,
            }
        )
    return by_rank


def load_mpi_events(connection, table, strings, thread_to_rank, process_to_rank):
    by_rank = defaultdict(list)
    if not table_exists(connection, table):
        return by_rank
    columns = table_columns(connection, table)
    text_expr = select_optional(columns, "text")
    text_id_expr = select_optional(columns, "textId")
    size_expr = select_optional(columns, "size")
    remote_expr = select_optional(columns, "remoteRank")
    tag_expr = select_optional(columns, "tag")
    query = (
        f"SELECT start, end, globalTid, {text_expr}, {text_id_expr}, "
        f"{size_expr}, {remote_expr}, {tag_expr} FROM {table}"
    )
    for start, end, global_tid, text, text_id, size, remote_rank, tag in connection.execute(query):
        rank = rank_for_thread(global_tid, thread_to_rank, process_to_rank)
        if rank is None:
            continue
        by_rank[rank].append(
            {
                "start": int(start),
                "end": int(end if end is not None else start),
                "global_tid": int(global_tid),
                "name": resolve_text(text, text_id, strings),
                "size": int(size or 0),
                "remote_rank": remote_rank,
                "tag": tag,
            }
        )
    return by_rank


def is_sendrecv(event):
    return "sendrecv" in event["name"].lower()


def dedupe_sendrecv_events(events):
    deduped = []
    sendrecv_groups = defaultdict(list)
    for event in events:
        if not is_sendrecv(event):
            deduped.append(event)
            continue
        key = (event["global_tid"], event["start"], event["tag"], event["remote_rank"])
        sendrecv_groups[key].append(event)

    for group in sendrecv_groups.values():
        representative = min(group, key=lambda event: (event["size"], event["end"]))
        event = dict(representative)
        event["size"] = min(item["size"] for item in group)
        event["end"] = max(item["end"] for item in group)
        deduped.append(event)

    return sorted(deduped, key=lambda event: (event["start"], event["end"], event["name"], event["size"]))


def interval_contains(outer, event):
    return outer[0] <= event["start"] < outer[1]


def child_intervals(entries, exchange):
    return [interval for interval in entries if exchange["start"] <= interval[0] and interval[1] <= exchange["end"]]


def correlated_activity_intervals(stage_intervals, runtime_events, activities, api_prefix, kind_token=None):
    correlations = {
        event["correlation_id"]
        for interval in stage_intervals
        for event in runtime_events
        if interval_contains(interval, event)
        and event["name"].startswith(api_prefix)
        and event["correlation_id"] is not None
    }
    matched = [
        (activity["start"], activity["end"])
        for activity in activities
        if activity["correlation_id"] in correlations
        and (kind_token is None or kind_token.lower() in activity.get("kind", "").lower())
    ]
    if matched:
        return matched
    return [
        (activity["start"], activity["end"])
        for interval in stage_intervals
        for activity in activities
        if interval_contains(interval, activity)
        and (kind_token is None or kind_token.lower() in activity.get("kind", "").lower())
    ]


def pack_kernel_intervals(pack_interval, runtime_events, kernels):
    correlations = {
        event["correlation_id"]
        for event in runtime_events
        if interval_contains(pack_interval, event)
        and event["name"].startswith("cudaLaunchKernel")
        and event["correlation_id"] is not None
    }
    matched = [
        (kernel["start"], kernel["end"])
        for kernel in kernels
        if kernel["is_pack"] and kernel["correlation_id"] in correlations
    ]
    if matched:
        return matched
    return [
        (kernel["start"], kernel["end"])
        for kernel in kernels
        if kernel["is_pack"] and pack_interval[0] <= kernel["start"]
    ][:1]


def associate_pack_ranges(pack_ranges, exchanges):
    associations = {}
    unused = set(range(len(pack_ranges)))
    previous_end = -1
    for exchange_index, exchange in enumerate(sorted(exchanges, key=lambda item: item["start"])):
        candidates = [
            index
            for index in unused
            if previous_end <= pack_ranges[index][0]
            and pack_ranges[index][1] <= exchange["start"]
        ]
        if candidates:
            selected = max(candidates, key=lambda index: pack_ranges[index][1])
            associations[exchange_index] = pack_ranges[selected]
            unused.remove(selected)
        previous_end = exchange["end"]
    return associations


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


def canonical_runtime_api(name):
    canonical = re.sub(r"_v\d+$", "", name)
    return canonical if canonical in RUNTIME_APIS else None


def kernel_class(name, benchmark):
    if benchmark == "qft":
        if "DiagMatr" in name:
            return "phase"
        if "OneTargDenseMatr" in name:
            return "hadamard"
        if "Swap" in name:
            return "swap"
    return "other"


def format_rank_row(point, benchmark, num_qubits, mpi_ranks, slurm_nodes, gpus, rank, classified, mpi, paths):
    total = classified["procedure_ns"]
    return {
        "point": point,
        "benchmark": benchmark,
        "num_qubits": num_qubits,
        "env_num_nodes": mpi_ranks,
        "mpi_ranks": mpi_ranks,
        "slurm_nodes": slurm_nodes,
        "gpus": gpus,
        "rank": rank,
        "procedure_wall_time_s": seconds(total),
        "execution_wall_time_s": seconds(classified["execution_ns"]),
        "computation_time_s": seconds(classified["computation_ns"]),
        "communication_time_s": seconds(classified["communication_ns"]),
        "lifecycle_time_s": seconds(classified["lifecycle_ns"]),
        "execution_overhead_time_s": seconds(classified["execution_overhead_ns"]),
        "others_time_s": seconds(classified["others_ns"]),
        "overlap_time_s": seconds(classified["overlap_ns"]),
        "computation_pct": percentage(classified["computation_ns"], total),
        "communication_pct": percentage(classified["communication_ns"], total),
        "lifecycle_pct": percentage(classified["lifecycle_ns"], total),
        "execution_overhead_pct": percentage(classified["execution_overhead_ns"], total),
        "others_pct": percentage(classified["others_ns"], total),
        "mpi_send_calls": mpi["calls"],
        "mpi_send_bytes": mpi["bytes"],
        "communication_path": communication_path(paths),
    }


def build_communication_rows(point, benchmark, num_qubits, mpi_ranks, rank, nvtx, runtime, kernels, memcopies, p2p, waits):
    rows = []
    exchanges = sorted(nvtx["exchanges"], key=lambda item: item["start"])
    pack_associations = associate_pack_ranges(sorted(nvtx["pack"]), exchanges)
    for exchange_id, exchange in enumerate(exchanges):
        exchange_interval = (exchange["start"], exchange["end"])
        d2h_ranges = child_intervals(nvtx["d2h"], exchange)
        compress_ranges = child_intervals(nvtx["compress"], exchange)
        size_exchange_ranges = child_intervals(nvtx["size_exchange"], exchange)
        mpi_ranges = child_intervals(nvtx["mpi"], exchange)
        h2d_ranges = child_intervals(nvtx["h2d"], exchange)
        decompress_ranges = child_intervals(nvtx["decompress"], exchange)
        exchange_p2p = [event for event in p2p if interval_contains(exchange_interval, event)]
        exchange_waits = [event for event in waits if interval_contains(exchange_interval, event)]
        sends = [event for event in exchange_p2p if "send" in event["name"].lower()]
        recvs = [event for event in exchange_p2p if "recv" in event["name"].lower()]
        peers = sorted({int(event["remote_rank"]) for event in exchange_p2p if event["remote_rank"] is not None})
        pack_interval = pack_associations.get(exchange_id)
        pack_active = pack_kernel_intervals(pack_interval, runtime, kernels) if pack_interval else []
        d2h_active = correlated_activity_intervals(d2h_ranges, runtime, memcopies, "cudaMemcpy", "Device-to-Host")
        h2d_active = correlated_activity_intervals(h2d_ranges, runtime, memcopies, "cudaMemcpy", "Host-to-Device")
        send_bytes = sum(event["size"] for event in sends)
        exchange_ns = exchange["end"] - exchange["start"]
        rows.append(
            {
                "point": point,
                "benchmark": benchmark,
                "num_qubits": num_qubits,
                "mpi_ranks": mpi_ranks,
                "rank": rank,
                "exchange_id": exchange_id,
                "communication_path": exchange["path"],
                "peer_rank": peers[0] if len(peers) == 1 else (",".join(str(peer) for peer in peers) if peers else "unknown"),
                "send_bytes": send_bytes,
                "recv_bytes": sum(event["size"] for event in recvs),
                "mpi_send_calls": len(sends),
                "mpi_recv_calls": len(recvs),
                "mpi_wait_calls": len(exchange_waits),
                "pack_time_s": seconds(interval_duration(pack_active)),
                "d2h_time_s": seconds(interval_duration(d2h_active)),
                "compress_time_s": seconds(interval_duration(compress_ranges)),
                "size_exchange_time_s": seconds(interval_duration(size_exchange_ranges)),
                "mpi_time_s": seconds(interval_duration(mpi_ranges)),
                "mpi_wait_time_s": seconds(interval_duration((event["start"], event["end"]) for event in exchange_waits)),
                "h2d_time_s": seconds(interval_duration(h2d_active)),
                "decompress_time_s": seconds(interval_duration(decompress_ranges)),
                "exchange_wall_time_s": seconds(exchange_ns),
                "effective_bandwidth_gbps": 0.0 if exchange_ns <= 0 else (8.0 * send_bytes) / exchange_ns,
            }
        )
    return rows


def build_computation_rows(point, benchmark, num_qubits, rank, kernels, classified):
    grouped = defaultdict(lambda: {"calls": 0, "intervals": []})
    for kernel in kernels:
        if kernel["is_pack"]:
            continue
        active = subtract_intervals(
            intersect_intervals([(kernel["start"], kernel["end"])], classified["execution_intervals"]),
            classified["communication_intervals"],
        )
        if not active:
            continue
        category = kernel_class(kernel["name"], benchmark)
        grouped[category]["calls"] += 1
        grouped[category]["intervals"].extend(active)
    total = classified["computation_ns"]
    return [
        {
            "point": point,
            "benchmark": benchmark,
            "num_qubits": num_qubits,
            "rank": rank,
            "kernel_class": category,
            "calls": values["calls"],
            "gpu_active_time_s": seconds(interval_duration(values["intervals"])),
            "computation_pct": percentage(interval_duration(values["intervals"]), total),
        }
        for category, values in sorted(grouped.items())
    ]


def build_lifecycle_rows(point, benchmark, num_qubits, rank, lifecycle):
    grouped = defaultdict(list)
    for event in lifecycle:
        grouped[event["stage"]].append((event["start"], event["end"]))
    return [
        {
            "point": point,
            "benchmark": benchmark,
            "num_qubits": num_qubits,
            "rank": rank,
            "stage": stage,
            "calls": len(intervals),
            "wall_time_s": seconds(interval_duration(intervals)),
        }
        for stage, intervals in sorted(grouped.items())
    ]


def build_runtime_rows(point, benchmark, num_qubits, rank, runtime):
    grouped = defaultdict(list)
    for event in runtime:
        api = canonical_runtime_api(event["name"])
        if api:
            grouped[api].append(event["end"] - event["start"])
    return [
        {
            "point": point,
            "benchmark": benchmark,
            "num_qubits": num_qubits,
            "rank": rank,
            "api": api,
            "calls": len(durations),
            "total_time_s": seconds(sum(durations)),
            "median_time_s": seconds(percentile(durations, 0.5)),
            "p95_time_s": seconds(percentile(durations, 0.95)),
        }
        for api, durations in sorted(grouped.items())
    ]


def parse_profile(sqlite_path, point, benchmark, num_qubits, mpi_ranks, slurm_nodes, gpus):
    connection = sqlite3.connect(str(sqlite_path))
    try:
        strings = string_values(connection)
        thread_to_rank, process_to_rank = load_rank_maps(connection)
        if not thread_to_rank:
            raise ValueError("Nsight SQLite does not contain MPI_RANKS; profile with --trace=mpi")
        nvtx = load_nvtx(connection, strings, thread_to_rank, process_to_rank)
        runtime = load_runtime(connection, strings, thread_to_rank, process_to_rank)
        kernels = load_kernels(connection, strings, process_to_rank)
        memcopies = load_memcopies(connection, process_to_rank)
        p2p = load_mpi_events(connection, "MPI_P2P_EVENTS", strings, thread_to_rank, process_to_rank)
        p2p = defaultdict(list, {rank: dedupe_sendrecv_events(events) for rank, events in p2p.items()})
        waits = load_mpi_events(connection, "MPI_START_WAIT_EVENTS", strings, thread_to_rank, process_to_rank)

        rank_rows = []
        communication_rows = []
        computation_rows = []
        lifecycle_rows = []
        runtime_rows = []
        mpi_sends = defaultdict(lambda: {"calls": 0, "bytes": 0})
        for rank, events in p2p.items():
            for event in events:
                if "send" in event["name"].lower():
                    mpi_sends[rank]["calls"] += 1
                    mpi_sends[rank]["bytes"] += event["size"]

        for rank in sorted(set(thread_to_rank.values())):
            rank_nvtx = nvtx[rank]
            if not rank_nvtx["procedure"]:
                raise ValueError(f"rank {rank} has no quest.procedure NVTX range")
            if not rank_nvtx["lifecycle"]:
                print(
                    f"WARNING: rank {rank} has no lifecycle NVTX markers; using procedure minus execution residual",
                    file=sys.stderr,
                )
            pack_intervals = [
                (kernel["start"], kernel["end"])
                for kernel in kernels[rank]
                if kernel["is_pack"]
            ]
            simulation_intervals = [
                (kernel["start"], kernel["end"])
                for kernel in kernels[rank]
                if not kernel["is_pack"]
            ]
            exchange_intervals = [
                (exchange["start"], exchange["end"])
                for exchange in rank_nvtx["exchanges"]
            ]
            classified = classify_intervals(
                procedure=rank_nvtx["procedure"],
                execution=rank_nvtx["execution"],
                communication=exchange_intervals,
                pack_kernels=pack_intervals,
                simulation_kernels=simulation_intervals,
            )
            rank_rows.append(
                format_rank_row(
                    point,
                    benchmark,
                    num_qubits,
                    mpi_ranks,
                    slurm_nodes,
                    gpus,
                    rank,
                    classified,
                    mpi_sends[rank],
                    [exchange["path"] for exchange in rank_nvtx["exchanges"]],
                )
            )
            communication_rows.extend(
                build_communication_rows(
                    point,
                    benchmark,
                    num_qubits,
                    mpi_ranks,
                    rank,
                    rank_nvtx,
                    runtime[rank],
                    kernels[rank],
                    memcopies[rank],
                    p2p[rank],
                    waits[rank],
                )
            )
            computation_rows.extend(
                build_computation_rows(point, benchmark, num_qubits, rank, kernels[rank], classified)
            )
            lifecycle_rows.extend(
                build_lifecycle_rows(point, benchmark, num_qubits, rank, rank_nvtx["lifecycle"])
            )
            runtime_rows.extend(
                build_runtime_rows(point, benchmark, num_qubits, rank, runtime[rank])
            )
    finally:
        connection.close()

    critical = max(rank_rows, key=lambda row: row["procedure_wall_time_s"])
    rank_walls = [row["procedure_wall_time_s"] for row in rank_rows]
    rank_wall_min = min(rank_walls)
    rank_wall_max = max(rank_walls)
    summary_row = {
        key: value
        for key, value in critical.items()
        if key not in {"rank", "mpi_send_calls", "mpi_send_bytes"}
    }
    summary_row["critical_rank"] = critical["rank"]
    summary_row["critical_rank_mpi_send_calls"] = critical["mpi_send_calls"]
    summary_row["critical_rank_mpi_send_bytes"] = critical["mpi_send_bytes"]
    summary_row["aggregate_mpi_send_calls"] = sum(row["mpi_send_calls"] for row in rank_rows)
    summary_row["aggregate_mpi_send_bytes"] = sum(row["mpi_send_bytes"] for row in rank_rows)
    summary_row["communication_path"] = communication_path(
        [row["communication_path"] for row in rank_rows if row["communication_path"] != "none"]
    )
    summary_row["rank_wall_min_s"] = rank_wall_min
    summary_row["rank_wall_max_s"] = rank_wall_max
    summary_row["rank_imbalance_s"] = rank_wall_max - rank_wall_min
    summary_row["rank_imbalance_pct"] = percentage(rank_wall_max - rank_wall_min, rank_wall_max)
    return {
        "rank_rows": rank_rows,
        "summary_row": summary_row,
        "communication_rows": communication_rows,
        "computation_rows": computation_rows,
        "lifecycle_rows": lifecycle_rows,
        "runtime_rows": runtime_rows,
    }


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
    parser.add_argument("--mpi-ranks", "--env-num-nodes", dest="mpi_ranks", required=True, type=int)
    parser.add_argument("--slurm-nodes", default=1, type=int)
    parser.add_argument("--gpus", type=int)
    parser.add_argument("--rank-output", required=True, type=Path)
    parser.add_argument("--summary-output", required=True, type=Path)
    parser.add_argument("--communication-output", required=True, type=Path)
    parser.add_argument("--computation-output", required=True, type=Path)
    parser.add_argument("--lifecycle-output", required=True, type=Path)
    parser.add_argument("--runtime-output", required=True, type=Path)
    return parser.parse_args()


def main():
    args = parse_args()
    result = parse_profile(
        sqlite_path=args.sqlite,
        point=args.point,
        benchmark=args.benchmark,
        num_qubits=args.num_qubits,
        mpi_ranks=args.mpi_ranks,
        slurm_nodes=args.slurm_nodes,
        gpus=args.gpus if args.gpus is not None else args.mpi_ranks,
    )
    append_tsv(args.rank_output, RANK_FIELDS, result["rank_rows"])
    append_tsv(args.summary_output, SUMMARY_FIELDS, [result["summary_row"]])
    append_tsv(args.communication_output, COMMUNICATION_FIELDS, result["communication_rows"])
    append_tsv(args.computation_output, COMPUTATION_FIELDS, result["computation_rows"])
    append_tsv(args.lifecycle_output, LIFECYCLE_FIELDS, result["lifecycle_rows"])
    append_tsv(args.runtime_output, RUNTIME_FIELDS, result["runtime_rows"])


if __name__ == "__main__":
    main()
