"""Рахує файли й рядки в папці, з групуванням за розширенням.

Приклади:
    local_helper count_lines                 поточна папка, всі файли
    local_helper count_lines D:\\Projects --ext .py .md
    local_helper count_lines . --ext py --skip node_modules .venv
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_SKIP = [".git", ".venv", "venv", "node_modules", "__pycache__"]


def count_lines(path: Path) -> int:
    with path.open("rb") as f:
        return sum(1 for _ in f)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="local_helper count_lines",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", type=Path, nargs="?", default=Path("."), help="папка (за замовчуванням поточна)")
    parser.add_argument("--ext", nargs="+", default=[], help="лише ці розширення, напр. .py .md")
    parser.add_argument("--skip", nargs="+", default=DEFAULT_SKIP, help="імена папок, які пропускати")
    args = parser.parse_args()

    root = args.path
    if not root.is_dir():
        print(f"Папку не знайдено: {root}", file=sys.stderr)
        return 1

    exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in args.ext}
    skip = set(args.skip)
    files: dict[str, int] = defaultdict(int)
    lines: dict[str, int] = defaultdict(int)

    for p in root.rglob("*"):
        if not p.is_file() or skip.intersection(p.relative_to(root).parts[:-1]):
            continue
        ext = p.suffix.lower() or "(без розширення)"
        if exts and ext not in exts:
            continue
        try:
            n = count_lines(p)
        except OSError as e:
            print(f"Пропущено {p}: {e}", file=sys.stderr)
            continue
        files[ext] += 1
        lines[ext] += n

    if not files:
        print("Файлів не знайдено.")
        return 0

    width = max(len("Разом"), *(len(e) for e in files))
    for ext in sorted(files, key=lambda e: -lines[e]):
        print(f"{ext:<{width}}  {files[ext]:>6} файлів  {lines[ext]:>9} рядків")
    print(f"{'Разом':<{width}}  {sum(files.values()):>6} файлів  {sum(lines.values()):>9} рядків")
    return 0


if __name__ == "__main__":
    sys.exit(main())
