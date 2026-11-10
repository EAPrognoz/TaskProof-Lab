#!/usr/bin/env python3
"""Audit a synthetic timesheet CSV for missing data and overlapping shifts.

This local report does not calculate pay, change a schedule, or edit the input.
Overlaps are detected within the same employee using half-open intervals [start, end).
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REQUIRED = ("shift_id", "employee_id", "shift_date", "start_time", "end_time")
AUDIT_FIELDS = (
    "source_row", "shift_id", "employee_id", "start", "end", "duration_minutes",
    "status", "overlaps_with",
)
REVIEW_FIELDS = (*REQUIRED, "source_row", "issue")
PAIR_FIELDS = ("shift_id_a", "shift_id_b", "employee_id", "overlap_start", "overlap_end", "overlap_minutes")


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not set(REQUIRED).issubset(reader.fieldnames):
            raise ValueError(f"{path.name} must contain columns: {', '.join(REQUIRED)}")
        return [dict(row) for row in reader]


def parse_date(value: str) -> date:
    raw = value.strip()
    parsed = date.fromisoformat(raw)
    if parsed.isoformat() != raw:
        raise ValueError("date must use YYYY-MM-DD")
    return parsed


def parse_time(value: str) -> time:
    raw = value.strip()
    parsed = datetime.strptime(raw, "%H:%M").time()
    if parsed.strftime("%H:%M") != raw:
        raise ValueError("time must use 24-hour HH:MM")
    return parsed


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def build(input_path: Path, output_dir: Path) -> dict[str, Any]:
    rows = read_rows(input_path)
    id_groups: dict[str, list[int]] = defaultdict(list)
    for idx, row in enumerate(rows):
        key = row.get("shift_id", "").strip().upper()
        if key:
            id_groups[key].append(idx)

    review: list[dict[str, Any]] = []
    valid: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        source_row = idx + 2  # Header occupies CSV line 1.
        shift_id = row.get("shift_id", "").strip()
        employee_id = row.get("employee_id", "").strip()
        issue = ""
        if not shift_id:
            issue = "missing shift_id"
        elif len(id_groups[shift_id.upper()]) > 1:
            issue = f"duplicate shift_id ({len(id_groups[shift_id.upper()])} rows); review all matches"
        elif not employee_id:
            issue = "missing employee_id"
        else:
            raw_date = row.get("shift_date", "").strip()
            raw_start = row.get("start_time", "").strip()
            raw_end = row.get("end_time", "").strip()
            if not raw_date:
                issue = "missing shift_date"
            elif not raw_start:
                issue = "missing start_time"
            elif not raw_end:
                issue = "missing end_time"
            else:
                try:
                    work_date = parse_date(raw_date)
                except (ValueError, TypeError, AttributeError):
                    issue = "invalid shift_date (expected YYYY-MM-DD)"
                if not issue:
                    try:
                        start_time = parse_time(raw_start)
                    except (ValueError, TypeError, AttributeError):
                        issue = "invalid start_time (expected HH:MM)"
                if not issue:
                    try:
                        end_time = parse_time(raw_end)
                    except (ValueError, TypeError, AttributeError):
                        issue = "invalid end_time (expected HH:MM)"
                if not issue:
                    start = datetime.combine(work_date, start_time)
                    end = datetime.combine(work_date, end_time)
                    if end_time < start_time:
                        end += timedelta(days=1)
                    elif end_time == start_time:
                        issue = "start and end are equal; duration is ambiguous"
        if issue:
            review.append({**{field: row.get(field, "") for field in REQUIRED},
                           "source_row": source_row, "issue": issue})
            continue
        valid.append({
            "source_row": source_row,
            "shift_id": shift_id,
            "employee_id": employee_id,
            "start_dt": start,
            "end_dt": end,
            "duration_minutes": int((end - start).total_seconds() // 60),
        })

    by_employee: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in valid:
        by_employee[item["employee_id"].strip().upper()].append(item)
    pair_rows: list[dict[str, Any]] = []
    conflict_rows: set[int] = set()
    overlaps_with: dict[int, set[str]] = defaultdict(set)
    for employee_rows in by_employee.values():
        ordered = sorted(employee_rows, key=lambda r: (r["start_dt"], r["end_dt"], r["source_row"]))
        for i, left in enumerate(ordered):
            for right in ordered[i + 1:]:
                if right["start_dt"] >= left["end_dt"]:
                    break
                overlap_start = max(left["start_dt"], right["start_dt"])
                overlap_end = min(left["end_dt"], right["end_dt"])
                if overlap_start < overlap_end:
                    conflict_rows.update((left["source_row"], right["source_row"]))
                    overlaps_with[left["source_row"]].add(right["shift_id"])
                    overlaps_with[right["source_row"]].add(left["shift_id"])
                    pair_rows.append({
                        "shift_id_a": left["shift_id"], "shift_id_b": right["shift_id"],
                        "employee_id": left["employee_id"],
                        "overlap_start": overlap_start.isoformat(timespec="minutes"),
                        "overlap_end": overlap_end.isoformat(timespec="minutes"),
                        "overlap_minutes": int((overlap_end - overlap_start).total_seconds() // 60),
                    })

    audit: list[dict[str, Any]] = []
    for item in sorted(valid, key=lambda r: r["source_row"]):
        source_row = item["source_row"]
        ids = sorted(overlaps_with[source_row])
        audit.append({
            "source_row": source_row,
            "shift_id": item["shift_id"],
            "employee_id": item["employee_id"],
            "start": item["start_dt"].isoformat(timespec="minutes"),
            "end": item["end_dt"].isoformat(timespec="minutes"),
            "duration_minutes": item["duration_minutes"],
            "status": "OVERLAP" if source_row in conflict_rows else "CLEAR",
            "overlaps_with": ";".join(ids),
        })

    clear_rows = sum(row["status"] == "CLEAR" for row in audit)
    overlap_rows = sum(row["status"] == "OVERLAP" for row in audit)
    issue_counts = Counter(row["issue"].split(" (")[0] for row in review)
    summary = {
        "synthetic_example": True,
        "input_rows": len(rows),
        "valid_unique_rows": len(valid),
        "clear_rows": clear_rows,
        "overlap_rows": overlap_rows,
        "overlap_pairs": len(pair_rows),
        "review_rows": len(review),
        "accounted_rows": clear_rows + overlap_rows + len(review),
        "row_reconciliation": f"{len(rows)} = {clear_rows} clear + {overlap_rows} overlap + {len(review)} review",
        "review_issue_counts": dict(sorted(issue_counts.items())),
        "overlap_rule": "Trimmed, case-insensitive employee ID match; half-open intervals [start, end); touching shifts do not overlap; end before start means next-day end.",
        "side_effects": "Writes local reports only; does not edit the input, change schedules, or calculate pay.",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "shift_audit.csv", AUDIT_FIELDS, audit)
    write_csv(output_dir / "overlap_pairs.csv", PAIR_FIELDS, pair_rows)
    write_csv(output_dir / "review_queue.csv", REVIEW_FIELDS, review)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "examples" / "timesheets.csv")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    args = parser.parse_args()
    print(json.dumps(build(args.input, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
