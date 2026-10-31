import sys
import tempfile
import unittest
from functools import partial
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import local_helper_tui as ui
    from textual.widgets import Checkbox, Input, ListView, RichLog
except ImportError:
    ui = None


@unittest.skipIf(ui is None, "textual не встановлено (lh setup)")
class TuiSmokeTest(unittest.IsolatedAsyncioTestCase):
    async def wait_for(self, pilot, cond, tries=80):
        for _ in range(tries):
            await pilot.pause(0.1)
            if cond():
                return True
        return False

    async def test_list_search_form_and_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.txt").write_text("1\n2\n", encoding="utf-8")
            app = ui.LocalHelperApp(workdir=Path(tmp))
            async with app.run_test(size=(140, 44)) as pilot:
                await pilot.pause(0.3)
                names = [i.tool_name for i in app.query_one("#tools", ListView).children]
                self.assertIn("photos_to_pdf", names)
                self.assertIn("count_lines", names)

                await pilot.press("slash", *"photo")
                await pilot.pause(0.3)
                self.assertEqual([i.tool_name for i in app.query_one("#tools", ListView).children],
                                 ["photos_to_pdf"])
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
        with tempfile.TemporaryDirectory() as tmp:
            tools, registry = Path(tmp) / "tools", Path(tmp) / "registry.json"
            tools.mkdir()
            lh = ui.lh
            with mock.patch.object(lh, "TOOLS_DIR", tools), \
                    mock.patch.object(lh, "discover", partial(lh.discover, tools, registry)):
                app = ui.LocalHelperApp(workdir=Path(tmp))
                async with app.run_test(size=(140, 44)) as pilot:
                    await pilot.pause(0.3)
                    await pilot.press("n")
                    await pilot.pause(0.2)
                    self.assertIsInstance(app.screen, ui.NewToolScreen)
                    app.screen.query_one("#new-name", Input).value = "My-Tool"
                    app.screen.query_one("#new-desc", Input).value = r"Робить C:\Temp річ"
                    await pilot.click("#new-ok")
                    self.assertTrue(await self.wait_for(
                        pilot, lambda: app.current is not None and app.current.name == "my_tool"))
                    self.assertEqual(app.current.summary, r"Робить C:\Temp річ")
                    self.assertTrue((tools / "my_tool" / "main.py").is_file())

                    await pilot.press("n")
                    await pilot.pause(0.2)
                    await pilot.press("escape")
                    await pilot.pause(0.2)
                    self.assertNotIsInstance(app.screen, ui.NewToolScreen)


if __name__ == "__main__":
    unittest.main()
