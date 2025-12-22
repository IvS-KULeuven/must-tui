import re
import asyncio

from textual import log, on, work
from textual.app import App, ComposeResult
from textual.message import Message
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, OptionList, Static, Checkbox, DataTable
from thefuzz import process

# from textual_plotext import PlotextPlot as PlotWidget
from textual_plot import HiResMode, PlotWidget

# from egse.system import title_to_kebab
from .must import (
    MustContext,
    title_to_kebab,
    get_parameter_data,
    get_parameter_metadata,
    get_raw_data_with_timestamp,
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


class ParameterSelected(Message):
    """Message sent when a parameter is selected from the option list."""

    def __init__(self, parameter_name: str) -> None:
        super().__init__()
        self.parameter_name = parameter_name


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
            log.debug(f"Adding row: {field}={value}")
            self.table.add_row(field, str(value), key=field)


class TimeRangePlotter(Static): ...


class MUSTApp(App[None]):
    CSS_PATH = "must_app.tcss"

    BINDINGS = [("ctrl+j", "toggle_jump", "Toggle Jump Mode")]

    def __init__(self, ctx: MustContext, parameters: dict[str, dict]) -> None:
        super().__init__()
        self.pars_info = parameters["pcf"]
        self.pars_mapping = parameters["mapping"]
        self.options: list[str] = sorted(self.pars_mapping.keys())
        self.jump = False
        self.fuzz = False
        self.plot_widget: PlotWidget = PlotWidget()
        self.ctx = ctx

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
                yield self.plot_widget
        yield Footer()

    @work()
    async def plot_parameter_data(self, par_name: str, data_provider: str, start: str, end: str) -> None:
        plt = self.plot_widget
        plt.clear()

        for data in get_parameter_data(self.ctx, data_provider, par_name, start, end, paginated=False):
            timestamps, values = get_raw_data_with_timestamp(data)
            log.info(f"Plotting data for parameter {par_name} from {start} to {end}, data length: {len(timestamps)}")
            await asyncio.sleep(0.1)
            if len(timestamps):
                # plt.plot([x.timestamp() for x in timestamps], values, hires_mode=HiResMode.QUADRANT)
                plt.scatter([x.timestamp() for x in timestamps], values, marker="⦿")
            else:
                break
        self.refresh()

    @on(ParameterSelected)
    async def on_par_selected(self, message: ParameterSelected) -> None:
        data_provider = "PLATO"
        par_name = message.parameter_name
        start = "2025-11-25 00:00:00"
        end = "2025-12-05 00:00:00"

        self.plot_parameter_data(par_name, data_provider, start, end)

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
            metadata = get_parameter_metadata(self.ctx, mib_name)
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
