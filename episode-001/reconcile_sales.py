#!/usr/bin/env python3
"""Clean and reconcile a small sales CSV using only the Python standard library.

All example inputs in this package are synthetic. Run from this directory:
    python reconcile_sales.py
Or pass your own copied CSV and an output directory:
    python reconcile_sales.py --input my_sales.csv --output-dir my_report
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable

REQUIRED = ("order_id", "order_date", "region", "product", "qty", "unit_price", "status")
MONEY = Decimal("0.01")
BOOKED = {"complete", "completed", "paid", "booked"}
CANCELLED = {"cancelled", "canceled", "void", "voided"}


def header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def clean_space(value: str) -> str:
    return " ".join(value.split())


def parse_date(value: str) -> str:
    value = value.strip()
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            pass
    raise ValueError("date is blank or not YYYY-MM-DD, YYYY/MM/DD, or MM/DD/YYYY")


def parse_quantity(value: str) -> int:
    value = value.strip().replace(",", "")
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError("quantity must be a whole number")
    quantity = int(value)
    if quantity <= 0:
        raise ValueError("quantity must be greater than zero")
    return quantity


def parse_price(value: str) -> Decimal:
    normalized = value.strip().replace("$", "").replace(",", "")
    try:
        price = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError("unit price must be a number") from exc
    if not price.is_finite() or price < 0:
        raise ValueError("unit price must be a non-negative number")
    return price.quantize(MONEY, rounding=ROUND_HALF_UP)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("input CSV has no header row")
        mapped = {header_key(name): name for name in reader.fieldnames if name}
        missing = [name for name in REQUIRED if name not in mapped]
        if missing:
            raise ValueError("missing required columns: " + ", ".join(missing))
        output = []
        for source_row, values in enumerate(reader, start=2):
            record = {key: clean_space(values.get(column) or "")
                      for key, column in mapped.items()}
            record["_source_row"] = str(source_row)
            output.append(record)
    return output


def normalized_order(raw: dict[str, str]) -> tuple[dict[str, str] | None, str | None, str]:
    order_id = clean_space(raw["order_id"]).upper()
    status_raw = clean_space(raw["status"]).casefold()
    if status_raw in CANCELLED:
        return None, "cancelled", order_id
    if not order_id:
        return None, "missing order ID", ""
    if status_raw not in BOOKED:
        return None, "status must be completed, paid, or cancelled", order_id

    errors: list[str] = []
    try:
        order_date = parse_date(raw["order_date"])
    except ValueError as exc:
        order_date = ""
        errors.append(str(exc))
    region = clean_space(raw["region"]).title()
    if not region:
        errors.append("region is blank")
    product = clean_space(raw["product"])
    if not product:
        errors.append("product is blank")
    try:
        quantity = parse_quantity(raw["qty"])
    except ValueError as exc:
        quantity = 0
        errors.append(str(exc))
    try:
        price = parse_price(raw["unit_price"])
    except ValueError as exc:
        price = Decimal("0.00")
        errors.append(str(exc))

    if errors:
        return None, "; ".join(errors), order_id
    record = {
        "order_id": order_id,
        "order_date": order_date,
        "region": region,
        "product": product,
        "qty": str(quantity),
        "unit_price": f"{price:.2f}",
        "order_total": f"{(price * quantity):.2f}",
        "status": "booked",
    }
    return record, None, order_id


def write_csv(path: Path, columns: Iterable[str], rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def process_file(input_path: Path, output_dir: Path) -> dict[str, Any]:
    input_path = input_path.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    raw_rows = read_rows(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reviewed: list[dict[str, str]] = []
    cancelled_rows: list[dict[str, str]] = []
    for raw in raw_rows:
        normalized, issue, order_id = normalized_order(raw)
        if issue == "cancelled":
            cancelled_rows.append({
                "source_row": raw["_source_row"], "order_id": order_id,
                "reason": "cancelled orders are excluded from booked totals",
            })
        elif not order_id:
            reviewed.append({
                "source_row": raw["_source_row"], "order_id": "",
                "reason": issue or "missing order ID",
                "raw_row": json.dumps({k: v for k, v in raw.items() if not k.startswith("_")},
                                      ensure_ascii=False, sort_keys=True),
            })
        else:
            groups[order_id].append({
                "source_row": raw["_source_row"], "normalized": normalized,
                "issue": issue,
                "raw_row": json.dumps({k: v for k, v in raw.items() if not k.startswith("_")},
                                      ensure_ascii=False, sort_keys=True),
            })

    approved: list[dict[str, str]] = []
    exact_duplicate_rows: list[dict[str, str]] = []
    for order_id, members in sorted(groups.items()):
        member_issues = [m["issue"] for m in members if m["issue"]]
        if member_issues:
            reason = "order ID group needs review; " + " | ".join(dict.fromkeys(member_issues))
            for member in members:
                reviewed.append({
                    "source_row": member["source_row"], "order_id": order_id,
                    "reason": reason, "raw_row": member["raw_row"],
                })
            continue
        fingerprints = [
            tuple(sorted((k, v) for k, v in member["normalized"].items() if k != "order_total"))
            for member in members
        ]
        if len(members) > 1 and len(set(fingerprints)) > 1:
            for member in members:
                reviewed.append({
                    "source_row": member["source_row"], "order_id": order_id,
                    "reason": "conflicting rows share this order ID; check the source export",
                    "raw_row": member["raw_row"],
                })
            continue

        approved.append(members[0]["normalized"])
        for duplicate in members[1:]:
            exact_duplicate_rows.append({
                "source_row": duplicate["source_row"],
                "order_id": order_id,
                "kept_source_row": members[0]["source_row"],
                "reason": "identical after whitespace and case normalization",
            })

    approved.sort(key=lambda row: (row["order_date"], row["order_id"]))
    reviewed.sort(key=lambda row: int(row["source_row"]))
    cancelled_rows.sort(key=lambda row: int(row["source_row"]))
    exact_duplicate_rows.sort(key=lambda row: int(row["source_row"]))

    clean_columns = ("order_id", "order_date", "region", "product", "qty",
                     "unit_price", "order_total", "status")
    write_csv(output_dir / "clean_orders.csv", clean_columns, approved)
    write_csv(output_dir / "review_queue.csv",
              ("source_row", "order_id", "reason", "raw_row"), reviewed)
    write_csv(output_dir / "cancelled_orders.csv",
              ("source_row", "order_id", "reason"), cancelled_rows)
    write_csv(output_dir / "duplicate_log.csv",
              ("source_row", "order_id", "kept_source_row", "reason"),
              exact_duplicate_rows)

    totals: dict[str, dict[str, Any]] = {}
    for order in approved:
        agg = totals.setdefault(order["region"], {
            "region": order["region"], "order_count": 0, "units": 0,
            "net_sales": Decimal("0.00"),
        })
        agg["order_count"] += 1
        agg["units"] += int(order["qty"])
        agg["net_sales"] += Decimal(order["order_total"])
    totals_rows = [
        {**entry, "net_sales": f'{entry["net_sales"]:.2f}'}
        for _, entry in sorted(totals.items())
    ]
    write_csv(output_dir / "totals_by_region.csv",
              ("region", "order_count", "units", "net_sales"), totals_rows)

    source_count = len(raw_rows)
    clean_count = len(approved)
    review_count = len(reviewed)
    cancelled_count = len(cancelled_rows)
    duplicate_count = len(exact_duplicate_rows)
    reconciled_count = clean_count + review_count + cancelled_count + duplicate_count
    if reconciled_count != source_count:
        raise AssertionError(f"row reconciliation failed: {reconciled_count} != {source_count}")

    summary = {
        "input_file": input_path.name,
        "synthetic_example": "true" if input_path.name == "sales_export_synthetic.csv" else "false",
        "currency_label": "USD (synthetic sample values)",
        "input_data_rows": source_count,
        "approved_unique_orders": clean_count,
        "review_rows": review_count,
        "cancelled_rows": cancelled_count,
        "exact_duplicate_rows_removed": duplicate_count,
        "reconciled_rows": reconciled_count,
        "approved_sample_total": f'{sum((Decimal(r["order_total"]) for r in approved), Decimal("0.00")):.2f}',
        "row_check": f"{source_count} = {clean_count} approved + {review_count} review + "
                      f"{cancelled_count} cancelled + {duplicate_count} duplicate",
    }
    (output_dir / "reconciliation_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    package_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path,
                        default=package_dir / "examples" / "sales_export_synthetic.csv",
                        help="input CSV file (work on a copy)")
    parser.add_argument("--output-dir", type=Path, default=package_dir / "output",
                        help="directory for the four CSV reports and JSON summary")
    args = parser.parse_args()
    try:
        summary = process_file(args.input, args.output_dir)
    except (OSError, ValueError, csv.Error) as exc:
        parser.exit(2, f"error: {exc}\n")
    print("Synthetic example: " + summary["synthetic_example"])
    print("Rows: " + summary["row_check"])
    print("Approved sample total: USD " + summary["approved_sample_total"])
    print("Wrote reports to: " + str(args.output_dir.expanduser().resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
