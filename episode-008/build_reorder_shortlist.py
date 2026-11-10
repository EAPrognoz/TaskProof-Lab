#!/usr/bin/env python3
"""Build a reviewable low-stock shortlist from two synthetic CSV exports."""
from __future__ import annotations

import argparse
import csv
import json
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


def read_csv(path: Path, required: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not set(required).issubset(reader.fieldnames):
            expected = ", ".join(required)
            raise ValueError(f"{path.name} must contain columns: {expected}")
        return [dict(row) for row in reader]


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


def build(inventory_path: Path, rules_path: Path, output_dir: Path) -> dict[str, Any]:
    inventory = read_csv(inventory_path, INVENTORY_FIELDS)
    rules = read_csv(rules_path, RULE_FIELDS)

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

    write_csv(output_dir / "reorder_shortlist.csv", SHORTLIST_FIELDS, shortlist)
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
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=ROOT / "examples" / "inventory.csv")
    parser.add_argument("--rules", type=Path, default=ROOT / "examples" / "reorder_rules.csv")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    args = parser.parse_args()
    summary = build(args.inventory, args.rules, args.output_dir)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
