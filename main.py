"""
main.py
-------
Entry point for the 2025 Project Matching Assistant.

    python main.py                                  -> launches the GUI
    python main.py --cli <factory_folder> <customer_folder> [-o out.xlsx]
                                                      -> headless run
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app import crash_logger  # noqa: E402
crash_logger.redirect_stdio_if_missing()
crash_logger.install_global_excepthooks()
crash_logger.checkpoint("Application starting")

from app.config import load_config, DEFAULT_CONFIG_PATH
from app.orchestrator import run_matching
from app.excel_writer import write_matching_results


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()
LOG_FILE = str(APP_DIR / "Matching_Log.txt")
DEFAULT_OUTPUT_FILE = str(APP_DIR / "Matching_Results.xlsx")
DEFAULT_CONFIG_FULL_PATH = str(APP_DIR / DEFAULT_CONFIG_PATH.name)
DEFAULT_PHOTO_STORE_DIR = APP_DIR / "photo_store"


def _setup_logging() -> None:
    handlers = [logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8")]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", handlers=handlers)


def run_cli(factory_folders: list, customer_folders: list, output: str, config_path: str) -> int:
    _setup_logging()
    log = logging.getLogger("main")
    cfg = load_config(config_path)

    log.info("Factory folder(s):  %s", ", ".join(factory_folders))
    log.info("Customer folder(s): %s", ", ".join(customer_folders))
    if cfg.sales_report_path:
        log.info("Sales report:    %s", cfg.sales_report_path)
    else:
        log.info("Sales report:    (not configured -- QUOTED evidence only)")

    result = run_matching(factory_folders, customer_folders, cfg, photo_store_dir=DEFAULT_PHOTO_STORE_DIR, progress=lambda m: None)
    out_path = write_matching_results(result, output)

    log.info("=" * 60)
    log.info("Factory folders scanned: %d", len(result.stats.factory_folders_scanned))
    log.info("Customer folders scanned: %d", len(result.stats.customer_folders_scanned))
    log.info("Factory files found:   %d", result.stats.factory_files_found)
    log.info("Customer files found:  %d", result.stats.customer_files_found)
    log.info("Sales records loaded:  %d", result.stats.sales_records_loaded)
    log.info("Photos extracted:      %d", result.stats.photos_extracted)
    log.info("Confirmed matches:     %d", len(result.matching.confirmed))
    log.info("Needs review:          %d", len(result.matching.needs_review))
    log.info("Unmatched factory:     %d", len(result.matching.unmatched_factory))
    log.info("Unmatched customer:    %d", len(result.matching.unmatched_customer))
    log.info("DB code mapping rows:  %d (%d flagged with alternates)",
              len(result.db_code_mapping), sum(1 for m in result.db_code_mapping if m.alternative_db_codes))
    total_products = sum(len(pa.comparison_rows) for pa in result.project_analyses)
    auto_selected = sum(1 for pa in result.project_analyses for p in pa.comparison_rows if p.auto_selected)
    needs_review_products = sum(1 for pa in result.project_analyses for p in pa.comparison_rows if p.matched and not p.auto_selected)
    reactive = sum(1 for pa in result.project_analyses for p in pa.comparison_rows if p.reactive_glaze)
    log.info("Products compared:     %d (%d auto-selected, %d need review, %d reactive glaze)",
              total_products, auto_selected, needs_review_products, reactive)
    if result.stats.errors:
        log.info("Errors:")
        for e in result.stats.errors:
            log.info("  - %s", e)
    log.info("Output written to: %s", out_path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="2025 Project Matching Assistant")
    parser.add_argument(
        "--factory-folders", nargs="+", metavar="FOLDER",
        help="Run headless: one or more factory-quotation folders (requires --customer-folders too)",
    )
    parser.add_argument(
        "--customer-folders", nargs="+", metavar="FOLDER",
        help="Run headless: one or more customer-quotation folders (requires --factory-folders too)",
    )
    parser.add_argument("-o", "--output", default=DEFAULT_OUTPUT_FILE, help="Output workbook path")
    parser.add_argument("--config", default=DEFAULT_CONFIG_FULL_PATH, help="Path to matcher_config.json")
    args = parser.parse_args()

    if args.factory_folders or args.customer_folders:
        if not args.factory_folders or not args.customer_folders:
            print("Error: need at least one --factory-folders AND at least one --customer-folders entry.", file=sys.stderr)
            return 2
        return run_cli(args.factory_folders, args.customer_folders, args.output, args.config)

    from app.gui import launch_gui
    return launch_gui(args.config)


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception as exc:  # noqa: BLE001
        crash_logger.write_crash_report("Uncaught exception escaped main()", exc)
        raise
    raise SystemExit(exit_code)
