import asyncio

from textual.widgets import Input, OptionList

import must_tui.must_app as must_app


def _populate(app, count=2000):
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

            app.notifications = []
            app.notify = lambda message, **kwargs: app.notifications.append(message)

            option_list = app.screen.query_one(OptionList)
            option_list.focus()
            await pilot.pause()

            await test(app, pilot, option_list)

    asyncio.run(main())


def test_selecting_the_hint_loads_every_match_and_notifies(monkeypatch):
    async def test(app, pilot, option_list):
        assert option_list.option_count == must_app.MAX_VISIBLE_OPTIONS + 1  # capped list + hint

        option_list.highlighted = must_app.MAX_VISIBLE_OPTIONS
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()  # let the deferred call_after_refresh load run

        assert option_list.option_count == len(app.options)
        assert any("2,000 matches" in message for message in app.notifications)

    _run_on_main_screen(monkeypatch, test)


def test_selecting_the_hint_does_not_select_a_parameter(monkeypatch):
    async def test(app, pilot, option_list):
        option_list.highlighted = must_app.MAX_VISIBLE_OPTIONS
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        info = app.screen.query_one(must_app.ParameterInfo)
        assert info.par_name == ""

    _run_on_main_screen(monkeypatch, test)


def test_show_all_uses_the_current_search_results_not_the_full_catalog(monkeypatch):
    async def test(app, pilot, option_list):
        search_input = app.screen.query_one("#input-search", Input)
        search_input.value = "P00"  # matches P00000..P00999 (the "00" thousands digits), still above the cap
        app.update_search()
        await asyncio.sleep(must_app.SEARCH_DEBOUNCE + 0.1)
        await pilot.pause()

        matches_count = len(app._current_matches)
        assert matches_count == 1000

        option_list.highlighted = must_app.MAX_VISIBLE_OPTIONS
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()

        assert option_list.option_count == matches_count

    _run_on_main_screen(monkeypatch, test)


def test_catalog_below_the_cap_has_no_hint(monkeypatch):
    async def small_initialize(self):
        _populate(self, count=50)
        self.install_screen(must_app.MainScreen(), name="main")
        self.push_screen("main")

    monkeypatch.setattr(must_app.MUSTApp, "_initialize_and_show_main_screen", small_initialize)

    async def main():
        app = must_app.MUSTApp()
        async with app.run_test(size=(100, 40)) as pilot:
            while not isinstance(app.screen, must_app.MainScreen):
                await pilot.pause(0.05)
            await pilot.pause()

            option_list = app.screen.query_one(OptionList)
            assert option_list.option_count == 50

    asyncio.run(main())
