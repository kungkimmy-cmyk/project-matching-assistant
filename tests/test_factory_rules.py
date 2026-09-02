import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import MatcherConfig
from app.theme_analysis import analyze_rfq_theme
from app.factory_rules import recommend_factory, factory_for_code, apply_project_coherence


class TestRecommendFactory(unittest.TestCase):
    def setUp(self):
        self.cfg = MatcherConfig()

    def test_rustic_recommends_yixin(self):
        rows = [(i, "f.xlsx", "Rustic stoneware craftstone plate") for i in range(1, 5)]
        theme = analyze_rfq_theme(rows, self.cfg)
        rec = recommend_factory(theme, self.cfg)
        self.assertEqual(rec.factory, "YIXIN")
        self.assertGreater(rec.confidence, 0)

    def test_colour_driven_recommends_huaxin(self):
        rows = [(i, "f.xlsx", "fresh green bowl") for i in range(1, 5)]
        theme = analyze_rfq_theme(rows, self.cfg)
        rec = recommend_factory(theme, self.cfg)
        self.assertEqual(rec.factory, "HUAXIN")

    def test_whiteware_recommends_jiaxiang(self):
        rows = [(i, "f.xlsx", "plain white classic plate") for i in range(1, 5)]
        theme = analyze_rfq_theme(rows, self.cfg)
        rec = recommend_factory(theme, self.cfg)
        self.assertEqual(rec.factory, "JIAXIANG")

    def test_mixed_no_recommendation(self):
        rows = [(1, "f.xlsx", "green plate"), (2, "f.xlsx", "blue bowl"),
                (3, "f.xlsx", "white cup"), (4, "f.xlsx", "rustic mug")]
        theme = analyze_rfq_theme(rows, self.cfg)
        rec = recommend_factory(theme, self.cfg)
        self.assertEqual(rec.factory, "")
        self.assertEqual(rec.confidence, 0)


class TestFactoryForCode(unittest.TestCase):
    def setUp(self):
        self.cfg = MatcherConfig()

    def test_jx_prefix(self):
        self.assertEqual(factory_for_code("JX0823-1", self.cfg), "JIAXIANG")

    def test_hx_prefix_takes_priority_over_bare_h(self):
        # "HX..." should resolve via the HX hint, not accidentally via
        # a shorter "H" prefix rule -- longer/more specific prefixes
        # must be checked first.
        self.assertEqual(factory_for_code("HX1234", self.cfg), "HUAXIN")

    def test_bare_h_prefix(self):
        self.assertEqual(factory_for_code("H0025", self.cfg), "HUAXIN")

    def test_yx_prefix(self):
        self.assertEqual(factory_for_code("YX5678", self.cfg), "YIXIN")

    def test_none_for_unknown(self):
        self.assertIsNone(factory_for_code("ZZ9999", self.cfg))

    def test_none_for_empty(self):
        self.assertIsNone(factory_for_code("", self.cfg))
        self.assertIsNone(factory_for_code(None, self.cfg))


class TestProjectCoherence(unittest.TestCase):
    def setUp(self):
        self.cfg = MatcherConfig()

    def test_recommended_factory_codes_get_bonus(self):
        bonuses = apply_project_coherence(["H0025", "JX0823-1", "YX1111"], "HUAXIN", self.cfg)
        self.assertGreater(bonuses["H0025"], 0)
        self.assertEqual(bonuses["JX0823-1"], 0)
        self.assertEqual(bonuses["YX1111"], 0)

    def test_no_recommendation_means_no_bonus(self):
        bonuses = apply_project_coherence(["H0025", "JX0823-1"], "", self.cfg)
        self.assertEqual(bonuses["H0025"], 0)
        self.assertEqual(bonuses["JX0823-1"], 0)

    def test_dimensions_not_ignored_bonus_is_additive_not_overriding(self):
        # This module only produces a bonus score -- it must not itself
        # decide the winner; product_comparison.py adds this on top of
        # (not instead of) the dimension/description score.
        bonuses = apply_project_coherence(["H0025"], "HUAXIN", self.cfg)
        self.assertIsInstance(bonuses["H0025"], int)
        self.assertLess(bonuses["H0025"], 100)  # never dominant enough to override real evidence by itself


if __name__ == "__main__":
    unittest.main()
