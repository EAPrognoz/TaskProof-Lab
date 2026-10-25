import csv
import json
import tempfile
import unittest
from pathlib import Path

from reconcile_exports import reconcile


ROOT = Path(__file__).resolve().parents[1]


class ExportReconciliationTests(unittest.TestCase):
    def test_synthetic_example_accounts_for_each_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "results"
            summary = reconcile(ROOT / "examples/previous_export.csv",
                                ROOT / "examples/current_export.csv", output)
            self.assertEqual(summary["previous_source_rows"], 10)
            self.assertEqual(summary["current_source_rows"], 11)
            self.assertEqual(summary["unchanged_matches"], 5)
            self.assertEqual(summary["changed_matches"], 2)
            self.assertEqual(summary["removed_from_current"], 2)
            self.assertEqual(summary["added_in_current"], 1)
            self.assertEqual(summary["review_previous_rows"], 1)
            self.assertEqual(summary["review_current_rows"], 3)
            self.assertTrue(summary["previous_row_check_passed"])
            self.assertTrue(summary["current_row_check_passed"])
            self.assertEqual(json.loads((output / "summary.json").read_text()), summary)
            with (output / "changed.csv").open(newline="", encoding="utf-8") as f:
                changed = list(csv.DictReader(f))
            self.assertEqual({row["record_id"] for row in changed}, {"R-2403", "R-2404"})
            by_id = {row["record_id"]: row for row in changed}
            self.assertEqual(by_id["R-2403"]["previous_unit_price"], "145.00")
            self.assertEqual(by_id["R-2403"]["current_unit_price"], "149.00")
            self.assertEqual(by_id["R-2404"]["previous_quantity"], "3")
            self.assertEqual(by_id["R-2404"]["current_quantity"], "4")

    def test_duplicate_key_quarantines_both_sides_instead_of_marking_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            previous = folder / "previous.csv"
            current = folder / "current.csv"
            header = "record_id,invoice_date,customer_code,sku,quantity,unit_price,status\n"
            previous.write_text(header + "A,2026-10-01,C1,SKU,1,2.00,posted\n", encoding="utf-8")
            current.write_text(header + "A,2026-10-01,C1,SKU,1,2.00,posted\nA,2026-10-01,C1,SKU,2,2.00,posted\n", encoding="utf-8")
            summary = reconcile(previous, current, folder / "out")
            self.assertEqual(summary["removed_from_current"], 0)
            self.assertEqual(summary["review_previous_rows"], 1)
            self.assertEqual(summary["review_current_rows"], 2)
            self.assertEqual(summary["ambiguous_keys"], ["A"])

    def test_missing_required_column_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            bad = folder / "bad.csv"
            good = folder / "good.csv"
            bad.write_text("record_id,invoice_date\nA,2026-10-01\n", encoding="utf-8")
            good.write_text("record_id,invoice_date,customer_code,sku,quantity,unit_price,status\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing required columns"):
                reconcile(bad, good, folder / "out")


if __name__ == "__main__":
    unittest.main()
