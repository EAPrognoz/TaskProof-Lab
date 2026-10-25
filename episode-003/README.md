# Episode 003 — Plan file renames safely

**Public video:** [https://youtu.be/i5x9uyzZqfI](https://youtu.be/i5x9uyzZqfI)

Companion example for **“Plan File Renames Safely with Python | Dry-Run Manifest & Collision Checks.”** It turns a two-column CSV mapping into a reviewable dry-run manifest, blocks duplicate destinations and unsafe or missing inputs, and can copy validated files to a separate folder while preserving the originals.

All six input files and all names are synthetic placeholders. The seven-row plan reports `7 = 3 ready + 2 planned target collisions + 1 unsafe path + 1 missing source`. These counts describe this sample only. They are not business results or a performance claim.

## Run a dry run

From this directory:

```sh
python3 plan_renames.py
```

The default command writes `output/dry_run_manifest.csv` and `output/dry_run_summary.json`. It does not create the destination folder or copy or change source files.

## Review, then optionally copy

Inspect the manifest yourself before deciding whether to use `--apply`. The script does not store per-row human approvals: `--apply` copies every row that passes its validation checks (`READY_DRY_RUN`) into the destination folder. It does not rename or remove originals, and exclusive file creation prevents overwriting a destination.

```sh
python3 plan_renames.py --apply \
  --report output/applied_manifest.csv \
  --summary output/apply_summary.json
```

This sample copies three files to `output/renamed_copy/`; source hashes are checked before copying and copied bytes are verified afterward. The checked-in `expected_outputs/` contains the verified dry-run and apply results, including the three copies.

## Adapt the example

Prepare a CSV with `source_name,new_name` columns and use disposable copies of your files first:

```sh
python3 plan_renames.py \
  --source-dir /path/to/test_inbox \
  --plan /path/to/rename_plan.csv \
  --target-dir /path/to/new_output_folder \
  --report /path/to/review_manifest.csv \
  --summary /path/to/summary.json
```

Add `--apply` only after reviewing the report. Keep the destination outside the source folder and retain a backup.

The standard-library script requires simple file names, preserves file extensions, rejects symbolic links and missing sources, blocks repeated source rows and case-insensitive duplicate or existing targets, protects report paths, verifies copied bytes with SHA-256, and leaves source files untouched. It does not inspect document contents or decide whether a business record is correct.

## Verify

```sh
python3 -m unittest discover -s tests -v
```

Seven tests cover row accounting, dry-run safety, copying and hash verification, source preservation, path safety, collisions, existing targets, report-path protection, and invalid headers. No third-party packages are required.
