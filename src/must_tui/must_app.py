import asyncio
import datetime
from functools import partial
import importlib.resources
import re
from dataclasses import dataclass
from itertools import cycle

from textual_timepiece.pickers import DateTimeRangePicker
from whenever import PlainDateTime, SystemDateTime, TimeDelta
from egse.env import bool_env
from textual import log, on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import var
from textual.events import MouseScrollDown, MouseScrollUp, MouseDown, MouseUp, MouseMove
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, OptionList, Static
from textual_plotext import PlotextPlot as PlotWidget
from thefuzz import process

from must_tui.dialogs import ErrorDialog
from must_tui.mib import read_pcf

# from textual_plot import HiResMode, PlotWidget
# from egse.system import title_to_kebab
from must_tui.must import (
    MustContext,
    get_all_data_providers,
    get_parameter_data,
    get_parameter_metadata,
    get_raw_data_with_timestamp,
    login,
    title_to_kebab,
)

PARAMETER_INFO_FIELDS = """
    description description_2 pid unit decim ptc pfc width valid related categ natur
    curtx inter uscon parval subsys valpar sptype corr obtid darc endian
""".split()
"""The names of the fields in the pcf.dat file of the MIB. Used to display parameter info."""

PARAMETER_METADATA_FIELDS = """
    description data-type first-sample last-sample subsystem id unit parameter-type name provider
""".split()
"""The names of the fields in the parameter metadata obtained from the MUST server."""


VERBOSE_DEBUG = bool_env("VERBOSE_DEBUG", False)


class ParameterSelected(Message):
    """Message sent when a parameter is selected from the option list."""

    def __init__(self, parameter_name: str) -> None:
        super().__init__()
        self.parameter_name = parameter_name


@dataclass
class TimeRange:
    start: PlainDateTime
    end: PlainDateTime


class ParameterMetadata(Static):
    """Widget to display metadata about a selected parameter.

    The metadata information is obtained from the MUST server and consists of:
    - Description: parameter mnemonic
    - Data Type: one of UNSIGNED_SMALL_INT, ...
    - First Sample: 'YYYY-MM-DD HH:MM:SS'
    - Last Sample: 'YYYY-MM-DD HH:MM:SS
    - Subsystem: one of TM, ...
    - Id:
    - Unit:
    - Parameter Type:
    - Name: mib name
    - Provider: name of the data provider

    """

    def __init__(self) -> None:
        super().__init__()
        self.par_name = ""
        self.metadata: dict = {}
        self.table: DataTable = DataTable()

    async def update_metadata(self, par_name: str, metadata: dict) -> None:
        self.par_name = par_name
        self.metadata = metadata
        log.debug(f"ParameterMetadata={metadata}")
        for idx, (key, value) in enumerate(self.metadata.items()):
            self.table.update_cell(key, "value", str(value), update_width=True)

        self.table.refresh()

    def compose(self) -> ComposeResult:
        yield self.table

    def on_mount(self) -> None:
        self.table = self.query_one(DataTable)
        self.table.add_columns(("Field", "field"), ("Value", "value"))
        self.table.zebra_stripes = True
        self.table.cursor_type = "row"
        # self.table.fixed_rows = 1
        for field in PARAMETER_METADATA_FIELDS:
            value = self.metadata.get(field, "N/A")
            log.debug(f"Adding row: {field}={value}")
            self.table.add_row(field, str(value), key=field)


class ParameterInfo(Static):
    """Widget to display information about a selected parameter.

    The information is obtained from the PCF file of the MIB and consists of:
    - description: parameter mnemonic
    - description_2: extended description
    - pid: On-board ID of the telemetry parameter
    - unit: Engineering unit mnemonic
    - ptc: Parameter Type Code
    - pfc: Parameter Format Code
    - width: Bit width of the parameter
    - valid: Validity flag
    - related: Related parameters
    - categ: Category of the parameter
    - natur: Nature of the parameter
    - curtx: Current telemetry index
    - inter: Interpretation
    - uscon: User context
    - decim: Decimation factor
    - parval: Parameter value
    - subsys: Subsystem
    - valpar: Validity parameter
    - sptype: Special type
    - corr: Correlation
    - obtid: On-board telemetry identifier
    - darc: Data archive
    - endian: Endianness
    """

    def __init__(self) -> None:
        super().__init__()
        self.par_name = ""
        self.par_info = {}
        self.table: DataTable = DataTable()

    async def update_info(self, par_name: str, par_info: dict) -> None:
        self.par_name = par_name
        self.par_info = par_info
        log.debug(f"ParameterInfo={par_info}")
        self.table.update_cell("par_name", "value", par_name, update_width=True)
        for idx, field in enumerate(PARAMETER_INFO_FIELDS):
            value = self.par_info.get(field, "N/A")
            if VERBOSE_DEBUG:
                log.debug(f"Updating row {idx}: {field}={value}")
            self.table.update_cell(field, "value", str(value), update_width=True)

        self.table.refresh()

    def compose(self) -> ComposeResult:
        yield self.table

    def on_mount(self) -> None:
        self.table = self.query_one(DataTable)
        self.table.add_columns(("Field", "field"), ("Value", "value"))
        self.table.zebra_stripes = True
        self.table.cursor_type = "row"
        # self.table.fixed_rows = 1
        self.table.add_row("par_name", self.par_name, key="par_name")
        for field in PARAMETER_INFO_FIELDS:
            value = self.par_info.get(field, "N/A")
            if VERBOSE_DEBUG:
                log.debug(f"Adding row: {field}={value}")
            self.table.add_row(field, str(value), key=field)


class TimeRangePlotter(PlotWidget, can_focus=True):
    """Widget to plot parameter data over a specified time range."""

    marker: var[str] = var("dot")
    """The type of marker to use for the plot."""

    def __init__(self, must_ctx: MustContext) -> None:
        super().__init__()
        self.must_ctx = must_ctx
        self.title: str = "Parameter Data Plot"
        self.data: list[list[float]] = []
        self.time: list[list[datetime.datetime]] = []
        self.xlimits: tuple[datetime.datetime, datetime.datetime] | None = None
        self.ylimits: tuple[float, float] | None = None

    def set_context(self, must_ctx: MustContext) -> None:
        self.must_ctx = must_ctx
        self.refresh()

    def set_title(self, title: str) -> None:
        self.title = title
        self.refresh()

    def clear_plot(self) -> None:
        self.data = []
        self.time = []
        self.plt.clear_data()
        self.replot()

    def set_xlimits(self, start: datetime.datetime, end: datetime.datetime) -> None:
        self.xlimits = (start, end)
        self.plt.xlim(start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"))
        self.refresh()

    def set_ylimits(self, ymin: float, ymax: float) -> None:
        self.ylimits = (ymin, ymax)
        self.plt.ylim(ymin, ymax)
        self.refresh()

    async def update(
        self, par_name: str, timestamps: list[datetime.datetime], values: list[float], time_range: TimeRange
    ) -> None:
        """Update the plot when the input data changes."""

        self.set_title(f"Parameter: {par_name}")

        self.time.append(timestamps)
        self.data.append(values)

        log.info(f"Updating plot for {par_name} with {len(values)} data points, {max(values)=}.")

        self.replot()

    def replot(self) -> None:
        """Replot the data with the current marker."""
        # self.plt.clear_data()
        self.plt.clear_figure()  # not sure yet what the difference is with clf() or cld()
        self.plt.date_form("Y-m-d H:M:S")
        self.plt.title(self.title)
        # self.plt.xlabel("Date-Time")  # takes too much real estate
        # self.plt.ylabel("Value")  # will be put in the same space as the x-label
        for time, data in zip(self.time, self.data):
            self.plt.plot([x.strftime("%Y-%m-%d %H:%M:%S") for x in time], data, marker=self.marker)
        self.refresh()

    async def _watch_marker(self) -> None:
        """React to the marker being changed."""
        self.replot()

    @on(MouseScrollDown)
    def zoom_out(self, event: MouseScrollDown) -> None:
        event.stop()
        log.debug(f"MouseScrollDown: {event=}")

    @on(MouseScrollUp)
    def zoom_in(self, event: MouseScrollUp) -> None:
        event.stop()
        log.debug(f"MouseScrollUp: {event=}")

    @on(MouseDown)
    def start_drag(self, event: MouseDown) -> None:
        event.stop()
        log.debug(f"MouseDown: {event=}")

    @on(MouseUp)
    def end_drag(self, event: MouseUp) -> None:
        event.stop()
        log.debug(f"MouseUp: {event=}")

    @on(MouseMove)
    def drag_with_mouse(self, event: MouseMove) -> None:
        event.stop()
        log.debug(f"MouseMove: {event=}")
        if not event.button == 1:
            return
        log.debug(f"Dragging with mouse: {event=}")


class MUSTApp(App[None]):
    CSS_PATH = "must_app.tcss"

    BINDINGS = [
        ("ctrl+j", "toggle_jump", "Toggle Jump Mode"),
        ("d", "app.toggle_dark", "Toggle light/dark mode"),
        ("m", "marker", "Cycle markers"),
        ("ctrl+q", "app.quit", "Quit"),
    ]

    MARKERS = {
        "braille": "Braille",
        "sd": "Standard Definition",
        "hd": "High Definition",
        "dot": "Dot",  # default, put last cause cycle() starts from first item in the dict
    }

    marker: var[str] = var("dot")
    """The marker used for each of the plots."""

    def __init__(self) -> None:
        super().__init__()
        self.must_ctx: MustContext = MustContext()
        self.pars: dict[str, dict] = {}
        self.pars_info: dict = {}
        self.pars_mapping: dict = {}
        self.options: list[str] = sorted(self.pars_mapping.keys())
        self.jump = False
        self.fuzz = False
        self.markers = cycle(self.MARKERS.keys())
        self.plot_widget: TimeRangePlotter = TimeRangePlotter(self.must_ctx)
        self.time_range = TimeRange(start=PlainDateTime(2025, 12, 2), end=PlainDateTime(2025, 12, 5))

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="input-container"):
            yield Input(placeholder="Search for a match...", id="input-search")
            yield Checkbox(label="Regex", value=True, id="regex-checkbox")
        with Horizontal(id="main-container"):
            yield OptionList(*self.options)
            with Vertical():
                with Horizontal(id="info-container"):
                    yield ParameterInfo()
                    yield ParameterMetadata()
                with Horizontal(id="plot-controls"):
                    yield Button("Clear Plot", id="bt-plot-clear")
                    yield DateTimeRangePicker(self.time_range.start, self.time_range.end, id="datetime-range-picker")
                yield self.plot_widget
        yield Footer()

    async def on_mount(self) -> None:
        pcf_path = importlib.resources.files("must_tui").joinpath("data/mib/pcf.dat")
        pcf_content = await read_pcf(pcf_path)

        self.must_ctx = await login()
        if self.must_ctx.authenticated:
            log.info("MUST context authenticated successfully.")
        else:
            log.error("MUST context authentication failed.")
            self.call_later(self.show_error_dialog, "Failed to authenticate with the MUST server.")

        _ = await get_all_data_providers(self.must_ctx)

        self.pars_info = pcf_content["pcf"]
        self.pars_mapping = pcf_content["mapping"]
        self.options = sorted(self.pars_mapping.keys())
        self.query_one(OptionList).set_options(self.options)
        self.query_one(TimeRangePlotter).set_context(self.must_ctx)

    @work()
    async def show_error_dialog(self, error_message: str) -> None:
        if await self.app.push_screen_wait(
            ErrorDialog(
                title="[b]An error occurred:[/]", error_message=error_message, ok_label="Abort", cancel_label="Ignore"
            )
        ):
            self.app.exit()

    @on(DateTimeRangePicker.Changed, "#datetime-range-picker")
    async def on_datetime_range_changed(self, event: DateTimeRangePicker.Changed) -> None:
        assert event.start is not None and event.end is not None
        log.info(f"DateTimeRangePicker changed: {event.start=} {event.end=}")
        # Convert to string format 'YYYY-MM-DD HH:MM:SS'
        self.call_later(self.plot_widget.set_xlimits, event.start.py_datetime(), event.end.py_datetime())

    @on(Button.Pressed, "#bt-plot-clear")
    def clear_plot(self, event) -> None:
        self.call_after_refresh(self.plot_widget.clear_plot)
        event.stop()

    @on(ParameterSelected)
    async def on_par_selected(self, message: ParameterSelected) -> None:
        data_provider = "PLATO"
        par_name = message.parameter_name

        async for data in get_parameter_data(
            self.must_ctx,
            data_provider,
            par_name,
            self.time_range.start.format_common_iso().replace("T", " "),
            self.time_range.end.format_common_iso().replace("T", " "),
            paginated=False,
        ):
            timestamps, values = get_raw_data_with_timestamp(data)
            log.info(
                f"Updating data for parameter {par_name} from {self.time_range.start} to {self.time_range.end}, data length: {len(timestamps)}"
            )

            self.plot_widget.set_xlimits(self.time_range.start.py_datetime(), self.time_range.end.py_datetime())
            self.plot_widget.set_ylimits(min(values) - 1.0, max(values) + 1.0)
            await self.plot_widget.update(par_name, timestamps, values, self.time_range)

        # self.plot_widget.replot()

    def action_toggle_jump(self) -> None:
        self.jump = not self.jump
        mode = "Jump" if self.jump else "Filter"
        self.query_one(Input).placeholder = f"Search Mode: {mode}"

    @on(Checkbox.Changed, "#regex-checkbox")
    def toggle_regex(self, event: Checkbox.Changed) -> None:
        log.debug(f"Regex checkbox changed: {event.value=}")
        self.fuzz = not event.value
        self.filter_items()

    @on(Input.Changed)
    def filter(self, event: Input.Changed) -> None:
        if self.jump:
            self.jump_to_item()
        else:
            self.filter_items()

    @on(OptionList.OptionSelected)
    async def show_parameter_info(self, event: OptionList.OptionSelected) -> None:
        log.debug(f"{event.option=}")
        par_name = event.option.prompt
        mib_name = self.pars_mapping.get(par_name)
        log.debug(f"{par_name=}, {mib_name=}")
        if mib_name:
            await self.query_one(ParameterInfo).update_info(mib_name, self.pars_info[mib_name])

    @on(OptionList.OptionSelected)
    async def show_parameter_metadata(self, event: OptionList.OptionSelected) -> None:
        log.debug(f"{event.option=}")
        par_name = event.option.prompt
        mib_name = self.pars_mapping.get(par_name)
        log.debug(f"{par_name=}, {mib_name=}")
        if mib_name:
            metadata = await get_parameter_metadata(self.must_ctx, mib_name)
            await self.query_one(ParameterMetadata).update_metadata(mib_name, metadata[0] if metadata else {})
            self.post_message(ParameterSelected(mib_name))

    def jump_to_item(self) -> None:
        search = self.query_one(Input).value
        result = process.extractOne(search, self.options)
        if result:
            best_match = result[0]
            idx = self.options.index(best_match)
            self.query_one(OptionList).highlighted = idx

    def filter_items(self) -> None:
        search = self.query_one(Input).value
        option_list = self.query_one(OptionList)
        option_list.clear_options()
        if search == "":
            option_list.set_options(self.options)
        else:
            if self.fuzz:
                matches = process.extract(search, self.options, limit=100)
                matched_options = [match[0] for match in matches if match[1] > 50]
            else:
                log.debug(f"Filtering with regex: {search=}")
                try:
                    pattern = re.compile(search, re.IGNORECASE)
                except Exception as exc:
                    log.error(f"Invalid regex pattern: {exc}")
                    return
                matched_options = [opt for opt in self.options if pattern.search(opt)]
            option_list.set_options(matched_options)

    def watch_marker(self) -> None:
        """React to the marker type being changed."""
        self.sub_title = self.MARKERS[self.marker]
        self.query_one(TimeRangePlotter).marker = self.marker

    def action_marker(self) -> None:
        """Cycle to the next marker type."""
        self.marker = next(self.markers)


if __name__ == "__main__":
    app = MUSTApp()
    app.run()
