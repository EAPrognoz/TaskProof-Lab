"""CLI-level regressions for malformed EP009 timesheet CSV structure."""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "audit_timesheets.py"
OUTPUT_NAMES = ("shift_audit.csv", "overlap_pairs.csv", "review_queue.csv", "summary.json")
HEADER = "shift_id,employee_id,shift_date,start_time,end_time\n"


class ShortCsvCliRegressionTests(unittest.TestCase):
    def run_cli(self, content: str, *, seed_outputs: bool = False):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        input_path = root / "timesheets.csv"
        output = root / "output"
        input_path.write_text(content, encoding="utf-8")
        output.mkdir()
        if seed_outputs:
            for name in OUTPUT_NAMES:
                (output / name).write_text("stale report", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--input", str(input_path), "--output-dir", str(output)],
            capture_output=True, text=True, check=False,
        )
        files = {name: (output / name).read_text(encoding="utf-8")
                 for name in OUTPUT_NAMES if (output / name).exists()}
        temp.cleanup()
        return proc, files

    def assert_failed_closed(self, proc, files, *messages):
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertNotIn("Traceback", proc.stderr)
        for message in messages:
            self.assertIn(message, proc.stderr)
        self.assertEqual(files, {})

    def test_multiple_short_row_lengths_fail_and_short_duplicate_cannot_be_ignored(self):
        good = "S1,E1,2026-11-11,09:00,10:00\n"
        for short in ("S1", "S1,E1", "S1,E1,2026-11-11,09:00"):
            with self.subTest(short=short):
                proc, files = self.run_cli(HEADER + good + short + "\n", seed_outputs=True)
                self.assert_failed_closed(proc, files, "timesheets.csv", "source record 3",
                                          "expected 5 fields")

    def test_empty_final_cell_is_review_but_absent_end_time_fails_closed(self):
        content = (HEADER + "S1,E1,2026-11-11,09:00,\n"
                   "S2,E2,2026-11-11,09:00,10:00\n")
        proc, files = self.run_cli(content)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(files["summary.json"])
        self.assertEqual(summary["input_rows"], 2)
        self.assertEqual(summary["review_rows"], 1)
        self.assertEqual(summary["accounted_rows"], 2)
        review = list(csv.DictReader(files["review_queue.csv"].splitlines()))
        self.assertEqual(review[0]["shift_id"], "S1")
        self.assertEqual(review[0]["issue"], "missing end_time")

        proc, files = self.run_cli(HEADER + "S1,E1,2026-11-11,09:00\n"
                                   "S2,E2,2026-11-11,09:00,10:00\n", seed_outputs=True)
        self.assert_failed_closed(proc, files, "timesheets.csv", "source record 2",
                                  "expected 5 fields", "end_time")

    def test_extra_cells_duplicate_headers_and_extra_columns_fail_closed(self):
        cases = (
            (HEADER + "S1,E1,2026-11-11,09:00,10:00,extra\n", "extra"),
            ("shift_id,employee_id,shift_date,start_time,end_time,shift_id\n"
             "S1,E1,2026-11-11,09:00,10:00,S2\n", "duplicate header"),
            ("shift_id,employee_id,shift_date,start_time,end_time,note\n"
             "S1,E1,2026-11-11,09:00,10:00,ok\n", "unexpected column"),
        )
        for content, expected in cases:
            with self.subTest(expected=expected):
                proc, files = self.run_cli(content)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn("timesheets.csv", proc.stderr)
                self.assertIn(expected, proc.stderr.lower())
                self.assertEqual(files, {})

    def test_unclosed_quote_and_blank_physical_row_fail_without_reports(self):
        cases = (
            (HEADER + 'S1,E1,2026-11-11,09:00,"10:00\nS2,E2,2026-11-11,09:00,10:00\n',
             "malformed CSV"),
            (HEADER + "\nS1,E1,2026-11-11,09:00,10:00\n", "source record 2"),
        )
        for content, expected in cases:
            with self.subTest(expected=expected):
                proc, files = self.run_cli(content)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn("timesheets.csv", proc.stderr)
                self.assertIn(expected, proc.stderr)
                self.assertEqual(files, {})

    def test_missing_header_key_is_a_structured_cli_error(self):
        proc, files = self.run_cli("shift_id,employee_id,shift_date,start_time\n"
                                   "S1,E1,2026-11-11,09:00\n")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("timesheets.csv", proc.stderr)
        self.assertIn("end_time", proc.stderr)
        self.assertEqual(files, {})

    def test_mixed_good_and_incomplete_rows_fail_and_remove_stale_reports(self):
        content = HEADER + "S1,E1,2026-11-11,09:00,10:00\nS2,E2,2026-11-11,11:00\n"
        proc, files = self.run_cli(content, seed_outputs=True)
        self.assert_failed_closed(proc, files, "source record 3", "end_time")

    def test_failed_run_removes_reports_from_a_prior_successful_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "timesheets.csv"
            output = root / "output"
            input_path.write_text(HEADER + "S1,E1,2026-11-11,09:00,10:00\n", encoding="utf-8")
            first = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(input_path), "--output-dir", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual({path.name for path in output.iterdir()}, set(OUTPUT_NAMES))

            input_path.write_text(HEADER + "S1,E1,2026-11-11,09:00\n", encoding="utf-8")
            second = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(input_path), "--output-dir", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(second.returncode, 2, second.stderr)
            self.assertNotIn("Traceback", second.stderr)
            self.assertIn("timesheets.csv", second.stderr)
            self.assertEqual(list(output.iterdir()), [])

    def test_multiline_csv_record_keeps_later_source_record_index(self):
        content = HEADER + 'S1,"E\n1",2026-11-11,09:00,10:00\nS2,E2,2026-11-11,11:00,\n'
        proc, files = self.run_cli(content)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(files["summary.json"])
        self.assertEqual(summary["input_rows"], 2)
        self.assertEqual(summary["review_rows"], 1)
        review = list(csv.DictReader(files["review_queue.csv"].splitlines()))
        self.assertEqual(review[0]["source_row"], "3")

    def test_stray_quote_in_unquoted_field_fails_closed(self):
        content = (HEADER + 'S1,E1",2026-11-11,09:00,12:00\n'
                   "S2,E1,2026-11-11,10:00,11:00\n")
        proc, files = self.run_cli(content, seed_outputs=True)
        self.assert_failed_closed(proc, files, "timesheets.csv", "quote", "physical line 2")

    def test_quoted_commas_and_escaped_quotes_are_accepted(self):
        content = (HEADER + 'S1,"E, ""One""",2026-11-11,09:00,10:00\n'
                   'S2,"E, ""One""",2026-11-11,10:00,11:00\n')
        proc, files = self.run_cli(content)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(files["summary.json"])
        self.assertEqual(summary["clear_rows"], 2)
        self.assertEqual(summary["overlap_rows"], 0)
        audit = list(csv.DictReader(files["shift_audit.csv"].splitlines()))
        self.assertEqual([row["employee_id"] for row in audit], ['E, "One"'] * 2)

    def test_maximum_date_overnight_overflow_becomes_review_row(self):
        content = HEADER + "S1,E1,9999-12-31,23:00,01:00\n"
        proc, files = self.run_cli(content)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        summary = json.loads(files["summary.json"])
        self.assertEqual(summary["review_rows"], 1)
        self.assertEqual(summary["clear_rows"], 0)
        review = list(csv.DictReader(files["review_queue.csv"].splitlines()))
        self.assertIn("date range", review[0]["issue"])

    def test_input_report_name_collision_matrix_preserves_valid_and_short_inputs(self):
        for name, valid in ((name, valid) for name in OUTPUT_NAMES for valid in (False, True)):
            with self.subTest(name=name, valid=valid), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / "output"
                output.mkdir()
                colliding = output / name
                content = HEADER + ("S1,E1,2026-11-11,09:00,10:00\n" if valid
                                    else "S1,E1,2026-11-11,09:00\n")
                colliding.write_text(content, encoding="utf-8")
                original_bytes = colliding.read_bytes()
                for report_name in OUTPUT_NAMES:
                    if report_name != name:
                        (output / report_name).write_bytes(b"prior report sentinel\n")
                unrelated = output / "unrelated.txt"
                unrelated.write_bytes(b"keep me\n")
                proc = subprocess.run(
                    [sys.executable, str(SCRIPT), "--input", f"output/../output/{name}",
                     "--output-dir", "output"],
                    cwd=root, capture_output=True, text=True, check=False,
                )
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn("input/output path collision", proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                self.assertEqual(colliding.read_bytes(), original_bytes)
                self.assertEqual(unrelated.read_bytes(), b"keep me\n")
                for report_name in OUTPUT_NAMES:
                    if report_name != name:
                        self.assertEqual((output / report_name).read_bytes(), b"prior report sentinel\n")

    def test_symlink_and_hardlink_report_aliases_are_preserved(self):
        for link_kind in ("symlink", "hardlink"):
            with self.subTest(link_kind=link_kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / "output"
                output.mkdir()
                input_path = root / "timesheets.csv"
                input_path.write_text(HEADER + "S1,E1,2026-11-11,09:00,10:00\n", encoding="utf-8")
                alias = output / "review_queue.csv"
                if link_kind == "symlink":
                    os.symlink(input_path, alias)
                else:
                    os.link(input_path, alias)
                before = input_path.read_bytes()
                unrelated = output / "unrelated.txt"
                unrelated.write_text("keep me\n", encoding="utf-8")
                proc = subprocess.run(
                    [sys.executable, str(SCRIPT), "--input", str(input_path),
                     "--output-dir", str(output)],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn("input/output path collision", proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                self.assertEqual(input_path.read_bytes(), before)
                self.assertTrue(alias.exists())
                self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep me\n")

    def test_cleanup_blocked_by_report_directory_is_structured_and_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "broken_timesheets.csv"
            output = root / "output"
            output.mkdir()
            input_path.write_text(HEADER + "S1,E1,2026-11-11,09:00\n", encoding="utf-8")
            blocked = output / "shift_audit.csv"
            blocked.mkdir()
            (blocked / "unrelated.txt").write_text("keep me\n", encoding="utf-8")
            for name in ("overlap_pairs.csv", "review_queue.csv", "summary.json"):
                (output / name).write_text("stale report\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(input_path), "--output-dir", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("broken_timesheets.csv", proc.stderr)
            self.assertIn("cleanup incomplete", proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
            self.assertEqual((blocked / "unrelated.txt").read_text(encoding="utf-8"), "keep me\n")
            self.assertFalse((output / "overlap_pairs.csv").exists())
            self.assertFalse((output / "review_queue.csv").exists())
            self.assertFalse((output / "summary.json").exists())

    def test_invalid_utf8_error_names_source_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "broken_timesheets.csv"
            output = root / "output"
            input_path.write_bytes(HEADER.encode("utf-8") + b"S1,E1,2026-11-11,09:00,\xff\n")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--input", str(input_path), "--output-dir", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("broken_timesheets.csv", proc.stderr)
            self.assertIn("UTF-8", proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
