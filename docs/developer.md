# Developer Guide

This section contains the basic developer workflow.

## Local setup

```bash
git clone https://github.com/IvS-KULeuven/must-tui
cd must-tui
uv venv
source .venv/bin/activate
uv sync --extra docs
```

## Run the app

```bash
must-tui
```

## Build docs

```bash
uv run mkdocs serve
```

## Publish docs (local deploy)

Publish to GitHub Pages using the local MkDocs deploy command:

```bash
uv run mkdocs gh-deploy -r upstream -m "Update docs..."
```

This pushes the generated site to the `gh-pages` branch.

## Package docs dependencies only

```bash
uv sync --extra docs
```

## Environment settings

See the dedicated environment documentation in [Environment](env.md), including variable meanings, fallback behavior, and setup examples.
