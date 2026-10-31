"""Збирає всі фото з папки в один PDF — за нумерацією в назвах або за датою зйомки.

За замовчуванням сторінки A4 (орієнтація під фото), порядок — за назвою з
натуральним сортуванням (1, 2, ..., 10, 11), результат — <папка>.pdf поруч із папкою.
JPEG вбудовуються без перестиснення. Підтримуються JPG, PNG, WEBP, BMP, TIFF, HEIC.

Приклади:
    local_helper photos_to_pdf                          поточна папка → ../<папка>.pdf
    local_helper photos_to_pdf D:\\Scans\\Паспорт --dry-run   показати порядок сторінок
    local_helper photos_to_pdf . --sort date -o trip.pdf
    local_helper photos_to_pdf . --page original --force
"""
import argparse
import io
import os
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    import img2pdf
    from PIL import Image, ImageOps
except ImportError as e:
    MISSING_DEP = e.name
else:
    MISSING_DEP = None

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_OK = True
except ImportError:
    HEIF_OK = False

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
HEIF_EXTENSIONS = {".heic", ".heif"}
LOSSLESS_EXTENSIONS = {".png", ".bmp", ".tif", ".tiff"}
A4_MM = (210, 297)
EXIF_IFD = 0x8769
TAG_DATETIME_ORIGINAL = 36867
TAG_DATETIME = 306
TAG_ORIENTATION = 274


def natural_key(path: Path) -> list:
    """'IMG_2.jpg' < 'IMG_10.jpg': числа в імені порівнюються як числа."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", path.name.lower())]


def find_photos(folder: Path) -> list[Path]:
    return [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS]


def photo_date(path: Path) -> datetime:
    """Дата зйомки з EXIF; якщо її немає — час зміни файлу."""
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            raw = exif.get_ifd(EXIF_IFD).get(TAG_DATETIME_ORIGINAL) or exif.get(TAG_DATETIME)
        if raw:
            return datetime.strptime(str(raw).strip("\x00 "), "%Y:%m:%d %H:%M:%S")
    except (OSError, ValueError):
        pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def prepare(path: Path):
    """Шлях до файлу (JPEG без змін) або байти перекодованого зображення для img2pdf."""
    with Image.open(path) as im:
        orientation = im.getexif().get(TAG_ORIENTATION, 1)
        if im.format == "JPEG" and orientation == 1 and im.mode in {"RGB", "L", "CMYK"}:
            return str(path)
        im = ImageOps.exif_transpose(im)
        if im.mode in {"RGBA", "LA", "PA"} or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            im = Image.new("RGB", rgba.size, "white")
            im.paste(rgba, mask=rgba.getchannel("A"))
        elif im.mode not in {"RGB", "L"}:
            im = im.convert("RGB")
        buf = io.BytesIO()
        if path.suffix.lower() in LOSSLESS_EXTENSIONS:
            im.save(buf, format="PNG")
        else:
            im.save(buf, format="JPEG", quality=95)
        return buf.getvalue()


def default_output(folder: Path) -> Path:
    folder = folder.resolve()
    if not folder.name:  # корінь диска
        return folder / "photos.pdf"
    return folder.parent / f"{folder.name}.pdf"


def layout(page: str, margin_mm: float):
    border = None
    if margin_mm > 0:
        m = img2pdf.mm_to_pt(margin_mm)
        border = (m, m)
    if page == "original":
        return img2pdf.get_layout_fun(border=border)
    pagesize = (img2pdf.mm_to_pt(A4_MM[0]), img2pdf.mm_to_pt(A4_MM[1]))
    return img2pdf.get_layout_fun(pagesize=pagesize, border=border,
                                  fit=img2pdf.FitMode.into, auto_orient=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="local_helper photos_to_pdf",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("folder", type=Path, nargs="?", default=Path("."),
                        help="папка з фото (за замовчуванням поточна)")
    parser.add_argument("-o", "--output", type=Path, help="шлях до PDF (за замовчуванням <папка>.pdf поруч)")
    parser.add_argument("--sort", choices=["name", "date"], default="name",
                        help="name — за нумерацією в назві (за замовчуванням), date — за датою зйомки")
    parser.add_argument("--reverse", action="store_true", help="зворотний порядок")
    parser.add_argument("--page", choices=["a4", "original"], default="a4",
                        help="a4 — фото вписане в A4 (за замовчуванням), original — сторінка за розміром фото")
    parser.add_argument("--margin", type=float, default=0, metavar="MM", help="поля в мм (за замовчуванням 0)")
    parser.add_argument("--dry-run", action="store_true", help="лише показати порядок сторінок")
    parser.add_argument("--force", action="store_true", help="перезаписати існуючий PDF")
    args = parser.parse_args()

    if MISSING_DEP:
        print(f"Не встановлено пакет «{MISSING_DEP}». Запусти: local_helper setup", file=sys.stderr)
        return 1

    folder: Path = args.folder
    if not folder.is_dir():
        print(f"Папку не знайдено: {folder}", file=sys.stderr)
        return 1

    photos = find_photos(folder)
    if not photos:
        print(f"У папці немає фото ({', '.join(sorted(EXTENSIONS))}): {folder.resolve()}", file=sys.stderr)
        return 1
    if not HEIF_OK and any(p.suffix.lower() in HEIF_EXTENSIONS for p in photos):
        print("Є HEIC-фото, але не встановлено pillow-heif. Запусти: local_helper setup", file=sys.stderr)
        return 1

    dates: dict[Path, datetime] = {}
    if args.sort == "date":
        dates = {p: photo_date(p) for p in photos}
        photos.sort(key=lambda p: (dates[p], natural_key(p)), reverse=args.reverse)
    else:
        photos.sort(key=natural_key, reverse=args.reverse)

    output = (args.output or default_output(folder)).resolve()
    if output.suffix.lower() != ".pdf":
        output = output.with_name(output.name + ".pdf")

    if args.dry_run:
        for i, p in enumerate(photos, 1):
            date = f"  {dates[p]:%Y-%m-%d %H:%M:%S}" if p in dates else ""
            print(f"{i:>4}. {p.name}{date}")
        print(f"\nСторінок: {len(photos)} → {output}" + ("  (файл існує)" if output.exists() else ""))
        return 0

    if output.exists() and not args.force:
        print(f"Файл уже існує: {output}\nДодай --force, щоб перезаписати.", file=sys.stderr)
        return 1

    images, skipped = [], 0
    for i, p in enumerate(photos, 1):
        print(f"\r[{i}/{len(photos)}] {p.name[:60]:<60}", end="", flush=True)
        try:
            images.append(prepare(p))
        except (OSError, ValueError, Image.DecompressionBombError) as e:
            print(f"\nПропущено {p.name}: {e}", file=sys.stderr)
            skipped += 1
    print("\r" + " " * 72 + "\r", end="")

    if not images:
        print("Жодне фото не вдалося прочитати.", file=sys.stderr)
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(output.name + ".part")
    try:
        with tmp.open("wb") as f:
            img2pdf.convert(images, layout_fun=layout(args.page, args.margin), outputstream=f)
        os.replace(tmp, output)
    except Exception as e:  # img2pdf кидає власні винятки на непідтримувані файли
        tmp.unlink(missing_ok=True)
        print(f"Не вдалося створити PDF: {e}", file=sys.stderr)
        return 1

    size_mb = output.stat().st_size / 1024 / 1024
    print(f"Готово: сторінок {len(images)} → {output} ({size_mb:.1f} МБ)")
    if skipped:
        print(f"Пропущено файлів: {skipped}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
