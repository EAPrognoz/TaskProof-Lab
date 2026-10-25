#!/usr/bin/env python3
"""Plan safe file-name copies from a CSV; dry-run by default and never overwrite."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
REQUIRED = ("source_name", "new_name")
REPORT_FIELDS = (
    "plan_row", "source_name", "new_name", "status", "reason",
    "source_sha256", "copy_sha256",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_basename(value: str) -> bool:
    """Accept one literal filename only; reject paths and ambiguous whitespace."""
    return bool(value) and value == value.strip() and value not in {".", ".."} \
        and "\x00" not in value and "/" not in value and "\\" not in value \
        and Path(value).name == value


def read_plan(plan_path: Path) -> list[dict[str, Any]]:
    with plan_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("plan CSV has no header row")
        mapped = {name.strip().casefold(): name for name in reader.fieldnames if name}
        missing = [field for field in REQUIRED if field not in mapped]
        if missing:
            raise ValueError("missing required columns: " + ", ".join(missing))
        rows = []
        for row_number, values in enumerate(reader, start=2):
            source_name = values.get(mapped["source_name"]) or ""
            new_name = values.get(mapped["new_name"]) or ""
            if not source_name and not new_name:
                continue
            rows.append({
                "plan_row": row_number,
                "source_name": source_name,
                "new_name": new_name,
                "status": "",
                "reason": "",
                "source_sha256": "",
                "copy_sha256": "",
            })
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def validate_output_paths(source_dir: Path, plan_path: Path, target_dir: Path,
                          report_path: Path, summary_path: Path) -> None:
    source_dir = source_dir.expanduser().resolve()
    plan_path = plan_path.expanduser().resolve()
    target_dir = target_dir.expanduser().resolve()
    raw_outputs = [report_path.expanduser(), summary_path.expanduser()]
    if any(path.is_symlink() for path in raw_outputs):
        raise ValueError("report and summary cannot be symbolic links")
    outputs = [path.resolve() for path in raw_outputs]
    if outputs[0] == outputs[1]:
        raise ValueError("report and summary paths must be different")
    for output in outputs:
        if output.is_relative_to(source_dir) or output.is_relative_to(target_dir):
            raise ValueError("report and summary must be outside source and target folders")
        if output == plan_path:
            raise ValueError("report and summary must not overwrite the plan CSV")
        if output.exists() and not output.is_file():
            raise ValueError("report and summary paths must be regular files or new file names")
    existing_protected = [plan_path]
    if source_dir.is_dir():
        existing_protected.extend(path for path in source_dir.iterdir() if path.is_file())
    if target_dir.is_dir():
        existing_protected.extend(path for path in target_dir.iterdir() if path.is_file())
    for output in outputs:
        if output.exists():
            for protected in existing_protected:
                if protected.exists() and output.samefile(protected):
                    raise ValueError("report and summary must not overwrite a plan, source, or target file")


def plan_copies(source_dir: Path, plan_path: Path, target_dir: Path,
                apply: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_dir = source_dir.expanduser().resolve()
    plan_path = plan_path.expanduser().resolve()
    target_dir = target_dir.expanduser().resolve()
    if not source_dir.is_dir():
        raise ValueError(f"source directory does not exist or is not a directory: {source_dir}")
    if target_dir == source_dir or target_dir.is_relative_to(source_dir):
        raise ValueError("target directory must be outside source directory")
    if target_dir.exists() and not target_dir.is_dir():
        raise ValueError(f"target path exists and is not a directory: {target_dir}")

    rows = read_plan(plan_path)
    source_hashes: dict[str, str] = {}
    source_paths: dict[int, Path] = {}
    unsafe_rows: set[int] = set()
    for item in rows:
        source_name = item["source_name"]
        new_name = item["new_name"]
        if not safe_basename(source_name) or not safe_basename(new_name):
            item["status"] = "BLOCKED_UNSAFE_NAME"
            item["reason"] = "source and target must each be a simple file name, not a path"
            unsafe_rows.add(item["plan_row"])
            continue
        if Path(source_name).suffix.casefold() != Path(new_name).suffix.casefold():
            item["status"] = "BLOCKED_EXTENSION_CHANGE"
            item["reason"] = "file extension must stay the same"
            unsafe_rows.add(item["plan_row"])
            continue
        source = source_dir / source_name
        if source.is_symlink() or not source.is_file():
            item["status"] = "BLOCKED_SOURCE_MISSING"
            item["reason"] = "source is missing or is not a regular file"
            continue
        source_paths[item["plan_row"]] = source
        item["source_sha256"] = sha256(source)
        source_hashes[source_name] = item["source_sha256"]

    source_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    target_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in rows:
        if item["plan_row"] in unsafe_rows:
            continue
        if safe_basename(item["source_name"]):
            source_groups[item["source_name"].casefold()].append(item)
        if safe_basename(item["new_name"]):
            target_groups[item["new_name"].casefold()].append(item)

    duplicate_source_rows = {
        item["plan_row"]
        for group in source_groups.values() if len(group) > 1
        for item in group
    }
    duplicate_target_rows = {
        item["plan_row"]
        for group in target_groups.values() if len(group) > 1
        for item in group
    }
    existing_targets = {item.name.casefold() for item in target_dir.iterdir()} if target_dir.is_dir() else set()

    for item in rows:
        if item["status"]:
            continue
        if item["plan_row"] in duplicate_source_rows:
            item["status"] = "BLOCKED_SOURCE_COLLISION"
            item["reason"] = "the same source file appears more than once in the plan"
        elif item["plan_row"] in duplicate_target_rows:
            item["status"] = "BLOCKED_PLAN_COLLISION"
            item["reason"] = "another plan row targets the same name, ignoring case"
        elif item["new_name"].casefold() in existing_targets:
            item["status"] = "BLOCKED_TARGET_EXISTS"
            item["reason"] = "a file or folder with this target name already exists"
        else:
            item["status"] = "READY_DRY_RUN"
            item["reason"] = "validated; no source file has been changed"

    ready_count = sum(row["status"] == "READY_DRY_RUN" for row in rows)
    if apply and ready_count:
        target_dir.mkdir(parents=True, exist_ok=True)
        for item in rows:
            if item["status"] != "READY_DRY_RUN":
                continue
            source = source_paths[item["plan_row"]]
            destination = target_dir / item["new_name"]
            try:
                if sha256(source) != item["source_sha256"]:
                    item["status"] = "BLOCKED_SOURCE_CHANGED"
                    item["reason"] = "source bytes changed after planning; no copy was made"
                    continue
                with source.open("rb") as source_handle, destination.open("xb") as destination_handle:
                    shutil.copyfileobj(source_handle, destination_handle)
                item["copy_sha256"] = sha256(destination)
                if item["copy_sha256"] != item["source_sha256"]:
                    destination.unlink(missing_ok=True)
                    item["copy_sha256"] = ""
                    item["status"] = "COPY_FAILED"
                    item["reason"] = "copy verification failed; incomplete copy removed"
                else:
                    item["status"] = "COPIED"
                    item["reason"] = "copy verified; original file remains in the source folder"
            except FileExistsError:
                item["status"] = "BLOCKED_TARGET_EXISTS"
                item["reason"] = "target appeared before copy; existing file was not overwritten"
            except OSError as exc:
                destination.unlink(missing_ok=True)
                item["status"] = "COPY_FAILED"
                item["reason"] = f"copy failed safely: {exc.__class__.__name__}"

    unchanged = all(
        (source_dir / name).is_file() and not (source_dir / name).is_symlink()
        and sha256(source_dir / name) == digest
        for name, digest in source_hashes.items()
    )
    counts = Counter(item["status"] for item in rows)
    summary = {
        "plan_rows": len(rows),
        "ready_to_copy": ready_count,
        "blocked_plan_collision": counts["BLOCKED_PLAN_COLLISION"],
        "blocked_source_collision": counts["BLOCKED_SOURCE_COLLISION"],
        "blocked_unsafe_name": counts["BLOCKED_UNSAFE_NAME"] + counts["BLOCKED_EXTENSION_CHANGE"],
        "blocked_source_missing": counts["BLOCKED_SOURCE_MISSING"],
        "blocked_target_exists": counts["BLOCKED_TARGET_EXISTS"],
        "copied": counts["COPIED"],
        "copy_failed": counts["COPY_FAILED"],
        "apply_requested": apply,
        "original_source_files_unchanged": unchanged,
    }
    return rows, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=ROOT / "examples" / "inbox")
    parser.add_argument("--plan", type=Path, default=ROOT / "examples" / "rename_plan.csv")
    parser.add_argument("--target-dir", type=Path, default=ROOT / "output" / "renamed_copy")
    parser.add_argument("--report", type=Path, default=ROOT / "output" / "dry_run_manifest.csv")
    parser.add_argument("--summary", type=Path, default=ROOT / "output" / "dry_run_summary.json")
    parser.add_argument("--apply", action="store_true",
                        help="copy ready files into target-dir; originals are never renamed or removed")
    args = parser.parse_args()
    try:
        validate_output_paths(args.source_dir, args.plan, args.target_dir, args.report, args.summary)
        rows, summary = plan_copies(args.source_dir, args.plan, args.target_dir, args.apply)
        write_csv(args.report, rows)
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, csv.Error) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
