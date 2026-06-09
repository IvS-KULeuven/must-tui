import argparse
from pathlib import Path

from .must_app import MUSTApp

from textual import log


def main():
    parser = argparse.ArgumentParser(description="MUST TUI — terminal interface for MUST link.")
    parser.add_argument(
        "--config",
        metavar="PATH",
        type=Path,
        default=None,
        help="Path to the config JSON file (default: ~/.config/must-tui/config.json).",
    )
    args = parser.parse_args()

    app = MUSTApp(config_file=args.config)

    app.log.info("Starting MUST TUI application...")
    app.run()
    app.log.info("MUST TUI application has stopped.")


if __name__ == "__main__":
    main()
