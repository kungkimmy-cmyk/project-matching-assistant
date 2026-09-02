"""
photo_extractor.py
--------------------
Phase 1 of visual/photo matching, per explicit instruction: NOT full
pixel-level image-to-image similarity matching yet. What this DOES do,
now:

  1. Extracts every embedded image from a workbook (file_reader.py
     already anchors each one to a row/col via openpyxl's drawing
     anchors).
  2. Associates each image with the nearest data row (a photo is
     almost always placed in the same or adjacent row as the product
     it illustrates).
  3. Stores the image bytes to a local photo store, keyed by a stable
     id, so they don't need to be re-extracted from the workbook every
     run.
  4. Extracts a COARSE colour hint (dominant colour, quantized to the
     same colour-family vocabulary theme_analysis.py uses) via a cheap
     average/histogram approach -- genuinely useful signal ("this photo
     is mostly green"), but explicitly NOT claimed to be shape/pattern/
     texture similarity matching.

THE SEAM FOR PHASE 2 (real visual similarity matching): every stored
photo is keyed by product_id/source_file/row in photo_index.json,
with the raw image bytes on disk. A future similarity-matching module
can read this same index and add embeddings/feature vectors without
this module, or anything upstream of it, needing to change. Do not
delete or restructure photo_index.json's schema without accounting for
that.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional

from .file_reader import WorkbookSummary, ImageRef
from .config import MatcherConfig

logger = logging.getLogger(__name__)

# Coarse colour buckets for average-colour classification -- deliberately
# the SAME family vocabulary as theme_analysis.py's colour_family_keywords,
# so a photo's colour hint and a description's colour keyword can agree
# or disagree on equal footing.
_COLOUR_BUCKETS = {
    "green": (0, 200, 0), "blue": (0, 80, 220), "brown_rustic": (120, 80, 50),
    "beige_sandy": (210, 190, 150), "grey": (130, 130, 130), "dark": (30, 30, 30),
    "white": (245, 245, 245), "mustard": (200, 160, 40),
}


@dataclass
class PhotoRecord:
    photo_id: str
    source_file: str
    sheet_name: str
    anchor_row: int
    anchor_col: int
    stored_path: str
    colour_hint: Optional[str] = None
    colour_hint_confidence: float = 0.0


@dataclass
class PhotoStore:
    """Thin wrapper around the on-disk photo store + its JSON index."""
    store_dir: Path
    index: Dict[str, PhotoRecord] = field(default_factory=dict)

    @property
    def index_path(self) -> Path:
        return self.store_dir / "photo_index.json"

    def load(self) -> None:
        if self.index_path.exists():
            try:
                raw = json.loads(self.index_path.read_text(encoding="utf-8"))
                self.index = {k: PhotoRecord(**v) for k, v in raw.items()}
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not load photo index (%s); starting fresh.", exc)
                self.index = {}

    def save(self) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(
            json.dumps({k: asdict(v) for k, v in self.index.items()}, indent=2), encoding="utf-8"
        )

    def photos_for_row(self, source_file: str, sheet_name: str, row: int) -> List[PhotoRecord]:
        return [
            p for p in self.index.values()
            if p.source_file == source_file and p.sheet_name == sheet_name and p.anchor_row == row
        ]


def _classify_colour(image_bytes: bytes) -> tuple[Optional[str], float]:
    """Cheap average-colour classification -- resize to a tiny
    thumbnail and average the pixels, then find the nearest colour
    bucket. This is intentionally simple: good enough to say 'this
    photo leans green' or 'this photo is mostly white/neutral', not
    good enough to distinguish two different green glazes from each
    other. That distinction is Phase 2 (see module docstring)."""
    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img = img.resize((16, 16))
        pixels = list(img.getdata())
        avg = tuple(sum(c[i] for c in pixels) / len(pixels) for i in range(3))

        best_family, best_dist = None, float("inf")
        for family, ref_rgb in _COLOUR_BUCKETS.items():
            dist = sum((avg[i] - ref_rgb[i]) ** 2 for i in range(3)) ** 0.5
            if dist < best_dist:
                best_family, best_dist = family, dist
        # Confidence: closer match to a bucket = higher; deliberately
        # conservative since this is average-colour, not dominant-colour.
        max_possible_dist = (255 ** 2 * 3) ** 0.5
        confidence = max(0.0, 1.0 - (best_dist / max_possible_dist)) * 0.7  # capped below 0.7 -- coarse signal only
        return best_family, round(confidence, 2)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not classify photo colour: %s", exc)
        return None, 0.0


def extract_photos(wb: WorkbookSummary, cfg: MatcherConfig, store: PhotoStore) -> List[PhotoRecord]:
    """Extracts every embedded image in the workbook, stores it, and
    returns the new PhotoRecords (also added to store.index). Safe to
    call repeatedly across a run -- re-extracting the same workbook
    produces the same photo_ids (content-hash based), so store.save()
    naturally dedupes rather than growing unbounded."""
    records: List[PhotoRecord] = []
    for sheet in wb.sheets:
        for img in sheet.images:
            try:
                photo_id = hashlib.sha256(img.image_bytes).hexdigest()[:16]
                stored_path = store.store_dir / f"{photo_id}.{img.format}"
                if photo_id not in store.index:
                    store.store_dir.mkdir(parents=True, exist_ok=True)
                    stored_path.write_bytes(img.image_bytes)
                    colour_hint, confidence = _classify_colour(img.image_bytes)
                    record = PhotoRecord(
                        photo_id=photo_id, source_file=wb.filename, sheet_name=sheet.name,
                        anchor_row=img.anchor_row, anchor_col=img.anchor_col,
                        stored_path=str(stored_path), colour_hint=colour_hint,
                        colour_hint_confidence=confidence,
                    )
                    store.index[photo_id] = record
                else:
                    record = store.index[photo_id]
                records.append(record)
            except Exception as exc:  # noqa: BLE001 - one bad image must not abort the whole extraction
                logger.warning("Could not extract one image from %s: %s", wb.filename, exc)
    return records
