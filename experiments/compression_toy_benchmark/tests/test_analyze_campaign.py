#!/usr/bin/env python3

import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANALYZE = ROOT / "scripts" / "analyze_campaign.py"


def load_module():
    spec = importlib.util.spec_from_file_location("analyze_campaign", ANALYZE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_rows(path, rows):
    fields = [
        "source_kind",
        "pattern",
        "checkpoint",
        "exchange_shape",
        "codec",
        "payload_bytes",
        "compressed_bytes",
        "compression_ratio",
        "d2h_s",
        "compress_s",
        "mpi_s",
        "h2d_s",
        "decompress_s",
        "total_s",
        "fallback_used",
        "verify_status",
        "status",
        "allocation_id",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            full = {field: "" for field in fields}
            full.update(row)
            writer.writerow(full)


class AnalyzeCampaignTests(unittest.TestCase):
    def test_genuine_speedup_drives_patch_recommendation(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            samples = tmp_path / "samples.tsv"
            rows = []
            for pattern in [
                "quest_h_plus_pre_exchange",
                "quest_h_halfzero_pre_exchange",
                "quest_qft",
                "quest_random",
            ]:
                for allocation in ["a1", "a2"]:
                    rows.append({
                        "source_kind": "genuine",
                        "pattern": pattern,
                        "checkpoint": pattern,
                        "exchange_shape": "amps_to_buffers",
                        "codec": "raw",
                        "payload_bytes": "1024",
                        "compressed_bytes": "1024",
                        "compression_ratio": "1.0",
                        "total_s": "10.0",
                        "verify_status": "PASS",
                        "status": "PASS",
                        "allocation_id": allocation,
                    })
                    rows.append({
                        "source_kind": "genuine",
                        "pattern": pattern,
                        "checkpoint": pattern,
                        "exchange_shape": "amps_to_buffers",
                        "codec": "nvcomp_lz4",
                        "payload_bytes": "1024",
                        "compressed_bytes": "512",
                        "compression_ratio": "2.0",
                        "total_s": "8.0",
                        "verify_status": "PASS",
                        "status": "PASS",
                        "allocation_id": allocation,
                    })
            write_rows(samples, rows)
            result = module.analyze(samples, tmp_path)
            self.assertEqual(result["overall_verdict"], "PATCH_CANDIDATE")
            self.assertTrue((tmp_path / "summary.tsv").exists())
            self.assertTrue((tmp_path / "condition_verdicts.tsv").exists())
            self.assertTrue((tmp_path / "conclusion.md").exists())

    def test_synthetic_only_benefit_is_not_patch_candidate(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            samples = tmp_path / "samples.tsv"
            rows = []
            for source_kind, pattern, raw_total, comp_total in [
                ("genuine", "quest_h_plus_pre_exchange", "10.0", "10.5"),
                ("genuine", "quest_qft", "10.0", "10.5"),
                ("synthetic", "zero_sparse", "10.0", "5.0"),
            ]:
                rows.append({
                    "source_kind": source_kind,
                    "pattern": pattern,
                    "checkpoint": pattern,
                    "exchange_shape": "amps_to_buffers",
                    "codec": "raw",
                    "payload_bytes": "1024",
                    "compressed_bytes": "1024",
                    "total_s": raw_total,
                    "verify_status": "PASS",
                    "status": "PASS",
                    "allocation_id": "a1",
                })
                rows.append({
                    "source_kind": source_kind,
                    "pattern": pattern,
                    "checkpoint": pattern,
                    "exchange_shape": "amps_to_buffers",
                    "codec": "nvcomp_lz4",
                    "payload_bytes": "1024",
                    "compressed_bytes": "256",
                    "total_s": comp_total,
                    "verify_status": "PASS",
                    "status": "PASS",
                    "allocation_id": "a1",
                })
            write_rows(samples, rows)
            result = module.analyze(samples, tmp_path)
            self.assertEqual(result["overall_verdict"], "SYNTHETIC_ONLY_BENEFIT")


if __name__ == "__main__":
    unittest.main()
