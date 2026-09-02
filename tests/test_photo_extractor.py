import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import MatcherConfig
from app.photo_extractor import PhotoStore, extract_photos, _classify_colour
from app.file_reader import read_workbook


def _make_solid_colour_png_bytes(rgb):
    from PIL import Image
    import io
    img = Image.new("RGB", (32, 32), rgb)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_workbook_with_image(path, rgb, anchor_row=5):
    import openpyxl
    from openpyxl.drawing.image import Image as XLImage
    import io

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.cell(row=anchor_row, column=1, value="Test Product")

    png_bytes = _make_solid_colour_png_bytes(rgb)
    img_path = Path(tempfile.mktemp(suffix=".png"))
    img_path.write_bytes(png_bytes)
    xl_img = XLImage(str(img_path))
    ws.add_image(xl_img, f"B{anchor_row}")
    wb.save(path)
    return png_bytes


class TestClassifyColour(unittest.TestCase):
    def test_green_image_classified_as_green(self):
        png_bytes = _make_solid_colour_png_bytes((20, 180, 20))
        family, confidence = _classify_colour(png_bytes)
        self.assertEqual(family, "green")
        self.assertGreater(confidence, 0)

    def test_white_image_classified_as_white(self):
        png_bytes = _make_solid_colour_png_bytes((250, 250, 250))
        family, confidence = _classify_colour(png_bytes)
        self.assertEqual(family, "white")

    def test_confidence_capped_below_high_threshold(self):
        # Confidence must stay conservative -- this is a coarse signal,
        # not claimed precision.
        png_bytes = _make_solid_colour_png_bytes((0, 200, 0))  # exact bucket match
        _, confidence = _classify_colour(png_bytes)
        self.assertLess(confidence, 0.75)

    def test_garbage_bytes_does_not_crash(self):
        family, confidence = _classify_colour(b"not an image")
        self.assertIsNone(family)
        self.assertEqual(confidence, 0.0)


class TestPhotoStore(unittest.TestCase):
    def setUp(self):
        self.store_dir = Path(tempfile.mkdtemp())
        self.cfg = MatcherConfig()

    def test_extract_and_store_real_embedded_image(self):
        wb_path = Path(tempfile.mktemp(suffix=".xlsx"))
        _make_workbook_with_image(wb_path, (20, 180, 20), anchor_row=5)

        wb = read_workbook(wb_path)
        store = PhotoStore(store_dir=self.store_dir)
        records = extract_photos(wb, self.cfg, store)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source_file, wb_path.name)
        self.assertTrue(Path(records[0].stored_path).exists())
        self.assertEqual(records[0].colour_hint, "green")

    def test_photo_associated_with_correct_row(self):
        wb_path = Path(tempfile.mktemp(suffix=".xlsx"))
        _make_workbook_with_image(wb_path, (20, 180, 20), anchor_row=7)
        wb = read_workbook(wb_path)
        store = PhotoStore(store_dir=self.store_dir)
        extract_photos(wb, self.cfg, store)

        matches = store.photos_for_row(wb_path.name, wb.sheets[0].name, 7)
        self.assertEqual(len(matches), 1)
        no_matches = store.photos_for_row(wb_path.name, wb.sheets[0].name, 99)
        self.assertEqual(no_matches, [])

    def test_index_persists_across_save_and_load(self):
        wb_path = Path(tempfile.mktemp(suffix=".xlsx"))
        _make_workbook_with_image(wb_path, (20, 180, 20))
        wb = read_workbook(wb_path)
        store = PhotoStore(store_dir=self.store_dir)
        extract_photos(wb, self.cfg, store)
        store.save()

        store2 = PhotoStore(store_dir=self.store_dir)
        store2.load()
        self.assertEqual(len(store2.index), 1)

    def test_reextracting_same_image_does_not_duplicate(self):
        wb_path = Path(tempfile.mktemp(suffix=".xlsx"))
        _make_workbook_with_image(wb_path, (20, 180, 20))
        wb = read_workbook(wb_path)
        store = PhotoStore(store_dir=self.store_dir)
        extract_photos(wb, self.cfg, store)
        extract_photos(wb, self.cfg, store)  # same workbook, extracted twice
        self.assertEqual(len(store.index), 1)


if __name__ == "__main__":
    unittest.main()
