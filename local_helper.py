"""local_helper (lh) — лаунчер особистих консольних тулів.

Тули беруться з двох місць і перечитуються при кожному запуску:
  - вбудовані: tools/<name>/main.py у папці local_helper;
  - підключені з будь-якої папки на ПК командою `add` (шляхи зберігаються в registry.json).
Опис тулу — docstring на початку його головного файлу.

Приклади:
    local_helper                          інтерактивне меню
    local_helper list                     список тулів
    local_helper add "D:\\Scripts\\my_tool"  підключити тул з іншої папки
    local_helper photos_to_pdf -help      що робить тул і які параметри приймає
    local_helper photos_to_pdf . --sort date
    local_helper remove my_tool           відключити підключений тул (файли не чіпає)
    local_helper new rename_photos --group Фото   створити вбудований тул за шаблоном
    local_helper group photos_to_pdf Фото  покласти тул у групу (ungroup — прибрати)
    local_helper groups                   групи і тули в них (groups rename/delete)
    local_helper list Фото                тули однієї групи
    local_helper setup                    створити .venv і встановити залежності
"""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOLS_DIR = ROOT / "tools"
VENV_DIR = ROOT / ".venv"
REGISTRY = ROOT / "registry.json"
GROUPS = ROOT / "groups.json"
NO_GROUP = "Без групи"
RESERVED = {"list", "info", "new", "setup", "add", "remove", "help", "ui", "group", "ungroup", "groups"}
HELP_FLAGS = {"-h", "--help", "-help", "help", "?"}
CLI_MODULES = {"argparse", "click", "typer", "optparse"}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

TEMPLATE = '''"""{description}

Приклад:
    local_helper {name} --help
"""
import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="local_helper {name}",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", type=Path, nargs="?", default=Path("."), help="шлях")
    args = parser.parse_args()

    print(f"TODO: {name} для {{args.path.resolve()}}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


@dataclass
class Tool:
    name: str
    path: Path  # головний .py-файл тулу
    doc: str
    source: str = "builtin"  # builtin | added
    missing: bool = False
    group: str | None = None

    @property
    def summary(self) -> str:
        return self.doc.strip().splitlines()[0] if self.doc.strip() else "(без опису)"


def read_doc(main_py: Path) -> str:
    """Docstring модуля без імпорту; при помилці синтаксису — позначка."""
    try:
        tree = ast.parse(main_py.read_text(encoding="utf-8"), filename=str(main_py))
    except SyntaxError as e:
        return f"(помилка синтаксису: рядок {e.lineno})"
    except (OSError, UnicodeDecodeError) as e:
        return f"(не вдалося прочитати: {e})"
    return ast.get_docstring(tree) or ""


def uses_cli_parser(main_py: Path, modules: set[str] = CLI_MODULES) -> bool:
    """Чи імпортує скрипт парсер аргументів — тоді `--help` безпечно запускати."""
    try:
        tree = ast.parse(main_py.read_text(encoding="utf-8"))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module]
        else:
            continue
        if any(m.split(".")[0] in modules for m in mods):
            return True
    return False


# Запускає скрипт, але замість розбору аргументів друкує опис його argparse-парсера.
ARGS_SHIM = r'''
import argparse, json, os, runpy, sys

KINDS = {"_StoreAction": "store", "_StoreTrueAction": "store_true", "_StoreFalseAction": "store_false",
         "_StoreConstAction": "store_const", "_CountAction": "count", "_AppendAction": "append",
         "_ExtendAction": "append", "_SubParsersAction": "store"}

def _default(v):
    if v is None or v == argparse.SUPPRESS:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (list, tuple)):
        return " ".join(map(str, v))
    return str(v)

def _dump(self, *args, **kwargs):
    specs = []
    for a in self._actions:
        if isinstance(a, (argparse._HelpAction, argparse._VersionAction)):
            continue
        choices = list(a.choices) if a.choices is not None else None
        specs.append({
            "dest": a.dest, "options": list(a.option_strings),
            "kind": KINDS.get(type(a).__name__, "other"),
            "help": a.help if a.help != argparse.SUPPRESS else None,
            "default": _default(a.default), "required": bool(a.required),
            "choices": [str(c) for c in choices] if choices else None,
            "nargs": a.nargs if not isinstance(a.nargs, int) else str(a.nargs),
            "metavar": a.metavar if isinstance(a.metavar, str) else None,
        })
    sys.stdout.write("\n__LH_ARGS__" + json.dumps(specs, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    raise SystemExit(0)

argparse.ArgumentParser.parse_args = _dump
argparse.ArgumentParser.parse_known_args = _dump
path = sys.argv[1]
sys.argv = [path]
sys.path.insert(0, os.path.dirname(path))
runpy.run_path(path, run_name="__main__")
'''
ARGS_MARKER = "__LH_ARGS__"


def describe_args(tool: Tool, timeout: float = 10) -> list[dict] | None:
    """Опис аргументів argparse тулу (для форми в TUI) або None, якщо його не отримати."""
    # лише argparse: click/typer прокладка не перехопить, і скрипт виконався б повністю
    if tool.missing or not uses_cli_parser(tool.path, {"argparse"}):
        return None
    try:
        res = subprocess.run([python_exe(tool), "-c", ARGS_SHIM, str(tool.path)], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=timeout,
                             cwd=tool.path.parent, stdin=subprocess.DEVNULL,
                             env={**os.environ, "PYTHONUTF8": "1"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in reversed(res.stdout.splitlines()):
        if line.startswith(ARGS_MARKER):
            try:
                return json.loads(line[len(ARGS_MARKER):])
            except json.JSONDecodeError:
                return None
    return None


def option_flag(spec: dict) -> str:
    """Найдовша форма опції: --dry-run замість -n."""
    return max(spec["options"], key=len)


def unquote(s: str) -> str:
    s = s.strip()
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'" else s


def build_argv(specs: list[dict], values: dict, extra: str = "") -> list[str]:
    """Аргументи командного рядка зі значень форми: dest -> str | bool | None."""
    positional, optional = [], []
    for s in specs:
        v = values.get(s["dest"])
        multi = s["nargs"] in ("+", "*") or (s["nargs"] or "").isdigit()
        if not s["options"]:
            if isinstance(v, str) and v.strip():
                positional.extend(split_args(v) if multi else [unquote(v)])
            continue
        flag = option_flag(s)
        if s["kind"] in ("store_true", "store_false", "store_const", "count"):
            if v is True:
                optional.append(flag)
            continue
        if v is None or v is False or not str(v).strip():
            continue
        v = str(v).strip()
        if s["default"] is not None and v == str(s["default"]):
            continue
        if s["kind"] == "append":
            for item in split_args(v):
                optional += [flag, item]
        elif multi:
            optional += [flag, *split_args(v)]
        else:
            optional += [flag, unquote(v)]
    return positional + optional + split_args(extra)


def load_registry(registry: Path = REGISTRY) -> dict[str, dict]:
    if not registry.is_file():
        return {}
    try:
        return json.loads(registry.read_text(encoding="utf-8")).get("tools", {})
    except (json.JSONDecodeError, OSError, AttributeError) as e:
        print(f"Не вдалося прочитати {registry}: {e}", file=sys.stderr)
        return {}


def save_registry(tools: dict[str, dict], registry: Path = REGISTRY) -> None:
    tmp = registry.with_name(registry.name + ".tmp")
    tmp.write_text(json.dumps({"tools": dict(sorted(tools.items()))}, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, registry)


def load_groups(groups_file: Path = GROUPS) -> dict[str, str]:
    """Ім'я тулу -> назва групи."""
    if not groups_file.is_file():
        return {}
    try:
        data = json.loads(groups_file.read_text(encoding="utf-8")).get("tools", {})
        return {k: v for k, v in data.items() if isinstance(v, str) and v.strip()}
    except (json.JSONDecodeError, OSError, AttributeError) as e:
        print(f"Не вдалося прочитати {groups_file}: {e}", file=sys.stderr)
        return {}


def save_groups(mapping: dict[str, str], groups_file: Path = GROUPS) -> None:
    tmp = groups_file.with_name(groups_file.name + ".tmp")
    tmp.write_text(json.dumps({"tools": dict(sorted(mapping.items()))}, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, groups_file)


def clean_group(name: str | None) -> str:
    return " ".join((name or "").split())


def assign_group(tool_name: str, group: str | None, groups_file: Path = GROUPS) -> str | None:
    """Покласти тул у групу (порожня назва — прибрати з групи). Повертає фактичну назву групи."""
    mapping = load_groups(groups_file)
    group = clean_group(group)
    if group:
        # «фото» і «Фото» — одна група: беремо вже існуюче написання
        group = next((g for g in mapping.values() if g.casefold() == group.casefold()), group)
        mapping[tool_name] = group
    else:
        mapping.pop(tool_name, None)
    save_groups(mapping, groups_file)
    return group or None


def ordered(tools: dict[str, Tool]) -> list[tuple[str, list[Tool]]]:
    """Тули по групах: групи за алфавітом, «Без групи» — в кінці."""
    buckets: dict[str, list[Tool]] = {}
    for t in tools.values():
        buckets.setdefault(t.group or NO_GROUP, []).append(t)
    keys = sorted((g for g in buckets if g != NO_GROUP), key=str.casefold)
    if NO_GROUP in buckets:
        keys.append(NO_GROUP)
    return [(g, sorted(buckets[g], key=lambda t: t.name)) for g in keys]


def flat(tools: dict[str, Tool]) -> list[Tool]:
    return [t for _, group in ordered(tools) for t in group]


def discover(tools_dir: Path = TOOLS_DIR, registry: Path = REGISTRY,
             groups_file: Path = GROUPS) -> dict[str, Tool]:
    tools: dict[str, Tool] = {}
    if tools_dir.is_dir():
        for d in sorted(tools_dir.iterdir(), key=lambda p: p.name.lower()):
            if not d.is_dir() or d.name.startswith(("_", ".")):
                continue
            main_py = d / "main.py"
            if main_py.is_file():
                tools[d.name] = Tool(d.name, main_py, read_doc(main_py))
    for name, rec in sorted(load_registry(registry).items()):
        if name in tools:
            continue
        entry = Path(rec.get("path", "")) / rec.get("entry", "main.py")
        missing = not entry.is_file()
        doc = rec.get("doc", "") if missing else read_doc(entry)
        tools[name] = Tool(name, entry, doc, source="added", missing=missing)
    groups = load_groups(groups_file)
    for t in tools.values():
        t.group = groups.get(t.name)
    return tools


def normalize(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", name.strip().lower())


def suggest(name: str, names: list[str]) -> list[str]:
    return difflib.get_close_matches(normalize(name), names, n=3, cutoff=0.6)


def name_error(name: str) -> str | None:
    if not NAME_RE.match(name):
        return f"Ім'я «{name}» не підходить: лише латиниця a-z, цифри та _, починається з літери."
    if name in RESERVED:
        return f"Ім'я «{name}» зарезервоване командою local_helper."
    return None


def python_exe(tool: Tool | None = None) -> str:
    sub = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    candidates = [VENV_DIR / sub]
    if tool is not None and tool.source == "added":
        candidates.insert(0, tool.path.parent / ".venv" / sub)
    for py in candidates:
        if py.is_file():
            return str(py)
    return sys.executable


def split_args(line: str) -> list[str]:
    """Розбір рядка аргументів без поїдання '\\' у Windows-шляхах."""
    parts = shlex.split(line, posix=False)
    return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]


def run_tool(tool: Tool, args: list[str]) -> int:
    if tool.missing:
        print(f"Файл тулу «{tool.name}» не знайдено: {tool.path}\n"
              f"Якщо тул перенесли: local_helper remove {tool.name}, потім local_helper add <новий шлях>",
              file=sys.stderr)
        return 1
    env = {**os.environ, "PYTHONUTF8": "1"}
    try:
        return subprocess.call([python_exe(tool), str(tool.path), *args], env=env)
    except KeyboardInterrupt:
        return 130


def find_tool(name: str, tools: dict[str, Tool]) -> Tool | None:
    tool = tools.get(normalize(name))
    if tool is None:
        print(f"Тул «{name}» не знайдено.", file=sys.stderr)
        hints = suggest(name, list(tools))
        if hints:
            print(f"Можливо: {', '.join(hints)}", file=sys.stderr)
        else:
            print("Список тулів: local_helper list", file=sys.stderr)
    return tool


def tags(tool: Tool) -> str:
    out = []
    if tool.source == "added":
        out.append("[added]")
    if tool.missing:
        out.append("[не знайдено]")
    return " ".join(out)


def cmd_list(tools: dict[str, Tool], group: str | None = None) -> int:
    if not tools:
        print("Тулів поки немає. Створи: local_helper new <name> або підключи: local_helper add <шлях>")
        return 0
    sections = ordered(tools)
    has_groups = any(g != NO_GROUP for g, _ in sections)
    if group:
        wanted = clean_group(group).casefold()
        sections = [s for s in sections if s[0].casefold() == wanted]
        if not sections:
            print(f"Групу «{group}» не знайдено. Групи: {', '.join(g for g, _ in ordered(tools))}",
                  file=sys.stderr)
            return 1
    width = max(len(n) for n in tools)
    i = 0
    for g, group_tools in sections:
        if has_groups:
            if i:
                print()
            print(g)
        for t in group_tools:
            i += 1
            tag = f"  {tags(t)}" if tags(t) else ""
            print(f"{i:>3}. {t.name:<{width}}  {t.summary}{tag}")
    return 0


def cmd_group(name: str, group: str | None, tools_dir: Path = TOOLS_DIR, registry: Path = REGISTRY,
              groups_file: Path = GROUPS) -> int:
    tool = find_tool(name, discover(tools_dir, registry, groups_file))
    if tool is None:
        return 2
    g = assign_group(tool.name, group, groups_file)
    print(f"«{tool.name}» → група «{g}»" if g else f"«{tool.name}» прибрано з групи")
    return 0


def cmd_groups(args: list[str], tools: dict[str, Tool], groups_file: Path = GROUPS) -> int:
    if not args:
        for g, group_tools in ordered(tools):
            print(f"{g} ({len(group_tools)}): {', '.join(t.name for t in group_tools)}")
        print("\nПокласти тул у групу: local_helper group <tool> <група>")
        return 0
    action, rest = args[0], args[1:]
    mapping = load_groups(groups_file)
    if action == "rename" and len(rest) == 2:
        old, new = clean_group(rest[0]).casefold(), clean_group(rest[1])
        hits = [k for k, v in mapping.items() if v.casefold() == old]
        if not hits or not new:
            print(f"Групу «{rest[0]}» не знайдено." if not hits else "Нова назва порожня.", file=sys.stderr)
            return 1
        for k in hits:
            mapping[k] = new
        save_groups(mapping, groups_file)
        print(f"Групу «{rest[0]}» перейменовано на «{new}» (тулів у ній: {len(hits)}).")
        return 0
    if action == "delete" and len(rest) == 1:
        old = clean_group(rest[0]).casefold()
        hits = [k for k, v in mapping.items() if v.casefold() == old]
        if not hits:
            print(f"Групу «{rest[0]}» не знайдено.", file=sys.stderr)
            return 1
        for k in hits:
            del mapping[k]
        save_groups(mapping, groups_file)
        print(f"Групу «{rest[0]}» видалено; тули ({', '.join(hits)}) тепер без групи. Самі тули не змінено.")
        return 0
    print("Використання: local_helper groups | groups rename <стара> <нова> | groups delete <група>",
          file=sys.stderr)
    return 2


def cmd_help(tool: Tool) -> int:
    source = "підключений" if tool.source == "added" else "вбудований"
    print(f"{tool.name}  ({source})")
    print(f"Файл: {tool.path}" + ("  [не знайдено]" if tool.missing else ""))
    print("-" * 60)
    doc = tool.doc or "(без опису — додай docstring на початок файлу)"
    if tool.missing:
        print(doc)
        print("-" * 60)
        print("Файл тулу зник, параметри недоступні.")
        return 1
    if not uses_cli_parser(tool.path):
        print(doc)
        print("-" * 60)
        print("Параметри не описані: тул не використовує argparse/click/typer.")
        return 0
    res = subprocess.run([python_exe(tool), str(tool.path), "--help"], capture_output=True,
                         text=True, encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUTF8": "1"})
    params = (res.stdout or res.stderr).strip()
    # argparse з description=__doc__ уже містить опис — не дублюємо його
    if not tool.doc or " ".join(tool.doc.split()) not in " ".join(params.split()):
        print(doc)
        print("-" * 60)
    print(params)
    return 0


def find_entry(folder: Path) -> tuple[Path | None, str]:
    for n in ("main.py", "__main__.py"):
        if (folder / n).is_file():
            return folder / n, ""
    pys = sorted(p.name for p in folder.glob("*.py"))
    if len(pys) == 1:
        return folder / pys[0], ""
    if not pys:
        return None, f"У папці немає .py-файлів: {folder}"
    return None, f"У папці кілька .py ({', '.join(pys)}) — вкажи головний: --entry <файл.py>"


def cmd_add(path: str, name: str | None = None, entry: str | None = None,
            tools_dir: Path = TOOLS_DIR, registry: Path = REGISTRY,
            group: str | None = None, groups_file: Path = GROUPS) -> int:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        print(f"Шлях не знайдено: {p}", file=sys.stderr)
        return 1
    if p.is_file():
        if p.suffix.lower() != ".py":
            print(f"Це не .py-файл: {p}", file=sys.stderr)
            return 1
        entry_path, default_name = p, p.stem
    else:
        default_name = p.name
        if entry:
            entry_path = p / entry
            if not entry_path.is_file():
                print(f"Файл не знайдено: {entry_path}", file=sys.stderr)
                return 1
        else:
            entry_path, err = find_entry(p)
            if entry_path is None:
                print(err, file=sys.stderr)
                return 1

    name = normalize(name or default_name)
    err = name_error(name)
    if err:
        print(f"{err}\nВкажи інше ім'я: local_helper add \"{path}\" --name <ім'я>", file=sys.stderr)
        return 2
    tools = discover(tools_dir, registry, groups_file)
    if name in tools:
        t = tools[name]
        hint = f"local_helper remove {name}" if t.source == "added" else "інше ім'я"
        print(f"Тул «{name}» уже є: {t.path}\nВкажи --name <ім'я> або спершу {hint}.", file=sys.stderr)
        return 1

    doc = read_doc(entry_path)
    reg = load_registry(registry)
    reg[name] = {"path": str(entry_path.parent), "entry": entry_path.name,
                 "doc": doc, "added": date.today().isoformat()}
    save_registry(reg, registry)
    print(f"Додано тул «{name}»: {entry_path}")
    print(f"Опис: {Tool(name, entry_path, doc).summary}")
    if clean_group(group):
        print(f"Група: {assign_group(name, group, groups_file)}")
    print(f"Довідка: local_helper {name} -help")
    return 0


def cmd_remove(name: str, tools_dir: Path = TOOLS_DIR, registry: Path = REGISTRY,
               groups_file: Path = GROUPS) -> int:
    key = normalize(name)
    reg = load_registry(registry)
    if key in reg:
        rec = reg.pop(key)
        save_registry(reg, registry)
        if key in load_groups(groups_file):
            assign_group(key, None, groups_file)
        print(f"Тул «{key}» відключено (файли в {rec.get('path')} не змінено).")
        return 0
    tools = discover(tools_dir, registry, groups_file)
    if key in tools:
        print(f"«{key}» — вбудований тул ({tools[key].path.parent}). "
              "Щоб прибрати його, видали цю папку.", file=sys.stderr)
        return 1
    find_tool(name, tools)
    return 2


def cmd_new(name: str, tools_dir: Path = TOOLS_DIR, description: str | None = None,
            group: str | None = None, groups_file: Path = GROUPS) -> int:
    name = normalize(name)
    err = name_error(name)
    if err:
        print(err, file=sys.stderr)
        return 2
    target = tools_dir / name
    if target.exists():
        print(f"Тул «{name}» уже існує: {target}", file=sys.stderr)
        return 1
    target.mkdir(parents=True)
    main_py = target / "main.py"
    description = " ".join((description or "").replace('"""', "").split()).replace("\\", "\\\\")
    description = description or "TODO: короткий опис тулу."
    main_py.write_text(TEMPLATE.format(name=name, description=description), encoding="utf-8")
    print(f"Створено {main_py}")
    if clean_group(group):
        print(f"Група: {assign_group(name, group, groups_file)}")
    print(f"Допиши опис і логіку, потім перевір: local_helper {name} -help")
    return 0


def cmd_setup(tools: dict[str, Tool]) -> int:
    if not VENV_DIR.exists():
        print(f"Створюю {VENV_DIR} ...")
        if subprocess.call([sys.executable, "-m", "venv", str(VENV_DIR)]) != 0:
            return 1
    reqs = [ROOT / "requirements.txt", *sorted(TOOLS_DIR.glob("*/requirements.txt"))]
    for t in tools.values():
        folder = t.path.parent
        if t.source == "added" and not t.missing and not (folder / ".venv").exists():
            reqs.append(folder / "requirements.txt")
    py = python_exe()
    for req in reqs:
        lines = req.read_text(encoding="utf-8").splitlines() if req.is_file() else []
        if not any(l.strip() and not l.strip().startswith("#") for l in lines):
            continue
        print(f"Встановлюю {req} ...")
        if subprocess.call([py, "-m", "pip", "install", "-r", str(req)]) != 0:
            return 1
    print("Готово.")
    return 0


def menu(tools: dict[str, Tool]) -> int:
    if not tools:
        return cmd_list(tools)
    names = [t.name for t in flat(tools)]  # той самий порядок, що й у cmd_list
    while True:
        print()
        cmd_list(tools)
        try:
            choice = input("\nНомер або ім'я тулу (q — вихід): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice.lower() in {"q", "quit", "exit", ""}:
            return 0
        if choice.isdigit() and 1 <= int(choice) <= len(names):
            tool = tools[names[int(choice) - 1]]
        else:
            tool = find_tool(choice, tools)
            if tool is None:
                continue
        print(f"\n{tool.name}: {tool.summary}")
        try:
            line = input("Аргументи (Enter — без аргументів, ? — довідка): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if line in HELP_FLAGS:
            cmd_help(tool)
            continue
        code = run_tool(tool, split_args(line))
        print(f"\n[{tool.name}] завершено з кодом {code}")


def parse_add(args: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="local_helper add",
                                     description="Підключити тул з будь-якої папки (шлях запам'ятовується).")
    parser.add_argument("path", help="папка тулу або .py-файл")
    parser.add_argument("--name", help="ім'я команди (за замовчуванням — ім'я папки/файлу)")
    parser.add_argument("--entry", help="головний .py-файл у папці, якщо їх кілька")
    parser.add_argument("--group", help="група, в яку покласти тул")
    return parser.parse_args(args)


def parse_new(args: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="local_helper new", description="Створити вбудований тул за шаблоном.")
    parser.add_argument("name", help="ім'я команди (латиниця, a-z 0-9 _)")
    parser.add_argument("--description", help="короткий опис (перший рядок docstring)")
    parser.add_argument("--group", help="група, в яку покласти тул")
    return parser.parse_args(args)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    tools = discover()

    if not argv or argv == ["ui"]:
        try:
            import local_helper_tui
        except ImportError as e:
            print(f"Графічний інтерфейс недоступний ({e.name} не встановлено). Запусти: lh setup\n",
                  file=sys.stderr)
            return menu(tools)
        return local_helper_tui.run()
    head, rest = argv[0], argv[1:]
    if head in {"-h", "--help", "-help"} or (head == "help" and not rest):
        print(__doc__)
        return 0
    if head == "list":
        return cmd_list(tools, " ".join(rest) or None)
    if head == "setup":
        return cmd_setup(tools)
    if head == "add":
        a = parse_add(rest)
        return cmd_add(a.path, a.name, a.entry, group=a.group)
    if head == "new":
        a = parse_new(rest)
        return cmd_new(a.name, description=a.description, group=a.group)
    if head == "groups":
        return cmd_groups(rest, tools)
    if head == "group":
        if len(rest) < 2:
            print("Використання: local_helper group <tool> <група>   (прибрати: local_helper ungroup <tool>)",
                  file=sys.stderr)
            return 2
        return cmd_group(rest[0], " ".join(rest[1:]))
    if head in {"info", "help", "remove", "ungroup"}:
        if len(rest) != 1:
            print(f"Використання: local_helper {head} <name>", file=sys.stderr)
            return 2
        if head == "ungroup":
            return cmd_group(rest[0], None)
        if head == "remove":
            return cmd_remove(rest[0])
        tool = find_tool(rest[0], tools)
        return cmd_help(tool) if tool else 2

    tool = find_tool(head, tools)
    if tool is None:
        return 2
    if len(rest) == 1 and rest[0] in HELP_FLAGS:
        return cmd_help(tool)
    return run_tool(tool, rest)


if __name__ == "__main__":
    sys.exit(main())
