#!/usr/bin/env python3
"""Run a two-case Nsight Systems profile for compression_exchange."""

from __future__ import annotations

import argparse
import csv
import os
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path


BENCH_ROOT = Path(__file__).resolve().parents[1]
QUEST_ROOT = BENCH_ROOT.parents[1]


def run_name(allocation_id: str) -> str:
    if allocation_id and allocation_id != "manual":
        return f"compression_toy_nvtx_{allocation_id}"
    return "compression_toy_nvtx_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def default_raw_root(name: str) -> Path:
    return QUEST_ROOT / "experiments" / "results" / "raw" / name


def default_processed_root(name: str) -> Path:
    return QUEST_ROOT / "experiments" / "results" / "processed" / name


def capture_exe_default() -> Path:
    return BENCH_ROOT / "build" / "capture" / "capture_quest_payloads"


def exchange_exe_default() -> Path:
    return BENCH_ROOT / "build" / "exchange" / "compression_exchange"


def mpi_prefix(args: argparse.Namespace, ranks: int) -> list[str]:
    return [args.mpi_launcher, *args.mpi_launcher_arg, args.mpi_np_flag, str(ranks)]


def command_label(command: list[str]) -> str:
    return shlex.join([str(part) for part in command])


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


def pick_h_plus_case(manifest_path: Path) -> tuple[str, int, int]:
    rows = read_payload_manifest(manifest_path)
    for row in rows:
        if (
            row["rank"] == "0"
            and row["pattern"] == "quest_h_plus_pre_exchange"
            and row["exchange_shape"] == "amps_to_buffers"
        ):
            return rank_template(row), int(row["payload_amps"]), int(row["num_qubits"])
    raise ValueError(f"quest_h_plus_pre_exchange amps_to_buffers rank-0 payload missing from {manifest_path}")


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


def run_capture(args: argparse.Namespace, payload_dir: Path, log_dir: Path, manifest_rows: list[dict[str, str]]) -> None:
    command = [
        *mpi_prefix(args, args.ranks),
        str(args.capture_exe),
        "--output-dir", str(payload_dir),
        "--qubits", str(args.qubits),
        "--payload-amps", str(args.payload_amps),
        "--patterns", "quest_h_plus_pre_exchange",
        "--exchange-shapes", "amps_to_buffers",
        "--seed", str(args.seed),
        "--two-qubit-ratio", str(args.two_qubit_ratio),
    ]
    run_command(command, log_dir / "capture_quest_h_plus_pre_exchange.log", args.dry_run, manifest_rows, "capture_h_plus")


def run_profile_case(
    args: argparse.Namespace,
    codec: str,
    payload_template: str,
    payload_amps: int,
    tsv_dir: Path,
    log_dir: Path,
    profile_dir: Path,
    manifest_rows: list[dict[str, str]],
) -> Path:
    profile_base = profile_dir / codec
    output_template = tsv_dir / f"{codec}_rank_{{rank}}.tsv"
    exchange_command = [
        str(args.exchange_exe),
        "--source-kind", "genuine",
        "--pattern", "quest_h_plus_pre_exchange",
        "--checkpoint", "quest_h_plus_pre_exchange",
        "--exchange-shape", "amps_to_buffers",
        "--codec", codec,
        "--payload-amps", str(payload_amps),
        "--chunk-amps", str(args.chunk_amps),
        "--nvcomp-chunk-bytes", str(args.nvcomp_chunk_bytes),
        "--warmup", str(args.warmup),
        "--reps", str(args.reps),
        "--allocation-id", args.allocation_id,
        "--payload-template", payload_template,
        "--output", str(output_template),
    ]
    profile_command = [
        args.nsys,
        "profile",
        "--trace=cuda,mpi,nvtx,osrt",
        "--mpi-impl=openmpi",
        "--force-overwrite=true",
        "-o", str(profile_base),
        *mpi_prefix(args, args.ranks),
        *exchange_command,
    ]
    run_command(profile_command, log_dir / f"nsys_profile_{codec}.log", args.dry_run, manifest_rows, f"profile_{codec}")
    report_path = profile_base.with_suffix(".nsys-rep")
    sqlite_path = profile_base.with_suffix(".sqlite")
    if not args.dry_run and not report_path.exists():
        raise FileNotFoundError(f"Nsight report missing: {report_path}")
    export_command = [
        args.nsys,
        "export",
        "--type", "sqlite",
        "--force-overwrite=true",
        "--output", str(sqlite_path),
        str(report_path),
    ]
    run_command(export_command, log_dir / f"nsys_export_{codec}.log", args.dry_run, manifest_rows, f"export_{codec}")
    return sqlite_path


def run_analysis(args: argparse.Namespace, samples: Path, sqlite_cases: dict[str, Path], processed_dir: Path, log_dir: Path, manifest_rows: list[dict[str, str]]) -> None:
    campaign_command = [
        sys.executable,
        str(BENCH_ROOT / "scripts" / "analyze_campaign.py"),
        "--samples", str(samples),
        "--output-dir", str(processed_dir),
    ]
    run_command(campaign_command, log_dir / "analyze_campaign.log", args.dry_run, manifest_rows, "analyze_campaign")
    nvtx_command = [
        sys.executable,
        str(BENCH_ROOT / "scripts" / "nvtx_breakdown.py"),
        "--output-dir", str(processed_dir),
    ]
    for label, path in sqlite_cases.items():
        nvtx_command.extend(["--case", f"{label}={path}"])
    run_command(nvtx_command, log_dir / "nvtx_breakdown.log", args.dry_run, manifest_rows, "nvtx_breakdown")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Profile raw vs Bitcomp compression_exchange with NVTX/Nsight.")
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--processed-dir", type=Path)
    parser.add_argument("--ranks", type=int, default=4)
    parser.add_argument("--qubits", type=int, default=28)
    parser.add_argument("--payload-amps", type=int, default=0)
    parser.add_argument("--chunk-amps", type=int, default=0)
    parser.add_argument("--nvcomp-chunk-bytes", type=int, default=1 << 20)
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260402)
    parser.add_argument("--two-qubit-ratio", type=float, default=0.5)
    parser.add_argument("--allocation-id", default=os.environ.get("SLURM_JOB_ID", "manual"))
    parser.add_argument("--capture-exe", type=Path, default=capture_exe_default())
    parser.add_argument("--exchange-exe", type=Path, default=exchange_exe_default())
    parser.add_argument("--skip-capture", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--nsys", default="nsys")
    parser.add_argument("--mpi-launcher", default="mpirun")
    parser.add_argument("--mpi-np-flag", default="-np")
    parser.add_argument("--mpi-launcher-arg", action="append", default=[])
    return parser.parse_args()


def configure_paths(args: argparse.Namespace) -> None:
    if (args.ranks % 2) != 0:
        raise ValueError("--ranks must be even because compression_exchange pairs rank^1")
    name = run_name(args.allocation_id)
    args.raw_dir = args.raw_dir or default_raw_root(name)
    args.processed_dir = args.processed_dir or default_processed_root(args.raw_dir.name)


def main() -> int:
    args = parse_args()
    configure_paths(args)
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    args.processed_dir.mkdir(parents=True, exist_ok=True)

    if not args.dry_run:
        if not args.exchange_exe.exists():
            raise FileNotFoundError(f"compression_exchange executable missing: {args.exchange_exe}")
        if not args.skip_capture and not args.capture_exe.exists():
            raise FileNotFoundError(f"capture_quest_payloads executable missing: {args.capture_exe}")

    payload_dir = args.raw_dir / "payloads"
    tsv_dir = args.raw_dir / "rank_tsv"
    log_dir = args.raw_dir / "logs"
    profile_dir = args.raw_dir / "profiles"
    for directory in [payload_dir, tsv_dir, log_dir, profile_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    manifest_rows: list[dict[str, str]] = []
    if not args.skip_capture:
        run_capture(args, payload_dir, log_dir, manifest_rows)

    payload_manifest = payload_dir / "payload_manifest.tsv"
    if not args.dry_run and not payload_manifest.exists():
        raise FileNotFoundError(f"payload manifest missing: {payload_manifest}")
    if args.dry_run:
        payload_template = str(payload_dir / "rank_{rank}_quest_h_plus_pre_exchange_amps_to_buffers_q28_a0.bin")
        payload_amps = args.payload_amps
    else:
        payload_template, payload_amps, captured_qubits = pick_h_plus_case(payload_manifest)
        if captured_qubits != args.qubits:
            raise ValueError(f"captured qubits mismatch: expected {args.qubits}, got {captured_qubits}")

    sqlite_cases = {
        "raw": run_profile_case(args, "raw", payload_template, payload_amps, tsv_dir, log_dir, profile_dir, manifest_rows),
        "nvcomp_bitcomp": run_profile_case(args, "nvcomp_bitcomp", payload_template, payload_amps, tsv_dir, log_dir, profile_dir, manifest_rows),
    }

    samples = args.raw_dir / "exchange_samples.tsv"
    if args.dry_run:
        print(f"dry_run_raw_dir={args.raw_dir}")
        print(f"dry_run_processed_dir={args.processed_dir}")
        write_manifest(args.raw_dir / "nvtx_profile_manifest.tsv", manifest_rows)
        return 0

    sample_count = combine_rank_tsvs(tsv_dir, samples)
    if sample_count == 0:
        raise RuntimeError("no sample rows were collected from rank TSV files")
    run_analysis(args, samples, sqlite_cases, args.processed_dir, log_dir, manifest_rows)
    write_manifest(args.raw_dir / "nvtx_profile_manifest.tsv", manifest_rows)
    print(f"raw_dir={args.raw_dir}")
    print(f"processed_dir={args.processed_dir}")
    print(f"samples={samples}")
    print(f"sample_rows={sample_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
