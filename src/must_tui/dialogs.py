from typing import Optional
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button
from textual.widgets import Label


class ErrorDialog(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "dismiss(False)", "", show=False)]

    def __init__(
        self,
        title: Optional[str] = None,
        error_message: Optional[str] = None,
        ok_label: Optional[str] = "OK",
        cancel_label: Optional[str] = "Cancel",
        quit_on_ok: bool = False,
    ) -> None:
        super().__init__()
        self._title = title or "Error"
        self._error_message = error_message or "An unexpected error has occurred."
        self._ok_label = ok_label
        self._cancel_label = cancel_label
        self._quit_on_ok = quit_on_ok

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title)
            yield Label(self._error_message, id="lbl-error-message")
            with Horizontal():
                yield Button(self._ok_label, variant="primary", id="btn-dialog-ok")
                yield Button(self._cancel_label, id="btn-dialog-cancel")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """React to button press."""

        if event.button.id == "btn-dialog-ok":
            if self._quit_on_ok:
                self.app.exit()
            self.dismiss(True)
            # self.app.query_one("#console-log", ConsoleOutput).write_log_info("You pressed YES...")
        else:
            self.dismiss(False)
            # self.app.query_one("#console-log", ConsoleOutput).write_log_info("You pressed NO...")


class WarningDialog(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "dismiss(False)", "", show=False)]

    def __init__(
        self,
        title: Optional[str] = None,
        warning_message: Optional[str] = None,
        ok_label: Optional[str] = "OK",
    ) -> None:
        super().__init__()
        self._title = title or "Warning"
        self._warning_message = warning_message or "An unexpected warning has occurred."
        self._ok_label = ok_label

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title)
            yield Label(self._warning_message, id="lbl-warning-message")
            with Horizontal():
                yield Button(self._ok_label, variant="primary", id="btn-dialog-ok")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """React to button press."""

        if event.button.id == "btn-dialog-ok":
            self.dismiss(False)
            # self.app.query_one("#console-log", ConsoleOutput).write_log_info("You pressed OK...")
