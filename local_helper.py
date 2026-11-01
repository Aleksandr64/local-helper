"""local_helper (lh) — лаунчер особистих консольних тулів.

Тули беруться з двох місць і перечитуються при кожному запуску:
  - вбудовані: tools/<name>/main.py (або main.ps1, main.sh) у папці local_helper;
  - підключені з будь-якої папки на ПК командою `add` (шляхи зберігаються в registry.json).
Тул — скрипт на Python (.py), PowerShell (.ps1) або Bash (.sh). Опис тулу — docstring
(.py), comment-based help <# .SYNOPSIS #> (.ps1) або #-коментар (.sh) на початку файлу.

Приклади:
    local_helper                          інтерактивне меню
    local_helper list                     список тулів
    local_helper add "D:\\Scripts\\my_tool"  підключити тул з іншої папки
    local_helper photos_to_pdf -help      що робить тул і які параметри приймає
    local_helper photos_to_pdf . --sort date
    local_helper remove my_tool           відключити підключений тул (файли не чіпає)
    local_helper new rename_photos --group Фото   створити вбудований тул за шаблоном
    local_helper new backup --lang ps1    тул на PowerShell (sh — на Bash)
    local_helper workdir backup "D:\Data"  завжди запускати тул у цій папці (--clear — прибрати)
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
import shutil
import subprocess
import textwrap
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOLS_DIR = ROOT / "tools"
VENV_DIR = ROOT / ".venv"
REGISTRY = ROOT / "registry.json"
GROUPS = ROOT / "groups.json"
WORKDIRS = ROOT / "workdirs.json"
NO_GROUP = "Без групи"
RESERVED = {"list", "info", "new", "setup", "add", "remove", "help", "ui", "group", "ungroup", "groups",
            "workdir"}
KINDS = {".py": "py", ".ps1": "ps1", ".sh": "sh"}  # розширення -> тип тулу
KIND_LABELS = {"py": "Python", "ps1": "PowerShell", "sh": "Bash"}
NO_INTERPRETER = {
    "ps1": "Для .ps1-тулів потрібен PowerShell (pwsh або powershell) у PATH.",
    "sh": "Для .sh-тулів потрібен Git Bash: https://git-scm.com/download/win",
}
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

TEMPLATE_PS1 = '''<#
.SYNOPSIS
    {description}

.DESCRIPTION
    TODO: що саме робить тул.

.PARAMETER Path
    шлях

.PARAMETER DryRun
    лише показати, що буде зроблено

.EXAMPLE
    local_helper {name} -Path . -DryRun
#>
param(
    [string]$Path = ".",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$target = Resolve-Path -LiteralPath $Path -ErrorAction SilentlyContinue
if (-not $target) {{
    [Console]::Error.WriteLine("Шлях не знайдено: $Path")
    exit 1
}}

Write-Output "TODO: {name} для $target"
exit 0
'''

TEMPLATE_SH = '''#!/usr/bin/env bash
# {description}
#
# TODO: що саме робить тул.
#
# Параметри:
#   [шлях]      папка (за замовчуванням — поточна)
#   --dry-run   лише показати, що буде зроблено
#
# Приклад:
#   local_helper {name} . --dry-run
set -euo pipefail

path="."
dry_run=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) dry_run=1 ;;
        -*) echo "Невідомий параметр: $1" >&2; exit 2 ;;
        *) path="$1" ;;
    esac
    shift
done

if [[ ! -e "$path" ]]; then
    echo "Шлях не знайдено: $path" >&2
    exit 1
fi

echo "TODO: {name} для $(cd "$path" && pwd) (dry_run=$dry_run)"
'''

TEMPLATES = {"py": ("main.py", TEMPLATE), "ps1": ("main.ps1", TEMPLATE_PS1), "sh": ("main.sh", TEMPLATE_SH)}


@dataclass
class Tool:
    name: str
    path: Path  # головний файл тулу: .py, .ps1 або .sh
    doc: str
    source: str = "builtin"  # builtin | added
    missing: bool = False
    group: str | None = None
    workdir: Path | None = None  # закріплена папка запуску (workdirs.json)

    @property
    def summary(self) -> str:
        return self.doc.strip().splitlines()[0] if self.doc.strip() else "(без опису)"

    @property
    def kind(self) -> str:
        return KINDS.get(self.path.suffix.lower(), "py")


def read_doc(path: Path) -> str:
    """Опис тулу з початку файлу, без запуску скрипта."""
    kind = KINDS.get(path.suffix.lower(), "py")
    if kind == "ps1":
        return ps1_help(path)[0]
    if kind == "sh":
        return sh_help(path)
    return py_doc(path)


def read_script(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


def ps1_help(path: Path) -> tuple[str, dict[str, str]]:
    """Comment-based help .ps1 (<# .SYNOPSIS ... #>) -> (опис, {параметр: пояснення})."""
    text = read_script(path)
    if text is None:
        return "(не вдалося прочитати файл)", {}
    lines = text.splitlines()
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().lower().startswith(("#requires", "#!"))):
        i += 1
    if i < len(lines) and lines[i].lstrip().startswith("<#"):
        end = text.find("#>", text.find("<#"))
        block = text[text.find("<#") + 2:end if end >= 0 else len(text)]
    else:
        block = sh_comment(lines[i:])
    sections: list[tuple[str, str, list[str]]] = []  # (ключове слово, аргумент, рядки)
    head: list[str] = []
    for line in block.splitlines():
        m = re.match(r"^\s*\.([A-Za-z]+)\s*(.*)$", line)
        if m and m.group(1).upper() in {"SYNOPSIS", "DESCRIPTION", "PARAMETER", "EXAMPLE", "NOTES",
                                         "INPUTS", "OUTPUTS", "LINK", "COMPONENT", "ROLE", "FUNCTIONALITY"}:
            sections.append((m.group(1).upper(), m.group(2).strip(), []))
        elif sections:
            sections[-1][2].append(line)
        else:
            head.append(line)

    def body(rows: list[str]) -> str:
        return textwrap.dedent("\n".join(rows)).strip()

    if not sections:
        return body(head), {}
    parts, examples, params = [], [], {}
    for key, arg, rows in sections:
        if key in ("SYNOPSIS", "DESCRIPTION") and body(rows):
            parts.append(body(rows))
        elif key == "EXAMPLE" and body(rows):
            examples.append(body(rows))
        elif key == "PARAMETER" and arg:
            params[arg.lower()] = " ".join(body(rows).split())
    if examples:
        parts.append("Приклади:\n" + "\n".join(textwrap.indent(e, "    ") for e in examples))
    return "\n\n".join(parts), params


def sh_comment(lines: list[str]) -> str:
    """Суцільний блок #-коментарів на початку (без shebang)."""
    out = []
    for line in lines:
        s = line.strip()
        if s.startswith("#!") and not out:
            continue
        if not s.startswith("#"):
            if out or s:
                break
            continue
        out.append(s[2:] if s.startswith("# ") else s[1:])
    return "\n".join(out).strip()


def sh_help(path: Path) -> str:
    text = read_script(path)
    return "(не вдалося прочитати файл)" if text is None else sh_comment(text.splitlines())


def py_doc(main_py: Path) -> str:
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

# Читає param() .ps1 через парсер PowerShell — сам скрипт не виконується.
PS_ARGS_SHIM = r'''
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:LH_SCRIPT, [ref]$tokens, [ref]$errors)
$specs = @()
if ($ast.ParamBlock) {
    foreach ($p in $ast.ParamBlock.Parameters) {
        $required = $false; $choices = $null
        foreach ($a in $p.Attributes) {
            if ($a -isnot [System.Management.Automation.Language.AttributeAst]) { continue }
            $t = $a.TypeName.GetReflectionAttributeType()
            if ($t -eq [System.Management.Automation.ParameterAttribute]) {
                foreach ($na in $a.NamedArguments) {
                    if ($na.ArgumentName -eq "Mandatory") {
                        $required = $na.ExpressionOmitted -or [bool]$na.Argument.SafeGetValue()
                    }
                }
            } elseif ($t -eq [System.Management.Automation.ValidateSetAttribute]) {
                $choices = @($a.PositionalArguments | ForEach-Object { [string]$_.SafeGetValue() })
            }
        }
        $default = $null
        if ($p.DefaultValue) {
            try { $default = $p.DefaultValue.SafeGetValue() } catch { $default = $p.DefaultValue.Extent.Text }
            if ($default -is [array]) { $default = $default -join " " }
            if ($null -ne $default) { $default = [string]$default }
        }
        $name = $p.Name.VariablePath.UserPath
        $switch = $p.StaticType -eq [switch]
        $specs += [ordered]@{
            dest = $name; options = @("-$name"); kind = $(if ($switch) { "store_true" } else { "store" })
            help = $null; default = $(if ($switch) { $null } else { $default }); required = $required
            choices = $choices; nargs = $null; metavar = $null
        }
    }
}
[Console]::Out.Write("`n__LH_ARGS__" + (ConvertTo-Json -InputObject @($specs) -Compress -Depth 4) + "`n")
'''


def describe_args(tool: Tool, timeout: float = 10) -> list[dict] | None:
    """Опис аргументів тулу (для форми в TUI) або None, якщо його не отримати."""
    if tool.missing or tool.kind == "sh":
        return None
    if tool.kind == "ps1":
        return describe_ps1_args(tool, timeout)
    # лише argparse: click/typer прокладка не перехопить, і скрипт виконався б повністю
    if not uses_cli_parser(tool.path, {"argparse"}):
        return None
    try:
        res = subprocess.run([python_exe(tool), "-c", ARGS_SHIM, str(tool.path)], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=timeout,
                             cwd=tool.path.parent, stdin=subprocess.DEVNULL,
                             env={**os.environ, "PYTHONUTF8": "1"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_args_marker(res.stdout)


def describe_ps1_args(tool: Tool, timeout: float = 15) -> list[dict] | None:
    pwsh = find_pwsh()
    if pwsh is None:
        return None
    try:
        res = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", PS_ARGS_SHIM],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=timeout, stdin=subprocess.DEVNULL,
                             env={**os.environ, "LH_SCRIPT": str(tool.path)})
    except (OSError, subprocess.TimeoutExpired):
        return None
    specs = parse_args_marker(res.stdout)
    if specs is not None:
        params = ps1_help(tool.path)[1]
        for s in specs:
            s["help"] = params.get(s["dest"].lower())
    return specs


def parse_args_marker(stdout: str) -> list[dict] | None:
    for line in reversed(stdout.splitlines()):
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
    save_registry(mapping, groups_file)  # той самий формат {"tools": {...}}


def load_workdirs(workdirs_file: Path = WORKDIRS) -> dict[str, str]:
    """Ім'я тулу -> закріплена папка запуску."""
    return load_groups(workdirs_file)


def set_workdir(tool_name: str, folder: Path | None, workdirs_file: Path = WORKDIRS) -> None:
    """Закріпити папку запуску за тулом (None — прибрати)."""
    mapping = load_workdirs(workdirs_file)
    if folder is None:
        if tool_name not in mapping:
            return
        mapping.pop(tool_name)
    else:
        mapping[tool_name] = str(folder)
    save_registry(mapping, workdirs_file)


def resolve_workdir(folder: str) -> tuple[Path | None, str]:
    """Папка для `workdir`: відносна — від поточної; має існувати."""
    p = Path(unquote(folder)).expanduser().resolve()
    if not p.is_dir():
        return None, f"Папку не знайдено: {p}"
    return p, ""


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
             groups_file: Path = GROUPS, workdirs_file: Path = WORKDIRS) -> dict[str, Tool]:
    tools: dict[str, Tool] = {}
    if tools_dir.is_dir():
        for d in sorted(tools_dir.iterdir(), key=lambda p: p.name.lower()):
            if not d.is_dir() or d.name.startswith(("_", ".")):
                continue
            main = next((d / f"main{ext}" for ext in KINDS if (d / f"main{ext}").is_file()), None)
            if main is not None:
                tools[d.name] = Tool(d.name, main, read_doc(main))
    for name, rec in sorted(load_registry(registry).items()):
        if name in tools:
            continue
        entry = Path(rec.get("path", "")) / rec.get("entry", "main.py")
        missing = not entry.is_file()
        doc = rec.get("doc", "") if missing else read_doc(entry)
        tools[name] = Tool(name, entry, doc, source="added", missing=missing)
    groups = load_groups(groups_file)
    workdirs = load_workdirs(workdirs_file)
    for t in tools.values():
        t.group = groups.get(t.name)
        t.workdir = Path(workdirs[t.name]) if t.name in workdirs else None
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


def find_pwsh() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def find_bash() -> str | None:
    """Git Bash на Windows (System32\\bash.exe — це WSL, він Windows-шляхів не розуміє)."""
    if os.name != "nt":
        return shutil.which("bash")
    candidates = []
    git = shutil.which("git")
    if git:
        candidates.append(Path(git).resolve().parent.parent / "bin" / "bash.exe")  # ...\Git\cmd\git.exe
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            candidates.append(Path(base) / ("Programs/Git" if var == "LOCALAPPDATA" else "Git") / "bin" / "bash.exe")
    for c in candidates:
        if c.is_file():
            return str(c)
    found = shutil.which("bash")
    if found and not any(part in found.lower() for part in ("\\system32\\", "\\windowsapps\\")):
        return found
    return None


def tool_command(tool: Tool) -> list[str] | None:
    """Команда запуску тулу без його аргументів; None — немає потрібного інтерпретатора."""
    if tool.kind == "ps1":
        pwsh = find_pwsh()
        return [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(tool.path)] if pwsh else None
    if tool.kind == "sh":
        bash = find_bash()
        return [bash, tool.path.as_posix()] if bash else None
    return [python_exe(tool), str(tool.path)]


def launch_error(tool: Tool) -> str | None:
    """Чому тул не можна запустити (або None, якщо можна)."""
    if tool.missing:
        return (f"Файл тулу «{tool.name}» не знайдено: {tool.path}\n"
                f"Якщо тул перенесли: local_helper remove {tool.name}, потім local_helper add <новий шлях>")
    if tool.workdir is not None and not tool.workdir.is_dir():
        return (f"Робочу папку тулу «{tool.name}» не знайдено: {tool.workdir}\n"
                f"Задай іншу: local_helper workdir {tool.name} <папка>  або прибери: --clear")
    if tool_command(tool) is None:
        return NO_INTERPRETER[tool.kind]
    return None


def tool_env() -> dict[str, str]:
    return {**os.environ, "PYTHONUTF8": "1"}


def split_args(line: str) -> list[str]:
    """Розбір рядка аргументів без поїдання '\\' у Windows-шляхах."""
    parts = shlex.split(line, posix=False)
    return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]


def run_tool(tool: Tool, args: list[str]) -> int:
    err = launch_error(tool)
    if err:
        print(err, file=sys.stderr)
        return 1
    try:
        return subprocess.call([*tool_command(tool), *args], env=tool_env(), cwd=tool.workdir)
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
    if tool.kind != "py":
        out.append(f"[{tool.kind}]")
    if tool.workdir is not None:
        out.append("[папка]")
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
              groups_file: Path = GROUPS, workdirs_file: Path = WORKDIRS) -> int:
    tool = find_tool(name, discover(tools_dir, registry, groups_file, workdirs_file))
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


def format_params(specs: list[dict]) -> str:
    """Таблиця параметрів для -help тулів без власного --help (.ps1)."""
    if not specs:
        return "Параметрів немає."
    rows = []
    for s in specs:
        flag = option_flag(s) if s["options"] else (s["metavar"] or s["dest"])
        if s["options"] and s["kind"] not in ("store_true", "store_false", "store_const", "count"):
            flag += f" <{s['metavar'] or s['dest']}>"
        notes = [s["help"] or ""]
        if s["choices"]:
            notes.append("варіанти: " + ", ".join(s["choices"]))
        if s["default"] not in (None, "", False):
            notes.append(f"за замовч.: {s['default']}")
        if s["required"]:
            notes.append("обов'язковий")
        rows.append((flag, "; ".join(n for n in notes if n)))
    width = max(len(f) for f, _ in rows)
    return "Параметри:\n" + "\n".join(f"  {f:<{width}}  {n}".rstrip() for f, n in rows)


def cmd_help(tool: Tool) -> int:
    source = "підключений" if tool.source == "added" else "вбудований"
    kind = "" if tool.kind == "py" else f", {KIND_LABELS[tool.kind]}"
    print(f"{tool.name}  ({source}{kind})")
    print(f"Файл: {tool.path}" + ("  [не знайдено]" if tool.missing else ""))
    if tool.workdir is not None:
        print(f"Робоча папка: {tool.workdir}" + ("" if tool.workdir.is_dir() else "  [не знайдено]"))
    print("-" * 60)
    doc = tool.doc or "(без опису — додай опис на початок файлу)"
    if tool.missing:
        print(doc)
        print("-" * 60)
        print("Файл тулу зник, параметри недоступні.")
        return 1
    if tool.kind in ("ps1", "sh"):
        print(doc)
        print("-" * 60)
        if tool.kind == "sh":
            print("Аргументи передаються скрипту як є (див. опис вище).")
            return 0
        specs = describe_args(tool)
        print(format_params(specs) if specs is not None else
              "Не вдалося прочитати param(): " + (NO_INTERPRETER["ps1"] if find_pwsh() is None
                                                  else "перевір синтаксис скрипта."))
        return 0
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
    for n in ("main.py", "__main__.py", "main.ps1", "main.sh"):
        if (folder / n).is_file():
            return folder / n, ""
    scripts = sorted(p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() in KINDS)
    if len(scripts) == 1:
        return folder / scripts[0], ""
    if not scripts:
        return None, f"У папці немає скриптів (.py, .ps1, .sh): {folder}"
    return None, f"У папці кілька скриптів ({', '.join(scripts)}) — вкажи головний: --entry <файл>"


def cmd_add(path: str, name: str | None = None, entry: str | None = None,
            tools_dir: Path = TOOLS_DIR, registry: Path = REGISTRY,
            group: str | None = None, groups_file: Path = GROUPS,
            workdir: str | None = None, workdirs_file: Path = WORKDIRS) -> int:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        print(f"Шлях не знайдено: {p}", file=sys.stderr)
        return 1
    folder = None
    if workdir:
        folder, err = resolve_workdir(workdir)
        if folder is None:
            print(err, file=sys.stderr)
            return 1
    if p.is_file():
        if p.suffix.lower() not in KINDS:
            print(f"Непідтримуваний тип файлу: {p} (потрібен .py, .ps1 або .sh)", file=sys.stderr)
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
        if entry_path.suffix.lower() not in KINDS:
            print(f"Непідтримуваний тип файлу: {entry_path} (потрібен .py, .ps1 або .sh)", file=sys.stderr)
            return 1

    name = normalize(name or default_name)
    err = name_error(name)
    if err:
        print(f"{err}\nВкажи інше ім'я: local_helper add \"{path}\" --name <ім'я>", file=sys.stderr)
        return 2
    tools = discover(tools_dir, registry, groups_file, workdirs_file)
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
    if folder is not None:
        set_workdir(name, folder, workdirs_file)
        print(f"Робоча папка: {folder}")
    print(f"Довідка: local_helper {name} -help")
    return 0


def cmd_remove(name: str, tools_dir: Path = TOOLS_DIR, registry: Path = REGISTRY,
               groups_file: Path = GROUPS, workdirs_file: Path = WORKDIRS) -> int:
    key = normalize(name)
    reg = load_registry(registry)
    if key in reg:
        rec = reg.pop(key)
        save_registry(reg, registry)
        if key in load_groups(groups_file):
            assign_group(key, None, groups_file)
        set_workdir(key, None, workdirs_file)
        print(f"Тул «{key}» відключено (файли в {rec.get('path')} не змінено).")
        return 0
    tools = discover(tools_dir, registry, groups_file, workdirs_file)
    if key in tools:
        print(f"«{key}» — вбудований тул ({tools[key].path.parent}). "
              "Щоб прибрати його, видали цю папку.", file=sys.stderr)
        return 1
    find_tool(name, tools)
    return 2


def cmd_new(name: str, tools_dir: Path = TOOLS_DIR, description: str | None = None,
            group: str | None = None, groups_file: Path = GROUPS, lang: str = "py",
            workdir: str | None = None, workdirs_file: Path = WORKDIRS) -> int:
    name = normalize(name)
    err = name_error(name)
    if err:
        print(err, file=sys.stderr)
        return 2
    if lang not in TEMPLATES:
        print(f"Невідома мова «{lang}»: py, ps1 або sh.", file=sys.stderr)
        return 2
    target = tools_dir / name
    if target.exists():
        print(f"Тул «{name}» уже існує: {target}", file=sys.stderr)
        return 1
    folder = None
    if workdir:
        folder, err = resolve_workdir(workdir)
        if folder is None:
            print(err, file=sys.stderr)
            return 1
    target.mkdir(parents=True)
    filename, template = TEMPLATES[lang]
    main = target / filename
    description = " ".join((description or "").replace('"""', "").replace("#>", "").split())
    if lang == "py":
        description = description.replace("\\", "\\\\")
    description = description or "TODO: короткий опис тулу."
    content = template.format(name=name, description=description)
    if lang == "ps1":  # BOM: інакше Windows PowerShell 5.1 читає кирилицю як ANSI
        main.write_text(content, encoding="utf-8-sig")
    else:  # .sh — тільки LF, bash не розуміє \r
        main.write_text(content, encoding="utf-8", newline="\n" if lang == "sh" else None)
    print(f"Створено {main}")
    if clean_group(group):
        print(f"Група: {assign_group(name, group, groups_file)}")
    if folder is not None:
        set_workdir(name, folder, workdirs_file)
        print(f"Робоча папка: {folder}")
    print(f"Допиши опис і логіку, потім перевір: local_helper {name} -help")
    return 0


def cmd_workdir(name: str, folder: str | None = None, clear: bool = False, tools_dir: Path = TOOLS_DIR,
                registry: Path = REGISTRY, groups_file: Path = GROUPS, workdirs_file: Path = WORKDIRS) -> int:
    tool = find_tool(name, discover(tools_dir, registry, groups_file, workdirs_file))
    if tool is None:
        return 2
    if clear:
        set_workdir(tool.name, None, workdirs_file)
        print(f"«{tool.name}» знову запускається в поточній папці")
        return 0
    if folder is None:
        print(tool.workdir or f"Робоча папка не закріплена: «{tool.name}» запускається в поточній папці")
        return 0
    path, err = resolve_workdir(folder)
    if path is None:
        print(err, file=sys.stderr)
        return 1
    set_workdir(tool.name, path, workdirs_file)
    print(f"«{tool.name}» запускатиметься в {path}")
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
    parser.add_argument("path", help="папка тулу або файл .py, .ps1, .sh")
    parser.add_argument("--name", help="ім'я команди (за замовчуванням — ім'я папки/файлу)")
    parser.add_argument("--entry", help="головний скрипт у папці, якщо їх кілька")
    parser.add_argument("--group", help="група, в яку покласти тул")
    parser.add_argument("--workdir", help="папка, в якій завжди запускати тул")
    return parser.parse_args(args)


def parse_new(args: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="local_helper new", description="Створити вбудований тул за шаблоном.")
    parser.add_argument("name", help="ім'я команди (латиниця, a-z 0-9 _)")
    parser.add_argument("--description", help="короткий опис (перший рядок docstring)")
    parser.add_argument("--group", help="група, в яку покласти тул")
    parser.add_argument("--lang", choices=list(TEMPLATES), default="py",
                        help="мова тулу: py (Python), ps1 (PowerShell), sh (Bash)")
    parser.add_argument("--workdir", help="папка, в якій завжди запускати тул")
    return parser.parse_args(args)


def parse_workdir(args: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="local_helper workdir",
                                     description="Закріпити за тулом папку, в якій він завжди запускається.")
    parser.add_argument("tool", help="ім'я тулу")
    parser.add_argument("folder", nargs="?", help="папка (без неї — показати поточну)")
    parser.add_argument("--clear", action="store_true", help="прибрати закріплену папку")
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
        return cmd_add(a.path, a.name, a.entry, group=a.group, workdir=a.workdir)
    if head == "new":
        a = parse_new(rest)
        return cmd_new(a.name, description=a.description, group=a.group, lang=a.lang, workdir=a.workdir)
    if head == "workdir":
        a = parse_workdir(rest)
        return cmd_workdir(a.tool, a.folder, a.clear)
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
