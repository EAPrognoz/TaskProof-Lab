from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from build_reorder_shortlist import ROOT, build


class ReorderShortlistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name) / "output"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def read_rows(self, filename: str) -> list[dict[str, str]]:
        with (self.output / filename).open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_synthetic_example_accounts_for_every_inventory_row(self) -> None:
        result = build(ROOT / "examples/inventory.csv", ROOT / "examples/reorder_rules.csv", self.output)
        self.assertEqual((8, 3, 2, 3), (
            result["inventory_rows"], result["reorder_suggestions"],
            result["above_threshold"], result["review_rows"],
        ))
        self.assertEqual(8, result["accounted_inventory_rows"])

    def test_equal_to_reorder_point_triggers_shortlist(self) -> None:
        result = build(ROOT / "examples/inventory.csv", ROOT / "examples/reorder_rules.csv", self.output)
        self.assertIn("SKU-103", {row["sku"] for row in self.read_rows("reorder_shortlist.csv")})
        self.assertEqual(3, result["reorder_suggestions"])

    def test_order_quantity_rounds_up_to_complete_pack(self) -> None:
        build(ROOT / "examples/inventory.csv", ROOT / "examples/reorder_rules.csv", self.output)
        row = next(row for row in self.read_rows("reorder_shortlist.csv") if row["sku"] == "SKU-101")
        self.assertEqual(("14", "18"), (row["recommended_order_qty"], row["projected_after_order"]))

    def test_above_threshold_is_not_ordered(self) -> None:
        build(ROOT / "examples/inventory.csv", ROOT / "examples/reorder_rules.csv", self.output)
        rows = (self.output / "summary.json").read_text()
        self.assertIn('"above_threshold": 2', rows)
        self.assertNotIn("SKU-102", {row["sku"] for row in self.read_rows("reorder_shortlist.csv")})

    def test_missing_rule_is_held_for_review(self) -> None:
        build(ROOT / "examples/inventory.csv", ROOT / "examples/reorder_rules.csv", self.output)
        row = next(row for row in self.read_rows("review_queue.csv") if row["sku"] == "SKU-106")
        self.assertEqual("no matching reorder rule", row["issue"])

    def test_duplicate_inventory_skus_hold_all_matching_rows(self) -> None:
        build(ROOT / "examples/inventory.csv", ROOT / "examples/reorder_rules.csv", self.output)
        rows = [row for row in self.read_rows("review_queue.csv") if row["sku"] == "SKU-107"]
        self.assertEqual(2, len(rows))
        self.assertTrue(all("duplicate inventory SKU" in row["issue"] for row in rows))

    def test_duplicate_rules_are_held_for_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            inventory = p / "inventory.csv"
            rules = p / "rules.csv"
            inventory.write_text("sku,item_name,on_hand\nX-1,Notebook,1\n", encoding="utf-8")
            rules.write_text("sku,reorder_point,target_stock,pack_size\nX-1,2,8,2\nX-1,3,9,3\n", encoding="utf-8")
            result = build(inventory, rules, self.output)
        self.assertEqual(1, result["review_rows"])
        self.assertIn("duplicate reorder rules", self.read_rows("review_queue.csv")[0]["issue"])

    def test_invalid_values_are_held_not_coerced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            inventory = p / "inventory.csv"
            rules = p / "rules.csv"
            inventory.write_text("sku,item_name,on_hand\nX-2,Paper,-1\n", encoding="utf-8")
            rules.write_text("sku,reorder_point,target_stock,pack_size\nX-2,2,8,2\n", encoding="utf-8")
            result = build(inventory, rules, self.output)
        self.assertEqual(1, result["review_rows"])
        self.assertIn("nonnegative", self.read_rows("review_queue.csv")[0]["issue"])

    def test_example_inputs_remain_unchanged(self) -> None:
        inventory = ROOT / "examples/inventory.csv"
        rules = ROOT / "examples/reorder_rules.csv"
        before = (inventory.read_bytes(), rules.read_bytes())
        build(inventory, rules, self.output)
        self.assertEqual(before, (inventory.read_bytes(), rules.read_bytes()))

    def test_repeated_run_is_deterministic(self) -> None:
        build(ROOT / "examples/inventory.csv", ROOT / "examples/reorder_rules.csv", self.output)
        first = {p.name: p.read_bytes() for p in self.output.iterdir()}
        build(ROOT / "examples/inventory.csv", ROOT / "examples/reorder_rules.csv", self.output)
        second = {p.name: p.read_bytes() for p in self.output.iterdir()}
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
