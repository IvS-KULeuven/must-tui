import asyncio
import datetime as dt

import pytest
from textual import on
from textual.app import App
from textual.widgets import Checkbox, Input

import must_tui.must_app as must_app
from must_tui.time_range_input import TimeRange, TimeRangeInput, parse_datetime

START = dt.datetime(2025, 12, 2)
END = dt.datetime(2025, 12, 5)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2025-10-01 13:45:07", dt.datetime(2025, 10, 1, 13, 45, 7)),
        ("2025-10-01 13:45", dt.datetime(2025, 10, 1, 13, 45)),
        ("2025-10-01", dt.datetime(2025, 10, 1)),
        ("2025-10-01T13:45:07", dt.datetime(2025, 10, 1, 13, 45, 7)),
        ("  2025-03-09   08:00  ", dt.datetime(2025, 3, 9, 8)),
        ("NOW", dt.datetime(2026, 10, 11, 9, 30, 15)),
    ],
)
def test_parse_datetime_accepts(text, expected):
    assert parse_datetime(text, now=dt.datetime(2026, 10, 11, 9, 30, 15, 123456)) == expected


@pytest.mark.parametrize(
    "text", ["", "2025-13-01", "2025-11-31", "2025-10", "yesterday", "2025-10-01 25:00", "0999-01-01", "0025-10-01"]
)
def test_parse_datetime_rejects(text):
    with pytest.raises(ValueError, match="not a valid date-time"):
        parse_datetime(text)


class _Host(App):
    def __init__(self):
        super().__init__()
        self.changes = []

    def compose(self):
        yield TimeRangeInput(START, END, id="time-range")

    @on(TimeRangeInput.Changed, "#time-range")
    def changed(self, event):
        self.changes.append(event.time_range)


async def _type_into(pilot, field_id, text, confirm="enter"):
    field = pilot.app.query_one(field_id, Input)
    field.focus()
    await pilot.pause()
    field.value = ""
    await pilot.press(*text)
    await pilot.pause()
    changes_before_confirm = list(pilot.app.changes)
    await pilot.press(confirm)
    await pilot.pause()
    return field, changes_before_confirm


def _run(test):
    async def main():
        app = _Host()
        async with app.run_test(size=(120, 10)) as pilot:
            await pilot.pause()
            await test(app, pilot)

    asyncio.run(main())


def test_typing_sends_nothing_until_enter_then_one_change():
    async def test(app, pilot):
        _, before_enter = await _type_into(pilot, "#time-range-start", "2025-10-01 06:00")
        assert before_enter == []
        assert app.changes == [TimeRange(dt.datetime(2025, 10, 1, 6), END)]
        # the field is normalized to the full format
        assert app.query_one("#time-range-start", Input).value == "2025-10-01 06:00:00"

    _run(test)


def test_leaving_the_field_also_applies_it_but_only_once():
    async def test(app, pilot):
        await _type_into(pilot, "#time-range-end", "2025-12-04", confirm="enter")
        await pilot.press("tab")
        await pilot.pause()
        assert app.changes == [TimeRange(START, dt.datetime(2025, 12, 4))]

    _run(test)


def test_invalid_text_is_marked_and_keeps_the_range():
    async def test(app, pilot):
        field, _ = await _type_into(pilot, "#time-range-start", "2025-11-31")
        assert app.changes == []
        assert field.has_class("-invalid")
        assert app.query_one(TimeRangeInput).time_range == TimeRange(START, END)
        await pilot.press("backspace")  # editing clears the mark
        await pilot.pause()
        assert not field.has_class("-invalid")

    _run(test)


def test_start_after_end_is_rejected():
    async def test(app, pilot):
        field, _ = await _type_into(pilot, "#time-range-start", "2025-12-06")
        assert app.changes == []
        assert field.has_class("-invalid")

    _run(test)


def test_lock_keeps_the_duration():
    async def test(app, pilot):
        app.query_one("#time-range-lock", Checkbox).value = True
        await _type_into(pilot, "#time-range-start", "2025-10-01")
        assert app.changes == [TimeRange(dt.datetime(2025, 10, 1), dt.datetime(2025, 10, 4))]
        assert app.query_one("#time-range-end", Input).value == "2025-10-04 00:00:00"

    _run(test)


def test_lock_beyond_the_last_year_is_rejected():
    async def test(app, pilot):
        app.query_one("#time-range-lock", Checkbox).value = True
        field, _ = await _type_into(pilot, "#time-range-start", "9999-12-31")
        assert app.changes == []
        assert field.has_class("-invalid")

    _run(test)


def test_confirming_an_unchanged_value_sends_nothing():
    async def test(app, pilot):
        await _type_into(pilot, "#time-range-start", "2025-12-02 00:00")
        assert app.changes == []

    _run(test)


def test_set_time_range_from_code_sends_nothing():
    async def test(app, pilot):
        widget = app.query_one(TimeRangeInput)
        widget.set_time_range(TimeRange(dt.datetime(2025, 1, 1), dt.datetime(2025, 1, 2)))
        await pilot.pause()
        assert app.changes == []
        assert app.query_one("#time-range-start", Input).value == "2025-01-01 00:00:00"

    _run(test)


class _FakePlotter:
    def __init__(self, series):
        self.labels = [name for name, _, _ in series]
        self.time = [t for _, t, _ in series]
        self.data = [v for _, _, v in series]
        self.xlimits = self.ylimits = None

    def clear_plot(self):
        self.labels, self.time, self.data = [], [], []

    def set_xlimits(self, start, end):
        self.xlimits = (start, end)

    def set_ylimits(self, ymin, ymax):
        self.ylimits = (ymin, ymax)

    async def update(self, name, timestamps, values, _time_range):
        self.labels.append(name)
        self.time.append(timestamps)
        self.data.append(values)


def _app_for_reload(monkeypatch, plotted, server_data, during_fetch=None):
    app = must_app.MUSTApp()
    app.time_range = TimeRange(dt.datetime(2025, 10, 1), dt.datetime(2025, 10, 2))
    app.plot_widget = _FakePlotter(plotted)
    app.notifications = []
    app.notify = lambda message, **kwargs: app.notifications.append(message)
    requests = []

    async def fake_get_parameter_data(_ctx, _provider, name, start, end, paginated=False):
        requests.append((name, start, end))
        if during_fetch:
            during_fetch(app)
        if name in server_data:
            yield {"content": [{"data": [{"date": str(ms), "value": v} for ms, v in server_data[name]]}]}

    monkeypatch.setattr(must_app, "get_parameter_data", fake_get_parameter_data)
    return app, requests


def _ms(*args):
    return int(dt.datetime(*args).timestamp() * 1000)


def test_reload_fetches_every_plotted_parameter_once_for_the_new_range(monkeypatch):
    old = [dt.datetime(2025, 12, 2)]
    plotted = [("A", old, [1.0]), ("B", old, [2.0]), ("A", old, [1.0])]
    server_data = {"A": [(_ms(2025, 10, 1, 12), 5.0)], "B": [(_ms(2025, 10, 1, 13), 7.0)]}
    app, requests = _app_for_reload(monkeypatch, plotted, server_data)

    asyncio.run(app._reload_plotted_parameters())

    period = ("2025-10-01 00:00:00", "2025-10-02 00:00:00")
    assert requests == [("A", *period), ("B", *period)]
    assert app.plot_widget.labels == ["A", "B"]
    assert app.plot_widget.data == [[5.0], [7.0]]
    assert app.plot_widget.xlimits == (app.time_range.start, app.time_range.end)
    assert app.plot_widget.ylimits == (4.0, 8.0)
    assert app.notifications[0].startswith(f"Reloading 2 plotted parameter(s) for {period[0]} – {period[1]}")
    assert app.notifications[-1].startswith("Reloaded 2 of 2 parameter(s)")


def test_reload_removes_parameters_without_data_and_says_so(monkeypatch):
    old = [dt.datetime(2025, 12, 2)]
    app, _ = _app_for_reload(
        monkeypatch, [("A", old, [1.0]), ("B", old, [2.0])], {"A": [(_ms(2025, 10, 1, 12), 5.0)]}
    )

    asyncio.run(app._reload_plotted_parameters())

    assert app.plot_widget.labels == ["A"]
    assert "No data in this range for: B" in app.notifications[-1]


def test_reload_with_nothing_plotted_does_nothing(monkeypatch):
    app, requests = _app_for_reload(monkeypatch, [], {})

    asyncio.run(app._reload_plotted_parameters())

    assert requests == [] and app.notifications == []


def test_reload_respects_clear_and_selection_during_the_fetch(monkeypatch):
    old = [dt.datetime(2025, 12, 2)]
    new = [dt.datetime(2025, 10, 1, 12)]
    server_data = {"A": [(_ms(2025, 10, 1, 12), 5.0)]}

    def clear_then_select_c(app):
        app.plot_widget.clear_plot()
        app.plot_widget.labels, app.plot_widget.time, app.plot_widget.data = ["C"], [new], [[9.0]]

    app, _ = _app_for_reload(monkeypatch, [("A", old, [1.0])], server_data, during_fetch=clear_then_select_c)

    asyncio.run(app._reload_plotted_parameters())

    # A was cleared by the user while it was being fetched, C was selected meanwhile
    assert app.plot_widget.labels == ["C"]
    assert app.plot_widget.data == [[9.0]]
