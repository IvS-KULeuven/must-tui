import asyncio
import datetime as dt

import aiohttp
from aiohttp import web

import plotext
import pytest
from textual.app import App
from textual.widgets import OptionList

import must_tui.must as must
import must_tui.must_app as must_app
from must_tui.matplotlib_bridge import MatplotlibPlotter, plot_title
from must_tui.must_app import (
    PLOT_COLORS,
    ParameterMetadata,
    TimeRangePlotter,
    colored_plot_title,
    downsample_min_max,
    parameter_data_range,
    series_color,
)
from must_tui.time_range_input import TimeRange


def _series(count):
    start = dt.datetime(2025, 12, 2)
    timestamps = [start + dt.timedelta(seconds=15 * i) for i in range(count)]
    values = [float(i % 100) for i in range(count)]
    return timestamps, values


def test_downsample_keeps_short_series_unchanged():
    timestamps, values = _series(1000)

    assert downsample_min_max(timestamps, values, max_points=1000) == (timestamps, values)


def test_downsample_limits_points_and_keeps_spikes_in_time_order():
    timestamps, values = _series(17298)
    values[12345] = 1e6
    values[54] = -1e6

    ds_time, ds_values = downsample_min_max(timestamps, values, max_points=1000)

    assert len(ds_values) <= 1000
    assert len(ds_time) == len(ds_values)
    assert max(ds_values) == 1e6
    assert min(ds_values) == -1e6
    assert ds_time == sorted(ds_time)


@pytest.mark.parametrize(
    "labels, expected",
    [
        ([], "Parameter Data Plot"),
        (["PAR_A"], "Parameter: PAR_A"),
        (["PAR_A", "PAR_A"], "Parameter: PAR_A"),  # same parameter selected twice
        (["PAR_A", "PAR_B"], "Parameters: PAR_A, PAR_B"),
        (["PAR_A", "PAR_B", "PAR_A"], "Parameters: PAR_A, PAR_B"),  # re-selected, order of first appearance kept
    ],
)
def test_plot_title(labels, expected):
    assert plot_title(labels) == expected


def test_series_color_cycles_through_the_palette():
    assert series_color(0) == PLOT_COLORS[0]
    assert series_color(1) == PLOT_COLORS[1]
    assert series_color(len(PLOT_COLORS)) == PLOT_COLORS[0]  # wraps around


@pytest.mark.parametrize(
    "labels, expected_plain",
    [
        ([], "Parameter Data Plot"),
        (["PAR_A"], "Parameter: PAR_A"),
        (["PAR_A", "PAR_A"], "Parameter: PAR_A"),
        (["PAR_A", "PAR_B"], "Parameters: PAR_A, PAR_B"),
        (["PAR_A", "PAR_B", "PAR_A"], "Parameters: PAR_A, PAR_B"),
    ],
)
def test_colored_plot_title_matches_plot_title_once_uncolored(labels, expected_plain):
    assert plotext.uncolorize(colored_plot_title(labels)) == expected_plain


def test_colored_plot_title_colors_each_name_to_match_its_series():
    colored = colored_plot_title(["PAR_A", "PAR_B"])

    assert plotext.colorize("PAR_A", color=series_color(0)) in colored
    assert plotext.colorize("PAR_B", color=series_color(1)) in colored


def test_colored_plot_title_uses_the_first_occurrences_color_for_a_repeated_label():
    colored = colored_plot_title(["PAR_A", "PAR_B", "PAR_A"])

    # PAR_A first appears at index 0, not at its repeat at index 2
    assert plotext.colorize("PAR_A", color=series_color(0)) in colored
    assert plotext.colorize("PAR_A", color=series_color(2)) not in colored


def test_time_range_plotter_title_covers_all_plotted_parameters():
    plotter = TimeRangePlotter(must.MustContext())
    timestamps, values = _series(3)

    asyncio.run(plotter.update("PAR_A", timestamps, values, None))
    assert plotext.uncolorize(plotter.title) == "Parameter: PAR_A"
    assert plotter.title != "Parameter: PAR_A"  # actually colored, not plain text

    asyncio.run(plotter.update("PAR_B", timestamps, values, None))
    assert plotext.uncolorize(plotter.title) == "Parameters: PAR_A, PAR_B"

    plotter.clear_plot()
    assert plotter.title == "Parameter Data Plot"


def test_time_range_plotter_renders_when_the_plotted_data_is_outside_xlimits():
    # Regression test: passing label= to plotext's plot() (for a legend) crashes with IndexError when a
    # labelled series has no point within the current xlim -- e.g. briefly while reloading after a time range
    # change, when set_xlimits() has already moved to the new range but the old out-of-range data is still
    # plotted. Do not add label= back to TimeRangePlotter.replot()'s plotter.plot() call without fixing this.
    plotter = TimeRangePlotter(must.MustContext())
    plotter.plt.plotsize(131, 18)
    timestamps = [dt.datetime(2025, 7, 23, 14, 29, 14) + dt.timedelta(minutes=i) for i in range(40)]
    values = [1000.0 + i * 50 for i in range(40)]

    plotter.set_xlimits(timestamps[0] + dt.timedelta(days=10), timestamps[-1] + dt.timedelta(days=10))
    plotter.set_ylimits(min(values) - 1, max(values) + 1)
    asyncio.run(plotter.update("A9BA0004", timestamps, values, None))

    plotter.plt.build()  # must not raise


def test_matplotlib_plotter_title_covers_all_plotted_parameters():
    plotter = MatplotlibPlotter()
    plotter._stdin = _CountingPipe()
    timestamps, values = _series(3)

    asyncio.run(plotter.update("PAR_A", timestamps, values, None))
    assert plotter.title == "Parameter: PAR_A"

    asyncio.run(plotter.update("PAR_B", timestamps, values, None))
    assert plotter.title == "Parameters: PAR_A, PAR_B"

    plotter.clear_plot()
    assert plotter.title == "Parameter Data Plot"


def test_parameter_data_range_uses_first_and_last_sample():
    metadata = {"first-sample": "2024-01-01 00:00:00", "last-sample": "2024-06-01 12:00:00", "name": "PAR_X"}

    assert parameter_data_range(metadata) == TimeRange(dt.datetime(2024, 1, 1), dt.datetime(2024, 6, 1, 12))


@pytest.mark.parametrize("metadata", [{}, {"first-sample": "2024-01-01 00:00:00"}, {"last-sample": None}])
def test_parameter_data_range_rejects_missing_samples(metadata):
    with pytest.raises(ValueError, match="Select a parameter"):
        parameter_data_range(metadata)


def test_parameter_data_range_rejects_first_not_before_last():
    metadata = {"first-sample": "2024-06-01 00:00:00", "last-sample": "2024-01-01 00:00:00"}

    with pytest.raises(ValueError, match="is not before its last sample"):
        parameter_data_range(metadata)


class _CountingPipe:
    def __init__(self):
        self.writes = 0

    def write(self, _text):
        self.writes += 1

    def flush(self):
        pass


def test_matplotlib_batch_sends_state_once():
    plotter = MatplotlibPlotter()
    plotter._stdin = pipe = _CountingPipe()
    timestamps, values = _series(10)

    async def add_series():
        with plotter.batch():
            plotter.set_xlimits(timestamps[0], timestamps[-1])
            plotter.set_ylimits(0.0, 100.0)
            await plotter.update("P1", timestamps, values, None)

    asyncio.run(add_series())

    assert pipe.writes == 1
    assert plotter.labels == ["P1"]


class _FakePopen:
    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode


def test_matplotlib_plotter_is_running_reflects_the_process():
    plotter = MatplotlibPlotter()
    assert plotter.is_running() is False  # never started

    plotter._process = _FakePopen(returncode=None)
    assert plotter.is_running() is True  # still alive

    plotter._process = _FakePopen(returncode=0)
    assert plotter.is_running() is False  # exited (e.g. window closed by the user)


class _FakeMatplotlibPlotter:
    def __init__(self, running):
        self._running = running
        self.closed = False

    def is_running(self):
        return self._running

    def close(self):
        self.closed = True
        self._running = False


def _app_with_matplotlib_backend(running, plot_backend="matplotlib"):
    app = must_app.MUSTApp()
    app.plot_backend = plot_backend
    app.matplotlib_plotter = _FakeMatplotlibPlotter(running)
    app.notifications = []
    app.notify = lambda message, **kwargs: app.notifications.append(message)
    return app


def test_check_matplotlib_window_reverts_when_the_window_was_closed():
    app = _app_with_matplotlib_backend(running=False)

    app._check_matplotlib_window()

    assert app.plot_backend == "textual"
    assert app.matplotlib_plotter.closed is True
    assert app.notifications and "closed" in app.notifications[0]


def test_check_matplotlib_window_does_nothing_while_the_window_is_open():
    app = _app_with_matplotlib_backend(running=True)

    app._check_matplotlib_window()

    assert app.plot_backend == "matplotlib"
    assert app.notifications == []


def test_check_matplotlib_window_does_nothing_on_the_tui_backend():
    app = _app_with_matplotlib_backend(running=False, plot_backend="textual")

    app._check_matplotlib_window()

    assert app.plot_backend == "textual"
    assert app.notifications == []


def test_must_request_returns_none_on_timeout(monkeypatch):
    monkeypatch.setattr(must, "REQUEST_TIMEOUT", aiohttp.ClientTimeout(total=None, sock_connect=0.2, sock_read=0.2))

    async def slow_handler(_request):
        await asyncio.sleep(2)
        return web.json_response({})

    async def run():
        app = web.Application()
        app.router.add_get("/slow", slow_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        try:
            ctx = must.MustContext(base_url=f"http://127.0.0.1:{port}", token="token", authenticated=True)
            started = asyncio.get_running_loop().time()
            result = await must.must_request(ctx, "/slow")
            return result, asyncio.get_running_loop().time() - started
        finally:
            await runner.cleanup()

    result, elapsed = asyncio.run(run())

    assert result is None
    assert elapsed < 1.5


def test_must_request_allows_slow_but_steady_transfer(monkeypatch):
    monkeypatch.setattr(must, "REQUEST_TIMEOUT", aiohttp.ClientTimeout(total=None, sock_connect=0.2, sock_read=0.2))

    async def streaming_handler(request):
        response = web.StreamResponse(headers={"content-type": "application/json"})
        await response.prepare(request)
        await response.write(b"[")
        for i in range(10):  # 1 s in total, but never more than 0.1 s without data
            await asyncio.sleep(0.1)
            await response.write(f"{',' if i else ''}{i}".encode())
        await response.write(b"]")
        await response.write_eof()
        return response

    async def run():
        app = web.Application()
        app.router.add_get("/stream", streaming_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        try:
            ctx = must.MustContext(base_url=f"http://127.0.0.1:{port}", token="token", authenticated=True)
            return await must.must_request(ctx, "/stream")
        finally:
            await runner.cleanup()

    assert asyncio.run(run()) == list(range(10))


def _app_with_range(start, end):
    app = must_app.MUSTApp()
    app.time_range = must_app.TimeRange(start=dt.datetime(*start), end=dt.datetime(*end))
    app.warnings = []
    app.show_warning_dialog = app.warnings.append
    return app


def test_plot_parameter_refuses_start_after_end(monkeypatch):
    app = _app_with_range((2026, 10, 9, 18), (2025, 12, 5))
    fetched = []

    async def fake_get_parameter_data(*args, **kwargs):
        fetched.append(args)
        yield {}

    monkeypatch.setattr(must_app, "get_parameter_data", fake_get_parameter_data)

    asyncio.run(app._plot_parameter("CNKA0966"))

    assert fetched == []
    assert len(app.warnings) == 1 and "must be before the end time" in app.warnings[0]


def test_toggle_plot_maximized_hides_everything_but_the_plot_and_its_controls(monkeypatch):
    async def fake_initialize(self):
        self.install_screen(must_app.MainScreen(), name="main")
        self.push_screen("main")

    monkeypatch.setattr(must_app.MUSTApp, "_initialize_and_show_main_screen", fake_initialize)

    async def run():
        app = must_app.MUSTApp()
        async with app.run_test(size=(160, 40)) as pilot:
            while not isinstance(app.screen, must_app.MainScreen):
                await pilot.pause(0.05)
            await pilot.pause()

            option_list = app.screen.query_one(OptionList)
            search_box = app.screen.query_one("#input-container")
            info = app.screen.query_one("#info-container")
            plot_controls = app.screen.query_one("#plot-controls")
            before_width = app.plot_widget.region.width

            await pilot.press("ctrl+z")  # works even though the search Input (not these) has focus by default
            await pilot.pause()

            assert app.plot_maximized is True
            assert option_list.display is False
            assert search_box.display is False
            assert info.display is False
            assert plot_controls.display is True
            assert app.plot_widget.display is True
            assert app.plot_widget.region.width > before_width  # the plot now fills the freed-up space

            await pilot.press("ctrl+z")
            await pilot.pause()

            assert app.plot_maximized is False
            assert option_list.display is True
            assert app.plot_widget.region.width == before_width

    asyncio.run(run())


class _MetadataHost(App):
    def compose(self):
        yield ParameterMetadata()


def _run_metadata(test):
    async def main():
        app = _MetadataHost()
        async with app.run_test() as pilot:
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(main())


def test_set_sample_count_shows_a_comma_formatted_number():
    async def test(app, pilot):
        metadata = app.query_one(ParameterMetadata)
        metadata.set_sample_count(123456)
        assert metadata.table.get_cell(must_app.PLOTTED_SAMPLES_FIELD, "value") == "123,456"

    _run_metadata(test)


def test_set_sample_count_none_shows_a_placeholder():
    async def test(app, pilot):
        metadata = app.query_one(ParameterMetadata)
        metadata.set_sample_count(42)
        metadata.set_sample_count(None)
        assert metadata.table.get_cell(must_app.PLOTTED_SAMPLES_FIELD, "value") == "–"

    _run_metadata(test)


def test_update_metadata_resets_the_sample_count():
    async def test(app, pilot):
        metadata = app.query_one(ParameterMetadata)
        metadata.set_sample_count(999)
        await metadata.update_metadata("PAR_X", {"description": "Some parameter"})
        assert metadata.table.get_cell(must_app.PLOTTED_SAMPLES_FIELD, "value") == "–"

    _run_metadata(test)


def test_show_sample_count_ignores_a_stale_parameter_name():
    app = must_app.MUSTApp()

    app._show_sample_count("SOME_PAR", 10)  # no screen at all: must not raise


def test_plot_parameter_shows_a_spinner_and_the_sample_count(monkeypatch):
    async def fake_initialize(self):
        self.install_screen(must_app.MainScreen(), name="main")
        self.push_screen("main")

    monkeypatch.setattr(must_app.MUSTApp, "_initialize_and_show_main_screen", fake_initialize)

    loading_during_fetch = []
    sample_epoch_ms = int(dt.datetime(2025, 12, 2).timestamp() * 1000)

    async def fake_get_parameter_data(*_args, **_kwargs):
        loading_during_fetch.append(app.plot_widget.loading)
        data = [{"date": str(sample_epoch_ms + h * 3_600_000), "value": float(h)} for h in range(24)]
        yield {"content": [{"data": data}]}

    monkeypatch.setattr(must_app, "get_parameter_data", fake_get_parameter_data)

    async def run():
        async with app.run_test(size=(120, 30)) as pilot:
            while not isinstance(app.screen, must_app.MainScreen):
                await pilot.pause(0.05)
            await pilot.pause()

            metadata = app.screen.query_one(ParameterMetadata)
            await metadata.update_metadata("PAR_X", {})
            app.time_range = TimeRange(dt.datetime(2025, 12, 2), dt.datetime(2025, 12, 3))

            assert app.plot_widget.loading is False
            await app.plot_parameter("PAR_X").wait()
            await pilot.pause()

            assert app.plot_widget.loading is False
            assert metadata.table.get_cell(must_app.PLOTTED_SAMPLES_FIELD, "value") == "24"

    app = must_app.MUSTApp()
    asyncio.run(run())

    assert loading_during_fetch == [True]
