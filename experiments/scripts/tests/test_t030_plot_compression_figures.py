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

    def test_runtime_conditions_exclude_cphase_from_speedup_panels(self):
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
                        "source_point_id": "cphase_p4_q28",
                        "benchmark": "gate_micro",
                        "gate_kind": "cphase",
                        "num_qubits": "28",
                        "mpi_ranks": "4",
                        "runtime_speedup_on_vs_off": "1.2",
                    },
                    {
                        "source_point_id": "h_p4_q28",
                        "benchmark": "gate_micro",
                        "gate_kind": "h",
                        "num_qubits": "28",
                        "mpi_ranks": "4",
                        "runtime_speedup_on_vs_off": "12.0",
                    },
                ],
            )

            panels = plots.read_runtime_conditions(root / "runtime_comparison.tsv")

        self.assertNotIn(("gate_micro", "cphase"), panels)
        self.assertEqual(panels[("gate_micro", "h")], [("q28 p4", 12.0)])

    def test_speedup_formula_text_uses_compression_off_over_on(self):
        self.assertIn("speedup", plots.SPEEDUP_FORMULA_TEXT)
        self.assertIn("compression\\ off", plots.SPEEDUP_FORMULA_TEXT)
        self.assertIn("compression\\ on", plots.SPEEDUP_FORMULA_TEXT)
        self.assertIn("\\frac", plots.SPEEDUP_FORMULA_TEXT)

    def test_stage_breakdown_uses_critical_rank_end_to_end_components(self):
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            for mode in ("off", "on"):
                campaign = root / f"campaign_{mode}_corrected"
                campaign.mkdir()
                write_tsv(
                    campaign / "procedure_breakdown.tsv",
                    [
                        "point",
                        "critical_rank",
                    ],
                    [
                        {
                            "point": "qft_p4_q28",
                            "critical_rank": "1",
                        },
                    ],
                )
                write_tsv(
                    campaign / "procedure_breakdown_rank.tsv",
                    [
                        "point",
                        "rank",
                        "procedure_wall_time_s",
                        "computation_time_s",
                    ],
                    [
                        {
                            "point": "qft_p4_q28",
                            "rank": "0",
                            "procedure_wall_time_s": "999.0",
                            "computation_time_s": "999.0",
                        },
                        {
                            "point": "qft_p4_q28",
                            "rank": "1",
                            "procedure_wall_time_s": "40.0",
                            "computation_time_s": "2.0",
                        },
                    ],
                )
                write_tsv(
                    campaign / "communication_breakdown_rank.tsv",
                    [
                        "point",
                        "rank",
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
                            "rank": "0",
                            "d2h_time_s": "100.0",
                            "mpi_time_s": "100.0",
                            "h2d_time_s": "100.0",
                            "compress_time_s": "100.0",
                            "size_exchange_time_s": "100.0",
                            "decompress_time_s": "100.0",
                            "exchange_wall_time_s": "600.0",
                            "send_bytes": "10000",
                        },
                        {
                            "point": "qft_p4_q28",
                            "rank": "1",
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
                            "rank": "1",
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
        self.assertEqual(qft_off["critical_rank"], 1)
        self.assertEqual(qft_off["procedure_wall_time_s"], 40.0)
        self.assertEqual(qft_off["computation_time_s"], 2.0)
        self.assertEqual(qft_off["d2h_time_s"], 5.0)
        self.assertEqual(qft_off["mpi_time_s"], 7.0)
        self.assertEqual(qft_off["send_bytes"], 400.0)
        self.assertAlmostEqual(
            qft_off["other_residual_time_s"],
            40.0 - 2.0 - 5.0 - 7.0 - 9.0 - 0.5 - 0.7 - 0.9,
        )
        self.assertEqual(data[("qft_p4_q28", "on")]["exchange_wall_time_s"], 24.0)


if __name__ == "__main__":
    unittest.main()
