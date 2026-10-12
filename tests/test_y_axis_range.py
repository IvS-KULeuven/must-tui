import asyncio
import datetime as dt

import must_tui.must_app as must_app
from must_tui.dialogs import YAxisRangeDialog
from must_tui.matplotlib_bridge import MatplotlibPlotter
from must_tui.must_app import MainScreen, MUSTApp, TimeRange, TimeRangePlotter


def _app_with_range(start, end):
    app = MUSTApp()
    app.time_range = TimeRange(start=dt.datetime(*start), end=dt.datetime(*end))
    app.warnings = []
    app.show_warning_dialog = app.warnings.append
    return app


def _ms(*args):
    return int(dt.datetime(*args).timestamp() * 1000)


class _FakePlotter:
    def __init__(self):
        self.ylimits = None

    def set_ylimits(self, ymin, ymax):
        self.ylimits = (ymin, ymax)


def test_apply_ylimits_does_nothing_when_auto():
    app = MUSTApp()
    plotter = _FakePlotter()

    app._apply_ylimits(plotter)

    assert plotter.ylimits is None


def test_apply_ylimits_sets_the_locked_range():
    app = MUSTApp()
    app.manual_ylimits = (0.0, 10.0)
    plotter = _FakePlotter()

    app._apply_ylimits(plotter)

    assert plotter.ylimits == (0.0, 10.0)


async def _build(plotter, par_name, values, ylimits=None):
    plotter.ylimits = ylimits
    t = [dt.datetime(2025, 1, 1) + dt.timedelta(seconds=i) for i in range(len(values))]
    await plotter.update(par_name, t, values, None)
    return plotter.plt.build()


def test_replot_reapplies_a_locked_y_range():
    """Regression test for the fix: `replot()` must re-apply `self.ylimits` when it's set, the same way it
    already re-applies `xlimits` after `clear_figure()` resets both. Without this, `set_ylimits()` is only
    ever visible for one frame -- the next `update()` (new parameter, marker change, time range reload) wipes
    it via `clear_figure()`, since `replot()` never issues `ylim()` again.
    """

    plotter = TimeRangePlotter(must_app.MustContext())
    plotter.plt.plotsize(60, 15)

    output = asyncio.run(_build(plotter, "PAR_A", [10.0, 12.0, 14.0, 16.0, 18.0], ylimits=(0.0, 1000.0)))

    assert "1000.0" in output


def test_replot_leaves_auto_scaling_when_no_lock():
    plotter = TimeRangePlotter(must_app.MustContext())
    plotter.plt.plotsize(60, 15)

    output = asyncio.run(_build(plotter, "PAR_A", [10.0, 12.0, 14.0, 16.0, 18.0], ylimits=None))

    assert "18.0" in output  # auto-scaled to the data's own range
    assert "1000.0" not in output


def test_reset_ylimits_clears_the_lock_and_goes_back_to_auto():
    plotter = TimeRangePlotter(must_app.MustContext())
    plotter.plt.plotsize(60, 15)
    asyncio.run(_build(plotter, "PAR_A", [10.0, 12.0, 14.0, 16.0, 18.0], ylimits=(0.0, 1000.0)))

    plotter.reset_ylimits()

    assert plotter.ylimits is None
    assert "1000.0" not in plotter.plt.build()


def test_matplotlib_reset_ylimits_clears_the_lock():
    plotter = MatplotlibPlotter()
    plotter.ylimits = (0.0, 10.0)

    plotter.reset_ylimits()

    assert plotter.ylimits is None


def test_plot_parameter_does_not_clip_other_traces_in_matplotlib_when_auto(monkeypatch):
    """Regression test for a real pre-existing bug: before this fix, each new selection called
    `set_ylimits(min(values)-1, max(values)+1)` for *that one parameter*, so plotting a second parameter with
    a very different range left the Matplotlib backend's `ylimits` pinned to the second parameter's range --
    clipping the first parameter's trace out of view (verified directly against `mpl_plotter.py`'s redraw
    logic, which always applies a non-None `ylimits`). Auto mode must leave `ylimits` as None so Matplotlib
    auto-fits everything, the same as the TUI plot already does.
    """

    app = _app_with_range((2025, 12, 2), (2025, 12, 5))
    app.plot_backend = "matplotlib"

    async def fake_get_parameter_data(_ctx, _provider, name, start, end, paginated=False, truncated=None):
        value = 5.0 if name == "A" else 1500.0  # very different ranges
        yield {"content": [{"data": [{"date": str(_ms(2025, 12, 2, 1)), "value": value}]}]}

    monkeypatch.setattr(must_app, "get_parameter_data", fake_get_parameter_data)

    asyncio.run(app._plot_parameter("A"))
    asyncio.run(app._plot_parameter("B"))

    assert app.matplotlib_plotter.ylimits is None


def test_plot_parameter_applies_the_locked_range_to_a_new_selection(monkeypatch):
    app = _app_with_range((2025, 12, 2), (2025, 12, 5))
    app.manual_ylimits = (0.0, 100.0)

    async def fake_get_parameter_data(_ctx, _provider, name, start, end, paginated=False, truncated=None):
        yield {"content": [{"data": [{"date": str(_ms(2025, 12, 2, 1)), "value": 5.0}]}]}

    monkeypatch.setattr(must_app, "get_parameter_data", fake_get_parameter_data)

    asyncio.run(app._plot_parameter("A"))

    assert app.plot_widget.ylimits == (0.0, 100.0)


def test_clear_plot_does_not_clear_a_locked_y_range():
    """Deliberate choice: clearing the plot's traces (Ctrl+L) is not the same as unlocking the Y range --
    the user asked for that range, not for it to reset just because the traces were cleared.
    """

    plotter = TimeRangePlotter(must_app.MustContext())
    plotter.ylimits = (0.0, 100.0)

    plotter.clear_plot()

    assert plotter.ylimits == (0.0, 100.0)


def test_matplotlib_clear_plot_does_not_clear_a_locked_y_range():
    plotter = MatplotlibPlotter()
    plotter.ylimits = (0.0, 100.0)

    plotter.clear_plot()

    assert plotter.ylimits == (0.0, 100.0)


# --- Dialog-level tests -------------------------------------------------------------------------------------


def test_dialog_apply_rejects_non_numeric_input():
    results = []

    async def main():
        from textual.app import App

        class Host(App):
            def compose(self):
                return []

        app = Host()
        async with app.run_test() as pilot:
            dialog = YAxisRangeDialog((0.0, 10.0))
            app.push_screen(dialog)
            await pilot.pause()

            from textual.widgets import Button, Input

            dialog.query_one("#inp-y-min", Input).value = "not a number"
            dialog.query_one("#btn-dialog-apply", Button).press()
            await pilot.pause()

            results.append(isinstance(app.screen, YAxisRangeDialog))
            results.append(str(dialog.query_one("#lbl-y-range-error")._Static__content))

    asyncio.run(main())
    assert results[0] is True
    assert "numeric" in results[1]


def test_dialog_apply_rejects_min_not_less_than_max():
    results = []

    async def main():
        from textual.app import App

        class Host(App):
            def compose(self):
                return []

        app = Host()
        async with app.run_test() as pilot:
            dialog = YAxisRangeDialog((0.0, 10.0))
            app.push_screen(dialog)
            await pilot.pause()

            from textual.widgets import Button, Input

            dialog.query_one("#inp-y-min", Input).value = "10"
            dialog.query_one("#inp-y-max", Input).value = "5"
            dialog.query_one("#btn-dialog-apply", Button).press()
            await pilot.pause()

            results.append(isinstance(app.screen, YAxisRangeDialog))

    asyncio.run(main())
    assert results[0] is True


# --- Full app, screen-based tests ---------------------------------------------------------------------------


async def fake_initialize(self):
    self.install_screen(MainScreen(), name="main")
    self.push_screen("main")


def _run_on_main_screen(monkeypatch, test, size=(100, 30)):
    monkeypatch.setattr(MUSTApp, "_initialize_and_show_main_screen", fake_initialize)

    async def main():
        app = MUSTApp()
        async with app.run_test(size=size) as pilot:
            while not isinstance(app.screen, MainScreen):
                await pilot.pause(0.05)
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(main())


def test_y_range_button_opens_the_dialog_prefilled_from_plotted_data(monkeypatch):
    async def test(app, pilot):
        t = [dt.datetime(2025, 1, 1) + dt.timedelta(seconds=i) for i in range(5)]
        await app.plot_widget.update("PAR_A", t, [1.0, 2.0, 3.0, 4.0, 5.0], app.time_range)

        app.open_y_range_dialog()
        await pilot.pause()

        from textual.widgets import Input

        assert isinstance(app.screen, YAxisRangeDialog)
        assert app.screen.query_one("#inp-y-min", Input).value == "1.0"
        assert app.screen.query_one("#inp-y-max", Input).value == "5.0"

    _run_on_main_screen(monkeypatch, test)


def test_y_range_dialog_with_nothing_plotted_shows_empty_fields(monkeypatch):
    async def test(app, pilot):
        from textual.widgets import Input

        assert app.plot_widget.data == []

        app.open_y_range_dialog()
        await pilot.pause()

        assert isinstance(app.screen, YAxisRangeDialog)
        assert app.screen.query_one("#inp-y-min", Input).value == ""
        assert app.screen.query_one("#inp-y-max", Input).value == ""

    _run_on_main_screen(monkeypatch, test)


def test_y_range_button_click_opens_the_dialog(monkeypatch):
    async def test(app, pilot):
        from textual.widgets import Button

        button = app.screen.query_one("#bt-y-range", Button)
        await pilot.click(button)
        await pilot.pause()

        assert isinstance(app.screen, YAxisRangeDialog)

    _run_on_main_screen(monkeypatch, test)


def test_applying_a_range_updates_the_app_and_the_plot(monkeypatch):
    async def test(app, pilot):
        from textual.widgets import Button, Input

        app.open_y_range_dialog()
        await pilot.pause()

        app.screen.query_one("#inp-y-min", Input).value = "0"
        app.screen.query_one("#inp-y-max", Input).value = "10"
        await pilot.click(app.screen.query_one("#btn-dialog-apply", Button))
        await pilot.pause()

        assert app.manual_ylimits == (0.0, 10.0)
        assert app.plot_widget.ylimits == (0.0, 10.0)
        assert isinstance(app.screen, MainScreen)

    _run_on_main_screen(monkeypatch, test)


def test_auto_button_clears_the_locked_range(monkeypatch):
    async def test(app, pilot):
        from textual.widgets import Button

        app.manual_ylimits = (0.0, 10.0)
        app.plot_widget.ylimits = (0.0, 10.0)

        app.open_y_range_dialog()
        await pilot.pause()
        await pilot.click(app.screen.query_one("#btn-dialog-auto", Button))
        await pilot.pause()

        assert app.manual_ylimits is None
        assert app.plot_widget.ylimits is None

    _run_on_main_screen(monkeypatch, test)


def test_cancel_button_leaves_everything_unchanged(monkeypatch):
    async def test(app, pilot):
        from textual.widgets import Button, Input

        app.manual_ylimits = (0.0, 10.0)
        app.plot_widget.ylimits = (0.0, 10.0)

        app.open_y_range_dialog()
        await pilot.pause()
        app.screen.query_one("#inp-y-min", Input).value = "999"  # edited, then cancelled -- must not apply
        await pilot.click(app.screen.query_one("#btn-dialog-cancel", Button))
        await pilot.pause()

        assert app.manual_ylimits == (0.0, 10.0)
        assert app.plot_widget.ylimits == (0.0, 10.0)
        assert isinstance(app.screen, MainScreen)

    _run_on_main_screen(monkeypatch, test)


def test_escape_cancels_the_dialog(monkeypatch):
    async def test(app, pilot):
        app.open_y_range_dialog()
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, MainScreen)

    _run_on_main_screen(monkeypatch, test)
