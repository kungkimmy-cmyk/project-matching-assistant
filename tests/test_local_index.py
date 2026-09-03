"""
Tests for local_index.py -- the foundational persistent index for
daily incremental use. NOT wired into orchestrator.run_matching() yet
(see CHANGELOG) -- these tests validate the module standalone.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import local_index
from app.historical_evidence import HistoricalRecord, EvidenceTier


class TestFileChangeDetection(unittest.TestCase):
    def setUp(self):
        self.index_path = Path(tempfile.mktemp(suffix=".db"))
        self.d = Path(tempfile.mkdtemp())

    def test_new_file_needs_processing(self):
        f = self.d / "a.xlsx"
        f.write_bytes(b"original content")
        self.assertTrue(local_index.needs_processing(f, self.index_path))

    def test_unchanged_file_does_not_need_reprocessing(self):
        f = self.d / "a.xlsx"
        f.write_bytes(b"original content")
        local_index.record_processed_file(f, rows_extracted=5, index_path=self.index_path)
        self.assertFalse(local_index.needs_processing(f, self.index_path))

    def test_changed_file_content_needs_reprocessing(self):
        f = self.d / "a.xlsx"
        f.write_bytes(b"original content")
        local_index.record_processed_file(f, rows_extracted=5, index_path=self.index_path)
        # Same file path, but content genuinely changed (a real revision).
        f.write_bytes(b"REVISED content, different bytes")
        self.assertTrue(local_index.needs_processing(f, self.index_path))

    def test_revised_file_reprocessed_then_recognized_as_unchanged_again(self):
        f = self.d / "a.xlsx"
        f.write_bytes(b"v1")
        local_index.record_processed_file(f, rows_extracted=5, index_path=self.index_path)
        f.write_bytes(b"v2 -- a real revision")
        self.assertTrue(local_index.needs_processing(f, self.index_path))
        local_index.record_processed_file(f, rows_extracted=7, index_path=self.index_path)
        self.assertFalse(local_index.needs_processing(f, self.index_path))

    def test_missing_file_does_not_need_processing(self):
        f = self.d / "does_not_exist.xlsx"
        self.assertFalse(local_index.needs_processing(f, self.index_path))

    def test_renamed_unchanged_file_is_a_new_path_but_detectable_by_hash(self):
        # Renaming/moving a file changes its path but not its content
        # hash -- record_processed_file keys by path, so a renamed file
        # is (correctly) treated as needing processing under its NEW
        # path; but the hash index makes it possible to recognize it's
        # the SAME content if ever needed (e.g. to avoid duplicate
        # evidence under two different paths for the same file).
        original = self.d / "a.xlsx"
        original.write_bytes(b"same content")
        local_index.record_processed_file(original, rows_extracted=5, index_path=self.index_path)

        renamed = self.d / "a_renamed.xlsx"
        renamed.write_bytes(b"same content")
        self.assertTrue(local_index.needs_processing(renamed, self.index_path))  # new path, correctly flagged
        self.assertEqual(local_index.hash_file(original), local_index.hash_file(renamed))  # but same content


class TestEvidenceRetention(unittest.TestCase):
    def setUp(self):
        self.index_path = Path(tempfile.mktemp(suffix=".db"))

    def _rec(self, factory_code, db_code, tier=EvidenceTier.QUOTED, source_file="f.xlsx"):
        return HistoricalRecord(factory_code=factory_code, db_code=db_code, tier=tier, source=source_file)

    def test_stored_evidence_is_retained(self):
        local_index.store_evidence(self._rec("H0001", "DB100001"), "f.xlsx", self.index_path)
        all_evidence = local_index.load_all_evidence(self.index_path)
        self.assertEqual(len(all_evidence), 1)
        self.assertEqual(all_evidence[0].factory_code, "H0001")
        self.assertEqual(all_evidence[0].db_code, "DB100001")

    def test_evidence_accumulates_across_multiple_stores(self):
        local_index.store_evidence(self._rec("H0001", "DB100001"), "f1.xlsx", self.index_path)
        local_index.store_evidence(self._rec("H0002", "DB100002"), "f2.xlsx", self.index_path)
        self.assertEqual(local_index.evidence_count(self.index_path), 2)

    def test_evidence_survives_a_fresh_connection(self):
        # Simulates evidence persisting across separate app runs (a
        # new process, not just a new function call).
        local_index.store_evidence(self._rec("H0001", "DB100001", tier=EvidenceTier.SOLD), "f.xlsx", self.index_path)
        reloaded = local_index.load_all_evidence(self.index_path)
        self.assertEqual(reloaded[0].tier, EvidenceTier.SOLD)

    def test_no_duplicate_evidence_when_same_unchanged_file_reprocessed(self):
        # The KEY incremental-update guarantee: if a file is unchanged
        # (needs_processing() is False), the caller should skip
        # extracting/storing evidence for it again at all -- this test
        # documents and enforces that contract at the orchestration
        # level a caller must follow: check needs_processing() BEFORE
        # calling store_evidence(), not after.
        f_path = Path(tempfile.mktemp(suffix=".xlsx"))
        f_path.write_bytes(b"content")
        record = self._rec("H0001", "DB100001", source_file=f_path.name)

        # First run: file is new, gets processed and its evidence stored.
        self.assertTrue(local_index.needs_processing(f_path, self.index_path))
        local_index.store_evidence(record, str(f_path), self.index_path)
        local_index.record_processed_file(f_path, rows_extracted=1, index_path=self.index_path)

        # Second run: file unchanged -- a correct caller does NOT call
        # store_evidence again, because needs_processing() says so.
        self.assertFalse(local_index.needs_processing(f_path, self.index_path))
        # (not calling store_evidence here -- that's the point)
        self.assertEqual(local_index.evidence_count(self.index_path), 1)  # still just one record


class TestReviewedMappings(unittest.TestCase):
    def setUp(self):
        self.index_path = Path(tempfile.mktemp(suffix=".db"))

    def test_no_reviewed_mapping_returns_none(self):
        self.assertIsNone(local_index.get_reviewed_mapping("H0001", self.index_path))

    def test_reviewed_mapping_is_retained(self):
        local_index.mark_mapping_reviewed("H0001", "DB100001", "confirmed by user, both codes intentional", self.index_path)
        self.assertEqual(local_index.get_reviewed_mapping("H0001", self.index_path), "DB100001")

    def test_reviewed_mapping_can_be_updated(self):
        local_index.mark_mapping_reviewed("H0001", "DB100001", "first review", self.index_path)
        local_index.mark_mapping_reviewed("H0001", "DB999999", "corrected on second review", self.index_path)
        self.assertEqual(local_index.get_reviewed_mapping("H0001", self.index_path), "DB999999")

    def test_reviewed_mappings_are_independent_per_factory_code(self):
        local_index.mark_mapping_reviewed("H0001", "DB100001", "", self.index_path)
        local_index.mark_mapping_reviewed("H0002", "DB100002", "", self.index_path)
        self.assertEqual(local_index.get_reviewed_mapping("H0001", self.index_path), "DB100001")
        self.assertEqual(local_index.get_reviewed_mapping("H0002", self.index_path), "DB100002")


class TestProcessedFileCount(unittest.TestCase):
    def setUp(self):
        self.index_path = Path(tempfile.mktemp(suffix=".db"))
        self.d = Path(tempfile.mkdtemp())

    def test_count_increases_as_files_are_recorded(self):
        self.assertEqual(local_index.processed_file_count(self.index_path), 0)
        f1 = self.d / "a.xlsx"
        f1.write_bytes(b"1")
        local_index.record_processed_file(f1, 1, self.index_path)
        self.assertEqual(local_index.processed_file_count(self.index_path), 1)

    def test_reprocessing_same_file_does_not_double_count(self):
        f1 = self.d / "a.xlsx"
        f1.write_bytes(b"1")
        local_index.record_processed_file(f1, 1, self.index_path)
        local_index.record_processed_file(f1, 2, self.index_path)  # re-recorded, e.g. after a revision
        self.assertEqual(local_index.processed_file_count(self.index_path), 1)  # still one row, updated not duplicated


if __name__ == "__main__":
    unittest.main()
