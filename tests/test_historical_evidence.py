import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.historical_evidence import (
    EvidenceTier, HistoricalRecord, resolve_authoritative_db_code, build_authoritative_index,
)


def _rec(factory_code, db_code, tier, date=None, quantity=None, source="test"):
    return HistoricalRecord(factory_code=factory_code, db_code=db_code, tier=tier, date=date, quantity=quantity, source=source)


class TestResolveAuthoritative(unittest.TestCase):
    def test_single_record_no_review_needed(self):
        result = resolve_authoritative_db_code([_rec("H0025", "DB30H0025", EvidenceTier.QUOTED)])
        self.assertEqual(result.resolved_db_code, "DB30H0025")
        self.assertFalse(result.needs_review)

    def test_sold_beats_quoted_only(self):
        records = [
            _rec("H0025", "DB30H0025", EvidenceTier.QUOTED),
            _rec("H0025", "DB99999999", EvidenceTier.SOLD),
        ]
        result = resolve_authoritative_db_code(records)
        self.assertEqual(result.resolved_db_code, "DB99999999")
        self.assertEqual(result.tier, EvidenceTier.SOLD)

    def test_sold_conflict_prefers_most_recent(self):
        records = [
            _rec("H0025", "DB100001", EvidenceTier.SOLD, date="2025-01-01", quantity=10),
            _rec("H0025", "DB100002", EvidenceTier.SOLD, date="2026-06-01", quantity=5),
        ]
        result = resolve_authoritative_db_code(records)
        self.assertEqual(result.resolved_db_code, "DB100002")  # most recent
        self.assertTrue(result.needs_review)  # real conflict, always flagged

    def test_sold_conflict_falls_back_to_larger_quantity_when_dates_tied(self):
        records = [
            _rec("H0025", "DB100001", EvidenceTier.SOLD, date="2026-01-01", quantity=10),
            _rec("H0025", "DB100002", EvidenceTier.SOLD, date="2026-01-01", quantity=500),
        ]
        result = resolve_authoritative_db_code(records)
        self.assertEqual(result.resolved_db_code, "DB100002")  # larger quantity

    def test_no_sales_single_quoted_code_retained(self):
        records = [
            _rec("H0025", "DB30H0025", EvidenceTier.QUOTED, date="2025-06-01"),
            _rec("H0025", "DB30H0025", EvidenceTier.FACTORY_QUOTED, date="2025-05-01"),
        ]
        result = resolve_authoritative_db_code(records)
        self.assertEqual(result.resolved_db_code, "DB30H0025")
        self.assertEqual(result.tier, EvidenceTier.QUOTED)  # highest tier present
        self.assertFalse(result.needs_review)

    def test_no_sales_conflicting_quotes_retains_earliest_and_flags(self):
        records = [
            _rec("H0025", "DB_OLD_CODE", EvidenceTier.QUOTED, date="2024-01-01"),
            _rec("H0025", "DB_NEW_CODE", EvidenceTier.QUOTED, date="2026-01-01"),
        ]
        result = resolve_authoritative_db_code(records)
        self.assertEqual(result.resolved_db_code, "DB_OLD_CODE")  # earliest/established, NOT newest
        self.assertTrue(result.needs_review)  # never silently resolved

    def test_factory_quoted_used_only_when_nothing_higher_tier(self):
        records = [_rec("H0025", "DB30H0025", EvidenceTier.FACTORY_QUOTED)]
        result = resolve_authoritative_db_code(records)
        self.assertEqual(result.tier, EvidenceTier.FACTORY_QUOTED)

    def test_empty_records_returns_none(self):
        self.assertIsNone(resolve_authoritative_db_code([]))

    def test_never_silently_overwrites_established_code(self):
        # A single well-established QUOTED code must survive even when
        # a lone, undated FACTORY_QUOTED record for a DIFFERENT code
        # also exists -- QUOTED outranks FACTORY_QUOTED regardless of
        # recency, so the customer-facing code is never bumped by a
        # lower-tier record.
        records = [
            _rec("H0025", "DB_CUSTOMER_FACING", EvidenceTier.QUOTED, date="2020-01-01"),
            _rec("H0025", "DB_FACTORY_ONLY", EvidenceTier.FACTORY_QUOTED, date="2026-01-01"),
        ]
        result = resolve_authoritative_db_code(records)
        self.assertEqual(result.resolved_db_code, "DB_CUSTOMER_FACING")


class TestBuildAuthoritativeIndex(unittest.TestCase):
    def test_groups_by_factory_code(self):
        records = [
            _rec("H0025", "DB30H0025", EvidenceTier.QUOTED),
            _rec("H0023", "DB30H0023", EvidenceTier.QUOTED),
        ]
        index = build_authoritative_index(records)
        self.assertEqual(set(index.keys()), {"H0025", "H0023"})
        self.assertEqual(index["H0025"].resolved_db_code, "DB30H0025")

    def test_records_with_no_factory_code_ignored(self):
        records = [HistoricalRecord(factory_code="", db_code="DB1", tier=EvidenceTier.QUOTED)]
        index = build_authoritative_index(records)
        self.assertEqual(index, {})


if __name__ == "__main__":
    unittest.main()
