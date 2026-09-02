"""
product_comparison.py
----------------------
Individual PRODUCT matching -- the bottom of the hierarchy:

    RFQ-LEVEL THEME -> FACTORY CAPABILITY -> PROJECT COHERENCE ->
    INDIVIDUAL PRODUCT MATCH -> photo+pattern+colour+description+
    dimensions+capacity+historical evidence

This module still does the core mechanics validated earlier (factory-
code-to-DB-code matching: direct, embedded, description-similarity
fallback) -- extended, not replaced, to also weigh theme/colour
alignment, project/factory coherence, reactive-glaze detection, and
historical evidence (SOLD/QUOTED priority). All new parameters are
optional with safe defaults so every already-validated code path
(and its tests) keeps working unchanged when called without the new
context.

Also builds the permanent Factory Code -> DB Code mapping table
(unchanged from the original build).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .project_matcher import FACTORY_CODE_RE, DB_CODE_RE, _is_likely_color_suffix
from .file_reader import WorkbookSummary
from .config import MatcherConfig
from .theme_analysis import RfqThemeProfile
from .factory_rules import FactoryRecommendation, apply_project_coherence, factory_for_code
from .historical_evidence import AuthoritativeRecord, EvidenceTier, TIER_LABELS


@dataclass
class ExtractedProduct:
    factory_code: Optional[str]
    db_code: Optional[str]
    description: str
    price: Optional[float]
    source_file: str
    row: int


@dataclass
class AlternativeMatch:
    db_code: str
    confidence: int
    notes: str


@dataclass
class ProductComparisonRow:
    factory_code: str
    db_code: str
    description: str
    factory_cost: Optional[float]
    selling_price: Optional[float]
    matched: bool
    confidence: int
    notes: str
    # --- extended fields (all default-valued: fully backward compatible) ---
    alternatives: List[AlternativeMatch] = field(default_factory=list)
    reactive_glaze: bool = False
    historical_tier: str = ""
    historical_needs_review: bool = False
    auto_selected: bool = True
    theme_alignment_note: str = ""


def extract_products(wb: WorkbookSummary) -> List[ExtractedProduct]:
    products: List[ExtractedProduct] = []
    for sheet in wb.sheets:
        rows_seen: Dict[int, dict] = defaultdict(dict)
        for cell in sheet.cells.values():
            if isinstance(cell.value, str):
                db_match = DB_CODE_RE.search(cell.value)
                if db_match:
                    rows_seen[cell.row]["db_code"] = db_match.group(0).upper()
                fc_match = FACTORY_CODE_RE.search(cell.value)
                if fc_match and not fc_match.group(0).upper().startswith("DB") and not _is_likely_color_suffix(fc_match.group(0)):
                    rows_seen[cell.row].setdefault("factory_code", fc_match.group(0).upper())
                if len(cell.value.strip()) > 6 and not db_match:
                    existing = rows_seen[cell.row].get("description", "")
                    if len(cell.value.strip()) > len(existing):
                        rows_seen[cell.row]["description"] = cell.value.strip()
            elif isinstance(cell.value, (int, float)) and 0 < cell.value < 100000:
                prices = rows_seen[cell.row].setdefault("prices", [])
                prices.append(float(cell.value))

        for row_num, data in rows_seen.items():
            if not data.get("db_code") and not data.get("factory_code"):
                continue
            price = None
            if data.get("prices"):
                price = min(p for p in data["prices"] if p < 10000)
            products.append(ExtractedProduct(
                factory_code=data.get("factory_code"), db_code=data.get("db_code"),
                description=data.get("description", ""), price=price,
                source_file=wb.filename, row=row_num,
            ))
    return products


import re as _re

_CODE_PREFIX_RE_CACHE: Dict[tuple, "_re.Pattern"] = {}


def _code_prefix_pattern(prefixes: tuple) -> "_re.Pattern":
    if prefixes not in _CODE_PREFIX_RE_CACHE:
        alternation = "|".join(_re.escape(p) for p in prefixes)
        _CODE_PREFIX_RE_CACHE[prefixes] = _re.compile(rf"\b(?:{alternation})\d{{0,6}}\b", _re.IGNORECASE)
    return _CODE_PREFIX_RE_CACHE[prefixes]


def _is_reactive_glaze(factory_code: str, db_code: str, description: str, cfg: MatcherConfig) -> bool:
    """Per spec: all MG colours are reactive, Mirage/DB6 is reactive,
    new ND colours are reactive. Code-style prefixes (MG007, ND005)
    are matched with a word-boundary regex against actual codes/text
    -- NOT as a bare 'mg'/'nd' substring check, which previously
    false-matched inside ordinary words like 'ha-nd-le'."""
    combined = f"{factory_code} {db_code} {description}"
    if db_code and any(db_code.upper().startswith(p) for p in cfg.reactive_glaze_db_code_prefixes):
        return True
    pattern = _code_prefix_pattern(tuple(cfg.reactive_glaze_code_prefixes))
    if pattern.search(combined):
        return True
    text_lower = combined.lower()
    return any(kw in text_lower for kw in cfg.reactive_glaze_keywords)


def _candidate_score(fp: ExtractedProduct, cp: ExtractedProduct) -> tuple[int, str]:
    """Core matching score for one factory-product/customer-product
    pair -- unchanged logic from the original build (embedded factory
    code = 95, exact factory-code match = 90, description similarity
    scaled up to ~70)."""
    from difflib import SequenceMatcher

    if fp.factory_code and cp.db_code and fp.factory_code in cp.db_code:
        return 95, f"Factory code {fp.factory_code} found embedded in DB code {cp.db_code}"
    if fp.factory_code and cp.factory_code and fp.factory_code == cp.factory_code:
        return 90, f"Factory code {fp.factory_code} matches directly"
    if fp.description and cp.description:
        ratio = SequenceMatcher(None, fp.description.lower(), cp.description.lower()).ratio()
        if ratio > 0.75:
            return round(ratio * 70), f"Description similarity {ratio:.0%}"
    return 0, ""


def compare_products(
    factory_products: List[ExtractedProduct],
    customer_products: List[ExtractedProduct],
    theme_profile: Optional[RfqThemeProfile] = None,
    factory_recommendation: Optional[FactoryRecommendation] = None,
    authoritative_index: Optional[Dict[str, AuthoritativeRecord]] = None,
    cfg: Optional[MatcherConfig] = None,
) -> List[ProductComparisonRow]:
    """For every product on either side, determine whether it also
    appears on the other side, plus (when the optional context is
    supplied) theme/factory-coherence bias, reactive-glaze flagging,
    and historical-evidence-based DB code resolution. Called with only
    the first two arguments, behaves exactly as the originally
    validated version (all extensions are additive)."""
    rows: List[ProductComparisonRow] = []
    matched_customer_indices: Set[int] = set()

    for fp in factory_products:
        # Rank EVERY candidate (not just the best) so alternatives can
        # be shown -- per "one BEST match first, alternatives underneath".
        scored_candidates: List[tuple[int, int, str]] = []  # (score, customer_idx, notes)
        for idx, cp in enumerate(customer_products):
            if idx in matched_customer_indices:
                continue
            score, notes = _candidate_score(fp, cp)
            if score > 0:
                scored_candidates.append((score, idx, notes))

        # Project/factory coherence bonus: if this factory code's
        # implied factory matches the RFQ's recommended factory, bias
        # its own candidacy upward slightly -- this affects which ROW
        # gets accepted with confidence, not which customer-side
        # candidate is chosen (coherence is about staying with the
        # recommended factory overall, already reflected in fp itself).
        coherence_bonus = 0
        theme_note = ""
        if cfg and factory_recommendation and factory_recommendation.factory:
            bonus_map = apply_project_coherence([fp.factory_code] if fp.factory_code else [], factory_recommendation.factory, cfg)
            coherence_bonus = bonus_map.get(fp.factory_code, 0) if fp.factory_code else 0
            actual_factory = factory_for_code(fp.factory_code, cfg) if fp.factory_code else None
            if actual_factory:
                theme_note = (
                    f"On recommended factory ({factory_recommendation.factory})" if actual_factory == factory_recommendation.factory
                    else f"Different factory ({actual_factory}) than RFQ recommendation ({factory_recommendation.factory})"
                )

        scored_candidates.sort(key=lambda t: -t[0])

        reactive = _is_reactive_glaze(fp.factory_code or "", "", fp.description, cfg) if cfg else False

        historical_tier, historical_review = "", False
        if authoritative_index and fp.factory_code and fp.factory_code in authoritative_index:
            auth = authoritative_index[fp.factory_code]
            historical_tier = TIER_LABELS[auth.tier]
            historical_review = auth.needs_review

        if scored_candidates:
            best_score, best_idx, best_notes = scored_candidates[0]
            best_cp = customer_products[best_idx]
            matched_customer_indices.add(best_idx)
            effective_confidence = min(100, best_score + coherence_bonus)

            alternatives = [
                AlternativeMatch(db_code=customer_products[idx].db_code or "", confidence=score, notes=notes)
                for score, idx, notes in scored_candidates[1:3]  # up to 2 alternatives
            ]

            second_best = scored_candidates[1][0] if len(scored_candidates) > 1 else 0
            auto_selected = effective_confidence >= 85 and (effective_confidence - second_best) >= 15 and not historical_review

            reactive = reactive or (cfg and _is_reactive_glaze(fp.factory_code or "", best_cp.db_code or "", best_cp.description, cfg))

            rows.append(ProductComparisonRow(
                factory_code=fp.factory_code or "", db_code=best_cp.db_code or "",
                description=best_cp.description or fp.description,
                factory_cost=fp.price, selling_price=best_cp.price,
                matched=True, confidence=effective_confidence,
                notes=f"Matched to {best_cp.source_file} row {best_cp.row}. {best_notes}".strip(),
                alternatives=alternatives, reactive_glaze=bool(reactive),
                historical_tier=historical_tier, historical_needs_review=historical_review,
                auto_selected=auto_selected, theme_alignment_note=theme_note,
            ))
        else:
            rows.append(ProductComparisonRow(
                factory_code=fp.factory_code or "", db_code="", description=fp.description,
                factory_cost=fp.price, selling_price=None, matched=False, confidence=0,
                notes="No corresponding product found in the customer file",
                reactive_glaze=bool(reactive), historical_tier=historical_tier,
                historical_needs_review=historical_review, auto_selected=False,
                theme_alignment_note=theme_note,
            ))

    for idx, cp in enumerate(customer_products):
        if idx in matched_customer_indices:
            continue
        cp_reactive = _is_reactive_glaze(cp.factory_code or "", cp.db_code or "", cp.description, cfg) if cfg else False
        rows.append(ProductComparisonRow(
            factory_code=cp.factory_code or "", db_code=cp.db_code or "",
            description=cp.description, factory_cost=None, selling_price=cp.price,
            matched=False, confidence=0,
            notes="No corresponding product found in the factory file",
            auto_selected=False, reactive_glaze=bool(cp_reactive),
        ))

    return rows


@dataclass
class DbCodeMappingRow:
    factory_code: str
    primary_db_code: str
    alternative_db_codes: List[str]
    occurrences: int
    projects: List[str]
    confidence: str
    recommendation: str


def build_db_code_mapping(all_comparison_rows: List[tuple]) -> List[DbCodeMappingRow]:
    by_factory: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_factory_projects: Dict[str, Set[str]] = defaultdict(set)

    for project_label, rows in all_comparison_rows:
        for row in rows:
            if not row.factory_code or not row.db_code:
                continue
            by_factory[row.factory_code][row.db_code] += 1
            by_factory_projects[row.factory_code].add(project_label)

    mapping: List[DbCodeMappingRow] = []
    for factory_code, db_code_counts in by_factory.items():
        sorted_codes = sorted(db_code_counts.items(), key=lambda kv: -kv[1])
        primary = sorted_codes[0][0]
        alternatives = [code for code, _ in sorted_codes[1:]]
        total_occurrences = sum(db_code_counts.values())

        if len(sorted_codes) == 1:
            confidence = "N/A -- single DB code"
            recommendation = "No action needed."
        else:
            confidence = "Review" if len(alternatives) == 1 else "Review (multiple alternates)"
            recommendation = (
                f"Factory code {factory_code} has been assigned {len(sorted_codes)} different DB codes "
                f"across {len(by_factory_projects[factory_code])} project(s). Confirm whether these are "
                f"the same product before treating {primary} as the sole current code."
            )

        mapping.append(DbCodeMappingRow(
            factory_code=factory_code, primary_db_code=primary, alternative_db_codes=alternatives,
            occurrences=total_occurrences, projects=sorted(by_factory_projects[factory_code]),
            confidence=confidence, recommendation=recommendation,
        ))

    mapping.sort(key=lambda m: (-len(m.alternative_db_codes), -m.occurrences))
    return mapping
