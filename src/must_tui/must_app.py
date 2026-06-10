import asyncio
import datetime
import importlib.resources
import re
from dataclasses import dataclass
from itertools import cycle
from pathlib import Path
from typing import Any, cast

from textual_timepiece.pickers import DateTimeRangePicker
from whenever import PlainDateTime
from egse.env import bool_env
from textual import log, on, work
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import var
from textual.screen import Screen
from textual.events import MouseScrollDown, MouseScrollUp, MouseDown, MouseUp, MouseMove
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, OptionList, Static
from textual_plotext import PlotextPlot as PlotWidget
from thefuzz import fuzz

from must_tui.dialogs import ErrorDialog, WarningDialog
from must_tui.matplotlib_bridge import MatplotlibPlotter, can_open_matplotlib_window
from must_tui.mib import read_pcf

# from textual_plot import HiResMode, PlotWidget
# from egse.system import title_to_kebab
from must_tui.must import (
    MustContext,
    get_parameter_data,
    get_parameter_metadata,
    get_raw_data_with_timestamp,
    load_parameter_catalog_async,
    login,
    reset_parameter_cache,
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
            if VERBOSE_DEBUG:
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
        self.labels: list[str] = []
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
        self.labels = []
        self.plt.clear_data()
        self.replot()

    def set_xlimits(self, start: datetime.datetime, end: datetime.datetime) -> None:
        self.xlimits = (start, end)
        plotter = cast(Any, self.plt)
        plotter.xlim(start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"))
        self.refresh()

    def set_ylimits(self, ymin: float, ymax: float) -> None:
        self.ylimits = (ymin, ymax)
        plotter = cast(Any, self.plt)
        plotter.ylim(ymin, ymax)
        self.refresh()

    async def update(
        self, par_name: str, timestamps: list[datetime.datetime], values: list[float], time_range: TimeRange
    ) -> None:
        """Update the plot when the input data changes."""

        self.set_title(f"Parameter: {par_name}")

        self.time.append(timestamps)
        self.data.append(values)
        self.labels.append(par_name)

        log.info(f"Updating plot for {par_name} with {len(values)} data points, {max(values)=}.")

        self.replot()

    def replot(self) -> None:
        """Replot the data with the current marker."""
        # self.plt.clear_data()
        plotter = cast(Any, self.plt)
        plotter.clear_figure()  # not sure yet what the difference is with clf() or cld()
        plotter.date_form("Y-m-d H:M:S")
        plotter.title(self.title)
        # self.plt.xlabel("Date-Time")  # takes too much real estate
        # self.plt.ylabel("Value")  # will be put in the same space as the x-label
        for time, data in zip(self.time, self.data):
            plotter.plot([x.strftime("%Y-%m-%d %H:%M:%S") for x in time], data, marker=self.marker)
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


class LoadingScreen(Screen[None]):
    def compose(self) -> ComposeResult:
        with Vertical(id="loading-dialog"):
            yield Static("MUST TUI", id="loading-title")
            yield Static("", id="loading-status")
            yield Static("Please wait…", id="loading-subtitle")

    def set_status(self, message: str) -> None:
        try:
            self.query_one("#loading-status", Static).update(message)
        except Exception:
            pass


class MainScreen(Screen[None]):
    def compose(self) -> ComposeResult:
        app = cast(Any, self.app)
        yield Header()
        with Horizontal(id="input-container"):
            yield Input(placeholder="Search for a match...", id="input-search")
            yield Checkbox(label="Regex", value=True, id="regex-checkbox")
        with Horizontal(id="main-container"):
            yield OptionList(*app.options, markup=False)
            with Vertical():
                with Horizontal(id="info-container"):
                    yield ParameterInfo()
                    yield ParameterMetadata()
                with Horizontal(id="plot-controls"):
                    yield Button("Clear Plot", id="bt-plot-clear")
                    yield Button("Plot: TUI", id="bt-plot-backend")
                    yield DateTimeRangePicker(app.time_range.start, app.time_range.end, id="datetime-range-picker")
                yield app.plot_widget
        yield Footer()


class MUSTApp(App[None]):
    CSS_PATH = "must_app.tcss"
    DATA_PROVIDER = "PLATO"

    BINDINGS = [
        ("ctrl+j", "toggle_jump", "Toggle Jump Mode"),
        ("c", "clear_plot", "Clear Plot"),
        ("p", "toggle_plot_backend", "Toggle plot backend"),
        ("r", "refresh_parameter_cache", "Refresh parameter cache"),
        ("ctrl+r", "reset_parameter_cache", "Reset parameter cache"),
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

    def __init__(self, config_file: Path | None = None) -> None:
        super().__init__()
        self.config_file = config_file
        self.offline_mode = False
        self.must_ctx: MustContext = MustContext()
        self.pars: dict[str, dict] = {}
        self.pars_info: dict = {}
        self.pars_catalog: dict = {}
        self.pars_mapping: dict = {}
        self.options: list[str] = sorted(self.pars_mapping.keys())
        self.jump = False
        self.fuzz = False
        self.markers = cycle(self.MARKERS.keys())
        self.plot_widget: TimeRangePlotter = TimeRangePlotter(self.must_ctx)
        self.matplotlib_plotter = MatplotlibPlotter()
        self.plot_backend = "textual"
        self.time_range = TimeRange(start=PlainDateTime(2025, 12, 2), end=PlainDateTime(2025, 12, 5))

    async def on_mount(self) -> None:
        self.install_screen(LoadingScreen(), name="loading")
        self.push_screen("loading")
        asyncio.create_task(self._initialize_and_show_main_screen())

    def _set_loading_status(self, message: str) -> None:
        if isinstance(self.screen, LoadingScreen):
            self.screen.set_status(message)

    async def _initialize_and_show_main_screen(self) -> None:
        auth_failed = False
        self._set_loading_status("Authenticating with MUST server…")
        self.must_ctx = await login(config_file=self.config_file)
        if self.must_ctx.authenticated:
            log.info("MUST context authenticated successfully.")
        else:
            log.error("MUST context authentication failed.")
            auth_failed = True
            self.offline_mode = True
            self.must_ctx = MustContext()
            self._set_loading_status("Continuing without WebMUST connection…")

        self._set_loading_status("Loading MIB parameter info…")
        try:
            pcf_path = importlib.resources.files("must_tui").joinpath("data/mib/pcf.dat")
            pcf_content = await read_pcf(pcf_path)
            self.pars_info = pcf_content.get("pcf", {})
            log.info(f"Loaded {len(self.pars_info)} MIB entries from pcf.dat for ParameterInfo enrichment.")
        except Exception as exc:
            self.pars_info = {}
            log.warning(f"Could not load optional pcf.dat for ParameterInfo enrichment: {exc}")

        self._set_loading_status("Loading parameter catalog…")
        if self.offline_mode:
            catalog = {}
        else:
            catalog = await load_parameter_catalog_async(data_provider=self.DATA_PROVIDER, ctx=self.must_ctx)
        self._apply_parameter_catalog(catalog)

        self.plot_widget.set_context(self.must_ctx)
        self.matplotlib_plotter.set_context(self.must_ctx)

        self.install_screen(MainScreen(), name="main")
        self.switch_screen("main")
        self._update_plot_backend_button()

        if auth_failed:
            should_abort = await self.push_screen_wait(
                ErrorDialog(
                    title="[b]An error occurred:[/]",
                    error_message="Failed to authenticate with the MUST server.",
                    ok_label="Abort",
                    cancel_label="Ignore",
                    quit_on_ok=True,
                )
            )
            if should_abort:
                return

        if self.must_ctx.authenticated and self.options:
            asyncio.create_task(self.refresh_parameter_catalog(force_refresh=True))

    def _main_screen_active(self) -> bool:
        return isinstance(self.screen, MainScreen)

    def _get_main_controls(self) -> tuple[Input, OptionList] | None:
        """Return main-screen controls once they are mounted and queryable."""
        if not self._main_screen_active():
            return None

        try:
            search_input = self.screen.query_one("#input-search", Input)
            option_list = self.screen.query_one(OptionList)
        except NoMatches:
            return None

        return search_input, option_list

    def _apply_parameter_catalog(self, catalog: dict[str, dict[str, str]]) -> None:
        self.pars_catalog = catalog
        self.pars_mapping = {}

        for mib_name, metadata in sorted(catalog.items()):
            must_description = (metadata.get("description") or "").strip()
            mib_info = self.pars_info.get(mib_name, {}) if isinstance(self.pars_info, dict) else {}
            pcf_description = str(mib_info.get("description", "")).strip()
            pcf_description_2 = str(mib_info.get("description_2", "")).strip()

            # Prefer MUST description unless it is empty or just repeats the name.
            description = must_description
            if not description or description.casefold() == mib_name.casefold():
                description = pcf_description or pcf_description_2
            if not description:
                description = "no description"

            label = f"{mib_name} [{description}]"
            if label in self.pars_mapping and self.pars_mapping[label] != mib_name:
                label = f"{label} ({mib_name})"
            self.pars_mapping[label] = mib_name

        self.options = sorted(self.pars_mapping.keys())
        controls = self._get_main_controls()
        if controls is not None:
            search_input, option_list = controls
            search = search_input.value
            if search == "":
                option_list.set_options(self.options)
            elif self.jump:
                option_list.set_options(self.options)
                self.jump_to_item()
            else:
                self.filter_items()

    def _option_search_fields(self, option_label: str) -> tuple[str, str, str]:
        """Return label, MIB name, and PCF description_2 for searching."""

        mib_name = self.pars_mapping.get(option_label, "")
        mib_info = self.pars_info.get(mib_name, {}) if isinstance(self.pars_info, dict) else {}
        description_2 = str(mib_info.get("description_2", ""))
        return option_label, mib_name, description_2

    async def refresh_parameter_catalog(self, force_refresh: bool = False) -> None:
        if not self.must_ctx.authenticated:
            return

        catalog = await load_parameter_catalog_async(
            data_provider=self.DATA_PROVIDER,
            ctx=self.must_ctx,
            force_refresh=force_refresh,
        )
        if catalog:
            self._apply_parameter_catalog(catalog)
            log.info(f"Loaded {len(catalog)} cached MUST parameters for provider {self.DATA_PROVIDER}.")

    def _matplotlib_backend_available(self) -> bool:
        return can_open_matplotlib_window()

    def _update_plot_backend_button(self) -> None:
        if not self.is_mounted or not self._main_screen_active():
            return

        try:
            button = self.screen.query_one("#bt-plot-backend", Button)
        except NoMatches:
            return
        button.label = "Plot: Matplotlib" if self.plot_backend == "matplotlib" else "Plot: TUI"

    def _enable_matplotlib_backend(self) -> None:
        if not self._matplotlib_backend_available():
            self.call_later(
                self.show_warning_dialog,
                "Matplotlib plotting requires a graphical session with a window manager.",
            )
            return

        self.plot_backend = "matplotlib"
        self.matplotlib_plotter.start()
        self.matplotlib_plotter.sync_from_plotter(self.plot_widget)
        self._update_plot_backend_button()

    def _disable_matplotlib_backend(self) -> None:
        self.plot_backend = "textual"
        self.matplotlib_plotter.close()
        self._update_plot_backend_button()

    @work()
    async def show_error_dialog(self, error_message: str) -> None:
        if await self.app.push_screen_wait(
            ErrorDialog(
                title="[b]An error occurred:[/]", error_message=error_message, ok_label="Abort", cancel_label="Ignore"
            )
        ):
            self.app.exit()

    @work()
    async def show_warning_dialog(self, warning_message: str) -> None:
        await self.app.push_screen_wait(
            WarningDialog(title="[b]A warning occurred:[/]", warning_message=warning_message, ok_label="OK")
        )

    @on(DateTimeRangePicker.Changed, "#datetime-range-picker")
    async def on_datetime_range_changed(self, event: DateTimeRangePicker.Changed) -> None:
        assert event.start is not None and event.end is not None
        log.info(f"DateTimeRangePicker changed: {event.start=} {event.end=}")
        # Convert to string format 'YYYY-MM-DD HH:MM:SS'
        self.call_later(self.plot_widget.set_xlimits, event.start.py_datetime(), event.end.py_datetime())
        if self.plot_backend == "matplotlib":
            self.call_later(self.matplotlib_plotter.set_xlimits, event.start.py_datetime(), event.end.py_datetime())

    @on(Button.Pressed, "#bt-plot-clear")
    def clear_plot(self, event) -> None:
        self.call_after_refresh(self.plot_widget.clear_plot)
        self.call_after_refresh(self.matplotlib_plotter.clear_plot)
        event.stop()

    @on(Button.Pressed, "#bt-plot-backend")
    def toggle_plot_backend(self, event) -> None:
        if not self._main_screen_active():
            return

        if self.plot_backend == "matplotlib":
            self._disable_matplotlib_backend()
        else:
            self._enable_matplotlib_backend()

        event.stop()

    @on(ParameterSelected)
    async def on_par_selected(self, message: ParameterSelected) -> None:
        data_provider = self.DATA_PROVIDER
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

            if not timestamps or not values:
                log.warning(f"No data available for parameter {par_name} in the specified time range.")
                self.call_later(
                    self.show_warning_dialog, f"No data available for parameter {par_name} in the specified time range."
                )
                continue

            self.plot_widget.set_xlimits(self.time_range.start.py_datetime(), self.time_range.end.py_datetime())
            self.plot_widget.set_ylimits(min(values) - 1.0, max(values) + 1.0)
            await self.plot_widget.update(par_name, timestamps, values, self.time_range)

            if self.plot_backend == "matplotlib":
                self.matplotlib_plotter.set_xlimits(
                    self.time_range.start.py_datetime(), self.time_range.end.py_datetime()
                )
                self.matplotlib_plotter.set_ylimits(min(values) - 1.0, max(values) + 1.0)
                await self.matplotlib_plotter.update(par_name, timestamps, values, self.time_range)

        # self.plot_widget.replot()

    def action_toggle_jump(self) -> None:
        controls = self._get_main_controls()
        if controls is None:
            return

        self.jump = not self.jump
        mode = "Jump" if self.jump else "Filter"
        controls[0].placeholder = f"Search Mode: {mode}"

    def action_refresh_parameter_cache(self) -> None:
        asyncio.create_task(self.refresh_parameter_catalog(force_refresh=True))

    def action_reset_parameter_cache(self) -> None:
        reset_parameter_cache(self.DATA_PROVIDER)
        asyncio.create_task(self.refresh_parameter_catalog(force_refresh=True))

    @on(Checkbox.Changed, "#regex-checkbox")
    def toggle_regex(self, event: Checkbox.Changed) -> None:
        if not self._main_screen_active():
            return

        log.debug(f"Regex checkbox changed: {event.value=}")
        self.fuzz = not event.value
        self.filter_items()

    @on(Input.Changed)
    def filter(self, event: Input.Changed) -> None:
        if not self._main_screen_active():
            return

        if self.jump:
            self.jump_to_item()
        else:
            self.filter_items()

    @on(OptionList.OptionSelected)
    async def show_parameter_info(self, event: OptionList.OptionSelected) -> None:
        if not self._main_screen_active():
            return

        log.debug(f"{event.option=}")
        par_name = event.option.prompt
        mib_name = self.pars_mapping.get(par_name)
        log.debug(f"{par_name=}, {mib_name=}")
        try:
            parameter_info = self.screen.query_one(ParameterInfo)
        except NoMatches:
            return

        if mib_name and mib_name in self.pars_info:
            await parameter_info.update_info(mib_name, self.pars_info[mib_name])
        else:
            fallback_name = mib_name if isinstance(mib_name, str) and mib_name else str(par_name)
            await parameter_info.update_info(fallback_name, {})

    @on(OptionList.OptionSelected)
    async def show_parameter_metadata(self, event: OptionList.OptionSelected) -> None:
        if not self._main_screen_active():
            return

        log.debug(f"{event.option=}")
        par_name = event.option.prompt
        mib_name = self.pars_mapping.get(par_name)
        log.debug(f"{par_name=}, {mib_name=}")
        if mib_name:
            metadata = await get_parameter_metadata(self.must_ctx, mib_name)
            try:
                parameter_metadata = self.screen.query_one(ParameterMetadata)
            except NoMatches:
                return
            await parameter_metadata.update_metadata(mib_name, metadata[0] if metadata else {})
            self.post_message(ParameterSelected(mib_name))

    def jump_to_item(self) -> None:
        controls = self._get_main_controls()
        if controls is None:
            return

        search_input, option_list = controls
        search = search_input.value
        if search == "":
            return

        scores: list[tuple[str, int]] = []
        for opt in self.options:
            label, mib_name, description_2 = self._option_search_fields(opt)
            score = max(
                fuzz.partial_ratio(search, label),
                fuzz.partial_ratio(search, mib_name),
                fuzz.partial_ratio(search, description_2),
            )
            scores.append((opt, score))

        if not scores:
            return

        best_match, _ = max(scores, key=lambda item: item[1])
        idx = self.options.index(best_match)
        option_list.highlighted = idx

    def filter_items(self) -> None:
        controls = self._get_main_controls()
        if controls is None:
            return

        search_input, option_list = controls
        search = search_input.value
        option_list.clear_options()
        if search == "":
            option_list.set_options(self.options)
        else:
            if self.fuzz:
                scored_options: list[tuple[str, int]] = []
                for opt in self.options:
                    label, mib_name, description_2 = self._option_search_fields(opt)
                    score = max(
                        fuzz.partial_ratio(search, label),
                        fuzz.partial_ratio(search, mib_name),
                        fuzz.partial_ratio(search, description_2),
                    )
                    scored_options.append((opt, score))

                scored_options.sort(key=lambda item: item[1], reverse=True)
                matched_options = [opt for opt, score in scored_options[:100] if score > 50]
            else:
                log.debug(f"Filtering with regex: {search=}")
                try:
                    pattern = re.compile(search, re.IGNORECASE)
                except Exception as exc:
                    log.error(f"Invalid regex pattern: {exc}")
                    return
                matched_options = [
                    opt
                    for opt in self.options
                    if any(pattern.search(field) for field in self._option_search_fields(opt))
                ]
            option_list.set_options(matched_options)

    def watch_marker(self) -> None:
        """React to the marker type being changed."""
        self.sub_title = self.MARKERS[self.marker]
        # The marker reactive watcher can run before the main screen is active.
        # Update the persistent widget instance directly instead of querying the DOM.
        self.plot_widget.marker = self.marker
        self.matplotlib_plotter.marker = self.marker
        if self.plot_backend == "matplotlib" and self._main_screen_active():
            self.matplotlib_plotter.replot()

    def action_marker(self) -> None:
        """Cycle to the next marker type."""
        self.marker = next(self.markers)

    def action_clear_plot(self) -> None:
        if not self._main_screen_active():
            return

        self.plot_widget.clear_plot()
        if self.plot_backend == "matplotlib":
            self.matplotlib_plotter.clear_plot()

    def action_toggle_plot_backend(self) -> None:
        if not self._main_screen_active():
            return

        if self.plot_backend == "matplotlib":
            self._disable_matplotlib_backend()
        else:
            self._enable_matplotlib_backend()

    def on_shutdown(self) -> None:
        self.matplotlib_plotter.close()


if __name__ == "__main__":
    app = MUSTApp()

    app.log.info("2Starting MUST TUI application...")
    app.run()
    app.log.info("2MUST TUI application has stopped.")
