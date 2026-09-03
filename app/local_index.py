"""
local_index.py
----------------
Foundational persistent-index layer for daily incremental use (per
the workflow clarification: this is not a one-time migration tool --
new files arrive every day, and reprocessing everything from scratch
every time is neither necessary nor safe for previously-reviewed
decisions).

Three responsibilities, kept deliberately separate from the existing
one-shot orchestrator.run_matching() pipeline so that pipeline is
NEVER put at risk by this addition:

1. FILE TRACKING -- has this exact file (by content hash, not just
   name/path, since a file can be renamed or a folder reorganized)
   already been processed? needs_processing() answers this so a daily
   "Update Master" run can skip anything unchanged.

2. PERSISTENT EVIDENCE STORE -- every HistoricalRecord extracted in
   any run is stored here, permanently, so an incremental run can
   combine NEWLY extracted evidence with everything already known
   without re-reading old files. This is what makes "process only
   new/changed files" possible without losing history.

3. REVIEWED MAPPINGS -- once a human confirms a DB Code Mapping
   decision (e.g. "yes, H11328 really does map to two different DB
   codes for a real reason, not a data error"), that decision is
   remembered, so the same conflict is never silently re-flagged for
   review again on a later run.

INTEGRATION STATUS: this module is implemented and tested standalone.
It is NOT yet wired into orchestrator.run_matching() or the GUI's
Analyse button -- see CHANGELOG.md for exactly what that next step
looks like and why it's being done as its own validated pass rather
than folded into this one. The existing full-rebuild pipeline is
completely unaffected by this file existing.
"""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .historical_evidence import HistoricalRecord, EvidenceTier

DEFAULT_INDEX_PATH = Path("master_index.db")


def hash_file(path: Path | str, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of a file's contents, read in chunks (large workbooks
    don't need to fit in memory at once)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_files (
    file_path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    file_mtime REAL,
    last_processed_at TEXT DEFAULT (datetime('now')),
    rows_extracted INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_processed_files_hash ON processed_files(content_hash);

CREATE TABLE IF NOT EXISTS evidence_store (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    factory_code TEXT NOT NULL,
    db_code TEXT NOT NULL,
    tier INTEGER NOT NULL,
    description TEXT,
    quantity REAL,
    unit_price REAL,
    record_date TEXT,
    source TEXT,
    provenance TEXT,
    source_file_path TEXT,
    stored_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_evidence_store_factory ON evidence_store(factory_code);

CREATE TABLE IF NOT EXISTS reviewed_mappings (
    factory_code TEXT PRIMARY KEY,
    approved_db_code TEXT NOT NULL,
    reviewer_note TEXT,
    reviewed_at TEXT DEFAULT (datetime('now'))
);
"""


@contextmanager
def _connect(index_path: Path | str):
    conn = sqlite3.connect(str(index_path))
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_index(index_path: Path | str = DEFAULT_INDEX_PATH) -> None:
    with _connect(index_path):
        pass


def is_new_file(file_path: Path | str, index_path: Path | str = DEFAULT_INDEX_PATH) -> bool:
    """True if this exact path has never been recorded before at all
    (as opposed to needs_processing(), which is also True for a
    previously-seen path whose content has since changed). Used to
    distinguish 'new file' from 'changed file' in the Update Master
    summary stats."""
    with _connect(index_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_files WHERE file_path = ?", (str(file_path),)
        ).fetchone()
    return row is None


def delete_evidence_for_file(source_file_path: str, index_path: Path | str = DEFAULT_INDEX_PATH) -> int:
    """Removes every evidence row previously stored FROM this exact
    file, before re-storing its freshly re-extracted contribution.
    This is what makes reprocessing a CHANGED file correct rather than
    additive: if a row was removed from the file between versions, its
    old evidence should be removed too, not left orphaned alongside
    the new extraction (which would otherwise silently duplicate
    unchanged rows AND retain stale evidence for removed ones). Returns
    the number of rows deleted, for logging."""
    with _connect(index_path) as conn:
        cur = conn.execute("DELETE FROM evidence_store WHERE source_file_path = ?", (source_file_path,))
        return cur.rowcount


def needs_processing(file_path: Path | str, index_path: Path | str = DEFAULT_INDEX_PATH) -> bool:
    """True if this file has never been processed, or its content has
    changed since it last was (by hash, not by name/mtime alone --
    mtime can change without content changing, e.g. after a file
    copy/move, and relying on it alone would cause unnecessary
    reprocessing; hash is the authoritative check, mtime is stored
    only as a fast pre-filter for a future optimization, not used to
    decide skip/process on its own)."""
    file_path = Path(file_path)
    if not file_path.exists():
        return False
    current_hash = hash_file(file_path)
    with _connect(index_path) as conn:
        row = conn.execute(
            "SELECT content_hash FROM processed_files WHERE file_path = ?", (str(file_path),)
        ).fetchone()
    if row is None:
        return True
    return row[0] != current_hash


def record_processed_file(file_path: Path | str, rows_extracted: int, index_path: Path | str = DEFAULT_INDEX_PATH) -> None:
    file_path = Path(file_path)
    content_hash = hash_file(file_path)
    mtime = file_path.stat().st_mtime if file_path.exists() else None
    with _connect(index_path) as conn:
        conn.execute(
            "INSERT INTO processed_files (file_path, content_hash, file_mtime, last_processed_at, rows_extracted) "
            "VALUES (?, ?, ?, datetime('now'), ?) "
            "ON CONFLICT(file_path) DO UPDATE SET content_hash=excluded.content_hash, "
            "file_mtime=excluded.file_mtime, last_processed_at=datetime('now'), rows_extracted=excluded.rows_extracted",
            (str(file_path), content_hash, mtime, rows_extracted),
        )


def store_evidence(record: HistoricalRecord, source_file_path: str, index_path: Path | str = DEFAULT_INDEX_PATH) -> None:
    """Append-only, same discipline as the extractor project's
    quotation_history: never updates or deletes an existing row."""
    with _connect(index_path) as conn:
        conn.execute(
            "INSERT INTO evidence_store (factory_code, db_code, tier, description, quantity, unit_price, "
            "record_date, source, provenance, source_file_path) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (record.factory_code, record.db_code, int(record.tier), record.description, record.quantity,
             record.unit_price, record.date, record.source, record.provenance, source_file_path),
        )


def load_all_evidence(index_path: Path | str = DEFAULT_INDEX_PATH) -> List[HistoricalRecord]:
    """Reconstructs every HistoricalRecord ever stored -- what an
    incremental run combines with newly-extracted evidence from
    changed files, without needing to re-read anything unchanged."""
    with _connect(index_path) as conn:
        rows = conn.execute(
            "SELECT factory_code, db_code, tier, description, quantity, unit_price, record_date, source, provenance "
            "FROM evidence_store"
        ).fetchall()
    return [
        HistoricalRecord(
            factory_code=r[0], db_code=r[1], tier=EvidenceTier(r[2]), description=r[3],
            quantity=r[4], unit_price=r[5], date=r[6], source=r[7], provenance=r[8] or "",
        )
        for r in rows
    ]


def mark_mapping_reviewed(factory_code: str, approved_db_code: str, note: str = "", index_path: Path | str = DEFAULT_INDEX_PATH) -> None:
    with _connect(index_path) as conn:
        conn.execute(
            "INSERT INTO reviewed_mappings (factory_code, approved_db_code, reviewer_note, reviewed_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(factory_code) DO UPDATE SET approved_db_code=excluded.approved_db_code, "
            "reviewer_note=excluded.reviewer_note, reviewed_at=datetime('now')",
            (factory_code, approved_db_code, note),
        )


def get_reviewed_mapping(factory_code: str, index_path: Path | str = DEFAULT_INDEX_PATH) -> Optional[str]:
    with _connect(index_path) as conn:
        row = conn.execute(
            "SELECT approved_db_code FROM reviewed_mappings WHERE factory_code = ?", (factory_code,)
        ).fetchone()
    return row[0] if row else None


def processed_file_count(index_path: Path | str = DEFAULT_INDEX_PATH) -> int:
    with _connect(index_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM processed_files").fetchone()[0]


def evidence_count(index_path: Path | str = DEFAULT_INDEX_PATH) -> int:
    with _connect(index_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM evidence_store").fetchone()[0]
