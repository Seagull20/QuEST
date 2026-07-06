import csv
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import t030_plot_compression_figures as plots


def write_tsv(path, fieldnames, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class T030PlotCompressionFiguresTests(unittest.TestCase):
    def test_runtime_conditions_deduplicate_scaling_membership_rows(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            write_tsv(
                root / "runtime_comparison.tsv",
                [
                    "source_point_id",
                    "benchmark",
                    "gate_kind",
                    "num_qubits",
                    "mpi_ranks",
                    "runtime_speedup_on_vs_off",
                ],
                [
                    {
                        "source_point_id": "qft_p4_q28",
                        "benchmark": "qft",
                        "gate_kind": "",
                        "num_qubits": "28",
                        "mpi_ranks": "4",
                        "runtime_speedup_on_vs_off": "4.5",
                    },
                    {
                        "source_point_id": "qft_p4_q28",
                        "benchmark": "qft",
                        "gate_kind": "",
                        "num_qubits": "28",
                        "mpi_ranks": "4",
                        "runtime_speedup_on_vs_off": "4.5",
                    },
                    {
                        "source_point_id": "qft_p2_q27",
                        "benchmark": "qft",
                        "gate_kind": "",
                        "num_qubits": "27",
                        "mpi_ranks": "2",
                        "runtime_speedup_on_vs_off": "3.0",
                    },
                ],
            )

            panels = plots.read_runtime_conditions(root / "runtime_comparison.tsv")

        self.assertEqual(panels[("qft", "")], [("q27 p2", 3.0), ("q28 p4", 4.5)])

    def test_stage_breakdown_aggregates_off_on_campaign_rows(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            for mode in ("off", "on"):
                campaign = root / f"campaign_{mode}_corrected"
                campaign.mkdir()
                write_tsv(
                    campaign / "communication_breakdown_rank.tsv",
                    [
                        "point",
                        "d2h_time_s",
                        "mpi_time_s",
                        "h2d_time_s",
                        "compress_time_s",
                        "size_exchange_time_s",
                        "decompress_time_s",
                        "exchange_wall_time_s",
                        "send_bytes",
                    ],
                    [
                        {
                            "point": "qft_p4_q28",
                            "d2h_time_s": "1.0",
                            "mpi_time_s": "2.0",
                            "h2d_time_s": "3.0",
                            "compress_time_s": "0.1",
                            "size_exchange_time_s": "0.2",
                            "decompress_time_s": "0.3",
                            "exchange_wall_time_s": "7.0",
                            "send_bytes": "100",
                        },
                        {
                            "point": "qft_p4_q28",
                            "d2h_time_s": "4.0",
                            "mpi_time_s": "5.0",
                            "h2d_time_s": "6.0",
                            "compress_time_s": "0.4",
                            "size_exchange_time_s": "0.5",
                            "decompress_time_s": "0.6",
                            "exchange_wall_time_s": "17.0",
                            "send_bytes": "300",
                        },
                    ],
                )

            data = plots.read_stage_breakdown(root)

        qft_off = data[("qft_p4_q28", "off")]
        self.assertEqual(qft_off["d2h_time_s"], 5.0)
        self.assertEqual(qft_off["mpi_time_s"], 7.0)
        self.assertEqual(qft_off["send_bytes"], 400.0)
        self.assertEqual(data[("qft_p4_q28", "on")]["exchange_wall_time_s"], 24.0)


if __name__ == "__main__":
    unittest.main()
