import asyncio
import contextlib
import importlib.resources as resources
from importlib.resources.abc import Traversable
from pathlib import Path

import aiofiles

import rich
from egse.system import Timer
from egse.log import logger


async def read_pcf(path: Path | Traversable) -> dict[str, dict]:
    """Read the PCF (Parameter Characteristics File) and return its contents as a dictionary.

    Args:
        path (Path | Traversable): The path to the PCF file.

    Returns:
        A dictionary containing the following two dictionaries.
        - 'pcf': The contents of the PCF file as a dictionary.
        - 'mapping': Mapping of the parameter mnemonic to the MIB name.
    """

    with (contextlib.nullcontext(path) if isinstance(path, Path) else resources.as_file(path)) as resolved_path:
        async with aiofiles.open(resolved_path, "r") as file:
            lines = await file.readlines()

    pcf_dict = {}
    """Content of the pcf.dat file as a dictionary."""
    pcf_mapping = {}
    """Mapping of the parameter mnemonic to the MIB name"""

    for line in lines:
        (
            pcf_name,
            pcf_descr,
            pcf_pid,
            pcf_unit,
            pcf_ptc,
            pcf_pfc,
            pcf_width,
            pcf_valid,
            pcf_related,
            pcf_categ,
            pcf_natur,
            pcf_curtx,
            pcf_inter,
            pcf_uscon,
            pcf_decim,
            pcf_parval,
            pcf_subsys,
            pcf_valpar,
            pcf_sptype,
            pcf_corr,
            pcf_obtid,
            pcf_darc,
            pcf_endian,
            pcf_descr_2,
        ) = line.split("\t")

        pcf_dict[pcf_name] = {
            "description": pcf_descr,  # contains the parameter mnemonic
            "description_2": pcf_descr_2.rstrip(),  # Remove trailing newline
            "pid": pcf_pid,  # On-board ID of the telemetry parameter
            "unit": pcf_unit,  # Engineering unit mnemonic
            "ptc": pcf_ptc,  # Parameter Type Code
            "pcf": pcf_pfc,  # Parameter Format Code
            "width": pcf_width,  # Padded Width in bits
            "valid": pcf_valid,
            "related": pcf_related,
            "categ": pcf_categ,  # 'N' for numeric, 'S' for status, 'T' for text
            "natur": pcf_natur,  # 'R' for raw, 'D' for dynamic OL, 'H' for hardcoded, 'S' for save synthetic, 'C' for constant
            "curtx": pcf_curtx,
            "inter": pcf_inter,
            "uscon": pcf_uscon,
            "decim": pcf_decim,
            "parval": pcf_parval,
            "subsys": pcf_subsys,
            "valpar": pcf_valpar,
            "sptype": pcf_sptype,
            "corr": pcf_corr,
            "obtid": pcf_obtid,
            "darc": pcf_darc,
            "endian": pcf_endian,
        }

        pcf_mapping[pcf_descr] = pcf_name

    return {"pcf": pcf_dict, "mapping": pcf_mapping}


async def _main() -> None:
    from egse.env import setup_env
    from pcot import get_camera_setup
    from pcot.setup import load_setup

    setup_env()

    pcf_path = Path(__file__).parent.parent / "data" / "mib" / "pcf.dat"
    with Timer():
        pcf_content = await read_pcf(pcf_path)

    pcf = pcf_content["pcf"]
    name_mapping = pcf_content["mapping"]

    rich.print(f"PCF contains {len(pcf)} entries.")
    mib_name = name_mapping["NAEU1_CAM_N11_LCL 7_V"]
    rich.print(pcf[mib_name])

    setup = load_setup()
    camera_setup = get_camera_setup("Brigand", setup)
    rich.print(camera_setup)
    mib_name = camera_setup.names.mib_number

    rich.print(list(pcf.keys())[0:10])

    pars_brigand = [f"{x:28s} {name_mapping[x]}" for x in name_mapping if x.startswith(mib_name)]
    rich.print(f"Brigand has {len(pars_brigand)} parameters.")
    rich.print(pars_brigand[0:10])

    rich.print("Do this for all camera names!")


if __name__ == "__main__":
    asyncio.run(_main())
