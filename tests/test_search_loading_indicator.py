import asyncio

from textual.widgets import Input, OptionList

import must_tui.must_app as must_app


def _populate(app, count=200):
    app.pars_mapping = {f"P{i:05d} [desc]": f"P{i:05d}" for i in range(count)}
    app.options = sorted(app.pars_mapping.keys())
    app._current_matches = app.options
    app.search_fields = [app._option_search_fields(opt) for opt in app.options]


async def fake_initialize(self):
    _populate(self)
    self.install_screen(must_app.MainScreen(), name="main")
    self.push_screen("main")


def _run_on_main_screen(monkeypatch, test, size=(100, 40)):
    monkeypatch.setattr(must_app.MUSTApp, "_initialize_and_show_main_screen", fake_initialize)

    async def main():
        app = must_app.MUSTApp()
        async with app.run_test(size=size) as pilot:
            while not isinstance(app.screen, must_app.MainScreen):
                await pilot.pause(0.05)
            await pilot.pause()

            option_list = app.screen.query_one(OptionList)
            search_input = app.screen.query_one("#input-search", Input)

            await test(app, pilot, search_input, option_list)

    asyncio.run(main())


# `pilot.press()`/`pilot.pause()` wait for the app to go fully idle, which includes draining the debounced
# search worker's `asyncio.sleep` -- so they can't reliably observe the transient "still searching" state (see
# CLAUDE.md's note on `Pilot.press()` overstating key latency). These tests instead call `update_search()`
# directly, the same plain method a keystroke's `Input.Changed` handler calls, which is deterministic.


def test_typing_shows_a_loading_indicator_until_the_search_settles(monkeypatch):
    async def test(app, pilot, search_input, option_list):
        search_input.value = "P0"
        app.update_search()
        assert option_list.loading is True

        await asyncio.sleep(must_app.SEARCH_DEBOUNCE + 0.2)
        await pilot.pause()
        assert option_list.loading is False

    _run_on_main_screen(monkeypatch, test)


def test_clearing_the_search_box_hides_the_indicator_immediately(monkeypatch):
    async def test(app, pilot, search_input, option_list):
        search_input.value = "P0"
        app.update_search()
        assert option_list.loading is True

        search_input.value = ""
        app.update_search()
        assert option_list.loading is False
        await pilot.pause()  # let the Input.Changed message this value assignment posts drain before teardown

    _run_on_main_screen(monkeypatch, test)


def test_retyping_before_the_debounce_elapses_keeps_the_indicator_on(monkeypatch):
    async def test(app, pilot, search_input, option_list):
        search_input.value = "P"
        app.update_search()
        assert option_list.loading is True
        first_generation = app._search_generation

        # A second keystroke, still before anything let the first worker settle, cancels and supersedes it.
        search_input.value = "P0"
        app.update_search()
        assert option_list.loading is True
        assert app._search_generation == first_generation + 1

        await asyncio.sleep(must_app.SEARCH_DEBOUNCE + 0.2)
        await pilot.pause()

        # The stale (cancelled) first worker's `finally` must not have cleared the indicator for the second
        # search while it was still in flight -- that's the race the generation counter guards against.
        assert option_list.loading is False

    _run_on_main_screen(monkeypatch, test)


def test_a_stale_search_worker_does_not_clear_the_indicator_for_a_newer_one(monkeypatch):
    async def test(app, pilot, search_input, option_list):
        option_list.loading = True
        app._search_generation = 5

        # Directly exercise the worker's own staleness check (its `finally` block), as if an old, cancelled
        # worker (generation 3) finished running after a newer search (generation 5) already took over.
        worker = app.search_options("x", "regex", 3)
        await worker.wait()

        assert option_list.loading is True

    _run_on_main_screen(monkeypatch, test)
