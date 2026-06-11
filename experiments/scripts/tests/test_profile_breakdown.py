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

    def test_classification_splits_lifecycle_and_execution_overhead(self):
        result = breakdown.classify_intervals(
            procedure=[(0, 100)],
            execution=[(10, 80)],
            communication=[(20, 40)],
            pack_kernels=[(18, 25)],
            simulation_kernels=[(12, 30), (35, 50), (85, 95)],
        )

        self.assertEqual(result["procedure_ns"], 100)
        self.assertEqual(result["execution_ns"], 70)
        self.assertEqual(result["communication_ns"], 22)
        self.assertEqual(result["computation_ns"], 16)
        self.assertEqual(result["execution_overhead_ns"], 32)
        self.assertEqual(result["lifecycle_ns"], 30)
        self.assertEqual(result["others_ns"], 62)

    def test_nearest_rank_percentile(self):
        self.assertEqual(breakdown.percentile([1, 2, 3, 100], 0.5), 2)
        self.assertEqual(breakdown.percentile([1, 2, 3, 100], 0.95), 100)


class SqliteProfileTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.sqlite_path = Path(self.tempdir.name) / "profile.sqlite"
        self.rank_tsv = Path(self.tempdir.name) / "procedure_breakdown_rank.tsv"
        self.summary_tsv = Path(self.tempdir.name) / "procedure_breakdown.tsv"
        self.communication_tsv = Path(self.tempdir.name) / "communication_breakdown_rank.tsv"
        self.computation_tsv = Path(self.tempdir.name) / "computation_breakdown_rank.tsv"
        self.lifecycle_tsv = Path(self.tempdir.name) / "lifecycle_breakdown_rank.tsv"
        self.runtime_tsv = Path(self.tempdir.name) / "cuda_runtime_summary_rank.tsv"
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
                remoteRank INTEGER,
                textId INTEGER
            );
            CREATE TABLE MPI_START_WAIT_EVENTS (
                start INTEGER NOT NULL,
                end INTEGER NOT NULL,
                globalTid INTEGER NOT NULL,
                textId INTEGER
            );
            CREATE TABLE CUPTI_ACTIVITY_KIND_RUNTIME (
                start INTEGER NOT NULL,
                end INTEGER NOT NULL,
                globalTid INTEGER NOT NULL,
                correlationId INTEGER,
                nameId INTEGER NOT NULL
            );
            CREATE TABLE CUPTI_ACTIVITY_KIND_MEMCPY (
                start INTEGER NOT NULL,
                end INTEGER NOT NULL,
                globalPid INTEGER NOT NULL,
                bytes INTEGER NOT NULL,
                copyKind INTEGER NOT NULL,
                correlationId INTEGER
            );
            CREATE TABLE ENUM_CUDA_MEMCPY_OPER (
                id INTEGER PRIMARY KEY,
                label TEXT NOT NULL
            );
            CREATE TABLE CUPTI_ACTIVITY_KIND_KERNEL (
                start INTEGER NOT NULL,
                end INTEGER NOT NULL,
                globalPid INTEGER NOT NULL,
                correlationId INTEGER,
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
            5: "quest.communication.pack",
            6: "quest.communication.d2h",
            7: "quest.communication.mpi",
            8: "quest.communication.h2d",
            9: "quest.lifecycle.environment_init",
            10: "quest.lifecycle.qureg_create",
            11: "quest.lifecycle.state_init",
            12: "quest.lifecycle.validation",
            13: "quest.lifecycle.qureg_destroy",
            14: "quest.lifecycle.environment_finalize",
            20: "void kernel_statevec_anyCtrlOneTargDiagMatr_sub<(int)1>()",
            21: "void kernel_statevec_anyCtrlOneTargDenseMatr_subA<(int)0>()",
            22: "void kernel_statevec_anyCtrlSwap_subC<(int)0>()",
            23: "void kernel_statevec_packAmpsIntoBuffer<(int)1>()",
            30: "MPI_Isend",
            31: "MPI_Irecv",
            32: "MPI_Waitall",
            40: "cudaLaunchKernel_v7000",
            41: "cudaMemcpy_v3020",
            42: "cudaMalloc_v3020",
            43: "cudaFree_v3020",
        }
        cursor.executemany("INSERT INTO StringIds VALUES (?, ?)", strings.items())
        cursor.executemany(
            "INSERT INTO ENUM_CUDA_MEMCPY_OPER VALUES (?, ?)",
            [(1, "Host-to-Device"), (2, "Device-to-Host")],
        )

        rank0_tid = global_thread_id(101)
        rank1_tid = global_thread_id(202)
        cursor.executemany(
            "INSERT INTO MPI_RANKS VALUES (?, ?)",
            [(rank0_tid, 0), (rank1_tid, 1)],
        )
        cursor.executemany(
            "INSERT INTO NVTX_EVENTS VALUES (?, ?, ?, ?, ?)",
            [
                (0, 200, None, 1, rank0_tid),
                (50, 160, None, 2, rank0_tid),
                (0, 20, None, 9, rank0_tid),
                (20, 40, None, 10, rank0_tid),
                (42, 48, None, 11, rank0_tid),
                (165, 175, None, 12, rank0_tid),
                (175, 185, None, 13, rank0_tid),
                (185, 200, None, 14, rank0_tid),
                (65, 72, None, 5, rank0_tid),
                (80, 140, None, 3, rank0_tid),
                (80, 92, None, 6, rank0_tid),
                (92, 125, None, 7, rank0_tid),
                (125, 140, None, 8, rank0_tid),
                (0, 220, None, 1, rank1_tid),
                (50, 180, None, 2, rank1_tid),
                (0, 20, None, 9, rank1_tid),
                (20, 40, None, 10, rank1_tid),
                (42, 48, None, 11, rank1_tid),
                (185, 195, None, 12, rank1_tid),
                (195, 205, None, 13, rank1_tid),
                (205, 220, None, 14, rank1_tid),
                (95, 102, None, 5, rank1_tid),
                (110, 165, None, 3, rank1_tid),
                (110, 120, None, 6, rank1_tid),
                (120, 150, None, 7, rank1_tid),
                (150, 165, None, 8, rank1_tid),
            ],
        )

        cursor.executemany(
            "INSERT INTO CUPTI_ACTIVITY_KIND_RUNTIME VALUES (?, ?, ?, ?, ?)",
            [
                (66, 67, rank0_tid, 100, 40),
                (81, 83, rank0_tid, 101, 41),
                (126, 128, rank0_tid, 102, 41),
                (52, 53, rank0_tid, 103, 40),
                (145, 146, rank0_tid, 104, 40),
                (10, 11, rank0_tid, None, 42),
                (11, 13, rank0_tid, None, 42),
                (13, 17, rank0_tid, None, 43),
                (96, 97, rank1_tid, 200, 40),
                (111, 113, rank1_tid, 201, 41),
                (151, 153, rank1_tid, 202, 41),
                (52, 53, rank1_tid, 203, 40),
                (170, 171, rank1_tid, 204, 40),
                (10, 12, rank1_tid, None, 42),
                (12, 16, rank1_tid, None, 43),
            ],
        )
        cursor.executemany(
            "INSERT INTO CUPTI_ACTIVITY_KIND_MEMCPY VALUES (?, ?, ?, ?, ?, ?)",
            [
                (83, 88, global_process_id(101), 1024, 2, 101),
                (128, 136, global_process_id(101), 1024, 1, 102),
                (113, 118, global_process_id(202), 2048, 2, 201),
                (153, 162, global_process_id(202), 2048, 1, 202),
            ],
        )
        cursor.executemany(
            "INSERT INTO CUPTI_ACTIVITY_KIND_KERNEL VALUES (?, ?, ?, ?, ?, ?)",
            [
                (67, 75, global_process_id(101), 100, 23, 23),
                (53, 63, global_process_id(101), 103, 20, 20),
                (146, 155, global_process_id(101), 104, 22, 22),
                (97, 105, global_process_id(202), 200, 23, 23),
                (53, 68, global_process_id(202), 203, 21, 21),
                (171, 178, global_process_id(202), 204, 22, 22),
            ],
        )
        cursor.executemany(
            "INSERT INTO MPI_P2P_EVENTS VALUES (?, ?, ?, ?, ?, ?)",
            [
                (100, 101, rank0_tid, 1024, 1, 30),
                (100, 101, rank0_tid, 1024, 1, 31),
                (130, 131, rank1_tid, 2048, 0, 30),
                (130, 131, rank1_tid, 2048, 0, 31),
            ],
        )
        cursor.executemany(
            "INSERT INTO MPI_START_WAIT_EVENTS VALUES (?, ?, ?, ?)",
            [(102, 120, rank0_tid, 32), (132, 148, rank1_tid, 32)],
        )
        connection.commit()
        connection.close()

    def test_parse_profile_builds_rank_and_summary_rows(self):
        result = breakdown.parse_profile(
            sqlite_path=self.sqlite_path,
            point="qft",
            benchmark="qft",
            num_qubits=28,
            mpi_ranks=2,
            slurm_nodes=1,
            gpus=2,
        )

        rank_rows = result["rank_rows"]
        summary = result["summary_row"]
        self.assertEqual([row["rank"] for row in rank_rows], [0, 1])
        self.assertEqual(summary["critical_rank"], 1)
        self.assertEqual(summary["critical_rank_mpi_send_calls"], 1)
        self.assertEqual(summary["critical_rank_mpi_send_bytes"], 2048)
        self.assertEqual(summary["aggregate_mpi_send_calls"], 2)
        self.assertEqual(summary["aggregate_mpi_send_bytes"], 3072)
        self.assertEqual(summary["rank_wall_min_s"], 200 / 1e9)
        self.assertEqual(summary["rank_wall_max_s"], 220 / 1e9)
        self.assertAlmostEqual(summary["rank_imbalance_s"], 20 / 1e9)
        self.assertAlmostEqual(
            summary["computation_pct"]
            + summary["communication_pct"]
            + summary["lifecycle_pct"]
            + summary["execution_overhead_pct"],
            100.0,
        )
        self.assertAlmostEqual(
            summary["others_time_s"],
            summary["lifecycle_time_s"] + summary["execution_overhead_time_s"],
        )

    def test_communication_detail_correlates_pack_copy_and_mpi(self):
        result = breakdown.parse_profile(
            sqlite_path=self.sqlite_path,
            point="qft",
            benchmark="qft",
            num_qubits=28,
            mpi_ranks=2,
            slurm_nodes=1,
            gpus=2,
        )
        rows = result["communication_rows"]

        self.assertEqual(len(rows), 2)
        rank0 = rows[0]
        self.assertEqual(rank0["exchange_id"], 0)
        self.assertEqual(rank0["communication_path"], "cpu_staged")
        self.assertEqual(rank0["peer_rank"], 1)
        self.assertEqual(rank0["send_bytes"], 1024)
        self.assertEqual(rank0["recv_bytes"], 1024)
        self.assertEqual(rank0["mpi_send_calls"], 1)
        self.assertEqual(rank0["mpi_recv_calls"], 1)
        self.assertEqual(rank0["mpi_wait_calls"], 1)
        self.assertEqual(rank0["pack_time_s"], 8 / 1e9)
        self.assertEqual(rank0["d2h_time_s"], 5 / 1e9)
        self.assertEqual(rank0["mpi_wait_time_s"], 18 / 1e9)
        self.assertEqual(rank0["h2d_time_s"], 8 / 1e9)
        self.assertEqual(rank0["exchange_wall_time_s"], 60 / 1e9)
        self.assertAlmostEqual(rank0["effective_bandwidth_gbps"], 8.0 * 1024 / 60)

    def test_computation_lifecycle_and_runtime_summaries_are_compact(self):
        result = breakdown.parse_profile(
            sqlite_path=self.sqlite_path,
            point="qft",
            benchmark="qft",
            num_qubits=28,
            mpi_ranks=2,
            slurm_nodes=1,
            gpus=2,
        )

        rank0_classes = {
            row["kernel_class"]: row for row in result["computation_rows"] if row["rank"] == 0
        }
        self.assertEqual(rank0_classes["phase"]["calls"], 1)
        self.assertEqual(rank0_classes["swap"]["calls"], 1)
        lifecycle_stages = {
            row["stage"] for row in result["lifecycle_rows"] if row["rank"] == 0
        }
        self.assertIn("environment_init", lifecycle_stages)
        self.assertIn("environment_finalize", lifecycle_stages)

        runtime = {
            (row["rank"], row["api"]): row for row in result["runtime_rows"]
        }
        self.assertEqual(runtime[(0, "cudaMalloc")]["calls"], 2)
        self.assertEqual(runtime[(0, "cudaMalloc")]["median_time_s"], 1 / 1e9)
        self.assertEqual(runtime[(0, "cudaMalloc")]["p95_time_s"], 2 / 1e9)
        self.assertEqual(runtime[(0, "cudaMemcpy")]["calls"], 2)

    def test_append_rows_writes_all_stable_tsv_schemas(self):
        result = breakdown.parse_profile(
            sqlite_path=self.sqlite_path,
            point="qft",
            benchmark="qft",
            num_qubits=28,
            mpi_ranks=2,
            slurm_nodes=1,
            gpus=2,
        )
        outputs = [
            (self.rank_tsv, breakdown.RANK_FIELDS, result["rank_rows"]),
            (self.summary_tsv, breakdown.SUMMARY_FIELDS, [result["summary_row"]]),
            (self.communication_tsv, breakdown.COMMUNICATION_FIELDS, result["communication_rows"]),
            (self.computation_tsv, breakdown.COMPUTATION_FIELDS, result["computation_rows"]),
            (self.lifecycle_tsv, breakdown.LIFECYCLE_FIELDS, result["lifecycle_rows"]),
            (self.runtime_tsv, breakdown.RUNTIME_FIELDS, result["runtime_rows"]),
        ]
        for path, fields, rows in outputs:
            breakdown.append_tsv(path, fields, rows)
            with path.open(newline="") as handle:
                written = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(len(written), len(rows))
            self.assertEqual(list(written[0]), fields)


class LegacyProfileTests(unittest.TestCase):
    def test_missing_lifecycle_markers_uses_procedure_residual(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "legacy.sqlite"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE StringIds (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE NVTX_EVENTS (
                    start INTEGER NOT NULL, end INTEGER, text TEXT,
                    textId INTEGER, globalTid INTEGER NOT NULL
                );
                CREATE TABLE MPI_RANKS (globalTid INTEGER NOT NULL, rank INTEGER NOT NULL);
                CREATE TABLE CUPTI_ACTIVITY_KIND_KERNEL (
                    start INTEGER NOT NULL, end INTEGER NOT NULL,
                    globalPid INTEGER NOT NULL, demangledName INTEGER, shortName INTEGER
                );
                """
            )
            tid = global_thread_id(303)
            connection.executemany(
                "INSERT INTO StringIds VALUES (?, ?)",
                [(1, "quest.procedure"), (2, "quest.execution.timed"), (3, "kernel")],
            )
            connection.execute("INSERT INTO MPI_RANKS VALUES (?, ?)", (tid, 0))
            connection.executemany(
                "INSERT INTO NVTX_EVENTS VALUES (?, ?, ?, ?, ?)",
                [(0, 100, None, 1, tid), (20, 80, None, 2, tid)],
            )
            connection.execute(
                "INSERT INTO CUPTI_ACTIVITY_KIND_KERNEL VALUES (?, ?, ?, ?, ?)",
                (30, 50, global_process_id(303), 3, 3),
            )
            connection.commit()
            connection.close()

            result = breakdown.parse_profile(
                sqlite_path=path,
                point="legacy",
                benchmark="qft",
                num_qubits=4,
                mpi_ranks=1,
                slurm_nodes=1,
                gpus=1,
            )
            row = result["rank_rows"][0]
            self.assertEqual(row["lifecycle_time_s"], 40 / 1e9)
            self.assertEqual(result["lifecycle_rows"], [])


if __name__ == "__main__":
    unittest.main()
