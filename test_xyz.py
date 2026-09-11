import csv
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

import xyz
import catalog


class DiscoveryTests(unittest.TestCase):
    def run_cli(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            try:
                status = xyz.main(["generate", *arguments])
            except SystemExit as error:
                status = error.code
        return status, output.getvalue(), errors.getvalue()

    def test_default_is_varied_unique_and_offline(self):
        with patch("socket.socket", side_effect=AssertionError("network access")):
            status, output, _ = self.run_cli("--format", "csv")
        self.assertEqual(status, 0)
        rows = list(csv.DictReader(io.StringIO(output)))
        self.assertEqual(len(rows), 50)
        self.assertEqual(len({row["domain"] for row in rows}), 50)
        self.assertEqual(rows[0]["domain"], "000000.xyz")
        self.assertEqual(set(rows[0]["reasons"].split(";")), {"repeat", "palindrome", "pair"})
        reasons = {reason for row in rows[:8] for reason in row["reasons"].split(";")}
        self.assertTrue(set(xyz.DEFAULT_PATTERNS) <= reasons)
        self.assertTrue(all(len(xyz.numeric_label(row["domain"])) == 6 for row in rows))
        self.assertEqual(output, self.run_cli("--format", "csv")[1])

    def test_explicit_identity_order_and_deduplication(self):
        status, output, _ = self.run_cli(
            "--number", " 001234.XYZ ", "--number", "0001234", "--number", "001234"
        )
        self.assertEqual(status, 0)
        self.assertEqual(output.splitlines(), ["001234.xyz", "0001234.xyz"])
        rows = list(csv.DictReader(io.StringIO(self.run_cli(
            "--number", "123321", "--pattern", "palindrome", "--limit", "1", "--format", "csv"
        )[1])))
        self.assertEqual(rows[0]["reasons"], "explicit;palindrome")

    def test_generators_known_counts_and_shapes(self):
        for length in (6, 7):
            palindromes = list(xyz.labels("palindrome", length))
            self.assertEqual(len(palindromes), 10**((length + 1) // 2))
            self.assertTrue(all(len(label) == length and label == label[::-1] for label in palindromes))
        self.assertEqual(len(list(xyz.labels("pair", 6))), 1000)
        self.assertEqual(list(xyz.labels("pair", 7)), [])
        self.assertEqual(len(set(xyz.labels("chunks", 6))), 270)
        self.assertIn("111222", set(xyz.labels("chunks", 6)))
        self.assertEqual(len(set(xyz.labels("sequence", 6))), 10)
        self.assertNotIn("789012", set(xyz.labels("sequence", 6)))

    def test_dates_leap_days_and_calendar_boundaries(self):
        for start, end, expected in [
            ("2024-02-28", "2024-03-01", ["20240228", "20240229", "20240301"]),
            ("2023-02-28", "2023-03-01", ["20230228", "20230301"]),
            ("9999-12-31", "9999-12-31", ["99991231"]),
            ("0001-01-01", "0001-01-01", ["00010101"]),
        ]:
            self.assertEqual(list(xyz.labels("date", 8, date.fromisoformat(start), date.fromisoformat(end))), expected)
        self.assertEqual(list(xyz.labels("date", 6, date(2024, 2, 29), date(2024, 2, 29))), ["240229"])

    def test_filters_and_empty_output(self):
        status, output, _ = self.run_cli(
            "--pattern", "repeat", "--prefix", "12", "--suffix", "12", "--contains", "21",
            "--no-leading-zero",
        )
        self.assertEqual(status, 0)
        self.assertEqual(output.strip(), "121212.xyz")
        status, output, errors = self.run_cli("--prefix", "1234567", "--length", "6")
        self.assertEqual((status, output), (0, ""))
        self.assertIn("No candidates", errors)

    def test_cap_limits_rejected_and_duplicate_constructions(self):
        status, output, errors = self.run_cli("--max-generated", "6", "--format", "csv")
        self.assertEqual(status, 0)
        self.assertIn("Examined 6 constructions", errors)
        self.assertIn("pool may be incomplete", errors)
        rows = list(csv.DictReader(io.StringIO(output)))
        self.assertIn("100000.xyz", [row["domain"] for row in rows])
        self.assertEqual(len(rows), 4)
        self.assertEqual(self.run_cli("--max-generated", "6", "--format", "csv")[1], output)
        status, output, errors = self.run_cli("--max-generated", "3", "--prefix", "999999")
        self.assertEqual((status, output), (0, ""))
        self.assertIn("Examined 3 constructions", errors)

    def test_invalid_arguments_emit_no_output(self):
        for arguments in [
            ["--number", "１２３４５６"], ["--number", "123456.com"],
            ["--number", "123 456"], ["--number", "12345"],
            ["--number", "1234567", "--length", "6"], ["--contains", "x"],
            ["--limit", "0"], ["--max-generated", "-1"],
            ["--pattern", "pair", "--length", "7"],
            ["--pattern", "date"], ["--date-start", "2024-01-01"],
            ["--pattern", "date", "--date-start", "2024-02-30"],
            ["--pattern", "date", "--date-start", "2024-03-01", "--date-end", "2024-02-01"],
        ]:
            with self.subTest(arguments=arguments):
                status, output, _ = self.run_cli(*arguments)
                self.assertEqual((status, output), (2, ""))

    def test_files_validate_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "names.txt"
            path.write_text("\ufeff001234.xyz\n\n0001234\n", encoding="utf-8")
            self.assertEqual(self.run_cli("--input", str(path))[1].splitlines(), ["001234.xyz", "0001234.xyz"])
            path.write_text("001234.xyz\nwrong\n")
            status, output, errors = self.run_cli("--input", str(path))
            self.assertEqual((status, output), (2, ""))
            self.assertIn(f"{path}:2:", errors)
            self.assertEqual(self.run_cli("--input", str(path.parent / "missing"))[0], 1)



class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "catalog.sqlite3"

    def run_cli(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            try:
                status = xyz.main([*arguments, "--database", str(self.path)])
            except SystemExit as error:
                status = error.code
        return status, output.getvalue(), errors.getvalue()

    def test_only_ranked_selection_is_committed_offline(self):
        with patch("socket.socket", side_effect=AssertionError("network access")):
            status, _, _ = self.run_cli("build")
        self.assertEqual(status, 0)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*), COUNT(DISTINCT rank), MIN(rank), MAX(rank) FROM domains").fetchone(), (1000, 1000, 1, 1000))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM domains WHERE reasons = '' OR availability != 'unchecked' OR checked_at IS NOT NULL OR registration_price IS NOT NULL").fetchone()[0], 0)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
            self.assertEqual(metadata["row_count"], "1000")
            self.assertEqual(metadata["examined"], "3399")
            self.assertEqual(metadata["cap_reached"], "false")
            query_plan = str(connection.execute("EXPLAIN QUERY PLAN SELECT domain FROM domains ORDER BY rank LIMIT 20").fetchall())
            self.assertIn("domains_rank", query_plan)

    def test_supported_lengths_and_sparse_retention(self):
        status, _, _ = self.run_cli("build", "--length", "9", "--pattern", "palindrome", "--keep", "25")
        self.assertEqual(status, 0)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*), MIN(length), MAX(length) FROM domains").fetchone(), (25, 9, 9))
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
            self.assertEqual(metadata["examined"], "100000")
            self.assertEqual(metadata["cap_reached"], "false")

    def test_filters_exports_and_stored_ranks(self):
        self.assertEqual(self.run_cli("build")[0], 0)
        status, output, _ = self.run_cli("find", "--pattern", "palindrome", "--contains", "88", "--no-leading-zero")
        rows = list(csv.DictReader(io.StringIO(output)))
        self.assertEqual(status, 0)
        self.assertTrue(rows)
        self.assertTrue(all("palindrome" in row["reasons"] and "88" in row["domain"] and not row["domain"].startswith("0") for row in rows))
        self.assertNotEqual(rows[0]["rank"], "1")
        status, output, _ = self.run_cli("find", "--prefix", "11", "--suffix", "11", "--format", "text")
        self.assertEqual(status, 0)
        self.assertTrue(output.strip())
        self.assertTrue(all(domain.startswith("11") and domain.endswith("11.xyz") for domain in output.splitlines()))
        rows = list(csv.DictReader(io.StringIO(self.run_cli("find", "--state", "available")[1])))
        self.assertEqual(rows, [])

    def test_reuse_and_explicit_replacement_preserve_retained_observations(self):
        self.assertEqual(self.run_cli("build", "--number", "001234", "--number", "0001234")[0], 0)
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE domains SET availability='available', provider='test', checked_at='2026-09-11T00:00:00Z', registration_price='0.99', currency='USD' WHERE label='001234'")
        status, _, errors = self.run_cli("build", "--number", "001234", "--number", "0001234")
        self.assertEqual(status, 0)
        self.assertIn("Reused", errors)
        original = self.path.read_bytes()
        self.assertEqual(self.run_cli("build", "--number", "001234")[0], 2)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.run_cli("build", "--number", "888888", "--number", "001234", "--replace")[0], 0)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT domain, rank, availability, registration_price FROM domains ORDER BY rank").fetchall(), [
                ("888888.xyz", 1, "unchecked", None), ("001234.xyz", 2, "available", "0.99")
            ])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM domains WHERE label='0001234'").fetchone()[0], 0)

    def test_failed_replacement_keeps_original_and_cleans_temporary_file(self):
        self.assertEqual(self.run_cli("build", "--keep", "10")[0], 0)
        original = self.path.read_bytes()
        with self.assertRaises(sqlite3.IntegrityError):
            catalog.build(self.path, [("123123", ["repeat"]), ("123123", ["repeat"])], {}, 2, False, replace=True)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob(".catalog-*")), [])

    def test_caps_metadata_empty_catalog_and_missing_paths(self):
        self.assertEqual(self.run_cli("find")[0], 1)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.run_cli("build", "--max-generated", "3", "--contains", "999999")[0], 0)
        with sqlite3.connect(self.path) as connection:
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
            self.assertEqual(metadata["cap_reached"], "true")
            self.assertEqual(metadata["row_count"], "0")
            self.assertEqual(json.loads(metadata["selection"])["max_generated"], 3)


if __name__ == "__main__":
    unittest.main()
