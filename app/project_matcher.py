"""
project_matcher.py
-------------------
Core file-level matching engine: determines whether a factory
quotation and a customer quotation belong to the same project, BEFORE
any product-level comparison happens. Unaffected by the theme/factory-
recommendation work added on top -- this module answers "which FILE
pairs with which FILE," a different question from "which factory/DB
code should be recommended for this row."
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Set

from .file_reader import WorkbookSummary

REFERENCE_TOKEN_RE = re.compile(r"\b\d{4}[-/][A-Za-z0-9][A-Za-z0-9\-/]{1,15}\b")
FACTORY_CODE_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,4}\d{2,8}(?:-[A-Za-z0-9]+)?(?![A-Za-z0-9])", re.IGNORECASE)
DB_CODE_RE = re.compile(r"\bDB[A-Z0-9][A-Z0-9\-]{2,}\b", re.IGNORECASE)
DIGIT_RUN_RE = re.compile(r"\d{3,}")

_COLOR_SUFFIX_RE = re.compile(r"^[A-Z]\d{3}$", re.IGNORECASE)


def _is_likely_color_suffix(token: str) -> bool:
    return bool(_COLOR_SUFFIX_RE.match(token))


KNOWN_FACTORY_PREFIXES = [
    "factory_price_", "factory_", "华星", "yx_华星", "jx_hx-", "jx_hx_", "jx_", "yx_", "hx_",
]
KNOWN_NEUTRAL_PREFIXES = ["quotation_", "quote_-_", "rfq_-_", "rfq-", "inquiry_", "copy_of_"]

BLUE_FILL_TONES = {
    "FFDDEBF7", "FFBDD7EE", "FF9BC2E6", "FF2E74B5", "FF1F4E78",
    "FFADD8E6", "FF00B0F0", "FFB4C6E7", "FF8EA9DB", "FF2E5395",
}

QUANTITY_MIN, QUANTITY_MAX = 1, 100000


@dataclass
class FileSignals:
    filename: str
    path: str
    created: Optional[str]
    reference_tokens: Set[str] = field(default_factory=set)
    factory_codes: Set[str] = field(default_factory=set)
    db_codes: Set[str] = field(default_factory=set)
    quantities: List[float] = field(default_factory=list)
    descriptions: List[str] = field(default_factory=list)
    blue_cell_texts: List[str] = field(default_factory=list)
    sheet_names: Set[str] = field(default_factory=set)
    normalized_stem: str = ""
    has_db_codes: bool = False
    has_rmb_style_pricing: bool = False
    row_count: int = 0


@dataclass
class MatchReason:
    points: int
    text: str


@dataclass
class ProjectMatch:
    factory_file: str
    customer_file: str
    confidence: int
    reasons: List[str]
    factory_created: Optional[str]
    customer_created: Optional[str]


def _normalize_stem(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0].lower()
    stem = re.sub(r"[_\s]+", "_", stem)
    for prefix in KNOWN_FACTORY_PREFIXES + KNOWN_NEUTRAL_PREFIXES:
        if stem.startswith(prefix.lower()):
            stem = stem[len(prefix):]
    stem = re.sub(r"[_\-]*\(?\d+\)?$", "", stem)
    return stem.strip("_- ")


def extract_signals(wb: WorkbookSummary) -> FileSignals:
    sig = FileSignals(
        filename=wb.filename, path=str(wb.path), created=wb.created,
        normalized_stem=_normalize_stem(wb.filename),
        sheet_names={s.name for s in wb.sheets},
    )
    text = wb.all_text()
    sig.reference_tokens = set(REFERENCE_TOKEN_RE.findall(text))
    sig.db_codes = {m.upper() for m in DB_CODE_RE.findall(text)}
    sig.has_db_codes = len(sig.db_codes) > 0

    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", text))

    for sheet in wb.sheets:
        sig.row_count = max(sig.row_count, sheet.max_row)
        for cell in sheet.cells.values():
            if isinstance(cell.value, str):
                for m in FACTORY_CODE_RE.finditer(cell.value):
                    token = m.group(0).upper()
                    if not token.startswith("DB") and not _is_likely_color_suffix(token):
                        sig.factory_codes.add(token)
                if len(cell.value) > 8 and not DB_CODE_RE.search(cell.value):
                    sig.descriptions.append(cell.value.strip())
                if cell.fill_rgb and cell.fill_rgb.upper() in BLUE_FILL_TONES:
                    sig.blue_cell_texts.append(cell.value.strip())
            elif isinstance(cell.value, (int, float)) and QUANTITY_MIN <= cell.value <= QUANTITY_MAX:
                sig.quantities.append(float(cell.value))

    sig.has_rmb_style_pricing = has_cjk and not sig.has_db_codes and len(sig.factory_codes) > 0
    return sig


def _timestamp_proximity_score(a: Optional[str], b: Optional[str]) -> tuple[int, Optional[str]]:
    if not a or not b:
        return 0, None
    try:
        dt_a, dt_b = datetime.fromisoformat(a), datetime.fromisoformat(b)
    except ValueError:
        return 0, None
    delta = abs((dt_a - dt_b).total_seconds())
    if delta <= 60:
        return 35, f"Created within the same minute ({a} vs {b})"
    if delta <= 3600:
        return 20, f"Created within the same hour ({a} vs {b})"
    if delta <= 86400:
        return 12, f"Created on the same day ({a} vs {b})"
    if delta <= 7 * 86400:
        return 5, f"Created within the same week ({a} vs {b})"
    return 0, None


def _factory_code_overlap_score(factory: FileSignals, customer: FileSignals) -> tuple[int, Optional[str]]:
    if not factory.factory_codes:
        return 0, None
    exact_overlap = factory.factory_codes & customer.factory_codes
    embedded = set()
    if customer.db_codes:
        for fcode in factory.factory_codes:
            for dbcode in customer.db_codes:
                if fcode.upper() in dbcode.upper():
                    embedded.add(fcode)
                    break
    strong_matches = exact_overlap | embedded
    if strong_matches:
        ratio = len(strong_matches) / len(factory.factory_codes)
        pts = round(min(ratio, 1.0) * 30)
        kind = "embedded in DB codes" if embedded and not exact_overlap else "shared"
        return pts, f"{len(strong_matches)} of {len(factory.factory_codes)} factory codes {kind} on the other file ({ratio:.0%})"

    factory_digits = {DIGIT_RUN_RE.search(fc).group(0) for fc in factory.factory_codes if DIGIT_RUN_RE.search(fc)}
    customer_digits = {DIGIT_RUN_RE.search(dc).group(0) for dc in customer.db_codes if DIGIT_RUN_RE.search(dc)}
    fuzzy = {fd for fd in factory_digits for cd in customer_digits if fd in cd or cd in fd}
    if fuzzy and factory_digits:
        ratio = len(fuzzy) / len(factory_digits)
        pts = round(min(ratio, 1.0) * 15)
        return pts, f"{len(fuzzy)} of {len(factory_digits)} factory code numbers fuzzy-match DB code numbers ({ratio:.0%})"
    return 0, None


def score_pair(factory: FileSignals, customer: FileSignals) -> ProjectMatch:
    reasons: List[MatchReason] = []
    total = 0

    shared_refs = factory.reference_tokens & customer.reference_tokens
    if shared_refs:
        pts = 55
        total += pts
        reasons.append(MatchReason(pts, f"Shared reference number(s): {', '.join(sorted(shared_refs))[:120]}"))

    ts_pts, ts_reason = _timestamp_proximity_score(factory.created, customer.created)
    if ts_pts:
        total += ts_pts
        reasons.append(MatchReason(ts_pts, ts_reason))

    fc_pts, fc_reason = _factory_code_overlap_score(factory, customer)
    if fc_pts:
        total += fc_pts
        reasons.append(MatchReason(fc_pts, fc_reason))

    if factory.quantities and customer.quantities:
        fq_set, cq_set = set(factory.quantities), set(customer.quantities)
        overlap = fq_set & cq_set
        union = fq_set | cq_set
        ratio = len(overlap) / len(union) if union else 0
        if ratio > 0.3:
            pts = round(ratio * 15)
            total += pts
            reasons.append(MatchReason(pts, f"Order quantities overlap {ratio:.0%} between the two files"))

    stem_ratio = SequenceMatcher(None, factory.normalized_stem, customer.normalized_stem).ratio()
    if stem_ratio > 0.5:
        pts = round(stem_ratio * 10)
        total += pts
        reasons.append(MatchReason(pts, f"Filename stems are {stem_ratio:.0%} similar after removing known prefixes"))

    shared_sheets = factory.sheet_names & customer.sheet_names
    if shared_sheets and shared_sheets != {"Sheet1"}:
        pts = 5
        total += pts
        reasons.append(MatchReason(pts, f"Shared sheet name(s): {', '.join(sorted(shared_sheets))[:80]}"))

    total = min(total, 100)
    reasons.sort(key=lambda r: -r.points)
    return ProjectMatch(
        factory_file=factory.filename, customer_file=customer.filename,
        confidence=total, reasons=[r.text for r in reasons],
        factory_created=factory.created, customer_created=customer.created,
    )


@dataclass
class MatchingResult:
    confirmed: List[ProjectMatch] = field(default_factory=list)
    needs_review: List[ProjectMatch] = field(default_factory=list)
    unmatched_factory: List[str] = field(default_factory=list)
    unmatched_customer: List[str] = field(default_factory=list)
    closest_candidate: Dict[str, ProjectMatch] = field(default_factory=dict)
    all_factory_signals: Dict[str, FileSignals] = field(default_factory=dict)
    all_customer_signals: Dict[str, FileSignals] = field(default_factory=dict)


CONFIRMED_THRESHOLD = 90
REVIEW_THRESHOLD = 70


def match_projects(
    factory_signals: List[FileSignals],
    customer_signals: List[FileSignals],
    confirmed_threshold: int = CONFIRMED_THRESHOLD,
    review_threshold: int = REVIEW_THRESHOLD,
) -> MatchingResult:
    result = MatchingResult(
        all_factory_signals={s.filename: s for s in factory_signals},
        all_customer_signals={s.filename: s for s in customer_signals},
    )
    matched_factory_files: Set[str] = set()
    matched_customer_files: Set[str] = set()
    best_for_factory: Dict[str, ProjectMatch] = {}
    best_for_customer: Dict[str, ProjectMatch] = {}

    for factory in factory_signals:
        for customer in customer_signals:
            match = score_pair(factory, customer)
            if match.confidence >= confirmed_threshold:
                result.confirmed.append(match)
                matched_factory_files.add(factory.filename)
                matched_customer_files.add(customer.filename)
            elif match.confidence >= review_threshold:
                result.needs_review.append(match)
                matched_factory_files.add(factory.filename)
                matched_customer_files.add(customer.filename)

            if factory.filename not in best_for_factory or match.confidence > best_for_factory[factory.filename].confidence:
                best_for_factory[factory.filename] = match
            if customer.filename not in best_for_customer or match.confidence > best_for_customer[customer.filename].confidence:
                best_for_customer[customer.filename] = match

    result.confirmed.sort(key=lambda m: -m.confidence)
    result.needs_review.sort(key=lambda m: -m.confidence)
    result.unmatched_factory = sorted(s.filename for s in factory_signals if s.filename not in matched_factory_files)
    result.unmatched_customer = sorted(s.filename for s in customer_signals if s.filename not in matched_customer_files)
    for fname in result.unmatched_factory:
        if fname in best_for_factory and best_for_factory[fname].confidence > 0:
            result.closest_candidate[fname] = best_for_factory[fname]
    for fname in result.unmatched_customer:
        if fname in best_for_customer and best_for_customer[fname].confidence > 0:
            result.closest_candidate[fname] = best_for_customer[fname]
    return result
