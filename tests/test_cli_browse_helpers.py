from __future__ import annotations

from pathlib import Path

from find_stuff.navigation import (
    DirEntry,
    FileEntry,
    RepoEntry,
    _strip_optional_quotes,
    resolve_dir_by_input,
    resolve_file_by_input,
    resolve_repo_by_input,
)


def test_strip_optional_quotes() -> None:
    assert _strip_optional_quotes('"abc"') == "abc"
    assert _strip_optional_quotes("'123'") == "123"
    assert _strip_optional_quotes("abc") == "abc"


def test_resolve_repo_by_input() -> None:
    items = [
        RepoEntry(1, "repo1", Path("/a/repo1")),
        RepoEntry(2, "repo2", Path("/b/repo2")),
    ]
    assert resolve_repo_by_input(items, "1") == items[0]
    assert resolve_repo_by_input(items, "repo2") == items[1]
    assert resolve_repo_by_input(items, str(Path("/a/repo1"))) == items[0]
    assert resolve_repo_by_input(items, '"repo2"') == items[1]


def test_resolve_dir_by_input() -> None:
    base = Path("/a")
    items = [
        DirEntry(1, "x", base / "x"),
        DirEntry(2, "y", base / "y"),
    ]
    assert resolve_dir_by_input(items, "2") == items[1]
    assert resolve_dir_by_input(items, "x") == items[0]
    assert resolve_dir_by_input(items, str(base / "y")) == items[1]


def test_resolve_file_by_input() -> None:
    items = [
        FileEntry(1, "a.py", Path("/a/a.py")),
        FileEntry(2, "123.txt", Path("/a/123.txt")),
    ]
    assert resolve_file_by_input(items, "1") == items[0]
    assert resolve_file_by_input(items, '"123.txt"') == items[1]
    assert resolve_file_by_input(items, str(Path("/a/a.py"))) == items[0]
