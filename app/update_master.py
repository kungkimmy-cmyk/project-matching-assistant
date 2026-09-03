"""
update_master.py
------------------
The "Update Master" daily-use workflow: incremental, persistent,
review-queue-driven -- distinct from (and does not modify) the
existing "Analyse" full-rebuild path in orchestrator.py.

Deliberately a SEPARATE module rather than folded into orchestrator.py,
per the explicit requirement to keep Analyse unchanged as a full-
rebuild/testing option. Reuses the same extraction building blocks
(file_reader, product_comparison.extract_products, workbook_provenance,
sales_history) -- nothing about HOW a file is read or evidence is
derived changes; only WHEN/HOW OFTEN that work happens, and how
results are merged into the persistent store, is new.

Core guarantees (see tests/test_update_master.py for the full matrix):
  - A file is only re-read/re-processed if local_index.needs_processing()
    says so (new file, or content hash changed since last run).
  - An unchanged file contributes ZERO new evidence rows and is not
    re-read at all.
  - A changed file's OLD evidence contribution is deleted before its
    fresh extraction is stored (local_index.delete_evidence_for_file),
    so reprocessing never duplicates unchanged rows or orphans removed
    ones.
  - A previously approved mapping (local_index.reviewed_mappings) is
    NEVER silently overwritten by new evidence, however strong. A
    conflicting proposal -- especially from SOLD-tier evidence -- is
    surfaced as a ReviewItem instead. Only an explicit human action via
    the GUI's review table calls mark_mapping_reviewed() to change it.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import file_reader, product_comparison, sales_history
from . import workbook_provenance, local_index, crash_logger
from .config import MatcherConfig
from .historical_evidence import EvidenceTier, HistoricalRecord, TIER_LABELS
from .project_matcher import split_finish_suffix

logger = logging.getLogger(__name__)

ProgressCallback = Optional[Callable[[str], None]]
FileProgressCallback = Optional[Callable[[int, int, str, float], None]]


@dataclass
class UpdateMasterStats:
    files_scanned: int = 0
    unchanged_skipped: int = 0
    new_processed: int = 0
    changed_reprocessed: int = 0
    failed: int = 0
    new_evidence_added: int = 0
    mappings_auto_confirmed: int = 0
    mappings_requiring_review: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class ReviewItem:
    """One row of the GUI review table. item_id is a unique, stable
    identifier (not derived from the other fields) so the GUI can act
    on the exact selected row even when two ReviewItems are otherwise
    field-for-field identical -- using dataclass value equality for
    this previously meant such a pair could both be removed by acting
    on just one of them."""
    factory_code: str
    proposed_db_code: str
    finish_code: str
    description: str
    source: str
    evidence_tier: str
    confidence: str
    prior_approved_mapping: str
    reason: str
    item_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class UpdateMasterResult:
    stats: UpdateMasterStats
    review_items: List[ReviewItem]


def _report(cb: ProgressCallback, msg: str) -> None:
    logger.info(msg)
    if cb:
        cb(msg)


def _discover_files(folders: Sequence[Path | str], cfg: MatcherConfig) -> List[Path]:
    exts = {e.lower() for e in cfg.supported_extensions}
    seen: Dict[Path, Path] = {}
    for folder in folders:
        for p in sorted(Path(folder).rglob("*")):
            if p.is_file() and p.suffix.lower() in exts and not p.name.startswith("~$"):
                resolved = p.resolve()
                seen.setdefault(resolved, p)
    return sorted(seen.values())


def _process_quotation_file(
    path: Path, cfg: MatcherConfig, index_path: Path, is_factory_side: bool,
) -> Tuple[int, List[str]]:
    errors: List[str] = []
    try:
        wb = file_reader.read_workbook(path)
    except Exception as exc:  # noqa: BLE001
        return 0, [f"{path.name}: {exc}"]

    try:
        products = product_comparison.extract_products(wb, cfg)
    except Exception as exc:  # noqa: BLE001
        return 0, [f"{path.name}: {exc}"]

    tier = EvidenceTier.QUOTED
    provenance_label = "customer_file"
    if is_factory_side:
        assessment = workbook_provenance.assess_provenance(wb, cfg)
        provenance_label = assessment.label
        tier = EvidenceTier.QUOTED if assessment.label == "likely_po_or_negotiation_working_file" else EvidenceTier.FACTORY_QUOTED

    stored = 0
    for p in products:
        if not p.factory_code or not p.db_code:
            continue
        record = HistoricalRecord(
            factory_code=p.factory_code, db_code=p.db_code, tier=tier,
            description=p.description, unit_price=p.price, date=wb.created,
            source=path.name, provenance=provenance_label,
        )
        local_index.store_evidence(record, str(path), index_path)
        stored += 1
    return stored, errors


def _process_sales_report(path: Path, cfg: MatcherConfig, index_path: Path) -> Tuple[int, List[str]]:
    try:
        raw_sales = sales_history.read_sales_history(path, cfg)
    except Exception as exc:  # noqa: BLE001
        return 0, [f"{path.name}: {exc}"]

    known_evidence = local_index.load_all_evidence(index_path)
    db_to_factory: Dict[str, str] = {}
    for r in known_evidence:
        if r.db_code and r.factory_code and r.db_code not in db_to_factory:
            db_to_factory[r.db_code] = r.factory_code

    stored = 0
    for s in raw_sales:
        if not s.db_code:
            continue
        factory_code = s.factory_code or db_to_factory.get(s.db_code)
        if not factory_code:
            continue
        record = HistoricalRecord(
            factory_code=factory_code, db_code=s.db_code, tier=EvidenceTier.SOLD,
            description=s.description, quantity=s.quantity, unit_price=s.unit_price,
            date=s.date, source=path.name, provenance="sales_report",
        )
        local_index.store_evidence(record, str(path), index_path)
        stored += 1
    return stored, []


def _build_review_items(cfg: MatcherConfig, index_path: Path) -> Tuple[List[ReviewItem], int, int]:
    all_evidence = local_index.load_all_evidence(index_path)

    by_factory: Dict[str, List[HistoricalRecord]] = {}
    for r in all_evidence:
        if r.factory_code:
            by_factory.setdefault(r.factory_code, []).append(r)

    review_items: List[ReviewItem] = []
    auto_confirmed = 0
    requiring_review = 0

    for factory_code, records in by_factory.items():
        base_codes: Dict[str, HistoricalRecord] = {}
        for r in records:
            base, finish = split_finish_suffix(r.db_code, cfg)
            existing = base_codes.get(base)
            if existing is None or r.tier > existing.tier:
                base_codes[base] = r

        approved = local_index.get_reviewed_mapping(factory_code, index_path)
        distinct_bases = list(base_codes.keys())

        if approved:
            if len(distinct_bases) == 1 and approved in distinct_bases:
                auto_confirmed += 1
                continue
            conflicting = [b for b in distinct_bases if b != approved]
            if not conflicting:
                auto_confirmed += 1
                continue
            for base in conflicting:
                rec = base_codes[base]
                _b, finish = split_finish_suffix(rec.db_code, cfg)
                requiring_review += 1
                review_items.append(ReviewItem(
                    factory_code=factory_code, proposed_db_code=base, finish_code=finish or "",
                    description=rec.description or "", source=rec.source,
                    evidence_tier=TIER_LABELS[rec.tier], confidence="High" if rec.tier == EvidenceTier.SOLD else "Medium",
                    prior_approved_mapping=approved,
                    reason=(
                        f"New {TIER_LABELS[rec.tier]} evidence proposes '{base}', which conflicts with the "
                        f"already-approved mapping '{approved}'. The approved mapping has NOT been changed."
                    ),
                ))
        else:
            if len(distinct_bases) == 1:
                auto_confirmed += 1
                continue
            for base in distinct_bases:
                rec = base_codes[base]
                _b, finish = split_finish_suffix(rec.db_code, cfg)
                requiring_review += 1
                review_items.append(ReviewItem(
                    factory_code=factory_code, proposed_db_code=base, finish_code=finish or "",
                    description=rec.description or "", source=rec.source,
                    evidence_tier=TIER_LABELS[rec.tier], confidence="High" if rec.tier == EvidenceTier.SOLD else "Medium",
                    prior_approved_mapping="",
                    reason=f"{len(distinct_bases)} different DB codes found for this factory code -- no prior approval on file.",
                ))

    review_items.sort(key=lambda it: (it.factory_code, -{"High": 2, "Medium": 1}.get(it.confidence, 0)))
    return review_items, auto_confirmed, requiring_review


def run_update_master(
    factory_folders: Sequence[Path | str],
    customer_folders: Sequence[Path | str],
    cfg: MatcherConfig,
    index_path: Path | str,
    progress: ProgressCallback = None,
    on_file_progress: FileProgressCallback = None,
) -> UpdateMasterResult:
    index_path = Path(index_path)
    local_index.init_index(index_path)
    stats = UpdateMasterStats()

    factory_paths = _discover_files(factory_folders, cfg)
    customer_paths = _discover_files(customer_folders, cfg)
    all_paths: List[Tuple[Path, Optional[bool]]] = [(p, True) for p in factory_paths] + [(p, False) for p in customer_paths]
    if cfg.sales_report_path:
        sales_path = Path(cfg.sales_report_path)
        if sales_path.exists():
            all_paths.append((sales_path, None))

    total = len(all_paths)
    stats.files_scanned = total
    _report(progress, f"Update Master: scanning {total} file(s) (factory + customer + sales report)")
    start_time = time.monotonic()

    for index, (path, is_factory_side) in enumerate(all_paths, start=1):
        if on_file_progress:
            on_file_progress(index - 1, total, path.name, time.monotonic() - start_time)

        if not local_index.needs_processing(path, index_path):
            stats.unchanged_skipped += 1
            continue

        is_new = local_index.is_new_file(path, index_path)
        crash_logger.checkpoint(f"Update Master: processing {'new' if is_new else 'changed'} file {index}/{total}: {path.name}")

        if not is_new:
            deleted = local_index.delete_evidence_for_file(str(path), index_path)
            if deleted:
                _report(progress, f"{path.name} changed -- removed {deleted} outdated evidence row(s) before re-extracting")

        if is_factory_side is None:
            rows_stored, errors = _process_sales_report(path, cfg, index_path)
        else:
            rows_stored, errors = _process_quotation_file(path, cfg, index_path, is_factory_side)

        if errors:
            stats.errors.extend(errors)
            stats.failed += 1
            _report(progress, f"ERROR processing {path.name}: {errors[0]}")
            continue

        local_index.record_processed_file(path, rows_stored, index_path)
        stats.new_evidence_added += rows_stored
        if is_new:
            stats.new_processed += 1
        else:
            stats.changed_reprocessed += 1
        _report(progress, f"{'Processed new' if is_new else 'Reprocessed changed'} file {path.name}: {rows_stored} evidence row(s)")

    if on_file_progress:
        on_file_progress(total, total, "", time.monotonic() - start_time)

    crash_logger.checkpoint("Update Master: building review queue against reviewed_mappings...")
    review_items, auto_confirmed, requiring_review = _build_review_items(cfg, index_path)
    stats.mappings_auto_confirmed = auto_confirmed
    stats.mappings_requiring_review = requiring_review

    _report(progress, f"Update Master complete: {stats.new_processed} new, {stats.changed_reprocessed} changed, "
                       f"{stats.unchanged_skipped} unchanged skipped, {requiring_review} mapping(s) need review")

    return UpdateMasterResult(stats=stats, review_items=review_items)
