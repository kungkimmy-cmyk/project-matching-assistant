"""
orchestrator.py
----------------
Top-level run. Now follows the full hierarchy per the updated
business spec:

    1. Read every file (as before).
    2. Match projects file-to-file (project_matcher.py -- unchanged).
    3. For each matched project: analyze the RFQ AS A WHOLE first
       (theme_analysis.py) BEFORE looking at individual rows.
    4. Determine the recommended factory for that theme (factory_rules.py).
    5. Load SOLD evidence (sales_history.py) once for the whole run,
       and build the QUOTED/FACTORY QUOTED evidence from every
       extracted product across every file, feeding
       historical_evidence.py's authoritative index.
    6. Extract embedded photos alongside product extraction
       (photo_extractor.py) -- storage/association only, no
       similarity matching yet.
    7. Compare products WITH all of that context.
    8. Aggregate the DB code mapping (unchanged).

Sequential processing (same reasoning as before: reliability over
speed, no multiprocessing in a packaged Windows .exe).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import file_reader, project_matcher, product_comparison, crash_logger
from . import theme_analysis, factory_rules, sales_history, historical_evidence, photo_extractor
from .config import MatcherConfig

logger = logging.getLogger(__name__)

FileProgressCallback = Optional[Callable[[int, int, str, float], None]]
ProgressCallback = Optional[Callable[[str], None]]


@dataclass
class RunStats:
    factory_files_found: int = 0
    customer_files_found: int = 0
    factory_files_failed: int = 0
    customer_files_failed: int = 0
    sales_records_loaded: int = 0
    sales_records_matched_to_factory_code: int = 0
    photos_extracted: int = 0
    factory_folders_scanned: List[str] = field(default_factory=list)
    customer_folders_scanned: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class ProjectAnalysis:
    """Everything computed for ONE matched factory/customer pair,
    before/alongside the product-level comparison -- kept as a
    structured record so the Excel output can show theme reasoning,
    not just the final numbers."""
    match: project_matcher.ProjectMatch
    theme: theme_analysis.RfqThemeProfile
    factory_recommendation: factory_rules.FactoryRecommendation
    comparison_rows: List[product_comparison.ProductComparisonRow]


@dataclass
class MatchingRunResult:
    matching: project_matcher.MatchingResult
    project_analyses: List[ProjectAnalysis]
    db_code_mapping: List[product_comparison.DbCodeMappingRow]
    stats: RunStats


def _discover_files(folder: Path, cfg: MatcherConfig) -> List[Path]:
    exts = {e.lower() for e in cfg.supported_extensions}
    return [p for p in sorted(folder.rglob("*")) if p.is_file() and p.suffix.lower() in exts and not p.name.startswith("~$")]


def _discover_files_multi(folders: Sequence[Path | str], cfg: MatcherConfig) -> List[Path]:
    """Same as _discover_files but across several folders, de-duplicated
    by resolved path (in case the same folder is added twice, or one
    selected folder is a subfolder of another)."""
    seen: dict = {}
    for folder in folders:
        folder_path = Path(folder)
        for p in _discover_files(folder_path, cfg):
            resolved = p.resolve()
            if resolved not in seen:
                seen[resolved] = p
    return sorted(seen.values())


def _report(cb: ProgressCallback, msg: str) -> None:
    logger.info(msg)
    if cb:
        cb(msg)


def run_matching(
    factory_folders: Sequence[Path | str] | Path | str,
    customer_folders: Sequence[Path | str] | Path | str,
    cfg: MatcherConfig,
    photo_store_dir: Optional[Path] = None,
    progress: ProgressCallback = None,
    on_file_progress: FileProgressCallback = None,
) -> MatchingRunResult:
    # Backward compatible: a single path (not a list) still works,
    # since earlier callers (and some tests) pass one folder directly.
    if isinstance(factory_folders, (str, Path)):
        factory_folders = [factory_folders]
    if isinstance(customer_folders, (str, Path)):
        customer_folders = [customer_folders]
    factory_folders = [Path(f) for f in factory_folders]
    customer_folders = [Path(f) for f in customer_folders]

    stats = RunStats(
        factory_folders_scanned=[str(f) for f in factory_folders],
        customer_folders_scanned=[str(f) for f in customer_folders],
    )

    factory_paths = _discover_files_multi(factory_folders, cfg)
    customer_paths = _discover_files_multi(customer_folders, cfg)
    stats.factory_files_found = len(factory_paths)
    stats.customer_files_found = len(customer_paths)
    _report(progress, f"Found {len(factory_paths)} factory file(s) and {len(customer_paths)} customer file(s)")

    # --- SOLD evidence setup. Real sales reports (confirmed against
    # the actual uploaded file) have NO factory-code column, only a DB
    # code -- but historical_evidence.py resolves evidence per FACTORY
    # code. Reading the raw sales rows now; the factory-code backfill
    # happens after every file is read (below), once we have a
    # db_code -> factory_code map built from every QUOTED/FACTORY
    # QUOTED record across every file. Without this backfill, SOLD
    # evidence would silently never be usable at all -- found and
    # fixed via real-data testing, not a hypothetical concern. ---
    raw_sales: List[sales_history.SalesRecord] = []
    if cfg.sales_report_path:
        raw_sales = sales_history.read_sales_history(cfg.sales_report_path, cfg)
        stats.sales_records_loaded = len(raw_sales)
        _report(progress, f"Loaded {len(raw_sales)} raw sales row(s) from the sales report (factory-code cross-reference happens after files are read)")
    else:
        _report(progress, "No sales report configured (matcher_config.json: sales_report_path) -- proceeding with QUOTED evidence only")

    photo_store = photo_extractor.PhotoStore(store_dir=photo_store_dir or (Path(cfg_dir_hint(cfg)) / "photo_store"))
    photo_store.load()

    all_paths = [(p, "factory") for p in factory_paths] + [(p, "customer") for p in customer_paths]
    total = len(all_paths)
    start_time = time.monotonic()

    factory_workbooks: List[file_reader.WorkbookSummary] = []
    customer_workbooks: List[file_reader.WorkbookSummary] = []
    factory_signals: List[project_matcher.FileSignals] = []
    customer_signals: List[project_matcher.FileSignals] = []
    wb_by_name: dict = {}

    for index, (path, kind) in enumerate(all_paths, start=1):
        if on_file_progress:
            on_file_progress(index - 1, total, path.name, time.monotonic() - start_time)
        crash_logger.checkpoint(f"Reading {kind} file {index}/{total}: {path.name}")
        try:
            wb = file_reader.read_workbook(path)
            sig = project_matcher.extract_signals(wb)
            photos = photo_extractor.extract_photos(wb, cfg, photo_store)
            stats.photos_extracted += len(photos)
        except Exception as exc:  # noqa: BLE001
            _report(progress, f"ERROR reading {path.name}: {exc}")
            stats.errors.append(f"{path.name}: {exc}")
            if kind == "factory":
                stats.factory_files_failed += 1
            else:
                stats.customer_files_failed += 1
            continue

        wb_by_name[wb.filename] = wb
        if kind == "factory":
            factory_workbooks.append(wb)
            factory_signals.append(sig)
        else:
            customer_workbooks.append(wb)
            customer_signals.append(sig)
        _report(progress, f"Read {path.name}: {len(sig.db_codes)} DB codes, {len(sig.factory_codes)} factory codes, {len(photos)} photo(s)")

    photo_store.save()
    if on_file_progress:
        on_file_progress(total, total, "", time.monotonic() - start_time)

    # --- Backfill factory codes onto SOLD records via a global
    # db_code -> factory_code map built from EVERY file read (not just
    # matched projects, for maximum cross-reference coverage) -- this
    # is what makes SOLD evidence usable at all, since the real sales
    # report has no factory-code column of its own. ---
    crash_logger.checkpoint("Cross-referencing sales report DB codes against factory codes from project files...")
    db_to_factory_code: dict = {}
    for wb in factory_workbooks + customer_workbooks:
        try:
            for p in product_comparison.extract_products(wb):
                if p.db_code and p.factory_code and p.db_code not in db_to_factory_code:
                    db_to_factory_code[p.db_code] = p.factory_code
        except Exception as exc:  # noqa: BLE001 - cross-reference building must not abort the run
            _report(progress, f"WARNING: could not extract products from {wb.filename} for cross-referencing: {exc}")

    sold_records: List[historical_evidence.HistoricalRecord] = []
    sold_unmatched_count = 0
    for s in raw_sales:
        if not s.db_code:
            continue
        factory_code = s.factory_code or db_to_factory_code.get(s.db_code)
        if not factory_code:
            sold_unmatched_count += 1
            continue  # can't place this sale into the factory-code-keyed hierarchy without one
        sold_records.append(historical_evidence.HistoricalRecord(
            factory_code=factory_code, db_code=s.db_code, tier=historical_evidence.EvidenceTier.SOLD,
            description=s.description, quantity=s.quantity, unit_price=s.unit_price,
            date=s.date, source=s.source_file,
        ))
    if raw_sales:
        stats.sales_records_matched_to_factory_code = len(sold_records)
        _report(progress, f"SOLD evidence: {len(sold_records)}/{len(raw_sales)} sales rows matched to a factory code "
                           f"via {len(db_to_factory_code)} cross-referenced DB codes ({sold_unmatched_count} sold DB codes "
                           f"don't appear in any project file read this run, so can't be placed yet)")

    crash_logger.checkpoint("Matching projects (file-level)...")
    matching = project_matcher.match_projects(
        factory_signals, customer_signals,
        confirmed_threshold=cfg.confirmed_threshold, review_threshold=cfg.review_threshold,
    )
    _report(progress, f"Matched {len(matching.confirmed)} confirmed, {len(matching.needs_review)} needing review")

    # --- Per matched project: theme analysis FIRST, then product comparison ---
    project_analyses: List[ProjectAnalysis] = []
    comparison_rows_for_mapping: List[Tuple[str, list]] = []
    all_quoted_records: List[historical_evidence.HistoricalRecord] = []

    crash_logger.checkpoint("Analyzing RFQ themes and comparing products for each matched project...")
    for match in matching.confirmed + matching.needs_review:
        factory_wb = wb_by_name.get(match.factory_file)
        customer_wb = wb_by_name.get(match.customer_file)
        if not factory_wb or not customer_wb:
            continue
        try:
            factory_products = product_comparison.extract_products(factory_wb)
            customer_products = product_comparison.extract_products(customer_wb)

            # STEP: analyze the RFQ as a whole before any row matching.
            # Theme analysis needs EVERY descriptive row, coded or not
            # (see theme_analysis.extract_theme_rows docstring) --
            # product-level matching below still correctly uses only
            # the coded rows (factory_products/customer_products).
            theme_rows = theme_analysis.extract_theme_rows(customer_wb)
            theme = theme_analysis.analyze_rfq_theme(theme_rows, cfg)
            recommendation = factory_rules.recommend_factory(theme, cfg)
            _report(progress, f"{match.customer_file}: theme={theme.dominant_theme} ({theme.theme_confidence}%), recommended factory={recommendation.factory or 'none'}")

            # QUOTED / FACTORY QUOTED evidence from this project's own files.
            for p in customer_products:
                if p.factory_code and p.db_code:
                    all_quoted_records.append(historical_evidence.HistoricalRecord(
                        factory_code=p.factory_code, db_code=p.db_code, tier=historical_evidence.EvidenceTier.QUOTED,
                        description=p.description, unit_price=p.price, date=customer_wb.created, source=p.source_file,
                    ))
            for p in factory_products:
                if p.factory_code and p.db_code:
                    all_quoted_records.append(historical_evidence.HistoricalRecord(
                        factory_code=p.factory_code, db_code=p.db_code, tier=historical_evidence.EvidenceTier.FACTORY_QUOTED,
                        description=p.description, unit_price=p.price, date=factory_wb.created, source=p.source_file,
                    ))

            authoritative_index = historical_evidence.build_authoritative_index(sold_records + all_quoted_records)

            comparison = product_comparison.compare_products(
                factory_products, customer_products,
                theme_profile=theme, factory_recommendation=recommendation,
                authoritative_index=authoritative_index, cfg=cfg,
            )
            project_analyses.append(ProjectAnalysis(
                match=match, theme=theme, factory_recommendation=recommendation, comparison_rows=comparison,
            ))
            project_label = f"{match.factory_file} <-> {match.customer_file}"
            comparison_rows_for_mapping.append((project_label, comparison))
        except Exception as exc:  # noqa: BLE001
            _report(progress, f"ERROR analyzing {match.factory_file} <-> {match.customer_file}: {exc}")
            stats.errors.append(f"project analysis {match.factory_file}/{match.customer_file}: {exc}")

    crash_logger.checkpoint("Building DB code mapping...")
    db_code_mapping = product_comparison.build_db_code_mapping(comparison_rows_for_mapping)

    return MatchingRunResult(
        matching=matching, project_analyses=project_analyses,
        db_code_mapping=db_code_mapping, stats=stats,
    )


def cfg_dir_hint(cfg: MatcherConfig) -> str:
    """Best-effort directory to put the photo store in when the caller
    didn't specify one -- next to the sales report if configured,
    else the current directory. Not load-bearing; callers (main.py,
    gui.py) normally pass photo_store_dir explicitly, anchored next to
    the .exe the same way config/logs are."""
    if cfg.sales_report_path:
        return str(Path(cfg.sales_report_path).resolve().parent)
    return "."
