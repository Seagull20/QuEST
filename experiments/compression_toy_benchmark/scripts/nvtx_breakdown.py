#!/usr/bin/env python3
"""Parse NVTX ranges from Nsight SQLite exports for compression_exchange."""

from __future__ import annotations

import argparse
import csv
import sqlite3
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


TOP_RANGES = {
    "quest_compression.exchange.raw": "raw",
    "quest_compression.exchange.compressed": "compressed",
    "quest_compression.exchange.fallback_raw": "fallback_raw",
}
STAGE_PREFIX = "quest_compression.stage."
REQUIRED_STAGES = {
    "raw": ["exchange_total", "d2h", "mpi", "h2d"],
    "compressed": ["exchange_total", "compress", "size_exchange", "d2h", "mpi", "h2d", "decompress"],
    "fallback_raw": ["exchange_total", "d2h", "mpi", "h2d"],
}
RANGE_FIELDS = [
    "case",
    "rank",
    "exchange_id",
    "path",
    "stage",
    "start_ns",
    "end_ns",
    "duration_s",
    "parent_duration_s",
    "stage_fraction",
    "status",
]
SUMMARY_FIELDS = [
    "case",
    "path",
    "stage",
    "samples",
    "median_s",
    "total_stage_s",
    "median_fraction",
    "status",
]
COMPARISON_FIELDS = [
    "stage",
    "raw_case",
    "compressed_case",
    "raw_stage_median_s",
    "compressed_stage_median_s",
    "raw_exchange_median_s",
    "compressed_exchange_median_s",
    "speedup_raw_vs_compressed",
    "status",
    "missing_required_stages",
]


@dataclass(frozen=True)
class NvtxEvent:
    rank: int
    name: str
    start: int
    end: int

    @property
    def duration_s(self) -> float:
        return (self.end - self.start) / 1e9


@dataclass(frozen=True)
class Exchange:
    case: str
    rank: int
    exchange_id: int
    path: str
    start: int
    end: int
    stages: tuple[NvtxEvent, ...]

    @property
    def duration_s(self) -> float:
        return (self.end - self.start) / 1e9


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    if not table_exists(connection, table_name):
        return set()
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})")}


def string_values(connection: sqlite3.Connection) -> dict[int, str]:
    if not table_exists(connection, "StringIds"):
        return {}
    return {
        int(row[0]): str(row[1])
        for row in connection.execute("SELECT id, value FROM StringIds")
    }


def process_id_from_thread(global_thread_id: int) -> int:
    return int(global_thread_id) & ~0xFFFFFF


def load_rank_maps(connection: sqlite3.Connection) -> tuple[dict[int, int], dict[int, int]]:
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


def rank_for_thread(global_tid, thread_to_rank: dict[int, int], process_to_rank: dict[int, int]) -> int:
    if global_tid is None:
        return 0
    global_tid = int(global_tid)
    rank = thread_to_rank.get(global_tid)
    if rank is not None:
        return rank
    rank = process_to_rank.get(process_id_from_thread(global_tid))
    return 0 if rank is None else rank


def resolve_text(text, text_id, strings: dict[int, str]) -> str:
    if text:
        return str(text)
    if text_id is None:
        return ""
    return strings.get(int(text_id), "")


def load_events(sqlite_path: Path) -> list[NvtxEvent]:
    with sqlite3.connect(sqlite_path) as connection:
        if not table_exists(connection, "NVTX_EVENTS"):
            raise ValueError(f"Nsight SQLite has no NVTX_EVENTS table: {sqlite_path}")
        columns = table_columns(connection, "NVTX_EVENTS")
        text_expr = "text" if "text" in columns else "NULL"
        text_id_expr = "textId" if "textId" in columns else "NULL"
        global_tid_expr = "globalTid" if "globalTid" in columns else "NULL"
        strings = string_values(connection)
        thread_to_rank, process_to_rank = load_rank_maps(connection)
        query = (
            f"SELECT start, end, {text_expr}, {text_id_expr}, {global_tid_expr} "
            "FROM NVTX_EVENTS WHERE end IS NOT NULL"
        )
        events: list[NvtxEvent] = []
        for start, end, text, text_id, global_tid in connection.execute(query):
            name = resolve_text(text, text_id, strings)
            if name in TOP_RANGES or name.startswith(STAGE_PREFIX):
                events.append(NvtxEvent(
                    rank=rank_for_thread(global_tid, thread_to_rank, process_to_rank),
                    name=name,
                    start=int(start),
                    end=int(end),
                ))
        return sorted(events, key=lambda event: (event.rank, event.start, event.end, event.name))


def contained(child: NvtxEvent, parent: NvtxEvent) -> bool:
    return child.rank == parent.rank and child.start >= parent.start and child.end <= parent.end


def stage_name(event: NvtxEvent) -> str:
    return event.name[len(STAGE_PREFIX):]


def build_exchanges(case: str, events: list[NvtxEvent]) -> list[Exchange]:
    stages = [event for event in events if event.name.startswith(STAGE_PREFIX)]
    top_events = [event for event in events if event.name in TOP_RANGES]
    counters: dict[tuple[int, str], int] = defaultdict(int)
    exchanges: list[Exchange] = []
    for top in sorted(top_events, key=lambda event: (event.rank, event.start, event.end)):
        path = TOP_RANGES[top.name]
        counters[(top.rank, path)] += 1
        child_stages = tuple(
            event
            for event in stages
            if contained(event, top)
        )
        exchanges.append(Exchange(
            case=case,
            rank=top.rank,
            exchange_id=counters[(top.rank, path)],
            path=path,
            start=top.start,
            end=top.end,
            stages=child_stages,
        ))
    return exchanges


def per_exchange_stage_samples(exchanges: list[Exchange]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for exchange in exchanges:
        parent_duration = exchange.duration_s
        rows.append({
            "case": exchange.case,
            "path": exchange.path,
            "stage": "exchange_total",
            "duration_s": parent_duration,
            "fraction": 1.0,
            "status": "PASS",
        })
        stage_ns: dict[str, int] = defaultdict(int)
        for stage in exchange.stages:
            stage_ns[stage_name(stage)] += stage.end - stage.start
        for stage, duration_ns in stage_ns.items():
            duration_s = duration_ns / 1e9
            rows.append({
                "case": exchange.case,
                "path": exchange.path,
                "stage": stage,
                "duration_s": duration_s,
                "fraction": duration_s / parent_duration if parent_duration > 0 else 0.0,
                "status": "PASS",
            })
    return rows


def build_range_rows(exchanges: list[Exchange]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for exchange in exchanges:
        parent_duration = exchange.duration_s
        rows.append({
            "case": exchange.case,
            "rank": str(exchange.rank),
            "exchange_id": str(exchange.exchange_id),
            "path": exchange.path,
            "stage": "exchange_total",
            "start_ns": str(exchange.start),
            "end_ns": str(exchange.end),
            "duration_s": format_float(parent_duration),
            "parent_duration_s": format_float(parent_duration),
            "stage_fraction": format_float(1.0),
            "status": "PASS",
        })
        for stage in exchange.stages:
            duration_s = stage.duration_s
            rows.append({
                "case": exchange.case,
                "rank": str(stage.rank),
                "exchange_id": str(exchange.exchange_id),
                "path": exchange.path,
                "stage": stage_name(stage),
                "start_ns": str(stage.start),
                "end_ns": str(stage.end),
                "duration_s": format_float(duration_s),
                "parent_duration_s": format_float(parent_duration),
                "stage_fraction": format_float(duration_s / parent_duration if parent_duration > 0 else 0.0),
                "status": "PASS",
            })
    return rows


def format_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.9f}"


def median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def summarize(stage_samples: list[dict[str, object]], exchanges: list[Exchange]) -> tuple[list[dict[str, str]], str]:
    sample_groups: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for sample in stage_samples:
        sample_groups[(str(sample["case"]), str(sample["path"]), str(sample["stage"]))].append(sample)

    existing_paths = sorted({(exchange.case, exchange.path) for exchange in exchanges})
    summary_rows: list[dict[str, str]] = []
    overall_status = "PASS"
    for case, path in existing_paths:
        stages = REQUIRED_STAGES.get(path, ["exchange_total"])
        for stage in stages:
            samples = sample_groups.get((case, path, stage), [])
            durations = [float(sample["duration_s"]) for sample in samples]
            fractions = [float(sample["fraction"]) for sample in samples]
            stage_status = "PASS" if durations else "MISSING_STAGE"
            if stage_status != "PASS":
                overall_status = "MISSING_STAGE"
            summary_rows.append({
                "case": case,
                "path": path,
                "stage": stage,
                "samples": str(len(durations)),
                "median_s": format_float(median(durations)),
                "total_stage_s": format_float(sum(durations)) if durations else "",
                "median_fraction": format_float(median(fractions)),
                "status": stage_status,
            })
    return summary_rows, overall_status


def pick_case_with_path(summary_rows: list[dict[str, str]], path: str) -> str:
    for row in summary_rows:
        if row["path"] == path:
            return row["case"]
    return ""


def summary_value(summary_rows: list[dict[str, str]], case: str, path: str, stage: str) -> str:
    for row in summary_rows:
        if row["case"] == case and row["path"] == path and row["stage"] == stage:
            return row["median_s"]
    return ""


def build_comparison(summary_rows: list[dict[str, str]], overall_status: str) -> list[dict[str, str]]:
    raw_case = pick_case_with_path(summary_rows, "raw")
    compressed_case = pick_case_with_path(summary_rows, "compressed")
    stages = sorted({
        row["stage"]
        for row in summary_rows
        if row["path"] in {"raw", "compressed"}
    }, key=lambda stage: (stage != "exchange_total", stage))
    if "exchange_total" not in stages:
        stages.insert(0, "exchange_total")

    raw_exchange = summary_value(summary_rows, raw_case, "raw", "exchange_total")
    compressed_exchange = summary_value(summary_rows, compressed_case, "compressed", "exchange_total")
    speedup = ""
    if raw_exchange and compressed_exchange and float(compressed_exchange) > 0:
        speedup = format_float(float(raw_exchange) / float(compressed_exchange))

    missing = [
        f"{row['case']}:{row['path']}:{row['stage']}"
        for row in summary_rows
        if row["status"] != "PASS"
    ]
    comparison_rows: list[dict[str, str]] = []
    for stage in stages:
        comparison_rows.append({
            "stage": stage,
            "raw_case": raw_case,
            "compressed_case": compressed_case,
            "raw_stage_median_s": summary_value(summary_rows, raw_case, "raw", stage) if raw_case else "",
            "compressed_stage_median_s": summary_value(summary_rows, compressed_case, "compressed", stage) if compressed_case else "",
            "raw_exchange_median_s": raw_exchange,
            "compressed_exchange_median_s": compressed_exchange,
            "speedup_raw_vs_compressed": speedup,
            "status": overall_status,
            "missing_required_stages": ",".join(missing),
        })
    return comparison_rows


def write_tsv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_conclusion(path: Path, comparison_rows: list[dict[str, str]], summary_rows: list[dict[str, str]], status: str) -> None:
    missing = [row for row in summary_rows if row["status"] != "PASS"]
    exchange_row = comparison_rows[0] if comparison_rows else {}
    speedup = exchange_row.get("speedup_raw_vs_compressed", "")
    raw_s = exchange_row.get("raw_exchange_median_s", "")
    compressed_s = exchange_row.get("compressed_exchange_median_s", "")
    lines = [
        "# NVTX Breakdown Conclusion",
        "",
        f"- Status: `{status}`",
    ]
    if raw_s and compressed_s:
        lines.append(f"- Median top-level exchange: raw `{raw_s}` s, Bitcomp/compressed `{compressed_s}` s.")
    if speedup:
        lines.append(f"- NVTX top-level exchange speedup: `{speedup}x`.")
    if missing:
        lines.append(f"- Missing required stages: `{len(missing)}`. These are reported explicitly and not treated as zero-duration stages.")
    else:
        lines.append("- All required raw/compressed NVTX stages were observed.")
    lines.append("")
    lines.append("This profile compares the standalone toy benchmark's default host staging path with the Bitcomp compression path; it is not an in-tree QuEST communication patch profile.")
    path.write_text("\n".join(lines) + "\n")


def analyze_cases(cases: dict[str, Path], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    all_exchanges: list[Exchange] = []
    for label, sqlite_path in cases.items():
        events = load_events(Path(sqlite_path))
        all_exchanges.extend(build_exchanges(label, events))

    range_rows = build_range_rows(all_exchanges)
    stage_samples = per_exchange_stage_samples(all_exchanges)
    summary_rows, status = summarize(stage_samples, all_exchanges)
    comparison_rows = build_comparison(summary_rows, status)

    write_tsv(output_dir / "nvtx_range_samples.tsv", RANGE_FIELDS, range_rows)
    write_tsv(output_dir / "nvtx_breakdown_summary.tsv", SUMMARY_FIELDS, summary_rows)
    write_tsv(output_dir / "nvtx_breakdown_comparison.tsv", COMPARISON_FIELDS, comparison_rows)
    write_conclusion(output_dir / "nvtx_breakdown_conclusion.md", comparison_rows, summary_rows, status)
    return {"status": status, "exchange_count": str(len(all_exchanges))}


def parse_case(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--case must have form LABEL=PATH")
    label, path = value.split("=", 1)
    if not label:
        raise argparse.ArgumentTypeError("--case label is empty")
    return label, Path(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse compression toy NVTX breakdowns from Nsight SQLite.")
    parser.add_argument("--case", action="append", type=parse_case, default=[], help="Case mapping, e.g. raw=raw.sqlite")
    parser.add_argument("--sqlite", type=Path, help="Single SQLite input, used with --case-label.")
    parser.add_argument("--case-label", default="profile", help="Label for --sqlite.")
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = dict(args.case)
    if args.sqlite:
        cases[args.case_label] = args.sqlite
    if not cases:
        raise SystemExit("provide at least one --case LABEL=PATH or --sqlite PATH")
    result = analyze_cases(cases, args.output_dir)
    print(f"status={result['status']}")
    print(f"exchange_count={result['exchange_count']}")
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
