"""
factory_rules.py
-----------------
Implements the FACTORY CAPABILITY -> PROJECT/FACTORY COHERENCE layers
of the matching hierarchy. Consumes theme_analysis.py's output.

Two responsibilities:

1. recommend_factory() -- given the RFQ's theme profile, what factory
   direction should be considered first (YIXIN / HUAXIN / JIAXIANG /
   none), per the business rules given directly:
     - Brown clay/rustic -> YIXIN
     - Other strong colour themes -> HUAXIN (broadest colour capability)
     - Plain whiteware -> JIAXIANG
     - Mixed/general -> no single strong recommendation

2. apply_project_coherence() -- once a recommended factory exists, bias
   individual row matches toward staying on that SAME factory rather
   than picking a different factory per row just because one row's
   shape happens to line up slightly better elsewhere. This does NOT
   ignore dimensions/capacity for individual rows -- it changes which
   candidate is preferred when candidates are otherwise close, per the
   explicit instruction: "this does NOT mean dimensions should be
   ignored... after the overall theme/factory direction has been
   determined."
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .config import MatcherConfig
from .theme_analysis import RfqThemeProfile


@dataclass
class FactoryRecommendation:
    factory: str  # 'YIXIN' | 'HUAXIN' | 'JIAXIANG' | ''
    confidence: int
    reasoning: str


def recommend_factory(theme: RfqThemeProfile, cfg: MatcherConfig) -> FactoryRecommendation:
    factory = cfg.factory_for_theme.get(theme.dominant_theme, "")
    if not factory:
        return FactoryRecommendation(
            factory="", confidence=0,
            reasoning="RFQ theme is mixed/general -- no single factory direction is strongly indicated; "
                      "individual product matching should proceed on a row-by-row basis.",
        )

    reasoning_by_theme = {
        "rustic_brown_clay": (
            f"RFQ shows a rustic/brown-clay/stoneware aesthetic across {theme.coordinated_share:.0%} of rows "
            f"(Night & Day / Craftstone-type character) -- YIXIN is the primary factory direction for this "
            f"aesthetic, per business rule."
        ),
        "colour_driven": (
            f"RFQ is strongly colour-themed ('{theme.dominant_colour_family}') across {theme.coordinated_share:.0%} "
            f"of rows -- HUAXIN is preferred first for its broad colour capability across shapes/sizes."
        ),
        "whiteware": (
            f"RFQ is plain white/classic whiteware across {theme.coordinated_share:.0%} of rows -- "
            f"JIAXIANG is the normal preference for ordinary whiteware."
        ),
        "patterned": (
            f"RFQ shows pattern/decal/line character across {theme.coordinated_share:.0%} of rows -- "
            f"routed to HUAXIN alongside colour-driven work (configurable in matcher_config.json "
            f"if a different factory should be preferred for pattern work)."
        ),
    }
    return FactoryRecommendation(
        factory=factory, confidence=theme.theme_confidence,
        reasoning=reasoning_by_theme.get(theme.dominant_theme, f"Theme '{theme.dominant_theme}' maps to {factory}."),
    )


def factory_for_code(code: Optional[str], cfg: MatcherConfig) -> Optional[str]:
    """Best-effort: which factory family a factory/DB code likely came
    from, using the same prefix conventions already validated
    elsewhere in this project (JX/HX/YX). Used to check whether an
    individual product match would keep the project on the
    recommended factory or split it across factories."""
    if not code:
        return None
    upper = code.upper()
    for prefix in sorted(cfg.factory_code_prefix_hints, key=len, reverse=True):  # longer prefixes first (JX before J)
        if upper.startswith(prefix):
            return cfg.factory_code_prefix_hints[prefix]
    return None


def apply_project_coherence(
    candidate_factory_codes: List[str],
    recommended_factory: str,
    cfg: MatcherConfig,
) -> Dict[str, int]:
    """Given several candidate factory codes for one product row, returns
    a bonus score per candidate: candidates that belong to the
    RECOMMENDED factory get a coherence bonus, so that -- all else
    close -- the row stays on the same factory as the rest of the
    coordinated collection, rather than each row independently drifting
    to whichever factory has a marginally closer individual shape.
    Never the ONLY signal used (see product_comparison.py): this is a
    bias applied alongside, not instead of, dimension/description
    matching."""
    if not recommended_factory:
        return {code: 0 for code in candidate_factory_codes}
    bonuses = {}
    for code in candidate_factory_codes:
        factory = factory_for_code(code, cfg)
        bonuses[code] = 15 if factory == recommended_factory else 0
    return bonuses
