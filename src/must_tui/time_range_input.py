"""A minimal start/end date-time input for MUST TUI.

Two plain text fields and a "Lock" checkbox. A field is only applied when the user presses Enter or leaves it, and
only when the resulting range is valid; then one `TimeRangeInput.Changed` message is posted. Typing does not send
anything, so the app never sees half-edited or start-after-end ranges. The Up/Down arrow keys step the date-time
component under the cursor (year, month, ..., second) in the focused field's text, without applying it.
"""

from __future__ import annotations

import calendar
import datetime
import re
from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Checkbox, Input, Label

DISPLAY_FORMAT = "%Y-%m-%d %H:%M:%S"
"""Format used to show a date-time in the fields."""

INPUT_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
"""Accepted input formats; a missing time or missing seconds means 0."""

_WORD_OFFSETS = {
    "today": datetime.timedelta(0),
    "yesterday": datetime.timedelta(days=-1),
    "tomorrow": datetime.timedelta(days=1),
}
"""Anchor words that snap to local midnight. `now` is handled separately, to the second."""

_OFFSET_UNITS = {"w": "weeks", "d": "days", "h": "hours", "m": "minutes", "s": "seconds"}
"""Units accepted in a relative offset. Months/years are excluded: a calendar month or year has no fixed duration."""

_OFFSET_TOKEN_RE = re.compile(r"(\d+)\s*([wdhms])", re.IGNORECASE)

_RELATIVE_RE = re.compile(
    r"^(?P<anchor>now|today|yesterday|tomorrow)?"
    r"(?:\s*(?P<sign>[+-])\s*(?P<offset>(?:\d+\s*[wdhms]\s*)+))?$",
    re.IGNORECASE,
)
"""An optional anchor word, optionally followed by a signed offset such as `-6h` or `+1w2d3h`.

A bare offset (no anchor word) is relative to `now`. The sign is mandatory on the offset, so e.g. `6h` alone
(ambiguous: before or after the anchor?) is not accepted, only `-6h` or `+6h`.
"""


def _parse_relative(text: str, now: datetime.datetime) -> datetime.datetime | None:
    """Resolve a relative expression like `now-6h`, `-2h30m` or `yesterday` to an absolute date-time.

    Returns `None` when `text` does not look like a relative expression at all, so the caller can try the
    absolute formats next.

    Raises:
        ValueError: when `text` is a syntactically valid relative expression that resolves out of range.
    """

    match = _RELATIVE_RE.match(text)
    if not match or not (match.group("anchor") or match.group("offset")):
        return None

    anchor_word = (match.group("anchor") or "now").casefold()
    if anchor_word == "now":
        anchor = now.replace(microsecond=0)
    else:
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        anchor = midnight + _WORD_OFFSETS[anchor_word]

    offset_text = match.group("offset")
    if offset_text:
        kwargs: dict[str, int] = {}
        for amount, unit in _OFFSET_TOKEN_RE.findall(offset_text):
            key = _OFFSET_UNITS[unit.lower()]
            kwargs[key] = kwargs.get(key, 0) + int(amount)
        try:
            delta = datetime.timedelta(**kwargs)
            anchor = anchor - delta if match.group("sign") == "-" else anchor + delta
        except OverflowError:
            raise ValueError(f"'{text}' is out of range.") from None

    if not (1000 <= anchor.year <= 9999):
        raise ValueError(f"'{text}' is out of range.") from None
    return anchor


@dataclass(frozen=True)
class TimeRange:
    start: datetime.datetime
    end: datetime.datetime

    def is_valid(self) -> bool:
        return self.start < self.end


def format_datetime(value: datetime.datetime) -> str:
    return value.strftime(DISPLAY_FORMAT)


def parse_datetime(text: str, now: datetime.datetime | None = None) -> datetime.datetime:
    """Parse a date-time typed by the user.

    Accepts `YYYY-MM-DD HH:MM:SS`, `YYYY-MM-DD HH:MM`, `YYYY-MM-DD` (also with a `T` between date and time),
    `now`, `today`, `yesterday`, `tomorrow`, and any of those with a signed offset such as `now-6h`, `-2h30m`
    or `tomorrow+1d` (units: `w`eeks, `d`ays, `h`ours, `m`inutes, `s`econds; a bare offset is relative to `now`).

    Raises:
        ValueError: with a message that can be shown to the user.
    """

    # Only an ISO "T" between digits (e.g. "2025-10-01T13:45:07") is a separator; "TODAY"/"TOMORROW" must survive.
    text = " ".join(re.sub(r"(?<=\d)T(?=\d)", " ", text.strip()).split())
    now = now or datetime.datetime.now()

    relative = _parse_relative(text, now)
    if relative is not None:
        return relative

    for fmt in INPUT_FORMATS:
        try:
            value = datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
        # strftime() does not zero-pad years below 1000, so such dates can't be shown, plotted or sent to the server.
        if value.year >= 1000:
            return value

    raise ValueError(
        f"'{text}' is not a valid date-time; use YYYY-MM-DD [HH:MM[:SS]], 'now'/'today'/'yesterday'/'tomorrow', "
        "or one of those with an offset such as 'now-6h' or '-2h30m'."
    )


# Cursor-position ranges (inclusive) mapped to the date-time component they fall in or next to, for the fixed-width
# "YYYY-MM-DD HH:MM:SS" display format. E.g. a cursor at the trailing separator falls back to the component before it.
_SEGMENT_SPANS: tuple[tuple[int, int, str], ...] = (
    (0, 4, "year"),
    (5, 7, "month"),
    (8, 10, "day"),
    (11, 13, "hour"),
    (14, 16, "minute"),
    (17, 19, "second"),
)

_UNIT_DELTAS = {
    "day": datetime.timedelta(days=1),
    "hour": datetime.timedelta(hours=1),
    "minute": datetime.timedelta(minutes=1),
    "second": datetime.timedelta(seconds=1),
}


def _segment_at(cursor: int) -> str:
    for start, end, unit in _SEGMENT_SPANS:
        if start <= cursor <= end:
            return unit
    return "second"


def _step_month(value: datetime.datetime, delta: int) -> datetime.datetime:
    month_index = value.year * 12 + (value.month - 1) + delta
    year, month = divmod(month_index, 12)
    month += 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _step_year(value: datetime.datetime, delta: int) -> datetime.datetime:
    year = value.year + delta
    day = min(value.day, calendar.monthrange(year, value.month)[1])
    return value.replace(year=year, day=day)


def _step_value(value: datetime.datetime, unit: str, delta: int) -> datetime.datetime | None:
    """Step one date-time component by `delta` units, or `None` if that would go out of the accepted range."""

    try:
        if unit == "year":
            stepped = _step_year(value, delta)
        elif unit == "month":
            stepped = _step_month(value, delta)
        else:
            stepped = value + delta * _UNIT_DELTAS[unit]
    except (OverflowError, ValueError):
        return None
    return stepped if 1000 <= stepped.year <= 9999 else None


class TimeRangeInput(Horizontal):
    """Start and end date-time fields, with an option to keep the duration fixed.

    Posts `TimeRangeInput.Changed` once per accepted change (Enter or leaving a field). Invalid input is marked in
    red, explained in a notification, and leaves the current range unchanged. The Up/Down arrow keys step the
    component under the cursor (year, month, ..., second) in the focused field's text only; Enter/blur/Escape
    still apply it, the same as any other edit.
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "defocus", "Defocus", show=False),
        Binding("up", "step_field(1)", "Increment", show=False),
        Binding("down", "step_field(-1)", "Decrement", show=False),
    ]

    DEFAULT_CSS: ClassVar[str] = """
    TimeRangeInput {
        height: 3;
        width: auto;

        Label {
            height: 3;
            content-align: center middle;
            padding: 0 1;
        }
        Input {
            width: 24;
            padding: 0 1;
        }
        Input.-invalid {
            border: tall $error;
        }
        Checkbox {
            height: 3;
        }
    }
    """

    class Changed(Message):
        """Posted when the user changed the time range to a new, valid range."""

        def __init__(self, time_range_input: TimeRangeInput, time_range: TimeRange) -> None:
            super().__init__()
            self.time_range_input = time_range_input
            self.time_range = time_range

        @property
        def control(self) -> TimeRangeInput:
            return self.time_range_input

    def __init__(
        self,
        start: datetime.datetime,
        end: datetime.datetime,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.time_range = TimeRange(start, end)

    def compose(self) -> ComposeResult:
        yield Input(
            format_datetime(self.time_range.start), id="time-range-start", select_on_focus=False, tooltip="Start"
        )
        yield Label("→")
        yield Input(format_datetime(self.time_range.end), id="time-range-end", select_on_focus=False, tooltip="End")
        yield Checkbox("Lock", id="time-range-lock", tooltip="Keep the duration fixed when changing one end.")

    @property
    def locked(self) -> bool:
        return self.query_one("#time-range-lock", Checkbox).value

    def action_defocus(self) -> None:
        """Leave the focused field (Escape), committing it like Tabbing away would."""

        if self.screen.focused in self.query("Input, Checkbox"):
            self.screen.set_focus(None)

    def action_step_field(self, delta: int) -> None:
        """Step the date-time component under the cursor in the focused field (Up/Down), without applying it."""

        field = self.screen.focused
        if not isinstance(field, Input) or field not in self.query(Input):
            return

        try:
            value = parse_datetime(field.value)
        except ValueError:
            return  # don't guess at invalid or incomplete text

        cursor = min(field.cursor_position, _SEGMENT_SPANS[-1][1])
        stepped = _step_value(value, _segment_at(cursor), delta)
        if stepped is None:
            return

        with field.prevent(Input.Changed):
            field.value = format_datetime(stepped)
            field.remove_class("-invalid")
            field.cursor_position = cursor

    @on(Input.Changed)
    def _editing(self, event: Input.Changed) -> None:
        event.stop()
        event.input.remove_class("-invalid")

    @on(Input.Submitted)
    @on(Input.Blurred)
    def _commit(self, event: Input.Submitted | Input.Blurred) -> None:
        event.stop()
        if isinstance(event, Input.Blurred) and event.input.has_class("-invalid"):
            return  # already rejected on Enter and not edited since; don't notify twice
        self.apply_input(event.input)

    def apply_input(self, field: Input) -> None:
        """Validate the text of one field and, when it gives a new valid range, apply it and post `Changed`."""

        try:
            value = parse_datetime(field.value)
        except ValueError as exc:
            self._reject(field, str(exc))
            return

        current = self.time_range
        duration = current.end - current.start
        try:
            if field.id == "time-range-start":
                new = TimeRange(value, value + duration if self.locked else current.end)
            else:
                new = TimeRange(value - duration if self.locked else current.start, value)
        except OverflowError:
            self._reject(field, "Keeping the locked duration would move the other end out of range.")
            return

        if not new.is_valid():
            self._reject(
                field, f"The start ({format_datetime(new.start)}) must be before the end ({format_datetime(new.end)})."
            )
            return

        self._show(new)
        if new != current:
            self.time_range = new
            self.post_message(self.Changed(self, new))

    def set_time_range(self, time_range: TimeRange) -> None:
        """Set the range from code. Does not post `Changed`."""

        self.time_range = time_range
        self._show(time_range)

    def _show(self, time_range: TimeRange) -> None:
        for field_id, value in (("#time-range-start", time_range.start), ("#time-range-end", time_range.end)):
            field = self.query_one(field_id, Input)
            field.remove_class("-invalid")
            text = format_datetime(value)
            if field.value != text:
                with field.prevent(Input.Changed):
                    field.value = text

    def _reject(self, field: Input, message: str) -> None:
        field.add_class("-invalid")
        self.notify(message, title="Time range not changed", severity="warning")
