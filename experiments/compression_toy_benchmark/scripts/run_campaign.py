#!/usr/bin/env python3
"""Run the QuEST-mimicking compression toy benchmark campaign."""

from __future__ import annotations

import argparse
import csv
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


BENCH_ROOT = Path(__file__).resolve().parents[1]
QUEST_ROOT = BENCH_ROOT.parents[1]

GENUINE_PATTERNS = [
    "quest_h_plus_pre_exchange",
    "quest_h_halfzero_pre_exchange",
    "quest_qft",
    "quest_random",
]

SYNTHETIC_PATTERNS = [
    "zero_sparse",
    "h_halfzero_real",
    "phase_lattice",
    "random_mantissa_normed",
]

CODECS = ["raw", "nvcomp_lz4", "nvcomp_gdeflate", "nvcomp_bitcomp"]


@dataclass(frozen=True)
class Case:
    source_kind: str
    pattern: str
    checkpoint: str
    exchange_shape: str
    payload_amps: int
    payload_template: str = ""


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def run_name() -> str:
    return "compression_toy_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def default_raw_root(name: str) -> Path:
    return QUEST_ROOT / "experiments" / "results" / "raw" / name


def default_processed_root(name: str) -> Path:
    return QUEST_ROOT / "experiments" / "results" / "processed" / name


def executable(path: Path, label: str) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"{label} executable not found: {path}")
    return path


def capture_exe_default() -> Path:
    return BENCH_ROOT / "build" / "capture" / "capture_quest_payloads"


def exchange_exe_default() -> Path:
    return BENCH_ROOT / "build" / "exchange" / "compression_exchange"


def mpi_prefix(args: argparse.Namespace, ranks: int) -> list[str]:
    return [args.mpi_launcher, *args.mpi_launcher_arg, args.mpi_np_flag, str(ranks)]


def command_label(parts: list[str]) -> str:
    return shlex.join(parts)


def run_command(
    command: list[str],
    log_path: Path,
    dry_run: bool,
    manifest_rows: list[dict[str, str]],
    case_id: str,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_rows.append({
        "case_id": case_id,
        "log_path": str(log_path),
        "command": command_label(command),
        "status": "DRY_RUN" if dry_run else "PENDING",
    })
    if dry_run:
        print(command_label(command))
        return

    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log_path.write_text(completed.stdout)
    manifest_rows[-1]["status"] = "PASS" if completed.returncode == 0 else f"FAIL_{completed.returncode}"
    if completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}); see {log_path}")


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    fields = ["case_id", "log_path", "command", "status"]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_payload_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def rank_template(row: dict[str, str]) -> str:
    rank = row["rank"]
    path = row["path"]
    token = f"rank_{rank}_"
    if token not in path:
        raise ValueError(f"cannot derive rank template from payload path: {path}")
    return path.replace(token, "rank_{rank}_", 1)


def build_genuine_cases(
    manifest: list[dict[str, str]],
    patterns: list[str],
    shapes: list[str],
) -> list[Case]:
    cases: list[Case] = []
    seen: set[tuple[str, str, int]] = set()
    for row in manifest:
        if row["rank"] != "0":
            continue
        if row["pattern"] not in patterns or row["exchange_shape"] not in shapes:
            continue
        payload_amps = int(row["payload_amps"])
        key = (row["pattern"], row["exchange_shape"], payload_amps)
        if key in seen:
            continue
        seen.add(key)
        cases.append(Case(
            source_kind="genuine",
            pattern=row["pattern"],
            checkpoint=row["checkpoint"],
            exchange_shape=row["exchange_shape"],
            payload_amps=payload_amps,
            payload_template=rank_template(row),
        ))
    return cases


def build_synthetic_cases(patterns: list[str], shapes: list[str], payload_amps_values: list[int]) -> list[Case]:
    cases: list[Case] = []
    for pattern in patterns:
        for shape in shapes:
            for payload_amps in payload_amps_values:
                cases.append(Case(
                    source_kind="synthetic",
                    pattern=pattern,
                    checkpoint="synthetic",
                    exchange_shape=shape,
                    payload_amps=payload_amps,
                ))
    return cases


def case_id(case: Case, codec: str, allocation_id: str) -> str:
    return "_".join([
        allocation_id,
        case.source_kind,
        case.pattern,
        case.exchange_shape,
        f"a{case.payload_amps}",
        codec,
    ])


def run_exchange_case(
    args: argparse.Namespace,
    case: Case,
    codec: str,
    allocation_id: str,
    raw_dir: Path,
    tsv_dir: Path,
    log_dir: Path,
    manifest_rows: list[dict[str, str]],
) -> None:
    cid = case_id(case, codec, allocation_id)
    output_template = tsv_dir / f"{cid}_rank_{{rank}}.tsv"
    command = [
        *mpi_prefix(args, args.ranks),
        str(args.exchange_exe),
        "--source-kind", case.source_kind,
        "--pattern", case.pattern,
        "--checkpoint", case.checkpoint,
        "--exchange-shape", case.exchange_shape,
        "--codec", codec,
        "--payload-amps", str(case.payload_amps),
        "--chunk-amps", str(args.chunk_amps),
        "--nvcomp-chunk-bytes", str(args.nvcomp_chunk_bytes),
        "--warmup", str(args.warmup),
        "--reps", str(args.reps),
        "--allocation-id", allocation_id,
        "--output", str(output_template),
    ]
    if case.source_kind == "genuine":
        command.extend(["--payload-template", case.payload_template])
    run_command(command, log_dir / f"{cid}.log", args.dry_run, manifest_rows, cid)


def combine_rank_tsvs(tsv_dir: Path, output_path: Path) -> int:
    files = sorted(tsv_dir.glob("*_rank_*.tsv"))
    header: str | None = None
    rows = 0
    with output_path.open("w") as out:
        for path in files:
            with path.open() as f:
                local_header = f.readline()
                if not local_header:
                    continue
                if header is None:
                    header = local_header
                    out.write(header)
                elif local_header != header:
                    raise ValueError(f"header mismatch in {path}")
                for line in f:
                    if not line.strip():
                        continue
                    out.write(line)
                    rows += 1
    return rows


def run_analysis(args: argparse.Namespace, samples: Path, processed_dir: Path) -> None:
    command = [
        sys.executable,
        str(BENCH_ROOT / "scripts" / "analyze_campaign.py"),
        "--samples", str(samples),
        "--output-dir", str(processed_dir),
    ]
    subprocess.run(command, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run QuEST compression toy benchmark campaign.")
    parser.add_argument("--mode", choices=["smoke", "main"], default="smoke")
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--processed-dir", type=Path)
    parser.add_argument("--ranks", type=int)
    parser.add_argument("--qubits", type=int)
    parser.add_argument("--payload-amps", type=int)
    parser.add_argument("--chunk-amps", type=int, default=0)
    parser.add_argument("--nvcomp-chunk-bytes", type=int, default=1 << 20)
    parser.add_argument("--warmup", type=int)
    parser.add_argument("--reps", type=int)
    parser.add_argument("--depth", type=int, default=0)
    parser.add_argument("--seed", type=int, default=20260402)
    parser.add_argument("--two-qubit-ratio", type=float, default=0.5)
    parser.add_argument("--allocations", type=int, default=1)
    parser.add_argument("--allocation-id", default=os.environ.get("SLURM_JOB_ID", "manual"))
    parser.add_argument("--genuine-patterns", default="")
    parser.add_argument("--synthetic-patterns", default="")
    parser.add_argument("--exchange-shapes", default="")
    parser.add_argument("--codecs", default=",".join(CODECS))
    parser.add_argument("--capture-exe", type=Path, default=capture_exe_default())
    parser.add_argument("--exchange-exe", type=Path, default=exchange_exe_default())
    parser.add_argument("--skip-capture", action="store_true")
    parser.add_argument("--skip-analysis", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mpi-launcher", default="mpirun")
    parser.add_argument("--mpi-np-flag", default="-np")
    parser.add_argument("--mpi-launcher-arg", action="append", default=[])
    return parser.parse_args()


def configure_defaults(args: argparse.Namespace) -> None:
    name = run_name()
    args.raw_dir = args.raw_dir or default_raw_root(name)
    args.processed_dir = args.processed_dir or default_processed_root(args.raw_dir.name)
    if args.ranks is None:
        args.ranks = 2 if args.mode == "smoke" else 4
    if args.qubits is None:
        args.qubits = 20 if args.mode == "smoke" else 28
    if args.payload_amps is None:
        args.payload_amps = 65536 if args.mode == "smoke" else 0
    if args.warmup is None:
        args.warmup = 1
    if args.reps is None:
        args.reps = 2 if args.mode == "smoke" else 7
    if not args.genuine_patterns:
        args.genuine_patterns = "quest_h_plus_pre_exchange" if args.mode == "smoke" else ",".join(GENUINE_PATTERNS)
    if not args.synthetic_patterns:
        args.synthetic_patterns = "zero_sparse,h_halfzero_real" if args.mode == "smoke" else ",".join(SYNTHETIC_PATTERNS)
    if not args.exchange_shapes:
        args.exchange_shapes = "amps_to_buffers" if args.mode == "smoke" else "amps_to_buffers,sub_buffers"
    if args.allocations < 1:
        raise ValueError("--allocations must be >= 1")
    if (args.ranks % 2) != 0:
        raise ValueError("--ranks must be even because the toy exchange pairs rank^1")


def main() -> int:
    args = parse_args()
    configure_defaults(args)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    args.processed_dir.mkdir(parents=True, exist_ok=True)

    if not args.dry_run:
        executable(args.exchange_exe, "compression_exchange")
        if not args.skip_capture:
            executable(args.capture_exe, "capture_quest_payloads")

    payload_dir = args.raw_dir / "payloads"
    tsv_dir = args.raw_dir / "rank_tsv"
    log_dir = args.raw_dir / "logs"
    payload_dir.mkdir(parents=True, exist_ok=True)
    tsv_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows: list[dict[str, str]] = []
    genuine_patterns = split_csv(args.genuine_patterns)
    synthetic_patterns = split_csv(args.synthetic_patterns)
    exchange_shapes = split_csv(args.exchange_shapes)
    codecs = split_csv(args.codecs)

    if not args.skip_capture:
        capture_command = [
            *mpi_prefix(args, args.ranks),
            str(args.capture_exe),
            "--output-dir", str(payload_dir),
            "--qubits", str(args.qubits),
            "--payload-amps", str(args.payload_amps),
            "--patterns", ",".join(genuine_patterns),
            "--exchange-shapes", ",".join(exchange_shapes),
            "--seed", str(args.seed),
            "--two-qubit-ratio", str(args.two_qubit_ratio),
        ]
        if args.depth > 0:
            capture_command.extend(["--depth", str(args.depth)])
        run_command(capture_command, log_dir / "capture_quest_payloads.log", args.dry_run, manifest_rows, "capture")

    payload_manifest = payload_dir / "payload_manifest.tsv"
    genuine_cases: list[Case] = []
    if payload_manifest.exists():
        genuine_cases = build_genuine_cases(read_payload_manifest(payload_manifest), genuine_patterns, exchange_shapes)
    elif not args.dry_run and genuine_patterns:
        raise FileNotFoundError(f"payload manifest missing: {payload_manifest}")

    payload_amps_values = sorted({case.payload_amps for case in genuine_cases})
    if not payload_amps_values:
        payload_amps_values = [args.payload_amps if args.payload_amps > 0 else 65536]
    synthetic_cases = build_synthetic_cases(synthetic_patterns, exchange_shapes, payload_amps_values)
    cases = genuine_cases + synthetic_cases

    for allocation_index in range(args.allocations):
        suffix = f"a{allocation_index + 1:02d}" if args.allocations > 1 else "a01"
        allocation_id = f"{args.allocation_id}_{suffix}"
        for case in cases:
            for codec in codecs:
                run_exchange_case(args, case, codec, allocation_id, args.raw_dir, tsv_dir, log_dir, manifest_rows)

    write_manifest(args.raw_dir / "campaign_manifest.tsv", manifest_rows)
    if args.dry_run:
        print(f"dry_run_manifest={args.raw_dir / 'campaign_manifest.tsv'}")
        return 0

    samples = args.raw_dir / "exchange_samples.tsv"
    sample_count = combine_rank_tsvs(tsv_dir, samples)
    if sample_count == 0:
        raise RuntimeError("no sample rows were collected from rank TSV files")

    if not args.skip_analysis:
        run_analysis(args, samples, args.processed_dir)

    print(f"raw_dir={args.raw_dir}")
    print(f"processed_dir={args.processed_dir}")
    print(f"samples={samples}")
    print(f"sample_rows={sample_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
