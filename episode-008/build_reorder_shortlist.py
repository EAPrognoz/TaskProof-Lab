#!/usr/bin/env python3
"""Build a reviewable low-stock shortlist from two synthetic CSV exports."""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
INVENTORY_FIELDS = ("sku", "item_name", "on_hand")
RULE_FIELDS = ("sku", "reorder_point", "target_stock", "pack_size")
SHORTLIST_FIELDS = (
    "sku", "item_name", "on_hand", "reorder_point", "target_stock",
    "pack_size", "recommended_order_qty", "projected_after_order",
)
REVIEW_FIELDS = ("source_row", "sku", "item_name", "on_hand", "issue")


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


def read_csv(path: Path, required: tuple[str, ...]) -> list[dict[str, str]]:
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
        missing = [name for name in required if name not in headers]
        if missing:
            raise ValueError(f"{path.name}: missing required column(s): {', '.join(missing)}")
        unexpected = [name for name in headers if name not in required]
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
                raise ValueError(f"{where}: blank physical row; expected {len(required)} fields")
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


def sku_key(value: str) -> str:
    return value.strip().upper()


def nonnegative_int(value: str) -> int:
    parsed = int(value.strip())
    if parsed < 0:
        raise ValueError("must not be negative")
    return parsed


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


OUTPUT_NAMES = ("reorder_shortlist.csv", "review_queue.csv", "summary.json")


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


def _build(inventory_path: Path, rules_path: Path, output_dir: Path) -> dict[str, Any]:
    inventory = read_csv(inventory_path, INVENTORY_FIELDS)
    rules = read_csv(rules_path, RULE_FIELDS)

    inventory_keys = {sku_key(row["sku"]) for row in inventory if sku_key(row["sku"])}
    for source_row, row in enumerate(rules, start=2):
        key = sku_key(row["sku"])
        where = f"{rules_path.name}: source record {source_row}"
        if not key:
            raise ValueError(f"{where}: missing sku")
        if key not in inventory_keys:
            raise ValueError(f"{where}: rule SKU {key} has no matching inventory row")
        try:
            parsed_rule = {
                field: nonnegative_int(row[field])
                for field in ("reorder_point", "target_stock", "pack_size")
            }
            if parsed_rule["pack_size"] == 0:
                raise ValueError("pack_size must be greater than zero")
            if parsed_rule["target_stock"] <= parsed_rule["reorder_point"]:
                raise ValueError("target_stock must exceed reorder_point")
        except ValueError as exc:
            raise ValueError(f"{where}: invalid reorder rule values: {exc}") from None

    inventory_groups: dict[str, list[tuple[int, dict[str, str]]]] = defaultdict(list)
    for source_row, row in enumerate(inventory, start=2):
        key = sku_key(row.get("sku", ""))
        if key:
            inventory_groups[key].append((source_row, row))

    rule_groups: dict[str, list[tuple[int, dict[str, str]]]] = defaultdict(list)
    for source_row, row in enumerate(rules, start=2):
        key = sku_key(row.get("sku", ""))
        if key:
            rule_groups[key].append((source_row, row))

    shortlist: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    above_threshold: list[dict[str, Any]] = []
    issue_counts: Counter[str] = Counter()

    for source_row, row in enumerate(inventory, start=2):
        raw_sku = row.get("sku", "").strip()
        key = sku_key(raw_sku)
        item_name = row.get("item_name", "").strip()
        raw_on_hand = row.get("on_hand", "").strip()

        issue = ""
        on_hand: int | None = None
        if not key:
            issue = "missing SKU"
        elif not item_name:
            issue = "missing item name"
        else:
            try:
                on_hand = nonnegative_int(raw_on_hand)
            except (ValueError, AttributeError):
                issue = "on_hand must be a nonnegative whole number"

        if not issue and len(inventory_groups[key]) > 1:
            issue = f"duplicate inventory SKU ({len(inventory_groups[key])} rows); review all matches"

        matched_rules = rule_groups.get(key, [])
        if not issue and not matched_rules:
            issue = "no matching reorder rule"
        elif not issue and len(matched_rules) > 1:
            issue = f"duplicate reorder rules ({len(matched_rules)} rows); review rule sheet"

        rule: dict[str, str] | None = matched_rules[0][1] if len(matched_rules) == 1 else None
        parsed: dict[str, int] = {}
        if not issue and rule is not None:
            try:
                parsed = {field: nonnegative_int(rule[field]) for field in ("reorder_point", "target_stock", "pack_size")}
                if parsed["pack_size"] == 0:
                    raise ValueError("pack_size must be greater than zero")
                if parsed["target_stock"] <= parsed["reorder_point"]:
                    raise ValueError("target_stock must exceed reorder_point")
            except (ValueError, AttributeError):
                issue = "invalid reorder rule values"

        if issue:
            review.append({
                "source_row": source_row,
                "sku": raw_sku,
                "item_name": item_name,
                "on_hand": raw_on_hand,
                "issue": issue,
            })
            issue_counts[issue.split(" (")[0]] += 1
            continue

        assert on_hand is not None and rule is not None
        point = parsed["reorder_point"]
        target = parsed["target_stock"]
        pack_size = parsed["pack_size"]
        if on_hand <= point:
            shortfall = target - on_hand
            order_qty = ((shortfall + pack_size - 1) // pack_size) * pack_size
            shortlist.append({
                "sku": key,
                "item_name": item_name,
                "on_hand": on_hand,
                "reorder_point": point,
                "target_stock": target,
                "pack_size": pack_size,
                "recommended_order_qty": order_qty,
                "projected_after_order": on_hand + order_qty,
            })
        else:
            above_threshold.append({
                "sku": key,
                "item_name": item_name,
                "on_hand": on_hand,
                "reorder_point": point,
            })

    input_paths = (inventory_path, rules_path)
    assert_no_input_output_collisions(input_paths, output_dir)
    write_csv(output_dir / "reorder_shortlist.csv", SHORTLIST_FIELDS, shortlist)
    assert_no_input_output_collisions(input_paths, output_dir)
    write_csv(output_dir / "review_queue.csv", REVIEW_FIELDS, review)
    summary = {
        "synthetic_example": True,
        "inventory_rows": len(inventory),
        "rule_rows": len(rules),
        "reorder_suggestions": len(shortlist),
        "above_threshold": len(above_threshold),
        "review_rows": len(review),
        "accounted_inventory_rows": len(shortlist) + len(above_threshold) + len(review),
        "row_reconciliation": f"{len(inventory)} = {len(shortlist)} reorder + {len(above_threshold)} above threshold + {len(review)} review",
        "review_issue_counts": dict(sorted(issue_counts.items())),
        "total_recommended_units": sum(int(row["recommended_order_qty"]) for row in shortlist),
        "side_effects": "No purchase orders are created and no inventory system is accessed.",
    }
    assert_no_input_output_collisions(input_paths, output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    assert_no_input_output_collisions(input_paths, output_dir)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def build(inventory_path: Path, rules_path: Path, output_dir: Path) -> dict[str, Any]:
    input_paths = (inventory_path, rules_path)
    # Check before reading, and again before every output mutation.
    assert_no_input_output_collisions(input_paths, output_dir)
    try:
        return _build(inventory_path, rules_path, output_dir)
    except (ValueError, OSError, csv.Error, UnicodeError) as original_error:
        # Old reports must not appear to describe a run that just failed.
        cleanup_status = _clear_previous_outputs(input_paths, output_dir)
        if cleanup_status:
            raise ValueError(f"{original_error}; {cleanup_status}") from None
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=ROOT / "examples" / "inventory.csv")
    parser.add_argument("--rules", type=Path, default=ROOT / "examples" / "reorder_rules.csv")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    args = parser.parse_args()
    try:
        summary = build(args.inventory, args.rules, args.output_dir)
    except (ValueError, OSError, csv.Error, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
