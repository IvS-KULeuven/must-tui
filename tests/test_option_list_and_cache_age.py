import datetime as dt

import must_tui.must_app as must_app
from must_tui.must_app import MAX_VISIBLE_OPTIONS
from must_tui.must_app import MUSTApp
from must_tui.parameter_cache import get_parameter_cache_updated_at
from must_tui.parameter_cache import store_parameter_cache_rows


def test_visible_options_returns_all_matches_below_the_cap():
    matches = [f"P{i}" for i in range(MAX_VISIBLE_OPTIONS)]

    assert MUSTApp.visible_options(matches) == matches


def test_visible_options_caps_matches_and_adds_disabled_hint():
    matches = [f"P{i}" for i in range(MAX_VISIBLE_OPTIONS + 42)]

    visible = MUSTApp.visible_options(matches)

    assert visible[:-1] == matches[:MAX_VISIBLE_OPTIONS]
    hint = visible[-1]
    assert hint.disabled
    assert "42 more matches" in str(hint.prompt)


def test_cache_updated_at_is_none_without_cache(tmp_path):
    assert get_parameter_cache_updated_at(db_path=tmp_path / "missing.sqlite3") is None


def test_cache_updated_at_is_per_provider(tmp_path):
    db_path = tmp_path / "parameters.sqlite3"
    before = dt.datetime.now(dt.timezone.utc)
    store_parameter_cache_rows([{"provider": "PLATO", "name": "A", "description": "a"}], db_path=db_path)

    updated_at = get_parameter_cache_updated_at(data_provider="PLATO", db_path=db_path)

    assert updated_at is not None and updated_at >= before
    assert get_parameter_cache_updated_at(data_provider="OTHER", db_path=db_path) is None


def test_parameter_cache_is_stale(monkeypatch):
    app = MUSTApp()
    now = dt.datetime.now(dt.timezone.utc)

    for updated_at, expected in [
        (None, True),
        (now - dt.timedelta(hours=1), False),
        (now - must_app.PARAMETER_CACHE_MAX_AGE - dt.timedelta(minutes=1), True),
    ]:
        monkeypatch.setattr(must_app, "get_parameter_cache_updated_at", lambda data_provider, value=updated_at: value)
        assert app._parameter_cache_is_stale() is expected
