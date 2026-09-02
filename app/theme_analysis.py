"""
theme_analysis.py
------------------
Implements the top of the new matching hierarchy:

    RFQ-LEVEL VISUAL THEME / COLOUR -> COLLECTION CHARACTER -> ...

Before any individual row is matched to a factory or DB code, the RFQ
as a WHOLE is classified: is this customer primarily looking for a
specific colour/theme, a rustic/brown-clay collection, plain
whiteware, a pattern/decal line, or a mixed/general requirement?

This is keyword-based (descriptions, colour-swatch labels, and --
where available -- coarse colour hints from embedded photos via
photo_extractor.py), not full computer vision. It's deliberately built
to be the STRONGEST reliable version of this achievable right now,
with an explicit, unhidden seam where real image-similarity matching
can be added later (see photo_extractor.py's module docstring).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .config import MatcherConfig


@dataclass
class RowTheme:
    row: int
    source_file: str
    colour_family: Optional[str]
    is_rustic: bool
    is_patterned: bool
    is_plain_white: bool
    matched_keywords: List[str] = field(default_factory=list)
    photo_colour_hint: Optional[str] = None  # filled in later if photo_extractor found a coarse colour


@dataclass
class RfqThemeProfile:
    dominant_theme: str  # 'rustic_brown_clay' | 'colour_driven' | 'whiteware' | 'patterned' | 'mixed'
    dominant_colour_family: Optional[str]
    theme_confidence: int  # 0-100
    coordinated_collection: bool
    coordinated_share: float  # fraction of rows sharing the dominant colour/theme
    row_themes: List[RowTheme]
    reasoning: List[str]
    recommended_factory: str  # from factory_rules-style mapping, filled in by factory_rules.py normally;
                               # left here too so callers that only need the theme still get a starting point


def _row_text(description: str) -> str:
    return description.lower() if description else ""


def classify_row(row: int, source_file: str, description: str, cfg: MatcherConfig) -> RowTheme:
    text = _row_text(description)
    matched: List[str] = []

    colour_family = None
    best_hits = 0
    for family, keywords in cfg.colour_family_keywords.items():
        hits = sum(1 for kw in keywords if kw in text)
        if hits > best_hits:
            best_hits = hits
            colour_family = family
            matched = [kw for kw in keywords if kw in text]

    is_rustic = any(kw in text for kw in cfg.rustic_keywords)
    is_patterned = any(kw in text for kw in cfg.pattern_keywords)
    is_plain_white = any(kw in text for kw in cfg.plain_white_keywords) or (
        colour_family == "white" and not is_patterned and not is_rustic
    )
    if is_rustic:
        matched += [kw for kw in cfg.rustic_keywords if kw in text]
    if is_patterned:
        matched += [kw for kw in cfg.pattern_keywords if kw in text]

    return RowTheme(
        row=row, source_file=source_file, colour_family=colour_family,
        is_rustic=is_rustic, is_patterned=is_patterned, is_plain_white=is_plain_white,
        matched_keywords=sorted(set(matched)),
    )


def _extract_db_code_suffix(db_code: Optional[str]) -> Optional[str]:
    """DB codes in this dataset often carry a colour/finish suffix
    after the first hyphen (e.g. 'DB8183021-ND008', 'DB30H2583-N591',
    'DB6211135-MG005') -- a much more reliable coordinated-collection
    signal than free-text keywords, since not every row's description
    repeats the colour word (a row might just say 'BREAD PLATE 6" -
    DAYBREAK', with the colour only encoded in its DB code's suffix).
    Returns None for codes with no hyphen, or where the "suffix" is
    just a numeric variant (e.g. 'DB1001-2') rather than a colour code."""
    if not db_code or "-" not in db_code:
        return None
    suffix = db_code.split("-", 1)[1].strip().upper()
    if not suffix or suffix.isdigit():
        return None
    return suffix


def extract_theme_rows(wb) -> List[tuple]:
    """Every descriptive row in a workbook, WITH or WITHOUT a DB/
    factory code -- unlike product_comparison.extract_products()
    (which correctly requires a code, since it's matching specific
    products), theme analysis needs to see the RFQ as a whole,
    including rows that haven't been assigned a code yet. Validated
    against a real file where the theme-defining language ("Rustic
    colour stone ware...") appeared entirely on rows with no code at
    all -- the original incoming template, before any DB code was
    assigned. Returns (row, filename, description, db_code_or_None)
    tuples, ready for analyze_rfq_theme()."""
    from .project_matcher import DB_CODE_RE

    rows: List[tuple] = []
    for sheet in wb.sheets:
        best_text_per_row: Dict[int, str] = {}
        db_code_per_row: Dict[int, str] = {}
        for cell in sheet.cells.values():
            if not isinstance(cell.value, str):
                continue
            text = cell.value.strip()
            if len(text) > 8:
                existing = best_text_per_row.get(cell.row, "")
                if len(text) > len(existing):
                    best_text_per_row[cell.row] = text
            match = DB_CODE_RE.search(text)
            if match and cell.row not in db_code_per_row:
                db_code_per_row[cell.row] = match.group(0).upper()
        for row_num, desc in best_text_per_row.items():
            rows.append((row_num, wb.filename, desc, db_code_per_row.get(row_num)))
    return rows


def analyze_rfq_theme(
    rows: Sequence[tuple],  # (row_number, source_file, description) OR (row_number, source_file, description, db_code)
    cfg: MatcherConfig,
) -> RfqThemeProfile:
    """rows: sequence of (row_number, source_file, description) tuples,
    or 4-tuples with a db_code as the last element -- when db_codes are
    supplied, a shared DB-code colour/finish suffix across most rows is
    treated as strong direct evidence of a coordinated collection (see
    _extract_db_code_suffix), checked BEFORE falling back to free-text
    keyword classification. Deliberately generic (not tied to
    product_comparison.ExtractedProduct) so this module has no
    dependency on that one."""
    normalized_rows = []
    db_codes: List[Optional[str]] = []
    for entry in rows:
        if len(entry) >= 4:
            r, f, d, db_code = entry[0], entry[1], entry[2], entry[3]
        else:
            r, f, d = entry
            db_code = None
        normalized_rows.append((r, f, d))
        db_codes.append(db_code)

    row_themes = [classify_row(r, f, d, cfg) for (r, f, d) in normalized_rows]
    reasoning: List[str] = []
    total = len(row_themes)
    if total == 0:
        return RfqThemeProfile(
            dominant_theme="mixed", dominant_colour_family=None, theme_confidence=0,
            coordinated_collection=False, coordinated_share=0.0, row_themes=[],
            reasoning=["No rows to analyze."], recommended_factory="",
        )

    # --- DB-code-suffix evidence (checked first: stronger than free text) ---
    suffix_counts: Dict[str, int] = {}
    codes_with_suffix = 0
    for code in db_codes:
        suffix = _extract_db_code_suffix(code)
        if suffix:
            codes_with_suffix += 1
            suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1

    if suffix_counts and codes_with_suffix >= max(2, total * 0.3):
        top_suffix, top_count = max(suffix_counts.items(), key=lambda kv: kv[1])
        suffix_share = top_count / total
        if suffix_share >= cfg.coordinated_collection_min_share:
            # Try to resolve the suffix to a known colour family via the
            # same keyword vocabulary (e.g. 'MIRAGE GREEN' -> green);
            # if it doesn't resolve, still report the coordination with
            # the raw code as the identifier -- never invented, always
            # traceable back to what's actually in the data.
            suffix_lower = top_suffix.lower()
            resolved_family = None
            for family, keywords in cfg.colour_family_keywords.items():
                if any(kw in suffix_lower for kw in keywords):
                    resolved_family = family
                    break
            reasoning.append(
                f"{top_count}/{total} rows ({suffix_share:.0%}) share the DB-code suffix '-{top_suffix}' "
                f"-- a stronger, structured coordinated-collection signal than free-text description alone "
                f"(only checked when at least 30% of rows have a DB code with a suffix at all)."
            )
            theme_confidence = round(suffix_share * 100)
            recommended_factory = cfg.factory_for_theme.get("colour_driven", "")
            return RfqThemeProfile(
                dominant_theme="colour_driven", dominant_colour_family=resolved_family or f"code:{top_suffix}",
                theme_confidence=theme_confidence, coordinated_collection=True, coordinated_share=suffix_share,
                row_themes=row_themes, reasoning=reasoning, recommended_factory=recommended_factory,
            )

    # --- Fall back to free-text keyword classification ---
    rustic_count = sum(1 for rt in row_themes if rt.is_rustic)
    patterned_count = sum(1 for rt in row_themes if rt.is_patterned)
    plain_white_count = sum(1 for rt in row_themes if rt.is_plain_white)
    colour_counts = Counter(rt.colour_family for rt in row_themes if rt.colour_family and rt.colour_family != "white")

    rustic_share = rustic_count / total
    patterned_share = patterned_count / total
    plain_white_share = plain_white_count / total

    dominant_colour_family, colour_family_share = None, 0.0
    if colour_counts:
        dominant_colour_family, top_n = colour_counts.most_common(1)[0]
        colour_family_share = top_n / total

    if rustic_share >= cfg.coordinated_collection_min_share:
        dominant_theme = "rustic_brown_clay"
        theme_confidence = round(rustic_share * 100)
        reasoning.append(f"{rustic_count}/{total} rows ({rustic_share:.0%}) show rustic/brown-clay/stoneware language.")
    elif colour_family_share >= cfg.coordinated_collection_min_share:
        dominant_theme = "colour_driven"
        theme_confidence = round(colour_family_share * 100)
        reasoning.append(f"{colour_counts[dominant_colour_family]}/{total} rows ({colour_family_share:.0%}) share the '{dominant_colour_family}' colour family.")
    elif plain_white_share >= cfg.coordinated_collection_min_share:
        dominant_theme = "whiteware"
        theme_confidence = round(plain_white_share * 100)
        reasoning.append(f"{plain_white_count}/{total} rows ({plain_white_share:.0%}) are plain white with no colour/pattern language.")
    elif patterned_share >= cfg.coordinated_collection_min_share:
        dominant_theme = "patterned"
        theme_confidence = round(patterned_share * 100)
        reasoning.append(f"{patterned_count}/{total} rows ({patterned_share:.0%}) show pattern/decal/line language.")
    else:
        dominant_theme = "mixed"
        best_share = max(rustic_share, colour_family_share, plain_white_share, patterned_share)
        theme_confidence = round(100 - (cfg.coordinated_collection_min_share - best_share) * 100) if best_share else 40
        theme_confidence = max(30, min(theme_confidence, 65))
        reasoning.append(
            f"No single theme reaches the {cfg.coordinated_collection_min_share:.0%} coordination threshold "
            f"(strongest: rustic {rustic_share:.0%}, colour {colour_family_share:.0%}, "
            f"white {plain_white_share:.0%}, pattern {patterned_share:.0%}) -- treated as a mixed/general RFQ."
        )

    coordinated_share = {
        "rustic_brown_clay": rustic_share, "colour_driven": colour_family_share,
        "whiteware": plain_white_share, "patterned": patterned_share, "mixed": 0.0,
    }[dominant_theme]
    coordinated_collection = coordinated_share >= cfg.coordinated_collection_min_share
    recommended_factory = cfg.factory_for_theme.get(dominant_theme, "")

    return RfqThemeProfile(
        dominant_theme=dominant_theme, dominant_colour_family=dominant_colour_family,
        theme_confidence=theme_confidence, coordinated_collection=coordinated_collection,
        coordinated_share=coordinated_share, row_themes=row_themes, reasoning=reasoning,
        recommended_factory=recommended_factory,
    )
