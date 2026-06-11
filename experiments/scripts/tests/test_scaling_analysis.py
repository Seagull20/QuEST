import csv
import math
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import scaling_analysis as scaling


def write_tsv(path, fieldnames, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class ScalingAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)

    def test_qft_logical_gate_count_matches_quest_full_qft(self):
        self.assertEqual(scaling.qft_logical_gate_count(28), 420)

    def test_load_samples_filters_qft_total_and_expands_shared_endpoint(self):
        raw = self.root / "qft_p4_q28.tsv"
        fields = [
            "benchmark",
            "num_qubits",
            "rep",
            "warmup",
            "status",
            "stage_label",
            "total_time_s",
        ]
        write_tsv(
            raw,
            fields,
            [
                {"benchmark": "qft", "num_qubits": 28, "rep": 0, "warmup": 1, "status": "PASS", "stage_label": "api_full_qft", "total_time_s": 9},
                {"benchmark": "qft", "num_qubits": 28, "rep": 0, "warmup": 1, "status": "PASS", "stage_label": "total", "total_time_s": 9},
                {"benchmark": "qft", "num_qubits": 28, "rep": 1, "warmup": 0, "status": "PASS", "stage_label": "api_full_qft", "total_time_s": 8},
                {"benchmark": "qft", "num_qubits": 28, "rep": 1, "warmup": 0, "status": "PASS", "stage_label": "total", "total_time_s": 8},
            ],
        )
        manifest = [
            {
                "point_id": "qft_p4_q28",
                "benchmark": "qft",
                "gate_kind": "",
                "num_qubits": "28",
                "mpi_ranks": "4",
                "gpus": "4",
                "slurm_nodes": "1",
                "scale_membership": "strong,weak",
                "source_file": raw.name,
                "profile_point": "qft_p4_q28",
            }
        ]

        rows = scaling.load_samples(self.root, manifest)

        self.assertEqual(len(rows), 4)
        self.assertEqual({row["scale_type"] for row in rows}, {"strong", "weak"})
        self.assertEqual(sum(row["warmup"] == 1 for row in rows), 2)
        self.assertTrue(all(row["logical_gate_count"] == 420 for row in rows))
        self.assertTrue(all(row["local_qubits"] == 26 for row in rows))
        self.assertTrue(all(row["source_point_id"] == "qft_p4_q28" for row in rows))

    def test_summaries_compute_strong_and_weak_metrics_from_medians(self):
        rows = []
        for scale_type, ranks, qubits, times, gate_count in [
            ("strong", 1, 28, [8.0, 10.0], 420),
            ("strong", 2, 28, [4.0, 5.0], 420),
            ("weak", 1, 26, [5.0], 364),
            ("weak", 2, 27, [6.25], 392),
        ]:
            for rep, duration in enumerate(times):
                rows.append(
                    {
                        "source_point_id": f"qft_{scale_type}_{ranks}",
                        "scale_type": scale_type,
                        "benchmark": "qft",
                        "gate_kind": "",
                        "num_qubits": qubits,
                        "local_qubits": qubits - int(math.log2(ranks)),
                        "local_amplitudes": 1 << (qubits - int(math.log2(ranks))),
                        "logical_gate_count": gate_count,
                        "mpi_ranks": ranks,
                        "gpus": ranks,
                        "slurm_nodes": 1,
                        "rep": rep,
                        "warmup": 0,
                        "status": "PASS",
                        "total_time_s": duration,
                        "time_per_gate_s": duration / gate_count,
                        "source_file": "fixture.tsv",
                    }
                )

        summaries = scaling.summarize_samples(rows)
        by_key = {(row["scale_type"], row["mpi_ranks"]): row for row in summaries}

        self.assertAlmostEqual(by_key[("strong", 2)]["speedup"], 2.0)
        self.assertAlmostEqual(by_key[("strong", 2)]["parallel_efficiency"], 1.0)
        self.assertAlmostEqual(by_key[("strong", 2)]["parallel_overhead_s"], 0.0)
        self.assertAlmostEqual(by_key[("weak", 2)]["weak_efficiency"], 0.8)
        self.assertAlmostEqual(by_key[("weak", 2)]["weak_slowdown"], 1.25)
        self.assertAlmostEqual(by_key[("weak", 2)]["normalized_per_gate_efficiency"], (5.0 / 364) / (6.25 / 392))

    def test_failed_point_is_preserved_without_fabricated_speedup(self):
        rows = [
            {
                "source_point_id": "qft_p2_q28",
                "scale_type": "strong",
                "benchmark": "qft",
                "gate_kind": "",
                "num_qubits": 28,
                "local_qubits": 27,
                "local_amplitudes": 1 << 27,
                "logical_gate_count": 420,
                "mpi_ranks": 2,
                "gpus": 2,
                "slurm_nodes": 1,
                "rep": 0,
                "warmup": 0,
                "status": "FAIL",
                "total_time_s": 7.0,
                "time_per_gate_s": 7.0 / 420,
                "source_file": "failed.tsv",
            }
        ]

        summary = scaling.summarize_samples(rows)[0]

        self.assertEqual(summary["summary_status"], "FAIL")
        self.assertEqual(summary["sample_count"], 0)
        self.assertTrue(math.isnan(summary["speedup"]))

    def test_validation_requires_expected_passed_warmup_and_measured_rows(self):
        manifest = [{"point_id": "qft_p1_q28"}]
        valid = []
        for rep in range(6):
            valid.append(
                {
                    "source_point_id": "qft_p1_q28",
                    "rep": rep,
                    "warmup": 1 if rep == 0 else 0,
                    "status": "PASS",
                }
            )

        scaling.validate_samples(valid, manifest, expected_reps=5, expected_warmup=1)
        invalid = [dict(row) for row in valid]
        invalid[-1]["status"] = "FAIL"
        with self.assertRaisesRegex(ValueError, "non-PASS"):
            scaling.validate_samples(invalid, manifest, expected_reps=5, expected_warmup=1)

    def test_profile_summary_joins_metadata_and_expands_shared_endpoint(self):
        profile_path = self.root / "procedure_breakdown.tsv"
        write_tsv(
            profile_path,
            [
                "point",
                "benchmark",
                "num_qubits",
                "mpi_ranks",
                "procedure_wall_time_s",
                "communication_pct",
                "rank_imbalance_pct",
            ],
            [
                {
                    "point": "qft_p4_q28",
                    "benchmark": "qft",
                    "num_qubits": 28,
                    "mpi_ranks": 4,
                    "procedure_wall_time_s": 11.0,
                    "communication_pct": 70.0,
                    "rank_imbalance_pct": 3.0,
                }
            ],
        )
        manifest = [
            {
                "point_id": "qft_p4_q28",
                "benchmark": "qft",
                "gate_kind": "",
                "num_qubits": "28",
                "mpi_ranks": "4",
                "gpus": "4",
                "slurm_nodes": "1",
                "scale_membership": "strong,weak",
                "source_file": "qft.tsv",
                "profile_point": "qft_p4_q28",
            }
        ]

        rows = scaling.load_profile_summary(profile_path, manifest)

        self.assertEqual(len(rows), 2)
        self.assertEqual({row["scale_type"] for row in rows}, {"strong", "weak"})
        self.assertTrue(all(row["source_point_id"] == "qft_p4_q28" for row in rows))

    def test_highlights_report_threshold_crossings_and_profile_growth(self):
        summaries = [
            {
                "scale_type": "strong",
                "benchmark": "qft",
                "gate_kind": "",
                "mpi_ranks": 2,
                "speedup": 0.8,
                "parallel_efficiency": 0.4,
                "weak_slowdown": math.nan,
                "cv": 0.2,
            },
            {
                "scale_type": "weak",
                "benchmark": "random",
                "gate_kind": "",
                "mpi_ranks": 4,
                "speedup": math.nan,
                "parallel_efficiency": math.nan,
                "weak_slowdown": 1.4,
                "cv": 0.02,
            },
        ]
        profiles = [
            {"scale_type": "strong", "benchmark": "qft", "gate_kind": "", "mpi_ranks": "1", "communication_pct": "20", "rank_imbalance_pct": "2"},
            {"scale_type": "strong", "benchmark": "qft", "gate_kind": "", "mpi_ranks": "4", "communication_pct": "35", "rank_imbalance_pct": "12"},
        ]

        text = scaling.generate_highlights(summaries, profiles)

        self.assertIn("negative scaling", text)
        self.assertIn("parallel efficiency 40.0%", text)
        self.assertIn("weak slowdown 1.400x", text)
        self.assertIn("communication share increased by 15.0 percentage points", text)
        self.assertIn("rank imbalance 12.0%", text)
        self.assertIn("timing CV 20.0%", text)

    def test_analyse_campaign_writes_samples_summary_profile_and_highlights(self):
        raw = self.root / "qft_p1_q28.tsv"
        write_tsv(
            raw,
            ["benchmark", "num_qubits", "rep", "warmup", "status", "stage_label", "total_time_s"],
            [
                {"benchmark": "qft", "num_qubits": 28, "rep": 0, "warmup": 1, "status": "PASS", "stage_label": "total", "total_time_s": 10},
                {"benchmark": "qft", "num_qubits": 28, "rep": 1, "warmup": 0, "status": "PASS", "stage_label": "total", "total_time_s": 8},
            ],
        )
        manifest_path = self.root / "point_manifest.tsv"
        write_tsv(
            manifest_path,
            ["point_id", "benchmark", "gate_kind", "num_qubits", "mpi_ranks", "gpus", "slurm_nodes", "scale_membership", "source_file", "profile_point"],
            [
                {
                    "point_id": "qft_p1_q28",
                    "benchmark": "qft",
                    "gate_kind": "",
                    "num_qubits": 28,
                    "mpi_ranks": 1,
                    "gpus": 1,
                    "slurm_nodes": 1,
                    "scale_membership": "strong",
                    "source_file": raw.name,
                    "profile_point": "qft_p1_q28",
                }
            ],
        )
        write_tsv(
            self.root / "procedure_breakdown.tsv",
            ["point", "benchmark", "num_qubits", "mpi_ranks", "communication_pct", "rank_imbalance_pct"],
            [{"point": "qft_p1_q28", "benchmark": "qft", "num_qubits": 28, "mpi_ranks": 1, "communication_pct": 0, "rank_imbalance_pct": 0}],
        )

        paths = scaling.analyse_campaign(self.root, manifest_path)

        self.assertEqual(
            set(paths),
            {"samples", "summary", "profile_summary", "highlights"},
        )
        for path in paths.values():
            self.assertTrue(path.is_file(), path)
        self.assertEqual(len(scaling.read_tsv(paths["samples"])), 2)
        self.assertEqual(len(scaling.read_tsv(paths["summary"])), 1)
        self.assertEqual(len(scaling.read_tsv(paths["profile_summary"])), 1)


if __name__ == "__main__":
    unittest.main()
