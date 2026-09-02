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
