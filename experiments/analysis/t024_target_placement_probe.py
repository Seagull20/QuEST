#!/usr/bin/env python3
"""Decide why T-024's CPhase weak/strong efficiency exceeded 1.

The hypothesis under test (recorded before the probe ran):

    The gate_micro campaigns pin target = num_qubits-1 and control = 0. QuEST's
    top log2(p) qubits are the rank-index (prefix) bits, so that target is a
    LOCAL qubit at 1 rank and a PREFIX qubit at 2 and 4 ranks. The operation
    therefore changes character exactly at the distribution threshold, and the
    ratio t(1)/t(p) is not a work-invariant scaling statistic.

The probe runs two arms that differ ONLY in target placement:

    prefix arm  target = q-1   (the campaign's choice; prefix once p > 1)
    suffix arm  target = q-3   (a local qubit at p = 1, 2 AND 4)

Predictions, fixed in advance:

    P1  CPhase prefix arm reproduces the anomaly: t(1)/t(2) > 2 at q23.
    P2  CPhase suffix arm does NOT: t(1)/t(2) <= 2 at every size, because no
        rank can skip the gate and the code path no longer changes.
    P3  Hadamard suffix arm scales normally (t(1)/t(p) > 1), because a local
        target needs no exchange at any rank count.
    P4  Hadamard prefix arm collapses (t(1)/t(2) << 1), reproducing the
        campaign, because a prefix target forces the staged exchange.

P2 is the falsifier. If the suffix arm still shows a ratio above 2, the
explanation is wrong and the cause lies elsewhere.

Usage:
    python3 t024_target_placement_probe.py <probe_results_dir>
"""

from __future__ import annotations

import csv
import statistics
import sys
from pathlib import Path


def load(probe_dir: Path) -> dict:
    """(kind, arm, qubits, ranks) -> median wall time over the timed reps."""
    out = {}
    for tsv in sorted(probe_dir.glob("*.tsv")):
        rows = [r for r in csv.DictReader(tsv.open(), delimiter="\t")
                if r.get("warmup") == "0" and r.get("status") == "PASS"]
        if not rows:
            print(f"  WARNING: no PASS timed rows in {tsv.name}")
            continue
        stem = tsv.stem                      # e.g. cphase_prefix_p2_q23
        kind, arm, ranks, qubits = stem.split("_")
        key = (kind, arm, int(qubits[1:]), int(ranks[1:]))
        out[key] = dict(
            median=statistics.median(float(r["total_time_s"]) for r in rows),
            n=len(rows),
            target=int(rows[0]["target_qubit"]),
            control=int(rows[0]["control_qubit"]),
            prob=rows[0]["total_prob"],
        )
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    data = load(Path(argv[1]).resolve())
    if not data:
        print("no probe data found")
        return 1

    kinds = sorted({k[0] for k in data})
    sizes = sorted({k[2] for k in data})

    print(f"{'kind':<8}{'arm':<8}{'q':>4}{'target':>8}{'t(1)':>10}{'t(2)':>10}{'t(4)':>10}"
          f"{'1->2':>8}{'2->4':>8}")
    ratios = {}
    for kind in kinds:
        for arm in ("prefix", "suffix"):
            for q in sizes:
                ts = {p: data.get((kind, arm, q, p)) for p in (1, 2, 4)}
                if not all(ts.values()):
                    continue
                r12 = ts[1]["median"] / ts[2]["median"]
                r24 = ts[2]["median"] / ts[4]["median"]
                ratios[(kind, arm, q)] = (r12, r24)
                print(f"{kind:<8}{arm:<8}{q:>4}{ts[1]['target']:>8}"
                      f"{ts[1]['median']:>10.5f}{ts[2]['median']:>10.5f}{ts[4]['median']:>10.5f}"
                      f"{r12:>8.2f}{r24:>8.2f}")

    print("\n--- predictions fixed before the run ---")
    verdicts = []

    def check(name: str, ok: bool | None, detail: str):
        tag = "NO DATA" if ok is None else ("HOLDS" if ok else "FAILS")
        verdicts.append(ok)
        print(f"{name}: {tag} — {detail}")

    p1 = ratios.get(("cphase", "prefix", 23))
    check("P1 CPhase prefix t(1)/t(2) > 2 at q23",
          None if p1 is None else p1[0] > 2.0,
          "n/a" if p1 is None else f"observed {p1[0]:.2f}")

    suffix_cphase = [(q, r[0]) for (k, a, q), r in ratios.items() if k == "cphase" and a == "suffix"]
    check("P2 CPhase suffix t(1)/t(2) <= 2 at every size  [FALSIFIER]",
          None if not suffix_cphase else all(r <= 2.0 for _, r in suffix_cphase),
          "n/a" if not suffix_cphase else
          ", ".join(f"q{q}={r:.2f}" for q, r in sorted(suffix_cphase)))

    suffix_h = [(q, r[0]) for (k, a, q), r in ratios.items() if k == "h" and a == "suffix"]
    check("P3 Hadamard suffix t(1)/t(2) > 1 at every size",
          None if not suffix_h else all(r > 1.0 for _, r in suffix_h),
          "n/a" if not suffix_h else ", ".join(f"q{q}={r:.2f}" for q, r in sorted(suffix_h)))

    prefix_h = [(q, r[0]) for (k, a, q), r in ratios.items() if k == "h" and a == "prefix"]
    check("P4 Hadamard prefix t(1)/t(2) < 1 at every size",
          None if not prefix_h else all(r < 1.0 for _, r in prefix_h),
          "n/a" if not prefix_h else ", ".join(f"q{q}={r:.2f}" for q, r in sorted(prefix_h)))

    # The within-size, same-rank-count contrast is the strongest statement the
    # probe can make: identical circuit, identical size, identical GPU count,
    # differing only in whether the target qubit is a rank-index bit.
    print("\n--- same size, same GPU count, target moved across the boundary ---")
    for kind in kinds:
        for q in sizes:
            for p in (2, 4):
                a = data.get((kind, "suffix", q, p))
                b = data.get((kind, "prefix", q, p))
                if a and b:
                    print(f"{kind} q{q} p{p}: suffix-target {a['median']:.5f}s vs "
                          f"prefix-target {b['median']:.5f}s -> {b['median'] / a['median']:.1f}x")

    known = [v for v in verdicts if v is not None]
    if known and all(known):
        print("\nAll evaluated predictions hold: the target-placement explanation stands.")
    elif known:
        print("\nAt least one prediction FAILED — the explanation is wrong as stated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
