from dataclasses import dataclass, field
import datetime
import json
from pathlib import Path

import requests
from egse.log import logger

# from egse.system import title_to_kebab
import rich

start = "2025-11-25 12:00:00"
end = "2025-12-05 00:00:00"


# FIXME: use the code from egse.system when the new version of cgse-common is released.
def title_to_kebab(title_str: str) -> str:
    """Convert Title Case (each word capitalized) to kebab-case"""
    return title_str.replace(" ", "-").lower()


@dataclass
class MustContext:
    base_url: str = ""
    token: str = ""
    data_providers: list = field(default_factory=list)


def login(config_file: Path | None = None):
    """
    Login to MUST link with credentials from config.json.

    If the path of the config file is not provided, it defaults to ~/must_config.json.
    """
    context = MustContext()

    config_file = config_file or Path.home() / "must_config.json"

    with open(config_file) as config_fd:
        config = json.load(config_fd)
        context.base_url = config["base_url"]
        payload = {"username": config["username"], "password": config["password"]}
        header = {"content-type": "application/json"}

        try:
            response = requests.post(context.base_url + "/auth/login", data=json.dumps(payload), headers=header)
        except ConnectionError as exc:
            logger.error(f"ConnectionError: {exc}")
            return context

        if response.status_code == 200:
            context.token = response.json()["token"]
            logger.info("Logged in successfully to MUST link.")
        else:
            if "token" in config:
                context.token = config["token"]
                logger.info("Retrieved token from config file.")
            else:
                logger.error("Login failed")

    return context


def must_request(ctx: MustContext, path: str, mode: str = "GET", payload: dict | None = None):
    """
    Do a get or post request to the must link API.

    Args:
        ctx (MustContext): The MUST link context.
        path (str): The API path to request, relative to the base url of must link.
        mode (str): The request mode, either 'GET' or 'POST'.
        payload (dict, optional): The payload for POST requests.

    Returns:
        The response object from the requests library.
    """
    url = ctx.base_url + path
    logger.debug(f"{url=}")
    if ctx.base_url == "" or ctx.token == "":
        logger.warning("Not logged in to MUST link. Call the `login()` function first.")
        return

    header = {"Authorization": ctx.token, "content-type": "application/json"}
    logger.debug(f"{header=}")

    if mode == "GET":
        response: requests.Response = requests.get(ctx.base_url + path, headers=header)
    elif mode == "POST":
        response = requests.post(ctx.base_url + path, data=json.dumps(payload), headers=header)
    else:
        logger.error(f"Invalid mode: {mode}")
        return

    if response.status_code == 401:
        logger.error("Not authorized, attempt to log in again.")
        return
    elif response.status_code == 404:
        logger.error(f"ERROR: Page not found, is the base_url correct? (base_url={ctx.base_url})")
        return
    else:
        return response


def get_all_data_providers(ctx: MustContext) -> list[dict]:
    """Returns a list of all available data providers.

    Data providers are returned as a dictionary with the following keys:

    - name (str): The name of the data provider.
    - user (str): The user associated with the data provider.
    - description (str): A description of the data provider.
    - searchable (bool): Whether the data provider is searchable.

    Data providers are automatically added to the MustContext object.

    Args:
        ctx (MustContext): The MUST link context.
    Returns:
        A list of data providers as dictionaries.
    """
    response = must_request(ctx, "/dataproviders")

    logger.debug(response)
    if response is not None:
        ctx.data_providers = response.json()
        return ctx.data_providers
    else:
        return []


def search_parameter_metadata(
    ctx: MustContext,
    data_provider: str,
    search_str: str,
    search_keys: str = "name",
) -> list[dict]:
    """Search in the given search keys for parameter metadata from a data provider.

    Args:
        ctx (MustContext): The MUST link context.
        data_provider (str): The name of the data provider to search in.
        search_str (str): The search string, can be a limited regex.
        search_keys (str): The fields to search in, separated by commas.

    Returns:
        A list of dictionaries containing parameter metadata for all matches.
    """
    start = ""
    end = ""
    path = f"/dataproviders/{data_provider}/parameters/?mode=SIMPLE&search=true&key={search_keys}&value={search_str}&start={start}&end={end}"

    response = must_request(ctx, path)
    logger.debug(f"{response=}")

    if response is not None:
        return [{title_to_kebab(field): value for field, value in metadata.items()} for metadata in response.json()]
    else:
        return []


def get_parameter_metadata(ctx: MustContext, par_name: str) -> list[dict]:
    """Retrieve parameter metadata from a data provider within a specified time range.

    The parameter metadata consists of:

    - Description: parameter mnemonic
    - Data Type: one of UNSIGNED_SMALL_INT, ...
    - First Sample: 'YYYY-MM-DD HH:MM:SS'
    - Last Sample: 'YYYY-MM-DD HH:MM:SS
    - Subsystem: one of TM, ...
    - Id:
    - Unit:
    - Parameter Type:
    - Name: mib name
    - Provider: name of the data provider

    Args:
        ctx (MustContext): The MUST link context.
        par_name (str): The name of the parameter to retrieve metadata for.

    Returns:
        A dictionary containing parameter metadata.
    """

    result = []

    for data_provider in ctx.data_providers:
        logger.debug(f"Retrieving metadata for parameter {par_name} from data provider {data_provider}")
        path = f"/dataproviders/{data_provider['name']}/parameters?mode=SIMPLE&search=true&key=name&value={par_name}"

        response = must_request(ctx, path)
        logger.debug(f"{response=}")

        if response is not None:
            result.extend(
                [{title_to_kebab(field): value for field, value in metadata.items()} for metadata in response.json()]
            )

    return result


def get_parameter_data(
    ctx: MustContext, data_provider: str, par_name: str, start: str, end: str, paginated: bool = False
):
    """Retrieve parameter data for a parameter from a data provider within a specified time range.

    The dictionary has the following structure:
    - content: A list of dictionaries with the following keys:
        - monitoringChecks: None or a dictionary of monitoring checks
        - metadata: A list with parameter metadata as a dictionary: id, name, description, subsystem, dataType,type, complete, unit
        - data: A list of dictionaries with the following keys:
            - date (str): Timestamp in milliseconds since epoch
            - value (int): Raw value
            - calibratedValue (float): Calibrated value or 'N/A'
    - cursor: The cursor for pagination, empty string if no more data is available.

    Args:
        ctx (MustContext): The MUST link context.
        data_provider (str): The name of the data provider to retrieve data from.
        par_name (str): The name of the parameter to retrieve data for.
        start (str): The start time in 'YYYY-MM-DD HH:MM:SS' format.
        end (str): The end time in 'YYYY-MM-DD HH:MM:SS' format.
        paginated (bool): Whether to retrieve data in a paginated manner.

    Returns:
        A dictionary containing the parameter data.

    Example:

        {
            "content": [
                {
                    "monitoringChecks": {
                        "requiredSamples": 0,
                        "useCalibrated": true,
                        "checkInterpretation": "REAL",
                        "checkDefinitions": [
                            {
                                "position": 0,
                                "type": "SOFT",
                                "lowValue": "string",
                                "highValue": "string",
                                "checkParameter": "string",
                                "checkValue": 0
                            }
                        ]
                    },
                    "metadata": [
                        {
                            "key": "string",
                            "value": "string"
                        }
                    ],
                    "data": [
                        {
                            "date": "string",
                            "value": 0,
                            "calibratedValue": "string"
                        }
                    ]
                }
            ],
            "cursor": "string"
        }
    """
    cursor = ""
    count = 0
    page_count = 4

    while True:
        count += 1
        if count > page_count:
            break

        path = (
            f"/dataproviders/{data_provider}/parameters/data{'/paginated' if paginated else ''}?"
            f"key=name&values={par_name}&from={start}&to={end}&limit=100&cursor={cursor}"
        )

        response = must_request(ctx, path)
        logger.debug(f"{response=}")

        if response is not None:
            data = response.json()
            if paginated:
                cursor = data.get("cursor", "")
                logger.debug(f"{cursor=}")
                yield data.get("content", [])
                if cursor == "":
                    break
            else:
                yield data
                break
        else:
            break


def get_raw_data_with_timestamp(data: dict) -> tuple[list[datetime.datetime], list[float]]:
    """Extract raw data with timestamps from the parameter data dictionary.

    Args:
        data (dict): The parameter data dictionary as returned by get_parameter_data().

    Returns:
        A list of tuples containing datetime objects and raw integer values.
    """
    timestamps = []
    raw_values = []

    if isinstance(data, list):
        # If data is a list, assume it's the content list
        data = {"content": data}

    for content in data.get("content", []):
        for entry in content.get("data", []):
            timestamp = datetime.datetime.fromtimestamp(int(entry["date"]) / 1000)
            raw_value = entry["value"]
            timestamps.append(timestamp)
            raw_values.append(raw_value)

    return timestamps, raw_values


if __name__ == "__main__":
    ctx = login()
    if ctx:
        providers = get_all_data_providers(ctx)
        logger.debug(providers)
        if providers:
            for provider in providers:
                logger.info(
                    f"Provider: {provider['name']} (user: {provider['user']}, description: {provider.get('description', 'N/A')})"
                )
        else:
            logger.error("Failed to retrieve data providers.")

        metadata = get_parameter_metadata(ctx, r"CNAA0965")
        logger.info(f"Parameter metadata: {metadata}")

        metadata = search_parameter_metadata(ctx, "PLATO", r"CNAA095.*", "name,description")
        logger.info(f"Search results: {metadata}")

        for data in get_parameter_data(ctx, "PLATO", r"CNAA0965", start, end, paginated=False):
            # logger.debug(f"Data response: {data['content'][0]['data']}")
            rich.print(data)
            # data = data["content"][0]["data"]  # this is a list of dictionaries with (date, value, calibratedValue) keys
            # logger.info(
            #     f"Parameter data: \n{
            #         '\n'.join(
            #             [
            #                 f'{datetime.datetime.fromtimestamp(int(entry["date"]) / 1000)}, {entry["value"]}'
            #                 for entry in data
            #             ]
            #         )
            #     }"
            # )
            raw_data = get_raw_data_with_timestamp(data)
            logger.info(f"Raw data length: {len(raw_data[0])} timestamps, {len(raw_data[1])} values")
            logger.info(f"Raw data with timestamps: {raw_data}")
