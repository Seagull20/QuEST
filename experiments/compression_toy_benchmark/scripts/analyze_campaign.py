#!/usr/bin/env python3
"""Analyze QuEST compression toy benchmark TSV output."""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path


GENUINE_REQUIRED_PATTERNS = {
    "quest_h_plus_pre_exchange",
    "quest_h_halfzero_pre_exchange",
    "quest_qft",
    "quest_random",
}

PATCH_REQUIRED_PATTERNS = {
    "quest_h_plus_pre_exchange",
    "quest_h_halfzero_pre_exchange",
    "quest_qft",
}


def parse_float(value: str, default: float = math.nan) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def median(values: list[float]) -> float:
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return math.nan
    return statistics.median(finite)


def coefficient_of_variation(values: list[float]) -> float:
    finite = [v for v in values if math.isfinite(v)]
    if len(finite) < 2:
        return 0.0
    mean = statistics.mean(finite)
    if mean == 0:
        return 0.0
    return statistics.stdev(finite) / mean


def iqr_bounds(values: list[float]) -> tuple[float, float]:
    finite = sorted(v for v in values if math.isfinite(v))
    if len(finite) < 4:
        return -math.inf, math.inf
    q1, _, q3 = statistics.quantiles(finite, n=4, method="inclusive")
    iqr = q3 - q1
    return q1 - 1.5 * iqr, q3 + 1.5 * iqr


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def condition_key(row: dict[str, str]) -> tuple[str, str, str, str, str, str]:
    return (
        row.get("source_kind", ""),
        row.get("pattern", ""),
        row.get("checkpoint", ""),
        row.get("exchange_shape", ""),
        row.get("payload_bytes", ""),
        row.get("allocation_id", ""),
    )


def summary_key(row: dict[str, str]) -> tuple[str, str, str, str, str]:
    return (
        row.get("source_kind", ""),
        row.get("pattern", ""),
        row.get("checkpoint", ""),
        row.get("exchange_shape", ""),
        row.get("payload_bytes", ""),
    )


def codec_key(row: dict[str, str]) -> tuple[str, str, str, str, str, str]:
    return (*summary_key(row), row.get("codec", ""))


def successful(row: dict[str, str]) -> bool:
    return row.get("status") == "PASS" and row.get("verify_status") == "PASS"


def write_tsv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def compute_summary(rows: list[dict[str, str]]) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    raw_by_condition: dict[tuple[str, str, str, str, str], list[float]] = defaultdict(list)
    samples_by_codec: dict[tuple[str, str, str, str, str, str], list[dict[str, str]]] = defaultdict(list)

    for row in rows:
        if not successful(row):
            continue
        total = parse_float(row.get("total_s", ""))
        if not math.isfinite(total):
            continue
        if row.get("codec") == "raw":
            raw_by_condition[summary_key(row)].append(total)
        samples_by_codec[codec_key(row)].append(row)

    summary_rows: list[dict[str, object]] = []
    outlier_rows: list[dict[str, object]] = []

    for key, codec_rows in sorted(samples_by_codec.items()):
        source_kind, pattern, checkpoint, exchange_shape, payload_bytes, codec = key
        totals = [parse_float(row.get("total_s", "")) for row in codec_rows]
        comp_ratios = [parse_float(row.get("compression_ratio", "")) for row in codec_rows]
        compressed_sizes = [parse_float(row.get("compressed_bytes", "")) for row in codec_rows]
        raw_median = median(raw_by_condition[(source_kind, pattern, checkpoint, exchange_shape, payload_bytes)])
        codec_median = median(totals)
        speedup = raw_median / codec_median if math.isfinite(raw_median) and math.isfinite(codec_median) and codec_median > 0 else math.nan
        cv = coefficient_of_variation(totals)
        low, high = iqr_bounds(totals)
        outlier_count = 0
        for row in codec_rows:
            value = parse_float(row.get("total_s", ""))
            if math.isfinite(value) and (value < low or value > high):
                outlier_count += 1
                outlier_rows.append({
                    "source_kind": source_kind,
                    "pattern": pattern,
                    "checkpoint": checkpoint,
                    "exchange_shape": exchange_shape,
                    "payload_bytes": payload_bytes,
                    "codec": codec,
                    "allocation_id": row.get("allocation_id", ""),
                    "total_s": value,
                    "iqr_low": low,
                    "iqr_high": high,
                })
        summary_rows.append({
            "source_kind": source_kind,
            "pattern": pattern,
            "checkpoint": checkpoint,
            "exchange_shape": exchange_shape,
            "payload_bytes": payload_bytes,
            "codec": codec,
            "samples": len(codec_rows),
            "median_total_s": codec_median,
            "raw_median_total_s": raw_median,
            "speedup_vs_raw_median": speedup,
            "median_compression_ratio": median(comp_ratios),
            "median_compressed_bytes": median(compressed_sizes),
            "cv_total_s": cv,
            "outlier_count": outlier_count,
        })

    condition_rows = compute_condition_verdicts(summary_rows, rows)
    return summary_rows, condition_rows, outlier_rows


def compute_condition_verdicts(summary_rows: list[dict[str, object]], rows: list[dict[str, str]]) -> list[dict[str, object]]:
    allocations_by_codec: dict[tuple[str, str, str, str, str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if not successful(row) or row.get("codec") == "raw":
            continue
        allocations_by_codec[codec_key(row)][row.get("allocation_id", "")].append(parse_float(row.get("total_s", "")))

    raw_allocations: dict[tuple[str, str, str, str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if not successful(row) or row.get("codec") != "raw":
            continue
        raw_allocations[summary_key(row)][row.get("allocation_id", "")].append(parse_float(row.get("total_s", "")))

    verdicts = []
    for row in summary_rows:
        if row["codec"] == "raw":
            continue
        speedup = float(row["speedup_vs_raw_median"])
        condition = (
            str(row["source_kind"]),
            str(row["pattern"]),
            str(row["checkpoint"]),
            str(row["exchange_shape"]),
            str(row["payload_bytes"]),
        )
        codec_condition = (*condition, str(row["codec"]))
        allocation_regression = False
        for allocation_id, codec_totals in allocations_by_codec[codec_condition].items():
            raw_totals = raw_allocations[condition].get(allocation_id, [])
            raw_med = median(raw_totals)
            codec_med = median(codec_totals)
            if math.isfinite(raw_med) and math.isfinite(codec_med) and codec_med > 0:
                if raw_med / codec_med < 1.0:
                    allocation_regression = True
        if not math.isfinite(speedup):
            verdict = "NO_RAW_BASELINE"
        elif speedup >= 1.10 and not allocation_regression:
            verdict = "WIN"
        elif speedup > 1.0:
            verdict = "MARGINAL_OR_UNSTABLE"
        else:
            verdict = "NO_BENEFIT"
        verdicts.append({
            **{k: row[k] for k in ["source_kind", "pattern", "checkpoint", "exchange_shape", "payload_bytes", "codec"]},
            "speedup_vs_raw_median": speedup,
            "allocation_regression": int(allocation_regression),
            "condition_verdict": verdict,
        })
    return verdicts


def overall_verdict(condition_rows: list[dict[str, object]]) -> str:
    genuine = [row for row in condition_rows if row["source_kind"] == "genuine"]
    synthetic = [row for row in condition_rows if row["source_kind"] == "synthetic"]

    genuine_winners = [row for row in genuine if row["condition_verdict"] == "WIN"]
    synthetic_winners = [row for row in synthetic if row["condition_verdict"] == "WIN"]
    patterns_with_genuine_wins = {str(row["pattern"]) for row in genuine_winners}

    if PATCH_REQUIRED_PATTERNS.issubset(patterns_with_genuine_wins):
        regressions = [row for row in genuine_winners if int(row["allocation_regression"]) != 0]
        if not regressions:
            return "PATCH_CANDIDATE"
    if genuine_winners:
        return "CONDITIONAL_BENEFIT"
    if synthetic_winners:
        return "SYNTHETIC_ONLY_BENEFIT"
    return "NO_BENEFIT"


def write_conclusion(path: Path, verdict: str, condition_rows: list[dict[str, object]], outlier_rows: list[dict[str, object]]) -> None:
    genuine_wins = [row for row in condition_rows if row["source_kind"] == "genuine" and row["condition_verdict"] == "WIN"]
    synthetic_wins = [row for row in condition_rows if row["source_kind"] == "synthetic" and row["condition_verdict"] == "WIN"]
    with path.open("w") as f:
        f.write("# Compression Toy Campaign Conclusion\n\n")
        f.write(f"- Overall verdict: `{verdict}`\n")
        f.write(f"- Genuine winning conditions: `{len(genuine_wins)}`\n")
        f.write(f"- Synthetic winning conditions: `{len(synthetic_wins)}`\n")
        f.write(f"- Outlier samples flagged: `{len(outlier_rows)}`\n\n")
        if verdict == "PATCH_CANDIDATE":
            f.write("所有 required genuine QuEST-generated conditions 都达到 break-even 以上的稳定收益，可作为 QuEST patch 候选。\n")
        elif verdict == "CONDITIONAL_BENEFIT":
            f.write("收益只出现在部分 genuine QuEST-generated conditions；下一步应只针对这些条件讨论 patch 或 fallback policy。\n")
        elif verdict == "SYNTHETIC_ONLY_BENEFIT":
            f.write("收益只在 synthetic calibration 中出现，不能支持 QuEST patch。\n")
        else:
            f.write("genuine QuEST-generated buffers 未显示足够 end-to-end 收益，不建议进入 QuEST patch。\n")


def analyze(samples: Path, output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = read_rows(samples)
    summary_rows, condition_rows, outlier_rows = compute_summary(rows)
    verdict = overall_verdict(condition_rows)

    write_tsv(output_dir / "summary.tsv", [
        "source_kind", "pattern", "checkpoint", "exchange_shape", "payload_bytes", "codec",
        "samples", "median_total_s", "raw_median_total_s", "speedup_vs_raw_median",
        "median_compression_ratio", "median_compressed_bytes", "cv_total_s", "outlier_count",
    ], summary_rows)
    write_tsv(output_dir / "condition_verdicts.tsv", [
        "source_kind", "pattern", "checkpoint", "exchange_shape", "payload_bytes", "codec",
        "speedup_vs_raw_median", "allocation_regression", "condition_verdict",
    ], condition_rows)
    write_tsv(output_dir / "outliers.tsv", [
        "source_kind", "pattern", "checkpoint", "exchange_shape", "payload_bytes",
        "codec", "allocation_id", "total_s", "iqr_low", "iqr_high",
    ], outlier_rows)
    write_conclusion(output_dir / "conclusion.md", verdict, condition_rows, outlier_rows)
    return {"overall_verdict": verdict, "conditions": condition_rows, "outliers": outlier_rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze QuEST compression toy benchmark samples.")
    parser.add_argument("--samples", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    result = analyze(args.samples, args.output_dir)
    print(f"overall_verdict={result['overall_verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
