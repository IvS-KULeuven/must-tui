# MUST TUI Help

A terminal UI for inspecting MUST link telemetry and housekeeping parameters.

## Searching

- **Filter mode** (default): narrows the parameter list to matches. The search text is a regular
  expression by default; untick **Regex** to switch to fuzzy matching instead.
- **Jump mode**: highlights the single best fuzzy match in the full, alphabetically sorted list
  instead of filtering it.
- When more than 500 parameters match, the list's last entry can be selected (Enter or click) to
  load all of them instead of just the first 500.

## Parameter List and Plot

- Select a parameter (Enter or click) to load its MIB info, server metadata, and plot its data for
  the current time range.
- Multiple parameters can be plotted together; each new selection adds a trace without clearing
  previous ones.
- The **Data Range** button sets the time range to the selected parameter's first and last sample.
- The Y-axis auto-scales by default. The **Y Range** button sets an explicit Min/Max, or goes back
  to auto-scaling; a locked range stays in effect until you choose Auto again.
- The plot marker style can be cycled through four options (Braille, Standard Definition, High
  Definition, Dot).
- Switch between the built-in terminal plot and an external Matplotlib window with the
  **Plot: TUI / Plot: Matplotlib** button (or its shortcut).

## Status Line

The line above the footer shows the data provider, connection state, the number of parameters
currently loaded, and how long ago the parameter cache was last refreshed.

## Keyboard Shortcuts

<!-- KEYBINDINGS -->
