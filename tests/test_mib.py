import asyncio
import importlib.resources

from must_tui.mib import read_pcf
from must_tui.must_app import PARAMETER_INFO_FIELDS


def test_read_pcf_keys_match_parameter_info_fields():
    pcf_path = importlib.resources.files("must_tui").joinpath("data/mib/pcf.dat")
    pcf = asyncio.run(read_pcf(pcf_path))["pcf"]

    entry = next(iter(pcf.values()))

    assert "pfc" in entry
    assert "pcf" not in entry
    assert set(PARAMETER_INFO_FIELDS) <= set(entry)
