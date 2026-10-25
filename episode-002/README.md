# Episode 002 — Reconcile two invoice CSV exports

**Public video:** [https://youtu.be/m8r2VVzPmH8](https://youtu.be/m8r2VVzPmH8)

Companion example for **“Reconcile Two Invoice CSV Exports: Find Missing and Changed Rows.”** It compares two synthetic snapshots and separates unchanged matches, changed matches, previous-only rows, current-only rows, and records that cannot be matched safely.

The sample contains 10 previous rows and 11 current rows. It classifies 5 unchanged, 2 changed, 2 previous-only, and 1 current-only; 1 previous row and 3 current rows are held for review. Both source-row checks pass: `10 = 5 + 2 + 2 + 1` and `11 = 5 + 2 + 1 + 3`. All rows and amounts are synthetic.

## Run

From this directory:

```sh
python3 reconcile_exports.py
python3 -m unittest discover -s tests -v
```

The script reads `examples/previous_export.csv` and `examples/current_export.csv` and writes results under `output/`. The default key rule trims and uppercases record IDs. Other fields are trimmed and compared as text, so values such as `8.0` and `8.00` count as different. Duplicate and blank keys go to review. See `expected_outputs/` for verified sample results.

To compare copies of your own exports, pass `--previous`, `--current`, and `--output-dir`; test the comparison policy against representative rows before relying on it. A balanced row count alone does not prove that a source export is complete or accurate.
