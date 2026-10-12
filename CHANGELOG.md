# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `Ctrl+Z` maximizes the plot and its controls (Clear Plot, Plot: TUI/Matplotlib, Data Range, the time range fields), hiding the header, footer, search box, parameter list and info panels; press again to restore.
- A spinner now covers the plot while fetching data for a selection or a time range change, so it's clear the app is working rather than stuck — this can take a while for a parameter with many samples in the selected range.
- The Parameter Metadata panel shows `plotted-samples`, the number of data points fetched for the current plot.
- When the parameter list has more than 500 matches, its truncation hint is now selectable: pressing Enter on it (or clicking it) loads every match instead of only the first 500.
- A spinner now covers the parameter list while a search is in progress, so it's clear the app is working rather than stuck — without it, the debounce plus the regex/fuzzy match itself (small but not instant) could look like typing had no effect.
- A status line above the footer shows the data provider, connection state, the number of parameters currently loaded, and how long ago the parameter cache was last refreshed.
- `F1` opens an in-app Help screen (usage notes and the full keyboard shortcut reference), scrollable, closed with the Close button or Escape. The shortcut table is generated from the app's own key bindings, so it can't drift out of sync with them.
- A **Y Range** button next to **Data Range** opens a small dialog to set an explicit Y-axis minimum and maximum, or go back to auto-scaling. A locked range stays in effect across new parameter selections, time range reloads, and clearing the plot, for both the TUI and Matplotlib backends, until Auto is chosen again.

### Changed

- **All keyboard shortcuts are now a `Ctrl`-combo or a function key; none are bare letters any more.** Previously, `c`, `p`, `r`, `d` and `m` only worked while the search box did not have focus (a focused text field takes plain letters as typed input first) and would appear in or vanish from the footer depending on focus. `p` → `F2` (toggle plot backend), `r` → `F5` (soft-refresh the parameter cache), `d` → `F4` (toggle light/dark mode), `m` → `F3` (cycle marker style); `c` (Clear Plot) is replaced by `Ctrl+L` with no bare-letter alias. `Ctrl+Q`, `Ctrl+Z`, `Ctrl+J` and `Ctrl+R` are unchanged. The footer shows only `F1`, `Ctrl+Q`, `Ctrl+L` and `Ctrl+Z`; the rest work the same as before but are left off the footer to keep it short — press `F1` for the complete list.
- Fetching parameter data for the TUI (both selecting a parameter and reloading on a time range change) now uses MUST link's paginated endpoint (5000 samples per request) instead of one unpaginated request for the whole range, matching how the REPL helper (`get_parameter_series_async`) already worked. The paginated endpoint is the one MUST link's REST API documents `limit`/`cursor` on; the plain endpoint has no such parameter, so it no longer receives a (previously meaningless) `limit=1000`.
- The internal safety limit on how many pages a paginated fetch follows is raised from 20 (20,000 samples) to 1000 (5,000,000 samples at the new page size), and reaching it now shows a warning naming the affected parameter(s) and the sample count actually loaded, instead of silently returning incomplete data.

### Fixed

- Fetching paginated parameter data reuses one connection for every page of a fetch, instead of opening a new one (a fresh TCP+TLS handshake) per page. This had made a multi-page fetch considerably slower than the single unpaginated request it replaced.
- The Matplotlib plot's title no longer shows raw ANSI color codes (e.g. after switching to that backend, or after a time range change). It was picking up the TUI plot's title as-is, which is colored per parameter to make up for not having a real legend there — the Matplotlib window has a real legend and now gets the plain title.
- The plot title now lists every currently plotted parameter (`Parameter: X` for one, `Parameters: X, Y, …` for several) instead of only the most recently selected one. This applies to both the TUI and Matplotlib plots. In the TUI plot, each name in the title is colored to match its trace — a real legend there can crash plotext (the terminal plotting library) in some situations, so this is used instead.
- An anchored regex search (e.g. `^NC01_FRAME`) now matches the description shown in brackets in the parameter list. It previously could never match there: the label is `"MIBNAME [description]"`, so `^` on the combined label only ever matched at the MIB name's position, not the bracket's — the description is now its own, separately-anchorable search field.
- Plotting a second parameter with a very different value range no longer clips the first one out of view in the Matplotlib backend. Each new selection used to set the Y-axis to just that one parameter's range (±1); the TUI plot never actually showed this (it was silently reset on every redraw), but Matplotlib applied it as-is. Auto mode now behaves the same, correctly, on both backends: the Y-axis fits everything currently plotted.

## [0.4.0] - 2026-10-12

### Changed

- The parameter list shows at most 500 entries, with a final entry saying how many more matches were left out. Refilling the list with all ~70,000 parameters took seconds on startup, on clearing the search, and on broad searches such as a single letter.
- Jump mode (`Ctrl+J`) shows a window of up to 500 parameters around the best match instead of the full list.
- At startup, the parameter catalog is only re-downloaded from the server when the local cache is missing or older than one day (previously on every launch). Press `r` to refresh it manually.
- Loading the parameter catalog from the SQLite cache and storing a refreshed catalog in it now run in a worker thread instead of on the UI event loop.
- The terminal plot draws at most 1000 points per trace, keeping the minimum and maximum of each time bucket so spikes remain visible. Redrawing previously cost about 100 ms per plotted trace (3 days of data), which made the app slower with every selected parameter. The Matplotlib backend still receives the full data.
- Selecting a parameter fetches its metadata and data in background workers, so keyboard input no longer waits for the MUST server. A new selection cancels a fetch that is still running.
- With the Matplotlib backend, each selection sends the plot state to the Matplotlib process once instead of four times.
- Searching the parameter list waits until typing pauses for 0.15 s and then runs on a worker thread, so keystrokes show up immediately instead of waiting for the previous key's search (previously 0.5 s in regex mode and 1.2–1.7 s in fuzzy and jump mode on the ~70,000-parameter catalog). Results are the same as before. Toggling jump mode (`Ctrl+J`) now re-runs the search for the current text.
- An invalid regular expression in the search box now keeps the current list instead of emptying it.
- The date-time range picker (`textual-timepiece`) is replaced by two plain start/end fields and a **Lock** checkbox. Type `YYYY-MM-DD`, `YYYY-MM-DD HH:MM`, `YYYY-MM-DD HH:MM:SS` or `now`; a field is applied when you press Enter or leave it. Invalid dates and a start that is not before the end are marked in red, explained in a notification, and leave the range unchanged. **Lock** keeps the duration fixed when one end is changed. This removes the old picker's problems: months 03–09 could not be typed, Backspace on the month edited the year, impossible dates such as 2025-11-31 were silently ignored, and every keystroke that formed a valid date already changed the range.
- Changing the time range now fetches the plotted parameters again for the new range (previously only the x-axis moved, leaving parts of the range without data). Notifications report the reload and any parameter without data in the new range, which is removed from the plot.
- `Escape` leaves a focused time range field, committing a pending valid edit first, like pressing `Tab` does.
- A **Data Range** button sets the time range to the selected parameter's first and last sample, as reported by MUST.
- The time range fields accept relative date-times: `now`, `today`, `yesterday`, `tomorrow`, and any of those with a signed offset such as `now-6h`, `tomorrow+1d` or a bare `-2h30m` (relative to `now`), using `w`/`d`/`h`/`m`/`s` units.
- `↑`/`↓` step the date-time component (year, month, day, hour, minute, second) under the cursor in a time range field, up or down by one, without applying the field. A relative expression is resolved to an absolute date-time first. Day/month stepping clamps to the last valid day of the month; stepping past year 1000 or 9999 does nothing.

### Fixed

- The terminal plot keeps showing the selected time range on its x-axis. Every redraw (a new trace, a marker change) used to reset the axis to the span of the data.
- Plotting the first parameter of a session no longer fails with `Date Form should be: %d/%m/%Y`. plotext's date format was only set when redrawing an existing plot, so the very first `set_xlimits()` call crashed (caught and shown as a warning dialog).
- MUST link requests now time out when connecting or waiting for data takes longer than 30 seconds (previously aiohttp's default of 5 minutes in total, during which the TUI appeared to hang). There is no limit on the total duration, so large transfers still complete. This applies to the REPL helpers too. In the TUI, a request that gets no response is reported in a warning dialog instead of failing silently.
- A login or request timeout is now handled and logged instead of raising an uncaught `TimeoutError`.
- The Parameter Format Code from the PCF is now stored under the key `pfc` (was `pcf`), so the TUI's ParameterInfo panel shows its value instead of `N/A`. In `search_parameters()` results the field is now `pcf_pfc` (was `pcf_pcf`).
- The Clear Plot, Plot: TUI/Matplotlib and Data Range buttons no longer reverse-highlight their label text when focused (Textual's default style). The whole button now gets the already-present subtle background tint instead.
- Closing the Matplotlib plot window now reverts the app to the TUI plot and updates the button label, instead of leaving the app thinking the Matplotlib backend is still active. Checked twice a second while that backend is selected.

## [0.3.7] - 2026-06-30

### Added

- `search_parameters()` and `search_parameters_async()` REPL helpers that search parameters by name, MUST description, or PCF `description_2` and return a list of `NavigableDict` records. Each record merges all MUST server metadata fields (snake_case keys, e.g. `first_sample`) with PCF/MIB fields (prefixed with `pcf_`, e.g. `pcf_description_2`). Results are directly compatible with `pd.DataFrame(result)`.
- `viz` optional dependency group (`uv sync --extra viz`) adding Plotly for richer visualisation workflows.

### Fixed

- `DateTimeRangePicker` changes in the TUI now correctly persist to `MUSTApp.time_range`, so downstream code and the Matplotlib backend both see the updated time range.

## [0.3.6] - 2026-06-10

### Changed

- `get_parameter_names` (and its async counterpart) now also searches the PCF `description_2` field from the bundled MIB, matching the search behaviour of the TUI.
- `get_parameter_names` now returns the MIB description instead of an empty string when the MUST description is absent or merely repeats the parameter name.

## [0.3.5] - 2026-06-10

### Added

- Optional `--config` command-line argument to specify a custom configuration file path.

### Fixed

- `ErrorDialog` enhanced with a `quit_on_ok` option to allow quitting the app on fatal errors.
- Added `clear_plot` action to `MUSTApp` for explicitly clearing the plot view.
- Improved descriptions for telemetry parameter metadata fields (`categ`, `natur`) in documentation.
- Clarified calibration category and nature-of-parameter descriptions in the usage guide.

### Documentation

- Enhanced documentation with environment variable configuration and a comprehensive usage guide.

## [0.3.4] - 2026-06-08

### Changed

- Refactored search functionality: improved description handling and fuzzy/regex matching in `MUSTApp`.

### Documentation

- Updated user guide to include async helper usage in Jupyter notebooks.

## [0.3.3] - 2026-06-05

### Fixed

- Corrected async handling in `must.py` to ensure sync wrappers behave correctly when called from both notebooks and scripts.

### Tests

- Added tests for async wrapper behavior in `must.py`.

## [0.3.2] - 2026-06-05

### Added

- Python REPL helper functions for parameter retrieval (e.g. for use in Jupyter notebooks).
- Documentation for REPL helpers and parameter access patterns.

## [0.3.1] - 2026-06-05

### Added

- `LoadingScreen` displayed during application initialization.
- Status update messages during the login and bootstrap process.

[0.3.7]: https://github.com/IvS-KULeuven/must-tui/compare/v0.3.6...v0.3.7
[0.3.6]: https://github.com/IvS-KULeuven/must-tui/compare/v0.3.5...v0.3.6
[0.3.5]: https://github.com/IvS-KULeuven/must-tui/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/IvS-KULeuven/must-tui/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/IvS-KULeuven/must-tui/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/IvS-KULeuven/must-tui/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/IvS-KULeuven/must-tui/compare/v0.3.0...v0.3.1
