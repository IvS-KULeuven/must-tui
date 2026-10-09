import asyncio
import datetime as dt

import aiohttp
from aiohttp import web

import must_tui.must as must
from must_tui.matplotlib_bridge import MatplotlibPlotter
from must_tui.must_app import downsample_min_max


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
    from types import SimpleNamespace

    from whenever import PlainDateTime

    import must_tui.must_app as must_app

    app = must_app.MUSTApp()
    app.time_range = must_app.TimeRange(start=PlainDateTime(*start), end=PlainDateTime(*end))
    app.warnings = []
    app.show_warning_dialog = app.warnings.append
    app.later = []
    app.call_later = lambda *args: app.later.append(args)
    return app, SimpleNamespace, PlainDateTime


def test_plot_parameter_refuses_start_after_end(monkeypatch):
    import must_tui.must_app as must_app

    app, _, _ = _app_with_range((2026, 10, 9, 18), (2025, 12, 5))
    fetched = []

    async def fake_get_parameter_data(*args, **kwargs):
        fetched.append(args)
        yield {}

    monkeypatch.setattr(must_app, "get_parameter_data", fake_get_parameter_data)

    asyncio.run(app._plot_parameter("CNKA0966"))

    assert fetched == []
    assert len(app.warnings) == 1 and "must be before the end time" in app.warnings[0]


def test_range_change_with_start_after_end_does_not_touch_plot_limits():
    app, SimpleNamespace, PlainDateTime = _app_with_range((2025, 12, 2), (2025, 12, 5))
    event = SimpleNamespace(start=PlainDateTime(2026, 10, 9, 18), end=PlainDateTime(2025, 12, 5))

    asyncio.run(app.on_datetime_range_changed(event))

    assert app.time_range.start == event.start
    assert app.later == []


def test_range_change_with_cleared_picker_keeps_previous_range():
    app, SimpleNamespace, PlainDateTime = _app_with_range((2025, 12, 2), (2025, 12, 5))
    previous = app.time_range

    asyncio.run(app.on_datetime_range_changed(SimpleNamespace(start=None, end=None)))

    assert app.time_range == previous
    assert app.later == []
