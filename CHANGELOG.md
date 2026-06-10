# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.5] - 2026-06-10

### Added

- Optional `--config` command-line argument to specify a custom configuration file path.

### Fixed

- `ErrorDialog` enhanced with a `quit_on_ok` option to allow quitting the app on fatal errors.
- Added `clear_plot` action to `MUSTApp` for explicitly clearing the plot view.
- Improved descriptions for telemetry parameter metadata fields (`categ`, `natur`) in documentation.
- Clarified calibration category and nature-of-parameter descriptions in the usage guide.

### Documentation

- Enhanced documentation with environment variable configuration and a comprehensive usage guide.

## [0.3.4] - 2026-06-08

### Changed

- Refactored search functionality: improved description handling and fuzzy/regex matching in `MUSTApp`.

### Documentation

- Updated user guide to include async helper usage in Jupyter notebooks.

## [0.3.3] - 2026-06-05

### Fixed

- Corrected async handling in `must.py` to ensure sync wrappers behave correctly when called from both notebooks and scripts.

### Tests

- Added tests for async wrapper behavior in `must.py`.

## [0.3.2] - 2026-06-05

### Added

- Python REPL helper functions for parameter retrieval (e.g. for use in Jupyter notebooks).
- Documentation for REPL helpers and parameter access patterns.

## [0.3.1] - 2026-06-05

### Added

- `LoadingScreen` displayed during application initialization.
- Status update messages during the login and bootstrap process.

[0.3.5]: https://github.com/IvS-KULeuven/must-tui/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/IvS-KULeuven/must-tui/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/IvS-KULeuven/must-tui/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/IvS-KULeuven/must-tui/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/IvS-KULeuven/must-tui/compare/v0.3.0...v0.3.1
