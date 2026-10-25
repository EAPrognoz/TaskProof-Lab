# Episode 001 — Clean a messy sales CSV

**Public video:** [https://youtu.be/uOKYgptAFs0](https://youtu.be/uOKYgptAFs0)

Companion example for **“Clean a Messy Sales CSV with Python | Duplicates, Exceptions & Regional Totals.”** It demonstrates a repeatable cleanup, validation, duplicate-check, review-queue, and regional-aggregation workflow.

All 15 input rows are synthetic. The verified sample has 7 approved orders, 6 rows for review, 1 canceled row, and 1 exact duplicate: `15 = 7 + 6 + 1 + 1`. Approved sample sales total **USD 946.00**, with East 360.00, North 230.00, South 228.00, and West 128.00. These are only invented demonstration values.

## Run

From this directory:

```sh
python3 reconcile_sales.py
python3 -m unittest discover -s tests -v
```

The script writes six reports to `output/`. To run against your own file, first make a copy and use:

```sh
python3 reconcile_sales.py --input /path/to/copied_sales.csv --output-dir ./my_report
```

The input columns are order ID, order date, region, product, quantity, unit price, and status. The code normalizes common spaces/case, validates fields, keeps exact duplicates once, routes conflicting IDs to review, and excludes canceled orders from booked totals. Every input row must be accounted for. See `expected_outputs/` for the checked-in results from the synthetic input.
