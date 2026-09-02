# 2025 Project Matching Assistant -- TEST BUILD

**This is the first local validation build, not the final production version.** A completely separate application from the RFQ Master Database Extractor -- nothing in that project was touched to build this.

## For non-programmers: how to run it

1. Double-click `Project_Matching_Assistant_TEST_BUILD.exe`
2. Click **Add Factory Folder...** for each folder of factory
   quotations (repeat for as many folders as you have -- 2025 and
   2026, different archive locations, etc.)
3. Click **Add Customer Folder...** the same way for customer
   quotation folders
4. (Optional) Click **Select Sales Report (SOLD history)...** and
   browse to your local Mimosa Sales Invoice Report file -- it stays
   on your computer, nothing is uploaded anywhere
5. Click **Analyse** -- progress bar shows files read, current
   filename, and an ETA
6. Save the results workbook when prompted
7. Click **Open Results** to view it immediately

All processing happens locally on your computer. Your files are never
uploaded anywhere by this application.

## What this build validates

- Multiple factory folders + multiple customer folders combined into
  one run
- Sales report selected as a local file (no re-upload needed)
- File-level project matching (factory quotation <-> customer quotation)
- RFQ-level theme/colour analysis, done BEFORE individual row matching
- Factory recommendation (YIXIN for rustic/brown-clay, HUAXIN for
  other strong colour themes, JIAXIANG for plain whiteware)
- Product-level matching with best-match + alternatives
- SOLD > QUOTED > FACTORY QUOTED > CATALOGUE historical evidence
  (SOLD evidence cross-referenced from your sales report against DB
  codes found in your project files)
- Reactive glaze flagging (DB6/Mirage collection, MG/ND colour codes)
- Photo extraction from embedded images (storage + coarse colour hint
  -- not yet full visual similarity matching)
- Original source files are never modified -- everything is read-only

## Output: `Matching_Results.xlsx`

Every sheet is marked **CONFIDENTIAL -- INTERNAL USE ONLY** and
contains factory cost/pricing data. This tool does not generate any
customer-facing output.

| Sheet | Contents |
|---|---|
| **Confirmed Matches** | Factory/Customer file pairs, confidence, reasoning, RFQ theme, recommended factory |
| **Needs Review** | 70-89% confidence matches, with the specific reason |
| **Unmatched Factory/Customer Files** | Including a "closest candidate" even when below threshold, so nothing is silently lost |
| **Product Comparison** | Best match + up to 2 alternatives per product, auto-selected vs needs-review, reactive glaze flag, historical evidence tier |
| **DB Code Mapping** | Factory codes assigned more than one DB code across projects -- review only, never auto-merged |
| **Master Migration File** | Aligned to the RFQ Extractor's own schema for future import |

## After you test this

Please send back:
1. **`Matching_Log.txt`** and **`Matcher_Crash_Log.txt`** (both land
   next to the .exe) -- even if nothing crashed, these have the run
   details
2. **The saved `Matching_Results.xlsx`** (or at least a description of
   what you see in it) -- this contains your real factory costs, so
   only share it back here if you're comfortable with that; a summary
   of the numbers/any obviously wrong rows is also fine
3. Anything that looks wrong, or crashed, or took much longer than
   expected

## Requirements (running from source, not needed if using the .exe)

```
pip install openpyxl PySide6 pillow
```
LibreOffice, installed separately, is needed only for reading legacy
`.xls` files.

## Project structure

```
main.py                entry point (CLI + GUI)
app/
  file_reader.py         standalone Excel reader + embedded image extraction
  project_matcher.py      file-level factory<->customer matching engine
  theme_analysis.py        RFQ-level colour/theme classification
  factory_rules.py          YIXIN/HUAXIN/JIAXIANG recommendation logic
  sales_history.py           Mimosa Sales Invoice Report reader (SOLD evidence)
  historical_evidence.py      SOLD > QUOTED > FACTORY QUOTED > CATALOGUE resolution
  photo_extractor.py           embedded image extraction + coarse colour hints
  product_comparison.py         product-level matching + DB code mapping
  orchestrator.py                 ties everything together
  excel_writer.py                   Matching_Results.xlsx builder
  gui.py                             PySide6 desktop UI (multi-folder + file pickers)
  crash_logger.py                    low-level crash diagnostics
  config.py                           matcher_config.json schema
tests/                                79 unit tests
```
