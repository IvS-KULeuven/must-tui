import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def can_open_matplotlib_window() -> bool:
    """Return True when a graphical plotting window is likely available."""

    if sys.platform == "darwin":
        return True

    return any(os.environ.get(variable) for variable in ("DISPLAY", "WAYLAND_DISPLAY", "MIR_SOCKET"))


class MatplotlibPlotter:
    """Render the selected parameter data in a separate matplotlib process."""

    MARKERS = {
        "braille": ".",
        "sd": "s",
        "hd": "o",
        "dot": ".",
    }

    def __init__(self) -> None:
        self.title: str = "Parameter Data Plot"
        self.data: list[list[float]] = []
        self.time: list[list[datetime.datetime]] = []
        self.labels: list[str] = []
        self.xlimits: tuple[datetime.datetime, datetime.datetime] | None = None
        self.ylimits: tuple[float, float] | None = None
        self.marker: str = "dot"
        self._process: subprocess.Popen[str] | None = None
        self._stdin: Any = None

    def set_context(self, _must_ctx: Any) -> None:
        # Keeps the same app-facing API as the textual plotter.
        return

    def set_title(self, title: str) -> None:
        self.title = title
        self._send_state()

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return

        script_path = Path(__file__).with_name("mpl_plotter.py")
        self._process = subprocess.Popen(
            [sys.executable, "-u", str(script_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._stdin = self._process.stdin
        self._send_state()

    def set_xlimits(self, start: datetime.datetime, end: datetime.datetime) -> None:
        self.xlimits = (start, end)
        self._send_state()

    def set_ylimits(self, ymin: float, ymax: float) -> None:
        self.ylimits = (ymin, ymax)
        self._send_state()

    def clear_plot(self) -> None:
        self.data = []
        self.time = []
        self.labels = []
        self._send_state()

    def sync_from_plotter(self, plotter: Any) -> None:
        self.title = plotter.title
        self.data = [series[:] for series in plotter.data]
        self.time = [timestamps[:] for timestamps in plotter.time]
        self.labels = plotter.labels[:]
        self.xlimits = plotter.xlimits
        self.ylimits = plotter.ylimits
        self.marker = plotter.marker
        self._send_state()

    async def update(
        self, par_name: str, timestamps: list[datetime.datetime], values: list[float], _time_range: Any
    ) -> None:
        self.set_title(f"Parameter: {par_name}")
        self.time.append(timestamps)
        self.data.append(values)
        self.labels.append(par_name)
        self._send_state()

    def close(self) -> None:
        if self._stdin is not None:
            try:
                self._stdin.write(json.dumps({"type": "close"}) + "\n")
                self._stdin.flush()
            except Exception:
                pass

        if self._process is None:
            return

        try:
            self._process.terminate()
        finally:
            self._process = None
            self._stdin = None

    def _send_state(self) -> None:
        if self._stdin is None:
            return

        payload = {
            "type": "update",
            "title": self.title,
            "marker": self.marker,
            "labels": self.labels,
            "time": [[timestamp.isoformat() for timestamp in series] for series in self.time],
            "data": self.data,
            "xlimits": [self.xlimits[0].isoformat(), self.xlimits[1].isoformat()] if self.xlimits else None,
            "ylimits": list(self.ylimits) if self.ylimits else None,
        }

        try:
            self._stdin.write(json.dumps(payload) + "\n")
            self._stdin.flush()
        except Exception:
            self._stdin = None

    def replot(self) -> None:
        self._send_state()
