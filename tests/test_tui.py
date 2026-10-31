import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import local_helper_tui as ui
    from textual.widgets import Checkbox, Input, RichLog, Tree
except ImportError:
    ui = None


@contextmanager
def sandbox(tmp: Path):
    """Підміняє tools/, registry.json і groups.json на тимчасові, з копією вбудованих тулів."""
    lh = ui.lh
    tools, registry, groups = tmp / "tools", tmp / "registry.json", tmp / "groups.json"
    tools.mkdir()
    for name in ("count_lines", "photos_to_pdf"):
        (tools / name).mkdir()
        (tools / name / "main.py").write_bytes((lh.TOOLS_DIR / name / "main.py").read_bytes())
    original = lh.discover

    def discover(*args):  # TUI викликає discover() без аргументів — тоді беремо тимчасові шляхи
        return original(*args) if args else original(tools, registry, groups)

    with mock.patch.object(lh, "TOOLS_DIR", tools), mock.patch.object(lh, "REGISTRY", registry), \
            mock.patch.object(lh, "GROUPS", groups), mock.patch.object(lh, "discover", discover):
        yield tools, groups


@unittest.skipIf(ui is None, "textual не встановлено (lh setup)")
class TuiSmokeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        (self.tmp / "a.txt").write_text("1\n2\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    async def wait_for(self, pilot, cond, tries=80):
        for _ in range(tries):
            await pilot.pause(0.1)
            if cond():
                return True
        return False

    async def test_list_search_form_and_run(self):
        with sandbox(self.tmp):
            app = ui.LocalHelperApp(workdir=self.tmp)
            async with app.run_test(size=(140, 44)) as pilot:
                await pilot.pause(0.3)
                self.assertEqual(app.visible_tools(), ["count_lines", "photos_to_pdf"])

                await pilot.press("slash", *"photo")
                await pilot.pause(0.3)
                self.assertEqual(app.visible_tools(), ["photos_to_pdf"])
                self.assertTrue(await self.wait_for(pilot, lambda: app.query("#f-dry_run")))
                self.assertIsInstance(app.query_one("#f-dry_run"), Checkbox)
                self.assertEqual(type(app.screen).__name__, "Screen")  # літери в пошуку не відкрили діалогів

                app.query_one("#search", Input).value = "count"
                self.assertTrue(await self.wait_for(
                    pilot, lambda: app.current is not None and app.current.name == "count_lines"))
                self.assertTrue(await self.wait_for(pilot, lambda: app.query("#f-ext")))
                await pilot.press("ctrl+r")
                log = app.query_one("#log", RichLog)
                self.assertTrue(await self.wait_for(
                    pilot, lambda: app.proc is None and any("Разом" in str(l.text) for l in log.lines)))
                self.assertIn("код виходу: 0", str(app.query_one("#outhead").render()))

    async def test_new_tool_dialog_creates_and_selects_tool(self):
        with sandbox(self.tmp) as (tools, groups):
            app = ui.LocalHelperApp(workdir=self.tmp)
            async with app.run_test(size=(140, 44)) as pilot:
                await pilot.pause(0.3)
                await pilot.press("n")
                await pilot.pause(0.2)
                self.assertIsInstance(app.screen, ui.NewToolScreen)
                app.screen.query_one("#new-name", Input).value = "My-Tool"
                app.screen.query_one("#new-desc", Input).value = r"Робить C:\Temp річ"
                app.screen.query_one("#new-group", Input).value = "Утиліти"
                await pilot.click("#new-ok")
                self.assertTrue(await self.wait_for(
                    pilot, lambda: app.current is not None and app.current.name == "my_tool"))
                self.assertEqual(app.current.summary, r"Робить C:\Temp річ")
                self.assertEqual(app.current.group, "Утиліти")
                self.assertTrue((tools / "my_tool" / "main.py").is_file())

                await pilot.press("n")
                await pilot.pause(0.2)
                self.assertEqual(app.screen.query_one("#new-group", Input).value, "Утиліти")  # група поточного
                await pilot.press("escape")
                await pilot.pause(0.2)
                self.assertNotIsInstance(app.screen, ui.NewToolScreen)

    async def test_group_dialog_builds_tree_and_persists(self):
        with sandbox(self.tmp) as (tools, groups):
            app = ui.LocalHelperApp(workdir=self.tmp)
            async with app.run_test(size=(140, 44)) as pilot:
                await pilot.pause(0.3)
                tree = app.query_one("#tools", Tree)
                self.assertTrue(all(n.data[0] == "tool" for n in tree.root.children))  # без груп — плоский список

                await pilot.press("down")  # photos_to_pdf
                self.assertTrue(await self.wait_for(
                    pilot, lambda: app.current is not None and app.current.name == "photos_to_pdf"))
                await pilot.press("g")
                await pilot.pause(0.2)
                self.assertIsInstance(app.screen, ui.GroupScreen)
                app.screen.query_one("#group", Input).value = "  Фото  "
                await pilot.press("enter")
                self.assertTrue(await self.wait_for(
                    pilot, lambda: app.current is not None and app.current.group == "Фото"))

                self.assertEqual([n.data for n in tree.root.children],
                                 [("group", "Фото"), ("group", ui.lh.NO_GROUP)])
                self.assertEqual(app.visible_tools(), ["photos_to_pdf", "count_lines"])
                self.assertIn("Фото", groups.read_text(encoding="utf-8"))

                # пошук за назвою групи
                app.query_one("#search", Input).value = "фото"
                await pilot.pause(0.3)
                self.assertEqual(app.visible_tools(), ["photos_to_pdf"])

                # прибрати з групи
                app.query_one("#search", Input).value = ""
                await pilot.pause(0.3)
                app.query_one("#tools", Tree).focus()
                await pilot.press("g")
                await pilot.pause(0.2)
                await pilot.click("#grp-clear")
                self.assertTrue(await self.wait_for(
                    pilot, lambda: app.current is not None and app.current.group is None))
                self.assertTrue(all(n.data[0] == "tool" for n in tree.root.children))


if __name__ == "__main__":
    unittest.main()
