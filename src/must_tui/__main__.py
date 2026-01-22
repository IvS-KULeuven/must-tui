from .must_app import MUSTApp

from egse.log import egse_logger


def main():
    egse_logger.info("Starting MUST TUI application...")

    app = MUSTApp()
    app.run()

    egse_logger.info("MUST TUI application has stopped.")


if __name__ == "__main__":
    main()
