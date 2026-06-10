"""
Unit tests for REPL helper functions in must.py.

What is covered

Wrapper behavior:
- get_parameter_names uses asyncio.run
- get_parameter_series uses asyncio.run

get_parameter_names_async:
- Cache-based matching works (case-insensitive regex)
- Invalid regex falls back to literal search
- Also searches PCF description_2 field from the bundled MIB
- Falls back to MIB description when MUST description is empty or equals the parameter name
- Non-cache path deduplicates parameter names across providers
- Result ordering is sorted by parameter name

get_parameter_series_async:
- Uses metadata first/last sample bounds when start/end are omitted
- Normalizes explicit datetime/string bounds
- Filters invalid parameter names in list input
- Aggregates paginated sample chunks
- Returns empty dict when context is not authenticated
"""

import asyncio
import datetime as dt
import inspect

import must_tui.must as must


def test_get_parameter_names_wrapper_uses_asyncio_run(monkeypatch):
    def fake_run(coro):
        assert inspect.iscoroutine(coro)
        coro.close()
        return {"A": "desc"}

    monkeypatch.setattr(must.asyncio, "run", fake_run)

    result = must.get_parameter_names("A.*")

    assert result == {"A": "desc"}


def test_get_parameter_names_wrapper_works_inside_running_loop(monkeypatch):
    async def fake_get_parameter_names_async(name_pattern, data_provider=None, use_cache=True):
        return {"INSIDE_LOOP": "ok"}

    monkeypatch.setattr(must, "get_parameter_names_async", fake_get_parameter_names_async)

    async def invoke_wrapper_inside_loop():
        return must.get_parameter_names("anything")

    result = asyncio.run(invoke_wrapper_inside_loop())

    assert result == {"INSIDE_LOOP": "ok"}


def test_get_parameter_series_wrapper_uses_asyncio_run(monkeypatch):
    def fake_run(coro):
        assert inspect.iscoroutine(coro)
        coro.close()
        return {"P": []}

    monkeypatch.setattr(must.asyncio, "run", fake_run)

    result = must.get_parameter_series("P", "PROVIDER")

    assert result == {"P": []}


def test_get_parameter_names_async_uses_cache_with_regex_and_literal_fallback(monkeypatch):
    async def fake_cache_loader(data_provider=None, ctx=None, force_refresh=False):
        return {
            "TEMP_A": "Primary temperature",
            "VOLT_1": "Voltage rail",
            "FLAG[a-": "Contains literal bracket sequence",
        }

    async def fake_pcf_loader():
        return {}

    monkeypatch.setattr(must, "load_parameter_cache_async", fake_cache_loader)
    monkeypatch.setattr(must, "_load_pcf_async", fake_pcf_loader)

    regex_result = asyncio.run(must.get_parameter_names_async("temp", use_cache=True))
    literal_result = asyncio.run(must.get_parameter_names_async("[a-", use_cache=True))

    assert regex_result == {"TEMP_A": "Primary temperature"}
    assert literal_result == {"FLAG[a-": "Contains literal bracket sequence"}


def test_get_parameter_names_async_searches_description_2(monkeypatch):
    async def fake_cache_loader(data_provider=None, ctx=None, force_refresh=False):
        return {
            "MIB_PAR_001": "no description",
            "MIB_PAR_002": "Voltage monitor",
        }

    async def fake_pcf_loader():
        return {
            "MIB_PAR_001": {"description": "short desc", "description_2": "Camera focus actuator position"},
        }

    monkeypatch.setattr(must, "load_parameter_cache_async", fake_cache_loader)
    monkeypatch.setattr(must, "_load_pcf_async", fake_pcf_loader)

    result = asyncio.run(must.get_parameter_names_async("focus actuator", use_cache=True))

    assert "MIB_PAR_001" in result
    assert "MIB_PAR_002" not in result


def test_get_parameter_names_async_falls_back_to_mib_description(monkeypatch):
    """When MUST description is empty or equals the parameter name, MIB description is returned."""

    async def fake_cache_loader(data_provider=None, ctx=None, force_refresh=False):
        return {
            "MIB_PAR_EMPTY": "",           # MUST description empty
            "MIB_PAR_SAME": "MIB_PAR_SAME",  # MUST description equals name
            "MIB_PAR_OK": "Good MUST desc",  # MUST description is fine
        }

    async def fake_pcf_loader():
        return {
            "MIB_PAR_EMPTY": {"description": "PCF short", "description_2": "PCF long desc empty"},
            "MIB_PAR_SAME":  {"description": "PCF short same", "description_2": "PCF long desc same"},
            "MIB_PAR_OK":    {"description": "PCF ignored", "description_2": "PCF also ignored"},
        }

    monkeypatch.setattr(must, "load_parameter_cache_async", fake_cache_loader)
    monkeypatch.setattr(must, "_load_pcf_async", fake_pcf_loader)

    result = asyncio.run(must.get_parameter_names_async("MIB_PAR", use_cache=True))

    assert result["MIB_PAR_EMPTY"] == "PCF short"
    assert result["MIB_PAR_SAME"] == "PCF short same"
    assert result["MIB_PAR_OK"] == "Good MUST desc"


def test_get_parameter_names_async_without_cache_deduplicates_and_sorts(monkeypatch):
    ctx = must.MustContext(authenticated=True, data_providers=[])

    async def fake_login():
        return ctx

    async def fake_get_all_data_providers(current_ctx):
        current_ctx.data_providers = [{"name": "B"}, {"name": "A"}]
        return current_ctx.data_providers

    async def fake_search_parameter_metadata(current_ctx, provider_name, name_pattern, search_keys):
        if provider_name == "B":
            return [
                {"name": "PAR_B", "description": "from-b"},
                {"name": "PAR_DUP", "description": "first"},
            ]
        return [
            {"name": "PAR_A", "description": "from-a"},
            {"name": "PAR_DUP", "description": "second"},
        ]

    monkeypatch.setattr(must, "login", fake_login)
    monkeypatch.setattr(must, "get_all_data_providers", fake_get_all_data_providers)
    monkeypatch.setattr(must, "search_parameter_metadata", fake_search_parameter_metadata)

    result = asyncio.run(must.get_parameter_names_async("PAR", use_cache=False))

    assert list(result.keys()) == ["PAR_A", "PAR_B", "PAR_DUP"]
    assert result["PAR_DUP"] == "first"


def test_get_parameter_series_async_uses_metadata_bounds_and_collects_samples(monkeypatch):
    ctx = must.MustContext(authenticated=True)
    calls = []

    base_ts = dt.datetime(2025, 1, 1, 12, 0, 0)

    async def fake_search_parameter_metadata(current_ctx, provider_name, par_name, search_keys):
        return [{"name": par_name, "first-sample": "2025-01-01 00:00:00", "last-sample": "2025-01-02 00:00:00"}]

    async def fake_get_parameter_data(current_ctx, provider_name, par_name, start, end, paginated=False):
        calls.append((provider_name, par_name, start, end, paginated))
        yield {"timestamps": [base_ts], "values": [1]}
        yield {"timestamps": [base_ts + dt.timedelta(seconds=1)], "values": [2]}

    def fake_get_raw_data_with_timestamp(chunk):
        return chunk["timestamps"], chunk["values"]

    monkeypatch.setattr(must, "search_parameter_metadata", fake_search_parameter_metadata)
    monkeypatch.setattr(must, "get_parameter_data", fake_get_parameter_data)
    monkeypatch.setattr(must, "get_raw_data_with_timestamp", fake_get_raw_data_with_timestamp)

    result = asyncio.run(must.get_parameter_series_async(["P1", "P2"], "PROVIDER", ctx=ctx))

    assert set(result.keys()) == {"P1", "P2"}
    assert result["P1"] == [(base_ts, 1), (base_ts + dt.timedelta(seconds=1), 2)]
    assert all(call[2] == "2025-01-01 00:00:00" for call in calls)
    assert all(call[3] == "2025-01-02 00:00:00" for call in calls)
    assert all(call[4] is True for call in calls)


def test_get_parameter_series_async_normalizes_names_and_explicit_time_range(monkeypatch):
    ctx = must.MustContext(authenticated=True)
    calls = []

    explicit_start = dt.datetime(2025, 2, 1, 10, 30, 0)
    explicit_end = "2025-02-01 11:00:00"

    async def fake_search_parameter_metadata(current_ctx, provider_name, par_name, search_keys):
        return [{"name": par_name, "first-sample": "IGNORED", "last-sample": "IGNORED"}]

    async def fake_get_parameter_data(current_ctx, provider_name, par_name, start, end, paginated=False):
        calls.append((par_name, start, end))
        yield {"timestamps": [], "values": []}

    def fake_get_raw_data_with_timestamp(chunk):
        return chunk["timestamps"], chunk["values"]

    monkeypatch.setattr(must, "search_parameter_metadata", fake_search_parameter_metadata)
    monkeypatch.setattr(must, "get_parameter_data", fake_get_parameter_data)
    monkeypatch.setattr(must, "get_raw_data_with_timestamp", fake_get_raw_data_with_timestamp)

    result = asyncio.run(
        must.get_parameter_series_async(
            ["P1", "", None, 5, "P2"],
            "PROVIDER",
            start=explicit_start,
            end=explicit_end,
            ctx=ctx,
        )
    )

    assert list(result.keys()) == ["P1", "P2"]
    assert calls == [
        ("P1", "2025-02-01 10:30:00", "2025-02-01 11:00:00"),
        ("P2", "2025-02-01 10:30:00", "2025-02-01 11:00:00"),
    ]


def test_get_parameter_series_async_returns_empty_when_not_authenticated():
    ctx = must.MustContext(authenticated=False)

    result = asyncio.run(must.get_parameter_series_async("P1", "PROVIDER", ctx=ctx))

    assert result == {}
