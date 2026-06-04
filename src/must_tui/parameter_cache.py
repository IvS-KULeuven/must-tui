from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any


def get_parameter_cache_db_path(db_path: str | Path | None = None) -> Path:
    """Return the SQLite path used for the persistent parameter cache."""

    if db_path is not None:
        return Path(db_path).expanduser()

    cache_root = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache")) / "must-tui"
    return cache_root / "parameters.sqlite3"


def initialize_parameter_cache(db_path: str | Path | None = None) -> Path:
    """Create the SQLite cache and schema when needed."""

    cache_path = get_parameter_cache_db_path(db_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(cache_path) as connection:
        connection.execute(
            """
            create table if not exists parameters (
                provider text not null,
                name text not null,
                description text not null default '',
                metadata_json text not null,
                updated_at text not null,
                primary key (provider, name)
            )
            """
        )
        connection.execute(
            "create index if not exists idx_parameters_provider_description on parameters(provider, description)"
        )
        connection.execute("create index if not exists idx_parameters_provider_name on parameters(provider, name)")

    return cache_path


def store_parameter_cache_rows(
    rows: list[dict[str, Any]],
    db_path: str | Path | None = None,
) -> None:
    """Persist parameter metadata rows in the SQLite cache."""

    cache_path = initialize_parameter_cache(db_path)
    updated_at = datetime.now(timezone.utc).isoformat()

    records = []
    for row in rows:
        provider = row.get("provider")
        name = row.get("name")
        description = row.get("description", "")
        if not isinstance(provider, str) or not provider:
            continue
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(description, str):
            description = ""
        records.append((provider, name, description, json.dumps(row, sort_keys=True), updated_at))

    with sqlite3.connect(cache_path) as connection:
        connection.executemany(
            """
            insert into parameters(provider, name, description, metadata_json, updated_at)
            values (?, ?, ?, ?, ?)
            on conflict(provider, name) do update set
                description = excluded.description,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            records,
        )


def load_parameter_cache_rows(
    data_provider: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load cached parameter metadata rows from SQLite."""

    cache_path = get_parameter_cache_db_path(db_path)
    if not cache_path.exists():
        return []

    with sqlite3.connect(cache_path) as connection:
        cursor = connection.cursor()
        if data_provider is None:
            cursor.execute("select metadata_json from parameters order by provider, description, name")
        else:
            cursor.execute(
                "select metadata_json from parameters where provider = ? order by description, name",
                (data_provider,),
            )
        return [json.loads(metadata_json) for (metadata_json,) in cursor.fetchall()]


def reset_parameter_cache_db(
    data_provider: str | None = None,
    db_path: str | Path | None = None,
) -> None:
    """Reset cached parameter metadata for one provider or for the whole cache."""

    cache_path = get_parameter_cache_db_path(db_path)
    if not cache_path.exists():
        return

    with sqlite3.connect(cache_path) as connection:
        if data_provider is None:
            connection.execute("delete from parameters")
        else:
            connection.execute("delete from parameters where provider = ?", (data_provider,))
