import asyncio
import importlib.resources

from .mib import read_pcf
from .must_app import MUSTApp
from .must import get_all_data_providers, login


async def main():
    pcf_path = importlib.resources.files("must_tui").joinpath("data/mib/pcf.dat")
    pcf_content = await read_pcf(pcf_path)

    ctx = login()
    _ = get_all_data_providers(ctx)

    MUSTApp(ctx, pcf_content).run()


if __name__ == "__main__":
    asyncio.run(main())
