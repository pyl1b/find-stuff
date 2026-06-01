# Changelog


All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to Semantic
Versioning.


## [Unreleased]

## [0.1.5] - 2026-06-01

### Added

- In interactive `browse` CLI allow adding or refreshing a repository.
- Use Dependabot

### Changed

- In interactive `browse` CLI allow adding or refreshing a repository.
- Use Dependabot
- update readme
- add distlift

## [0.1.4]

### Added

- Interactive `browse` CLI command to navigate repositories, directories and files.
- Up/Down to navigate, Enter to select, `i` to type index/name/path (quotes supported), `b`/Backspace to go to parent, `c` to open in VS Code, `q` to quit.
- Shows file metadata from the database and indicates whether a file changed (mtime/hash) compared to the index.
- New `find_stuff.navigation` module with helpers for listing and resolving items, and checking file status. Also includes optional VS Code integration via `code`.
- Beginner-friendly install instructions in README.
- GitHub Actions workflow to run tests on each push and pull request.
- GitHub Actions workflow to publish to PyPI on GitHub release.

### Changed

- Documentation updates for the new `browse` command in README.

[0.1.5]: https://github.com/pyl1b/find-stuff/compare/v0.1.4...v0.1.5
[unreleased]: https://github.com/pyl1b/find-stuff/compare/v0.1.5...HEAD
