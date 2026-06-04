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

## Package docs dependencies only

```bash
uv sync --extra docs
```
