# Environment Configuration

This page describes environment variables used by must-tui, how they are resolved, and practical setup examples.

## Supported environment variables

must-tui uses the following environment variables:

- `MUST_LINK_BASE_URL`: Base URL of the MUST link server.
- `MUST_LINK_USERNAME`: Username used for authentication.
- `MUST_LINK_PASSWORD`: Password used for authentication.
- `VERBOSE_DEBUG`: Optional debug toggle (`1` enables verbose debug logs).
- `XDG_CACHE_HOME`: Root directory for cache files.

`XDG` stands for **Cross-Desktop Group**. `XDG_CACHE_HOME` is part of the XDG Base Directory Specification, which defines standard locations for user-specific files such as cache, configuration, and data.


## Resolution behavior

Credential lookup behavior:

1. Read from environment variables.
2. If missing, fall back to user config file at `~/.config/must-tui/config.json`.

Cache database location:

- If `XDG_CACHE_HOME` is set:
  - `$XDG_CACHE_HOME/must-tui/parameters.sqlite3`
- If `XDG_CACHE_HOME` is not set:
  - `~/.cache/must-tui/parameters.sqlite3`

## Example: temporary shell session

```bash
export MUST_LINK_BASE_URL="https://must.example.org"
export MUST_LINK_USERNAME="flight-user"
export MUST_LINK_PASSWORD="change-me"
export VERBOSE_DEBUG=1
export XDG_CACHE_HOME="$HOME/.cache"

uv run must-tui
```

## Example: add to your shell profile

Add these lines to `~/.zshrc` (or equivalent shell profile):

```bash
export MUST_LINK_BASE_URL="https://must.example.org"
export MUST_LINK_USERNAME="flight-user"
export XDG_CACHE_HOME="$HOME/.cache"
```

Then reload your shell:

```bash
source ~/.zshrc
```

Note: prefer entering secrets securely at runtime or through a secure secrets manager instead of storing passwords in shell profile files.

## Example: config-file fallback

If environment variables are not provided, create `~/.config/must-tui/config.json`:

```json
{
  "MUST_LINK_BASE_URL": "https://must.example.org",
  "MUST_LINK_USERNAME": "flight-user",
  "MUST_LINK_PASSWORD": "change-me"
}
```

## Example: custom cache location

```bash
export XDG_CACHE_HOME="$HOME/work-cache"
uv run must-tui
```

With this configuration, cache will be stored at:

```text
$HOME/work-cache/must-tui/parameters.sqlite3
```

## Quick checks

Verify variables in the current shell:

```bash
env | grep -E 'MUST_LINK|VERBOSE_DEBUG|XDG_CACHE_HOME'
```

Check where the cache file should be:

```bash
echo "${XDG_CACHE_HOME:-$HOME/.cache}/must-tui/parameters.sqlite3"
```
