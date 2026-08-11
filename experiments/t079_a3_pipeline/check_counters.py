#!/usr/bin/env python3
"""T-079 engagement and accounting checks for one run's log.

check_results.py proves the states are bit-identical to the raw exchange. This
proves what bit identity cannot see: that the run took the arm it claims, that
the codec was invoked on exactly the units the per-unit gate should have let
through, and that no rank quietly fell back.

Every assertion is exact where the expectation is exact. Unit accounting is
expressed per exchange and multiplied by each rank's own observed
window_exchanges count, so it does not depend on how many exchanges the probe
happens to perform.
"""

import argparse
import re
import sys
from pathlib import Path


STATE_RE = re.compile(
    r"T039_STATE qubits=(?P<q>\d+) target=(?P<t>\d+) "
    r"rank=(?P<rank>\d+) local_amps=(?P<amps>\d+) hash=(?P<hash>[0-9a-fA-F]+)"
)
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
    r"\[quest-nvcomp\] fused window pipeline ENABLED .*? "
    r"rank=(?P<rank>-?\d+) unit_bytes=(?P<unit>\d+) gate_bytes=(?P<gate>\d+)"
)
INIT_FALLBACK_RE = re.compile(r"A3_INIT_FALLBACK rank=(?P<rank>-?\d+) reason=(?P<reason>.*)")


class CheckFailure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise CheckFailure(message)


def parse(path):
    states, staging, codec, pipeline, fallbacks = set(), {}, {}, {}, []
    for line in Path(path).read_text(errors="replace").splitlines():
        match = STATE_RE.search(line)
        if match:
            states.add((int(match["q"]), int(match["t"]), int(match["rank"])))
        match = STAGING_RE.search(line)
        if match:
            staging[int(match["rank"])] = match.groupdict()
        match = CODEC_RE.search(line)
        if match:
            codec[int(match["rank"])] = match.groupdict()
        match = PIPELINE_RE.search(line)
        if match:
            pipeline.setdefault(int(match["rank"]), []).append(match.groupdict())
        match = INIT_FALLBACK_RE.search(line)
        if match:
            fallbacks.append(f"rank {match['rank']}: {match['reason'].strip()}")
    return states, staging, codec, pipeline, fallbacks


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("log")
    parser.add_argument("--ranks", type=int, required=True)
    parser.add_argument("--qubits", type=int, required=True)
    parser.add_argument("--targets", required=True,
                        help="comma-separated distributed targets the probe exercised")
    parser.add_argument("--expect-mode", required=True)
    parser.add_argument("--window", choices=("required", "none"), default="required")
    parser.add_argument("--codec", choices=("required", "none"), default="none")
    parser.add_argument("--units-per-exchange", type=int, default=None)
    parser.add_argument("--gated-per-exchange", type=int, default=None)
    parser.add_argument("--expect-unit-bytes", type=int, default=None,
                        help="omit to assert the fused pipeline was NEVER prepared")
    args = parser.parse_args(argv[1:])

    try:
        states, staging, codec, pipeline, fallbacks = parse(args.log)

        # An A3 run that quietly became an I1 run is the provenance hazard this
        # whole harness exists to catch, so the self-reported fallback is fatal
        # wherever it appears.
        require(not fallbacks,
                "the fused arm reported an initialisation fallback: " + "; ".join(fallbacks))

        # (a) the exact state-record key set, not merely equality between two
        # logs: both sides missing the same records would otherwise pass.
        targets = [int(t) for t in args.targets.split(",") if t != ""]
        expected_states = {(args.qubits, t, r) for t in targets for r in range(args.ranks)}
        require(states == expected_states,
                "state records do not match the expected key set; missing "
                f"{sorted(expected_states - states)}, unexpected {sorted(states - expected_states)}")

        require(set(staging) == set(range(args.ranks)),
                f"staging-stats ranks {sorted(staging)} != expected {list(range(args.ranks))}")

        for rank, stat in staging.items():
            require(stat["mode"] == args.expect_mode,
                    f"rank {rank} reported mode={stat['mode']}, expected {args.expect_mode}")
            if args.window == "required":
                require(int(stat["window"]) > 0, f"rank {rank} performed no window exchange")
                require(int(stat["payload_bytes"]) == 0,
                        f"rank {rank} sent {stat['payload_bytes']} payload bytes over MPI")
                require(float(stat["payload"]) == 0.0,
                        f"rank {rank} spent MPI payload time on a window path")
                require(int(stat["fallback"]) == 0,
                        f"rank {rank} fell back to the raw exchange {stat['fallback']} times")
                require(int(stat["offnode"]) == 0,
                        f"rank {rank} reported an off-node pair in an on-node case")
                require(int(stat["control_bytes"]) > 0, f"rank {rank} exchanged no descriptors")
            else:
                require(int(stat["window"]) == 0,
                        f"rank {rank} used the window in a baseline expected to be raw")
                require(int(stat["payload_bytes"]) > 0,
                        f"rank {rank} carried no raw MPI payload in the baseline")

        # The fused pipeline announces its effective unit once per rank, in
        # exact bytes. Absence is asserted just as strictly as presence: it is
        # how a non-fused leg proves it did not quietly engage the codec.
        if args.expect_unit_bytes is None:
            require(not pipeline,
                    f"the fused pipeline was prepared on ranks {sorted(pipeline)} in a leg "
                    "that must never reach it")
        else:
            require(set(pipeline) == set(range(args.ranks)),
                    f"fused-pipeline start-up ranks {sorted(pipeline)} != "
                    f"expected {list(range(args.ranks))}")
            for rank, records in pipeline.items():
                require(len(records) == 1,
                        f"rank {rank} announced the pipeline {len(records)} times, expected once")
                unit = int(records[0]["unit"])
                require(unit == args.expect_unit_bytes,
                        f"rank {rank} ran unit_bytes={unit}, expected {args.expect_unit_bytes}")

        if args.codec == "none":
            require(not codec,
                    f"nvcomp counters appeared on ranks {sorted(codec)} in a codec-off leg")
        else:
            require(set(codec) == set(range(args.ranks)),
                    f"nvcomp-stats ranks {sorted(codec)} != expected {list(range(args.ranks))}")
            require(args.units_per_exchange is not None and args.gated_per_exchange is not None,
                    "--units-per-exchange and --gated-per-exchange are required with --codec required")
            encoded_per_exchange = args.units_per_exchange - args.gated_per_exchange
            require(encoded_per_exchange >= 0, "gated units exceed the units per exchange")

            for rank, stat in codec.items():
                exchanges = int(staging[rank]["window"])
                expected_compressed = exchanges * encoded_per_exchange
                expected_fallback = exchanges * args.gated_per_exchange
                require(int(stat["compressed"]) == expected_compressed,
                        f"rank {rank} encoded {stat['compressed']} units, expected "
                        f"{expected_compressed} ({exchanges} exchanges x {encoded_per_exchange})")
                require(int(stat["fallback"]) == expected_fallback,
                        f"rank {rank} staged {stat['fallback']} units raw, expected "
                        f"{expected_fallback} ({exchanges} exchanges x {args.gated_per_exchange})")
                require(int(stat["raw"]) > 0, f"rank {rank} passed no bytes through the codec")
                if encoded_per_exchange == 0:
                    require(int(stat["sent"]) == int(stat["raw"]),
                            f"rank {rank} staged {stat['sent']} of {stat['raw']} raw bytes with "
                            "every unit raw; the fallback must be byte-for-byte")
                else:
                    require(int(stat["sent"]) < int(stat["raw"]),
                            f"rank {rank} staged {stat['sent']} bytes for {stat['raw']} raw "
                            "bytes; the codec cannot have run")

        summary = f"mode={args.expect_mode} ranks={args.ranks}"
        if args.expect_unit_bytes is not None:
            summary += f" unit_bytes={args.expect_unit_bytes}"
        if args.codec == "required":
            summary += (f" units_per_exchange={args.units_per_exchange}"
                        f" gated_per_exchange={args.gated_per_exchange}")
        print(f"T079_COUNTER_CHECK PASS {summary} log={args.log}")
        return 0
    except (OSError, ValueError, CheckFailure) as error:
        print(f"T079_COUNTER_CHECK FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
