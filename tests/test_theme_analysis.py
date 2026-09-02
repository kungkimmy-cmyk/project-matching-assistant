import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import MatcherConfig
from app.theme_analysis import analyze_rfq_theme, classify_row


class TestClassifyRow(unittest.TestCase):
    def setUp(self):
        self.cfg = MatcherConfig()

    def test_rustic_takes_priority_over_colour_words(self):
        # Real sample text: rustic aesthetic that also happens to
        # mention colour words -- rustic must win per spec ("brown
        # clay is a specific rustic aesthetic and should not be
        # treated as merely a colour").
        rt = classify_row(1, "f.xlsx", "Rustic colour stone ware light blue with seasame light brown", self.cfg)
        self.assertTrue(rt.is_rustic)

    def test_green_row_detected(self):
        rt = classify_row(1, "f.xlsx", "10 inch fresh green dinner plate", self.cfg)
        self.assertEqual(rt.colour_family, "green")

    def test_plain_white_row(self):
        rt = classify_row(1, "f.xlsx", "9.25 inch plate - white porcelain", self.cfg)
        self.assertTrue(rt.is_plain_white)

    def test_patterned_row(self):
        rt = classify_row(1, "f.xlsx", "Diamond pattern rim plate with decal line", self.cfg)
        self.assertTrue(rt.is_patterned)


class TestAnalyzeRfqTheme(unittest.TestCase):
    def setUp(self):
        self.cfg = MatcherConfig()

    def test_coordinated_green_collection(self):
        # The user's own example: different shapes/sizes, same colour.
        rows = [
            (1, "f.xlsx", "10 inch green dinner plate"),
            (2, "f.xlsx", "8 inch green plate"),
            (3, "f.xlsx", "green bowl"),
            (4, "f.xlsx", "green cup"),
        ]
        profile = analyze_rfq_theme(rows, self.cfg)
        self.assertEqual(profile.dominant_theme, "colour_driven")
        self.assertEqual(profile.dominant_colour_family, "green")
        self.assertTrue(profile.coordinated_collection)
        self.assertEqual(profile.recommended_factory, "HUAXIN")

    def test_rustic_brown_clay_collection_recommends_yixin(self):
        rows = [
            (1, "f.xlsx", "Rustic stoneware dinner plate, earthy brown"),
            (2, "f.xlsx", "Rustic stoneware bowl, craftstone finish"),
            (3, "f.xlsx", "Night and Day rustic mug"),
        ]
        profile = analyze_rfq_theme(rows, self.cfg)
        self.assertEqual(profile.dominant_theme, "rustic_brown_clay")
        self.assertEqual(profile.recommended_factory, "YIXIN")

    def test_whiteware_collection_recommends_jiaxiang(self):
        rows = [
            (1, "f.xlsx", "Classic white dinner plate"),
            (2, "f.xlsx", "Plain white porcelain bowl"),
            (3, "f.xlsx", "White porcelain cup"),
        ]
        profile = analyze_rfq_theme(rows, self.cfg)
        self.assertEqual(profile.dominant_theme, "whiteware")
        self.assertEqual(profile.recommended_factory, "JIAXIANG")

    def test_mixed_rfq_no_strong_recommendation(self):
        rows = [
            (1, "f.xlsx", "green plate"),
            (2, "f.xlsx", "blue bowl"),
            (3, "f.xlsx", "white cup"),
            (4, "f.xlsx", "rustic mug"),
        ]
        profile = analyze_rfq_theme(rows, self.cfg)
        self.assertEqual(profile.dominant_theme, "mixed")
        self.assertEqual(profile.recommended_factory, "")
        self.assertFalse(profile.coordinated_collection)
        self.assertLessEqual(profile.theme_confidence, 65)  # mixed never reported high-confidence

    def test_empty_rows_handled_gracefully(self):
        profile = analyze_rfq_theme([], self.cfg)
        self.assertEqual(profile.dominant_theme, "mixed")
        self.assertEqual(profile.theme_confidence, 0)

    def test_coordinated_share_reflects_actual_fraction(self):
        rows = [(i, "f.xlsx", "green plate") for i in range(1, 9)] + [(9, "f.xlsx", "blue bowl")]
        profile = analyze_rfq_theme(rows, self.cfg)
        self.assertAlmostEqual(profile.coordinated_share, 8 / 9, places=2)

    def test_db_code_suffix_detects_coordination_missed_by_free_text(self):
        # Regression: found via real-file testing -- a genuine
        # coordinated green collection (26/27 real rows shared one DB
        # code suffix) was classified 'mixed' because only 28% of rows
        # repeated the word 'green' in free text (many just said the
        # collection/shape name, e.g. "BREAD PLATE 6\" - DAYBREAK").
        # DB-code-suffix evidence must catch this even when free text doesn't.
        rows = [
            (1, "f.xlsx", "Flat green square 16CM", "DB8183021-ND008"),
            (2, "f.xlsx", "Flat green square 22CM", "DB8183025-ND008"),
            (3, "f.xlsx", 'BREAD PLATE 6" - DAYBREAK', "DB8110115-ND008"),  # no colour word at all
            (4, "f.xlsx", "Oval plate, 21CM x 16CM", "DB8121024-ND008"),  # no colour word at all
            (5, "f.xlsx", "Green plate, 18CM", "DB8150116-ND008"),
        ]
        profile = analyze_rfq_theme(rows, self.cfg)
        self.assertEqual(profile.dominant_theme, "colour_driven")
        self.assertTrue(profile.coordinated_collection)
        self.assertGreaterEqual(profile.coordinated_share, 0.9)

    def test_backward_compatible_three_tuples_still_work(self):
        # The original 3-tuple call signature (no db_code) must still work.
        rows = [(1, "f.xlsx", "green plate"), (2, "f.xlsx", "green bowl")]
        profile = analyze_rfq_theme(rows, self.cfg)
        self.assertEqual(profile.dominant_theme, "colour_driven")

    def test_db_code_suffix_ignored_when_purely_numeric(self):
        # A numeric suffix (e.g. "-1", "-2" set-piece variants) is not
        # a colour code and must not trigger false coordination.
        rows = [
            (1, "f.xlsx", "cup", "DB100001-1"),
            (2, "f.xlsx", "saucer", "DB100001-2"),
            (3, "f.xlsx", "unrelated blue item", "DB999999"),
        ]
        profile = analyze_rfq_theme(rows, self.cfg)
        self.assertNotEqual(profile.dominant_theme, "colour_driven")  # falls through to free-text, which is weak here


class TestExtractThemeRows(unittest.TestCase):
    """Regression coverage for the real-file finding: theme-defining
    language often sits on rows with NO DB/factory code yet (the
    original incoming RFQ template, before quotation)."""

    def test_rows_without_any_code_are_still_included(self):
        from app.file_reader import WorkbookSummary, SheetSummary, CellInfo
        from app.theme_analysis import extract_theme_rows

        sheet = SheetSummary(name="Sheet1", max_row=2, max_col=2, cells={
            "1,1": CellInfo(row=1, col=1, coordinate="A1", value="Rustic colour stone ware light blue with seasame light brown", fill_rgb=None),
            "2,1": CellInfo(row=2, col=1, coordinate="A2", value="Round side plate D20.5XH2cm", fill_rgb=None),
        })
        wb = WorkbookSummary(path=None, filename="x.xlsx", created=None, modified=None, creator=None, sheets=[sheet])
        rows = extract_theme_rows(wb)
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0][3])  # no DB code, but still included

    def test_rows_with_a_code_include_it(self):
        from app.file_reader import WorkbookSummary, SheetSummary, CellInfo
        from app.theme_analysis import extract_theme_rows

        sheet = SheetSummary(name="Sheet1", max_row=1, max_col=2, cells={
            "1,1": CellInfo(row=1, col=1, coordinate="A1", value="Green plate 18cm DB8150116-ND008", fill_rgb=None),
        })
        wb = WorkbookSummary(path=None, filename="x.xlsx", created=None, modified=None, creator=None, sheets=[sheet])
        rows = extract_theme_rows(wb)
        self.assertEqual(rows[0][3], "DB8150116-ND008")


if __name__ == "__main__":
    unittest.main()
