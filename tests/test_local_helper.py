import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

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

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, path: Path, code: str = '"""Опис тулу.\n\nДеталі."""\n') -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
        return path

    def discover(self):
        return lh.discover(self.tools, self.registry)

    def add(self, path, name=None, entry=None):
        return quiet(lh.cmd_add, str(path), name, entry, tools_dir=self.tools, registry=self.registry)


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
        self.assertEqual(quiet(lh.cmd_remove, "ext", self.tools, self.registry)[0], 0)
        self.assertNotIn("ext", self.discover())
        self.assertTrue((self.base / "ext" / "main.py").exists())
        self.write(self.tools / "builtin_one" / "main.py")
        self.assertEqual(quiet(lh.cmd_remove, "builtin_one", self.tools, self.registry)[0], 1)

    def test_moved_tool_is_marked_missing(self):
        main_py = self.write(self.base / "gone" / "main.py")
        self.add(self.base / "gone")
        main_py.unlink()
        tool = self.discover()["gone"]
        self.assertTrue(tool.missing)
        self.assertEqual(tool.summary, "Опис тулу.")  # опис збережено в реєстрі
        self.assertEqual(quiet(lh.run_tool, tool, [])[0], 1)


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
