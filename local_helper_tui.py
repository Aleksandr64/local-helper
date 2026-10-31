"""Повноекранний TUI для local_helper (Textual): список тулів, опис, форма параметрів, запуск."""
from __future__ import annotations

import asyncio
import io
import os
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (Button, Checkbox, Footer, Header, Input, Label, ListItem, ListView,
                             RichLog, Select, Static)

import local_helper as lh

BANNER = "╦  ╦ ╦\n║  ╠═╣\n╩═╝╩ ╩"
FLAG_KINDS = {"store_true", "store_false", "store_const", "count"}


def captured(fn, *args) -> tuple[int, str]:
    """Виклик команди local_helper з перехопленим виводом для повідомлення в TUI."""
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(out):
        code = fn(*args)
    return code, out.getvalue().strip()


def is_required(spec: dict) -> bool:
    if spec["options"]:
        return spec["required"]
    return spec["nargs"] in (None, "+") or (spec["nargs"] or "").isdigit()


class ToolItem(ListItem):
    def __init__(self, tool: lh.Tool) -> None:
        label = Text(tool.name, style="bold")
        if tool.source == "added":
            label.append("  added", style="italic cyan")
        if tool.missing:
            label.append("  не знайдено", style="italic red")
        super().__init__(Label(label))
        self.tool_name = tool.name


class AddToolScreen(ModalScreen[dict | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Скасувати")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(Text("Підключити тул з іншої папки", style="bold"))
            yield Label("Шлях до папки або .py-файлу")
            yield Input(placeholder=r"D:\Scripts\my_tool", id="add-path")
            yield Label("Ім'я команди (необов'язково, латиниця)")
            yield Input(placeholder="за замовчуванням — ім'я папки", id="add-name")
            yield Label("Головний файл (необов'язково, якщо .py кілька)")
            yield Input(placeholder="main.py", id="add-entry")
            with Horizontal(classes="buttons"):
                yield Button("Додати", variant="primary", id="add-ok")
                yield Button("Скасувати", id="add-cancel")

    @on(Button.Pressed, "#add-ok")
    @on(Input.Submitted)
    def submit(self) -> None:
        path = lh.unquote(self.query_one("#add-path", Input).value)
        if not path:
            self.notify("Вкажи шлях", severity="error")
            return
        self.dismiss({"path": path,
                      "name": self.query_one("#add-name", Input).value.strip() or None,
                      "entry": self.query_one("#add-entry", Input).value.strip() or None})

    @on(Button.Pressed, "#add-cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class NewToolScreen(ModalScreen[dict | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Скасувати")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(Text("Новий тул (шаблон у tools/<ім'я>/main.py)", style="bold"))
            yield Label("Ім'я команди (латиниця, a-z 0-9 _)")
            yield Input(placeholder="rename_photos", id="new-name")
            yield Label("Короткий опис (перший рядок у списку)")
            yield Input(placeholder="Перейменовує фото за датою зйомки", id="new-desc")
            with Horizontal(classes="buttons"):
                yield Button("Створити", variant="primary", id="new-ok")
                yield Button("Скасувати", id="new-cancel")

    @on(Button.Pressed, "#new-ok")
    @on(Input.Submitted)
    def submit(self) -> None:
        name = lh.normalize(self.query_one("#new-name", Input).value)
        if not name:
            self.notify("Вкажи ім'я", severity="error")
            return
        err = lh.name_error(name)
        if err:
            self.notify(err, severity="error", markup=False)
            return
        self.dismiss({"name": name, "description": self.query_one("#new-desc", Input).value.strip()})

    @on(Button.Pressed, "#new-cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "dismiss(False)", "Ні")]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(Text(self.question))
            with Horizontal(classes="buttons"):
                yield Button("Так", variant="error", id="yes")
                yield Button("Ні", id="no")

    @on(Button.Pressed)
    def answer(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class WorkdirScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "Скасувати")]

    def __init__(self, current: Path) -> None:
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(Text("Робоча папка (від неї рахуються відносні шляхи)", style="bold"))
            yield Input(value=str(self.current), id="wd-path")
            with Horizontal(classes="buttons"):
                yield Button("OK", variant="primary", id="wd-ok")
                yield Button("Скасувати", id="wd-cancel")

    @on(Button.Pressed, "#wd-ok")
    @on(Input.Submitted)
    def submit(self) -> None:
        path = Path(lh.unquote(self.query_one("#wd-path", Input).value)).expanduser()
        if not path.is_dir():
            self.notify(f"Папку не знайдено: {path}", severity="error", markup=False)
            return
        self.dismiss(str(path.resolve()))

    @on(Button.Pressed, "#wd-cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class LocalHelperApp(App):
    TITLE = "local_helper"
    CSS = """
    #sidebar { width: 34; border-right: tall $panel; }
    #banner { color: $accent; padding: 1 2 0 2; height: 4; }
    #banner-info { padding: 0 2 1 2; color: $text-muted; }
    #search { margin: 0 1; }
    #tools { height: 1fr; margin-top: 1; }
    #detail { padding: 0 1; }
    #top { height: 3fr; }
    #title { padding: 1 0 0 0; }
    #docbox { height: auto; max-height: 9; margin-bottom: 1; }
    #doc { color: $text-muted; }
    #form { height: auto; }
    .section { color: $accent; text-style: bold; margin-top: 1; }
    .row { height: auto; }
    .flabel { width: 18; padding: 1 1 0 0; text-style: bold; }
    .row Input, .row Select { width: 40; }
    .fhelp { width: 1fr; padding: 1 0 0 2; color: $text-muted; }
    #extra { width: 1fr; }
    #preview { color: $success; margin: 1 0 0 0; height: auto; max-height: 3; }
    #actions { height: auto; }
    #actions Button { margin-right: 2; }
    #outhead { height: 1; background: $panel; padding: 0 1; margin-top: 1; }
    #log { height: 2fr; border: round $panel; }
    ModalScreen { align: center middle; }
    .dialog { width: 72; height: auto; padding: 1 2; border: thick $accent; background: $surface; }
    .dialog Label { margin-top: 1; }
    .buttons { height: auto; margin-top: 1; }
    .buttons Button { margin-right: 2; }
    """
    BINDINGS = [
        Binding("ctrl+r", "run", "Запуск", priority=True),
        Binding("ctrl+s", "stop", "Стоп", priority=True),
        Binding("slash", "search", "Пошук"),
        Binding("ctrl+w", "workdir", "Папка", priority=True),
        Binding("n", "new", "Новий"),
        Binding("a", "add", "Додати"),
        Binding("d", "remove", "Відключити"),
        Binding("r", "reload", "Оновити"),
        Binding("q", "quit", "Вихід"),
    ]

    def __init__(self, workdir: Path | None = None) -> None:
        super().__init__()
        self.workdir = (workdir or Path.cwd()).resolve()
        self.tools: dict[str, lh.Tool] = {}
        self.current: lh.Tool | None = None
        self.specs: dict[str, list[dict] | None] = {}  # кеш опису аргументів
        self.values: dict[str, dict] = {}  # значення форми по тулах
        self.extras: dict[str, str] = {}
        self.proc: asyncio.subprocess.Process | None = None

    def notify(self, message: str, *, markup: bool = False, **kwargs) -> None:
        # без markup: шляхи й вивід тулів можуть містити [дужки]
        super().notify(message, markup=markup, **kwargs)

    # ---------- розмітка ----------
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Static(Text(BANNER), id="banner")
                yield Static(id="banner-info")
                yield Input(placeholder="пошук тулу  ( / )", id="search")
                yield ListView(id="tools")
            with Vertical(id="detail"):
                with VerticalScroll(id="top"):
                    yield Static(id="title")
                    with VerticalScroll(id="docbox"):
                        yield Static(id="doc")
                    yield Static("Параметри", classes="section")
                    yield Vertical(id="form")
                    yield Static("Додаткові аргументи (рядком)", classes="section")
                    yield Input(placeholder='напр. --force "D:\\My Files"', id="extra")
                yield Static(id="preview")
                with Horizontal(id="actions"):
                    yield Button("▶ Запустити  ^R", variant="success", id="run")
                    yield Button("■ Зупинити  ^S", variant="error", id="stop", disabled=True)
                yield Static(id="outhead")
                yield RichLog(id="log", wrap=True, markup=False, highlight=False, max_lines=5000)
        yield Footer()

    async def on_mount(self) -> None:
        self.set_outhead("Вивід")
        await self.reload_tools()
        self.query_one("#tools", ListView).focus()

    # ---------- список тулів ----------
    async def reload_tools(self) -> None:
        self.tools = lh.discover()
        self.specs.clear()
        self.update_info()
        await self.fill_list(self.query_one("#search", Input).value)

    def update_info(self) -> None:
        self.sub_title = f"робоча папка: {self.workdir}"
        added = sum(t.source == "added" for t in self.tools.values())
        self.query_one("#banner-info", Static).update(
            Text(f"тулів: {len(self.tools)}  (підключених: {added})"))

    async def fill_list(self, query: str) -> None:
        q = query.strip().lower()
        shown = [t for t in self.tools.values()
                 if not q or q in t.name.lower() or q in t.doc.lower()]
        lv = self.query_one("#tools", ListView)
        await lv.clear()
        await lv.extend(ToolItem(t) for t in shown)
        names = [t.name for t in shown]
        if self.current and self.current.name in names:
            lv.index = names.index(self.current.name)
        elif names:
            lv.index = 0
        else:
            self.show_tool(None)

    @on(Input.Changed, "#search")
    async def search_changed(self, event: Input.Changed) -> None:
        await self.fill_list(event.value)

    @on(Input.Submitted, "#search")
    def search_submitted(self) -> None:
        self.query_one("#tools", ListView).focus()

    @on(ListView.Highlighted, "#tools")
    def tool_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, ToolItem):
            tool = self.tools.get(event.item.tool_name)
            if tool is not self.current:
                self.show_tool(tool)

    @on(ListView.Selected, "#tools")
    def tool_selected(self) -> None:
        fields = self.query(".field")
        (fields.first() if fields else self.query_one("#extra", Input)).focus()

    # ---------- деталі та форма ----------
    def show_tool(self, tool: lh.Tool | None) -> None:
        self.current = tool
        title = self.query_one("#title", Static)
        doc = self.query_one("#doc", Static)
        if tool is None:
            title.update(Text("Тулів не знайдено", style="bold"))
            doc.update(Text("Створи новий тул — клавіша n, або підключи існуючий — клавіша a"))
            self.query_one("#form", Vertical).remove_children()
            self.update_preview()
            return
        t = Text(tool.name, style="bold " + ("red" if tool.missing else "cyan"))
        t.append(f"  ·  {'підключений' if tool.source == 'added' else 'вбудований'}  ·  ", style="dim")
        t.append(str(tool.path), style="dim")
        title.update(t)
        doc.update(Text(tool.doc or "(без опису — додай docstring на початок файлу)"))
        extra = self.query_one("#extra", Input)
        with extra.prevent(Input.Changed):
            extra.value = self.extras.get(tool.name, "")
        self.query_one("#run", Button).disabled = tool.missing
        self.load_form(tool)

    @work(exclusive=True, group="form")
    async def load_form(self, tool: lh.Tool) -> None:
        form = self.query_one("#form", Vertical)
        await form.remove_children()
        if tool.name not in self.specs:
            await form.mount(Static(Text("Читаю параметри…", style="dim italic")))
            self.specs[tool.name] = await asyncio.to_thread(lh.describe_args, tool)
            await form.remove_children()
        if self.current is not tool:
            return
        specs = self.specs[tool.name]
        if tool.missing:
            await form.mount(Static(Text("Файл тулу зник. Відключи його (d) і підключи знову з новим шляхом.",
                                         style="red")))
        elif not specs:
            await form.mount(Static(Text("Форма недоступна: тул не використовує argparse або не має параметрів. "
                                         "Введи аргументи рядком нижче.", style="dim")))
        else:
            values = self.values.setdefault(tool.name, {})
            await form.mount_all(self.field_row(s, values.get(s["dest"])) for s in specs)
        self.update_preview()

    def field_row(self, spec: dict, value) -> Horizontal:
        wid = f"f-{spec['dest']}"
        help_text = Text(spec["help"] or "", style="dim")
        if spec["options"] and spec["kind"] in FLAG_KINDS:
            widget = Checkbox(lh.option_flag(spec), value=bool(value), id=wid, classes="field")
            return Horizontal(Static("", classes="flabel"), widget, Static(help_text, classes="fhelp"),
                              classes="row")
        name = lh.option_flag(spec) if spec["options"] else (spec["metavar"] or spec["dest"])
        if is_required(spec):
            name += " *"
        default = spec["default"]
        if spec["choices"]:
            prompt = f"(за замовч.: {default})" if default is not None else "(не вибрано)"
            widget = Select([(c, c) for c in spec["choices"]], prompt=prompt,
                            value=value if value in spec["choices"] else Select.NULL, id=wid, classes="field")
        else:
            placeholder = f"за замовч.: {default}" if default not in (None, "", False) else ""
            if spec["nargs"] in ("+", "*"):
                placeholder = (placeholder + "  " if placeholder else "") + "(кілька через пробіл)"
            widget = Input(value=value or "", placeholder=placeholder, id=wid, classes="field")
        return Horizontal(Static(Text(name), classes="flabel"), widget, Static(help_text, classes="fhelp"),
                          classes="row")

    def form_values(self) -> dict:
        values = {}
        for w in self.query(".field"):
            dest = (w.id or "")[2:]
            if isinstance(w, Select):
                values[dest] = None if w.value is Select.NULL else w.value
            else:
                values[dest] = w.value
        return values

    @on(Input.Changed, ".field")
    @on(Checkbox.Changed, ".field")
    @on(Select.Changed, ".field")
    @on(Input.Changed, "#extra")
    def form_changed(self) -> None:
        if self.current:
            self.values[self.current.name] = self.form_values()
            self.extras[self.current.name] = self.query_one("#extra", Input).value
        self.update_preview()

    def current_argv(self) -> list[str]:
        tool = self.current
        specs = self.specs.get(tool.name) if tool else None
        extra = self.query_one("#extra", Input).value
        return lh.build_argv(specs or [], self.form_values(), extra)

    def update_preview(self) -> None:
        preview = self.query_one("#preview", Static)
        if not self.current:
            preview.update("")
            return
        cmd = subprocess.list2cmdline(self.current_argv())
        preview.update(Text(f"▶ lh {self.current.name} {cmd}".rstrip()))

    # ---------- запуск ----------
    def set_outhead(self, text: str, style: str = "") -> None:
        self.query_one("#outhead", Static).update(Text(text, style=style))

    @on(Button.Pressed, "#run")
    def action_run(self) -> None:
        tool = self.current
        if tool is None or tool.missing:
            return
        if self.proc is not None:
            self.notify("Тул ще виконується. Зупини його: ^S", severity="warning")
            return
        values = self.form_values()
        missing = [s["dest"] for s in self.specs.get(tool.name) or []
                   if is_required(s) and not str(values.get(s["dest"]) or "").strip()]
        if missing:
            self.notify(f"Заповни обов'язкові поля: {', '.join(missing)}", severity="error")
            return
        self.run_tool(tool, self.current_argv())

    @work(exclusive=True, group="run")
    async def run_tool(self, tool: lh.Tool, argv: list[str]) -> None:
        log = self.query_one("#log", RichLog)
        log.clear()
        log.write(Text(f"▶ lh {tool.name} {subprocess.list2cmdline(argv)}   (у {self.workdir})", style="bold"))
        self.set_outhead(f"Вивід · {tool.name} · виконується…", "bold yellow")
        self.query_one("#stop", Button).disabled = False
        self.query_one("#run", Button).disabled = True
        try:
            self.proc = await asyncio.create_subprocess_exec(
                lh.python_exe(tool), str(tool.path), *argv, cwd=str(self.workdir),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env={**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
            await asyncio.gather(self.pump(self.proc.stdout, log, ""),
                                 self.pump(self.proc.stderr, log, "red"))
            code = await self.proc.wait()
        except OSError as e:
            log.write(Text(f"Не вдалося запустити: {e}", style="red"))
            code = -1
        finally:
            self.proc = None
            self.query_one("#stop", Button).disabled = True
            self.query_one("#run", Button).disabled = bool(self.current and self.current.missing)
        ok = code == 0
        self.set_outhead(f"Вивід · {tool.name} · код виходу: {code}", "bold green" if ok else "bold red")
        if not ok:
            self.notify(f"{tool.name} завершився з кодом {code}", severity="error")

    @staticmethod
    async def pump(stream: asyncio.StreamReader, log: RichLog, style: str) -> None:
        """Потоковий вивід; '\\r' (прогрес) теж вважається кінцем рядка."""
        buf = ""
        while chunk := await stream.read(4096):
            buf += chunk.decode("utf-8", errors="replace")
            *lines, buf = buf.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            for line in lines:
                if line.strip():
                    log.write(Text(line, style=style))
        if buf.strip():
            log.write(Text(buf, style=style))

    @on(Button.Pressed, "#stop")
    def action_stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            self.notify("Зупиняю…")

    # ---------- інші дії ----------
    def action_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_workdir(self) -> None:
        def done(path: str | None) -> None:
            if path:
                self.workdir = Path(path)
                self.update_info()
        self.push_screen(WorkdirScreen(self.workdir), done)

    def action_add(self) -> None:
        async def done(result: dict | None) -> None:
            if not result:
                return
            code, msg = captured(lh.cmd_add, result["path"], result["name"], result["entry"])
            self.notify(msg or "Готово", severity="information" if code == 0 else "error", timeout=8)
            if code == 0:
                await self.reload_tools()
        self.push_screen(AddToolScreen(), done)

    def action_new(self) -> None:
        async def done(result: dict | None) -> None:
            if not result:
                return
            code, msg = captured(lh.cmd_new, result["name"], lh.TOOLS_DIR, result["description"])
            self.notify(msg, severity="information" if code == 0 else "error", timeout=10)
            if code == 0:
                await self.select_tool(result["name"])
        self.push_screen(NewToolScreen(), done)

    async def select_tool(self, name: str) -> None:
        """Перечитати тули, скинути пошук і виділити тул з цим ім'ям."""
        search = self.query_one("#search", Input)
        with search.prevent(Input.Changed):
            search.value = ""
        self.current = None
        await self.reload_tools()
        lv = self.query_one("#tools", ListView)
        names = [i.tool_name for i in lv.children if isinstance(i, ToolItem)]
        if name in names:
            lv.index = names.index(name)
            lv.focus()

    def action_remove(self) -> None:
        tool = self.current
        if tool is None:
            return
        if tool.source != "added":
            self.notify(f"«{tool.name}» — вбудований тул; щоб прибрати, видали папку {tool.path.parent}",
                        severity="warning", timeout=8)
            return

        async def done(yes: bool | None) -> None:
            if yes:
                code, msg = captured(lh.cmd_remove, tool.name)
                self.notify(msg, severity="information" if code == 0 else "error")
                self.current = None
                await self.reload_tools()
        self.push_screen(ConfirmScreen(f"Відключити тул «{tool.name}»?\nФайли в {tool.path.parent} не зміняться."),
                         done)

    async def action_reload(self) -> None:
        await self.reload_tools()
        self.notify("Список тулів оновлено")


def run() -> int:
    LocalHelperApp().run()
    return 0


if __name__ == "__main__":
    run()
