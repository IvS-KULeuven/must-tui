import asyncio
import datetime as dt

import pytest
from textual.widgets import Checkbox, Static

import must_tui.must_app as must_app
from must_tui.must_app import format_cache_age


@pytest.mark.parametrize(
    "age, expected",
    [
        (dt.timedelta(seconds=10), "just now"),
        (dt.timedelta(minutes=5), "5m ago"),
        (dt.timedelta(hours=3), "3h ago"),
        (dt.timedelta(days=2), "2d ago"),
    ],
)
def test_format_cache_age_formats_relative_to_now(age, expected):
    updated_at = dt.datetime.now(dt.timezone.utc) - age
    assert format_cache_age(updated_at) == expected


def test_format_cache_age_of_none_is_never():
    assert format_cache_age(None) == "never"


def _populate(app, count=50):
    app.pars_mapping = {f"P{i:05d} [desc]": f"P{i:05d}" for i in range(count)}
    app.options = sorted(app.pars_mapping.keys())
    app._current_matches = app.options
    app.search_fields = [app._option_search_fields(opt) for opt in app.options]


async def fake_initialize(self):
    self.must_ctx.authenticated = True
    _populate(self)
    self.install_screen(must_app.MainScreen(), name="main")
    self.push_screen("main")


def _run_on_main_screen(monkeypatch, test, size=(120, 40)):
    monkeypatch.setattr(must_app.MUSTApp, "_initialize_and_show_main_screen", fake_initialize)

    async def main():
        app = must_app.MUSTApp()
        async with app.run_test(size=size) as pilot:
            while not isinstance(app.screen, must_app.MainScreen):
                await pilot.pause(0.05)
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(main())


def _shown_keys(app):
    return {key for key, binding in app.active_bindings.items() if binding.binding.show}


def test_footer_bindings_are_the_same_regardless_of_focus(monkeypatch):
    async def test(app, pilot):
        shown_with_input_focus = _shown_keys(app)

        checkbox = app.screen.query_one("#regex-checkbox", Checkbox)
        checkbox.focus()
        await pilot.pause()
        shown_with_checkbox_focus = _shown_keys(app)

        assert shown_with_input_focus == shown_with_checkbox_focus == {"f1", "ctrl+q", "ctrl+l", "ctrl+z"}

    _run_on_main_screen(monkeypatch, test)


def test_non_footer_bindings_are_hidden_but_still_active(monkeypatch):
    async def test(app, pilot):
        checkbox = app.screen.query_one("#regex-checkbox", Checkbox)
        checkbox.focus()
        await pilot.pause()

        # They're still active (usable) bindings, just not advertised in the footer.
        hidden = {"ctrl+j", "f2", "f3", "f4", "f5", "ctrl+r"}
        assert hidden <= set(app.active_bindings)
        assert hidden.isdisjoint(_shown_keys(app))

    _run_on_main_screen(monkeypatch, test)


def test_ctrl_l_clears_the_plot_regardless_of_focus(monkeypatch):
    async def test(app, pilot):
        # No bare letters remain in BINDINGS, so this must work the same whether the search Input (which
        # swallows bare letters as typed text) or some other widget has focus.
        app.plot_widget.labels = ["X"]
        await pilot.press("ctrl+l")
        assert app.plot_widget.labels == []

        checkbox = app.screen.query_one("#regex-checkbox", Checkbox)
        checkbox.focus()
        await pilot.pause()

        app.plot_widget.labels = ["X"]
        await pilot.press("ctrl+l")
        assert app.plot_widget.labels == []

    _run_on_main_screen(monkeypatch, test)


def test_status_bar_shows_provider_connection_state_and_catalog_size(monkeypatch):
    monkeypatch.setattr(must_app, "get_parameter_cache_updated_at", lambda data_provider=None: None)

    async def test(app, pilot):
        status_bar = app.screen.query_one("#status-bar", Static)
        text = str(status_bar._Static__content)
        assert "PLATO" in text
        assert "online" in text
        assert "50 parameters" in text
        assert "never" in text

    _run_on_main_screen(monkeypatch, test)


def test_status_bar_shows_offline_when_not_authenticated(monkeypatch):
    monkeypatch.setattr(must_app, "get_parameter_cache_updated_at", lambda data_provider=None: None)

    async def offline_initialize(self):
        self.must_ctx.authenticated = False
        _populate(self, count=0)
        self.install_screen(must_app.MainScreen(), name="main")
        self.push_screen("main")

    monkeypatch.setattr(must_app.MUSTApp, "_initialize_and_show_main_screen", offline_initialize)

    async def main():
        app = must_app.MUSTApp()
        async with app.run_test(size=(120, 40)) as pilot:
            while not isinstance(app.screen, must_app.MainScreen):
                await pilot.pause(0.05)
            await pilot.pause()

            status_bar = app.screen.query_one("#status-bar", Static)
            text = str(status_bar._Static__content)
            assert "offline" in text
            assert "0 parameters" in text

    asyncio.run(main())


def test_status_bar_updates_after_a_catalog_refresh(monkeypatch):
    monkeypatch.setattr(must_app, "get_parameter_cache_updated_at", lambda data_provider=None: None)

    async def test(app, pilot):
        app._apply_parameter_catalog({name: {"description": ""} for name in [f"Q{i}" for i in range(5)]})
        await pilot.pause()

        status_bar = app.screen.query_one("#status-bar", Static)
        text = str(status_bar._Static__content)
        assert "5 parameters" in text

    _run_on_main_screen(monkeypatch, test)


def test_status_bar_populates_through_the_real_startup_path(monkeypatch):
    """Regression test: `switch_screen()` mounts a screen's children in a deferred task, not synchronously.

    Calling `_update_status_bar()` right after `switch_screen("main")` (as the real startup code used to)
    found `self.screen` already updated but `#status-bar` not yet in the DOM, silently failing (caught by
    `_update_status_bar()`'s own `NoMatches` guard) and leaving the status bar blank for the whole session.
    `MainScreen.on_mount()` is the fix -- it only fires once the screen's children actually exist. This test
    drives the real `_initialize_and_show_main_screen()` (not a test fake that bypasses `switch_screen`).
    """

    async def fake_login(config_file=None):
        return must_app.MustContext(base_url="http://x", token="t", authenticated=True)

    async def fake_load_catalog(data_provider, ctx, force_refresh=False):
        return {f"P{i}": {"description": f"d{i}"} for i in range(5)}

    monkeypatch.setattr(must_app, "login", fake_login)
    monkeypatch.setattr(must_app, "load_parameter_catalog_async", fake_load_catalog)
    # A fresh, non-stale cache timestamp: otherwise `_initialize_and_show_main_screen()`'s own stale-cache
    # check schedules a second, later `_apply_parameter_catalog()` call (via `refresh_parameter_catalog()`)
    # that calls `_update_status_bar()` itself once the screen is *definitely* mounted by then, masking the
    # exact race this test exists to catch.
    fresh = dt.datetime.now(dt.timezone.utc)
    monkeypatch.setattr(must_app, "get_parameter_cache_updated_at", lambda data_provider=None: fresh)

    async def main():
        app = must_app.MUSTApp()
        async with app.run_test(size=(120, 40)) as pilot:
            while not isinstance(app.screen, must_app.MainScreen):
                await pilot.pause(0.05)
            await pilot.pause()

            status_bar = app.screen.query_one("#status-bar", Static)
            assert "5 parameters" in str(status_bar._Static__content)

    asyncio.run(main())
