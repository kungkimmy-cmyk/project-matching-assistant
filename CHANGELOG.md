# CHANGELOG - Packaging round (TEST BUILD)

Preparing the first local-validation Windows .exe. Per the 11-point
checklist:

## Changes made this round

1-2. **Consistency + tests**: 79/79 tests passing before and after
     this round's changes (was 76 at the start; added 3 new tests for
     multi-folder support).

3. **Sales report file picker**: `gui.py` gained "Select Sales Report
   (SOLD history)..." -- a standard local file picker
   (`QFileDialog.getOpenFileName`), saved into `matcher_config.json`
   so it's remembered across runs. Never uploads anything; the file
   stays wherever it already is on your computer.

4. **Multiple folders per side**: `orchestrator.py`'s `run_matching()`
   now takes lists of factory/customer folders (backward compatible --
   a bare path still works). `gui.py`'s single "Select Factory Folder"
   /"Select Customer Folder" buttons became Add/Remove/Clear lists,
   matching the pattern already validated in the RFQ Extractor. CLI
   updated to `--factory-folders A B --customer-folders C D`.

5. Already true, reconfirmed: both factory and customer folders are
   processed together in one run.

6. **No source file modification**: audited every write operation in
   the codebase -- writes only happen to the photo store directory and
   `matcher_config.json`/log files, never back to a read source file.

7. **Factory cost/margin protection**: every sheet in
   `Matching_Results.xlsx` carries a "CONFIDENTIAL -- INTERNAL USE
   ONLY" banner (verified present on all 7 sheets in this round's
   test run). This tool does not generate any customer-facing output
   at all -- there is nothing to accidentally leak into a customer
   document.

8. **DB6 = Mirage = reactive**: confirmed already correct (a pure
   DB-code-prefix check, never conditioned on BLACK/WHITE description
   text) -- added an explicit regression test locking this in. Found
   and fixed one unrelated real bug while testing this: unmatched
   customer-only products were never checked for reactive glaze at
   all, even with an unambiguous DB6 code.

9. **RFQ-level theme -> factory logic**: already built and validated
   against real files in the previous round (`theme_analysis.py`,
   `factory_rules.py`).

10. **SOLD > QUOTED > FACTORY QUOTED > CATALOGUE**: validated against
    your real Sales Invoice Report this round -- 1,627 real sales rows
    read, cross-referenced against project files, and flowing into
    real product-comparison rows with "SOLD" as the historical tier.

11. **Architecture preserved**: no module was deleted or rebuilt --
    `project_matcher.py`, `file_reader.py`'s core, `crash_logger.py`
    untouched; other modules extended, not replaced.

## Packaging

`.github/workflows/build-windows-exe.yml` and `build_exe.bat` build
`Project_Matching_Assistant_TEST_BUILD.exe` -- deliberately named and
labeled (including in the GUI's own title bar) as a TEST build, not
production. Same GitHub Actions approach used for the RFQ Extractor,
since this environment still cannot build or launch a Windows binary
directly.

## What "TEST BUILD" means here

This is meant for YOU to run against your real, complete local folders
and report back what happens -- not to hand to anyone else yet. Please
send back `Matching_Log.txt` and `Matcher_Crash_Log.txt` after your
run regardless of outcome, per the README.

## Round: MG007 finish-code fix, provenance classification, incremental-update foundation

Triggered by real findings from the first local TEST BUILD run against
real 2025+2026 folders and the Mimosa sales history. No architecture
rebuild -- every change below is additive/corrective to the existing
modules, per the explicit instruction to preserve the working
architecture, GUI, multi-folder support, sales-report integration,
evidence hierarchy, DB6/Mirage rules, and factory-cost protection.

### A. Root cause of the MG007 bug (confirmed empirically)

`FACTORY_CODE_RE.search("DB3H11328-MG007")` returns `MG007` as its
ONLY match. `H11328` never matches at all: it's glued directly to
`DB3` with no separator, which fails the regex's negative-lookbehind
protection `(?<![A-Za-z0-9])` (by design, to prevent a different class
of false match elsewhere). `-MG007` IS preceded by a hyphen (a valid
separator), so it passes the lookbehind and gets accepted as `factory_
code` since it doesn't start with "DB". The exclusion list that would
normally catch a finish/colour code (`_is_likely_color_suffix`) only
recognized single-letter+3-digit codes like "N349" at the time --
it had no concept of 2-letter finish prefixes like "MG"/"ND" at all.

### B/C. Fixes implemented

| Module | Change |
|---|---|
| `project_matcher.py` | Generalized `_is_likely_color_suffix()` to also match configurable finish-code prefixes (`cfg.reactive_glaze_code_prefixes`), not just the single-letter pattern. Added `split_finish_suffix()` (separates product identity from finish identity, only stripping the LAST hyphen segment when it's actually finish-code-shaped, correctly preserving a genuine shape suffix like `-X`). Added `derive_factory_code_from_db_code()` (recovers the true Part No. from a finish-stripped DB code when no separate Part No. cell exists in the row). `extract_signals()` now takes an optional `cfg` parameter so file-level matching uses the same configurable exclusion list. |
| `product_comparison.py` | `extract_products()` now takes `cfg` and uses it throughout; a genuinely separate factory-code cell in the row always wins over anything derived (per "same product row evidence is strongest"); added `finish_code` and `factory_code_source` fields to `ExtractedProduct` for transparency. `build_db_code_mapping()` now normalizes DB codes to their base (finish-stripped) form before counting distinct codes per factory code -- finish variants (MG007 vs MG006 of the same product) collapse into one product identity instead of a false "alternative DB codes" conflict, while still being visible in the recommendation text. Also fixed a second real bug found via testing: price extraction previously took the *minimum* of every numeric cell in a row, which meant a small "Item No." column could be mistaken for the price; now prefers header-identified price/cost columns first. |
| `historical_evidence.py` | Added `provenance` field to `HistoricalRecord` (default `""`, backward compatible). |
| **NEW** `workbook_provenance.py` | Content-based classifier distinguishing a genuine factory proposal from an internal PO/negotiation working file (PO/negotiation keywords vs. factory-proposal keywords, both configurable). First-pass heuristic, same honesty standard as `theme_analysis.py`'s colour classification -- validated against the scenarios described, not yet a large real corpus. |
| `orchestrator.py` | Calls `workbook_provenance.assess_provenance()` on the factory-side file before assigning its evidence tier; downgrades FACTORY_QUOTED to QUOTED tier (and labels the record's `provenance`) when the content looks like a PO/negotiation working file, regardless of which folder the file was found in. All `extract_products`/`extract_signals`/`build_db_code_mapping` call sites updated to pass `cfg` through. |
| `config.py` | Added `po_negotiation_keywords`, `factory_proposal_keywords` (both configurable, not hardcoded). |
| **NEW** `local_index.py` | Foundational persistent-index layer for daily incremental use -- see "Daily incremental-update architecture" below. Implemented and tested standalone; **not yet wired into `orchestrator.run_matching()` or the GUI**. |

### D/E/F. Tests added (34 new: 12 + 6 + 16)

- `test_finish_glaze_suffix.py` (12) -- the exact real examples from the bug report (H11328/DB3H11328-MG007/MG006, H5827-X/DB30H5827-X-MG007/MG006), plus regression coverage proving real factory codes are never excluded and genuinely different DB codes are still flagged as real conflicts.
- `test_workbook_provenance.py` (6) -- customer-origin RFQ with factory columns on the far right; PO/negotiation working file with copied RMB cost not misclassified as a factory proposal; an end-to-end orchestrator run confirming the tier downgrade actually fires. **This test suite is what caught the price-extraction bug** (a small "Item No." column being returned as the price) -- found by writing a realistic test, not found by inspection.
- `test_local_index.py` (16) -- all 6 requested guarantees: unchanged-file detection, new-file detection, changed/revised-file detection, evidence retention across a fresh connection, no duplicate evidence when an unchanged file is skipped correctly, and reviewed-mapping retention/update.

### Two real bugs caught by writing tests, not by inspection

Both found while implementing D/E/F, not anticipated in the original bug report:
1. **Copy-paste error while editing `project_matcher.py`**: a `str_replace` accidentally deleted the entire `_timestamp_proximity_score()` function while inserting `derive_factory_code_from_db_code()` nearby. Caught immediately by running the test suite (a `NameError` on the next call), fixed within the same turn before any test was reported as passing.
2. **Same class of accidental deletion in `product_comparison.py`**: `import re as _re` was deleted while inserting the price-column helper function. Same immediate catch via the test suite.

Both are called out explicitly because they're exactly the failure mode automated testing exists to catch -- neither reached a "tests passing" report.

### Daily incremental-update architecture (design + foundational implementation)

`local_index.py` is a SQLite-backed index with three tables:
- `processed_files` -- file path, content hash (SHA-256, not just name/mtime, since a file can be renamed or a folder reorganized without its content changing), last-processed timestamp, rows extracted.
- `evidence_store` -- every `HistoricalRecord` ever extracted, permanently, append-only (same discipline as the RFQ Extractor's `knowledge.db`). This is what lets an incremental run combine newly-extracted evidence with everything already known, without re-reading unchanged files.
- `reviewed_mappings` -- factory code -> human-approved DB code, with a note and timestamp, so a DB Code Mapping conflict a person has already confirmed is never silently re-flagged.

**What integrating this into the live app still requires** (explicitly not done this round, per "do not start new feature work"):
1. A new GUI button, "Update Master" (distinct from "Analyse", which remains the existing full-rebuild path, unchanged and untouched).
2. `orchestrator.py` gains a new entry point (not a replacement for `run_matching()`) that: discovers files as today, calls `local_index.needs_processing()` per file and skips anything unchanged, processes only new/changed files through the *existing* extraction pipeline, calls `local_index.store_evidence()` for each new record instead of (or alongside) the current in-memory `all_quoted_records` list, and merges `local_index.load_all_evidence()` with the newly-extracted records before building the authoritative index -- so file matching and product comparison for *this run* still sees the complete historical picture, not just what changed today.
3. `build_db_code_mapping()` needs one small addition: check `local_index.get_reviewed_mapping(factory_code)` before flagging a conflict, and skip flagging it if a human has already reviewed and approved it (or annotate it as "previously reviewed" rather than omitting it entirely, so nothing is silently hidden).
4. A minimal review UI: today, review happens by reading the Excel output; a "mark as reviewed" action needs some interface (could be as simple as a config-driven allow-list file the user edits, or a proper GUI table -- this choice was not made this round and needs your input).
5. `matcher_config.json` needs an `index_db_path` setting (same pattern as `sales_report_path`), and the GUI needs a way to set/see it (or it can simply default to living next to the `.exe`, matching every other local file already used).

None of this touches the existing "Analyse" full-rebuild path -- it stays exactly as-is, unaffected, as an explicit design requirement (per "without breaking the existing first-run/full-rebuild mode").

### Test results

113/113 passing (was 97 before this round; 34 new, all passing on first or second run except the two accidental-deletion incidents described above, which were caught and fixed within the same turn before being reported).

## Round: Update Master wired into the live GUI

Approved wiring of the daily incremental-update layer. Analyse (full
rebuild) is completely unchanged -- Update Master is an entirely
separate button, worker thread, and code path.

### Modules changed
- **NEW `update_master.py`**: the incremental orchestration --
  `run_update_master()`, `UpdateMasterStats`, `ReviewItem`,
  `UpdateMasterResult`. Reuses the existing extraction building blocks
  (`file_reader`, `product_comparison.extract_products`,
  `workbook_provenance`, `sales_history`) unchanged; only WHEN/HOW
  OFTEN they run, and how results merge into `local_index.py`'s
  persistent store, is new.
- `local_index.py`: added `is_new_file()` (distinguishes "new" from
  "changed" for the summary stats) and `delete_evidence_for_file()`
  (so a changed file's old evidence contribution is cleanly replaced,
  never duplicated or orphaned).
- `config.py`: added `index_db_path` setting (same pattern as
  `sales_report_path`).
- `gui.py`: added the "Update Master (daily use)" button, its worker
  thread (`UpdateMasterWorker`, same thread-safety pattern as
  `MatchingWorker`), the review table widget with Approve Proposed /
  Keep Existing / Mark Unresolved actions, and the post-run summary
  display. `Analyse` was renamed to "Analyse (full rebuild)" for
  clarity but its behavior, worker, and code path are byte-for-byte
  unchanged.

### Tests added: 8 (121 total, up from 113)

`test_update_master.py` -- all 7 requested scenarios (A-G) plus one
extra sanity check that Analyse still works independently:
first run, immediate no-change rerun, one new file added, one existing
file changed (evidence cleanly replaced, not duplicated), an approved
mapping surviving an unrelated later run, conflicting SOLD evidence
surfacing for review WITHOUT overwriting the approved mapping, and no
evidence growth across three repeated unchanged runs.

### Known limitation, stated honestly

This sandbox has no PySide6 installed and no network access to
install it, so `gui.py`'s new widgets/handlers were validated by
`py_compile` (syntax) and a manual cross-reference audit (every new
`self.` attribute traced back to where it's defined, every widget
initialized in `__init__` before any handler could use it) -- not by
actually running the GUI. This is the same limitation that has applied
to every GUI change in this project from the start, not something new
this round.

## Round: TEST BUILD 2 packaging fix

Smallest safe fix, per explicit instruction, before packaging:

- **`update_master.py`**: `ReviewItem` gained `item_id` -- a unique,
  stable identifier generated at creation (`uuid.uuid4().hex`), not
  derived from the other fields. Fixes the identified edge case: two
  field-for-field identical review items could previously both be
  affected by acting on just one, because row removal filtered by
  dataclass value equality.
- **`gui.py`**: `_remove_review_rows()` now filters by `item_id`
  instead of value equality -- the only behavioral change.
- 2 regression tests added proving the exact scenario: two identical
  items get distinct IDs, and removing one by ID leaves the other
  untouched.

No other changes. 123/123 tests passing (up from 121).

The GitHub Actions workflow (`.github/workflows/build-windows-exe.yml`)
and `build_exe.bat` are UNCHANGED from TEST BUILD 1 -- the build name
inside them (`Project_Matching_Assistant_TEST_BUILD`) already
correctly signals "not production." Replacing TEST BUILD 1's source
with TEST BUILD 2's in the same GitHub repo and re-running the
existing Action is the intended, unchanged workflow.
