from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import List, Tuple

import pytest

from find_stuff import indexing


def _git_available() -> bool:
    return shutil.which("git") is not None


def _run(cmd: List[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True, capture_output=True)


def _init_git_repo(repo_dir: Path, files: List[Tuple[str, str]]) -> None:
    """Create a git repo with given files and an initial commit.

    Args:
        repo_dir: Directory to initialize as a git repository.
        files: List of (relative_path, content) pairs to write and commit.
    """

    repo_dir.mkdir(parents=True, exist_ok=True)
    _run(["git", "init"], repo_dir)

    for rel, content in files:
        fpath = repo_dir / rel
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")

    _run(["git", "add", "-A"], repo_dir)
    # Configure identity locally to avoid relying on global config
    _run(["git", "config", "user.email", "test@example.com"], repo_dir)
    _run(["git", "config", "user.name", "Test User"], repo_dir)
    # Disable GPG signing to prevent interactive passphrase prompt in CI
    _run(["git", "config", "commit.gpgsign", "false"], repo_dir)
    _run(["git", "commit", "-m", "init"], repo_dir)


def test_find_git_repos_detects_git_dirs(tmp_path: Path) -> None:
    # Construct two mock repos by creating .git directories (no git needed)
    repo_a = tmp_path / "projects" / "a"
    repo_b = tmp_path / "projects" / "b" / "nested"
    (repo_a / ".git").mkdir(parents=True)
    (repo_b / ".git").mkdir(parents=True)

    found = indexing.find_git_repos(tmp_path)
    found_set = {p.resolve() for p in found}

    assert repo_a.resolve() in found_set
    assert repo_b.resolve() in found_set


@pytest.mark.skipif(
    not _git_available(), reason="git is required for this test"
)
def test_list_git_tracked_files_filters_extensions(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(
        repo,
        files=[
            ("a.py", "print('hello')\n"),
            ("b.txt", "not indexed\n"),
            ("sub/c.py", "x = 1\n"),
        ],
    )

    py_files = indexing.list_git_tracked_files(repo, ["py"])  # type: ignore[list-item]
    txt_files = indexing.list_git_tracked_files(repo, [".txt"])  # type: ignore[list-item]

    py_names = sorted(
        [str(p.relative_to(repo)).replace("\\", "/") for p in py_files]
    )
    txt_names = sorted(
        [str(p.relative_to(repo)).replace("\\", "/") for p in txt_files]
    )

    assert py_names == ["a.py", "sub/c.py"]
    assert txt_names == ["b.txt"]


def test_iter_token_postings_yields_positions(tmp_path: Path) -> None:
    f = tmp_path / "sample.py"
    f.write_text(
        (
            """
def foo_bar(x, y):
    return x + y  # add
            """.strip()
            + "\n"
        ),
        encoding="utf-8",
    )

    tokens = list(indexing._iter_token_postings(f))
    token_set = {t.token for t in tokens}

    assert {"def", "foo_bar", "x", "y", "return"}.issubset(token_set)
    # Ensure at least one token has a valid 1-based position
    assert all(t.line >= 1 and t.column >= 1 for t in tokens)


@pytest.mark.skipif(
    not _git_available(), reason="git is required for this test"
)
def test_rebuild_index_creates_db_and_tokens(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(
        repo,
        files=[
            ("app.py", "def hello():\n    return 1\n"),
            ("util.py", "VALUE = 42\n"),
        ],
    )

    db_path = tmp_path / "index.sqlite3"
    indexing.rebuild_index(tmp_path, db_path, file_types=("py",))

    assert db_path.exists()

    # Inspect a few tables exist
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cur.fetchall()}
        assert {"repositories", "files", "tokens", "postings"}.issubset(tables)
    finally:
        conn.close()


@pytest.mark.skipif(
    not _git_available(), reason="git is required for this test"
)
def test_add_to_index_appends_without_wiping(tmp_path: Path) -> None:
    repo1 = tmp_path / "repo1"
    _init_git_repo(
        repo1,
        files=[
            ("a.py", "alpha = 1\n"),
        ],
    )

    db_path = tmp_path / "index.sqlite3"
    indexing.rebuild_index(tmp_path, db_path, file_types=("py",))

    # Add a second repository later
    repo2 = tmp_path / "repo2"
    _init_git_repo(
        repo2,
        files=[
            ("b.py", "beta = 2\n"),
        ],
    )

    # Append without dropping existing data
    indexing.add_to_index(tmp_path, db_path, file_types=("py",))

    # Search should find symbols from both repos
    res_alpha = indexing.search_files(
        db_path, ["alpha"], require_all_terms=True
    )
    res_beta = indexing.search_files(db_path, ["beta"], require_all_terms=True)

    assert any(str(p).endswith("a.py") for p, _ in res_alpha)
    assert any(str(p).endswith("b.py") for p, _ in res_beta)

    # Calling add_to_index again should not duplicate repo1 or repo2
    indexing.add_to_index(tmp_path, db_path, file_types=("py",))
    # Ensure repository count remains 2
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM repositories")
        (count_repos,) = cur.fetchone()
        assert int(count_repos) == 2
    finally:
        conn.close()


# end


@pytest.mark.skipif(
    not _git_available(), reason="git is required for this test"
)
def test_search_files_basic_and_limit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(
        repo,
        files=[
            ("a.py", "foo = 1\nbar = foo\n"),
            ("b.py", "bar = 2\n"),
            ("c.py", "baz = 3\n"),
        ],
    )

    db_path = tmp_path / "index.sqlite3"
    indexing.rebuild_index(tmp_path, db_path, file_types=("py",))

    # ALL terms: only files with both tokens
    results_all = indexing.search_files(
        db_path,
        ["foo", "bar"],
        require_all_terms=True,
    )
    assert any(str(p).endswith("a.py") for p, _ in results_all)
    assert all("b.py" not in str(p) for p, _ in results_all)

    # ANY term with limit
    results_any_limited = indexing.search_files(
        db_path,
        ["bar", "baz"],
        require_all_terms=False,
        limit=1,
    )
    assert len(results_any_limited) == 1


@pytest.mark.skipif(
    not _git_available(), reason="git is required for this test"
)
def test_search_files_regex_and_case_sensitivity(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(
        repo,
        files=[
            ("a.py", "ClassName = 1\nclassname = 2\n"),
        ],
    )

    db_path = tmp_path / "index.sqlite3"
    indexing.rebuild_index(tmp_path, db_path, file_types=("py",))

    # Case-insensitive regex should match both
    results_ci = indexing.search_files(
        db_path,
        ["class"],
        regex=True,
        case_sensitive=False,
    )
    assert results_ci, "Expected at least one match"

    # Case-sensitive exact should only match exact token
    results_cs = indexing.search_files(
        db_path,
        ["ClassName"],
        regex=False,
        case_sensitive=True,
    )
    assert any(str(p).endswith("a.py") for p, _ in results_cs)


@pytest.mark.skipif(
    not _git_available(), reason="git is required for this test"
)
def test_search_files_file_types_filter(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(
        repo,
        files=[
            ("a.py", "token = 1\n"),
            ("note.md", "token token\n"),
        ],
    )

    db_path = tmp_path / "index.sqlite3"
    # Index both py and md so we can filter at search time
    indexing.rebuild_index(tmp_path, db_path, file_types=("py", "md"))

    # Searching for "token" across both should return both files when no filter
    results = indexing.search_files(db_path, ["token"], require_all_terms=True)
    paths = {Path(p).name for p, _ in results}
    assert {"a.py", "note.md"}.issubset(paths)

    # Now restrict to md only
    results_md = indexing.search_files(
        db_path,
        ["token"],
        require_all_terms=True,
        file_types=["md"],
    )
    names_md = {Path(p).name for p, _ in results_md}
    assert names_md == {"note.md"}


def test_search_files_no_terms_returns_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite3"
    # Create an empty database to ensure function handles gracefully
    sqlite3.connect(str(db_path)).close()
    assert indexing.search_files(db_path, []) == []


@pytest.mark.skipif(
    not _git_available(), reason="git is required for this test"
)
def test__matching_token_ids_exact_and_regex(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(
        repo,
        files=[
            ("a.py", "Alpha = 1\n"),
            ("b.py", "beta = 2\n"),
        ],
    )

    db_path = tmp_path / "index.sqlite3"
    indexing.rebuild_index(tmp_path, db_path, file_types=("py",))

    conn = sqlite3.connect(str(db_path))
    try:
        # Exact case-insensitive should match both 'Alpha' and 'alpha' tokens
        ids_ci = indexing._matching_token_ids(
            conn, term="alpha", regex=False, case_sensitive=False
        )
        # Regex case-sensitive should only match capitalized 'Alpha'
        ids_cs_regex = indexing._matching_token_ids(
            conn, term=r"^Alpha$", regex=True, case_sensitive=True
        )

        assert isinstance(ids_ci, list) and all(
            isinstance(i, int) for i in ids_ci
        )
        assert isinstance(ids_cs_regex, list)
        # The regex case-sensitive set should be subset of the case-insensitive
        assert set(ids_cs_regex).issubset(set(ids_ci))
    finally:
        conn.close()


@pytest.mark.skipif(
    not _git_available(), reason="git is required for this test"
)
def test_metadata_recorded_for_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(
        repo,
        files=[
            ("a.py", "alpha = 1\n"),
        ],
    )

    db_path = tmp_path / "index.sqlite3"
    indexing.rebuild_index(tmp_path, db_path, file_types=("py",))

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT size_bytes, mtime_ns, ctime_ns, sha256_hex FROM files"
        )
        row = cur.fetchone()
        assert row is not None
        size_b, mt_ns, ct_ns, digest = row
        assert int(size_b) >= 0
        assert int(mt_ns) > 0
        assert int(ct_ns) > 0
        assert isinstance(digest, str)
        assert len(digest) in (0, 64)
    finally:
        conn.close()
