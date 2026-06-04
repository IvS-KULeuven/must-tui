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
