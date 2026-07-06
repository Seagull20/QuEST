import csv
import tempfile
import unittest
from pathlib import Path
import sys


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import t030_compare_compression as compare


def write_tsv(path, fieldnames, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class T030CompareCompressionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.off = self.root / "off"
        self.on = self.root / "on"
        self.out = self.root / "comparison"
        self.off.mkdir()
        self.on.mkdir()

        summary_fields = [
            "source_point_id",
            "scale_type",
            "benchmark",
            "gate_kind",
            "num_qubits",
            "mpi_ranks",
            "summary_status",
            "median_time_s",
        ]
        summary_rows_off = [
            {
                "source_point_id": "qft_p4_q28",
                "scale_type": "strong",
                "benchmark": "qft",
                "gate_kind": "",
                "num_qubits": "28",
                "mpi_ranks": "4",
                "summary_status": "PASS",
                "median_time_s": "8.0",
            }
        ]
        summary_rows_on = [dict(summary_rows_off[0], median_time_s="4.0")]
        write_tsv(self.off / "scaling_summary.tsv", summary_fields, summary_rows_off)
        write_tsv(self.on / "scaling_summary.tsv", summary_fields, summary_rows_on)

        comm_fields = [
            "point",
            "benchmark",
            "num_qubits",
            "mpi_ranks",
            "rank",
            "exchange_id",
            "send_bytes",
            "mpi_time_s",
            "compress_time_s",
            "size_exchange_time_s",
            "decompress_time_s",
            "exchange_wall_time_s",
        ]
        write_tsv(
            self.off / "communication_breakdown_rank.tsv",
            comm_fields,
            [
                {
                    "point": "qft_p4_q28",
                    "benchmark": "qft",
                    "num_qubits": "28",
                    "mpi_ranks": "4",
                    "rank": "0",
                    "exchange_id": "0",
                    "send_bytes": "1024",
                    "mpi_time_s": "2.0",
                    "compress_time_s": "0",
                    "size_exchange_time_s": "0",
                    "decompress_time_s": "0",
                    "exchange_wall_time_s": "3.0",
                }
            ],
        )
        write_tsv(
            self.on / "communication_breakdown_rank.tsv",
            comm_fields,
            [
                {
                    "point": "qft_p4_q28",
                    "benchmark": "qft",
                    "num_qubits": "28",
                    "mpi_ranks": "4",
                    "rank": "0",
                    "exchange_id": "0",
                    "send_bytes": "256",
                    "mpi_time_s": "0.5",
                    "compress_time_s": "0.2",
                    "size_exchange_time_s": "0.1",
                    "decompress_time_s": "0.2",
                    "exchange_wall_time_s": "1.1",
                }
            ],
        )

    def test_compare_campaigns_writes_summary_figures_and_teams_draft(self):
        paths = compare.compare_campaigns(self.off, self.on, self.out)

        self.assertTrue(paths["runtime"].is_file())
        self.assertTrue(paths["communication"].is_file())
        self.assertTrue(paths["runtime_svg"].is_file())
        self.assertTrue(paths["runtime_pdf"].is_file())
        self.assertTrue(paths["teams"].is_file())

        runtime = compare.read_tsv(paths["runtime"])
        self.assertEqual(runtime[0]["runtime_speedup_on_vs_off"], "2.000000")
        self.assertEqual(runtime[0]["runtime_delta_pct"], "-50.000000")

        communication = compare.read_tsv(paths["communication"])
        self.assertEqual(communication[0]["mpi_send_byte_reduction_pct"], "75.000000")
        teams = paths["teams"].read_text(encoding="utf-8")
        self.assertIn("qft_p4_q28", teams)
        self.assertIn("deduplicated single-direction MPI send bytes", teams)
        self.assertNotIn("-13.58", teams)


if __name__ == "__main__":
    unittest.main()
