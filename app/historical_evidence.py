"""
historical_evidence.py
------------------------
Implements the historical-evidence hierarchy exactly as specified:

    SOLD > QUOTED > FACTORY QUOTED > CATALOGUE

Tie-break rules (applied literally, not "pick the newest record"):
  - A SOLD DB code beats a QUOTED-only DB code, always.
  - If a factory code was sold under more than one DB code, prefer the
    most recent sale; if dates are missing/tied, prefer the larger
    quantity.
  - If nothing was sold but exactly one DB code was ever quoted,
    retain that established code.
  - If nothing was sold and MULTIPLE different DB codes were quoted
    for the same factory code (a real conflict, not just re-quoting
    the same code), the earliest/established one is kept as the
    resolved value, but the record is flagged needs_review=True --
    this is the "do not silently overwrite historical customer-facing
    DB codes" rule: ambiguity is surfaced, never silently resolved by
    picking whichever happens to be newest.

CATALOGUE is the lowest tier and is architecturally reserved (no
catalogue data source exists yet) -- it will slot in as another
HistoricalRecord source without changing this module once one exists.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional


class EvidenceTier(IntEnum):
    CATALOGUE = 1
    FACTORY_QUOTED = 2
    QUOTED = 3
    SOLD = 4


TIER_LABELS = {
    EvidenceTier.SOLD: "SOLD",
    EvidenceTier.QUOTED: "QUOTED",
    EvidenceTier.FACTORY_QUOTED: "FACTORY QUOTED",
    EvidenceTier.CATALOGUE: "CATALOGUE",
}


@dataclass
class HistoricalRecord:
    factory_code: str
    db_code: str
    tier: EvidenceTier
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    date: Optional[str] = None  # ISO date string, sortable; None sorts last
    source: str = ""
    provenance: str = ""  # e.g. 'likely_po_or_negotiation_working_file' -- see workbook_provenance.py


@dataclass
class AuthoritativeRecord:
    factory_code: str
    resolved_db_code: str
    tier: EvidenceTier
    needs_review: bool
    reasoning: str
    all_records: List[HistoricalRecord] = field(default_factory=list)


def _sort_key_for_sold_tiebreak(r: HistoricalRecord):
    # Most recent date first; missing dates sort last. Larger quantity
    # as the secondary tiebreak when dates are equal/missing.
    return (r.date or "", r.quantity or 0)


def _sort_key_for_established(r: HistoricalRecord):
    # Earliest date first (the originally established record);
    # missing dates sort last so a dated record always wins over an
    # undated one.
    return (r.date is None, r.date or "")


def resolve_authoritative_db_code(records: List[HistoricalRecord]) -> Optional[AuthoritativeRecord]:
    """records: every historical record for ONE factory code, any
    tier, any source. Returns None if given no records."""
    if not records:
        return None

    factory_code = records[0].factory_code
    highest_tier = max(r.tier for r in records)
    top_tier_records = [r for r in records if r.tier == highest_tier]
    distinct_db_codes = sorted({r.db_code for r in top_tier_records})

    if len(distinct_db_codes) == 1:
        resolved = distinct_db_codes[0]
        reasoning = (
            f"Single {TIER_LABELS[highest_tier]} record for this factory code -- "
            f"{resolved} retained as the established DB code."
        )
        return AuthoritativeRecord(
            factory_code=factory_code, resolved_db_code=resolved, tier=highest_tier,
            needs_review=False, reasoning=reasoning, all_records=records,
        )

    # Multiple distinct DB codes at the highest tier -- apply the
    # specified tie-break, and ALWAYS flag for review since this is a
    # genuine historical conflict, never silently resolved.
    if highest_tier == EvidenceTier.SOLD:
        sorted_records = sorted(top_tier_records, key=_sort_key_for_sold_tiebreak, reverse=True)
        winner = sorted_records[0]
        reasoning = (
            f"{len(distinct_db_codes)} different DB codes were SOLD under factory code {factory_code}: "
            f"{', '.join(distinct_db_codes)}. Resolved to {winner.db_code} "
            f"(most recent sale{' / largest quantity' if not winner.date else ''}: "
            f"{winner.date or 'no date'}, qty {winner.quantity or 'unknown'}). "
            f"Flagged for review since this reflects a real historical conflict, not a single established code."
        )
    else:
        sorted_records = sorted(top_tier_records, key=_sort_key_for_established)
        winner = sorted_records[0]
        reasoning = (
            f"{len(distinct_db_codes)} different DB codes were {TIER_LABELS[highest_tier]} for factory code "
            f"{factory_code} (no SOLD evidence available): {', '.join(distinct_db_codes)}. "
            f"Retained {winner.db_code} as the earliest/established code. "
            f"Flagged for review -- do not silently treat this as resolved."
        )

    return AuthoritativeRecord(
        factory_code=factory_code, resolved_db_code=winner.db_code, tier=highest_tier,
        needs_review=True, reasoning=reasoning, all_records=records,
    )


def build_authoritative_index(records: List[HistoricalRecord]) -> Dict[str, AuthoritativeRecord]:
    """Groups every historical record by factory code and resolves
    each group -- the single index product_comparison.py consults for
    'what is the authoritative DB code for this factory code, and
    should this be flagged for review'."""
    by_factory: Dict[str, List[HistoricalRecord]] = defaultdict(list)
    for r in records:
        if r.factory_code:
            by_factory[r.factory_code].append(r)

    index: Dict[str, AuthoritativeRecord] = {}
    for factory_code, group in by_factory.items():
        resolved = resolve_authoritative_db_code(group)
        if resolved:
            index[factory_code] = resolved
    return index
