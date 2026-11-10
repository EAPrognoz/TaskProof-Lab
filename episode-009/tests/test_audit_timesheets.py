from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import audit_timesheets as audit


ROOT = Path(__file__).resolve().parents[1]


class TimesheetAuditTests(unittest.TestCase):
    def test_sample_reconciles_every_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            summary = audit.build(ROOT / "examples" / "timesheets.csv", out)
            self.assertEqual((summary["input_rows"], summary["clear_rows"], summary["overlap_rows"], summary["review_rows"]), (14, 3, 6, 5))
            self.assertEqual(summary["overlap_pairs"], 3)
            self.assertEqual(summary["accounted_rows"], 14)

    def test_back_to_back_shifts_are_not_overlap(self):
        rows = self.make_rows([
            ["A", "E1", "2026-10-27", "08:00", "12:00"],
            ["B", "E1", "2026-10-27", "12:00", "16:00"],
        ])
        result = self.run_rows(rows)
        self.assertEqual(result["overlap_pairs"], 0)
        self.assertEqual(result["clear_rows"], 2)

    def test_same_day_overlap_is_reported(self):
        rows = self.make_rows([
            ["A", "E1", "2026-10-27", "08:00", "12:00"],
            ["B", "E1", "2026-10-27", "11:30", "15:00"],
        ])
        result = self.run_rows(rows)
        self.assertEqual(result["overlap_pairs"], 1)
        self.assertEqual(result["overlap_rows"], 2)

    def test_employee_id_matching_trims_and_ignores_case(self):
        rows = self.make_rows([
            ["A", " E1 ", "2026-10-27", "08:00", "12:00"],
            ["B", "e1", "2026-10-27", "11:00", "15:00"],
        ])
        result = self.run_rows(rows)
        self.assertEqual(result["overlap_pairs"], 1)
        self.assertEqual(result["overlap_rows"], 2)

    def test_end_before_start_is_next_day(self):
        rows = self.make_rows([["N", "E1", "2026-10-27", "22:00", "06:00"]])
        with tempfile.TemporaryDirectory() as tmp:
            source = self.write_rows(rows, Path(tmp) / "input.csv")
            summary = audit.build(source, Path(tmp) / "out")
            with (Path(tmp) / "out" / "shift_audit.csv").open() as handle:
                record = next(csv.DictReader(handle))
            self.assertEqual(record["end"], "2026-10-28T06:00")
            self.assertEqual(record["duration_minutes"], "480")
            self.assertEqual(summary["clear_rows"], 1)

    def test_cross_midnight_overlap_is_reported(self):
        rows = self.make_rows([
            ["N", "E1", "2026-10-27", "22:00", "06:00"],
            ["D", "E1", "2026-10-28", "01:00", "04:00"],
        ])
        result = self.run_rows(rows)
        self.assertEqual(result["overlap_pairs"], 1)
        self.assertEqual(result["overlap_rows"], 2)

    def test_missing_end_goes_to_review(self):
        result = self.run_rows(self.make_rows([["A", "E1", "2026-10-27", "08:00", ""]]))
        self.assertEqual(result["review_rows"], 1)

    def test_invalid_time_goes_to_review(self):
        result = self.run_rows(self.make_rows([["A", "E1", "2026-10-27", "9am", "17:00"]]))
        self.assertEqual(result["review_rows"], 1)

    def test_equal_times_are_ambiguous_and_reviewed(self):
        result = self.run_rows(self.make_rows([["A", "E1", "2026-10-27", "08:00", "08:00"]]))
        self.assertEqual(result["review_rows"], 1)

    def test_duplicate_ids_send_all_matches_to_review(self):
        rows = self.make_rows([
            ["A", "E1", "2026-10-27", "08:00", "12:00"],
            ["a", "E2", "2026-10-27", "08:00", "12:00"],
        ])
        result = self.run_rows(rows)
        self.assertEqual(result["review_rows"], 2)
        self.assertEqual(result["valid_unique_rows"], 0)

    def test_empty_employee_goes_to_review(self):
        result = self.run_rows(self.make_rows([["A", "", "2026-10-27", "08:00", "12:00"]]))
        self.assertEqual(result["review_rows"], 1)

    def test_input_is_not_modified(self):
        rows = self.make_rows([["A", "E1", "2026-10-27", "08:00", "12:00"]])
        with tempfile.TemporaryDirectory() as tmp:
            source = self.write_rows(rows, Path(tmp) / "input.csv")
            original = source.read_bytes()
            audit.build(source, Path(tmp) / "out")
            self.assertEqual(source.read_bytes(), original)

    def test_repeated_run_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            in_path = ROOT / "examples" / "timesheets.csv"
            audit.build(in_path, root / "a")
            audit.build(in_path, root / "b")
            for name in ("shift_audit.csv", "overlap_pairs.csv", "review_queue.csv", "summary.json"):
                self.assertEqual((root / "a" / name).read_bytes(), (root / "b" / name).read_bytes())

    @staticmethod
    def make_rows(rows):
        return [dict(zip(audit.REQUIRED, row)) for row in rows]

    @staticmethod
    def write_rows(rows, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=audit.REQUIRED, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        return path

    def run_rows(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.write_rows(rows, Path(tmp) / "input.csv")
            return audit.build(source, Path(tmp) / "out")


if __name__ == "__main__":
    unittest.main()
