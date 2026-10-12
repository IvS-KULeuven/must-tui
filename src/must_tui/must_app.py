import asyncio
import datetime
import importlib.resources
import re
from itertools import cycle
from pathlib import Path
from typing import Any, cast

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
from textual.widgets.option_list import Option
from textual_plotext import PlotextPlot as PlotWidget
# rapidfuzz is not a direct dependency: it is the scoring engine that `thefuzz` (a dependency) is built on.
from rapidfuzz import fuzz as rf_fuzz, process
# plotext is not a direct dependency: it is the plotting engine textual-plotext (a dependency) wraps. Used
# directly for colorize(): textual_plotext.Plot.colorize() forwards to it with the wrong keyword and always
# raises TypeError.
import plotext

from must_tui.dialogs import ErrorDialog, WarningDialog
from must_tui.matplotlib_bridge import MatplotlibPlotter, can_open_matplotlib_window
from must_tui.mib import read_pcf
from must_tui.parameter_cache import get_parameter_cache_updated_at
from must_tui.time_range_input import TimeRange, TimeRangeInput, format_datetime, parse_datetime

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

PLOTTED_SAMPLES_FIELD = "plotted-samples"
"""Row key for the (locally computed, not server-provided) sample count shown in `ParameterMetadata`."""


VERBOSE_DEBUG = bool_env("VERBOSE_DEBUG", False)

MAX_PLOT_POINTS = 1000
"""Maximum number of points per series drawn in the terminal plot. Rendering cost grows with the number of points."""

MAX_VISIBLE_OPTIONS = 500
"""Maximum number of parameters shown in the option list. Refilling the list with all ~70k parameters takes seconds."""

SHOW_ALL_MATCHES_ID = "__show_all_matches__"
"""Option id of the selectable hint shown when matches are truncated; selecting it loads the full match list."""

PARAMETER_CACHE_MAX_AGE = datetime.timedelta(days=1)
"""The parameter catalog is re-downloaded at startup only when the cache is older than this. Press 'r' to force it."""

SEARCH_DEBOUNCE = 0.15
"""Seconds to wait after the last keystroke before searching, so typing a word searches once instead of per key."""

MAX_FUZZY_MATCHES = 100
FUZZY_MIN_SCORE = 50

SEARCH_CHUNK_SIZE = 4096
"""Number of options scored per rapidfuzz call; each call blocks the UI thread until it returns (a few ms)."""

SearchFields = tuple[str, str, str, str]
"""The option label, MIB name, the label's bracketed description, and PCF description_2 of one parameter.

The bracketed description is also a substring of the label (`"{mib_name} [{description}]"`), but as its own,
separately-anchorable field: an anchored regex like `^NC01_FRAME` can otherwise never match it, since `^` on
the label only ever matches at the mib_name's position (position 0), not at the bracket's.
"""


def match_options(search: str, fields: list[SearchFields], mode: str) -> list[int]:
    """Return the indices into `fields` that match `search`.

    Modes:

    - "regex": all options where the case-insensitive regex matches one of the fields, in list order.
      Raises re.error for an invalid pattern.
    - "fuzzy": at most MAX_FUZZY_MATCHES options scoring above FUZZY_MIN_SCORE, best first.
    - "jump": the single best-scoring option (the first one when scores tie), or nothing for an empty list.

    The fuzzy score of an option is the highest `partial_ratio` over its fields, rounded like `thefuzz` does.
    This is a pure function, so it can run on a worker thread.
    """

    if mode == "regex":
        pattern = re.compile(search, re.IGNORECASE).search
        return [
            idx
            for idx, (label, name, description, description_2) in enumerate(fields)
            if pattern(label) or pattern(name) or pattern(description) or pattern(description_2)
        ]

    # rapidfuzz scores many strings in one C call; `thefuzz.fuzz.partial_ratio` is the same scorer, one pair at a time.
    # A C call holds the GIL until it returns, so the options are scored in chunks to let the UI thread run in between.
    # Options that score too low to be selected are skipped: below FUZZY_MIN_SCORE in fuzzy mode, and below the best
    # score so far in jump mode, where the first option with the best score wins.
    best: dict[int, int] = {}
    jump_idx, jump_score = None, -1
    for offset in range(0, len(fields), SEARCH_CHUNK_SIZE):
        cutoff = FUZZY_MIN_SCORE if mode == "fuzzy" else max(jump_score - 0.5, 0)
        chunk_best: dict[int, int] = {}
        for column in zip(*fields[offset : offset + SEARCH_CHUNK_SIZE]):
            for _, score, idx in process.extract(
                search, column, scorer=rf_fuzz.partial_ratio, processor=None, limit=None, score_cutoff=cutoff
            ):
                chunk_best[offset + idx] = max(chunk_best.get(offset + idx, 0), int(round(score)))
        if mode == "jump":
            for idx in sorted(chunk_best):
                if chunk_best[idx] > jump_score:
                    jump_idx, jump_score = idx, chunk_best[idx]
        else:
            best.update(chunk_best)

    if mode == "jump":
        return [] if jump_idx is None else [jump_idx]

    scored = sorted((idx for idx, score in best.items() if score > FUZZY_MIN_SCORE), key=lambda idx: (-best[idx], idx))
    return scored[:MAX_FUZZY_MATCHES]


class ParameterSelected(Message):
    """Message sent when a parameter is selected from the option list."""

    def __init__(self, parameter_name: str) -> None:
        super().__init__()
        self.parameter_name = parameter_name


def parameter_data_range(metadata: dict) -> TimeRange:
    """The `TimeRange` spanning a parameter's `first-sample`/`last-sample`, as reported by MUST's metadata.

    Raises:
        ValueError: with a message that can be shown to the user, when the metadata has no (valid) sample range.
    """

    first_sample, last_sample = metadata.get("first-sample"), metadata.get("last-sample")
    if not isinstance(first_sample, str) or not isinstance(last_sample, str):
        raise ValueError("Select a parameter with data on MUST first.")

    new_range = TimeRange(parse_datetime(first_sample), parse_datetime(last_sample))
    if not new_range.is_valid():
        raise ValueError(
            f"The parameter's first sample ({first_sample}) is not before its last sample ({last_sample})."
        )
    return new_range


def downsample_min_max(
    timestamps: list[datetime.datetime], values: list[float], max_points: int = MAX_PLOT_POINTS
) -> tuple[list[datetime.datetime], list[float]]:
    """Reduce a series to at most `max_points` points, keeping the minimum and maximum of each bucket.

    Unlike taking every n-th sample, this keeps spikes and dips visible in the plot.
    """

    count = len(values)
    if count <= max_points:
        return timestamps, values

    buckets = max_points // 2
    out_time: list[datetime.datetime] = []
    out_values: list[float] = []
    for bucket in range(buckets):
        lo = bucket * count // buckets
        hi = (bucket + 1) * count // buckets
        if lo >= hi:
            continue
        i_min = min(range(lo, hi), key=values.__getitem__)
        i_max = max(range(lo, hi), key=values.__getitem__)
        for idx in sorted({i_min, i_max}):
            out_time.append(timestamps[idx])
            out_values.append(values[idx])

    return out_time, out_values


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

    Also shows `plotted-samples`, the number of data points fetched for the parameter's current plot. This is
    computed locally (not provided by MUST) and is reset to "–" whenever a new parameter is selected.
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
        self.set_sample_count(None)

        self.table.refresh()

    def set_sample_count(self, count: int | None) -> None:
        """Show how many samples were fetched for the parameter's current plot, or "–" while that's unknown."""

        self.table.update_cell(PLOTTED_SAMPLES_FIELD, "value", "–" if count is None else f"{count:,}")

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
        self.table.add_row(PLOTTED_SAMPLES_FIELD, "–", key=PLOTTED_SAMPLES_FIELD)


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


PLOT_DATE_FORM = "Y-m-d H:M:S"
"""plotext date format matching `format_datetime()`, for `Plot.date_form()` / `Plot.xlim()`."""

PLOT_COLORS = (
    "blue+", "green+", "red+", "cyan+", "magenta+", "orange+", "gray+", "blue", "green", "red", "cyan", "magenta",
    "orange", "gray",
)
"""Colors cycled across plotted series in the TUI plot; the first five match plotext's own default order.
Deliberately excludes black/white, which can blend into the background. Names must be ones `plotext.colorize()`
accepts (see `plotext._dict.color_codes`); it has no `yellow` or `gold`, unlike plotext's own default sequence.
"""


def series_color(index: int) -> str:
    """The TUI plot color for the series at `index` (its position among all plotted series, duplicates included)."""

    return PLOT_COLORS[index % len(PLOT_COLORS)]


def colored_plot_title(labels: list[str]) -> str:
    """Like `plot_title()`, but each parameter name is colored (ANSI) to match its plotted series.

    For a repeated label, the color of its first occurrence is used, matching the color `replot()` gives that
    series (by `series_color()` on the same index). TUI only: the Matplotlib backend has a real legend and
    keeps the plain `plot_title()`.
    """

    first_index: dict[str, int] = {}
    for index, label in enumerate(labels):
        first_index.setdefault(label, index)

    if not first_index:
        return "Parameter Data Plot"

    colored = [plotext.colorize(label, color=series_color(index)) for label, index in first_index.items()]
    if len(colored) == 1:
        return f"Parameter: {colored[0]}"
    return "Parameters: " + ", ".join(colored)


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
        self.display_series: list[tuple[list[str], list[float]]] = []
        """Downsampled copies of the series, with formatted timestamps, as drawn by plotext."""
        self.xlimits: tuple[datetime.datetime, datetime.datetime] | None = None
        self.ylimits: tuple[float, float] | None = None
        # plotext defaults to "%d/%m/%Y" until date_form() is called; set it now so the first-ever
        # set_xlimits() (before any replot() has run) doesn't crash on our "YYYY-MM-DD HH:MM:SS" strings.
        self.plt.date_form(PLOT_DATE_FORM)

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
        self.display_series = []
        self.title = colored_plot_title(self.labels)
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

        self.time.append(timestamps)
        self.data.append(values)
        self.labels.append(par_name)
        self.title = colored_plot_title(self.labels)

        display_time, display_values = downsample_min_max(timestamps, values)
        self.display_series.append(([x.strftime("%Y-%m-%d %H:%M:%S") for x in display_time], display_values))

        log.info(f"Updating plot for {par_name} with {len(values)} data points, {max(values)=}.")

        self.replot()

    def replot(self) -> None:
        """Replot the data with the current marker."""
        # self.plt.clear_data()
        plotter = cast(Any, self.plt)
        plotter.clear_figure()  # not sure yet what the difference is with clf() or cld()
        plotter.date_form(PLOT_DATE_FORM)  # clear_figure() also resets the date form
        if self.xlimits is not None:
            # clear_figure() also clears the limits; keep showing the selected time range.
            start, end = self.xlimits
            plotter.xlim(start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"))
        plotter.title(self.title)
        # self.plt.xlabel("Date-Time")  # takes too much real estate
        # self.plt.ylabel("Value")  # will be put in the same space as the x-label
        # Not passing label= here: plotext's legend crashes (IndexError) when a labelled series has no point
        # within the current xlim, e.g. briefly while reloading after a time range change. The title is
        # colored to match each series instead (colored_plot_title()); color= here must stay in sync with it.
        for index, (time, data) in enumerate(self.display_series):
            plotter.plot(time, data, marker=self.marker, color=series_color(index))
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
            yield OptionList(*app.visible_options(app.options), markup=False)
            with Vertical():
                with Horizontal(id="info-container"):
                    yield ParameterInfo()
                    yield ParameterMetadata()
                with Horizontal(id="plot-controls"):
                    yield Button("Clear Plot", id="bt-plot-clear")
                    yield Button("Plot: TUI", id="bt-plot-backend")
                    yield Button(
                        "Data Range",
                        id="bt-time-range-data",
                        tooltip="Set the time range to the selected parameter's first and last sample on MUST.",
                    )
                    yield TimeRangeInput(app.time_range.start, app.time_range.end, id="time-range")
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
        ("ctrl+z", "toggle_plot_maximized", "Maximize plot"),
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

    plot_maximized: var[bool] = var(False)
    """Whether the plot area (plot + its controls) is maximized, hiding the rest of the main screen."""

    def __init__(self, config_file: Path | None = None) -> None:
        super().__init__()
        self.config_file = config_file
        self.offline_mode = False
        self.must_ctx: MustContext = MustContext()
        self.pars: dict[str, dict] = {}
        self.pars_info: dict = {}
        self.pars_catalog: dict = {}
        self.pars_mapping: dict = {}
        self.pars_descriptions: dict[str, str] = {}
        self.options: list[str] = sorted(self.pars_mapping.keys())
        self.search_fields: list[SearchFields] = []
        self.jump = False
        self._jump_window_start: int | None = None
        self._current_matches: list[str] = self.options
        self._search_generation: int = 0
        self.fuzz = False
        self.markers = cycle(self.MARKERS.keys())
        self.plot_widget: TimeRangePlotter = TimeRangePlotter(self.must_ctx)
        self.matplotlib_plotter = MatplotlibPlotter()
        self.plot_backend = "textual"
        self.time_range = TimeRange(start=datetime.datetime(2025, 12, 2), end=datetime.datetime(2025, 12, 5))

    async def on_mount(self) -> None:
        self.install_screen(LoadingScreen(), name="loading")
        self.push_screen("loading")
        asyncio.create_task(self._initialize_and_show_main_screen())
        self.set_interval(0.5, self._check_matplotlib_window)

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

        if self.must_ctx.authenticated and self._parameter_cache_is_stale():
            asyncio.create_task(self.refresh_parameter_catalog(force_refresh=True))

    def _parameter_cache_is_stale(self) -> bool:
        updated_at = get_parameter_cache_updated_at(data_provider=self.DATA_PROVIDER)
        if updated_at is None:
            return True
        return datetime.datetime.now(datetime.timezone.utc) - updated_at > PARAMETER_CACHE_MAX_AGE

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
        self.pars_descriptions = {}

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
            self.pars_descriptions[mib_name] = description

            label = f"{mib_name} [{description}]"
            if label in self.pars_mapping and self.pars_mapping[label] != mib_name:
                label = f"{label} ({mib_name})"
            self.pars_mapping[label] = mib_name

        self.options = sorted(self.pars_mapping.keys())
        self.search_fields = [self._option_search_fields(opt) for opt in self.options]
        self._jump_window_start = None
        self._current_matches = self.options
        controls = self._get_main_controls()
        if controls is not None:
            search_input, option_list = controls
            if search_input.value == "":
                option_list.set_options(self.visible_options(self.options))
            else:
                self.update_search()

    @staticmethod
    def visible_options(matches: list[str]) -> list[str | Option]:
        """Return at most MAX_VISIBLE_OPTIONS matches, plus a selectable hint when more were left out."""

        if len(matches) <= MAX_VISIBLE_OPTIONS:
            return list(matches)

        hidden = len(matches) - MAX_VISIBLE_OPTIONS
        hint = Option(
            f"… {hidden} more matches — press Enter to show all, or refine the search",
            id=SHOW_ALL_MATCHES_ID,
        )
        return [*matches[:MAX_VISIBLE_OPTIONS], hint]

    def _show_all_current_matches(self) -> None:
        """Replace the truncated option list with the full current match list (catalog or search results).

        `set_options()` on the full ~70k catalog takes ~2.7s and blocks the UI thread (see CLAUDE.md's
        performance notes) -- it's only ever called here, as an explicit, informed user action, never
        automatically. `loading` and the notification are set before that freeze, via `call_after_refresh`,
        so both actually paint first -- same `Widget.loading` spinner pattern used for plot fetches.
        """

        controls = self._get_main_controls()
        if controls is None:
            return
        _, option_list = controls
        matches = self._current_matches

        option_list.loading = True
        self.notify(f"Loading all {len(matches):,} matches…")
        self.call_after_refresh(self._finish_showing_all_matches, option_list, matches)

    @staticmethod
    def _finish_showing_all_matches(option_list: OptionList, matches: list[str]) -> None:
        option_list.set_options(matches)
        option_list.loading = False

    def _option_search_fields(self, option_label: str) -> SearchFields:
        """Return label, MIB name, the label's bracketed description, and PCF description_2 for searching."""

        mib_name = self.pars_mapping.get(option_label, "")
        mib_info = self.pars_info.get(mib_name, {}) if isinstance(self.pars_info, dict) else {}
        description = self.pars_descriptions.get(mib_name, "")
        description_2 = str(mib_info.get("description_2", ""))
        return option_label, mib_name, description, description_2

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
        # `self.screen` raises if no screen was ever pushed yet; `self.is_mounted` is a method (always truthy),
        # not the property this guard needs, so check the screen stack directly instead.
        if not self.screen_stack or not self._main_screen_active():
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

    def _check_matplotlib_window(self) -> None:
        """Revert to the TUI plot when the Matplotlib window was closed by the user, outside the app."""

        if self.plot_backend == "matplotlib" and not self.matplotlib_plotter.is_running():
            self._disable_matplotlib_backend()
            self.notify("The Matplotlib window was closed; showing the TUI plot.", title="Plot backend")

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

    @on(TimeRangeInput.Changed, "#time-range")
    def on_time_range_changed(self, event: TimeRangeInput.Changed) -> None:
        """Apply a new time range: move the x-axis and fetch the plotted parameters again for the new range.

        TimeRangeInput only posts valid ranges (start before end), once per change the user confirmed.
        """

        log.info(f"Time range changed to {event.time_range}")
        self._apply_time_range(event.time_range)

    def _apply_time_range(self, time_range: TimeRange) -> None:
        """Move the x-axis to `time_range` and fetch the plotted parameters again for it."""

        self.time_range = time_range
        start, end = time_range.start, time_range.end
        self.plot_widget.set_xlimits(start, end)
        if self.plot_backend == "matplotlib":
            self.matplotlib_plotter.set_xlimits(start, end)
        self.reload_plotted_parameters()

    @on(Button.Pressed, "#bt-time-range-data")
    def use_parameter_data_range(self, event: Button.Pressed) -> None:
        """Set the time range to the selected parameter's first and last sample, as reported by MUST."""

        event.stop()
        try:
            metadata = self.screen.query_one(ParameterMetadata).metadata
            time_range_input = self.screen.query_one(TimeRangeInput)
        except NoMatches:
            return

        try:
            new_range = parameter_data_range(metadata)
        except ValueError as exc:
            self.notify(str(exc), title="Time range not changed", severity="warning")
            return

        time_range_input.set_time_range(new_range)
        if new_range != self.time_range:
            self._apply_time_range(new_range)

    @work(exclusive=True, group="reload", exit_on_error=False)
    async def reload_plotted_parameters(self) -> None:
        """Fetch the plotted parameters again for the current time range and replace their series.

        Being exclusive, a further range change cancels a reload that is still running.
        """

        self.plot_widget.loading = True
        try:
            await self._reload_plotted_parameters()
        except Exception as exc:
            log.error(f"Reloading the plotted parameters failed: {exc!r}")
            self.notify(f"Reloading the plotted parameters failed: {exc}", severity="error")
        finally:
            self.plot_widget.loading = False

    async def _reload_plotted_parameters(self) -> None:
        names = list(dict.fromkeys(self.plot_widget.labels))  # unique, in plot order
        if not names:
            return

        time_range = self.time_range
        period = f"{format_datetime(time_range.start)} – {format_datetime(time_range.end)}"
        self.notify(f"Reloading {len(names)} plotted parameter(s) for {period}…", title="Time range changed")

        reloaded: dict[str, tuple[list[datetime.datetime], list[float]]] = {}
        without_data: list[str] = []
        truncated_names: list[str] = []
        for name in names:
            timestamps, values, was_truncated = await self._fetch_parameter_data(name, time_range)
            if timestamps and values:
                reloaded[name] = (timestamps, values)
                if was_truncated:
                    truncated_names.append(name)
            else:
                without_data.append(name)

        # Selecting a parameter or clearing the plot may have happened while fetching. Keep what was added
        # meanwhile (it was fetched for the new range already) and drop what was cleared.
        current = list(zip(self.plot_widget.labels, self.plot_widget.time, self.plot_widget.data))
        current_names = {label for label, _, _ in current}
        series = [(name, *reloaded[name]) for name in names if name in reloaded and name in current_names]
        series += [(label, t, v) for label, t, v in current if label not in names]

        self.plot_widget.clear_plot()
        if series:
            self.plot_widget.set_xlimits(time_range.start, time_range.end)
            self.plot_widget.set_ylimits(
                min(min(v) for _, _, v in series) - 1.0, max(max(v) for _, _, v in series) + 1.0
            )
            for name, timestamps, values in series:
                await self.plot_widget.update(name, timestamps, values, time_range)
        if self.plot_backend == "matplotlib":
            self.matplotlib_plotter.sync_from_plotter(self.plot_widget)

        message = f"Reloaded {len(reloaded)} of {len(names)} parameter(s) for {period}."
        if without_data:
            message += f" No data in this range for: {', '.join(without_data)} (removed from the plot)."
        if truncated_names:
            message += f" Only partial data (more samples were available) for: {', '.join(truncated_names)}."
        self.notify(
            message,
            title="Time range changed",
            severity="warning" if (without_data or truncated_names) else "information",
        )

    async def _fetch_parameter_data(
        self, par_name: str, time_range: TimeRange
    ) -> tuple[list[datetime.datetime], list[float], bool]:
        """Fetch the data of one parameter for a time range; empty lists when there is no data or no response.

        The third element is True when more data was available than the pagination safety limit allowed.
        """

        timestamps: list[datetime.datetime] = []
        values: list[float] = []
        truncated: list[bool] = []
        async for data in get_parameter_data(
            self.must_ctx,
            self.DATA_PROVIDER,
            par_name,
            format_datetime(time_range.start),
            format_datetime(time_range.end),
            paginated=True,
            truncated=truncated,
        ):
            page_timestamps, page_values = get_raw_data_with_timestamp(data)
            timestamps += page_timestamps
            values += page_values
        return timestamps, values, bool(truncated)

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
    def on_par_selected(self, message: ParameterSelected) -> None:
        self.plot_parameter(message.parameter_name)

    @work(exclusive=True, group="plot", exit_on_error=False)
    async def plot_parameter(self, par_name: str) -> None:
        """Fetch and plot data for a parameter in the background, so the UI stays responsive.

        Being exclusive, a new selection cancels a fetch that is still running.
        """

        self.plot_widget.loading = True
        try:
            await self._plot_parameter(par_name)
        except Exception as exc:
            log.error(f"Plotting parameter {par_name} failed: {exc!r}")
            self.show_warning_dialog(f"Plotting parameter {par_name} failed: {exc}")
        finally:
            self.plot_widget.loading = False

    async def _plot_parameter(self, par_name: str) -> None:
        if not self.time_range.is_valid():
            self.show_warning_dialog(
                f"The start time ({self.time_range.start}) must be before the end time ({self.time_range.end}). "
                "Adjust the time range and select the parameter again."
            )
            return

        data_provider = self.DATA_PROVIDER
        received_response = False
        timestamps: list[datetime.datetime] = []
        values: list[float] = []
        truncated: list[bool] = []

        # paginated=True: avoids one huge request for a parameter with many samples, and matches how MUST link's
        # REST API is documented -- the plain (non-paginated) endpoint has no limit/cursor of its own. Pages are
        # accumulated below rather than plotted as they arrive, since plot_widget.update() adds a new trace per
        # call; it isn't (yet) able to append more points to the trace it just added.
        async for data in get_parameter_data(
            self.must_ctx,
            data_provider,
            par_name,
            format_datetime(self.time_range.start),
            format_datetime(self.time_range.end),
            paginated=True,
            truncated=truncated,
        ):
            received_response = True
            page_timestamps, page_values = get_raw_data_with_timestamp(data)
            timestamps += page_timestamps
            values += page_values

        self._show_sample_count(par_name, len(timestamps) if received_response else None)

        if not received_response:
            self.show_warning_dialog(
                f"No response from the MUST server for parameter {par_name}. The request failed or timed out; "
                "see the log for details."
            )
            return

        log.info(
            f"Fetched data for parameter {par_name} from {self.time_range.start} to {self.time_range.end}, "
            f"data length: {len(timestamps)}"
        )

        if not timestamps or not values:
            log.warning(f"No data available for parameter {par_name} in the specified time range.")
            self.show_warning_dialog(f"No data available for parameter {par_name} in the specified time range.")
            return

        if truncated:
            self.notify(
                f"Only the first {len(timestamps):,} samples of {par_name} were loaded; more were available "
                "in this time range.",
                title="Parameter data truncated",
                severity="warning",
            )

        self.plot_widget.set_xlimits(self.time_range.start, self.time_range.end)
        self.plot_widget.set_ylimits(min(values) - 1.0, max(values) + 1.0)
        await self.plot_widget.update(par_name, timestamps, values, self.time_range)

        if self.plot_backend == "matplotlib":
            with self.matplotlib_plotter.batch():
                self.matplotlib_plotter.set_xlimits(self.time_range.start, self.time_range.end)
                self.matplotlib_plotter.set_ylimits(min(values) - 1.0, max(values) + 1.0)
                await self.matplotlib_plotter.update(par_name, timestamps, values, self.time_range)

    def _show_sample_count(self, par_name: str, count: int | None) -> None:
        """Show how many samples were fetched for `par_name`, if its metadata panel is still the one displayed."""

        if not self.screen_stack:
            return
        try:
            parameter_metadata = self.screen.query_one(ParameterMetadata)
        except NoMatches:
            return
        if parameter_metadata.par_name == par_name:
            parameter_metadata.set_sample_count(count)

    def action_toggle_jump(self) -> None:
        controls = self._get_main_controls()
        if controls is None:
            return

        self.jump = not self.jump
        mode = "Jump" if self.jump else "Filter"
        controls[0].placeholder = f"Search Mode: {mode}"
        self.update_search()

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
        self.update_search()

    @on(Input.Changed, "#input-search")
    def filter(self, event: Input.Changed) -> None:
        # Only the search box: the time range fields are Input widgets too.
        if not self._main_screen_active():
            return

        self.update_search()

    @on(OptionList.OptionSelected)
    def expand_to_all_matches(self, event: OptionList.OptionSelected) -> None:
        if event.option.id != SHOW_ALL_MATCHES_ID:
            return
        event.stop()
        self._show_all_current_matches()

    @on(OptionList.OptionSelected)
    async def show_parameter_info(self, event: OptionList.OptionSelected) -> None:
        if event.option.id == SHOW_ALL_MATCHES_ID or not self._main_screen_active():
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
        if event.option.id == SHOW_ALL_MATCHES_ID or not self._main_screen_active():
            return

        log.debug(f"{event.option=}")
        par_name = event.option.prompt
        mib_name = self.pars_mapping.get(par_name)
        log.debug(f"{par_name=}, {mib_name=}")
        if mib_name:
            self.load_parameter_metadata(mib_name)

    @work(exclusive=True, group="metadata", exit_on_error=False)
    async def load_parameter_metadata(self, mib_name: str) -> None:
        """Fetch parameter metadata in the background, then request the plot.

        Being exclusive, a new selection cancels a fetch that is still running.
        """

        try:
            metadata = await get_parameter_metadata(self.must_ctx, mib_name)
            try:
                parameter_metadata = self.screen.query_one(ParameterMetadata)
            except NoMatches:
                return
            await parameter_metadata.update_metadata(mib_name, metadata[0] if metadata else {})
        except Exception as exc:
            log.error(f"Loading metadata for parameter {mib_name} failed: {exc!r}")
        self.post_message(ParameterSelected(mib_name))

    def _search_mode(self) -> str:
        return "jump" if self.jump else "fuzzy" if self.fuzz else "regex"

    def update_search(self) -> None:
        """Update the option list for the text in the search box.

        An empty search shows the start of the list right away; otherwise the search runs in the `search_options`
        worker after a short pause in typing.
        """

        controls = self._get_main_controls()
        if controls is None:
            return

        search_input, option_list = controls
        self.workers.cancel_group(self, "search")
        if search_input.value == "":
            option_list.loading = False
            if not self.jump:
                self._current_matches = self.options
                option_list.set_options(self.visible_options(self.options))
            return

        option_list.loading = True
        self._search_generation += 1
        self.search_options(search_input.value, self._search_mode(), self._search_generation)

    @work(exclusive=True, group="search", exit_on_error=False)
    async def search_options(self, search: str, mode: str, generation: int) -> None:
        """Search the ~70k options on a thread, so typing stays responsive.

        Being exclusive, every keystroke cancels the previous search while it is still waiting out the debounce.
        A search that is already running on the thread can't be stopped, so its result is dropped when the search
        text, the mode or the catalog changed in the meantime. `option_list.loading` (set by the caller before
        this worker starts) covers the debounce + match time with a spinner, so typing doesn't look like it did
        nothing -- the combined delay (debounce plus the regex/fuzzy match itself) is small but not instant.

        `generation` guards against a race on that shared `loading` flag: if this worker is cancelled, its
        `finally` can still run after a newer keystroke has already started a new worker and set `loading`
        back to True, since cancellation only takes effect at the next `await`, not synchronously. Clearing
        `loading` only when `generation` is still the latest one avoids an old, superseded worker turning the
        spinner off for a search that is still in progress.
        """

        try:
            await asyncio.sleep(SEARCH_DEBOUNCE)
            await self._search_options(search, mode)
        finally:
            if generation == self._search_generation:
                controls = self._get_main_controls()
                if controls is not None:
                    controls[1].loading = False

    async def _search_options(self, search: str, mode: str) -> None:
        options = self.options
        try:
            indices = await asyncio.to_thread(match_options, search, self.search_fields, mode)
        except re.error as exc:
            log.error(f"Invalid regex pattern: {exc}")
            return

        controls = self._get_main_controls()
        if controls is None:
            return
        search_input, option_list = controls
        if search_input.value != search or self._search_mode() != mode or self.options is not options:
            return

        if mode == "jump":
            if indices:
                self._jump_to_index(option_list, indices[0])
        else:
            matches = [options[idx] for idx in indices]
            self._current_matches = matches
            option_list.set_options(self.visible_options(matches))

    def _jump_to_index(self, option_list: OptionList, idx: int) -> None:
        """Highlight option `idx`, showing a window of the sorted list around it instead of the whole list."""

        # Only refill the list when the option is not already in the window that is shown.
        start = self._jump_window_start
        if not (
            start is not None
            and 0 <= idx - start < option_list.option_count
            and option_list.get_option_at_index(idx - start).prompt == self.options[idx]
        ):
            start = max(0, min(idx - MAX_VISIBLE_OPTIONS // 2, len(self.options) - MAX_VISIBLE_OPTIONS))
            option_list.set_options(self.options[start : start + MAX_VISIBLE_OPTIONS])
            self._jump_window_start = start
        option_list.highlighted = idx - start

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

    def action_toggle_plot_maximized(self) -> None:
        """Toggle whether the plot area (plot + its controls) fills the main screen."""

        if not self._main_screen_active():
            return
        self.plot_maximized = not self.plot_maximized

    def watch_plot_maximized(self, maximized: bool) -> None:
        if not self._main_screen_active():
            return
        self.screen.set_class(maximized, "-plot-maximized")

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
