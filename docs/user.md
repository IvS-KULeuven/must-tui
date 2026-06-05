# User Guide

This section describes how to install and run `must-tui`.

## Installation

```bash
uv tool install must-tui
```

## Run

```bash
must-tui
```

## Configuration

The application reads MUST link connection settings from environment variables
with precedence over the config file.

Configuration precedence:

1. Environment variables
2. Config file at `~/.config/must-tui/config.json`

Supported environment variables:

- `MUST_LINK_BASE_URL`
- `MUST_LINK_USERNAME`
- `MUST_LINK_PASSWORD`

If any required value is missing from both sources, login fails with an error.

Example config file:

```json
{
	"base_url": "https://mustlink.example.org",
	"username": "your-username",
	"password": "your-password",
	"connect_timeout": 30,
	"token": "optional-fallback-token"
}
```

Example environment-based setup:

```bash
export MUST_LINK_BASE_URL="https://mustlink.example.org"
export MUST_LINK_USERNAME="your-username"
export MUST_LINK_PASSWORD="your-password"
must-tui
```

## Notes

- The tool requires network access to the MUST backend.
- Authentication must be configured in your runtime environment.

## Python REPL Helpers

The `must_tui.must` module provides two helper functions that are
useful from a Python REPL or scripts.

In a notebook, prefer the async helpers with `await` when you can:

```python
from must_tui.must import get_parameter_names_async

matches = await get_parameter_names_async(
  name_pattern="nc12.*tsense",
  data_provider="PLATO",
  use_cache=True,
)
```

The synchronous wrappers, such as `get_parameter_names()` and
`get_parameter_series()`, are still available for plain scripts and will also
work in notebooks through an internal bridge.

### `get_parameter_names()`

Use this function to search parameter names and descriptions.

```python
from must_tui.must import get_parameter_names

matches = get_parameter_names(
    name_pattern="nc12.*tsense",
    data_provider="PLATO",
    use_cache=True,
)

for name, description in matches.items():
    print(name, "-", description)
```

Arguments:

- `name_pattern` (`str`): Case-insensitive regex pattern. If the regex is
  invalid, the value is treated as a literal string.
- `data_provider` (`str | None`): Optional provider name. If omitted, all
  available providers are searched.
- `use_cache` (`bool`): When `True` (default), searches the local parameter
  catalog cache for faster lookups.

Returns:

- `dict[str, str]`: Mapping of parameter name to description.

### `get_parameter_series()`

Use this function to retrieve one or more parameter time series.

```python
from datetime import datetime
from must_tui.must import get_parameter_series

series = get_parameter_series(
    parameter_names=["CNLA1022", "CNLA1023"],
    data_provider="PLATO",
    start=datetime(2025, 1, 1, 0, 0, 0),
    end=datetime(2026, 1, 1, 1, 0, 0),
)

for par_name, samples in series.items():
    print(par_name, len(samples), "samples")
```

Arguments:

- `parameter_names` (`str | list[str]`): One parameter name or a list of names.
- `data_provider` (`str`): Provider name used for data retrieval.
- `start` (`str | datetime | None`): Optional start time.
- `end` (`str | datetime | None`): Optional end time.
- `ctx` (`MustContext | None`): Optional authenticated context.

If `start` and `end` are omitted, the helper uses metadata bounds
(`first-sample` and `last-sample`) per parameter.

Returns:

- `dict[str, list[tuple[datetime, float | int]]]`: Mapping of parameter name to
  `(timestamp, value)` samples.
