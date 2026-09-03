"""
config.py
---------
Tunable settings for the 2025 Project Matching Assistant, saved to
matcher_config.json next to the executable. Business rules that don't
have a hard "right answer" yet (colour keyword lists, factory
preferences, currency conversion) live here, not hardcoded in the
matching logic -- per the instruction to make undefined things
configurable rather than blocking development.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("matcher_config.json")


@dataclass
class MatcherConfig:
    # --- File-level project matching (unchanged) ---
    confirmed_threshold: int = 90
    review_threshold: int = 70
    product_match_threshold: int = 60
    supported_extensions: List[str] = field(default_factory=lambda: [".xlsx", ".xlsm", ".xls"])

    # --- RFQ-level theme analysis ---
    # Colour family -> keywords that indicate it (English; extend with
    # Vietnamese/Chinese terms as they're observed in real files).
    colour_family_keywords: Dict[str, List[str]] = field(default_factory=lambda: {
        "green": ["green", "sage", "olive", "moss", "jade"],
        "blue": ["blue", "indigo", "duck egg", "cobalt", "navy"],
        "brown_rustic": ["brown", "clay", "rustic", "earthy", "terracotta", "craftstone",
                          "night and day", "stoneware", "sesame", "seasame", "sand stone"],
        "beige_sandy": ["beige", "sandy", "sand", "khaki", "taupe", "cream"],
        "grey": ["grey", "gray", "charcoal", "slate"],
        "dark": ["black", "matt black", "dark", "ebony"],
        "white": ["white", "ivory", "pearl", "porcelain white"],
        "mustard": ["mustard", "gold rim", "ginger"],
    })
    rustic_keywords: List[str] = field(default_factory=lambda: [
        "rustic", "stoneware", "craftstone", "night and day", "earthy", "brown clay",
        "reactive", "kiln-transformed", "kiln transformed", "窑变",
    ])
    pattern_keywords: List[str] = field(default_factory=lambda: [
        "pattern", "decal", "line", "stripe", "floral", "print", "pad print", "花纸",
        "diamond", "spiral", "旋纹", "花边",
    ])
    plain_white_keywords: List[str] = field(default_factory=lambda: [
        "plain white", "classic white", "white porcelain", "whiteware", "白瓷",
    ])
    # Below this fraction of rows sharing one colour family, the RFQ is
    # NOT considered a coordinated single-colour collection.
    coordinated_collection_min_share: float = 0.6

    # --- Factory rules (spec: brown-clay -> YIXIN, other strong colour
    # -> HUAXIN, whiteware -> JIAXIANG) ---
    factory_for_theme: Dict[str, str] = field(default_factory=lambda: {
        "rustic_brown_clay": "YIXIN",
        "colour_driven": "HUAXIN",
        "whiteware": "JIAXIANG",
        "patterned": "HUAXIN",  # decal/pattern work commonly sourced alongside colour capability; adjust as needed
        "mixed": "",  # no single strong recommendation -- left for manual judgement
    })
    # Filename/text markers already used elsewhere in this project to
    # detect which factory actually produced a given file (JX -> a
    # Jiaxiang-family code, HX -> Huaxin, YX -> Yixin) -- kept
    # consistent with matcher naming already validated on real files.
    factory_code_prefix_hints: Dict[str, str] = field(default_factory=lambda: {
        "JX": "JIAXIANG", "HX": "HUAXIN", "YX": "YIXIN", "H": "HUAXIN",
    })

    # --- Reactive glaze (spec: MG colours, Mirage/DB6, new ND colours) ---
    reactive_glaze_db_code_prefixes: List[str] = field(default_factory=lambda: ["DB6"])
    # Short code-style prefixes (MG007, ND005, etc) checked with a
    # WORD-BOUNDARY regex in product_comparison.py, never as bare
    # substrings -- a bare "nd"/"mg" substring check previously
    # false-matched inside ordinary words like "ha-nd-le", flagging
    # plain black/white plates as reactive glaze. Full phrases below
    # remain safe as substrings since they're long/specific enough.
    reactive_glaze_code_prefixes: List[str] = field(default_factory=lambda: ["MG", "ND"])
    reactive_glaze_keywords: List[str] = field(default_factory=lambda: [
        "mirage", "night and day", "reactive", "窑变",
    ])
    reactive_glaze_disclaimer: str = (
        "This item uses a reactive glaze. Colour, pattern, and finish vary piece to piece "
        "and kiln firing to kiln firing -- the photo/sample shown is representative, not exact."
    )

    # --- Historical evidence (spec: SOLD > QUOTED > FACTORY QUOTED > CATALOGUE) ---
    sales_report_path: str = ""  # set once the Mimosa Sales Invoice Report file is available
    index_db_path: str = ""  # persistent Update Master index (local_index.py); defaults to next to the .exe if unset
    # Column-name mapping for the sales report. Updated to match the
    # REAL headers found in the actual uploaded Sales_Invoice_Report
    # file (validated, not guessed): there is NO separate factory-code
    # column in this report -- only "Product | Material Code" (the DB
    # code). See historical_evidence.py / orchestrator.py for how a
    # factory code is backfilled onto SOLD records by cross-referencing
    # the DB code against QUOTED/FACTORY QUOTED records from the
    # project files themselves.
    sales_report_column_map: Dict[str, str] = field(default_factory=lambda: {
        "db_code": "Product | Material Code",
        "description": "Product | Material Brief Description",
        "quantity": "Sales Invoice (Product) Quantity",
        "unit_price": "Sales Invoice (Product) Unit Price",
        "date": "Sales Invoice Date",
        "customer": "Customer Code",
        "invoice_no": "Sales Invoice No.",
        "amount": "Sales Invoice (Product) Amount",
        "currency": "Sales Invoice (Product) Currency Symbol",
    })
    currency_conversion_rmb_per_usd: float = 7.2  # configurable, per instruction example

    # --- Workbook provenance (content-based, since folder location
    # alone is confirmed unreliable -- a PO/negotiation working file
    # can be saved in the factory folder after VLOOKUP'ing old RMB
    # costs into it for margin checking). See workbook_provenance.py. ---
    po_negotiation_keywords: List[str] = field(default_factory=lambda: [
        "purchase order", "po no", "po number", "deposit", "balance payment",
        "final negotiation", "confirmed qty", "confirmed quantity", "negotiation",
    ])
    factory_proposal_keywords: List[str] = field(default_factory=lambda: [
        "part no", "part number", "factory code", "出厂价", "厂价", "工厂",
    ])

    # --- Security ---
    # Columns that must NEVER appear in a customer-facing export.
    internal_only_columns: List[str] = field(default_factory=lambda: [
        "Factory Cost", "Factory Cost (RMB)", "Margin", "Bank", "Bank Account",
        "Internal Notes", "Factory File",
    ])

    def rebalance(self) -> None:
        """No-op placeholder retained for forward compatibility if
        weight re-normalization is ever needed; currently all weights
        are independent point contributions, not required to sum to 1."""
        return


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> MatcherConfig:
    path = Path(path)
    if not path.exists():
        cfg = MatcherConfig()
        save_config(cfg, path)
        return cfg
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        valid_fields = {f.name for f in fields(MatcherConfig)}
        raw = {k: v for k, v in raw.items() if k in valid_fields}
        return MatcherConfig(**raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load %s (%s); using defaults", path, exc)
        return MatcherConfig()


def save_config(cfg: MatcherConfig, path: Path | str = DEFAULT_CONFIG_PATH) -> None:
    Path(path).write_text(json.dumps(asdict(cfg), indent=2, ensure_ascii=False), encoding="utf-8")
