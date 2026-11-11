# EP008 — Check stock rules and pack sizes before reordering

This companion contains the synthetic Python example used in [TaskProof Lab EP008](https://www.youtube.com/watch?v=6HiphM1DgVU). All sample records and item names are invented. The script reads two local CSV files and writes local reports; it does not access an inventory system or place an order.

## Inputs and rules

- `examples/inventory.csv`: `sku`, `item_name`, `on_hand` (whole units currently recorded).
- `examples/reorder_rules.csv`: `sku`, `reorder_point`, `target_stock`, `pack_size`.
- The script joins trimmed, case-insensitive SKU values. It validates nonnegative whole-number counts and rules; a valid row is suggested when on-hand is less than or equal to the reorder point. The target-stock shortfall is rounded up to a whole pack.
- Repeated inventory SKUs, repeated valid rule SKUs, missing matches, and row-level inventory issues go to review. Both rows for a duplicated inventory SKU are held. Invalid rule data or a rule with no matching inventory SKU stops the run so it cannot produce a misleading reorder result.

For real use, confirm the two exports use matching SKU codes and the same unit. A pack-size round-up can put projected stock above the target. The script does not write a detailed above-threshold CSV; `summary.json` contains only the above-threshold count.

## Run the sample on Linux

This package was tested on Linux with Python 3.12.14. It uses only the Python standard library; no packages need to be installed. Windows and macOS workflows were not tested for this release candidate.

1. Open the [TaskProof Lab repository](https://github.com/EAPrognoz/TaskProof-Lab), select **Code → Download ZIP**, and extract it. The archive creates `TaskProof-Lab-main/`; open a terminal in its `episode-008/` folder. These files are in the repository tree, not a separate Release asset.
2. Run `pwd` and `ls`. Confirm the folder contains `build_reorder_shortlist.py`, `examples/`, `tests/`, and `expected_outputs/`.
3. In a bash-compatible terminal, enable stop-on-error and run the original tests:

   ```sh
   set -e
   python3 --version
   python3 -m unittest discover -s tests -v
   ```

   The suite should report 24 passing tests: 10 original behavior tests and 14 command-line regression tests. The original tests check selected rules and behavior; they do not compare every output file with the expected snapshots.
4. Run the sample into a fresh `output/` folder:

   ```sh
   python3 build_reorder_shortlist.py --inventory examples/inventory.csv --rules examples/reorder_rules.csv --output-dir output
   ```

   Because `set -e` is active, the terminal stops on a nonzero exit status. The command prints a summary and writes three files under `output/`.
5. Compare every generated file with the expected snapshot:

   ```sh
   cmp expected_outputs/reorder_shortlist.csv output/reorder_shortlist.csv
   cmp expected_outputs/review_queue.csv output/review_queue.csv
   cmp expected_outputs/summary.json output/summary.json
   ```

   Each comparison should exit 0 and print nothing. This is a separate byte comparison, not part of the 10 original tests.
6. Open the reports in a text editor or spreadsheet viewer, or inspect them in the terminal:

   ```sh
   cat output/reorder_shortlist.csv
   cat output/review_queue.csv
   cat output/summary.json
   ```

## CSV structure and failed runs

Each input must contain its documented headers exactly once; unknown columns, duplicate headers, blank physical rows, malformed CSV quoting, rows with too few or too many cells, invalid rule values, and rule SKUs absent from the inventory produce a concise error and exit status 2. Input paths are checked against the output directory and every report path before reading, writing, and cleanup; the check covers normalized relative/absolute paths, symlinks, and existing hard links. If an input aliases an output, the run stops without changing it. If a previous run left the three report filenames in the selected output folder, a failed run removes those files so old results cannot look current. If a report path is instead a directory or cannot be removed, cleanup continues for other reports and reports the incomplete cleanup; inspect the output folder before using any remaining files. A full-width blank inventory cell is different from a physically missing cell: it remains a row-level review item. Correct quoted commas and doubled quotes are accepted; stray quotes in unquoted fields and invalid text encoding are rejected. Valid quoted multi-line fields are accepted. CSV diagnostics identify the source file; depending on the error, they also include the logical record, physical line, or invalid byte offset. For compatibility, the output `source_row` field retains the existing header-inclusive logical record index (the first data record is 2); it is not a physical line number.

This is a focused shape check, not a general-purpose CSV repair tool. It accepts the documented UTF-8 CSV format and documented columns; adapt exports deliberately rather than adding columns and assuming they are ignored.

## Expected synthetic result and limits

The sample has 8 inventory rows and 6 rule rows. The summary reconciles `8 = 3 reorder + 2 above threshold + 3 review`; the three suggested quantities sum to 40 units. SKU-101 has 4 on hand, a reorder point of 5, target 18 and pack size 7, so its suggested quantity is 14 and projected stock is 18. SKU-103 is included at equality (10 on hand, threshold 10). SKU-106 has no matching rule; both SKU-107 rows are held because the SKU is duplicated.

These are outputs for invented records, not recommendations for an actual purchase. The script has no product-master baseline, demand forecast, lead-time data, reserved-stock data, open-order data, or order integration. Before adapting the example, confirm the SKU mapping, units, current count, rules, open orders, inbound stock and supplier timing. A person must review the shortlist.

## Rights and attribution

This is the TaskProof Lab companion for the linked educational episode. No third-party assets or dependencies are included; the code uses Python's standard library and the records are synthetic. No explicit reuse license was found in the source package or repository. This archive includes no license grant and must not be described as open-source. Public availability does not resolve broader reuse or modification rights.
