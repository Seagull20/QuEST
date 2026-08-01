#!/usr/bin/env python3
"""T-024 counter-side second source for the per-exchange compressibility trend.

The trend published so far comes from ONE instrument: the nsys/profiler view of
MPI bytes on the wire. This script derives the same quantity from a second,
independent instrument -- QuEST's own in-path counters, emitted per rank at exit
by comm_compression.cpp when QUEST_EXCHANGE_COMPRESSION_STATS=1:

    [quest-nvcomp-stats] rank=R raw_bytes=.. sent_bytes=.. compressed_chunks=..
                         fallback_chunks=.. control_bytes=..

raw_bytes / sent_bytes is the achieved payload reduction on the compressed
exchange path, counted at the call site rather than observed on the wire.

Association: the counters are printed at process exit, so one mpirun invocation
contributes exactly one contiguous block of `ranks` lines, in invocation order.
commands.log records the invocations in the same order, and each carries the
point's output path, which names the point. Invocations that never enter the
exchange (the CPhase zero-communication control) register no atexit handler and
therefore emit no block at all -- so the association is a merge, not a zip, and
the script asserts the arithmetic instead of assuming it.

Usage:
    python3 t024_counter_second_source.py <results_raw_dir> [<results_raw_dir> ...]

Writes one TSV per campaign plus a combined summary to
experiments/results/processed/t024_counter_second_source/.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

STATS_RE = re.compile(
    r"\[quest-nvcomp-stats\]\s+rank=(?P<rank>-?\d+)\s+"
    r"raw_bytes=(?P<raw>\d+)\s+sent_bytes=(?P<sent>\d+)\s+"
    r"compressed_chunks=(?P<comp>\d+)\s+fallback_chunks=(?P<fallback>\d+)\s+"
    r"control_bytes=(?P<control>\d+)"
)
# `--output <path>/{timing,profiles}/<point>.tsv` identifies the invocation.
OUTPUT_RE = re.compile(r"--output\s+(?P<path>\S+?/(?P<phase>timing|profiles)/(?P<point>[a-z0-9_]+)\.tsv)")
NP_RE = re.compile(r"\bnp\s+(?P<np>\d+)\b")


@dataclass
class Invocation:
    index: int
    point: str
    phase: str
    ranks: int


@dataclass
class StatsLine:
    rank: int
    raw_bytes: int
    sent_bytes: int
    compressed_chunks: int
    fallback_chunks: int
    control_bytes: int


def parse_invocations(commands_log: Path) -> list[Invocation]:
    out: list[Invocation] = []
    for line in commands_log.read_text(errors="replace").splitlines():
        m = OUTPUT_RE.search(line)
        if not m:
            continue
        npm = NP_RE.search(line)
        if not npm:
            raise SystemExit(f"invocation without -np in {commands_log}: {line[:160]}")
        out.append(
            Invocation(
                index=len(out),
                point=m.group("point"),
                phase="timing" if m.group("phase") == "timing" else "profile",
                ranks=int(npm.group("np")),
            )
        )
    return out


def parse_stats_blocks(err_file: Path) -> list[list[StatsLine]]:
    """Contiguous runs of stats lines, split whenever a rank id repeats."""
    blocks: list[list[StatsLine]] = []
    current: list[StatsLine] = []
    seen: set[int] = set()
    for line in err_file.read_text(errors="replace").splitlines():
        m = STATS_RE.search(line)
        if not m:
            continue
        rank = int(m.group("rank"))
        if rank in seen:
            blocks.append(current)
            current, seen = [], set()
        seen.add(rank)
        current.append(
            StatsLine(
                rank=rank,
                raw_bytes=int(m.group("raw")),
                sent_bytes=int(m.group("sent")),
                compressed_chunks=int(m.group("comp")),
                fallback_chunks=int(m.group("fallback")),
                control_bytes=int(m.group("control")),
            )
        )
    if current:
        blocks.append(current)
    return blocks


# CPhase is the campaign's zero-communication control: it is diagonal, so the
# localiser never reaches the distributed exchange, comm_compression is never
# entered, and no atexit handler is ever registered. Its invocations therefore
# contribute no counter block at all. This is the ONLY silent-skip case, and the
# assertion below turns any other mismatch into a failure instead of a shifted
# association that would relabel every point after it.
NON_COMMUNICATING_PREFIXES = ("cphase_",)


def associate(invocations: list[Invocation], blocks: list[list[StatsLine]]) -> list[tuple[Invocation, list[StatsLine]]]:
    expected = [inv for inv in invocations if not inv.point.startswith(NON_COMMUNICATING_PREFIXES)]
    if len(blocks) > len(expected):
        raise SystemExit(
            f"association refused: {len(blocks)} counter blocks exceed {len(expected)} communicating "
            f"invocations ({len(invocations)} total). Positional association cannot be sound — "
            "inspect the campaign by hand."
        )
    if len(blocks) < len(expected):
        # A walltime kill truncates the TAIL: earlier points already exited and
        # printed, later ones never ran. The prefix association stays sound, but
        # the dropped points must be named rather than silently absent.
        dropped = ", ".join(f"{i.point}/{i.phase}" for i in expected[len(blocks):])
        print(f"  WARNING: campaign truncated — no counters for: {dropped}")
        expected = expected[: len(blocks)]
    for inv, block in zip(expected, blocks):
        if inv.ranks != len(block):
            raise SystemExit(
                f"association refused: invocation {inv.point}/{inv.phase} ran {inv.ranks} ranks "
                f"but its counter block has {len(block)} lines."
            )
    return list(zip(expected, blocks))


def process(raw_dir: Path, out_dir: Path) -> dict | None:
    job = raw_dir.name.split("_")[-1]
    err = raw_dir.parent / f"{raw_dir.name}.err"
    commands = raw_dir / "commands.log"
    if not err.exists() or not commands.exists():
        print(f"skip {raw_dir.name}: missing .err or commands.log")
        return None

    invocations = parse_invocations(commands)
    blocks = parse_stats_blocks(err)
    if not blocks:
        print(f"skip {raw_dir.name}: no counter lines (arm not run with STATS=1)")
        return None
    pairs = associate(invocations, blocks)

    meta = {}
    meta_file = raw_dir / "campaign_metadata.txt"
    if meta_file.exists():
        for line in meta_file.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                meta[k.strip()] = v.strip()

    rows = []
    for inv, block in pairs:
        for s in block:
            ratio = s.raw_bytes / s.sent_bytes if s.sent_bytes else float("nan")
            rows.append(
                dict(
                    job=job,
                    compression_mode=meta.get("compression_mode", "unknown"),
                    point=inv.point,
                    phase=inv.phase,
                    rank=s.rank,
                    raw_bytes=s.raw_bytes,
                    sent_bytes=s.sent_bytes,
                    counter_ratio=ratio,
                    compressed_chunks=s.compressed_chunks,
                    fallback_chunks=s.fallback_chunks,
                    control_bytes=s.control_bytes,
                )
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    per_rank = out_dir / f"counters_per_rank_{job}.tsv"
    cols = list(rows[0].keys())
    with per_rank.open("w") as fh:
        fh.write("\t".join(cols) + "\n")
        for r in rows:
            fh.write("\t".join(f"{r[c]:.6f}" if isinstance(r[c], float) else str(r[c]) for c in cols) + "\n")

    # Per-point aggregate: ranks differ (the critical rank carries more traffic),
    # so report the whole-point ratio and the per-rank spread rather than a mean
    # that would hide it.
    summary = []
    for inv, block in pairs:
        raw = sum(s.raw_bytes for s in block)
        sent = sum(s.sent_bytes for s in block)
        ratios = sorted(s.raw_bytes / s.sent_bytes for s in block if s.sent_bytes)
        summary.append(
            dict(
                job=job,
                compression_mode=meta.get("compression_mode", "unknown"),
                point=inv.point,
                phase=inv.phase,
                ranks=len(block),
                raw_bytes_total=raw,
                sent_bytes_total=sent,
                counter_ratio_total=raw / sent if sent else float("nan"),
                counter_ratio_min=ratios[0] if ratios else float("nan"),
                counter_ratio_max=ratios[-1] if ratios else float("nan"),
                fallback_chunks_total=sum(s.fallback_chunks for s in block),
                compressed_chunks_total=sum(s.compressed_chunks for s in block),
                control_bytes_total=sum(s.control_bytes for s in block),
            )
        )
    return dict(job=job, meta=meta, rows=rows, summary=summary, per_rank_file=per_rank)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    raw_dirs = [Path(a).resolve() for a in argv[1:]]
    out_dir = raw_dirs[0].parent.parent / "processed" / "t024_counter_second_source"

    all_summary = []
    for d in raw_dirs:
        res = process(d, out_dir)
        if res:
            all_summary.extend(res["summary"])
            print(f"{d.name}: {len(res['rows'])} counter lines over {len(res['summary'])} points")

    if not all_summary:
        print("no campaigns produced counters")
        return 1

    cols = list(all_summary[0].keys())
    combined = out_dir / "counter_second_source_summary.tsv"
    with combined.open("w") as fh:
        fh.write("\t".join(cols) + "\n")
        for r in all_summary:
            fh.write("\t".join(f"{r[c]:.6f}" if isinstance(r[c], float) else str(r[c]) for c in cols) + "\n")

    print(f"\nwrote {combined}")
    print(f"\n{'point':<18}{'phase':<9}{'mode':<7}{'ratio':>10}{'min':>10}{'max':>10}{'fallback':>10}")
    for r in all_summary:
        print(
            f"{r['point']:<18}{r['phase']:<9}{r['compression_mode']:<7}"
            f"{r['counter_ratio_total']:>10.2f}{r['counter_ratio_min']:>10.2f}"
            f"{r['counter_ratio_max']:>10.2f}{r['fallback_chunks_total']:>10d}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
