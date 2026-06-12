import csv
import math
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import required_experiments_analysis as analysis


def write_tsv(path, fieldnames, rows):
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class RequiredExperimentsAnalysisTests(unittest.TestCase):
    def test_reproducibility_requires_negative_scaling_in_all_allocations(self):
        rows = [
            {"allocation": "1", "mpi_ranks": 1, "median_time_s": 4.0},
            {"allocation": "1", "mpi_ranks": 2, "median_time_s": 6.0},
            {"allocation": "1", "mpi_ranks": 4, "median_time_s": 8.0},
            {"allocation": "2", "mpi_ranks": 1, "median_time_s": 4.2},
            {"allocation": "2", "mpi_ranks": 2, "median_time_s": 5.8},
            {"allocation": "2", "mpi_ranks": 4, "median_time_s": 8.3},
            {"allocation": "3", "mpi_ranks": 1, "median_time_s": 4.1},
            {"allocation": "3", "mpi_ranks": 2, "median_time_s": 6.1},
            {"allocation": "3", "mpi_ranks": 4, "median_time_s": 8.1},
        ]

        verdict = analysis.reproducibility_verdict(rows)

        self.assertTrue(verdict["reproducible_negative_scaling"])
        self.assertEqual(verdict["allocation_count"], 3)

    def test_reproducibility_rejects_one_positive_scaling_allocation(self):
        rows = [
            {"allocation": "1", "mpi_ranks": 1, "median_time_s": 4.0},
            {"allocation": "1", "mpi_ranks": 2, "median_time_s": 6.0},
            {"allocation": "1", "mpi_ranks": 4, "median_time_s": 8.0},
            {"allocation": "2", "mpi_ranks": 1, "median_time_s": 4.2},
            {"allocation": "2", "mpi_ranks": 2, "median_time_s": 3.8},
            {"allocation": "2", "mpi_ranks": 4, "median_time_s": 8.3},
            {"allocation": "3", "mpi_ranks": 1, "median_time_s": 4.1},
            {"allocation": "3", "mpi_ranks": 2, "median_time_s": 6.1},
            {"allocation": "3", "mpi_ranks": 4, "median_time_s": 8.1},
        ]

        verdict = analysis.reproducibility_verdict(rows)

        self.assertFalse(verdict["reproducible_negative_scaling"])

    def test_bootstrap_speedup_is_deterministic_and_detects_robust_crossover(self):
        result = analysis.bootstrap_speedup(
            [10.0, 10.1, 9.9, 10.2, 9.8],
            [4.9, 5.0, 5.1, 4.8, 5.0],
            iterations=1000,
            seed=20260612,
        )

        self.assertGreater(result["median_speedup"], 1.0)
        self.assertGreater(result["ci_low"], 1.0)
        self.assertTrue(result["observed_crossover"])
        self.assertTrue(result["robust_crossover"])

    def test_gate_path_verdict_requires_only_h_four_gpu_communication(self):
        rows = [
            {"gate_kind": "h", "mpi_ranks": 1, "communication_time_s": 0, "aggregate_mpi_send_bytes": 0, "communication_path": "none"},
            {"gate_kind": "h", "mpi_ranks": 4, "communication_time_s": 10, "aggregate_mpi_send_bytes": 1024, "communication_path": "cpu_staged"},
            {"gate_kind": "cphase", "mpi_ranks": 1, "communication_time_s": 0, "aggregate_mpi_send_bytes": 0, "communication_path": "none"},
            {"gate_kind": "cphase", "mpi_ranks": 4, "communication_time_s": 0, "aggregate_mpi_send_bytes": 0, "communication_path": "none"},
        ]

        verdict = analysis.gate_path_verdict(rows)

        self.assertTrue(verdict["communication_only_on_h_p4"])

    def test_analyse_campaign_writes_all_required_outputs(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            campaign = root / "required_experiments_2080ti_test"
            campaign.mkdir()
            run_rows = []
            for allocation in (1, 2, 3):
                run = campaign / f"repro_{allocation}_job{allocation}"
                (run / "timing").mkdir(parents=True)
                run_rows.append({"mode": "repro", "allocation": allocation, "job_id": allocation, "run_dir": run.name, "state": "COMPLETED"})
                manifest = []
                for ranks, value in ((1, 4.0), (2, 6.0), (4, 8.0)):
                    point = f"qft_p{ranks}_q28"
                    manifest.append({"point_id": point, "benchmark": "qft", "gate_kind": "none", "num_qubits": 28, "mpi_ranks": ranks, "gpus": ranks, "slurm_nodes": 1, "source_file": f"timing/{point}.tsv", "profile_selected": 0})
                    write_tsv(run / "timing" / f"{point}.tsv", ["benchmark", "num_qubits", "rep", "warmup", "status", "stage_label", "total_time_s"], [
                        {"benchmark": "qft", "num_qubits": 28, "rep": 0, "warmup": 1, "status": "PASS", "stage_label": "total", "total_time_s": value},
                        *[
                            {"benchmark": "qft", "num_qubits": 28, "rep": rep, "warmup": 0, "status": "PASS", "stage_label": "total", "total_time_s": value}
                            for rep in range(1, 11)
                        ],
                    ])
                write_tsv(run / "point_manifest.tsv", manifest[0].keys(), manifest)

            write_tsv(campaign / "submission_manifest.tsv", run_rows[0].keys(), run_rows)
            outputs = analysis.analyse_required_campaign(campaign, validate=False)

            expected = {
                "qft_repro_allocation_summary.tsv",
                "qft_repro_summary.tsv",
                "qft_size_sweep_summary.tsv",
                "qft_crossover.tsv",
                "gate_path_timing_summary.tsv",
                "gate_path_profile_summary.tsv",
                "required_experiment_answers.md",
            }
            self.assertEqual({path.name for path in outputs.values()}, expected)
            self.assertIn("reproducible", (campaign / "required_experiment_answers.md").read_text(encoding="utf-8").lower())

    def test_validation_rejects_incomplete_campaign(self):
        with tempfile.TemporaryDirectory() as tempdir:
            campaign = Path(tempdir)
            write_tsv(
                campaign / "submission_manifest.tsv",
                ["mode", "allocation", "job_id", "node", "dependency", "run_dir", "state"],
                [],
            )

            with self.assertRaisesRegex(ValueError, "three repro runs"):
                analysis.analyse_required_campaign(campaign, validate=True)


if __name__ == "__main__":
    unittest.main()
