import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


EPISODE = Path(__file__).resolve().parents[1]
SCRIPT = EPISODE / "plan_renames.py"


def write_plan(path: Path, rows: list[tuple[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source_name", "new_name"])
        writer.writerows(rows)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_cli(source: Path, plan: Path, target: Path, report: Path,
            summary: Path, apply: bool = False) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPT), "--source-dir", str(source),
               "--plan", str(plan), "--target-dir", str(target),
               "--report", str(report), "--summary", str(summary)]
    if apply:
        command.append("--apply")
    return subprocess.run(command, capture_output=True, text=True, check=False)


class SafeRenameTests(unittest.TestCase):
    def test_bundled_synthetic_plan_matches_expected_row_accounting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = EPISODE / "examples" / "inbox"
            plan = EPISODE / "examples" / "rename_plan.csv"
            before = {p.name: digest(p) for p in source.iterdir()} if source.exists() else {}

            result = run_cli(source, plan, root / "copies", root / "manifest.csv", root / "summary.json")

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["plan_rows"], 7)
            self.assertEqual(summary["ready_to_copy"], 3)
            self.assertEqual(summary["blocked_plan_collision"], 2)
            self.assertEqual(summary["blocked_unsafe_name"], 1)
            self.assertEqual(summary["blocked_source_missing"], 1)
            self.assertEqual({p.name: digest(p) for p in source.iterdir()}, before)

    def test_dry_run_flags_plan_collisions_missing_and_unsafe_names_without_writing_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "inbox"
            source.mkdir()
            (source / "scan_001.txt").write_text("synthetic one\n", encoding="utf-8")
            (source / "scan_002.txt").write_text("synthetic two\n", encoding="utf-8")
            (source / "scan_003.txt").write_text("synthetic three\n", encoding="utf-8")
            before = {p.name: digest(p) for p in source.iterdir()}
            plan = root / "plan.csv"
            write_plan(plan, [
                ("scan_001.txt", "2026-10-25_NORTH_101.txt"),
                ("scan_002.txt", "2026-10-25_SOUTH_102.txt"),
                ("scan_003.txt", "2026-10-25_NORTH_101.txt"),
                ("scan_001.txt", "../outside.txt"),
                ("missing.txt", "2026-10-25_WEST_104.txt"),
            ])
            target, report, summary_path = root / "copies", root / "manifest.csv", root / "summary.json"

            result = run_cli(source, plan, target, report, summary_path)

            self.assertEqual(result.returncode, 0, result.stderr)
            with report.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["status"] for row in rows], [
                "BLOCKED_PLAN_COLLISION", "READY_DRY_RUN", "BLOCKED_PLAN_COLLISION",
                "BLOCKED_UNSAFE_NAME", "BLOCKED_SOURCE_MISSING",
            ])
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["plan_rows"], 5)
            self.assertEqual(summary["ready_to_copy"], 1)
            self.assertEqual(summary["blocked_plan_collision"], 2)
            self.assertEqual(summary["blocked_unsafe_name"], 1)
            self.assertEqual(summary["blocked_source_missing"], 1)
            self.assertFalse(target.exists())
            self.assertEqual({p.name: digest(p) for p in source.iterdir()}, before)

    def test_apply_copies_only_ready_rows_and_preserves_originals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "inbox"
            source.mkdir()
            (source / "scan_001.txt").write_text("synthetic one\n", encoding="utf-8")
            (source / "scan_002.txt").write_text("synthetic two\n", encoding="utf-8")
            (source / "scan_003.txt").write_text("synthetic three\n", encoding="utf-8")
            before = {p.name: digest(p) for p in source.iterdir()}
            plan = root / "plan.csv"
            write_plan(plan, [
                ("scan_001.txt", "2026-10-25_NORTH_101.txt"),
                ("scan_002.txt", "2026-10-25_SOUTH_102.txt"),
                ("scan_003.txt", "2026-10-25_NORTH_101.txt"),
            ])
            target, report, summary_path = root / "copies", root / "manifest.csv", root / "summary.json"

            result = run_cli(source, plan, target, report, summary_path, apply=True)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sorted(p.name for p in target.iterdir()), ["2026-10-25_SOUTH_102.txt"])
            self.assertEqual(digest(target / "2026-10-25_SOUTH_102.txt"), before["scan_002.txt"])
            self.assertEqual({p.name: digest(p) for p in source.iterdir()}, before)
            with report.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["status"] for row in rows], [
                "BLOCKED_PLAN_COLLISION", "COPIED", "BLOCKED_PLAN_COLLISION",
            ])

    def test_existing_target_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "inbox"
            target = root / "copies"
            source.mkdir()
            target.mkdir()
            (source / "scan.txt").write_text("source bytes\n", encoding="utf-8")
            existing = target / "final.txt"
            existing.write_text("keep these bytes\n", encoding="utf-8")
            plan = root / "plan.csv"
            write_plan(plan, [("scan.txt", "final.txt")])

            result = run_cli(source, plan, target, root / "manifest.csv", root / "summary.json", apply=True)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep these bytes\n")
            with (root / "manifest.csv").open(newline="", encoding="utf-8") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["status"], "BLOCKED_TARGET_EXISTS")

    def test_apply_rejects_a_destination_inside_the_source_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "inbox"
            source.mkdir()
            (source / "scan.txt").write_text("synthetic\n", encoding="utf-8")
            plan = root / "plan.csv"
            write_plan(plan, [("scan.txt", "renamed.txt")])

            result = run_cli(source, plan, source / "copies", root / "manifest.csv", root / "summary.json", apply=True)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("target directory must be outside source directory", result.stderr)
            self.assertEqual(sorted(p.name for p in source.iterdir()), ["scan.txt"])

    def test_report_cannot_overwrite_a_source_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "inbox"
            source.mkdir()
            source_file = source / "scan.txt"
            source_file.write_text("preserve these source bytes\n", encoding="utf-8")
            before = digest(source_file)
            plan = root / "plan.csv"
            write_plan(plan, [("scan.txt", "renamed.txt")])

            result = run_cli(source, plan, root / "copies", source_file, root / "summary.json")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("report and summary must be outside source and target folders", result.stderr)
            self.assertEqual(digest(source_file), before)

    def test_plan_missing_required_column_fails_clearly(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "inbox"
            source.mkdir()
            (source / "scan.txt").write_text("synthetic\n", encoding="utf-8")
            plan = root / "bad.csv"
            plan.write_text("source_name,wrong_column\nscan.txt,new.txt\n", encoding="utf-8")

            result = run_cli(source, plan, root / "copies", root / "manifest.csv", root / "summary.json")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing required columns", result.stderr)


if __name__ == "__main__":
    unittest.main()
