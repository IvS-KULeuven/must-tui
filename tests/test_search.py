import asyncio
import re

import pytest
from thefuzz import fuzz

import must_tui.must_app as must_app
from must_tui.must_app import match_options

FIELDS = [
    ("CNKA0960 [NC11_TOU_SENSE_1]", "CNKA0960", "NC11 N-FEE housekeeping parameter TOU sensor 1"),
    ("CNKA0966 [NC11_CCD1_TS]", "CNKA0966", "NC11 N-FEE housekeeping parameter CCD 1 temperature"),
    ("CNKA0970 [NC11_PRT1]", "CNKA0970", "NC11 N-FEE housekeeping parameter PRT 1"),
    ("CNAA0966 [NC01_CCD1_TS]", "CNAA0966", "NC01 N-FEE housekeeping parameter CCD 1 temperature"),
    ("DFAW0104 [FD1_TEMP_CCD1]", "DFAW0104", "FD1 F-CAM CCD1 temperature"),
    ("A98A0413 [TANK_TEMP_MEDIAN]", "A98A0413", "TM_152_TANK_TEMP_MEDIAN_152"),
    ("X0000001 [no description]", "X0000001", ""),
    ("X0000002 [no description]", "X0000002", ""),
]


def reference_scores(search, fields):
    """The scoring of the original per-option loop, using thefuzz."""

    return [max(fuzz.partial_ratio(search, field) for field in option) for option in fields]


def reference_fuzzy(search, fields):
    scores = reference_scores(search, fields)
    ranked = sorted(range(len(fields)), key=lambda idx: scores[idx], reverse=True)
    return [idx for idx in ranked[: must_app.MAX_FUZZY_MATCHES] if scores[idx] > must_app.FUZZY_MIN_SCORE]


def reference_jump(search, fields):
    scores = reference_scores(search, fields)
    return [max(range(len(fields)), key=scores.__getitem__)]


def test_regex_is_case_insensitive_matches_any_field_and_keeps_order():
    assert match_options("ccd1_ts", FIELDS, "regex") == [1, 3]
    assert match_options("^CNKA", FIELDS, "regex") == [0, 1, 2]  # anchors apply per field
    assert match_options("tank_temp_median_152$", FIELDS, "regex") == [5]  # description_2 only


def test_invalid_regex_raises():
    with pytest.raises(re.error):
        match_options("NC11_(", FIELDS, "regex")


@pytest.mark.parametrize("chunk_size", [1, 3, 4096])
@pytest.mark.parametrize("search", ["NC11_CCD1", "ccd1 temperature", "TEMP", "N-FEE", "zzz", "X000000"])
def test_fuzzy_and_jump_match_the_original_thefuzz_loop(monkeypatch, chunk_size, search):
    # Small chunks exercise the chunk offsets and, in jump mode, the cutoff carried from earlier chunks.
    monkeypatch.setattr(must_app, "SEARCH_CHUNK_SIZE", chunk_size)

    assert match_options(search, FIELDS, "fuzzy") == reference_fuzzy(search, FIELDS)
    assert match_options(search, FIELDS, "jump") == reference_jump(search, FIELDS)


def test_jump_picks_the_first_of_equal_best_scores(monkeypatch):
    monkeypatch.setattr(must_app, "SEARCH_CHUNK_SIZE", 1)
    fields = [("aaa", "", ""), ("NC11", "", ""), ("bbb", "", ""), ("NC11", "", "")]

    assert match_options("NC11", fields, "jump") == [1]


def test_fuzzy_limits_the_number_of_matches():
    fields = [(f"NC11_PAR_{i:04d}", "", "") for i in range(must_app.MAX_FUZZY_MATCHES + 50)]

    assert len(match_options("NC11_PAR", fields, "fuzzy")) == must_app.MAX_FUZZY_MATCHES


def test_empty_list_has_no_matches():
    assert match_options("NC11", [], "regex") == []
    assert match_options("NC11", [], "fuzzy") == []
    assert match_options("NC11", [], "jump") == []


class _FakeInput:
    def __init__(self, value):
        self.value = value


class _FakeOptionList:
    def __init__(self):
        self.options = None

    def set_options(self, options):
        self.options = options


def _app_with_controls(search):
    app = must_app.MUSTApp()
    app.options = [option[0] for option in FIELDS]
    app.search_fields = list(FIELDS)
    search_input, option_list = _FakeInput(search), _FakeOptionList()
    app._get_main_controls = lambda: (search_input, option_list)
    return app, search_input, option_list


def test_search_result_is_applied_when_nothing_changed():
    app, _, option_list = _app_with_controls("ccd1_ts")

    asyncio.run(app._search_options("ccd1_ts", "regex"))

    assert option_list.options == [FIELDS[1][0], FIELDS[3][0]]


def test_stale_search_result_is_dropped(monkeypatch):
    app, search_input, option_list = _app_with_controls("ccd1_ts")
    real_match_options = must_app.match_options

    def match_while_user_keeps_typing(search, fields, mode):
        search_input.value = "ccd1_tsx"
        return real_match_options(search, fields, mode)

    monkeypatch.setattr(must_app, "match_options", match_while_user_keeps_typing)

    asyncio.run(app._search_options("ccd1_ts", "regex"))

    assert option_list.options is None


def test_search_result_is_dropped_when_the_catalog_was_replaced(monkeypatch):
    app, _, option_list = _app_with_controls("ccd1_ts")
    real_match_options = must_app.match_options

    def match_while_catalog_refreshes(search, fields, mode):
        app.options = list(app.options)
        return real_match_options(search, fields, mode)

    monkeypatch.setattr(must_app, "match_options", match_while_catalog_refreshes)

    asyncio.run(app._search_options("ccd1_ts", "regex"))

    assert option_list.options is None


def test_invalid_regex_keeps_the_current_list():
    app, _, option_list = _app_with_controls("NC11_(")

    asyncio.run(app._search_options("NC11_(", "regex"))

    assert option_list.options is None
