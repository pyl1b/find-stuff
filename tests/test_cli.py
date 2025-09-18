from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from find_stuff.cli import cli
from tests.test_indexing import _git_available, _init_git_repo


@pytest.mark.skipif(
    not _git_available(), reason="git is required for this test"
)
def test_cli_rebuild_index_and_search_with_exts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(
        repo,
        files=[
            ("src/a.py", "alpha = 1\n"),
            ("docs/readme.md", "alpha beta\n"),
        ],
    )

    db = tmp_path / ".find_stuff" / "index.sqlite3"
    runner = CliRunner()

    # Build index for both .py and .md
    result_rebuild = runner.invoke(
        cli,
        [
            "rebuild-index",
            str(tmp_path),
            "--db",
            str(db),
            "--ext",
            "py",
            "--ext",
            "md",
        ],
    )
    assert result_rebuild.exit_code == 0, result_rebuild.output

    # Search with md-only filter
    result_search_md = runner.invoke(
        cli,
        [
            "search",
            "--db",
            str(db),
            "--ext",
            "md",
            "alpha",
        ],
    )
    assert result_search_md.exit_code == 0, result_search_md.output
    lines = [ln for ln in result_search_md.output.splitlines() if ln.strip()]
    # Should include readme.md and not a.py
    assert any("readme.md" in ln for ln in lines)
    assert all("a.py" not in ln for ln in lines)


@pytest.mark.skipif(
    not _git_available(), reason="git is required for this test"
)
def test_cli_search_flags_behavior(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(
        repo,
        files=[
            ("a.py", "foo = 1\nbar = foo\n"),
            ("b.py", "bar = 2\n"),
        ],
    )

    db = tmp_path / ".find_stuff" / "index.sqlite3"
    runner = CliRunner()
    res_rebuild = runner.invoke(
        cli,
        ["rebuild-index", str(tmp_path), "--db", str(db), "--ext", "py"],
    )
    assert res_rebuild.exit_code == 0, res_rebuild.output

    # --all (default) requires both terms; should only match a.py
    res_all = runner.invoke(cli, ["search", "--db", str(db), "foo", "bar"])
    assert res_all.exit_code == 0
    out_all = res_all.output
    assert "a.py" in out_all and "b.py" not in out_all

    # --any should allow either term; should include both files
    res_any = runner.invoke(
        cli, ["search", "--db", str(db), "--any", "foo", "bar"]
    )
    assert res_any.exit_code == 0
    out_any = res_any.output
    assert "a.py" in out_any and "b.py" in out_any


@pytest.mark.skipif(
    not _git_available(), reason="git is required for this test"
)
def test_cli_add_to_index_appends(tmp_path: Path) -> None:
    repo1 = tmp_path / "repo1"
    _init_git_repo(
        repo1,
        files=[
            ("a.py", "alpha = 1\n"),
        ],
    )

    db = tmp_path / ".find_stuff" / "index.sqlite3"
    runner = CliRunner()
    res_rebuild = runner.invoke(
        cli, ["rebuild-index", str(tmp_path), "--db", str(db), "--ext", "py"]
    )
    assert res_rebuild.exit_code == 0, res_rebuild.output

    # Create a second repo and add to index non-destructively
    repo2 = tmp_path / "repo2"
    _init_git_repo(
        repo2,
        files=[
            ("b.py", "beta = 2\n"),
        ],
    )

    res_add = runner.invoke(
        cli, ["add-to-index", str(tmp_path), "--db", str(db), "--ext", "py"]
    )
    assert res_add.exit_code == 0, res_add.output

    # Search should find both files
    res_search_alpha = runner.invoke(cli, ["search", "--db", str(db), "alpha"])
    res_search_beta = runner.invoke(cli, ["search", "--db", str(db), "beta"])
    assert "a.py" in res_search_alpha.output
    assert "b.py" in res_search_beta.output


@pytest.mark.skipif(
    not _git_available(), reason="git is required for this test"
)
def test_cli_file_info_reports_status(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(
        repo,
        files=[
            ("a.py", "alpha = 1\n"),
        ],
    )

    db = tmp_path / ".find_stuff" / "index.sqlite3"
    runner = CliRunner()

    res_rebuild = runner.invoke(
        cli, ["rebuild-index", str(tmp_path), "--db", str(db), "--ext", "py"]
    )
    assert res_rebuild.exit_code == 0, res_rebuild.output

    fpath = repo / "a.py"

    # Unchanged
    res_info_unchanged = runner.invoke(
        cli, ["file-info", "--db", str(db), str(fpath)]
    )
    assert res_info_unchanged.exit_code == 0
    assert "Status: unchanged" in res_info_unchanged.output

    # Modify file content to change hash and mtime
    fpath.write_text("alpha = 2\n", encoding="utf-8")

    res_info_changed = runner.invoke(
        cli, ["file-info", "--db", str(db), str(fpath)]
    )
    assert res_info_changed.exit_code == 0
    assert "Status: modified" in res_info_changed.output
