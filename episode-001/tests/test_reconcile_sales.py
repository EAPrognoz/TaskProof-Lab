import csv
import json
import tempfile
import unittest
from pathlib import Path

from reconcile_sales import process_file


HERE = Path(__file__).resolve().parents[1]


class ReconcileSalesTests(unittest.TestCase):
    def test_synthetic_example_reconciles_each_input_row(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            summary = process_file(HERE / "examples" / "sales_export_synthetic.csv", out)
            self.assertEqual(summary["input_data_rows"], 15)
            self.assertEqual(summary["approved_unique_orders"], 7)
            self.assertEqual(summary["review_rows"], 6)
            self.assertEqual(summary["cancelled_rows"], 1)
            self.assertEqual(summary["exact_duplicate_rows_removed"], 1)
            self.assertEqual(summary["reconciled_rows"], 15)
            self.assertEqual(summary["approved_sample_total"], "946.00")

            with (out / "totals_by_region.csv").open(newline="", encoding="utf-8") as f:
                totals = {row["region"]: row for row in csv.DictReader(f)}
            self.assertEqual(totals["East"]["net_sales"], "360.00")
            self.assertEqual(totals["South"]["net_sales"], "228.00")
            self.assertEqual(totals["North"]["net_sales"], "230.00")
            self.assertEqual(totals["West"]["net_sales"], "128.00")

            with (out / "duplicate_log.csv").open(newline="", encoding="utf-8") as f:
                duplicates = list(csv.DictReader(f))
            self.assertEqual(len(duplicates), 1)
            self.assertEqual(duplicates[0]["order_id"], "SO-1044")

            with (out / "review_queue.csv").open(newline="", encoding="utf-8") as f:
                review = list(csv.DictReader(f))
            reasons = " ".join(row["reason"] for row in review)
            self.assertIn("conflicting rows share", reasons)
            self.assertIn("unit price must be a number", reasons)
            self.assertIn("date is blank or not", reasons)

            on_disk = json.loads((out / "reconciliation_summary.json").read_text())
            self.assertEqual(on_disk["row_check"], summary["row_check"])

    def test_missing_required_column_fails_clearly(self):
        with tempfile.TemporaryDirectory() as temp:
            input_path = Path(temp) / "bad.csv"
            input_path.write_text("order_id,region\n1,East\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing required columns"):
                process_file(input_path, Path(temp) / "out")


if __name__ == "__main__":
    unittest.main()
