"""
Regression tests for the MG007-as-factory-code bug, using the exact
real examples reported. Proves:
  - MG007/MG006 are recognized as FINISH variants, never factory codes
  - H11328 / H5827-X are correctly recovered as the true factory Part No.
  - DB Code Mapping no longer flags finish variants as false conflicts
  - The 'same product row' evidence (a genuinely separate Part No.
    cell) always outranks anything derived/inferred
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.project_matcher import split_finish_suffix, derive_factory_code_from_db_code, _is_likely_color_suffix
from app.product_comparison import ExtractedProduct, extract_products, build_db_code_mapping
from app.config import MatcherConfig
from app.file_reader import WorkbookSummary, SheetSummary, CellInfo


class TestMGCodesNotFactoryCodes(unittest.TestCase):
    def setUp(self):
        self.cfg = MatcherConfig()

    def test_mg007_excluded_as_factory_code(self):
        self.assertTrue(_is_likely_color_suffix("MG007", self.cfg))
        self.assertTrue(_is_likely_color_suffix("MG006", self.cfg))

    def test_nd_codes_excluded_too_generalized_not_just_mg(self):
        self.assertTrue(_is_likely_color_suffix("ND008", self.cfg))

    def test_real_factory_codes_never_excluded(self):
        for code in ("H11328", "H5827-X", "H12039", "JX0823-1"):
            self.assertFalse(_is_likely_color_suffix(code, self.cfg), f"{code} incorrectly treated as a finish code")


class TestSplitAndDeriveRealExamples(unittest.TestCase):
    """The exact three examples given in the bug report."""

    def setUp(self):
        self.cfg = MatcherConfig()

    def test_h11328_db3h11328_mg007(self):
        base, finish = split_finish_suffix("DB3H11328-MG007", self.cfg)
        self.assertEqual(base, "DB3H11328")
        self.assertEqual(finish, "MG007")
        self.assertEqual(derive_factory_code_from_db_code("DB3H11328-MG007", self.cfg), "H11328")

    def test_h11328_db3h11328_mg006_same_base_different_finish(self):
        base, finish = split_finish_suffix("DB3H11328-MG006", self.cfg)
        self.assertEqual(base, "DB3H11328")  # SAME base as -MG007
        self.assertEqual(finish, "MG006")
        self.assertEqual(derive_factory_code_from_db_code("DB3H11328-MG006", self.cfg), "H11328")

    def test_h5827_x_db30h5827_x_mg007(self):
        base, finish = split_finish_suffix("DB30H5827-X-MG007", self.cfg)
        self.assertEqual(base, "DB30H5827-X")  # the '-X' shape suffix must be KEPT
        self.assertEqual(finish, "MG007")
        self.assertEqual(derive_factory_code_from_db_code("DB30H5827-X-MG007", self.cfg), "H5827-X")

    def test_h12039_db3h12039_mg007(self):
        self.assertEqual(derive_factory_code_from_db_code("DB3H12039-MG007", self.cfg), "H12039")


class TestExtractProductsRealScenario(unittest.TestCase):
    """A workbook where the ONLY code present in a row is the compound
    DB+finish code (no separate Part No. cell) -- the exact real-file
    layout that produced the bug."""

    def setUp(self):
        self.cfg = MatcherConfig()

    def _make_wb(self, cell_value):
        sheet = SheetSummary(name="Sheet1", max_row=1, max_col=1, cells={
            "1,1": CellInfo(row=1, col=1, coordinate="A1", value=cell_value, fill_rgb=None),
        })
        return WorkbookSummary(path=None, filename="x.xlsx", created=None, modified=None, creator=None, sheets=[sheet])

    def test_factory_code_derived_not_mg007(self):
        wb = self._make_wb("DB3H11328-MG007")
        products = extract_products(wb, self.cfg)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].factory_code, "H11328")
        self.assertNotEqual(products[0].factory_code, "MG007")
        self.assertEqual(products[0].finish_code, "MG007")
        self.assertEqual(products[0].factory_code_source, "derived_from_db_code")

    def test_separate_part_no_cell_wins_over_derivation(self):
        # 'Same product row' evidence (a real, separate Part No. cell)
        # must always outrank anything inferred from the DB code alone.
        sheet = SheetSummary(name="Sheet1", max_row=1, max_col=2, cells={
            "1,1": CellInfo(row=1, col=1, coordinate="A1", value="DB3H11328-MG007", fill_rgb=None),
            "1,2": CellInfo(row=1, col=2, coordinate="B1", value="H11328-REAL", fill_rgb=None),
        })
        wb = WorkbookSummary(path=None, filename="x.xlsx", created=None, modified=None, creator=None, sheets=[sheet])
        products = extract_products(wb, self.cfg)
        self.assertEqual(products[0].factory_code, "H11328-REAL")
        self.assertEqual(products[0].factory_code_source, "same_row_cell")


class TestDbCodeMappingNoFalseConflict(unittest.TestCase):
    """The actual reported false-positive: MG007/MG006 finish variants
    of the same product must NOT be flagged as competing 'alternative
    DB codes' needing manual review."""

    def setUp(self):
        self.cfg = MatcherConfig()

    def _row(self, factory_code, db_code):
        from app.product_comparison import ProductComparisonRow
        return ProductComparisonRow(factory_code, db_code, "desc", 1.0, 2.0, True, 95, "")

    def test_finish_variants_collapse_to_one_product_no_review_flag(self):
        rows = [
            self._row("H11328", "DB3H11328-MG007"),
            self._row("H11328", "DB3H11328-MG006"),
        ]
        mapping = build_db_code_mapping([("Project A", rows)], self.cfg)
        self.assertEqual(len(mapping), 1)
        self.assertEqual(mapping[0].primary_db_code, "DB3H11328")
        self.assertEqual(mapping[0].alternative_db_codes, [])  # NOT flagged as alternatives
        self.assertIn("N/A", mapping[0].confidence)  # no review needed
        self.assertIn("MG006", mapping[0].recommendation)  # finish variants still visible in the notes
        self.assertIn("MG007", mapping[0].recommendation)

    def test_genuinely_different_products_still_flagged(self):
        # A real conflict (two actually different DB codes for one
        # factory code) must still be caught -- the fix must not
        # suppress real conflicts, only false ones caused by finish suffixes.
        rows = [
            self._row("H0001", "DB100001"),
            self._row("H0001", "DB200002"),
        ]
        mapping = build_db_code_mapping([("Project A", rows)], self.cfg)
        self.assertEqual(len(mapping[0].alternative_db_codes), 1)
        self.assertIn("Review", mapping[0].confidence)

    def test_h5827_x_finish_variants_also_collapse(self):
        rows = [
            self._row("H5827-X", "DB30H5827-X-MG007"),
            self._row("H5827-X", "DB30H5827-X-MG006"),
        ]
        mapping = build_db_code_mapping([("Project A", rows)], self.cfg)
        self.assertEqual(mapping[0].primary_db_code, "DB30H5827-X")
        self.assertEqual(mapping[0].alternative_db_codes, [])


if __name__ == "__main__":
    unittest.main()
