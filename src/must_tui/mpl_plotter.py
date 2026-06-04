from __future__ import annotations

import datetime
import json
import queue
import sys
import threading
from typing import Any

import matplotlib.dates as mdates
import matplotlib.pyplot as plt


MARKERS = {
    "braille": ".",
    "sd": "s",
    "hd": "o",
    "dot": ".",
}


class PlotState:
    def __init__(self) -> None:
        self.title = "Parameter Data Plot"
        self.marker = "dot"
        self.labels: list[str] = []
        self.time: list[list[datetime.datetime]] = []
        self.data: list[list[float]] = []
        self.xlimits: tuple[datetime.datetime, datetime.datetime] | None = None
        self.ylimits: tuple[float, float] | None = None

    def update(self, payload: dict[str, Any]) -> None:
        self.title = payload.get("title", self.title)
        self.marker = payload.get("marker", self.marker)
        self.labels = payload.get("labels", self.labels)
        self.time = [
            [datetime.datetime.fromisoformat(timestamp) for timestamp in series]
            for series in payload.get("time", self.time)
        ]
        self.data = payload.get("data", self.data)

        xlimits = payload.get("xlimits")
        self.xlimits = (
            (
                datetime.datetime.fromisoformat(xlimits[0]),
                datetime.datetime.fromisoformat(xlimits[1]),
            )
            if xlimits
            else None
        )

        ylimits = payload.get("ylimits")
        self.ylimits = (ylimits[0], ylimits[1]) if ylimits else None


def main() -> int:
    state_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    state = PlotState()
    figure, axes = plt.subplots()

    def redraw() -> None:
        axes.clear()
        axes.set_title(state.title)
        axes.grid(True, alpha=0.2)
        axes.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d %H:%M:%S"))

        marker = MARKERS.get(state.marker, ".")
        for timestamps, values, label in zip(state.time, state.data, state.labels):
            axes.plot(mdates.date2num(timestamps), values, marker=marker, linestyle="-", label=label)

        if state.xlimits is not None:
            axes.set_xlim(float(mdates.date2num(state.xlimits[0])), float(mdates.date2num(state.xlimits[1])))
        if state.ylimits is not None:
            axes.set_ylim(state.ylimits[0], state.ylimits[1])
        if state.time and state.labels:
            axes.legend(loc="best")

        figure.autofmt_xdate()
        figure.canvas.draw_idle()
        figure.canvas.flush_events()

    def reader() -> None:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            state_queue.put(message)
            if message.get("type") == "close":
                break

    threading.Thread(target=reader, daemon=True).start()

    def poll_queue() -> None:
        updated = False
        while True:
            try:
                message = state_queue.get_nowait()
            except queue.Empty:
                break

            if message.get("type") == "close":
                plt.close(figure)
                return

            state.update(message)
            updated = True

        if updated:
            redraw()

    timer = figure.canvas.new_timer(interval=100)
    timer.add_callback(poll_queue)
    timer.start()

    plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
