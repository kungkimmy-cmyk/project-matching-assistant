"""
workbook_provenance.py
------------------------
Per the real workflow described: folder location is only a CLUE about
what a workbook actually is, never proof. A customer PO/negotiation
working file can sit in the "factory" folder (because RMB costs were
VLOOKUP'd into it for margin checking) and a genuine factory proposal
can arrive back inside what started as the customer's own template.

This module adds a CONTENT-based signal on top of (never instead of)
the existing folder-based classification, specifically to catch the
one scenario that matters most for evidence quality: a workbook that
LOOKS like a true factory quotation (has RMB costs) but is actually an
internal PO/negotiation working file where those costs were copied in
for reference, not produced by the factory in that document.

This is explicitly a first-pass heuristic, not a definitive classifier
-- keyword-based, same honesty standard as theme_analysis.py's colour
classification: validated against the scenarios described, not yet
against a large real corpus. Every keyword list is configurable
(matcher_config.json), not hardcoded, so it can be tuned once more
real files are seen.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .file_reader import WorkbookSummary


@dataclass
class ProvenanceAssessment:
    label: str  # 'likely_po_or_negotiation_working_file' | 'likely_factory_proposal' | 'unclear'
    matched_po_keywords: List[str]
    matched_factory_keywords: List[str]
    note: str


def _find_matches(text_lower: str, keywords: List[str]) -> List[str]:
    return [kw for kw in keywords if kw.lower() in text_lower]


def assess_provenance(wb: WorkbookSummary, cfg) -> ProvenanceAssessment:
    """cfg needs po_negotiation_keywords and factory_proposal_keywords
    (see config.py). PO/negotiation language takes priority when both
    are present -- a workbook can genuinely contain a factory's
    original proposal AND later PO negotiation notes added on top; in
    that case the SAFER assumption is that any cost figures may have
    been touched/copied during negotiation, not that they're still a
    pristine factory quote."""
    text_lower = wb.all_text().lower()

    po_matches = _find_matches(text_lower, cfg.po_negotiation_keywords)
    factory_matches = _find_matches(text_lower, cfg.factory_proposal_keywords)

    if po_matches:
        return ProvenanceAssessment(
            label="likely_po_or_negotiation_working_file",
            matched_po_keywords=po_matches, matched_factory_keywords=factory_matches,
            note=(
                f"Contains PO/negotiation language ({', '.join(po_matches[:3])}) -- any RMB cost figures in "
                f"this workbook may be copied/VLOOKUP'd from an earlier quotation for margin reference, not "
                f"produced by the factory in this document. Treat cost evidence here as internal reference, "
                f"not a verified factory quotation, regardless of which folder this file was found in."
            ),
        )
    if factory_matches:
        return ProvenanceAssessment(
            label="likely_factory_proposal",
            matched_po_keywords=[], matched_factory_keywords=factory_matches,
            note=f"Contains factory-proposal language ({', '.join(factory_matches[:3])}) with no PO/negotiation markers found.",
        )
    return ProvenanceAssessment(
        label="unclear", matched_po_keywords=[], matched_factory_keywords=[],
        note="No strong content signal either way -- falling back to folder-based classification.",
    )
