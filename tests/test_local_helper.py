import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import local_helper as lh  # noqa: E402


def quiet(fn, *args, **kwargs):
    """Виклик без шуму в консолі; повертає (код, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fn(*args, **kwargs)
    return code, out.getvalue(), err.getvalue()


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.tools = self.base / "tools"
        self.tools.mkdir()
        self.registry = self.base / "registry.json"
        self.groups = self.base / "groups.json"
        self.workdirs = self.base / "workdirs.json"

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, path: Path, code: str = '"""Опис тулу.\n\nДеталі."""\n') -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
        return path

    def discover(self):
        return lh.discover(self.tools, self.registry, self.groups, self.workdirs)

    def add(self, path, name=None, entry=None, group=None):
        return quiet(lh.cmd_add, str(path), name, entry, tools_dir=self.tools, registry=self.registry,
                     group=group, groups_file=self.groups)


class DiscoverTest(TempDirTest):
    def test_finds_builtin_and_reads_docstring(self):
        self.write(self.tools / "alpha" / "main.py")
        found = self.discover()
        self.assertEqual(list(found), ["alpha"])
        self.assertEqual(found["alpha"].summary, "Опис тулу.")
        self.assertEqual(found["alpha"].source, "builtin")

    def test_skips_hidden_private_and_without_main(self):
        self.write(self.tools / "_private" / "main.py")
        self.write(self.tools / ".hidden" / "main.py")
        (self.tools / "no_main").mkdir()
        self.write(self.tools / "ok" / "main.py")
        self.assertEqual(list(self.discover()), ["ok"])

    def test_broken_syntax_and_missing_docstring(self):
        self.write(self.tools / "broken" / "main.py", "def (:\n")
        self.write(self.tools / "plain" / "main.py", "print(1)\n")
        found = self.discover()
        self.assertIn("помилка синтаксису", found["broken"].summary)
        self.assertEqual(found["plain"].summary, "(без опису)")

    def test_missing_tools_dir_and_registry(self):
        self.assertEqual(lh.discover(self.base / "nope", self.base / "nope.json"), {})

    def test_new_creates_runnable_template(self):
        self.assertEqual(quiet(lh.cmd_new, "my-tool", self.tools)[0], 0)
        main_py = self.tools / "my_tool" / "main.py"
        compile(main_py.read_text(encoding="utf-8"), str(main_py), "exec")
        self.assertIn("my_tool", self.discover())

    def test_new_with_description(self):
        quiet(lh.cmd_new, "described", self.tools, 'Опис з """лапками""" і C:\\Nope')
        main_py = self.tools / "described" / "main.py"
        compile(main_py.read_text(encoding="utf-8"), str(main_py), "exec")
        self.assertEqual(self.discover()["described"].summary, "Опис з лапками і C:\\Nope")

    def test_new_rejects_reserved_existing_and_invalid(self):
        self.write(self.tools / "exists" / "main.py")
        self.assertEqual(quiet(lh.cmd_new, "add", self.tools)[0], 2)
        self.assertEqual(quiet(lh.cmd_new, "exists", self.tools)[0], 1)
        self.assertEqual(quiet(lh.cmd_new, "1bad", self.tools)[0], 2)


class AddRemoveTest(TempDirTest):
    def test_add_folder_with_main(self):
        self.write(self.base / "ext" / "My Tool" / "main.py")
        code, out, _ = self.add(self.base / "ext" / "My Tool")
        self.assertEqual(code, 0)
        self.assertIn("my_tool -help", out)
        tool = self.discover()["my_tool"]
        self.assertEqual(tool.source, "added")
        self.assertEqual(tool.summary, "Опис тулу.")
        rec = json.loads(self.registry.read_text(encoding="utf-8"))["tools"]["my_tool"]
        self.assertEqual(rec["entry"], "main.py")

    def test_add_folder_with_single_py(self):
        self.write(self.base / "solo" / "run_me.py")
        self.assertEqual(self.add(self.base / "solo")[0], 0)
        self.assertEqual(self.discover()["solo"].path.name, "run_me.py")

    def test_add_folder_with_many_py_needs_entry(self):
        self.write(self.base / "multi" / "a.py")
        self.write(self.base / "multi" / "b.py")
        code, _, err = self.add(self.base / "multi")
        self.assertEqual(code, 1)
        self.assertIn("--entry", err)
        self.assertEqual(self.add(self.base / "multi", entry="b.py")[0], 0)
        self.assertEqual(self.discover()["multi"].path.name, "b.py")

    def test_add_single_file(self):
        self.write(self.base / "scripts" / "clean-up.py")
        self.assertEqual(self.add(self.base / "scripts" / "clean-up.py")[0], 0)
        self.assertIn("clean_up", self.discover())

    def test_add_name_conflicts(self):
        self.write(self.tools / "builtin_one" / "main.py")
        self.write(self.base / "builtin_one" / "main.py")
        self.write(self.base / "list" / "main.py")
        self.write(self.base / "Фото" / "main.py")
        self.assertEqual(self.add(self.base / "builtin_one")[0], 1)
        self.assertEqual(self.add(self.base / "list")[0], 2)
        code, _, err = self.add(self.base / "Фото")
        self.assertEqual(code, 2)
        self.assertIn("--name", err)
        self.assertEqual(self.add(self.base / "Фото", name="photo")[0], 0)
        self.assertEqual(self.add(self.base / "Фото", name="photo")[0], 1)

    def test_add_missing_path(self):
        self.assertEqual(self.add(self.base / "nope")[0], 1)

    def test_remove(self):
        self.write(self.base / "ext" / "main.py")
        self.add(self.base / "ext")
        self.assertEqual(quiet(lh.cmd_remove, "ext", self.tools, self.registry, self.groups)[0], 0)
        self.assertNotIn("ext", self.discover())
        self.assertTrue((self.base / "ext" / "main.py").exists())
        self.write(self.tools / "builtin_one" / "main.py")
        self.assertEqual(quiet(lh.cmd_remove, "builtin_one", self.tools, self.registry, self.groups)[0], 1)

    def test_moved_tool_is_marked_missing(self):
        main_py = self.write(self.base / "gone" / "main.py")
        self.add(self.base / "gone")
        main_py.unlink()
        tool = self.discover()["gone"]
        self.assertTrue(tool.missing)
        self.assertEqual(tool.summary, "Опис тулу.")  # опис збережено в реєстрі
        self.assertEqual(quiet(lh.run_tool, tool, [])[0], 1)


class GroupsTest(TempDirTest):
    def setUp(self):
        super().setUp()
        for name in ("alpha", "beta", "gamma"):
            self.write(self.tools / name / "main.py")

    def group(self, name, group):
        return quiet(lh.cmd_group, name, group, self.tools, self.registry, self.groups)

    def test_assign_ungroup_and_case_insensitive_reuse(self):
        self.assertEqual(self.group("alpha", " Фото  документи ")[0], 0)
        self.assertEqual(self.group("beta", "фото документи")[0], 0)  # та сама група
        found = self.discover()
        self.assertEqual(found["alpha"].group, "Фото документи")
        self.assertEqual(found["beta"].group, "Фото документи")
        self.assertIsNone(found["gamma"].group)
        self.assertEqual(self.group("alpha", None)[0], 0)
        self.assertIsNone(self.discover()["alpha"].group)
        self.assertEqual(self.group("nope", "X")[0], 2)

    def test_ordered_and_list_output(self):
        self.group("gamma", "Б-група")
        self.group("alpha", "А-група")
        sections = lh.ordered(self.discover())
        self.assertEqual([(g, [t.name for t in ts]) for g, ts in sections],
                         [("А-група", ["alpha"]), ("Б-група", ["gamma"]), (lh.NO_GROUP, ["beta"])])
        code, out, _ = quiet(lh.cmd_list, self.discover())
        self.assertLess(out.index("А-група"), out.index("alpha"))
        self.assertLess(out.index(lh.NO_GROUP), out.index("beta"))
        code, out, _ = quiet(lh.cmd_list, self.discover(), "б-група")
        self.assertIn("gamma", out)
        self.assertNotIn("alpha", out)
        self.assertEqual(quiet(lh.cmd_list, self.discover(), "нема")[0], 1)

    def test_groups_rename_and_delete(self):
        self.group("alpha", "Old")
        self.group("beta", "Old")
        self.assertEqual(quiet(lh.cmd_groups, ["rename", "old", "New"], self.discover(), self.groups)[0], 0)
        self.assertEqual({t.group for t in self.discover().values()}, {"New", None})
        self.assertEqual(quiet(lh.cmd_groups, ["delete", "New"], self.discover(), self.groups)[0], 0)
        self.assertEqual({t.group for t in self.discover().values()}, {None})
        self.assertEqual(quiet(lh.cmd_groups, ["delete", "New"], self.discover(), self.groups)[0], 1)

    def test_new_and_add_with_group_and_remove_cleans_up(self):
        quiet(lh.cmd_new, "fresh", self.tools, None, "Нове", self.groups)
        self.assertEqual(self.discover()["fresh"].group, "Нове")
        self.write(self.base / "ext" / "main.py")
        self.assertEqual(self.add(self.base / "ext", group="Зовнішні")[0], 0)
        self.assertEqual(self.discover()["ext"].group, "Зовнішні")
        quiet(lh.cmd_remove, "ext", self.tools, self.registry, self.groups)
        self.assertNotIn("ext", lh.load_groups(self.groups))


class HelpTest(TempDirTest):
    def test_help_does_not_run_script_without_parser(self):
        marker = self.base / "ran.txt"
        script = self.write(self.base / "danger" / "main.py",
                            f'"""Небезпечний."""\nopen(r"{marker}", "w").close()\n')
        code, out, _ = quiet(lh.cmd_help, lh.Tool("danger", script, "Небезпечний.", "added"))
        self.assertEqual(code, 0)
        self.assertIn("Параметри не описані", out)
        self.assertFalse(marker.exists())

    def test_uses_cli_parser(self):
        self.assertTrue(lh.uses_cli_parser(self.write(self.base / "a.py", "import argparse\n")))
        self.assertTrue(lh.uses_cli_parser(self.write(self.base / "b.py", "from click import command\n")))
        self.assertFalse(lh.uses_cli_parser(self.write(self.base / "c.py", "import os\n")))


ARGPARSE_TOOL = '''"""Демо."""
import argparse
p = argparse.ArgumentParser()
p.add_argument("src")
p.add_argument("--mode", choices=["a", "b"], default="a", help="режим")
p.add_argument("-n", "--dry-run", action="store_true")
p.add_argument("--ext", nargs="+")
p.add_argument("--tag", action="append")
p.parse_args()
open(r"{marker}", "w").close()
'''


class DescribeArgsTest(TempDirTest):
    def test_reads_parser_without_running_main_logic(self):
        marker = self.base / "ran.txt"
        path = self.write(self.base / "t" / "main.py", ARGPARSE_TOOL.format(marker=marker))
        specs = lh.describe_args(lh.Tool("t", path, ""))
        by_dest = {s["dest"]: s for s in specs}
        self.assertEqual(list(by_dest), ["src", "mode", "dry_run", "ext", "tag"])
        self.assertEqual(by_dest["mode"]["choices"], ["a", "b"])
        self.assertEqual(by_dest["mode"]["default"], "a")
        self.assertEqual(by_dest["dry_run"]["kind"], "store_true")
        self.assertEqual(by_dest["ext"]["nargs"], "+")
        self.assertEqual(by_dest["tag"]["kind"], "append")
        self.assertFalse(marker.exists())

    def test_no_argparse_is_not_executed(self):
        marker = self.base / "ran.txt"
        path = self.write(self.base / "t" / "main.py", f'open(r"{marker}", "w").close()\n')
        self.assertIsNone(lh.describe_args(lh.Tool("t", path, "")))
        self.assertFalse(marker.exists())

    def test_real_photos_to_pdf(self):
        tool = lh.discover()["photos_to_pdf"]
        dests = {s["dest"]: s for s in lh.describe_args(tool)}
        self.assertEqual(dests["folder"]["options"], [])
        self.assertEqual(dests["sort"]["choices"], ["name", "date"])

    def test_build_argv(self):
        marker = self.base / "ran.txt"
        path = self.write(self.base / "t" / "main.py", ARGPARSE_TOOL.format(marker=marker))
        specs = lh.describe_args(lh.Tool("t", path, ""))
        values = {"src": r'"D:\My Files"', "mode": "a", "dry_run": True, "ext": ".py .md", "tag": "x y"}
        self.assertEqual(lh.build_argv(specs, values, "--force"),
                         [r"D:\My Files", "--dry-run", "--ext", ".py", ".md",
                          "--tag", "x", "--tag", "y", "--force"])
        self.assertEqual(lh.build_argv(specs, {"mode": "b", "dry_run": False}), ["--mode", "b"])


PS1_TOOL = '''#Requires -Version 5.1
<#
.SYNOPSIS
    Робить резервну копію.

.DESCRIPTION
    Копіює папку в архів.

.PARAMETER Source
    що копіювати

.PARAMETER Mode
    режим копіювання

.EXAMPLE
    local_helper backup -Source D:\\Data
#>
param(
    [Parameter(Mandatory)][string]$Source,
    [ValidateSet("full", "diff")][string]$Mode = "full",
    [int]$Keep = 3,
    [switch]$DryRun
)
New-Item -ItemType File -Path "{marker}" | Out-Null
'''

SH_TOOL = '''#!/usr/bin/env bash
# Друкує аргументи і поточну папку.
#
# Приклад:
#   local_helper echo_args a b
set -eu
printf '%s\\n' "$@"
pwd
'''


class ScriptKindsTest(TempDirTest):
    def test_discovers_ps1_and_sh_builtins(self):
        self.write(self.tools / "backup" / "main.ps1", PS1_TOOL)
        self.write(self.tools / "echo_args" / "main.sh", SH_TOOL)
        found = self.discover()
        self.assertEqual(found["backup"].kind, "ps1")
        self.assertEqual(found["backup"].summary, "Робить резервну копію.")
        self.assertIn("Копіює папку в архів.", found["backup"].doc)
        self.assertIn("local_helper backup -Source", found["backup"].doc)
        self.assertNotIn("що копіювати", found["backup"].doc)  # .PARAMETER — лише в довідці параметрів
        self.assertEqual(found["echo_args"].kind, "sh")
        self.assertEqual(found["echo_args"].summary, "Друкує аргументи і поточну папку.")
        self.assertIn("local_helper echo_args a b", found["echo_args"].doc)

    def test_ps1_help_params_and_plain_comment_fallback(self):
        path = self.write(self.base / "b.ps1", PS1_TOOL)
        self.assertEqual(lh.ps1_help(path)[1], {"source": "що копіювати", "mode": "режим копіювання"})
        plain = self.write(self.base / "plain.ps1", "# Простий опис.\n# Ще рядок.\nWrite-Output 1\n")
        self.assertEqual(lh.read_doc(plain), "Простий опис.\nЩе рядок.")

    def test_add_script_files_and_folder(self):
        ps1 = self.write(self.base / "ext" / "Clean Up.ps1", PS1_TOOL)
        self.assertEqual(self.add(ps1)[0], 0)
        self.assertEqual(self.discover()["clean_up"].kind, "ps1")
        self.write(self.base / "shdir" / "run.sh", SH_TOOL)
        self.assertEqual(self.add(self.base / "shdir")[0], 0)
        self.assertEqual(self.discover()["shdir"].path.name, "run.sh")
        bad = self.write(self.base / "x.bat", "echo 1\n")
        code, _, err = self.add(bad)
        self.assertEqual(code, 1)
        self.assertIn("Непідтримуваний", err)

    def test_find_entry_lists_all_script_kinds(self):
        self.write(self.base / "m" / "a.py")
        self.write(self.base / "m" / "b.sh", SH_TOOL)
        entry, err = lh.find_entry(self.base / "m")
        self.assertIsNone(entry)
        self.assertIn("a.py, b.sh", err)

    def test_tool_command_per_kind(self):
        py = lh.Tool("p", self.base / "p.py", "")
        ps = lh.Tool("s", self.base / "s.ps1", "")
        sh = lh.Tool("b", self.base / "d" / "b.sh", "")
        with mock.patch.object(lh, "find_pwsh", return_value="pwsh"), \
                mock.patch.object(lh, "find_bash", return_value="bash"):
            self.assertEqual(lh.tool_command(py)[1], str(py.path))
            self.assertEqual(lh.tool_command(ps), ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                                   "-File", str(ps.path)])
            self.assertEqual(lh.tool_command(sh), ["bash", sh.path.as_posix()])
        self.write(sh.path, SH_TOOL)
        with mock.patch.object(lh, "find_bash", return_value=None):
            self.assertIsNone(lh.tool_command(sh))
            code, _, err = quiet(lh.run_tool, sh, [])
        self.assertEqual(code, 1)
        self.assertIn("Git Bash", err)

    def test_new_with_lang(self):
        self.assertEqual(quiet(lh.cmd_new, "ps_tool", self.tools, "Опис PS", lang="ps1")[0], 0)
        self.assertEqual(quiet(lh.cmd_new, "sh_tool", self.tools, "Опис SH", lang="sh")[0], 0)
        self.assertEqual(quiet(lh.cmd_new, "bad_lang", self.tools, lang="cmd")[0], 2)
        found = self.discover()
        self.assertEqual((found["ps_tool"].kind, found["ps_tool"].summary), ("ps1", "Опис PS"))
        self.assertEqual((found["sh_tool"].kind, found["sh_tool"].summary), ("sh", "Опис SH"))
        self.assertTrue((self.tools / "ps_tool" / "main.ps1").read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", (self.tools / "sh_tool" / "main.sh").read_bytes())

    @unittest.skipUnless(lh.find_pwsh(), "PowerShell не знайдено")
    def test_describe_ps1_args_without_running(self):
        marker = self.base / "ran.txt"
        path = self.write(self.base / "b" / "main.ps1", PS1_TOOL.replace("{marker}", str(marker)))
        specs = {s["dest"]: s for s in lh.describe_args(lh.Tool("b", path, ""))}
        self.assertEqual(list(specs), ["Source", "Mode", "Keep", "DryRun"])
        self.assertTrue(specs["Source"]["required"])
        self.assertEqual(specs["Source"]["help"], "що копіювати")
        self.assertEqual(specs["Mode"]["choices"], ["full", "diff"])
        self.assertEqual(specs["Mode"]["default"], "full")
        self.assertEqual(specs["Keep"]["default"], "3")
        self.assertEqual(specs["DryRun"]["kind"], "store_true")
        self.assertEqual(lh.build_argv(list(specs.values()), {"Source": r"D:\My Data", "Mode": "diff",
                                                              "DryRun": True}),
                         ["-Source", r"D:\My Data", "-Mode", "diff", "-DryRun"])
        self.assertFalse(marker.exists())

    @unittest.skipUnless(lh.find_pwsh(), "PowerShell не знайдено")
    def test_run_ps1_with_cyrillic_args(self):
        out = self.base / "out.txt"
        path = self.write(self.base / "w" / "main.ps1",
                          'param([string]$Text)\nSet-Content -LiteralPath "%s" -Value $Text -Encoding utf8\n' % out)
        tool = lh.Tool("w", path, "")
        self.assertEqual(quiet(lh.run_tool, tool, ["-Text", "Привіт світ"])[0], 0)
        self.assertEqual(out.read_text(encoding="utf-8-sig").strip(), "Привіт світ")

    @unittest.skipUnless(lh.find_bash(), "Git Bash не знайдено")
    def test_run_sh_in_workdir(self):
        wd = self.base / "папка з пробілом"
        wd.mkdir()
        script = SH_TOOL.split("set -eu")[0] + 'set -eu\npwd > out.txt\nprintf "%s" "$1" > arg.txt\n'
        path = self.write(self.base / "s" / "main.sh", script)
        tool = lh.Tool("s", path, "", workdir=wd)
        self.assertEqual(quiet(lh.run_tool, tool, ["Кирилиця й пробіл"])[0], 0)
        self.assertEqual((wd / "arg.txt").read_text(encoding="utf-8"), "Кирилиця й пробіл")
        self.assertTrue((wd / "out.txt").is_file())


class WorkdirTest(TempDirTest):
    def workdir(self, *args, **kwargs):
        return quiet(lh.cmd_workdir, *args, tools_dir=self.tools, registry=self.registry,
                     groups_file=self.groups, workdirs_file=self.workdirs, **kwargs)

    def test_set_show_clear(self):
        self.write(self.tools / "alpha" / "main.py")
        target = self.base / "дані"
        target.mkdir()
        self.assertEqual(self.workdir("alpha", str(target))[0], 0)
        self.assertEqual(self.discover()["alpha"].workdir, target.resolve())
        code, out, _ = self.workdir("alpha")
        self.assertEqual((code, out.strip()), (0, str(target.resolve())))
        self.assertIn("[папка]", quiet(lh.cmd_list, self.discover())[1])
        self.assertEqual(self.workdir("alpha", clear=True)[0], 0)
        self.assertIsNone(self.discover()["alpha"].workdir)
        self.assertEqual(self.workdir("alpha", str(self.base / "nope"))[0], 1)
        self.assertEqual(self.workdir("nope", str(target))[0], 2)

    def test_run_tool_uses_workdir(self):
        target = self.base / "робоча папка"
        target.mkdir()
        path = self.write(self.tools / "where" / "main.py",
                          'from pathlib import Path\nPath("cwd.txt").write_text(str(Path.cwd()), encoding="utf-8")\n')
        self.workdir("where", str(target))
        self.assertEqual(quiet(lh.run_tool, self.discover()["where"], [])[0], 0)
        self.assertEqual((target / "cwd.txt").read_text(encoding="utf-8"), str(target.resolve()))
        self.assertTrue(path.exists())

    def test_missing_workdir_is_reported(self):
        self.write(self.tools / "alpha" / "main.py")
        tool = self.discover()["alpha"]
        tool.workdir = self.base / "gone"
        code, _, err = quiet(lh.run_tool, tool, [])
        self.assertEqual(code, 1)
        self.assertIn("--clear", err)

    def test_add_new_with_workdir_and_remove_clears_it(self):
        self.write(self.base / "ext" / "main.py")
        code, _, _ = quiet(lh.cmd_add, str(self.base / "ext"), tools_dir=self.tools, registry=self.registry,
                           groups_file=self.groups, workdir=str(self.base), workdirs_file=self.workdirs)
        self.assertEqual(code, 0)
        self.assertEqual(self.discover()["ext"].workdir, self.base.resolve())
        quiet(lh.cmd_remove, "ext", self.tools, self.registry, self.groups, self.workdirs)
        self.assertNotIn("ext", lh.load_workdirs(self.workdirs))
        quiet(lh.cmd_new, "fresh", self.tools, workdir=str(self.base), workdirs_file=self.workdirs)
        self.assertEqual(self.discover()["fresh"].workdir, self.base.resolve())


@unittest.skipUnless(sys.platform == "win32", "POSIX-шляхи Git Bash — лише на Windows")
class CliPathTest(TempDirTest):
    def test_drive_form_and_windows_paths(self):
        self.assertEqual(lh.cli_path("/d/Dev/My Tools"), Path(r"D:\Dev\My Tools"))
        self.assertEqual(lh.cli_path("/c"), Path("C:\\"))
        self.assertEqual(lh.cli_path(f'"{self.base}"'), self.base.resolve())

    @unittest.skipUnless(lh.cygpath_w("/tmp"), "cygpath (Git for Windows) не знайдено")
    def test_msys_mount_with_brackets_and_cyrillic(self):
        folder = Path(lh.cygpath_w("/tmp")) / f"lh тест [1];x {self.base.name}"
        folder.mkdir()
        try:
            posix = f"/tmp/{folder.name}"
            self.assertEqual(lh.cli_path(posix), folder.resolve())
            self.assertEqual(lh.resolve_workdir(posix)[0], folder.resolve())
        finally:
            folder.rmdir()


class HelpersTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(lh.normalize(" Rename-Photos Now "), "rename_photos_now")

    def test_suggest(self):
        self.assertEqual(lh.suggest("cont_lines", ["count_lines", "other"]), ["count_lines"])

    def test_split_args_keeps_windows_paths(self):
        self.assertEqual(
            lh.split_args(r'"D:\My Files\a.txt" --ext .py C:\x'),
            [r"D:\My Files\a.txt", "--ext", ".py", r"C:\x"],
        )


if __name__ == "__main__":
    unittest.main()
