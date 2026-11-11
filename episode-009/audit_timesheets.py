#!/usr/bin/env python3
"""Audit a synthetic timesheet CSV for missing data and overlapping shifts.

This local report does not calculate pay, change a schedule, or edit the input.
Overlaps are detected within the same employee using half-open intervals [start, end).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
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


def validate_quote_placement(text: str, filename: str) -> None:
    """Reject quote characters outside CSV quoted fields before csv.reader parses."""
    state = "field_start"
    line = 1
    quote_start = 1
    index = 0
    while index < len(text):
        char = text[index]
        if state == "quoted":
            if char == '"':
                if index + 1 < len(text) and text[index + 1] == '"':
                    index += 2
                    continue
                state = "after_quote"
            elif char == "\r":
                line += 1
                if index + 1 < len(text) and text[index + 1] == "\n":
                    index += 1
            elif char == "\n":
                line += 1
        elif state == "after_quote":
            if char == ",":
                state = "field_start"
            elif char in "\r\n":
                state = "field_start"
                if char == "\r":
                    line += 1
                    if index + 1 < len(text) and text[index + 1] == "\n":
                        index += 1
                else:
                    line += 1
            else:
                raise ValueError(
                    f"{filename}: malformed CSV quote at physical line {line}: "
                    "unexpected character after closing quote"
                )
        elif char == '"':
            if state != "field_start":
                raise ValueError(
                    f"{filename}: malformed CSV quote at physical line {line}: "
                    "quote inside an unquoted field"
                )
            state = "quoted"
            quote_start = line
        elif char == ",":
            state = "field_start"
        elif char in "\r\n":
            state = "field_start"
            if char == "\r":
                line += 1
                if index + 1 < len(text) and text[index + 1] == "\n":
                    index += 1
            else:
                line += 1
        else:
            state = "unquoted"
        index += 1

    if state == "quoted":
        raise ValueError(
            f"{filename}: malformed CSV quote at physical line {quote_start}: "
            "unclosed quoted field"
        )


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        try:
            text = handle.read()
        except UnicodeError as exc:
            raise ValueError(f"{path.name}: invalid UTF-8 input at byte offset {exc.start}") from None
        validate_quote_placement(text, path.name)
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        try:
            headers = next(reader)
        except StopIteration:
            raise ValueError(f"{path.name}: missing CSV header") from None
        except csv.Error as exc:
            raise ValueError(f"{path.name}: malformed CSV header: {exc}") from None

        duplicates = sorted({name for name in headers if headers.count(name) > 1})
        if duplicates:
            raise ValueError(f"{path.name}: duplicate header column(s): {', '.join(duplicates)}")
        missing = [name for name in REQUIRED if name not in headers]
        if missing:
            raise ValueError(f"{path.name}: missing required column(s): {', '.join(missing)}")
        unexpected = [name for name in headers if name not in REQUIRED]
        if unexpected:
            raise ValueError(f"{path.name}: unexpected column(s): {', '.join(unexpected)}")

        rows: list[dict[str, str]] = []
        data_record = 0
        while True:
            physical_start = reader.line_num + 1
            try:
                values = next(reader)
            except StopIteration:
                break
            except csv.Error as exc:
                raise ValueError(
                    f"{path.name}: malformed CSV near physical line {reader.line_num}: {exc}"
                ) from None
            data_record += 1
            csv_record = data_record + 1  # Record 1 is the header.
            where = f"{path.name}: source record {csv_record} (physical line {physical_start})"
            if not values:
                raise ValueError(f"{where}: blank physical row; expected {len(REQUIRED)} fields")
            if len(values) != len(headers):
                if len(values) > len(headers):
                    detail = "extra fields"
                else:
                    missing_names = ", ".join(headers[len(values):])
                    detail = f"missing fields ({missing_names})"
                raise ValueError(
                    f"{where}: {detail}; expected {len(headers)} fields, got {len(values)}"
                )
            rows.append(dict(zip(headers, values)))
        return rows


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


OUTPUT_NAMES = ("shift_audit.csv", "overlap_pairs.csv", "review_queue.csv", "summary.json")


def _path_identity(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve(strict=False)))
    except (OSError, RuntimeError):
        raise ValueError(f"cannot safely resolve path for collision check: {path.name}") from None


def assert_no_input_output_collisions(inputs: tuple[Path, ...], output_dir: Path) -> None:
    destinations = [(output_dir, "output directory")]
    destinations.extend((output_dir / name, name) for name in OUTPUT_NAMES)
    for source in inputs:
        source_identity = _path_identity(source)
        for destination, destination_name in destinations:
            if source_identity == _path_identity(destination):
                raise ValueError(
                    f"input/output path collision: input '{source.name}' aliases "
                    f"'{destination_name}'; choose a separate output directory"
                )
            try:
                same_file = os.path.samefile(source, destination)
            except (FileNotFoundError, NotADirectoryError):
                same_file = False
            except OSError:
                raise ValueError(
                    f"cannot safely compare input '{source.name}' with output '{destination_name}'"
                ) from None
            if same_file:
                raise ValueError(
                    f"input/output path collision: input '{source.name}' aliases "
                    f"'{destination_name}'; choose a separate output directory"
                )


def _clear_previous_outputs(inputs: tuple[Path, ...], output_dir: Path) -> str | None:
    failures: list[str] = []
    for name in OUTPUT_NAMES:
        try:
            assert_no_input_output_collisions(inputs, output_dir)
        except ValueError as exc:
            return f"report cleanup stopped to protect an input: {exc}"
        path = output_dir / name
        try:
            if path.is_dir() and not path.is_symlink():
                failures.append(f"{name} is a directory and was preserved")
                continue
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            failures.append(f"{name} could not be removed ({exc.strerror or 'filesystem error'})")
    if failures:
        return "cleanup incomplete: " + "; ".join(failures) + ". Inspect existing outputs before use."
    return None


def _build(input_path: Path, output_dir: Path) -> dict[str, Any]:
    rows = read_rows(input_path)
    id_groups: dict[str, list[int]] = defaultdict(list)
    for idx, row in enumerate(rows):
        key = row.get("shift_id", "").strip().upper()
        if key:
            id_groups[key].append(idx)

    review: list[dict[str, Any]] = []
    valid: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        source_row = idx + 2  # Preserve the legacy header-inclusive logical CSV record index.
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
                        try:
                            end += timedelta(days=1)
                        except OverflowError:
                            issue = "overnight end date exceeds supported date range"
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
    assert_no_input_output_collisions((input_path,), output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    assert_no_input_output_collisions((input_path,), output_dir)
    write_csv(output_dir / "shift_audit.csv", AUDIT_FIELDS, audit)
    assert_no_input_output_collisions((input_path,), output_dir)
    write_csv(output_dir / "overlap_pairs.csv", PAIR_FIELDS, pair_rows)
    assert_no_input_output_collisions((input_path,), output_dir)
    write_csv(output_dir / "review_queue.csv", REVIEW_FIELDS, review)
    assert_no_input_output_collisions((input_path,), output_dir)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def build(input_path: Path, output_dir: Path) -> dict[str, Any]:
    input_paths = (input_path,)
    # Check before reading, and again before every output mutation.
    assert_no_input_output_collisions(input_paths, output_dir)
    try:
        return _build(input_path, output_dir)
    except (ValueError, OSError, csv.Error, UnicodeError) as original_error:
        # Old reports must not appear to describe a run that just failed.
        cleanup_status = _clear_previous_outputs(input_paths, output_dir)
        if cleanup_status:
            raise ValueError(f"{original_error}; {cleanup_status}") from None
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "examples" / "timesheets.csv")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    args = parser.parse_args()
    try:
        summary = build(args.input, args.output_dir)
    except (ValueError, OSError, csv.Error, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
