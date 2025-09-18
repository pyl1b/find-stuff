# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project adheres to Semantic Versioning.

## [Unreleased]

### Added

- Interactive `browse` CLI command to navigate repositories, directories and files.
  - Up/Down to navigate, Enter to select, `i` to type index/name/path (quotes supported),
    `b`/Backspace to go to parent, `c` to open in VS Code, `q` to quit.
  - Shows file metadata from the database and indicates whether a file changed
    (mtime/hash) compared to the index.
- New `find_stuff.navigation` module with helpers for listing and resolving items,
  and checking file status. Also includes optional VS Code integration via `code`.

### Changed

- Documentation updates for the new `browse` command in README.
