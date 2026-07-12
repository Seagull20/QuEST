#!/usr/bin/env python3
"""Breakdown figures (Level-0 five-category + communication deepening).

Generic over campaign points since 2026-07-11 (codex cross-review fixes):
  - points discovered from procedure_breakdown.tsv (optionally filtered via
    --points), any number of columns; titles derived from point names
  - unclassified residual is SIGNED: classified stages exceeding the wall are
    reported as over-attribution, never clamped to zero (finding #9)
  - alarm notes name the offending category (overhead / residual /
    over-attribution) instead of a fixed "overhead" prefix (finding #10)
  - inside-bar labels in the absolute row are gated on panel scale, not
    per-bar share, so short ON bars no longer grow crowded labels (finding #11)
  - the caption's attribution-completeness clause is computed from the data,
    not asserted (finding #9 corollary)

Destinations (--dest, PLOTTING.md §6 R5):
  standalone   (default) message suptitle + baked caption + job-id corner
               note — for Teams/deck/any context without a surrounding text
  dissertation descriptive Title-Case on-figure title, NO baked caption, NO
               rendered provenance; emits a LaTeX message-caption skeleton
               (.tex) and a machine-written provenance manifest (.manifest.tsv)
               next to the figure

Input : --off-dir/--on-dir pointing at raw campaign dirs that contain
        procedure_breakdown.tsv, procedure_breakdown_rank.tsv,
        communication_breakdown_rank.tsv
        (legacy: --processed-dir with campaign_{off,on}_corrected/ subdirs)
Output: {basename}_level0{suffix}.{png,pdf,svg}
        {basename}_comm{suffix}.{png,pdf,svg}
        {basename}{suffix}_validation.tsv   (signed residual + alarm flags)
        + in dissertation mode: *.tex caption skeletons, *.manifest.tsv

Provenance (kept OUT of rendered text per PLOTTING.md §6 reader-frame rule):
  taxonomy authority spec 2026-07-05-cpu-staging-tiled-pipeline-quest-spec.md
  §18.1–18.3 (five-category Level-0, communication-only deepening, 5% alarm
  thresholds); rank TSVs carry the MPI_Sendrecv dedup (T-045) in the
  producing pipeline. Rendered captions carry only what a cold reader can
  resolve: definitions, hardware/experiment context, and job IDs (standalone
  mode only; dissertation mode presents no provenance — see the manifest).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

# ---- PLOTTING.md drop-in style ------------------------------------------------
OKABE_ITO = ["#0072B2", "#E69F00", "#009E73", "#D55E00",
             "#CC79A7", "#56B4E9", "#F0E442", "#000000"]
matplotlib.rcParams.update({
    "figure.dpi": 150, "savefig.dpi": 300, "savefig.bbox": "tight",
    "font.family": "sans-serif", "font.size": 10,
    "axes.labelsize": 10, "axes.titlesize": 10,
    "xtick.labelsize": 9, "ytick.labelsize": 9, "legend.fontsize": 9,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "axes.grid.axis": "y", "grid.alpha": 0.3,
    "legend.frameon": False,
})
EDGE = "#333333"
ALARM = 5.0          # % of end-to-end wall (spec §18.2)
OVERATTR_TOL = 0.5   # % of wall: negative residual beyond this is flagged
CIRCUIT_LABEL = {"h": "Hadamard", "cphase": "CPhase", "qft": "QFT",
                 "random": "Random"}

# Level-0 five categories (bottom -> top). color + hatch (never color-only).
LEVEL0 = [
    ("computation_time_s",           "computation",         "#0072B2", ""),
    ("communication_time_s",         "communication",       "#E69F00", "//"),
    ("execution_overhead_time_s",    "execution overhead",  "#CC79A7", "xx"),
    ("lifecycle_time_s",             "lifecycle",           "#009E73", ".."),
    ("unclassified_residual_time_s", "unclassified residual", "#555555", "++"),
]
# Communication deepening substages (bottom -> top of the comm swimlane).
COMM = [
    ("pack_time_s",          "pack",          "#999999", ".."),
    ("d2h_time_s",           "D2H copy",      "#56B4E9", "\\\\"),
    ("compress_time_s",      "compress (GPU)","#CC79A7", "oo"),
    ("size_exchange_time_s", "size exchange", "#000000", "xx"),
    ("mpi_time_s",           "MPI payload",   "#E69F00", "//"),
    ("h2d_time_s",           "H2D copy",      "#009E73", "++"),
    ("decompress_time_s",    "decompress (GPU)", "#D55E00", "**"),
]


def rd(p: Path):
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def fn(x):
    return float(x) if x not in (None, "") else 0.0


def point_title(pt: str) -> str:
    """'h_p4_q29' -> 'Hadamard  q29 / p4' (falls back to the raw name)."""
    m = re.match(r"^([a-z0-9]+)_(p\d+)_(q\d+)$", pt)
    if not m:
        return pt
    circ, part, q = m.groups()
    return f"{CIRCUIT_LABEL.get(circ, circ)}  {q} / {part}"


def load(off_dir: Path, on_dir: Path, only_points=None):
    """Return (data {(point, mode): rec}, ordered point list).

    Point order follows the OFF procedure_breakdown.tsv, filtered to points
    present in both arms (and to --points when given)."""
    data, order = {}, []
    for mode, d in (("off", off_dir), ("on", on_dir)):
        crit = {r["point"]: int(r["critical_rank"]) for r in rd(d / "procedure_breakdown.tsv")}
        if mode == "off":
            order = [r["point"] for r in rd(d / "procedure_breakdown.tsv")]
        prow = {(r["point"], int(r["rank"])): r for r in rd(d / "procedure_breakdown_rank.tsv")}
        comm = defaultdict(lambda: defaultdict(float))
        for r in rd(d / "communication_breakdown_rank.tsv"):
            k = (r["point"], int(r["rank"]))
            for field, *_ in COMM:
                comm[k][field] += fn(r.get(field))
            comm[k]["send_bytes"] += fn(r.get("send_bytes"))
        for pt, cr in crit.items():
            pr = prow.get((pt, cr))
            if pr is None:
                continue
            wall = fn(pr["procedure_wall_time_s"])
            rec = {
                "critical_rank": cr,
                "procedure_wall_time_s": wall,
                "computation_time_s": fn(pr["computation_time_s"]),
                "communication_time_s": fn(pr["communication_time_s"]),
                "execution_overhead_time_s": fn(pr["execution_overhead_time_s"]),
                "lifecycle_time_s": fn(pr["lifecycle_time_s"]),
                "send_bytes": comm[(pt, cr)]["send_bytes"],
            }
            # SIGNED residual: negative = classified stages exceed the wall
            # (over-attribution) and must be surfaced, not clamped (codex #9).
            rec["unclassified_residual_time_s"] = wall - (
                rec["computation_time_s"] + rec["communication_time_s"]
                + rec["execution_overhead_time_s"] + rec["lifecycle_time_s"])
            for field, *_ in COMM:
                rec[field] = comm[(pt, cr)][field]
            data[(pt, mode)] = rec
    pts = [p for p in order if (p, "off") in data and (p, "on") in data]
    if only_points:
        keep = set(only_points)
        pts = [p for p in pts if p in keep]
    return data, pts


def _stack(ax, x, rec, spec, total, as_pct=False, label_min_abs=None):
    """Stack one bar. Inside labels: pct row keeps the per-bar >=6% rule;
    abs row uses label_min_abs (panel-scale gate, codex #11)."""
    wall = rec["procedure_wall_time_s"]
    bottom = 0.0
    for field, _lbl, color, hatch in spec:
        v = rec[field]
        if v <= 0:      # negative residual is annotated, never drawn
            continue
        h = 100.0 * v / total if as_pct else v
        ax.bar(x, h, 0.62, bottom=bottom, color=color, hatch=hatch,
               edgecolor=EDGE, linewidth=0.4)
        labeled = (v / wall >= 0.06) if as_pct else (
            label_min_abs is not None and v >= label_min_abs)
        if labeled:
            txt = f"{100*v/wall:.0f}%" if as_pct else (f"{v:.1f}" if v >= 1 else f"{v:.2f}")
            ax.text(x, bottom + h / 2, txt, ha="center", va="center",
                    fontsize=7.2, color="white" if color in ("#0072B2", "#D55E00", "#555555", "#000000") else "#111111")
        bottom += h
    return bottom


def alarm_flags(rec):
    """Named consistency alarms (codex #10): category kept in the string."""
    wall = rec["procedure_wall_time_s"]
    if not wall:
        return []
    flags = []
    ov = 100.0 * rec["execution_overhead_time_s"] / wall
    if ov > ALARM:
        flags.append(f"overhead {ov:.1f}%")
    res = 100.0 * rec["unclassified_residual_time_s"] / wall
    if res > ALARM:
        flags.append(f"residual {res:.1f}%")
    elif res < -OVERATTR_TOL:
        flags.append(f"over-attribution {abs(res):.1f}%")
    return flags


def residual_clause(data, points):
    """Data-driven completeness statement (never asserted blindly)."""
    worst = max(abs(data[(pt, m)]["unclassified_residual_time_s"])
                for pt in points for m in ("off", "on"))
    if worst < 0.05:
        return " — 0.0 s in all panels, so the attribution is complete."
    return f" — at most {worst:.1f} s in any panel."


def fig_width(n):
    return 3.45 * n + 1.15


def fig_level0(data, points, out_dir, ctx, args):
    n = len(points)
    fig, axes = plt.subplots(2, n, figsize=(fig_width(n), 7.4 if args.dest == "standalone" else 7.2),
                             sharex="col", squeeze=False)
    if args.dest == "standalone":
        fig.suptitle(
            "Where the runtime goes, compression OFF vs ON: every second of the run is attributed\n"
            + ctx, fontsize=11.5, y=0.995)
    else:
        fig.suptitle("End-to-End Runtime Breakdown by Cost Category and Compression Mode",
                     fontsize=12, y=0.975)

    for col, pt in enumerate(points):
        for row, as_pct in enumerate((False, True)):
            ax = axes[row][col]
            maxwall = max(data[(pt, m)]["procedure_wall_time_s"] for m in ("off", "on"))
            for i, mode in enumerate(("off", "on")):
                rec = data[(pt, mode)]
                _stack(ax, i, rec, LEVEL0, rec["procedure_wall_time_s"],
                       as_pct=as_pct, label_min_abs=0.045 * maxwall)
                if not as_pct:
                    ax.text(i, rec["procedure_wall_time_s"],
                            f"{rec['procedure_wall_time_s']:.1f}s", ha="center",
                            va="bottom", fontsize=8, fontweight="bold")
            ax.set_xticks([0, 1]); ax.set_xticklabels(["OFF", "ON"])
            ax.grid(axis="y", alpha=0.25); ax.set_axisbelow(True)
            if row == 0:
                ax.set_title(point_title(pt), fontsize=10.5)
                # Temporary CPhase taxonomy note removed 2026-07-12: the
                # critical-rank classification fix landed, so the panels no
                # longer carry the anomaly it disclosed.
                ax.set_ylim(0, maxwall * 1.15)
            if as_pct:
                ax.set_ylim(0, 108)
        axes[0][col].set_xlabel("")
    axes[0][0].set_ylabel("wall time of slowest rank (s)", fontsize=9.5)
    axes[1][0].set_ylabel("share of wall time (%)", fontsize=9.5)
    for c in range(n):
        axes[1][c].set_xlabel("compression mode", fontsize=9)

    handles = [Patch(facecolor=c, hatch=h, edgecolor=EDGE, label=l) for _, l, c, h in LEVEL0]
    fig.legend(handles=handles, loc="lower center", ncol=5, fontsize=8.5,
               bbox_to_anchor=(0.5, 0.0))
    if args.dest == "standalone":
        lifec = [data[(pt, m)]["lifecycle_time_s"] for pt in points for m in ("off", "on")]
        lif_txt = f"{min(lifec):.0f}–{max(lifec):.0f} s" if max(lifec) - min(lifec) >= 0.5 else f"~{max(lifec):.0f} s"
        fig.text(0.5, 0.945,
                 "Compression mainly removes communication time; fast ON bars expose the roughly constant lifecycle start-up.",
                 ha="center", va="top", fontsize=8.4)
        fig.text(0.5, 0.918,
                 "Top: seconds. Bottom: the same runs as shares of wall time. 'Execution overhead' is gate-execution time outside "
                 "computation and communication; 'unclassified residual' is wall time outside all categories"
                 + residual_clause(data, points),
                 ha="center", va="top", fontsize=7.7)
        fig.text(0.5, 0.895,
                 f"Lifecycle is approximately constant environment start-up ({lif_txt}), so its share grows when compression shortens the run.",
                 ha="center", va="top", fontsize=7.7)
        fig.text(0.995, 0.004,
                 f"runs: job {args.job_off} (OFF) / {args.job_on} (ON), one profiled repetition per point",
                 ha="right", fontsize=6.8, color="#555555")
        fig.subplots_adjust(left=0.075, right=0.99, bottom=0.115, top=0.835,
                            hspace=0.13, wspace=0.18)
    else:
        fig.subplots_adjust(left=0.075, right=0.99, bottom=0.105, top=0.90,
                            hspace=0.13, wspace=0.12)
    base = f"{args.basename}_level0{args.suffix}"
    for ext in ("svg", "png", "pdf"):
        fig.savefig(out_dir / f"{base}.{ext}")
    plt.close(fig)
    if args.dest == "dissertation":
        write_caption_tex_level0(data, points, out_dir / f"{base}.tex", base, ctx)
        write_manifest(out_dir, base, data, points, args,
                       inputs=(args.off_dir, args.on_dir))


def _comm_segments(rec, min_frac=0.02):
    """Return (segments, comm_sum, merged_names): substages >= min_frac of the
    comm sum kept individually; everything smaller folded into one
    'other copies' slice so no sub-pixel sliver is drawn. comm_sum is the TRUE
    sum (may be 0.0) — division guards happen at the call sites, never in a
    rendered label."""
    csum = sum(rec[f] for f, *_ in COMM)
    denom = csum or 1.0
    kept, merged, merged_names = [], 0.0, []
    for field, lbl, color, hatch in COMM:
        v = rec[field]
        if v <= 0:
            continue
        if v / denom >= min_frac:
            kept.append((field, lbl, color, hatch, v))
        else:
            merged += v
            merged_names.append(lbl.split()[0])
    if merged > 0:
        kept.append(("_other", "other copies", "#BBBBBB", "..", merged))
    return kept, csum, merged_names


def fig_comm(data, points, out_dir, ctx, args):
    # This figure deepens communication; points with no inter-rank
    # communication (e.g. diagonal-gate circuits) have nothing to deepen and
    # are skipped — the Level-0 figure still shows their (zero) comm bar.
    skipped = [pt for pt in points
               if max(sum(data[(pt, m)][f] for f, *_ in COMM) for m in ("off", "on")) < 0.01]
    points = [pt for pt in points if pt not in skipped]
    if skipped:
        print("fig_comm: skipped zero-communication points:", ",".join(skipped))
    if not points:
        print("fig_comm: no points with communication — figure not produced")
        return
    n = len(points)
    fig, axes = plt.subplots(2, n, figsize=(fig_width(n), 8.0 if args.dest == "standalone" else 7.0),
                             sharex="col", squeeze=False)
    if args.dest == "standalone":
        fig.suptitle(
            "Inside the communication time, compression OFF vs ON: which stage the seconds go to\n"
            + ctx, fontsize=11.5, y=0.995)
    else:
        fig.suptitle("Communication-Stage Breakdown by Compression Mode",
                     fontsize=12, y=0.975)
    for col, pt in enumerate(points):
        for row, as_pct in enumerate((False, True)):
            ax = axes[row][col]
            panelmax = max(sum(data[(pt, m)][f] for f, *_ in COMM) for m in ("off", "on")) or 1.0
            for i, mode in enumerate(("off", "on")):
                rec = data[(pt, mode)]
                segs, csum, _ = _comm_segments(rec)
                # abs row: skip inside-labels on short bars (norm row carries them)
                label_ok = as_pct or (csum >= 0.30 * panelmax)
                bottom = 0.0
                for _field, _lbl, color, hatch, v in segs:
                    h = 100.0 * v / csum if as_pct else v
                    ax.bar(i, h, 0.62, bottom=bottom, color=color, hatch=hatch,
                           edgecolor=EDGE, linewidth=0.4)
                    frac = v / csum
                    if label_ok and frac >= (0.05 if as_pct else 0.10):
                        txt = f"{100*frac:.0f}%" if as_pct else (f"{v:.1f}" if v >= 1 else f"{v:.2f}")
                        ax.text(i, bottom + h / 2, txt, ha="center", va="center",
                                fontsize=7.0, color="white" if color in ("#000000", "#D55E00", "#56B4E9") else "#111111")
                    bottom += h
                if not as_pct:
                    ax.text(i, bottom, f"Σ{csum:.1f}s\n{rec['send_bytes']/1e9:.2f} GB",
                            ha="center", va="bottom", fontsize=7.2, color="#333333")
            ax.set_xticks([0, 1]); ax.set_xticklabels(["OFF", "ON"])
            ax.grid(axis="y", alpha=0.25); ax.set_axisbelow(True)
            if row == 0:
                ax.set_title(point_title(pt), fontsize=10.5)
                ax.set_ylim(0, panelmax * 1.24)
            else:
                ax.set_ylim(0, 108)
                ax.set_xlabel("compression mode", fontsize=9)
    axes[0][0].set_ylabel("stage time (s)\n(stages sum to ≈ total communication time)", fontsize=9)
    axes[1][0].set_ylabel("share of communication (%)\n(each bar normalized to 100%)", fontsize=9)

    legend_specs = [(l, c, h) for _, l, c, h in COMM] + [("other copies", "#BBBBBB", "..")]
    handles = [Patch(facecolor=c, hatch=h, edgecolor=EDGE, label=l) for l, c, h in legend_specs]
    fig.legend(handles=handles, loc="lower center", ncol=8, fontsize=8, bbox_to_anchor=(0.5, 0.0))
    if args.dest == "standalone":
        fig.text(0.5, 0.945,
                 "Compression ON replaces most raw-payload MPI time with smaller GPU compression stages; the payload (GB) is shown above each bar.",
                 ha="center", va="top", fontsize=8.4)
        fig.text(0.5, 0.918,
                 "Top: seconds and stage total Σ. Bottom: the same communication stages normalized to 100%. Stages below 2% are folded into 'other copies'.",
                 ha="center", va="top", fontsize=7.8)
        fig.text(0.5, 0.895,
                 "Computation, start-up, and execution overhead are outside this communication-only view.",
                 ha="center", va="top", fontsize=7.8)
        fig.text(0.995, 0.004,
                 f"runs: job {args.job_off} (OFF) / {args.job_on} (ON), one profiled repetition per point",
                 ha="right", fontsize=6.8, color="#555555")
        fig.subplots_adjust(left=0.08, right=0.99, bottom=0.135, top=0.835, hspace=0.12, wspace=0.2)
    else:
        fig.subplots_adjust(left=0.08, right=0.99, bottom=0.12, top=0.90, hspace=0.12, wspace=0.2)
    base = f"{args.basename}_comm{args.suffix}"
    for ext in ("svg", "png", "pdf"):
        fig.savefig(out_dir / f"{base}.{ext}")
    plt.close(fig)
    if args.dest == "dissertation":
        write_caption_tex_comm(data, points, out_dir / f"{base}.tex", base)
        write_manifest(out_dir, base, data, points, args,
                       inputs=(args.off_dir, args.on_dir))


# ---- dissertation-mode side files ----------------------------------------------

def write_caption_tex_level0(data, points, path: Path, base, ctx):
    """LaTeX message-caption skeleton: takeaway first (computed numbers),
    then minimal category definitions. Deeper interpretation belongs to the
    body text, provenance to the manifest (PLOTTING.md §6 R5)."""
    walls = "; ".join(
        f"{data[(pt,'off')]['procedure_wall_time_s']:.1f}\\,s to "
        f"{data[(pt,'on')]['procedure_wall_time_s']:.1f}\\,s ({point_title(pt).split()[0]})"
        for pt in points)
    lifec = [data[(pt, m)]["lifecycle_time_s"] for pt in points for m in ("off", "on")]
    lo, hi = min(lifec), max(lifec)
    clause = residual_clause(data, points).rstrip(".")
    path.write_text(
        "% Auto-generated message-caption skeleton — edit the takeaway to fit the\n"
        "% chapter's argument; keep definitions. Provenance: see the manifest.\n"
        "\\begin{figure}[t]\n  \\centering\n"
        f"  \\includegraphics[width=\\linewidth]{{figures/{base}}}\n"
        "  \\caption{The reduction in communication dominates the end-to-end\n"
        f"  speedup: wall time falls from {walls} on the slowest rank, which sets\n"
        "  the wall time. \\emph{Lifecycle} is the approximately constant\n"
        f"  {lo:.1f}--{hi:.1f}\\,s environment start-up cost; \\emph{{execution\n"
        "  overhead} is gate-execution time attributable to neither computation\n"
        "  nor communication (kernel-launch gaps, synchronisation);\n"
        "  \\emph{unclassified residual} is wall time outside every category"
        f"{clause}.\n"
        "  Top row: seconds; bottom row: the same runs as shares of wall time.}\n"
        f"  \\label{{fig:{base.replace('_', '-')}}}\n\\end{{figure}}\n",
        encoding="utf-8")


def write_caption_tex_comm(data, points, path: Path, base):
    path.write_text(
        "% Auto-generated message-caption skeleton — edit the takeaway to fit the\n"
        "% chapter's argument. Provenance: see the manifest.\n"
        "\\begin{figure}[t]\n  \\centering\n"
        f"  \\includegraphics[width=\\linewidth]{{figures/{base}}}\n"
        "  \\caption{With compression enabled, raw-payload MPI transfer time is\n"
        "  largely replaced by the much smaller compress, size-exchange and\n"
        "  decompress stages. Each bar expands the single communication cost of\n"
        "  a run into its stages; the run's other costs lie outside this figure.\n"
        "  Top row: seconds, with the stage total and MPI payload above each\n"
        "  bar; bottom row: the same bars normalized to 100\\%.}\n"
        f"  \\label{{fig:{base.replace('_', '-')}}}\n\\end{{figure}}\n",
        encoding="utf-8")


def write_manifest(out_dir: Path, base, data, points, args, inputs):
    """Machine-written provenance (codex #5–#7): actual producer + invocation,
    script hash, absolute inputs, corroboration status stated honestly."""
    script = Path(__file__).resolve()
    sha = hashlib.sha256(script.read_bytes()).hexdigest()[:16]
    off_dir, on_dir = (Path(p).resolve() for p in inputs)
    rows = [
        ("figure", f"{base}.{{png,pdf,svg}}"),
        ("produced_by", f"{script} " + " ".join(sys.argv[1:])),
        ("script_sha256_16", sha),
        ("script_version", "copy alongside outputs; QuEST-fork landing pending user sign-off"),
        ("generated_utc", datetime.now(timezone.utc).isoformat(timespec="seconds")),
        ("authoritative_dir", str(Path(out_dir).resolve())),
        ("input_off", str(off_dir / "procedure_breakdown_rank.tsv")),
        ("input_on", str(on_dir / "procedure_breakdown_rank.tsv")),
        ("input_comm_off", str(off_dir / "communication_breakdown_rank.tsv")),
        ("input_comm_on", str(on_dir / "communication_breakdown_rank.tsv")),
        ("points", ";".join(points)),
        ("jobs", f"{args.job_off} (compression OFF); {args.job_on} (compression ON)"),
        ("repetitions", "one profiled repetition per point; campaign timing medians (5 reps) in scaling_summary.tsv"),
        ("hardware", f"{args.node}, 4x RTX 2080 Ti, 4 MPI ranks"),
        ("corrections", "MPI_Sendrecv dedup (T-045) applied in the producing pipeline"),
        ("taxonomy", "spec 2026-07-05-cpu-staging-tiled-pipeline-quest-spec.md s18.1-18.3"),
        ("corroborating_source", "profiled walls cross-checked against scaling_summary.tsv 5-rep medians (same campaign, independent timing path); no second independent campaign yet"),
    ]
    with (out_dir / f"{base}.manifest.tsv").open("w", encoding="utf-8") as h:
        h.write("key\tvalue\n")
        for k, v in rows:
            h.write(f"{k}\t{v}\n")


def write_validation(data, points, path: Path):
    fields = ["point", "mode", "critical_rank", "wall_s", "computation_s", "communication_s",
              "execution_overhead_s", "lifecycle_s", "unclassified_residual_s",
              "overhead_pct", "residual_pct", "alarm_flags"]
    with path.open("w", newline="", encoding="utf-8") as h:
        w = csv.DictWriter(h, fieldnames=fields, delimiter="\t", lineterminator="\n")
        w.writeheader()
        for pt in points:
            for mode in ("off", "on"):
                r = data[(pt, mode)]; wall = r["procedure_wall_time_s"]
                w.writerow({
                    "point": pt, "mode": mode, "critical_rank": r["critical_rank"],
                    "wall_s": f"{wall:.4f}",
                    "computation_s": f"{r['computation_time_s']:.4f}",
                    "communication_s": f"{r['communication_time_s']:.4f}",
                    "execution_overhead_s": f"{r['execution_overhead_time_s']:.4f}",
                    "lifecycle_s": f"{r['lifecycle_time_s']:.4f}",
                    "unclassified_residual_s": f"{r['unclassified_residual_time_s']:.4f}",
                    "overhead_pct": f"{100*r['execution_overhead_time_s']/wall:.2f}",
                    "residual_pct": f"{100*r['unclassified_residual_time_s']/wall:.2f}",
                    "alarm_flags": ";".join(alarm_flags(r)) or "-",
                })


def context_line(data, points, node):
    qs = sorted({int(re.search(r"q(\d+)$", pt).group(1)) for pt in points if re.search(r"q(\d+)$", pt)})
    qtxt = f"{qs[0]}" if len(qs) == 1 else f"{qs[0]}–{qs[-1]}"
    return (f"QuEST GPU+MPI, {qtxt} qubits / 4 ranks, 4×RTX 2080 Ti ({node}); "
            "slowest of the 4 ranks shown — it sets the wall time")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--off-dir", type=Path, help="raw campaign dir, compression OFF")
    ap.add_argument("--on-dir", type=Path, help="raw campaign dir, compression ON")
    ap.add_argument("--processed-dir", type=Path,
                    help="legacy layout with campaign_{off,on}_corrected/ subdirs")
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--points", type=str, default="",
                    help="comma-separated point filter (default: all shared points)")
    ap.add_argument("--basename", type=str, default="t030_breakdown")
    ap.add_argument("--suffix", type=str, default="")
    ap.add_argument("--dest", choices=("standalone", "dissertation"), default="standalone")
    ap.add_argument("--job-off", type=str, default=None)
    ap.add_argument("--job-on", type=str, default=None)
    ap.add_argument("--node", type=str, default="landonia01")
    args = ap.parse_args()

    if args.processed_dir:
        args.off_dir = args.processed_dir / "campaign_off_corrected"
        args.on_dir = args.processed_dir / "campaign_on_corrected"
    if not (args.off_dir and args.on_dir):
        ap.error("need --off-dir and --on-dir (or legacy --processed-dir)")
    for side in ("job_off", "job_on"):
        if getattr(args, side) is None:
            m = re.search(r"(\d{6,})", str(getattr(args, side.replace("job_", "") + "_dir")))
            setattr(args, side, m.group(1) if m else "unknown")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    only = [p.strip() for p in args.points.split(",") if p.strip()] or None
    data, points = load(args.off_dir, args.on_dir, only)
    if not points:
        sys.exit("no shared points between the two arms after filtering")
    ctx = context_line(data, points, args.node)
    fig_level0(data, points, args.output_dir, ctx, args)
    fig_comm(data, points, args.output_dir, ctx, args)
    write_validation(data, points, args.output_dir / f"{args.basename}{args.suffix}_validation.tsv")
    print("wrote:", args.output_dir, "points:", ",".join(points))


if __name__ == "__main__":
    main()
