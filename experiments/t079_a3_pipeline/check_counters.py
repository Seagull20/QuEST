#!/usr/bin/env python3
"""T-079 counter-engagement checks for the fused win/on/tiled arm.

check_results.py already proves the states are bit-identical to the raw
exchange and that the window path carried the payload.  This adds what only
the fused arm can show: that the codec actually ran INSIDE the pipeline, on
the unit the run claims, without falling back to MPI for any byte.
"""

import argparse
import re
import sys
from pathlib import Path


STAGING_RE = re.compile(
    r"\[quest-staging-stats\] rank=(?P<rank>\d+) mode=(?P<mode>\w+) "
    r"window_exchanges=(?P<window>\d+) raw_fallback_exchanges=(?P<fallback>\d+) "
    r"fallback_off_node=(?P<offnode>\d+) fallback_registration_consensus=(?P<registration>\d+) "
    r"window_control_seconds=(?P<control>[0-9.eE+-]+) "
    r"window_payload_seconds=(?P<window_payload>[0-9.eE+-]+) "
    r"payload_mpi_seconds=(?P<payload>[0-9.eE+-]+) "
    r"window_control_bytes=(?P<control_bytes>\d+) mpi_payload_bytes=(?P<payload_bytes>\d+)"
)
CODEC_RE = re.compile(
    r"\[quest-nvcomp-stats\] rank=(?P<rank>-?\d+) raw_bytes=(?P<raw>\d+) "
    r"sent_bytes=(?P<sent>\d+) compressed_chunks=(?P<compressed>\d+) "
    r"fallback_chunks=(?P<fallback>\d+) control_bytes=(?P<control>\d+)"
)
PIPELINE_RE = re.compile(
    r"\[quest-nvcomp\] fused window pipeline ENABLED .*? unit=(?P<unit>\d+) MiB"
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("log")
    parser.add_argument("--ranks", type=int, required=True)
    parser.add_argument("--expect-mode", default="tiled_materialize_codec")
    parser.add_argument("--expect-unit-mib", type=int, default=None)
    parser.add_argument("--expect-all-fallback", action="store_true",
                        help="force-raw ablation: every unit must take the raw fallback")
    args = parser.parse_args(argv[1:])

    try:
        text = Path(args.log).read_text(errors="replace")
        staging = {}
        codec = {}
        units = set()
        for line in text.splitlines():
            match = STAGING_RE.search(line)
            if match:
                staging[int(match["rank"])] = match.groupdict()
            match = CODEC_RE.search(line)
            if match:
                codec[int(match["rank"])] = match.groupdict()
            match = PIPELINE_RE.search(line)
            if match:
                units.add(int(match["unit"]))

        require(len(staging) == args.ranks,
                f"expected {args.ranks} staging-stats lines, found {len(staging)}")
        require(len(codec) == args.ranks,
                f"expected {args.ranks} nvcomp-stats lines, found {len(codec)}")

        # The pipeline announces its effective unit once per rank at window
        # set-up. D-032 requires that unit to be provable from the run's own
        # output, and every rank must agree on it.
        require(units, "no fused-pipeline start-up line: the codec never prepared")
        require(len(units) == 1, f"ranks disagree on the pipeline unit: {sorted(units)}")
        unit_mib = units.pop()
        if args.expect_unit_mib is not None:
            require(unit_mib == args.expect_unit_mib,
                    f"pipeline unit is {unit_mib} MiB, expected {args.expect_unit_mib} MiB")

        for rank, stat in staging.items():
            require(stat["mode"] == args.expect_mode,
                    f"rank {rank} reported mode={stat['mode']}, expected {args.expect_mode}")
            require(int(stat["window"]) > 0, f"rank {rank} performed no window exchange")
            require(int(stat["payload_bytes"]) == 0,
                    f"rank {rank} sent {stat['payload_bytes']} payload bytes over MPI")
            require(float(stat["payload"]) == 0.0,
                    f"rank {rank} spent MPI payload time on the fused path")
            require(int(stat["fallback"]) == 0,
                    f"rank {rank} fell back to the raw exchange {stat['fallback']} times")
            require(int(stat["offnode"]) == 0,
                    f"rank {rank} reported an off-node pair in an on-node case")
            require(int(stat["control_bytes"]) > 0,
                    f"rank {rank} exchanged no descriptors")

        for rank, stat in codec.items():
            require(int(stat["raw"]) > 0, f"rank {rank} passed no bytes through the codec")
            require(int(stat["sent"]) > 0, f"rank {rank} recorded no staged bytes")
            if args.expect_all_fallback:
                require(int(stat["compressed"]) == 0,
                        f"rank {rank} encoded {stat['compressed']} units under force-raw")
                require(int(stat["fallback"]) > 0,
                        f"rank {rank} recorded no raw-fallback units under force-raw")
                require(int(stat["sent"]) == int(stat["raw"]),
                        f"rank {rank} staged {stat['sent']} of {stat['raw']} raw bytes "
                        "under force-raw; the fallback must be byte-for-byte")
            else:
                require(int(stat["compressed"]) > 0,
                        f"rank {rank} encoded no units inside the pipeline")
                require(int(stat["fallback"]) == 0,
                        f"rank {rank} took the per-unit raw fallback {stat['fallback']} "
                        "times on a compressible state")
                require(int(stat["sent"]) < int(stat["raw"]),
                        f"rank {rank} staged {stat['sent']} bytes for {stat['raw']} raw "
                        "bytes; the codec cannot have run")

        print(f"T079_COUNTER_CHECK PASS unit={unit_mib}MiB log={args.log}")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"T079_COUNTER_CHECK FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
