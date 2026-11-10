# EP009 — Validate timesheet rows and flag overlaps

This companion contains the synthetic Python example used in [TaskProof Lab EP009](https://www.youtube.com/watch?v=2goCMBc7JXE). All employee IDs and records are invented. The script reads a local timesheet CSV and writes local CSV/JSON reports; it does not change the source, contact employees, change a roster, or calculate pay.

## Important meaning of “missing”

The example checks for missing or invalid fields **inside rows already present** in the timesheet CSV. It does not detect a planned shift absent from the file: there is no schedule baseline or expected-shifts input. The sample has a missing `end_time`, a missing `employee_id`, a malformed start time, and duplicate `shift_id` values. It has no row with a blank `shift_id`, though the code sends one to review if encountered.

## Input and rules

- `examples/timesheets.csv`: `shift_id`, `employee_id`, `shift_date`, `start_time`, `end_time`.
- Dates must use `YYYY-MM-DD`; times must use 24-hour `HH:MM`.
- The script checks required fields and unique shift IDs, then compares intervals only for the same employee ID after trimming and case-folding.
- Intervals are half-open `[start, end)`: a shift ending at 12:00 and one starting at 12:00 do not overlap. If end time is earlier than start time, this example treats the end as the next calendar day. Equal start and end times are held as ambiguous.

## Run the sample on Linux

This package was tested on Linux with Python 3.12.14. It uses only the Python standard library; no packages need to be installed. Windows and macOS workflows were not tested for this release candidate.

1. Open the [TaskProof Lab repository](https://github.com/EAPrognoz/TaskProof-Lab), select **Code → Download ZIP**, and extract it. The archive creates `TaskProof-Lab-main/`; open a terminal in its `episode-009/` folder. These files are in the repository tree, not a separate Release asset.
2. Run `pwd` and `ls`. Confirm the folder contains `audit_timesheets.py`, `examples/`, `tests/`, and `expected_outputs/`.
3. In a bash-compatible terminal, enable stop-on-error and run the original tests:

   ```sh
   set -e
   python3 --version
   python3 -m unittest discover -s tests -v
   ```

   The suite should report 13 passing tests. These original tests cover selected sample and boundary behavior; they do not compare every output file with the expected snapshots.
4. Run the sample into a fresh `output/` folder:

   ```sh
   python3 audit_timesheets.py --input examples/timesheets.csv --output-dir output
   ```

   Because `set -e` is active, the terminal stops on a nonzero exit status. The command prints a summary and writes four files under `output/`.
5. Compare every generated file with its expected snapshot:

   ```sh
   cmp expected_outputs/shift_audit.csv output/shift_audit.csv
   cmp expected_outputs/overlap_pairs.csv output/overlap_pairs.csv
   cmp expected_outputs/review_queue.csv output/review_queue.csv
   cmp expected_outputs/summary.json output/summary.json
   ```

   Each comparison should exit 0 and print nothing. This is a separate byte comparison, not part of the 13 original tests.
6. Open the reports in a text editor or spreadsheet viewer, or inspect them in the terminal:

   ```sh
   cat output/shift_audit.csv
   cat output/overlap_pairs.csv
   cat output/review_queue.csv
   cat output/summary.json
   ```

## Expected synthetic result and limits

The 14-row sample reconciles as `14 = 3 clear + 6 overlap + 5 review`. The overlap-pairs file contains three pairs: S-103/S-111 for 180 minutes across midnight, S-104/S-105 for 30 minutes, and S-106/S-107 for 60 minutes. The review file contains five rows: one missing end time, one invalid start time, one missing employee ID, and both duplicate S-113 rows.

This report does not establish which row is correct or what is scheduled. Before adapting it, confirm the export's field meanings, employee-ID matching rules, date/time format, overnight policy, time zone, daylight-saving handling, break rules and local policy. The code does not account for time zones or daylight-saving changes. It compares multiple recorded intervals, but does not implement workplace break or split-shift policies. It is not a payroll, legal-compliance, or roster-management system.

## Rights and attribution

This is the TaskProof Lab companion for the linked educational episode. No third-party assets or dependencies are included; the code uses Python's standard library and the records are synthetic. No explicit reuse license was found in the source package or repository. This archive includes no license grant and must not be described as open-source. Public availability does not resolve broader reuse or modification rights.
