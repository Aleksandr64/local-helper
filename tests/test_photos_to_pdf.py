import importlib.util
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

MAIN = Path(__file__).resolve().parent.parent / "tools" / "photos_to_pdf" / "main.py"
spec = importlib.util.spec_from_file_location("photos_to_pdf", MAIN)
p2p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p2p)

try:
    import pikepdf
    from PIL import Image
except ImportError:
    pikepdf = None


def make_photo(path: Path, size=(40, 30), mode="RGB", date: str | None = None, orientation: int | None = None):
    im = Image.new(mode, size, "red" if mode == "RGB" else (0, 0, 255, 128))
    exif = Image.Exif()
    if date:
        exif.get_ifd(p2p.EXIF_IFD)[p2p.TAG_DATETIME_ORIGINAL] = date
    if orientation:
        exif[p2p.TAG_ORIENTATION] = orientation
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        im.save(path, exif=exif)
    else:
        im.save(path)


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(MAIN), *args], capture_output=True, text=True,
                          encoding="utf-8", env={"PYTHONUTF8": "1", "SYSTEMROOT": "C:\\Windows"})


class NaturalSortTest(unittest.TestCase):
    def test_numbers_compared_as_numbers(self):
        names = ["10.jpg", "2.jpg", "IMG_3.jpg", "1.jpg", "img_20.jpg"]
        ordered = [p.name for p in sorted(map(Path, names), key=p2p.natural_key)]
        self.assertEqual(ordered, ["1.jpg", "2.jpg", "10.jpg", "IMG_3.jpg", "img_20.jpg"])


@unittest.skipIf(pikepdf is None or p2p.MISSING_DEP, "немає залежностей photos_to_pdf (tt setup)")
class PhotosToPdfTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "photos"
        self.dir.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def pages(self, pdf: Path):
        with pikepdf.open(pdf) as doc:
            return [(float(p.mediabox[2]), float(p.mediabox[3])) for p in doc.pages]

    def test_photo_date_reads_exif(self):
        make_photo(self.dir / "a.jpg", date="2024:05:01 10:20:30")
        self.assertEqual(p2p.photo_date(self.dir / "a.jpg"), datetime(2024, 5, 1, 10, 20, 30))

    def test_sort_by_date_ignores_names(self):
        make_photo(self.dir / "1.jpg", date="2024:05:03 00:00:00")
        make_photo(self.dir / "2.jpg", date="2024:05:01 00:00:00")
        make_photo(self.dir / "3.jpg", date="2024:05:02 00:00:00")
        out = run(str(self.dir), "--sort", "date", "--dry-run").stdout
        self.assertLess(out.index("2.jpg"), out.index("3.jpg"))
        self.assertLess(out.index("3.jpg"), out.index("1.jpg"))

    def test_end_to_end_mixed_formats(self):
        make_photo(self.dir / "1.jpg", size=(60, 30))                    # альбомне
        make_photo(self.dir / "2.png", size=(30, 60), mode="RGBA")       # з прозорістю
        make_photo(self.dir / "3.jpg", size=(40, 20), orientation=6)     # повернуте EXIF
        (self.dir / "notes.txt").write_text("не фото", encoding="utf-8")

        res = run(str(self.dir))
        self.assertEqual(res.returncode, 0, res.stderr)
        pdf = self.dir.parent / "photos.pdf"
        pages = self.pages(pdf)
        self.assertEqual(len(pages), 3)
        self.assertGreater(pages[0][0], pages[0][1])   # A4 альбомна під альбомне фото
        self.assertLess(pages[1][0], pages[1][1])

        res = run(str(self.dir), "--page", "original", "-o", str(self.dir.parent / "orig.pdf"))
        self.assertEqual(res.returncode, 0, res.stderr)
        w, h = self.pages(self.dir.parent / "orig.pdf")[2]
        self.assertLess(w, h)                           # Orientation=6 → портрет

    def test_refuses_overwrite_without_force(self):
        make_photo(self.dir / "1.jpg")
        self.assertEqual(run(str(self.dir)).returncode, 0)
        res = run(str(self.dir))
        self.assertEqual(res.returncode, 1)
        self.assertIn("--force", res.stderr)
        self.assertEqual(run(str(self.dir), "--force").returncode, 0)

    def test_empty_folder_is_error(self):
        res = run(str(self.dir))
        self.assertEqual(res.returncode, 1)
        self.assertIn("немає фото", res.stderr)


if __name__ == "__main__":
    unittest.main()
