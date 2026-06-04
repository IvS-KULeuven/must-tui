# Release Notes and Publishing

This document contains the release-time commands for docs publishing.

## Publish Documentation

Publish MkDocs to the upstream GitHub Pages branch (not your fork):

```bash
uv run mkdocs gh-deploy --remote-name upstream --remote-branch gh-pages --force
```

Expected published URL:

- https://IvS-KULeuven.github.io/must-tui/

## Verify Remotes (Optional)

```bash
git remote -v
```

You should see:

- `origin` -> your fork
- `upstream` -> `IvS-KULeuven/must-tui`
