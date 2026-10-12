import asyncio

import pytest
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Button, Input, Markdown

import must_tui.must_app as must_app
from must_tui.dialogs import HelpDialog
from must_tui.must_app import (
    HELP_KEYBINDINGS_PLACEHOLDER,
    format_binding_key,
    keybindings_markdown_table,
    load_help_markdown,
)


@pytest.mark.parametrize(
    "key, expected",
    [
        ("ctrl+q", "Ctrl+Q"),
        ("ctrl+l", "Ctrl+L"),
        ("f1", "F1"),
        ("f12", "F12"),
        ("ctrl+j", "Ctrl+J"),
    ],
)
def test_format_binding_key(key, expected):
    assert format_binding_key(key) == expected


def test_keybindings_markdown_table_renders_a_row_per_described_binding():
    bindings = [
        Binding("ctrl+q", "app.quit", "Quit"),
        Binding("f1", "show_help", "Help"),
        Binding("escape", "dismiss", ""),  # no description -- skipped
    ]

    table = keybindings_markdown_table(bindings)

    assert "| `Ctrl+Q` | Quit |" in table
    assert "| `F1` | Help |" in table
    assert "dismiss" not in table


def test_load_help_markdown_fills_in_the_keybindings_placeholder():
    markdown = load_help_markdown()

    assert HELP_KEYBINDINGS_PLACEHOLDER not in markdown
    assert "| `Ctrl+Q` | Quit |" in markdown
    assert "| `F1` | Help |" in markdown


async def fake_initialize(self):
    self.pars_mapping = {f"P{i:05d} [desc]": f"P{i:05d}" for i in range(50)}
    self.options = sorted(self.pars_mapping.keys())
    self._current_matches = self.options
    self.search_fields = [self._option_search_fields(opt) for opt in self.options]
    self.install_screen(must_app.MainScreen(), name="main")
    self.push_screen("main")


def _run_on_main_screen(monkeypatch, test, size=(100, 30)):
    monkeypatch.setattr(must_app.MUSTApp, "_initialize_and_show_main_screen", fake_initialize)

    async def main():
        app = must_app.MUSTApp()
        async with app.run_test(size=size) as pilot:
            while not isinstance(app.screen, must_app.MainScreen):
                await pilot.pause(0.05)
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(main())


def test_f1_opens_help_even_while_the_search_box_has_focus(monkeypatch):
    async def test(app, pilot):
        search_input = app.screen.query_one("#input-search", Input)
        search_input.focus()
        await pilot.pause()

        await pilot.press("f1")
        await pilot.pause()

        assert isinstance(app.screen, HelpDialog)
        assert search_input.value == ""  # F1 wasn't typed into the search box

    _run_on_main_screen(monkeypatch, test)


def test_help_dialog_content_is_scrollable(monkeypatch):
    async def test(app, pilot):
        app.push_screen(HelpDialog(load_help_markdown()))
        await pilot.pause()
        await pilot.pause()

        scroller = app.screen.query_one(VerticalScroll)
        assert scroller.max_scroll_y > 0

        markdown = app.screen.query_one(Markdown)
        assert "Ctrl+J" in markdown.source

    _run_on_main_screen(monkeypatch, test, size=(80, 24))


def test_escape_dismisses_the_help_dialog(monkeypatch):
    async def test(app, pilot):
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, HelpDialog)

        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, must_app.MainScreen)

    _run_on_main_screen(monkeypatch, test)


def test_close_button_dismisses_the_help_dialog(monkeypatch):
    async def test(app, pilot):
        await pilot.press("f1")
        await pilot.pause()

        close_button = app.screen.query_one("#btn-dialog-close", Button)
        await pilot.click(close_button)
        await pilot.pause()

        assert isinstance(app.screen, must_app.MainScreen)

    _run_on_main_screen(monkeypatch, test)
