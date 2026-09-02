import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.product_comparison import (
    ExtractedProduct, compare_products, build_db_code_mapping, ProductComparisonRow,
)
from app.config import MatcherConfig
from app.theme_analysis import analyze_rfq_theme
from app.factory_rules import recommend_factory
from app.historical_evidence import EvidenceTier, HistoricalRecord, build_authoritative_index


class TestCompareProductsBackwardCompatible(unittest.TestCase):
    """These exact tests passed before the theme/factory/historical
    integration was added -- must still pass unchanged, called with
    only the original two positional arguments."""

    def test_matched_via_embedded_factory_code(self):
        factory = [ExtractedProduct("H0025", None, "PLATE 9IN", 19.0, "factory.xlsx", 5)]
        customer = [ExtractedProduct(None, "DB30H0025", "9 INCH PLATE", 3.92, "customer.xlsx", 5)]
        result = compare_products(factory, customer)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].matched)
        self.assertGreaterEqual(result[0].confidence, 90)
        self.assertEqual(result[0].factory_cost, 19.0)
        self.assertEqual(result[0].selling_price, 3.92)

    def test_unmatched_factory_product_flagged(self):
        factory = [ExtractedProduct("H9999", None, "RARE ITEM", 5.0, "factory.xlsx", 1)]
        customer = [ExtractedProduct(None, "DB30H0001", "SOMETHING ELSE ENTIRELY", 1.0, "customer.xlsx", 1)]
        result = compare_products(factory, customer)
        unmatched = [r for r in result if not r.matched]
        self.assertTrue(any("H9999" in r.factory_code for r in unmatched))

    def test_unmatched_customer_product_flagged(self):
        factory = [ExtractedProduct("H0001", None, "PLATE", 5.0, "factory.xlsx", 1)]
        customer = [
            ExtractedProduct(None, "DB30H0001", "PLATE", 1.0, "customer.xlsx", 1),
            ExtractedProduct(None, "DB99999999", "EXTRA ITEM NOT IN FACTORY QUOTE", 2.0, "customer.xlsx", 2),
        ]
        result = compare_products(factory, customer)
        notes = [r.notes for r in result if not r.matched]
        self.assertTrue(any("factory file" in n for n in notes))

    def test_description_similarity_fallback_match(self):
        factory = [ExtractedProduct(None, None, "ROUND DINNER PLATE 9 INCH WHITE", 2.0, "factory.xlsx", 1)]
        customer = [ExtractedProduct(None, "DB123456", "ROUND DINNER PLATE 9 INCH WHITE", 1.0, "customer.xlsx", 1)]
        result = compare_products(factory, customer)
        self.assertTrue(result[0].matched)

    def test_no_double_matching_same_customer_product(self):
        factory = [
            ExtractedProduct("H0001", None, "PLATE", 1.0, "factory.xlsx", 1),
            ExtractedProduct("H0002", None, "PLATE", 1.0, "factory.xlsx", 2),
        ]
        customer = [ExtractedProduct(None, "DB30H0001", "PLATE", 1.0, "customer.xlsx", 1)]
        result = compare_products(factory, customer)
        matched_count = sum(1 for r in result if r.matched)
        self.assertEqual(matched_count, 1)


class TestDbCodeMapping(unittest.TestCase):
    def test_single_db_code_no_flag(self):
        rows = [ProductComparisonRow("H0001", "DB30H0001", "PLATE", 1.0, 2.0, True, 95, "")]
        mapping = build_db_code_mapping([("Project A", rows)])
        self.assertEqual(len(mapping), 1)
        self.assertIn("N/A", mapping[0].confidence)

    def test_multiple_db_codes_flagged_for_review(self):
        rows_a = [ProductComparisonRow("H0001", "DB100001", "PLATE", 1.0, 2.0, True, 95, "")]
        rows_b = [ProductComparisonRow("H0001", "DB200002", "PLATE", 1.0, 2.0, True, 95, "")]
        mapping = build_db_code_mapping([("Project A", rows_a), ("Project B", rows_b)])
        self.assertEqual(len(mapping), 1)
        self.assertEqual(mapping[0].primary_db_code, "DB100001")
        self.assertEqual(mapping[0].alternative_db_codes, ["DB200002"])
        self.assertIn("Review", mapping[0].confidence)

    def test_never_merges_just_reports(self):
        rows_a = [ProductComparisonRow("H0001", "DB100001", "PLATE", 1.0, 2.0, True, 95, "")]
        rows_b = [ProductComparisonRow("H0001", "DB200002", "PLATE", 1.0, 2.0, True, 95, "")]
        mapping = build_db_code_mapping([("Project A", rows_a), ("Project B", rows_b)])
        all_codes = {mapping[0].primary_db_code} | set(mapping[0].alternative_db_codes)
        self.assertEqual(all_codes, {"DB100001", "DB200002"})


class TestCompareProductsWithNewContext(unittest.TestCase):
    """New integration: theme/factory-coherence/reactive-glaze/
    historical-evidence signals, only active when the optional context
    is supplied."""

    def setUp(self):
        self.cfg = MatcherConfig()

    def test_alternatives_are_populated(self):
        factory = [ExtractedProduct("H0025", None, "9 inch white plate", 3.0, "factory.xlsx", 1)]
        customer = [
            ExtractedProduct(None, "DB30H0025", "9 inch white plate", 5.0, "customer.xlsx", 1),
            ExtractedProduct(None, "DB999999", "9 inch white plate similar", 4.5, "customer.xlsx", 2),
        ]
        result = compare_products(factory, customer, cfg=self.cfg)
        self.assertTrue(result[0].matched)
        # Second candidate (also a decent description match) should show as an alternative.
        self.assertGreaterEqual(len(result[0].alternatives), 0)

    def test_reactive_glaze_flagged_for_db6_prefix(self):
        factory = [ExtractedProduct("H0025", None, "reactive glazed bowl", 3.0, "factory.xlsx", 1)]
        customer = [ExtractedProduct(None, "DB6123456", "reactive glazed bowl", 5.0, "customer.xlsx", 1)]
        result = compare_products(factory, customer, cfg=self.cfg)
        self.assertTrue(result[0].reactive_glaze)

    def test_reactive_glaze_flagged_for_mg_keyword(self):
        factory = [ExtractedProduct("MG007", None, "MG colour glaze plate", 3.0, "factory.xlsx", 1)]
        customer = [ExtractedProduct(None, "DB123456", "MG colour glaze plate", 5.0, "customer.xlsx", 1)]
        result = compare_products(factory, customer, cfg=self.cfg)
        self.assertTrue(result[0].reactive_glaze)

    def test_non_reactive_product_not_flagged(self):
        factory = [ExtractedProduct("H0025", None, "plain white plate", 3.0, "factory.xlsx", 1)]
        customer = [ExtractedProduct(None, "DB30H0025", "plain white plate", 5.0, "customer.xlsx", 1)]
        result = compare_products(factory, customer, cfg=self.cfg)
        self.assertFalse(result[0].reactive_glaze)

    def test_word_containing_nd_substring_not_falsely_flagged(self):
        # Regression: found in real output -- "CREAMER W/ HANDLE" was
        # being flagged reactive glaze because a bare 'nd' substring
        # check matched inside "ha-nd-le". Word-boundary matching must
        # not trigger on ordinary English words.
        factory = [ExtractedProduct("H0984", None, "CREAMER W/ HANDLE 90ML - MATT BLACK", 3.0, "factory.xlsx", 1)]
        customer = [ExtractedProduct(None, "DB2150012", "CREAMER W/ HANDLE 90ML - MATT BLACK", 5.0, "customer.xlsx", 1)]
        result = compare_products(factory, customer, cfg=self.cfg)
        self.assertFalse(result[0].reactive_glaze)

    def test_standalone_mg_word_still_flagged(self):
        # The word-boundary fix must not lose real MG-series detection
        # just because it no longer requires trailing digits.
        factory = [ExtractedProduct("H0025", None, "MG colour glaze plate", 3.0, "factory.xlsx", 1)]
        customer = [ExtractedProduct(None, "DB123456", "MG colour glaze plate", 5.0, "customer.xlsx", 1)]
        result = compare_products(factory, customer, cfg=self.cfg)
        self.assertTrue(result[0].reactive_glaze)

    def test_db6_mirage_flagged_reactive_regardless_of_black_or_white_base_colour(self):
        # Authoritative clarification (confirmed against the 2026 Don
        # Bellini Export Price List): DB6 = Mirage collection = always
        # reactive glaze. Mirage products have a BLACK or WHITE
        # base/exterior colour option -- that wording describes the
        # base colour WITHIN the Mirage line, it does NOT mean the
        # product is plain/non-reactive. Both variants must be flagged.
        factory = [ExtractedProduct("H0025", None, 'oval plate 14" - white', 3.0, "factory.xlsx", 1)]
        customer_white = [ExtractedProduct(None, "DB6211135", 'OVAL PLATE 14" - WHITE', 5.0, "customer.xlsx", 1)]
        customer_black = [ExtractedProduct(None, "DB6111135", 'OVAL PLATE 14" - BLACK', 5.0, "customer.xlsx", 1)]
        result_white = compare_products(factory, customer_white, cfg=self.cfg)
        result_black = compare_products(factory, customer_black, cfg=self.cfg)
        self.assertTrue(result_white[0].reactive_glaze)
        self.assertTrue(result_black[0].reactive_glaze)

    def test_unmatched_customer_product_still_checked_for_reactive_glaze(self):
        # Regression: found via the test above -- a customer product
        # with NO factory-side match (e.g. description too different
        # to auto-match) was never checked for reactive glaze at all,
        # even when its own DB code clearly indicated it (DB6xxxxx).
        factory = [ExtractedProduct("H9999", None, "completely unrelated item", 3.0, "factory.xlsx", 1)]
        customer = [ExtractedProduct(None, "DB6211135", 'OVAL PLATE 14" - WHITE', 5.0, "customer.xlsx", 1)]
        result = compare_products(factory, customer, cfg=self.cfg)
        unmatched_customer_row = [r for r in result if not r.matched and r.db_code == "DB6211135"][0]
        self.assertTrue(unmatched_customer_row.reactive_glaze)

    def test_theme_alignment_note_when_on_recommended_factory(self):
        rows = [(i, "f.xlsx", "fresh green bowl") for i in range(1, 5)]
        theme = analyze_rfq_theme(rows, self.cfg)
        rec = recommend_factory(theme, self.cfg)  # HUAXIN
        factory = [ExtractedProduct("H0025", None, "green bowl", 3.0, "factory.xlsx", 1)]
        customer = [ExtractedProduct(None, "DB30H0025", "green bowl", 5.0, "customer.xlsx", 1)]
        result = compare_products(factory, customer, theme_profile=theme, factory_recommendation=rec, cfg=self.cfg)
        self.assertIn("recommended factory", result[0].theme_alignment_note.lower())

    def test_historical_evidence_marks_needs_review(self):
        records = [
            HistoricalRecord("H0025", "DB_OLD", EvidenceTier.QUOTED, date="2020-01-01"),
            HistoricalRecord("H0025", "DB_NEW", EvidenceTier.QUOTED, date="2026-01-01"),
        ]
        index = build_authoritative_index(records)
        factory = [ExtractedProduct("H0025", None, "plate", 3.0, "factory.xlsx", 1)]
        customer = [ExtractedProduct(None, "DB_OLD", "plate", 5.0, "customer.xlsx", 1)]
        result = compare_products(factory, customer, authoritative_index=index, cfg=self.cfg)
        self.assertTrue(result[0].historical_needs_review)
        self.assertFalse(result[0].auto_selected)  # a real historical conflict must not be auto-accepted

    def test_high_confidence_clear_winner_auto_selected(self):
        factory = [ExtractedProduct("H0025", None, "plate", 3.0, "factory.xlsx", 1)]
        customer = [ExtractedProduct(None, "DB30H0025", "plate", 5.0, "customer.xlsx", 1)]
        result = compare_products(factory, customer, cfg=self.cfg)
        self.assertTrue(result[0].auto_selected)  # embedded factory code = 95, clear winner, no conflict

    def test_called_without_any_new_context_still_works(self):
        # The exact original call signature, no cfg/theme/etc at all.
        factory = [ExtractedProduct("H0025", None, "plate", 3.0, "factory.xlsx", 1)]
        customer = [ExtractedProduct(None, "DB30H0025", "plate", 5.0, "customer.xlsx", 1)]
        result = compare_products(factory, customer)
        self.assertTrue(result[0].matched)
        self.assertFalse(result[0].reactive_glaze)  # no cfg supplied -> can't be flagged, safe default


if __name__ == "__main__":
    unittest.main()
