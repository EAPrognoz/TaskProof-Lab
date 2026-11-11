"""CLI-level regressions for malformed EP008 CSV structure."""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "build_reorder_shortlist.py"
OUTPUT_NAMES = ("reorder_shortlist.csv", "review_queue.csv", "summary.json")
INV_HEADER = "sku,item_name,on_hand\n"
RULE_HEADER = "sku,reorder_point,target_stock,pack_size\n"


class ShortCsvCliRegressionTests(unittest.TestCase):
    def run_cli(self, inventory: str, rules: str, *, seed_outputs: bool = False):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        inventory_path = root / "inventory.csv"
        rules_path = root / "rules.csv"
        output = root / "output"
        inventory_path.write_text(inventory, encoding="utf-8")
        rules_path.write_text(rules, encoding="utf-8")
        output.mkdir()
        if seed_outputs:
            for name in OUTPUT_NAMES:
                (output / name).write_text("stale report", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--inventory", str(inventory_path),
             "--rules", str(rules_path), "--output-dir", str(output)],
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

    def test_multiple_short_inventory_lengths_fail_and_do_not_reorder_good_duplicate(self):
        rules = RULE_HEADER + "D,1,5,2\n"
        for short in ("D", "D,Widget"):
            with self.subTest(short=short):
                proc, files = self.run_cli(INV_HEADER + "D,Widget,1\n" + short + "\n",
                                           rules, seed_outputs=True)
                self.assert_failed_closed(proc, files, "inventory.csv", "source record 3",
                                          "expected 3 fields")

    def test_empty_final_inventory_cell_is_review_but_absent_cell_fails_closed(self):
        rules = RULE_HEADER + "A,2,10,5\nB,2,10,5\n"
        proc, files = self.run_cli(INV_HEADER + "A,Widget,\nB,Good,1\n", rules)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(files["summary.json"])
        self.assertEqual(summary["inventory_rows"], 2)
        self.assertEqual(summary["review_rows"], 1)
        self.assertEqual(summary["reorder_suggestions"], 1)
        self.assertEqual(summary["accounted_inventory_rows"], 2)
        review = list(csv.DictReader(files["review_queue.csv"].splitlines()))
        self.assertEqual(review[0]["source_row"], "2")
        self.assertIn("on_hand", review[0]["issue"])

        proc, files = self.run_cli(INV_HEADER + "A,Widget\nB,Good,1\n", rules,
                                   seed_outputs=True)
        self.assert_failed_closed(proc, files, "inventory.csv", "source record 2",
                                  "expected 3 fields", "on_hand")

    def test_short_rules_row_fails_closed_before_matching_inventory(self):
        inventory = INV_HEADER + "A,Widget,1\nB,Good,9\n"
        for rule_row in ("A,2,10", "A,2,10,"):
            with self.subTest(rule_row=rule_row):
                proc, files = self.run_cli(inventory, RULE_HEADER + rule_row + "\nB,2,10,5\n",
                                           seed_outputs=True)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertEqual(proc.stdout, "")
                self.assertNotIn("Traceback", proc.stderr)
                self.assertIn("rules.csv", proc.stderr)
                self.assertIn("source record 2", proc.stderr)
                self.assertEqual(files, {})

    def test_failed_run_removes_reports_from_a_prior_successful_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "inventory.csv"
            rules = root / "rules.csv"
            output = root / "output"
            inventory.write_text(INV_HEADER + "A,Widget,1\n", encoding="utf-8")
            rules.write_text(RULE_HEADER + "A,2,10,5\n", encoding="utf-8")
            first = subprocess.run(
                [sys.executable, str(SCRIPT), "--inventory", str(inventory), "--rules", str(rules),
                 "--output-dir", str(output)], capture_output=True, text=True, check=False,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual({path.name for path in output.iterdir()}, set(OUTPUT_NAMES))

            inventory.write_text(INV_HEADER + "A,Widget\n", encoding="utf-8")
            second = subprocess.run(
                [sys.executable, str(SCRIPT), "--inventory", str(inventory), "--rules", str(rules),
                 "--output-dir", str(output)], capture_output=True, text=True, check=False,
            )
            self.assertEqual(second.returncode, 2, second.stderr)
            self.assertNotIn("Traceback", second.stderr)
            self.assertIn("inventory.csv", second.stderr)
            self.assertEqual(list(output.iterdir()), [])

    def test_missing_and_orphan_rule_skus_are_reported(self):
        inventory = INV_HEADER + "A,Widget,1\n"
        for rule_row, expected in ((",2,10,5", "sku"),
                                   ("ORPHAN,2,10,5", "no matching inventory")):
            with self.subTest(rule_row=rule_row):
                proc, files = self.run_cli(inventory, RULE_HEADER + rule_row + "\n")
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn("rules.csv", proc.stderr)
                self.assertIn("source record 2", proc.stderr)
                self.assertIn(expected, proc.stderr.lower())
                self.assertEqual(files, {})

    def test_extra_cells_duplicate_headers_and_extra_columns_fail_closed(self):
        cases = (
            (INV_HEADER + "A,Widget,1,unexpected\n", RULE_HEADER + "A,2,10,5\n",
             "inventory.csv", "extra"),
            ("sku,item_name,on_hand,on_hand\nA,Widget,1,2\n", RULE_HEADER + "A,2,10,5\n",
             "inventory.csv", "duplicate header"),
            ("sku,item_name,on_hand,note\nA,Widget,1,x\n", RULE_HEADER + "A,2,10,5\n",
             "inventory.csv", "unexpected column"),
            (INV_HEADER + "A,Widget,1\n", RULE_HEADER + "A,2,10,5,x\n",
             "rules.csv", "extra"),
        )
        for inventory, rules, filename, expected in cases:
            with self.subTest(filename=filename, expected=expected):
                proc, files = self.run_cli(inventory, rules)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn(filename, proc.stderr)
                self.assertIn(expected, proc.stderr.lower())
                self.assertEqual(files, {})

    def test_unclosed_quote_and_blank_physical_row_fail_without_reports(self):
        cases = (
            (INV_HEADER + 'A,"Widget\nB,Other,1\n', "malformed CSV"),
            (INV_HEADER + "\nA,Widget,1\n", "source record 2"),
        )
        for inventory, expected in cases:
            with self.subTest(expected=expected):
                proc, files = self.run_cli(inventory, RULE_HEADER + "A,2,10,5\nB,2,10,5\n")
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn("inventory.csv", proc.stderr)
                self.assertIn(expected, proc.stderr)
                self.assertEqual(files, {})

    def test_missing_header_key_is_a_structured_cli_error(self):
        proc, files = self.run_cli("sku,item_name\nA,Widget\n", RULE_HEADER + "A,2,10,5\n")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("inventory.csv", proc.stderr)
        self.assertIn("on_hand", proc.stderr)
        self.assertEqual(files, {})

    def test_multiline_csv_record_keeps_later_source_record_index(self):
        inventory = INV_HEADER + 'A,"Widget\nBlue",5\nB, ,\n'
        rules = RULE_HEADER + "A,2,10,5\nB,2,10,5\n"
        proc, files = self.run_cli(inventory, rules)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(files["summary.json"])
        self.assertEqual(summary["inventory_rows"], 2)
        self.assertEqual(summary["review_rows"], 1)
        review = list(csv.DictReader(files["review_queue.csv"].splitlines()))
        self.assertEqual(review[0]["source_row"], "3")

    def test_quoted_comma_and_escaped_quote_in_item_name_are_accepted(self):
        inventory = INV_HEADER + 'A,"Widget, ""Blue""",1\n'
        rules = RULE_HEADER + "A,1,5,2\n"
        proc, files = self.run_cli(inventory, rules)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rows = list(csv.DictReader(files["reorder_shortlist.csv"].splitlines()))
        self.assertEqual(rows[0]["item_name"], 'Widget, "Blue"')

    def test_input_report_name_collision_matrix_preserves_valid_and_short_inputs(self):
        inventory_header = INV_HEADER
        rule_header = RULE_HEADER
        for input_role, name, valid in (
            (role, name, valid)
            for role in ("inventory", "rules")
            for name in OUTPUT_NAMES
            for valid in (False, True)
        ):
            with self.subTest(input_role=input_role, name=name, valid=valid):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    output = root / "output"
                    output.mkdir()
                    colliding = output / name
                    if input_role == "inventory":
                        source = inventory_header + ("A,Widget,1\n" if valid else "A,Widget\n")
                        other = root / "rules.csv"
                        other.write_text(rule_header + "A,1,5,2\n", encoding="utf-8")
                    else:
                        source = rule_header + ("A,1,5,2\n" if valid else "A,1,5\n")
                        other = root / "inventory.csv"
                        other.write_text(inventory_header + "A,Widget,1\n", encoding="utf-8")
                    colliding.write_text(source, encoding="utf-8")
                    original_bytes = colliding.read_bytes()
                    other_report_bytes = {}
                    for report_name in OUTPUT_NAMES:
                        if report_name != name:
                            other_report_bytes[report_name] = b"prior report sentinel\n"
                            (output / report_name).write_bytes(other_report_bytes[report_name])
                    unrelated = output / "unrelated.txt"
                    unrelated.write_bytes(b"keep me\n")

                    if input_role == "inventory":
                        input_arg = f"output/./{name}"
                        rules_arg = str(other)
                        inventory_arg = input_arg
                    else:
                        input_arg = str(colliding)
                        rules_arg = input_arg
                        inventory_arg = str(other)
                    proc = subprocess.run(
                        [sys.executable, str(SCRIPT), "--inventory", inventory_arg,
                         "--rules", rules_arg, "--output-dir", "output"],
                        cwd=root, capture_output=True, text=True, check=False,
                    )
                    self.assertEqual(proc.returncode, 2, proc.stderr)
                    self.assertIn("input/output path collision", proc.stderr)
                    self.assertNotIn("Traceback", proc.stderr)
                    self.assertEqual(colliding.read_bytes(), original_bytes)
                    self.assertEqual(unrelated.read_bytes(), b"keep me\n")
                    for report_name, expected in other_report_bytes.items():
                        self.assertEqual((output / report_name).read_bytes(), expected)

    def test_symlink_and_hardlink_report_aliases_are_preserved(self):
        for link_kind in ("symlink", "hardlink"):
            with self.subTest(link_kind=link_kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / "output"
                output.mkdir()
                inventory = root / "inventory.csv"
                inventory.write_text(INV_HEADER + "A,Widget,1\n", encoding="utf-8")
                rules = root / "rules.csv"
                rules.write_text(RULE_HEADER + "A,1,5,2\n", encoding="utf-8")
                alias = output / "review_queue.csv"
                if link_kind == "symlink":
                    os.symlink(inventory, alias)
                else:
                    os.link(inventory, alias)
                before = inventory.read_bytes()
                unrelated = output / "unrelated.txt"
                unrelated.write_text("keep me\n", encoding="utf-8")
                proc = subprocess.run(
                    [sys.executable, str(SCRIPT), "--inventory", str(inventory),
                     "--rules", str(rules), "--output-dir", str(output)],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn("input/output path collision", proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                self.assertEqual(inventory.read_bytes(), before)
                self.assertTrue(alias.exists())
                self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep me\n")

    def test_cleanup_blocked_by_report_directory_is_structured_and_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "inventory.csv"
            rules = root / "rules.csv"
            output = root / "output"
            output.mkdir()
            inventory.write_text(INV_HEADER + "A,Widget\n", encoding="utf-8")
            rules.write_text(RULE_HEADER + "A,1,5,2\n", encoding="utf-8")
            blocked = output / "reorder_shortlist.csv"
            blocked.mkdir()
            (blocked / "unrelated.txt").write_text("keep me\n", encoding="utf-8")
            for name in ("review_queue.csv", "summary.json"):
                (output / name).write_text("stale report\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--inventory", str(inventory), "--rules", str(rules),
                 "--output-dir", str(output)], capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("inventory.csv", proc.stderr)
            self.assertIn("cleanup incomplete", proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
            self.assertEqual((blocked / "unrelated.txt").read_text(encoding="utf-8"), "keep me\n")
            self.assertFalse((output / "review_queue.csv").exists())
            self.assertFalse((output / "summary.json").exists())

    def test_invalid_utf8_error_names_source_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "broken_inventory.csv"
            rules = root / "rules.csv"
            output = root / "output"
            inventory.write_bytes(b"sku,item_name,on_hand\nA,\xff,1\n")
            rules.write_text(RULE_HEADER + "A,1,5,2\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--inventory", str(inventory), "--rules", str(rules),
                 "--output-dir", str(output)], capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("broken_inventory.csv", proc.stderr)
            self.assertIn("UTF-8", proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
