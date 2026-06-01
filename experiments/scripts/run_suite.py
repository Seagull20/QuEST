#!/usr/bin/env python3

import argparse
import csv
import math
import os
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
EXPERIMENTS_DIR = REPO_ROOT / "experiments"
BUILD_ROOT = EXPERIMENTS_DIR / "build"
RAW_ROOT = EXPERIMENTS_DIR / "results" / "raw"
DEFAULT_BASE_QUBITS = 26
DEFAULT_REPS = 3
DEFAULT_WARMUP = 1
DEFAULT_SYNC_MODE = "benchmark"

PROBE_HEADER = [
    "platform",
    "backend",
    "deployment",
    "benchmark",
    "label",
    "num_qubits",
    "rep",
    "warmup",
    "status",
    "sync_mode",
    "total_prob",
    "env_num_nodes",
    "max_qubits",
    "probe_attempts",
    "search_min",
    "search_max",
    "alloc_time_s",
    "validation_time_s",
]

QFT_HEADER = [
    "platform",
    "backend",
    "deployment",
    "benchmark",
    "label",
    "num_qubits",
    "rep",
    "warmup",
    "status",
    "sync_mode",
    "total_prob",
    "env_num_nodes",
    "stage",
    "stage_label",
    "stage_time_s",
    "total_time_s",
]

H_HEADER = [
    "platform",
    "backend",
    "deployment",
    "benchmark",
    "label",
    "num_qubits",
    "rep",
    "warmup",
    "status",
    "sync_mode",
    "total_prob",
    "env_num_nodes",
    "target_qubit",
    "gate_time_s",
]

GATE_MICRO_HEADER = [
    "platform",
    "backend",
    "deployment",
    "benchmark",
    "label",
    "num_qubits",
    "rep",
    "warmup",
    "status",
    "sync_mode",
    "total_prob",
    "env_num_nodes",
    "env_num_threads",
    "preheat_mode",
    "preheat_qubits",
    "gate_kind",
    "control_qubit",
    "target_qubit",
    "gate_repeats",
    "gate_count",
    "total_time_s",
    "time_per_gate_s",
]

RANDOM_HEADER = [
    "platform",
    "backend",
    "deployment",
    "benchmark",
    "label",
    "num_qubits",
    "rep",
    "warmup",
    "status",
    "sync_mode",
    "total_prob",
    "env_num_nodes",
    "env_num_threads",
    "preheat_mode",
    "preheat_qubits",
    "depth",
    "seed",
    "two_qubit_ratio",
    "actual_two_qubit_ratio",
    "single_qubit_gate_count",
    "two_qubit_gate_count",
    "gate_count",
    "total_time_s",
]


def info(message: str) -> None:
    print(f">>> {message}", flush=True)


def fail(message: str, code: int = 1) -> int:
    print(f">>> ERROR: {message}", file=sys.stderr, flush=True)
    return code


def ensure_file_header(path: Path, header: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, delimiter="\t")
        writer.writeheader()


def append_row(path: Path, header: List[str], row: Dict[str, object]) -> None:
    ensure_file_header(path, header)
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, delimiter="\t")
        writer.writerow(row)


def parse_last_row(path: Path) -> Optional[Dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return None
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    return rows[-1] if rows else None


def benchmark_executable(benchmark: str, backend: str) -> Path:
    return BUILD_ROOT / benchmark / backend / benchmark


def run_command(cmd: List[str], env: Dict[str, str], cwd: Optional[Path] = None) -> int:
    info("Running: " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(cwd or REPO_ROOT), env=env, check=False)
    return result.returncode


def make_env(platform: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = os.environ.copy()
    env["BENCH_PLATFORM"] = platform
    if extra:
        env.update(extra)
    return env


def raw_file(raw_dir: Path, benchmark: str, platform: str, backend: str, deployment: str) -> Path:
    return raw_dir / f"{benchmark}_{platform}_{backend}_{deployment}.tsv"


def sampled_qubits(base: int, maximum: int) -> List[int]:
    values = {base, maximum, (base + maximum) // 2}
    return sorted(v for v in values if base <= v <= maximum)


def append_probe_failure(path: Path, platform: str, backend: str, deployment: str, sync_mode: str, search_min: int, search_max: int) -> None:
    append_row(
        path,
        PROBE_HEADER,
        {
            "platform": platform,
            "backend": backend,
            "deployment": deployment,
            "benchmark": "probe",
            "label": "probe",
            "num_qubits": 0,
            "rep": 0,
            "warmup": 0,
            "status": "FAILURE",
            "sync_mode": sync_mode,
            "total_prob": "nan",
            "env_num_nodes": 1,
            "max_qubits": 0,
            "probe_attempts": 0,
            "search_min": search_min,
            "search_max": search_max,
            "alloc_time_s": "nan",
            "validation_time_s": "nan",
        },
    )


def append_qft_failure(path: Path, platform: str, backend: str, deployment: str, qubits: int, sync_mode: str, env_num_nodes: int, label: str, status: str) -> None:
    append_row(
        path,
        QFT_HEADER,
        {
            "platform": platform,
            "backend": backend,
            "deployment": deployment,
            "benchmark": "qft",
            "label": label,
            "num_qubits": qubits,
            "rep": -1,
            "warmup": 0,
            "status": status,
            "sync_mode": sync_mode,
            "total_prob": "nan",
            "env_num_nodes": env_num_nodes,
            "stage": -1,
            "stage_label": "total",
            "stage_time_s": "nan",
            "total_time_s": "nan",
        },
    )


def append_h_failure(path: Path, platform: str, backend: str, deployment: str, qubits: int, sync_mode: str, env_num_nodes: int, label: str) -> None:
    append_row(
        path,
        H_HEADER,
        {
            "platform": platform,
            "backend": backend,
            "deployment": deployment,
            "benchmark": "h_sweep",
            "label": label,
            "num_qubits": qubits,
            "rep": -1,
            "warmup": 0,
            "status": "FAILURE",
            "sync_mode": sync_mode,
            "total_prob": "nan",
            "env_num_nodes": env_num_nodes,
            "target_qubit": -1,
            "gate_time_s": "nan",
        },
    )


def append_gate_micro_failure(path: Path, platform: str, backend: str, deployment: str, qubits: int, sync_mode: str, env_num_nodes: int, label: str, gate_kind: str, gate_repeats: int) -> None:
    append_row(
        path,
        GATE_MICRO_HEADER,
        {
            "platform": platform,
            "backend": backend,
            "deployment": deployment,
            "benchmark": "gate_micro",
            "label": label,
            "num_qubits": qubits,
            "rep": -1,
            "warmup": 0,
            "status": "FAILURE",
            "sync_mode": sync_mode,
            "total_prob": "nan",
            "env_num_nodes": env_num_nodes,
            "env_num_threads": "",
            "preheat_mode": "",
            "preheat_qubits": "",
            "gate_kind": gate_kind,
            "control_qubit": -1,
            "target_qubit": -1,
            "gate_repeats": gate_repeats,
            "gate_count": -1,
            "total_time_s": "nan",
            "time_per_gate_s": "nan",
        },
    )


def append_random_failure(path: Path, platform: str, backend: str, deployment: str, qubits: int, sync_mode: str, env_num_nodes: int, label: str, depth: int, seed: int, two_qubit_ratio: float) -> None:
    append_row(
        path,
        RANDOM_HEADER,
        {
            "platform": platform,
            "backend": backend,
            "deployment": deployment,
            "benchmark": "random",
            "label": label,
            "num_qubits": qubits,
            "rep": -1,
            "warmup": 0,
            "status": "FAILURE",
            "sync_mode": sync_mode,
            "total_prob": "nan",
            "env_num_nodes": env_num_nodes,
            "env_num_threads": "",
            "preheat_mode": "",
            "preheat_qubits": "",
            "depth": depth,
            "seed": seed,
            "two_qubit_ratio": two_qubit_ratio,
            "actual_two_qubit_ratio": "nan",
            "single_qubit_gate_count": -1,
            "two_qubit_gate_count": -1,
            "gate_count": -1,
            "total_time_s": "nan",
        },
    )


def run_probe(args: argparse.Namespace, raw_dir: Path) -> int:
    path = raw_file(raw_dir, "probe", args.platform, args.backend, args.deployment)
    exe = benchmark_executable("probe", args.backend)
    env = make_env(args.platform)
    cmd = [
        str(exe),
        "--distribution",
        args.deployment,
        "--sync-mode",
        args.sync_mode,
        "--search-min",
        str(args.search_min),
        "--search-max",
        str(args.search_max),
        "--output",
        str(path),
    ]
    rc = run_command(cmd, env)
    if rc != 0 and not path.exists():
        append_probe_failure(path, args.platform, args.backend, args.deployment, args.sync_mode, args.search_min, args.search_max)
        return 0

    row = parse_last_row(path)
    if not row:
        append_probe_failure(path, args.platform, args.backend, args.deployment, args.sync_mode, args.search_min, args.search_max)
        return 0
    try:
        return int(row["max_qubits"])
    except (KeyError, TypeError, ValueError):
        return 0


def run_one_node_sweep(args: argparse.Namespace) -> int:
    raw_dir = Path(args.raw_dir).resolve()
    raw_dir.mkdir(parents=True, exist_ok=True)

    maximum = run_probe(args, raw_dir)
    if maximum < args.base_qubits:
        return fail(f"probe max_qubits={maximum} is below base qubits {args.base_qubits}")

    info(f"Detected one-node maximum qubits: {maximum}")

    qft_path = raw_file(raw_dir, "qft", args.platform, args.backend, args.deployment)
    qft_exe = benchmark_executable("qft", args.backend)
    for qubits in range(args.base_qubits, maximum + 1):
        rc = run_command(
            [
                str(qft_exe),
                "--qubits",
                str(qubits),
                "--reps",
                str(args.reps),
                "--warmup",
                str(args.warmup),
                "--distribution",
                args.deployment,
                "--sync-mode",
                args.sync_mode,
                "--output",
                str(qft_path),
            ],
            make_env(args.platform),
        )
        if rc != 0:
            append_qft_failure(qft_path, args.platform, args.backend, args.deployment, qubits, args.sync_mode, 1, "qft", "FAILURE")
            info(f"Fail-fast stop on qft qubits={qubits}")
            break

    sample_points = sampled_qubits(args.base_qubits, maximum)
    info(f"Sampled qubits for h_sweep/random: {sample_points}")

    h_path = raw_file(raw_dir, "h_sweep", args.platform, args.backend, args.deployment)
    h_exe = benchmark_executable("h_sweep", args.backend)
    for qubits in sample_points:
        rc = run_command(
            [
                str(h_exe),
                "--qubits",
                str(qubits),
                "--reps",
                str(args.reps),
                "--warmup",
                str(args.warmup),
                "--distribution",
                args.deployment,
                "--sync-mode",
                args.sync_mode,
                "--output",
                str(h_path),
            ],
            make_env(args.platform),
        )
        if rc != 0:
            append_h_failure(h_path, args.platform, args.backend, args.deployment, qubits, args.sync_mode, 1, "h_sweep")
            info(f"Fail-fast stop on h_sweep qubits={qubits}")
            break

    random_path = raw_file(raw_dir, "random", args.platform, args.backend, args.deployment)
    random_exe = benchmark_executable("random", args.backend)
    for qubits in sample_points:
        depth = args.random_depth if args.random_depth > 0 else 2 * qubits
        rc = run_command(
            [
                str(random_exe),
                "--qubits",
                str(qubits),
                "--depth",
                str(depth),
                "--seed",
                str(args.random_seed),
                "--two-qubit-ratio",
                str(args.random_two_qubit_ratio),
                "--reps",
                str(args.reps),
                "--warmup",
                str(args.warmup),
                "--distribution",
                args.deployment,
                "--sync-mode",
                args.sync_mode,
                "--output",
                str(random_path),
            ],
            make_env(args.platform),
        )
        if rc != 0:
            append_random_failure(random_path, args.platform, args.backend, args.deployment, qubits, args.sync_mode, 1, "random", depth, args.random_seed, args.random_two_qubit_ratio)
            info(f"Fail-fast stop on random qubits={qubits}")
            break

    return 0


def run_proposal_suite(args: argparse.Namespace) -> int:
    raw_dir = Path(args.raw_dir).resolve()
    raw_dir.mkdir(parents=True, exist_ok=True)

    qubits = args.base_qubits

    gate_path = raw_file(raw_dir, "gate_micro", args.platform, args.backend, args.deployment)
    gate_exe = benchmark_executable("gate_micro", args.backend)
    for gate_kind in args.gate_kinds:
        label = f"gate_micro_{gate_kind}"
        cmd = [
            str(gate_exe),
            "--qubits",
            str(qubits),
            "--gate-kind",
            gate_kind,
            "--gate-repeats",
            str(args.gate_repeats),
            "--reps",
            str(args.reps),
            "--warmup",
            str(args.warmup),
            "--distribution",
            args.deployment,
            "--sync-mode",
            args.sync_mode,
            "--preheat-mode",
            args.preheat_mode,
            "--preheat-qubits",
            str(args.preheat_qubits),
            "--label",
            label,
            "--output",
            str(gate_path),
        ]
        rc = run_command(cmd, make_env(args.platform))
        if rc != 0:
            append_gate_micro_failure(gate_path, args.platform, args.backend, args.deployment, qubits, args.sync_mode, 1, label, gate_kind, args.gate_repeats)
            return fail(f"gate_micro failed for gate_kind={gate_kind}")

    qft_path = raw_file(raw_dir, "qft", args.platform, args.backend, args.deployment)
    qft_exe = benchmark_executable("qft", args.backend)
    rc = run_command(
        [
            str(qft_exe),
            "--qubits",
            str(qubits),
            "--reps",
            str(args.reps),
            "--warmup",
            str(args.warmup),
            "--distribution",
            args.deployment,
            "--sync-mode",
            args.sync_mode,
            "--preheat-mode",
            args.preheat_mode,
            "--preheat-qubits",
            str(args.preheat_qubits),
            "--label",
            "qft",
            "--output",
            str(qft_path),
        ],
        make_env(args.platform),
    )
    if rc != 0:
        append_qft_failure(qft_path, args.platform, args.backend, args.deployment, qubits, args.sync_mode, 1, "qft", "FAILURE")
        return fail("qft failed in proposal suite")

    random_path = raw_file(raw_dir, "random", args.platform, args.backend, args.deployment)
    random_exe = benchmark_executable("random", args.backend)
    depth = args.random_depth if args.random_depth > 0 else 2 * qubits
    for ratio in args.random_two_qubit_ratios:
        label = f"random_tqr{ratio:g}"
        rc = run_command(
            [
                str(random_exe),
                "--qubits",
                str(qubits),
                "--depth",
                str(depth),
                "--seed",
                str(args.random_seed),
                "--two-qubit-ratio",
                str(ratio),
                "--reps",
                str(args.reps),
                "--warmup",
                str(args.warmup),
                "--distribution",
                args.deployment,
                "--sync-mode",
                args.sync_mode,
                "--preheat-mode",
                args.preheat_mode,
                "--preheat-qubits",
                str(args.preheat_qubits),
                "--label",
                label,
                "--output",
                str(random_path),
            ],
            make_env(args.platform),
        )
        if rc != 0:
            append_random_failure(random_path, args.platform, args.backend, args.deployment, qubits, args.sync_mode, 1, label, depth, args.random_seed, ratio)
            return fail(f"random failed for two_qubit_ratio={ratio}")

    return 0


def parse_probe_max_from_file(path: Path) -> int:
    row = parse_last_row(path)
    if not row:
        raise ValueError(f"probe file is empty: {path}")
    return int(row["max_qubits"])


def run_archer2_mpi_qft(args: argparse.Namespace) -> int:
    if args.backend != "cpu_mpi":
        return fail("mpi-qft is only implemented for backend cpu_mpi in this round")

    raw_dir = Path(args.raw_dir).resolve()
    raw_dir.mkdir(parents=True, exist_ok=True)

    if args.max_local_qubits is not None:
        max_local_qubits = args.max_local_qubits
    elif args.probe_file:
        max_local_qubits = parse_probe_max_from_file(Path(args.probe_file))
    else:
        probe_args = argparse.Namespace(**vars(args))
        probe_args.deployment = "off"
        max_local_qubits = run_probe(probe_args, raw_dir)

    if max_local_qubits < args.base_qubits:
        return fail(f"probe max_qubits={max_local_qubits} is below base qubits {args.base_qubits}")

    qft_path = raw_file(raw_dir, "qft", args.platform, args.backend, "on")
    qft_exe = benchmark_executable("qft", args.backend)

    for qubits in range(max_local_qubits + 1, max_local_qubits + args.extra_qubits + 1):
        success = False
        for nodes in args.node_counts:
            label = f"qft_nodes{nodes}"
            env = make_env(
                args.platform,
                {
                    "OMP_NUM_THREADS": str(args.cpus_per_task),
                    "OMP_PLACES": "cores",
                    "OMP_PROC_BIND": "close",
                },
            )
            cmd = [
                "srun",
                f"--nodes={nodes}",
                f"--ntasks={nodes}",
                "--ntasks-per-node=1",
                f"--cpus-per-task={args.cpus_per_task}",
                str(qft_exe),
                "--qubits",
                str(qubits),
                "--reps",
                str(args.reps),
                "--warmup",
                str(args.warmup),
                "--distribution",
                "on",
                "--sync-mode",
                args.sync_mode,
                "--label",
                label,
                "--output",
                str(qft_path),
            ]
            rc = run_command(cmd, env)
            if rc == 0:
                success = True
                break
            append_qft_failure(qft_path, args.platform, args.backend, "on", qubits, args.sync_mode, nodes, label, "FAILURE")

        if not success:
            append_qft_failure(qft_path, args.platform, args.backend, "on", qubits, args.sync_mode, args.node_counts[-1], f"qft_nodes{args.node_counts[-1]}", "MPI_CAPACITY_EXHAUSTED")
            info(f"MPI capacity exhausted at qubits={qubits}")
            break

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run QuEST experiment sweeps")
    subparsers = parser.add_subparsers(dest="command")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--platform", required=True)
    common.add_argument("--backend", required=True)
    common.add_argument("--deployment", default="off", choices=["off", "on"])
    common.add_argument("--raw-dir", default=str(RAW_ROOT))
    common.add_argument("--base-qubits", type=int, default=DEFAULT_BASE_QUBITS)
    common.add_argument("--reps", type=int, default=DEFAULT_REPS)
    common.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    common.add_argument("--sync-mode", default=DEFAULT_SYNC_MODE, choices=["benchmark", "profile"])
    common.add_argument("--search-min", type=int, default=1)
    common.add_argument("--search-max", type=int, default=62)
    common.add_argument("--random-seed", type=int, default=20260402)
    common.add_argument("--random-depth", type=int, default=-1)
    common.add_argument("--random-two-qubit-ratio", type=float, default=0.5)
    common.add_argument("--preheat-mode", default="identical", choices=["identical", "light", "off"])
    common.add_argument("--preheat-qubits", type=int, default=24)

    one_node = subparsers.add_parser("one-node", parents=[common], help="Run probe + one-node sweeps")
    one_node.set_defaults(handler=run_one_node_sweep)

    proposal = subparsers.add_parser("proposal", parents=[common], help="Run proposal suite: gate_micro + qft + random")
    proposal.add_argument("--gate-kinds", nargs="+", default=["h", "cnot", "cphase", "hn"])
    proposal.add_argument("--gate-repeats", type=int, default=64)
    proposal.add_argument("--random-two-qubit-ratios", type=float, nargs="+", default=[0.25, 0.5])
    proposal.set_defaults(handler=run_proposal_suite)

    mpi = subparsers.add_parser("mpi-qft", parents=[common], help="Run ARCHER2 QFT MPI extension")
    mpi.add_argument("--probe-file")
    mpi.add_argument("--max-local-qubits", type=int)
    mpi.add_argument("--extra-qubits", type=int, default=2)
    mpi.add_argument("--node-counts", type=int, nargs="+", default=[2, 4, 8])
    mpi.add_argument("--cpus-per-task", type=int, default=32)
    mpi.set_defaults(handler=run_archer2_mpi_qft)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "handler", None) is None:
        parser.print_help()
        return 2
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
