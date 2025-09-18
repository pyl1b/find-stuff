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
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from sqlalchemy import func, select  # type: ignore[import-not-found]
from sqlalchemy.orm import Session  # type: ignore[import-not-found]

from find_stuff.models import (
    File as SAFile,
)
from find_stuff.models import (
    Posting as SAPosting,
)
from find_stuff.models import (
    Repository,
    create_engine_for_path,
    ensure_db,
    init_db,
)
from find_stuff.models import (
    Token as SAToken,
)

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
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
        )
        relpaths = [
            p
            for p in result.stdout.decode("utf-8", "ignore").split("\x00")
            if p
        ]
        return [repo_root / rel for rel in relpaths]
    except Exception as e:
        print(f"Error listing git tracked files for {repo_root}: {e}")
        subprocess.run(
            ["git", "ls-files"],
            cwd=str(repo_root),
        )
        return []


def list_git_tracked_files(
    repo_root: Path, file_types: Sequence[str]
) -> List[Path]:
    """Enumerate files with given extensions that are tracked by git.

    Args:
        repo_root: The repository root path.
        file_types: One or more file extensions to include. Each entry may be
            specified with or without a leading dot (e.g. "py" or ".py").

    Returns:
        List of absolute paths to tracked files that match the extensions.
    """

    candidates = _git_tracked_files(repo_root)
    if not file_types:
        return []

    normalized_exts = {
        "." + ext.lstrip(".").lower() for ext in file_types if ext.strip()
    }
    return [p for p in candidates if p.suffix.lower() in normalized_exts]


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
    """Create database schema (drop existing tables).

    Note: Kept for backwards compatibility in tests/imports.
    Actual schema creation is handled via SQLAlchemy in rebuild_index.
    """
    cur = conn.cursor()
    cur.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        """
    )
    conn.commit()


def rebuild_index(
    root: Path,
    db_path: Path,
    file_types: Optional[Sequence[str]] = ("py",),
) -> None:
    """Rebuild the index for all git repositories under a root directory.

    This clears and recreates the SQLite database at ``db_path``.

    Args:
        root: Root directory to scan recursively for repositories.
        db_path: Path to the SQLite database to (re)build.
        file_types: File extensions to include while indexing. Defaults to
            ("py",).
    """

    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine_for_path(db_path)
    init_db(engine)

    repos = find_git_repos(root)
    with Session(engine) as session:
        for repo_root in repos:
            repo = Repository(root=str(repo_root))
            session.add(repo)
            session.flush()  # populate repo.id

            selected_files = list_git_tracked_files(
                repo_root, file_types or ("py",)
            )
            for fpath in selected_files:
                relpath = os.path.relpath(fpath, repo_root)
                db_file = SAFile(
                    repo_id=repo.id, relpath=relpath, abspath=str(fpath)
                )
                session.add(db_file)
                session.flush()  # populate db_file.id

                # Collect tokens for the file
                tokens_in_file: List[Tuple[str, int, int]] = []
                for post in _iter_token_postings(fpath):
                    tokens_in_file.append((post.token, post.line, post.column))

                if not tokens_in_file:
                    continue

                unique_tokens = sorted({t for t, _l, _c in tokens_in_file})

                # Fetch existing tokens
                existing_rows = session.execute(
                    select(SAToken.id, SAToken.token).where(
                        SAToken.token.in_(unique_tokens)
                    )
                ).all()
                existing_map = {tok: tid for tid, tok in existing_rows}
                missing_tokens = [
                    t for t in unique_tokens if t not in existing_map
                ]

                # Insert missing tokens
                if missing_tokens:
                    session.bulk_save_objects(
                        [
                            SAToken(token=t, token_lc=t.lower())
                            for t in missing_tokens
                        ]
                    )
                    session.flush()

                # Build mapping token -> id after inserts
                all_rows = session.execute(
                    select(SAToken.id, SAToken.token).where(
                        SAToken.token.in_(unique_tokens)
                    )
                ).all()
                token_to_id = {tok: int(tid) for tid, tok in all_rows}

                # Create postings
                session.bulk_save_objects(
                    [
                        SAPosting(
                            file_id=db_file.id,
                            token_id=token_to_id[tok],
                            line=line,
                            col=col,
                        )
                        for (tok, line, col) in tokens_in_file
                    ]
                )

        session.commit()


def add_to_index(
    root: Path,
    db_path: Path,
    file_types: Optional[Sequence[str]] = ("py",),
) -> None:
    """Add repositories and files under a root without clearing the index.

    This function discovers git repositories under ``root`` and appends their
    files and token postings to the existing SQLite database at ``db_path``.
    Existing data is preserved. Repositories already present in the database
    (by exact root path) are skipped to avoid duplication.

    Args:
        root: Root directory to scan recursively for repositories.
        db_path: Path to the SQLite database to update or create.
        file_types: File extensions to include while indexing. Defaults to
            ("py",).
    """

    # Ensure DB directory exists and schema is present without dropping data
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine_for_path(db_path)
    ensure_db(engine)

    repos = find_git_repos(root)
    if not repos:
        return

    with Session(engine) as session:
        # Fetch existing repository roots for skip logic
        existing_roots = {
            r for (r,) in session.execute(select(Repository.root)).all()
        }

        for repo_root in repos:
            repo_root_str = str(repo_root)
            if repo_root_str in existing_roots:
                # Skip repositories already in the index
                continue

            repo = Repository(root=repo_root_str)
            session.add(repo)
            session.flush()  # populate repo.id

            selected_files = list_git_tracked_files(
                repo_root, file_types or ("py",)
            )
            for fpath in selected_files:
                relpath = os.path.relpath(fpath, repo_root)
                db_file = SAFile(
                    repo_id=repo.id, relpath=relpath, abspath=str(fpath)
                )
                session.add(db_file)
                session.flush()  # populate db_file.id

                tokens_in_file: List[Tuple[str, int, int]] = []
                for post in _iter_token_postings(fpath):
                    tokens_in_file.append((post.token, post.line, post.column))

                if not tokens_in_file:
                    continue

                unique_tokens = sorted({t for t, _l, _c in tokens_in_file})

                existing_rows = session.execute(
                    select(SAToken.id, SAToken.token).where(
                        SAToken.token.in_(unique_tokens)
                    )
                ).all()
                existing_map = {tok: tid for tid, tok in existing_rows}
                missing_tokens = [
                    t for t in unique_tokens if t not in existing_map
                ]

                if missing_tokens:
                    session.bulk_save_objects(
                        [
                            SAToken(token=t, token_lc=t.lower())
                            for t in missing_tokens
                        ]
                    )
                    session.flush()

                all_rows = session.execute(
                    select(SAToken.id, SAToken.token).where(
                        SAToken.token.in_(unique_tokens)
                    )
                ).all()
                token_to_id = {tok: int(tid) for tid, tok in all_rows}

                session.bulk_save_objects(
                    [
                        SAPosting(
                            file_id=db_file.id,
                            token_id=token_to_id[tok],
                            line=line,
                            col=col,
                        )
                        for (tok, line, col) in tokens_in_file
                    ]
                )

        session.commit()


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
    file_types: Optional[Sequence[str]] = None,
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
        file_types: If provided, restrict results to files whose extension
            matches one of these. Entries can include or omit the leading dot
            (e.g. "py" or ".py").

    Returns:
        List of tuples ``(file_path, score)`` where score is the number of
        matched postings in the file, ordered descending by score.
    """

    if not terms:
        return []

    engine = create_engine_for_path(db_path)
    with Session(engine) as session:
        # Resolve matching token ids for each term
        term_token_ids: List[List[int]] = []
        for term in terms:
            if not regex:
                if case_sensitive:
                    ids = session.scalars(
                        select(SAToken.id).where(SAToken.token == term)
                    ).all()
                else:
                    ids = session.scalars(
                        select(SAToken.id).where(
                            SAToken.token_lc == term.lower()
                        )
                    ).all()
            else:
                # Regex: fetch tokens and filter in Python
                rows = session.execute(
                    select(SAToken.id, SAToken.token, SAToken.token_lc)
                ).all()
                flags = 0 if case_sensitive else re.IGNORECASE
                pattern = re.compile(term, flags)
                ids = [
                    int(tok_id)
                    for tok_id, tok, tok_lc in rows
                    if pattern.search(tok if case_sensitive else tok_lc)
                    is not None
                ]
            term_token_ids.append([int(i) for i in ids])

        if require_all_terms and any(len(ids) == 0 for ids in term_token_ids):
            return []

        file_to_count: Dict[int, int] = {}

        if require_all_terms:
            first_ids = term_token_ids[0]
            if not first_ids:
                return []

            first_files = set(
                session.scalars(
                    select(SAPosting.file_id)
                    .where(SAPosting.token_id.in_(first_ids))
                    .distinct()
                ).all()
            )
            candidate_files = first_files

            for ids in term_token_ids[1:]:
                if not ids:
                    return []
                these_files = set(
                    session.scalars(
                        select(SAPosting.file_id)
                        .where(SAPosting.token_id.in_(ids))
                        .distinct()
                    ).all()
                )
                candidate_files &= these_files
                if not candidate_files:
                    return []

            if not candidate_files:
                return []

            all_ids = sorted({tid for ids in term_token_ids for tid in ids})
            rows = session.execute(
                select(SAPosting.file_id, func.count())
                .where(
                    SAPosting.file_id.in_(list(candidate_files)),
                    SAPosting.token_id.in_(all_ids),
                )
                .group_by(SAPosting.file_id)
            ).all()
            for file_id, count in rows:
                file_to_count[int(file_id)] = int(count)
        else:
            all_ids = sorted({tid for ids in term_token_ids for tid in ids})
            if not all_ids:
                return []
            rows = session.execute(
                select(SAPosting.file_id, func.count())
                .where(SAPosting.token_id.in_(all_ids))
                .group_by(SAPosting.file_id)
            ).all()
            for file_id, count in rows:
                file_to_count[int(file_id)] = int(count)

        if not file_to_count:
            return []

        if file_types:
            normalized_exts = {
                "." + ext.lstrip(".").lower()
                for ext in file_types
                if ext.strip()
            }
            if normalized_exts:
                all_ids_list = list(file_to_count.keys())
                rows = session.execute(
                    select(SAFile.id, SAFile.abspath).where(
                        SAFile.id.in_(all_ids_list)
                    )
                ).all()
                allowed_ids = {
                    int(i)
                    for i, p in rows
                    if Path(p).suffix.lower() in normalized_exts
                }
                if allowed_ids:
                    file_to_count = {
                        fid: cnt
                        for fid, cnt in file_to_count.items()
                        if fid in allowed_ids
                    }
                else:
                    return []

        file_ids_sorted = [
            fid
            for fid, _ in sorted(
                file_to_count.items(), key=lambda kv: kv[1], reverse=True
            )
        ]
        if limit > 0:
            file_ids_sorted = file_ids_sorted[:limit]

        rows = session.execute(
            select(SAFile.id, SAFile.abspath).where(
                SAFile.id.in_(file_ids_sorted)
            )
        ).all()
        id_to_path = {int(i): Path(p) for i, p in rows}

        results: List[Tuple[Path, int]] = [
            (id_to_path[fid], file_to_count[fid])
            for fid in file_ids_sorted
            if fid in id_to_path
        ]
        return results
