from .must_app import MUSTApp

from textual import log


def main():
    app = MUSTApp()

    app.log.info("Starting MUST TUI application...")
    app.run()
    app.log.info("MUST TUI application has stopped.")


if __name__ == "__main__":
    main()
