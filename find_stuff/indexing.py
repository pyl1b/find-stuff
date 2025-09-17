"""Indexing and searching for Python code in git-controlled repositories.

This module provides functionality to:

- Discover git repositories under a root directory.
- Enumerate only ``.py`` files tracked by git.
- Build an SQLite inverted index mapping tokens to file locations.
- Search for files containing given terms (exact or regex), with support for
  logical ALL/ANY matching and a result limit.

Designed for use both as a library and via the CLI.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Sequence, Tuple


_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class Posting:
    """Represents a single token occurrence in a file.

    Attributes:
        file_path: Absolute path to the file.
        token: Token string as extracted from source code.
        line: 1-based line number where the token occurs.
        column: 1-based column number (start position) of the token.
    """

    file_path: Path
    token: str
    line: int
    column: int


def find_git_repos(start: Path) -> List[Path]:
    """Recursively discover git repository roots under a starting directory.

    Args:
        start: Directory to scan.

    Returns:
        A list of repository root paths that contain a ``.git`` directory or
        pointer file.
    """

    repos: List[Path] = []

    for root, dirnames, _filenames in os.walk(start):
        root_path = Path(root)

        # Detect a Git repository: either a .git directory or a .git file
        # (as used by submodules/worktrees) in the current directory.
        git_dir = root_path / ".git"
        if git_dir.exists():
            repos.append(root_path)

            # Do not descend into subdirectories of a repository.
            # Nested repos will be discovered separately when the walk
            # reaches them as roots.
            dirnames[:] = []
            continue

    return repos


def _git_tracked_files(repo_root: Path) -> List[Path]:
    """List files tracked by git in a repository.

    Args:
        repo_root: The repository root path.

    Returns:
        List of absolute file paths tracked by git.
    """

    # Use `-z` to avoid path issues and simplify splitting.
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
    )
    relpaths = [
        p for p in result.stdout.decode("utf-8", "ignore").split("\x00") if p
    ]
    return [repo_root / rel for rel in relpaths]


def list_git_tracked_python_files(repo_root: Path) -> List[Path]:
    """Enumerate ``.py`` files tracked by git in a repository.

    Args:
        repo_root: The repository root path.

    Returns:
        List of absolute paths to tracked Python files.
    """

    candidates = _git_tracked_files(repo_root)
    return [p for p in candidates if p.suffix == ".py"]


def _iter_token_postings(file_path: Path) -> Iterator[Posting]:
    """Yield token postings for a Python file.

    Args:
        file_path: Absolute file path to read and tokenize.

    Yields:
        Posting entries for each token found in the file.
    """

    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return

    for line_idx, line in enumerate(text.splitlines(), start=1):
        for match in _WORD_RE.finditer(line):
            column = match.start() + 1
            token = match.group(0)
            yield Posting(
                file_path=file_path,
                token=token,
                line=line_idx,
                column=column,
            )


def _db_init(conn: sqlite3.Connection) -> None:
    """Create database schema (drop existing tables)."""

    cur = conn.cursor()
    cur.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;

        DROP TABLE IF EXISTS postings;
        DROP TABLE IF EXISTS tokens;
        DROP TABLE IF EXISTS files;
        DROP TABLE IF EXISTS repositories;
        DROP TABLE IF EXISTS metadata;

        CREATE TABLE repositories (
            id INTEGER PRIMARY KEY,
            root TEXT NOT NULL UNIQUE
        );

        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            repo_id INTEGER NOT NULL REFERENCES repositories(id)
                ON DELETE CASCADE,
            relpath TEXT NOT NULL,
            abspath TEXT NOT NULL,
            UNIQUE(repo_id, relpath)
        );

        CREATE TABLE tokens (
            id INTEGER PRIMARY KEY,
            token TEXT NOT NULL UNIQUE,
            token_lc TEXT NOT NULL
        );

        CREATE TABLE postings (
            file_id INTEGER NOT NULL REFERENCES files(id)
                ON DELETE CASCADE,
            token_id INTEGER NOT NULL REFERENCES tokens(id)
                ON DELETE CASCADE,
            line INTEGER NOT NULL,
            col INTEGER NOT NULL,
            PRIMARY KEY (file_id, token_id, line, col)
        );

        CREATE INDEX idx_tokens_token ON tokens(token);
        CREATE INDEX idx_tokens_token_lc ON tokens(token_lc);
        CREATE INDEX idx_postings_token ON postings(token_id);
        CREATE INDEX idx_postings_file ON postings(file_id);

        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    conn.commit()


def rebuild_index(root: Path, db_path: Path) -> None:
    """Rebuild the index for all git repositories under a root directory.

    This clears and recreates the SQLite database at ``db_path``.

    Args:
        root: Root directory to scan recursively for repositories.
        db_path: Path to the SQLite database to (re)build.
    """

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        _db_init(conn)
        cur = conn.cursor()

        repos = find_git_repos(root)
        for repo_root in repos:
            # Insert repository
            cur.execute(
                "INSERT INTO repositories(root) VALUES (?)",
                (str(repo_root),),
            )
            repo_id = cur.lastrowid

            # Enumerate files and index tokens
            py_files = list_git_tracked_python_files(repo_root)
            for fpath in py_files:
                relpath = os.path.relpath(fpath, repo_root)
                cur.execute(
                    (
                        "INSERT INTO files(repo_id, relpath, abspath) "
                        "VALUES (?, ?, ?)"
                    ),
                    (repo_id, relpath, str(fpath)),
                )
                file_id = cur.lastrowid

                # Collect tokens for the file
                token_counts: Dict[str, int] = {}
                postings: List[Tuple[int, int, int]] = []

                # Stage tokens (string) to later map to ids
                tokens_in_file: List[Tuple[str, int, int]] = []
                for post in _iter_token_postings(fpath):
                    tokens_in_file.append(
                        (post.token, post.line, post.column)
                    )
                    token_counts[post.token] = (
                        token_counts.get(post.token, 0) + 1
                    )

                if not tokens_in_file:
                    continue

                # Ensure tokens exist in tokens table
                unique_tokens = sorted({t for t, _l, _c in tokens_in_file})
                cur.executemany(
                    "INSERT OR IGNORE INTO tokens(token, token_lc) "
                    "VALUES(?, ?)",
                    [(tok, tok.lower()) for tok in unique_tokens],
                )

                # Build mapping token -> id
                cur.execute(
                    (
                        "SELECT id, token FROM tokens WHERE token IN ("
                        + ",".join(["?"] * len(unique_tokens))
                        + ")"
                    ),
                    unique_tokens,
                )
                token_to_id = {
                    row[1]: int(row[0]) for row in cur.fetchall()
                }

                # Prepare postings rows
                for tok, line, col in tokens_in_file:
                    tok_id = token_to_id[tok]
                    postings.append((tok_id, line, col))

                cur.executemany(
                    (
                        "INSERT OR IGNORE INTO postings("
                        "file_id, token_id, line, col) VALUES (?, ?, ?, ?)"
                    ),
                    [
                        (file_id, tok_id, line, col)
                        for (tok_id, line, col) in postings
                    ],
                )

            conn.commit()
    finally:
        conn.close()


def _matching_token_ids(
    conn: sqlite3.Connection,
    term: str,
    regex: bool,
    case_sensitive: bool,
) -> List[int]:
    """Find token IDs that match a term.

    Args:
        conn: Open SQLite connection.
        term: The term or regex pattern to match.
        regex: If True, treat ``term`` as a regular expression.
        case_sensitive: If False, match case-insensitively.

    Returns:
        List of token ids.
    """

    cur = conn.cursor()
    if not regex:
        if case_sensitive:
            cur.execute("SELECT id FROM tokens WHERE token = ?", (term,))
        else:
            cur.execute(
                "SELECT id FROM tokens WHERE token_lc = ?",
                (term.lower(),),
            )
        return [int(r[0]) for r in cur.fetchall()]

    # Regex path: fetch tokens (optionally lowercased) and filter in Python.
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(term, flags)
    # Use a reasonable approach: fetch tokens and filter in Python. This avoids
    # SQLite user-defined regex functions and keeps logic simple.
    cur.execute("SELECT id, token, token_lc FROM tokens")
    matched: List[int] = []
    for tok_id, token, token_lc in cur.fetchall():
        target = token if case_sensitive else token_lc
        if pattern.search(target) is not None:
            matched.append(int(tok_id))
    return matched


def search_files(
    db_path: Path,
    terms: Sequence[str],
    *,
    limit: int = 50,
    require_all_terms: bool = True,
    regex: bool = False,
    case_sensitive: bool = False,
) -> List[Tuple[Path, int]]:
    """Search for files containing specified terms.

    Args:
        db_path: Path to the SQLite index database.
        terms: One or more search terms. May be regex if ``regex`` is True.
        limit: Maximum number of files to return.
        require_all_terms: If True, a file must contain all terms;
            otherwise any.
        regex: Treat terms as regular expressions.
        case_sensitive: Use case-sensitive matching.

    Returns:
        List of tuples ``(file_path, score)`` where score is the number of
        matched postings in the file, ordered descending by score.
    """

    if not terms:
        return []

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()

        # Resolve matching token ids for each term
        term_token_ids: List[List[int]] = [
            _matching_token_ids(
                conn,
                term,
                regex=regex,
                case_sensitive=case_sensitive,
            )
            for term in terms
        ]

        # Early exit if any term has no matches when all are required
        if require_all_terms and any(len(ids) == 0 for ids in term_token_ids):
            return []

        # Build candidate files and counts
        file_to_count: Dict[int, int] = {}

        if require_all_terms:
            # Start with files for the first term
            first_ids = term_token_ids[0]
            if not first_ids:
                return []

            cur.execute(
                (
                    "SELECT DISTINCT file_id FROM postings WHERE token_id IN ("
                    + ",".join(["?"] * len(first_ids))
                    + ")"
                ),
                first_ids,
            )
            candidate_files = {int(r[0]) for r in cur.fetchall()}

            # Intersect with files for remaining terms
            for ids in term_token_ids[1:]:
                if not ids:
                    return []
                cur.execute(
                    (
                        "SELECT DISTINCT file_id FROM postings WHERE "
                        "token_id IN ("
                        + ",".join(["?"] * len(ids))
                        + ")"
                    ),
                    ids,
                )
                these_files = {int(r[0]) for r in cur.fetchall()}
                candidate_files &= these_files
                if not candidate_files:
                    return []

            if not candidate_files:
                return []

            # Count postings for all matching tokens within candidate files
            all_ids = sorted({tid for ids in term_token_ids for tid in ids})
            cur.execute(
                (
                    "SELECT file_id, COUNT(*) FROM postings WHERE "
                    "file_id IN ("
                    + ",".join(["?"] * len(candidate_files))
                    + ") AND token_id IN ("
                    + ",".join(["?"] * len(all_ids))
                    + ") GROUP BY file_id"
                ),
                [*candidate_files, *all_ids],
            )
            for file_id, count in cur.fetchall():
                file_to_count[int(file_id)] = int(count)
        else:
            # Any term: union of files across all terms
            all_ids = sorted({tid for ids in term_token_ids for tid in ids})
            if not all_ids:
                return []
            cur.execute(
                (
                    "SELECT file_id, COUNT(*) FROM postings WHERE "
                    "token_id IN ("
                    + ",".join(["?"] * len(all_ids))
                    + ") GROUP BY file_id"
                ),
                all_ids,
            )
            for file_id, count in cur.fetchall():
                file_to_count[int(file_id)] = int(count)

        if not file_to_count:
            return []

        # Map file ids to absolute paths
        file_ids_sorted = [
            fid
            for fid, _ in sorted(
                file_to_count.items(), key=lambda kv: kv[1], reverse=True
            )
        ]
        if limit > 0:
            file_ids_sorted = file_ids_sorted[:limit]

        cur.execute(
            (
                "SELECT id, abspath FROM files WHERE id IN ("
                + ",".join(["?"] * len(file_ids_sorted))
                + ")"
            ),
            file_ids_sorted,
        )
        id_to_path = {
            int(i): Path(p) for i, p in cur.fetchall()
        }

        results: List[Tuple[Path, int]] = [
            (id_to_path[fid], file_to_count[fid])
            for fid in file_ids_sorted
            if fid in id_to_path
        ]
        return results
    finally:
        conn.close()
