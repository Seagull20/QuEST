#!/usr/bin/env python3
"""Compare T-039 raw/window logs without requiring cluster access here."""

import re
import sys
import os
from pathlib import Path


STATE_RE = re.compile(
    r"T039_STATE qubits=(?P<q>\d+) target=(?P<t>\d+) "
    r"rank=(?P<rank>\d+) local_amps=(?P<amps>\d+) hash=(?P<hash>[0-9a-fA-F]+)"
)
STATS_RE = re.compile(
    r"\[quest-staging-stats\] rank=(?P<rank>\d+) mode=(?P<mode>\w+) "
    r"window_exchanges=(?P<window>\d+) raw_fallback_exchanges=(?P<fallback>\d+) "
    r"fallback_off_node=(?P<offnode>\d+) fallback_registration_consensus=(?P<registration>\d+) "
    r"window_control_seconds=(?P<control>[0-9.eE+-]+) "
    r"window_payload_seconds=(?P<window_payload>[0-9.eE+-]+) "
    r"payload_mpi_seconds=(?P<payload>[0-9.eE+-]+) "
    r"window_control_bytes=(?P<control_bytes>\d+) mpi_payload_bytes=(?P<payload_bytes>\d+)"
)


def read_log(path: Path):
    states = {}
    stats = {}
    for line in path.read_text(errors="replace").splitlines():
        match = STATE_RE.search(line)
        if match:
            key = (int(match["q"]), int(match["t"]), int(match["rank"]))
            states[key] = match["hash"].lower()
        match = STATS_RE.search(line)
        if match:
            rank = int(match["rank"])
            stats[rank] = {key: value for key, value in match.groupdict().items()}
    return states, stats


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def check_pair(raw_path: Path, window_path: Path, expected_ranks: int, require_window: bool):
    raw_states, raw_stats = read_log(raw_path)
    window_states, window_stats = read_log(window_path)
    require(raw_states, f"no state records in {raw_path}")
    require(window_states, f"no state records in {window_path}")
    require(raw_states == window_states, f"raw/window state hashes differ for {raw_path.name}")
    require(len(raw_stats) == expected_ranks, f"raw stats rank count mismatch in {raw_path}")
    require(len(window_stats) == expected_ranks, f"window stats rank count mismatch in {window_path}")
    for rank, stat in raw_stats.items():
        require(stat["mode"] == "raw", f"rank {rank} did not report raw mode")
        require(int(stat["payload_bytes"]) > 0,
                f"rank {rank} reported no raw MPI payload in baseline")

    if require_window:
        expected_mode = os.environ.get("T039_EXPECT_WINDOW_MODE", "bulk_async")
        for rank, stat in window_stats.items():
            require(stat["mode"] == expected_mode,
                    f"rank {rank} did not report {expected_mode}")
            require(int(stat["window"]) > 0, f"rank {rank} used no window exchanges")
            require(int(stat["payload_bytes"]) == 0,
                    f"rank {rank} reported MPI payload bytes on window path")
            require(float(stat["payload"]) == 0.0,
                    f"rank {rank} reported raw MPI payload time on window path")
            require(float(stat["control"]) >= 0.0 and float(stat["window_payload"]) >= 0.0,
                    f"rank {rank} reported invalid separated window timings")


def check_forced_fallback(raw_path: Path, fallback_path: Path, expected_ranks: int):
    raw_states, raw_stats = read_log(raw_path)
    fallback_states, fallback_stats = read_log(fallback_path)
    require(raw_states == fallback_states, "forced-fallback state hashes differ from raw")
    require(len(raw_stats) == expected_ranks, "forced-fallback raw stats rank count mismatch")
    for rank, stat in raw_stats.items():
        require(stat["mode"] == "raw", f"rank {rank} did not report raw fallback baseline")
        require(int(stat["payload_bytes"]) > 0,
                f"rank {rank} reported no raw MPI payload fallback baseline")
    require(len(fallback_stats) == expected_ranks, "forced-fallback stats rank count mismatch")
    for rank, stat in fallback_stats.items():
        require(int(stat["window"]) == 0, f"rank {rank} unexpectedly used window in fallback")
        require(int(stat["fallback"]) > 0, f"rank {rank} did not report raw fallback")
        require(int(stat["registration"]) > 0,
                f"rank {rank} did not report registration-consensus fallback")
        require(int(stat["payload_bytes"]) > 0,
                f"rank {rank} reported no raw payload in fallback")


def check_offnode(raw_path: Path, window_path: Path, expected_ranks: int):
    raw_states, raw_stats = read_log(raw_path)
    window_states, window_stats = read_log(window_path)
    require(raw_states == window_states, "off-node raw/window state hashes differ")
    require(len(raw_stats) == expected_ranks, "off-node raw stats rank count mismatch")
    for rank, stat in raw_stats.items():
        require(stat["mode"] == "raw", f"rank {rank} did not report raw mode off-node")
        require(int(stat["payload_bytes"]) > 0,
                f"rank {rank} reported no raw MPI payload baseline off-node")
    require(len(window_stats) == expected_ranks, "off-node stats rank count mismatch")
    expected_mode = os.environ.get("T039_EXPECT_WINDOW_MODE", "bulk_async")
    for rank, stat in window_stats.items():
        require(stat["mode"] == expected_mode,
                f"rank {rank} did not report {expected_mode}")
        require(int(stat["window"]) == 0, f"rank {rank} unexpectedly used a window off-node")
        require(int(stat["offnode"]) > 0, f"rank {rank} did not report off-node fallback")
        require(int(stat["fallback"]) > 0, f"rank {rank} did not report raw fallback off-node")
        require(int(stat["payload_bytes"]) > 0,
                f"rank {rank} reported no raw payload off-node")


def main(argv):
    if len(argv) < 2:
        print(
            f"usage: {argv[0]} raw.log window.log ranks "
            "[--expect-offnode | --expect-registration-fallback ranks]",
            file=sys.stderr,
        )
        return 2

    try:
        # Arguments are grouped as: raw, window, ranks, then either a forced
        # fallback log + rank count or the off-node expectation marker.
        if len(argv) not in (4, 5, 6):
            raise RuntimeError("unexpected result-check arguments")
        raw_path = Path(argv[1])
        window_path = Path(argv[2])
        ranks = int(argv[3])
        if len(argv) == 4:
            check_pair(raw_path, window_path, ranks, True)
        elif len(argv) == 5 and argv[4] == "--expect-offnode":
            check_offnode(raw_path, window_path, ranks)
        elif len(argv) == 6 and argv[4] == "--expect-registration-fallback":
            require(int(argv[5]) == ranks, "fallback rank count arguments disagree")
            check_forced_fallback(raw_path, window_path, ranks)
        else:
            raise RuntimeError("unexpected result-check arguments")
        print("T039_RESULT_CHECK PASS")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"T039_RESULT_CHECK FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
