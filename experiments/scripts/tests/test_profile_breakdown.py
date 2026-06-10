import csv
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import profile_breakdown as breakdown


def global_process_id(pid):
    return pid << 24


def global_thread_id(pid, tid=1):
    return global_process_id(pid) | tid


class IntervalTests(unittest.TestCase):
    def test_merge_intervals_coalesces_overlap_and_adjacency(self):
        self.assertEqual(
            breakdown.merge_intervals([(20, 25), (0, 10), (5, 15), (15, 18)]),
            [(0, 18), (20, 25)],
        )

    def test_subtract_intervals_removes_overlapping_segments(self):
        self.assertEqual(
            breakdown.subtract_intervals([(0, 20)], [(5, 10), (12, 15)]),
            [(0, 5), (10, 12), (15, 20)],
        )

    def test_classification_prioritises_communication_overlap(self):
        result = breakdown.classify_intervals(
            procedure=[(0, 100)],
            execution=[(10, 80)],
            communication=[(20, 40)],
            pack_kernels=[(18, 25)],
            simulation_kernels=[(12, 30), (35, 50), (85, 95)],
        )

        self.assertEqual(result["procedure_ns"], 100)
        self.assertEqual(result["communication_ns"], 22)
        self.assertEqual(result["computation_ns"], 16)
        self.assertEqual(result["overlap_ns"], 17)
        self.assertEqual(result["others_ns"], 62)


class SqliteProfileTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.sqlite_path = Path(self.tempdir.name) / "profile.sqlite"
        self.rank_tsv = Path(self.tempdir.name) / "procedure_breakdown_rank.tsv"
        self.summary_tsv = Path(self.tempdir.name) / "procedure_breakdown.tsv"
        self._create_profile_database()

    def _create_profile_database(self):
        connection = sqlite3.connect(self.sqlite_path)
        cursor = connection.cursor()
        cursor.executescript(
            """
            CREATE TABLE StringIds (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE NVTX_EVENTS (
                start INTEGER NOT NULL,
                end INTEGER,
                text TEXT,
                textId INTEGER,
                globalTid INTEGER NOT NULL
            );
            CREATE TABLE MPI_RANKS (globalTid INTEGER NOT NULL, rank INTEGER NOT NULL);
            CREATE TABLE MPI_P2P_EVENTS (
                start INTEGER NOT NULL,
                end INTEGER NOT NULL,
                globalTid INTEGER NOT NULL,
                size INTEGER NOT NULL,
                textId INTEGER
            );
            CREATE TABLE CUPTI_ACTIVITY_KIND_KERNEL (
                start INTEGER NOT NULL,
                end INTEGER NOT NULL,
                globalPid INTEGER NOT NULL,
                demangledName INTEGER,
                shortName INTEGER
            );
            """
        )

        strings = {
            1: "quest.procedure",
            2: "quest.execution.timed",
            3: "quest.communication.exchange.cpu_staged",
            4: "quest.communication.exchange.direct_gpu",
            5: "kernel_statevec_applyHadamard",
            6: "kernel_statevec_packAmpsIntoBuffer",
            7: "MPI_Isend",
            8: "MPI_Irecv",
        }
        cursor.executemany("INSERT INTO StringIds VALUES (?, ?)", strings.items())

        rank0_tid = global_thread_id(101)
        rank1_tid = global_thread_id(202)
        cursor.executemany(
            "INSERT INTO MPI_RANKS VALUES (?, ?)",
            [(rank0_tid, 0), (rank1_tid, 1)],
        )
        cursor.executemany(
            "INSERT INTO NVTX_EVENTS VALUES (?, ?, ?, ?, ?)",
            [
                (0, 100, None, 1, rank0_tid),
                (10, 80, None, 2, rank0_tid),
                (20, 40, None, 3, rank0_tid),
                (0, 120, None, 1, rank1_tid),
                (10, 100, None, 2, rank1_tid),
                (40, 60, None, 4, rank1_tid),
            ],
        )
        cursor.executemany(
            "INSERT INTO CUPTI_ACTIVITY_KIND_KERNEL VALUES (?, ?, ?, ?, ?)",
            [
                (12, 30, global_process_id(101), 5, 5),
                (18, 25, global_process_id(101), 6, 6),
                (35, 50, global_process_id(101), 5, 5),
                (20, 50, global_process_id(202), 5, 5),
            ],
        )
        cursor.executemany(
            "INSERT INTO MPI_P2P_EVENTS VALUES (?, ?, ?, ?, ?)",
            [
                (25, 30, rank0_tid, 64, 7),
                (25, 30, rank0_tid, 64, 8),
                (45, 50, rank1_tid, 128, 7),
            ],
        )
        connection.commit()
        connection.close()

    def test_parse_profile_writes_rank_and_critical_rank_rows(self):
        rank_rows, summary_row = breakdown.parse_profile(
            sqlite_path=self.sqlite_path,
            point="qft",
            benchmark="qft",
            num_qubits=28,
            env_num_nodes=2,
        )

        self.assertEqual([row["rank"] for row in rank_rows], [0, 1])
        self.assertEqual(summary_row["critical_rank"], 1)
        self.assertEqual(summary_row["mpi_send_calls"], 2)
        self.assertEqual(summary_row["mpi_send_bytes"], 192)
        self.assertEqual(summary_row["communication_path"], "mixed")
        self.assertAlmostEqual(
            summary_row["computation_pct"]
            + summary_row["communication_pct"]
            + summary_row["others_pct"],
            100.0,
        )

    def test_append_rows_writes_stable_tsv_schema(self):
        rank_rows, summary_row = breakdown.parse_profile(
            sqlite_path=self.sqlite_path,
            point="qft",
            benchmark="qft",
            num_qubits=28,
            env_num_nodes=2,
        )
        breakdown.append_tsv(self.rank_tsv, breakdown.RANK_FIELDS, rank_rows)
        breakdown.append_tsv(self.summary_tsv, breakdown.SUMMARY_FIELDS, [summary_row])

        with self.rank_tsv.open(newline="") as handle:
            written_rank_rows = list(csv.DictReader(handle, delimiter="\t"))
        with self.summary_tsv.open(newline="") as handle:
            written_summary_rows = list(csv.DictReader(handle, delimiter="\t"))

        self.assertEqual(len(written_rank_rows), 2)
        self.assertEqual(written_rank_rows[1]["communication_path"], "direct_gpu")
        self.assertEqual(written_summary_rows[0]["critical_rank"], "1")


if __name__ == "__main__":
    unittest.main()
