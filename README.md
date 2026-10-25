# TaskProof Lab — runnable episode examples

Small, reproducible Python examples accompanying practical office-automation videos from TaskProof Lab. Every input is synthetic tutorial data. The scripts use only the Python standard library and keep source CSVs unchanged; Episode 003 creates verified copies in a separate output folder when explicitly applied.

## Episodes

| Episode | Public video | What the example produces |
|---|---|---|
| [001 — Clean a messy sales CSV](episode-001/README.md) | [Watch Episode 001](https://youtu.be/uOKYgptAFs0) | Normalize sales rows, separate exact duplicates, quarantine conflicts and invalid rows, and calculate regional totals. Outputs include cleaned orders, review queue, duplicate log, cancellations, regional totals, and a row-reconciliation summary. |
| [002 — Reconcile two invoice CSV exports](episode-002/README.md) | [Watch Episode 002](https://youtu.be/m8r2VVzPmH8) | Compare two synthetic snapshots and separate unchanged, changed, previous-only, current-only, and review rows, with two-sided row accounting. |
| [003 — Plan file renames safely](episode-003/README.md) | [Watch Episode 003](https://youtu.be/i5x9uyzZqfI) | Validate a synthetic CSV rename plan, block collisions and unsafe/missing inputs, then optionally copy only validated rows while preserving originals. |

## Requirements

- Python 3.9 or newer
- No third-party packages

Run each example from its episode directory. Each script writes fresh results under `output/`; `expected_outputs/` contains the verified sample results.

## Synthetic data and interpretation

Names, identifiers, file contents, quantities, and dollar values are invented placeholders. They are not real customer or business records, and sample amounts are not financial claims. The summaries verify how these particular rows were classified; they do not establish that a real source export is complete or correct. Review the rules and outputs before adapting a script to a copy of a real file.
