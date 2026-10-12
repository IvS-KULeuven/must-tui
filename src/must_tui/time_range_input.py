"""A minimal start/end date-time input for MUST TUI.

Two plain text fields and a "Lock" checkbox. A field is only applied when the user presses Enter or leaves it, and
only when the resulting range is valid; then one `TimeRangeInput.Changed` message is posted. Typing does not send
anything, so the app never sees half-edited or start-after-end ranges.
"""

from __future__ import annotations

import datetime
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
    and `now` (the current local time, to the second).

    Raises:
        ValueError: with a message that can be shown to the user.
    """

    text = " ".join(text.strip().replace("T", " ").split())
    if text.casefold() == "now":
        return (now or datetime.datetime.now()).replace(microsecond=0)

    for fmt in INPUT_FORMATS:
        try:
            value = datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
        # strftime() does not zero-pad years below 1000, so such dates can't be shown, plotted or sent to the server.
        if value.year >= 1000:
            return value

    raise ValueError(f"'{text}' is not a valid date-time; use YYYY-MM-DD [HH:MM[:SS]] or 'now'.")


class TimeRangeInput(Horizontal):
    """Start and end date-time fields, with an option to keep the duration fixed.

    Posts `TimeRangeInput.Changed` once per accepted change (Enter or leaving a field). Invalid input is marked in
    red, explained in a notification, and leaves the current range unchanged.
    """

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "defocus", "Defocus", show=False)]

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
