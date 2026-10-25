#!/usr/bin/env python3
"""Compare two CSV snapshots and route every source row to a reviewable result."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
FIELDS = ["record_id", "invoice_date", "customer_code", "sku", "quantity", "unit_price", "status"]
COMPARE_FIELDS = FIELDS[1:]


def load_snapshot(path: Path, side: str) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{path}: CSV has no header row")
        header_by_name = {name.strip().casefold(): name for name in reader.fieldnames if name}
        missing = [field for field in FIELDS if field not in header_by_name]
        if missing:
            raise ValueError(f"{path}: missing required columns: {', '.join(missing)}")

        rows = []
        for source_row, raw in enumerate(reader, start=2):
            original = {field: raw.get(header_by_name[field], "") or "" for field in FIELDS}
            values = {field: original[field].strip() for field in FIELDS}
            key = values["record_id"].upper()
            rows.append({"side": side, "source_row": source_row, "raw": original,
                         "values": values, "key": key})
    return rows


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _result_row(row: dict[str, Any], row_field: str) -> dict[str, Any]:
    return {"record_id": row["key"], row_field: row["source_row"], **row["values"]}


def reconcile(previous_path: Path, current_path: Path, output_dir: Path) -> dict[str, Any]:
    previous = load_snapshot(previous_path, "previous")
    current = load_snapshot(current_path, "current")

    previous_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    current_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    previous_missing = [row for row in previous if not row["key"]]
    current_missing = [row for row in current if not row["key"]]
    for row in previous:
        if row["key"]:
            previous_groups[row["key"]].append(row)
    for row in current:
        if row["key"]:
            current_groups[row["key"]].append(row)

    previous_duplicates = {key for key, group in previous_groups.items() if len(group) > 1}
    current_duplicates = {key for key, group in current_groups.items() if len(group) > 1}
    ambiguous_keys = previous_duplicates | current_duplicates
    review: list[dict[str, Any]] = []

    def send_to_review(row: dict[str, Any], reason: str) -> None:
        review.append({
            "side": row["side"],
            "source_row": row["source_row"],
            "raw_record_id": row["raw"]["record_id"],
            "normalized_record_id": row["key"],
            "reason": reason,
            **{f"raw_{field}": value for field, value in row["raw"].items()},
        })

    for row in previous_missing + current_missing:
        send_to_review(row, "record_id is blank; the row cannot be matched safely")
    for key in sorted(ambiguous_keys):
        sources = []
        if key in previous_duplicates:
            sources.append("previous export")
        if key in current_duplicates:
            sources.append("current export")
        cause = " and ".join(sources)
        for row in previous_groups.get(key, []):
            reason = f"ambiguous record_id; duplicate key in {cause}"
            send_to_review(row, reason)
        for row in current_groups.get(key, []):
            reason = f"duplicate record_id in {row['side']} export"
            send_to_review(row, reason)

    valid_previous = {key: group[0] for key, group in previous_groups.items()
                      if key not in ambiguous_keys}
    valid_current = {key: group[0] for key, group in current_groups.items()
                     if key not in ambiguous_keys}

    unchanged: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []
    for key in sorted(valid_previous.keys() & valid_current.keys()):
        before, after = valid_previous[key], valid_current[key]
        differences = [field for field in COMPARE_FIELDS
                       if before["values"][field] != after["values"][field]]
        if differences:
            item = {"record_id": key,
                    "previous_source_row": before["source_row"],
                    "current_source_row": after["source_row"],
                    "changed_fields": ", ".join(differences)}
            for field in COMPARE_FIELDS:
                item[f"previous_{field}"] = before["values"][field]
                item[f"current_{field}"] = after["values"][field]
            changed.append(item)
        else:
            unchanged.append({"record_id": key,
                              "previous_source_row": before["source_row"],
                              "current_source_row": after["source_row"],
                              **after["values"]})
    for key in sorted(valid_previous.keys() - valid_current.keys()):
        removed.append(_result_row(valid_previous[key], "previous_source_row"))
    for key in sorted(valid_current.keys() - valid_previous.keys()):
        added.append(_result_row(valid_current[key], "current_source_row"))

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "unchanged.csv",
               ["record_id", "previous_source_row", "current_source_row", *FIELDS[1:]], unchanged)
    changed_headers = ["record_id", "previous_source_row", "current_source_row", "changed_fields"]
    changed_headers += [f"{side}_{field}" for field in COMPARE_FIELDS for side in ("previous", "current")]
    _write_csv(output_dir / "changed.csv", changed_headers, changed)
    _write_csv(output_dir / "removed.csv", ["record_id", "previous_source_row", *FIELDS[1:]], removed)
    _write_csv(output_dir / "added.csv", ["record_id", "current_source_row", *FIELDS[1:]], added)
    review_headers = ["side", "source_row", "raw_record_id", "normalized_record_id", "reason"]
    review_headers += [f"raw_{field}" for field in FIELDS]
    _write_csv(output_dir / "review.csv", review_headers, review)

    review_counts = Counter(item["side"] for item in review)
    prev_accounted = len(unchanged) + len(changed) + len(removed) + review_counts["previous"]
    current_accounted = len(unchanged) + len(changed) + len(added) + review_counts["current"]
    summary = {
        "synthetic_demo": True,
        "comparison_policy": "Record IDs are trimmed and uppercased. Other fields are trimmed and compared as text.",
        "previous_source_rows": len(previous),
        "current_source_rows": len(current),
        "unchanged_matches": len(unchanged),
        "changed_matches": len(changed),
        "removed_from_current": len(removed),
        "added_in_current": len(added),
        "review_previous_rows": review_counts["previous"],
        "review_current_rows": review_counts["current"],
        "ambiguous_keys": sorted(ambiguous_keys),
        "previous_accounted_rows": prev_accounted,
        "current_accounted_rows": current_accounted,
        "previous_row_check_passed": prev_accounted == len(previous),
        "current_row_check_passed": current_accounted == len(current),
    }
    if not summary["previous_row_check_passed"] or not summary["current_row_check_passed"]:
        raise RuntimeError("Row accounting failed; outputs are not a complete reconciliation.")
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path, default=ROOT / "examples" / "previous_export.csv")
    parser.add_argument("--current", type=Path, default=ROOT / "examples" / "current_export.csv")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    args = parser.parse_args()
    summary = reconcile(args.previous.resolve(), args.current.resolve(), args.output_dir.resolve())
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
