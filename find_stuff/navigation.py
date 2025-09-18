"""Interactive navigation helpers for repositories, directories, and files.
This module provides functions to enumerate repositories, directories inside
repositories, and files; parse user input that may be an index, name, or path;
and retrieve file metadata and modification status from the database.
The interactive UI itself is implemented in the CLI module; this module
contains non-interactive helpers that are easy to test.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from find_stuff.models import File as SAFile
from find_stuff.models import Repository, create_engine_for_path


@dataclass(frozen=True)
class RepoEntry:
    """Repository entry with display info.

    Attributes:
        index: 1-based index for display.
        name: Basename of the repo directory.
        root: Absolute path to the repository root.
    """

    index: int
    name: str
    root: Path


@dataclass(frozen=True)
class DirEntry:
    """Directory entry with display info.

    Attributes:
        index: 1-based index for display.
        name: Directory name.
        path: Absolute path to the directory.
    """

    index: int
    name: str
    path: Path


@dataclass(frozen=True)
class FileEntry:
    """File entry with display info.

    Attributes:
        index: 1-based index for display.
        name: Filename (with relative path from repo if requested).
        path: Absolute path to the file.
    """

    index: int
    name: str
    path: Path


@dataclass(frozen=True)
class FileStatus:
    """Status information for a file in the database and on disk.

    Attributes:
        in_index: Whether the file exists in the database.
        size_bytes: Size at index time.
        mtime_ns: Modification time at index time.
        ctime_ns: Change time at index time.
        sha256_hex: Content hash at index time.
        current_size_bytes: Current file size.
        current_mtime_ns: Current modification time.
        current_ctime_ns: Current change time.
        current_sha256_hex: Current hash.
        status: Human sentence describing change status.
    """

    in_index: bool
    size_bytes: Optional[int]
    mtime_ns: Optional[int]
    ctime_ns: Optional[int]
    sha256_hex: Optional[str]
    current_size_bytes: Optional[int]
    current_mtime_ns: Optional[int]
    current_ctime_ns: Optional[int]
    current_sha256_hex: Optional[str]
    status: str


def _hash_file(path: Path) -> str:
    """Compute sha256 hex for a file path."""

    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as rf:
        for chunk in iter(lambda: rf.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def list_repositories(db_path: Path) -> List[RepoEntry]:
    """Return repositories known to the database.

    Args:
        db_path: Path to the SQLite database.

    Returns:
        List of repositories with 1-based indices.
    """

    engine = create_engine_for_path(db_path)
    with Session(engine) as session:
        rows = session.execute(
            select(Repository.root).order_by(Repository.root)
        ).all()
    repos = [Path(r) for (r,) in rows]
    return [
        RepoEntry(index=i + 1, name=repo.name or str(repo), root=repo)
        for i, repo in enumerate(repos)
    ]


def list_directories(base: Path) -> List[DirEntry]:
    """List immediate subdirectories of a base directory (filesystem).

    This helper is currently unused by the interactive browser which relies on
    database-backed directory listing. It remains for potential utilities.

    Args:
        base: Absolute base directory path.

    Returns:
        List of directory entries, sorted case-insensitively.
    """

    try:
        entries = [p for p in base.iterdir() if p.is_dir()]
    except Exception:
        return []

    entries.sort(key=lambda p: p.name.lower())
    return [
        DirEntry(index=i + 1, name=p.name, path=p)
        for i, p in enumerate(entries)
    ]


def list_files_in_repo(db_path: Path, repo_root: Path) -> List[FileEntry]:
    """List files known to the DB for a given repository.

    Args:
        db_path: SQLite db path.
        repo_root: Repository root absolute path.

    Returns:
        Files with relative names from the repo root and absolute paths.
    """

    engine = create_engine_for_path(db_path)
    with Session(engine) as session:
        repo_row = session.execute(
            select(Repository.id).where(Repository.root == str(repo_root))
        ).first()
        if not repo_row:
            return []
        repo_id = int(repo_row[0])
        rows = session.execute(
            select(SAFile.relpath, SAFile.abspath)
            .where(SAFile.repo_id == repo_id)
            .order_by(SAFile.relpath)
        ).all()
    files = [(rel, Path(abs_path)) for (rel, abs_path) in rows]
    return [
        FileEntry(index=i + 1, name=rel, path=abs_path)
        for i, (rel, abs_path) in enumerate(files)
    ]


def list_repo_dir_contents(
    db_path: Path, repo_root: Path, rel_dir: str
) -> Tuple[List[DirEntry], List[FileEntry]]:
    """List immediate subdirectories and files within a repo relative dir.

    The listing is restricted to directories and files that are present in the
    database for the given repository.

    Args:
        db_path: SQLite database path.
        repo_root: Absolute repository root path.
        rel_dir: Directory relative to repo root using OS separators; may be
            empty or '.' to indicate the repository root.

    Returns:
        Tuple of (directories, files) for the given directory level.
    """

    rel_dir_norm = (Path(rel_dir).as_posix().strip("/")) if rel_dir else ""

    engine = create_engine_for_path(db_path)
    with Session(engine) as session:
        repo_row = session.execute(
            select(Repository.id).where(Repository.root == str(repo_root))
        ).first()
        if not repo_row:
            return [], []
        repo_id = int(repo_row[0])
        rows = session.execute(
            select(SAFile.relpath, SAFile.abspath)
            .where(SAFile.repo_id == repo_id)
            .order_by(SAFile.relpath)
        ).all()

    dirs_set: List[str] = []
    files_list: List[Tuple[str, Path]] = []
    prefix = f"{rel_dir_norm}/" if rel_dir_norm else ""
    for rel, abs_path in rows:
        rel_posix = Path(rel).as_posix()
        if not rel_posix.startswith(prefix):
            continue
        remainder = rel_posix[len(prefix) :]
        if "/" in remainder:
            head = remainder.split("/", 1)[0]
            if head not in dirs_set:
                dirs_set.append(head)
        else:
            files_list.append((remainder, Path(abs_path)))

    dirs_set.sort(key=lambda s: s.lower())
    files_list.sort(key=lambda t: t[0].lower())

    dirs = [
        DirEntry(index=i + 1, name=name, path=(repo_root / name))
        for i, name in enumerate(dirs_set)
    ]
    files = [
        FileEntry(index=i + 1, name=name, path=path)
        for i, (name, path) in enumerate(files_list)
    ]
    return dirs, files


def resolve_repo_by_input(
    items: Sequence[RepoEntry], raw: str
) -> Optional[RepoEntry]:
    """Resolve a repository entry by user input (index, name, or path).

    Args:
        items: Available repository entries.
        raw: User input; quotes are stripped. Full paths are matched exactly.

    Returns:
        Matching `RepoEntry` or None.
    """

    text = _strip_optional_quotes(raw).strip()
    if not text:
        return None

    # Index (1-based)
    if text.isdigit():
        idx = int(text)
        if 1 <= idx <= len(items):
            return items[idx - 1]

    # Path or name
    p = Path(text)
    for it in items:
        if text.lower() == it.name.lower():
            return it
        if p.resolve() == it.root.resolve():
            return it
    return None


def resolve_dir_by_input(
    items: Sequence[DirEntry], raw: str
) -> Optional[DirEntry]:
    """Resolve a directory entry by index, name, or absolute path."""

    text = _strip_optional_quotes(raw).strip()
    if not text:
        return None
    if text.isdigit():
        idx = int(text)
        if 1 <= idx <= len(items):
            return items[idx - 1]
    p = Path(text)
    for it in items:
        if text.lower() == it.name.lower():
            return it
        if p.resolve() == it.path.resolve():
            return it
    return None


def resolve_file_by_input(
    items: Sequence[FileEntry], raw: str
) -> Optional[FileEntry]:
    """Resolve a file entry by index, display name, or path."""

    text = _strip_optional_quotes(raw).strip()
    if not text:
        return None
    if text.isdigit():
        idx = int(text)
        if 1 <= idx <= len(items):
            return items[idx - 1]
    p = Path(text)
    for it in items:
        if text == it.name:
            return it
        try:
            if p.resolve() == it.path.resolve():
                return it
        except Exception:
            # Non-existing path
            pass
    return None


def file_status(db_path: Path, path: Path) -> FileStatus:
    """Return file status comparing DB and current filesystem.

    Args:
        db_path: SQLite database path.
        path: File to inspect.

    Returns:
        FileStatus with stored and current values and a status summary.
    """

    resolved = path.resolve()
    engine = create_engine_for_path(db_path)
    with Session(engine) as session:
        row = session.execute(
            select(
                SAFile.size_bytes,
                SAFile.mtime_ns,
                SAFile.ctime_ns,
                SAFile.sha256_hex,
            ).where(SAFile.abspath == str(resolved))
        ).first()

    if row is None:
        return FileStatus(
            in_index=False,
            size_bytes=None,
            mtime_ns=None,
            ctime_ns=None,
            sha256_hex=None,
            current_size_bytes=None,
            current_mtime_ns=None,
            current_ctime_ns=None,
            current_sha256_hex=None,
            status="Not found in index",
        )

    size_b, mt_ns, ct_ns, digest = row

    try:
        st = resolved.stat()
        cur_size = int(st.st_size)
        cur_mtime_ns = int(
            getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
        )
        cur_ctime_ns = int(
            getattr(st, "st_ctime_ns", int(st.st_ctime * 1_000_000_000))
        )
        cur_digest = _hash_file(resolved)
    except Exception:
        return FileStatus(
            in_index=True,
            size_bytes=int(size_b) if size_b is not None else None,
            mtime_ns=int(mt_ns) if mt_ns is not None else None,
            ctime_ns=int(ct_ns) if ct_ns is not None else None,
            sha256_hex=str(digest) if digest is not None else None,
            current_size_bytes=None,
            current_mtime_ns=None,
            current_ctime_ns=None,
            current_sha256_hex=None,
            status="Error reading current file state",
        )

    time_changed = (mt_ns != cur_mtime_ns) or (ct_ns != cur_ctime_ns)
    hash_changed = (digest or "") != cur_digest

    if not time_changed and not hash_changed:
        st_text = "unchanged"
    elif time_changed and hash_changed:
        st_text = "modified (time and hash differ)"
    elif time_changed and not hash_changed:
        st_text = "time changed but content hash is identical"
    else:
        st_text = "content hash changed but times are same"

    return FileStatus(
        in_index=True,
        size_bytes=int(size_b) if size_b is not None else None,
        mtime_ns=int(mt_ns) if mt_ns is not None else None,
        ctime_ns=int(ct_ns) if ct_ns is not None else None,
        sha256_hex=str(digest) if digest is not None else None,
        current_size_bytes=cur_size,
        current_mtime_ns=cur_mtime_ns,
        current_ctime_ns=cur_ctime_ns,
        current_sha256_hex=cur_digest,
        status=st_text,
    )


def _strip_optional_quotes(text: str) -> str:
    """Remove matching single or double quotes around a string if present."""

    if len(text) >= 2 and ((text[0] == text[-1]) and text[0] in ('"', "'")):
        return text[1:-1]
    return text


def open_in_code(path: Path) -> Tuple[bool, str]:
    """Open a path in VS Code if available.

    Args:
        path: Path to open (repo dir, directory, or file).

    Returns:
        Tuple of (ok, message). On success, returns (True, ""). On failure,
        returns (False, reason).
    """

    exe = shutil.which("code")
    if not exe:
        return False, "VS Code 'code' executable not found on PATH"
    try:
        subprocess.Popen([exe, str(path)], shell=False)
        return True, ""
    except Exception as exc:
        return False, f"Failed to launch VS Code: {exc}"
