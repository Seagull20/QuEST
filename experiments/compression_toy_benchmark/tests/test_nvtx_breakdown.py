#!/usr/bin/env python3

import csv
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NVTX_BREAKDOWN = ROOT / "scripts" / "nvtx_breakdown.py"


def load_module():
    spec = importlib.util.spec_from_file_location("nvtx_breakdown", NVTX_BREAKDOWN)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_tsv(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def create_sqlite(path, marker_schema, exchange_path, missing_stage=None):
    assert marker_schema in {"text", "text_id"}
    labels = []

    def label_id(label):
        labels.append(label)
        return len(labels)

    if exchange_path == "raw":
        events = [
            (0, 100_000_000, "quest_compression.exchange.raw"),
            (0, 20_000_000, "quest_compression.stage.d2h"),
            (20_000_000, 80_000_000, "quest_compression.stage.mpi"),
            (80_000_000, 100_000_000, "quest_compression.stage.h2d"),
        ]
    elif exchange_path == "compressed":
        events = [
            (200_000_000, 260_000_000, "quest_compression.exchange.compressed"),
            (200_000_000, 210_000_000, "quest_compression.stage.compress"),
            (210_000_000, 211_000_000, "quest_compression.stage.size_exchange"),
            (211_000_000, 216_000_000, "quest_compression.stage.d2h"),
            (216_000_000, 224_000_000, "quest_compression.stage.mpi"),
            (224_000_000, 229_000_000, "quest_compression.stage.h2d"),
            (229_000_000, 260_000_000, "quest_compression.stage.decompress"),
        ]
    else:
        raise ValueError(exchange_path)

    if missing_stage:
        events = [event for event in events if event[2] != f"quest_compression.stage.{missing_stage}"]

    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE MPI_RANKS(globalTid INTEGER, rank INTEGER)")
        connection.execute("INSERT INTO MPI_RANKS VALUES(1, 0)")
        if marker_schema == "text":
            connection.execute("CREATE TABLE NVTX_EVENTS(start INTEGER, end INTEGER, text TEXT, globalTid INTEGER)")
            connection.executemany(
                "INSERT INTO NVTX_EVENTS VALUES(?, ?, ?, 1)",
                events,
            )
        else:
            connection.execute("CREATE TABLE StringIds(id INTEGER, value TEXT)")
            connection.execute("CREATE TABLE NVTX_EVENTS(start INTEGER, end INTEGER, textId INTEGER, globalTid INTEGER)")
            rows = []
            for start, end, text in events:
                rows.append((start, end, label_id(text), 1))
            connection.executemany("INSERT INTO StringIds VALUES(?, ?)", enumerate(labels, start=1))
            connection.executemany("INSERT INTO NVTX_EVENTS VALUES(?, ?, ?, ?)", rows)


class NvtxBreakdownTests(unittest.TestCase):
    def test_parser_handles_text_and_text_id_nvtx_schemas(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_sqlite = tmp_path / "raw.sqlite"
            bitcomp_sqlite = tmp_path / "bitcomp.sqlite"
            create_sqlite(raw_sqlite, "text", "raw")
            create_sqlite(bitcomp_sqlite, "text_id", "compressed")

            result = module.analyze_cases(
                {"raw": raw_sqlite, "nvcomp_bitcomp": bitcomp_sqlite},
                tmp_path,
            )

            self.assertEqual(result["status"], "PASS")
            self.assertTrue((tmp_path / "nvtx_range_samples.tsv").exists())
            self.assertTrue((tmp_path / "nvtx_breakdown_summary.tsv").exists())
            self.assertTrue((tmp_path / "nvtx_breakdown_comparison.tsv").exists())
            self.assertTrue((tmp_path / "nvtx_breakdown_conclusion.md").exists())

            summary = read_tsv(tmp_path / "nvtx_breakdown_summary.tsv")
            rows = {(row["case"], row["path"], row["stage"]): row for row in summary}
            self.assertEqual(rows[("raw", "raw", "d2h")]["status"], "PASS")
            self.assertAlmostEqual(float(rows[("raw", "raw", "d2h")]["median_s"]), 0.02)
            self.assertEqual(rows[("nvcomp_bitcomp", "compressed", "decompress")]["status"], "PASS")
            self.assertAlmostEqual(float(rows[("nvcomp_bitcomp", "compressed", "decompress")]["median_s"]), 0.031)

            comparison = read_tsv(tmp_path / "nvtx_breakdown_comparison.tsv")
            self.assertEqual(comparison[0]["status"], "PASS")
            self.assertAlmostEqual(float(comparison[0]["raw_exchange_median_s"]), 0.1)
            self.assertAlmostEqual(float(comparison[0]["compressed_exchange_median_s"]), 0.06)

    def test_missing_stage_is_reported_not_silently_zeroed(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_sqlite = tmp_path / "raw_missing.sqlite"
            create_sqlite(raw_sqlite, "text", "raw", missing_stage="mpi")

            result = module.analyze_cases({"raw": raw_sqlite}, tmp_path)

            self.assertEqual(result["status"], "MISSING_STAGE")
            summary = read_tsv(tmp_path / "nvtx_breakdown_summary.tsv")
            rows = {(row["case"], row["path"], row["stage"]): row for row in summary}
            self.assertEqual(rows[("raw", "raw", "mpi")]["status"], "MISSING_STAGE")
            self.assertEqual(rows[("raw", "raw", "mpi")]["median_s"], "")


if __name__ == "__main__":
    unittest.main()
